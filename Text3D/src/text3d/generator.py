"""
Text3D — Text-to-3D via Text2D (texto → imagem) e Hunyuan3D-2.1 (imagem → mesh).

Fluxo: KleinFluxGenerator → unload explícito → Hunyuan3DDiTFlowMatchingPipeline.
SDNQ quantization é opcional (activada via ``sdnq_preset`` ou hw-auto em GPUs pequenas).
Pre-quantização (save/load) não funciona devido a tensores SVD não-contíguos do SDNQ int4.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import torch
import trimesh
from PIL import Image

from gamedev_shared.logging import Logger
from text2d.generator import KleinFluxGenerator

from . import defaults as _defaults
from .utils.bg_removal import (
    BiRefNetBGRemover,
    crop_to_content,
    has_meaningful_alpha,
    key_uniform_background,
)
from .utils.memory import clear_cuda_memory as _clear_cuda_cache
from .utils.prompt_enhance import create_optimized_prompt as _optimize_prompt

_logger = Logger()


def _as_trimesh(mesh_or_nested: Any) -> trimesh.Trimesh:
    """Normaliza saída do pipeline Hunyuan (lista aninhada ou Trimesh)."""
    m: Any = mesh_or_nested
    while isinstance(m, (list, tuple)):
        if not m:
            raise ValueError("Saída 3D vazia do pipeline Hunyuan")
        m = m[0]
    if not isinstance(m, trimesh.Trimesh):
        raise TypeError(f"Esperado trimesh.Trimesh, obtido {type(m)}")
    return m


class HunyuanTextTo3DGenerator:
    """
    Gera mesh 3D a partir de texto: primeiro Text2D, depois Hunyuan3D-2.1 (image-to-3D).

    Por defeito os parâmetros de shape seguem ``text3d.defaults`` (perfil ~6-8GB VRAM em CUDA).
    SDNQ quantization é opcional (activada via ``sdnq_preset`` ou hw-auto em GPUs pequenas).
    O modelo 2D é sempre descarregado antes de carregar o Hunyuan.
    """

    DEFAULT_HF_ID = "tencent/Hunyuan3D-2.1"
    DEFAULT_SUBFOLDER = "hunyuan3d-dit-v2-1"
    DEFAULT_SDNQ_PRESET = ""

    VOLUME_DECODERS = frozenset({"vanilla", "hierarchical", "flashvdm"})
    MC_ALGOS = frozenset({"mc", "dmc"})

    def __init__(
        self,
        device: str | None = None,
        verbose: bool = False,
        cache_dir: str | None = None,
        hunyuan_model_id: str = DEFAULT_HF_ID,
        hunyuan_subfolder: str = DEFAULT_SUBFOLDER,
        sdnq_preset: str = DEFAULT_SDNQ_PRESET,
        gpu_ids: list[int] | None = None,
        volume_decoder: str = "vanilla",
        mc_algo: str | None = None,
        compile_models: bool = False,
        sage_attention: bool = False,
        sdnq_quantized_matmul: bool = False,
    ):
        if volume_decoder not in self.VOLUME_DECODERS:
            raise ValueError(f"volume_decoder inválido: {volume_decoder!r} (válidos: {sorted(self.VOLUME_DECODERS)})")
        if mc_algo is not None and mc_algo not in self.MC_ALGOS:
            raise ValueError(f"mc_algo inválido: {mc_algo!r} (válidos: {sorted(self.MC_ALGOS)})")

        self.verbose = verbose
        self.cache_dir = cache_dir
        self.hunyuan_model_id = hunyuan_model_id
        self.hunyuan_subfolder = hunyuan_subfolder
        self.sdnq_preset = sdnq_preset
        self._gpu_ids = gpu_ids
        self.volume_decoder = volume_decoder
        self.mc_algo = mc_algo
        self.compile_models = compile_models
        self.sdnq_quantized_matmul = sdnq_quantized_matmul
        self.sage_attention = self._setup_sage_attention(sage_attention)

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self._hunyuan_pipeline: Any = None
        self._bg_remover: Any = None

        if self.verbose:
            _logger.info(f"device={self.device}")
            _logger.info(f"Hunyuan: {self.hunyuan_model_id} / {self.hunyuan_subfolder}")
            if gpu_ids is not None:
                _logger.info(f"Multi-GPU IDs: {gpu_ids}")
            if volume_decoder != "vanilla" or mc_algo or compile_models or self.sage_attention:
                _logger.info(
                    f"Aceleração: volume_decoder={volume_decoder} mc_algo={mc_algo} "
                    f"compile={compile_models} sage_attn={self.sage_attention} "
                    f"sdnq_matmul={sdnq_quantized_matmul}"
                )

    def _setup_sage_attention(self, requested: bool) -> bool:
        """Activa SageAttention (attention INT8) via env CA_USE_SAGEATTN.

        O binding acontece no import dos módulos hy3dshape — tem de correr antes
        do primeiro ``_load_hunyuan`` do processo. Devolve o estado efectivo.
        """
        if not requested:
            return False
        try:
            import sageattention  # noqa: F401
        except ImportError:
            _logger.warn("sageattention não instalado — a continuar com SDPA (pip install sageattention).")
            return False
        if (
            "text3d.hy3dshape.models.denoisers.hunyuan3ddit" in sys.modules
            and os.environ.get("CA_USE_SAGEATTN", "0") != "1"
        ):
            _logger.warn("hy3dshape já importado sem SageAttention — flag sem efeito neste processo.")
            return False
        os.environ["CA_USE_SAGEATTN"] = "1"
        return True

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(msg)

    def _unload_hunyuan(self) -> None:
        if self._bg_remover is not None:
            self._bg_remover.unload()
            self._bg_remover = None
        if self._hunyuan_pipeline is None:
            return
        self._log("A libertar pipeline Hunyuan...")
        del self._hunyuan_pipeline
        self._hunyuan_pipeline = None
        _clear_cuda_cache()

    def unload_hunyuan(self) -> None:
        """Liberta VRAM do pipeline Hunyuan shape (ex.: antes de Hunyuan3D-Paint)."""
        self._unload_hunyuan()

    def _load_hunyuan(self) -> Any:
        if self._hunyuan_pipeline is not None:
            return self._hunyuan_pipeline

        from .hy3dshape_paths import ensure_hy3dshape_on_path

        ensure_hy3dshape_on_path()
        from .hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline

        hunyuan_device = self.device
        wants_quant = bool(self.sdnq_preset) and hunyuan_device == "cuda"

        load_device = "cpu"
        # fp16 em CPU é numericamente degradado (sem kernels half nativos —
        # acumulação meia-precisão). Em CPU, sempre fp32.
        load_dtype = torch.float16 if hunyuan_device == "cuda" else torch.float32

        kwargs: dict = {
            "subfolder": self.hunyuan_subfolder,
            "use_safetensors": False,
            "device": load_device,
            "dtype": load_dtype,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        self._log(f"A carregar Hunyuan3DDiTFlowMatchingPipeline ({self.hunyuan_model_id})...")
        _clear_cuda_cache()
        pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(self.hunyuan_model_id, **kwargs)

        if wants_quant:
            self._log(f"A quantizar DiT com SDNQ preset={self.sdnq_preset} (post-load)...")
            from gamedev_shared.sdnq import is_available as _sdnq_ok
            from gamedev_shared.sdnq import quantize_model

            if _sdnq_ok():
                pipe.model = quantize_model(
                    pipe.model,
                    preset=self.sdnq_preset,
                    quantization_device="cpu",
                    return_device="cpu",
                    use_quantized_matmul=self.sdnq_quantized_matmul,
                )
                self._log(f"SDNQ {self.sdnq_preset} aplicado ao DiT (CPU).")
            else:
                wants_quant = False
                self._log("SDNQ não disponível — a correr sem quantização (VRAM elevada).")

        if self._gpu_ids is not None and hunyuan_device == "cuda":
            from gamedev_shared.multi_gpu import MultiGPUPlanner

            planner = (
                MultiGPUPlanner().for_model(pipe).model_attr("model").with_gpus(self._gpu_ids).architecture("hunyuan3d")
            )
            plan = planner.plan()
            if plan.status == "multi_gpu":
                pipe = planner.apply()
                primary = plan.primary_device
                primary_dev = f"cuda:{primary}" if isinstance(primary, int) else primary
                pipe.conditioner.to(primary_dev)
                pipe.vae.to(primary_dev)
                pipe.device = torch.device(primary_dev)
                self._log(f"Multi-GPU dispatch: GPUs {self._gpu_ids}, primary={primary_dev}")
                self._configure_acceleration(pipe)
                self._hunyuan_pipeline = pipe
                return pipe
            self._log(f"Multi-GPU plan: {plan.status} — a usar colocação simples.")

        if hunyuan_device == "cuda":
            _clear_cuda_cache()
            pipe.to(hunyuan_device)
            if torch.cuda.is_available():
                alloc = torch.cuda.memory_allocated() / (1024**3)
                self._log(f"Shape na VRAM: {alloc:.2f} GB")

        self._configure_acceleration(pipe)
        self._hunyuan_pipeline = pipe
        return pipe

    def _configure_acceleration(self, pipe: Any) -> None:
        """Liga decoder volumétrico esparso, extractor GPU e torch.compile no pipeline.

        Defaults preservam o comportamento original (VanillaVolumeDecoder + skimage MC).
        ``hierarchical`` consulta apenas voxels perto da superfície (mesma matemática,
        ~quase lossless); ``flashvdm`` adiciona selecção top-k de KV no cross-attention
        (mais rápido, perda de qualidade ligeira). ``dmc`` requer o pacote ``diso``.
        """
        mc_algo = self.mc_algo
        if mc_algo == "dmc":
            if self.device != "cuda":
                _logger.warn("dmc requer CUDA (pipeline em CPU) — fallback para mc_algo='mc'.")
                mc_algo = "mc"
            else:
                try:
                    import diso  # noqa: F401
                except ImportError:
                    _logger.warn("diso não instalado — fallback para mc_algo='mc' (pip install diso).")
                    mc_algo = "mc"

        if self.volume_decoder != "vanilla":
            pipe.vae.enable_flashvdm_decoder(
                enabled=True,
                adaptive_kv_selection=(self.volume_decoder == "flashvdm"),
                topk_mode="mean",
                mc_algo=mc_algo or "mc",
            )
            self._log(f"Volume decoder: {self.volume_decoder} (mc_algo={mc_algo or 'mc'})")
        elif mc_algo is not None:
            from .hy3dshape.models.autoencoders import SurfaceExtractors

            pipe.vae.surface_extractor = SurfaceExtractors[mc_algo]()
            self._log(f"Surface extractor: {mc_algo}")

        if self.compile_models:
            pipe.compile()
            self._log("torch.compile activo (DiT+VAE+conditioner; warmup na 1ª inferência).")

    def generate(
        self,
        prompt: str,
        t2d_width: int = _defaults.DEFAULT_T2D_WIDTH,
        t2d_height: int = _defaults.DEFAULT_T2D_HEIGHT,
        t2d_steps: int = _defaults.DEFAULT_T2D_STEPS,
        t2d_guidance: float = _defaults.DEFAULT_T2D_GUIDANCE,
        text2d_model_id: str | None = None,
        t2d_seed: int | None = None,
        num_inference_steps: int = _defaults.DEFAULT_HY_STEPS,
        guidance_scale: float = _defaults.DEFAULT_HY_GUIDANCE,
        octree_resolution: int = _defaults.DEFAULT_OCTREE_RESOLUTION,
        num_chunks: int = _defaults.DEFAULT_NUM_CHUNKS,
        hy_seed: int | None = None,
        mc_level: float = 0.0,
        t2d_full_gpu: bool = False,
        return_reference_image: bool = False,
        optimize_prompt: bool = True,
        remove_bg: bool = True,
    ) -> trimesh.Trimesh | tuple[trimesh.Trimesh, Image.Image]:
        """
        Text-to-3D: gera imagem com Text2D, descarrega Text2D, gera mesh com Hunyuan3D-2.1.

        Com ``return_reference_image=True`` devolve ``(mesh, imagem_pil)`` para Hunyuan3D-Paint.
        Com ``optimize_prompt=True`` melhora o prompt para evitar placas/sombras na base.
        """
        if not prompt or not str(prompt).strip():
            raise ValueError("Prompt não pode ser vazio")

        original_prompt = prompt
        if optimize_prompt:
            prompt = _optimize_prompt(prompt, aggressive=True)
            if self.verbose and prompt != original_prompt:
                self._log(f"Prompt otimizado: {prompt[:120]}...")

        if self.device == "cpu":
            low_t2d = True
        elif t2d_full_gpu:
            low_t2d = False
        else:
            low_t2d = _defaults.DEFAULT_T2D_CPU_OFFLOAD

        self._log("Fase 1: Text2D (texto → imagem)")
        if low_t2d and self.device == "cuda":
            self._log("Text2D com CPU offload (defeito para ~6GB VRAM).")

        t2d = KleinFluxGenerator(
            device=self.device,
            low_vram=low_t2d,
            verbose=self.verbose,
            model_id=text2d_model_id,
            cache_dir=self.cache_dir,
        )
        try:
            pil_image = t2d.generate(
                prompt=prompt,
                height=t2d_height,
                width=t2d_width,
                guidance_scale=t2d_guidance,
                num_inference_steps=t2d_steps,
                seed=t2d_seed,
            )
        finally:
            t2d.unload()
            del t2d
            _clear_cuda_cache()

        mesh = self.generate_from_image(
            pil_image,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            octree_resolution=octree_resolution,
            num_chunks=num_chunks,
            hy_seed=hy_seed,
            mc_level=mc_level,
            remove_bg=remove_bg,
        )
        if return_reference_image:
            return mesh, pil_image
        return mesh

    def generate_from_image(
        self,
        image: str | Path | Image.Image,
        num_inference_steps: int = _defaults.DEFAULT_HY_STEPS,
        guidance_scale: float = _defaults.DEFAULT_HY_GUIDANCE,
        octree_resolution: int = _defaults.DEFAULT_OCTREE_RESOLUTION,
        num_chunks: int = _defaults.DEFAULT_NUM_CHUNKS,
        hy_seed: int | None = None,
        mc_level: float = 0.0,
        remove_bg: bool = True,
        keep_loaded: bool = False,
        step_callback: Callable[[int, Any, Any], None] | None = None,
    ) -> trimesh.Trimesh:
        """Image-to-3D apenas com Hunyuan (sem Text2D).

        Args:
            keep_loaded: Se True, não descarrega o pipeline Hunyuan após inferência
                (útil para processamento batch onde o modelo é reutilizado).
            step_callback: Optional callback invoked after each diffusion step with
                ``(step_idx, t, outputs)``. When provided, ``callback`` and
                ``callback_steps=1`` are forwarded to the pipeline.
        """
        if isinstance(image, (str, Path)):
            image = Image.open(image).convert("RGB")

        if remove_bg:
            self._log("A remover fundo com BiRefNet...")
            bg_remover = self._bg_remover or BiRefNetBGRemover(device=self.device)
            image = bg_remover.remove_background(image)
            image = crop_to_content(image)
            if keep_loaded:
                self._bg_remover = bg_remover
            else:
                bg_remover.unload()
                self._bg_remover = None
        elif not has_meaningful_alpha(image):
            # Sem alpha o preprocessor trata o frame inteiro como objecto e o
            # Hunyuan esculpe placas/pedestais fundidos ao modelo. Para renders
            # com fundo uniforme, keying barato recupera a silhueta.
            keyed = key_uniform_background(image)
            if keyed is not None:
                self._log("Sem alpha: fundo uniforme removido por keying (anti-placa).")
                image = crop_to_content(keyed)
            else:
                _logger.warn(
                    "remove_bg desligado e imagem sem alpha com fundo não-uniforme — "
                    "risco alto de placa/pedestal fundido. Recomenda-se BiRefNet (omitir --no-remove-bg)."
                )

        self._log("Fase 2: Hunyuan3D-2.1 (imagem → mesh)")
        pipe = self._load_hunyuan()

        pd = getattr(pipe, "device", self.device)
        gen_device = pd if isinstance(pd, torch.device) else (pd if pd == "cpu" else self.device)
        if gen_device == "cuda" and not torch.cuda.is_available():
            gen_device = "cpu"
        generator = torch.Generator(device=gen_device)
        if hy_seed is not None:
            generator.manual_seed(hy_seed)

        _clear_cuda_cache()
        self._log(
            f"Inferência: steps={num_inference_steps} octree={octree_resolution} "
            f"chunks={num_chunks} guidance={guidance_scale}"
        )
        with torch.inference_mode():
            pipe_kwargs: dict[str, Any] = dict(
                image=image,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                octree_resolution=octree_resolution,
                num_chunks=num_chunks,
                generator=generator,
                output_type="trimesh",
                mc_level=mc_level,
            )
            if step_callback is not None:
                pipe_kwargs["callback"] = step_callback
                pipe_kwargs["callback_steps"] = 1
            raw = pipe(**pipe_kwargs)

        mesh = _as_trimesh(raw)

        if not keep_loaded:
            self._unload_hunyuan()

        return mesh

    def __enter__(self) -> HunyuanTextTo3DGenerator:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._unload_hunyuan()
