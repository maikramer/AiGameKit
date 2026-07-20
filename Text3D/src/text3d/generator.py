"""
Text3D — Text-to-3D via Text2D (texto → imagem) e Hunyuan3D-Omni (imagem → mesh).

Fluxo: KleinFluxGenerator → unload explícito → Hunyuan3DOmniSiTFlowMatchingPipeline.
SDNQ quantization é opcional (activada via ``sdnq_preset`` ou hw-auto em GPUs pequenas).
Pre-quantização (save/load) não funciona devido a tensores SVD não-contíguos do SDNQ int4.

OmniEncoder exige um controlo geométrico (bbox/pose/point/voxel). Sem controlo
explícito usa-se bbox humanoid 2u (ver ``DEFAULT_OMNI_BBOX``).
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
from .utils.omni_controls import resolve_control_kwargs
from .utils.prompt_enhance import create_optimized_prompt as _optimize_prompt

_logger = Logger()


def _as_trimesh(mesh_or_nested: Any) -> trimesh.Trimesh:
    """Normaliza saída do pipeline Omni (dict ``shapes`` / lista aninhada / Trimesh).

    Aplica guarda anti-NaN: decoders FP16/FlashVDM podem emitir vértices
    não-finitos que o exporter glTF converte em (0,0,0) — leque de faces
    gigantes na origem dentro do shape.
    """
    import numpy as np

    m: Any = mesh_or_nested
    if isinstance(m, dict):
        if "shapes" not in m:
            raise TypeError(f"Dict de saída Omni sem chave 'shapes': {list(m.keys())}")
        m = m["shapes"]
    while isinstance(m, (list, tuple)):
        if not m:
            raise ValueError("Saída 3D vazia do pipeline Omni")
        m = m[0]
    if not isinstance(m, trimesh.Trimesh):
        raise TypeError(f"Esperado trimesh.Trimesh, obtido {type(m)}")

    from gamedev_shared.mesh_repair import drop_nonfinite_faces

    verts, faces, n_bad = drop_nonfinite_faces(
        np.asarray(m.vertices, dtype=np.float64), np.asarray(m.faces, dtype=np.int64)
    )
    if n_bad:
        _logger.warn(f"Decode Omni: {n_bad} faces com vértices NaN/Inf removidas (guard anti-leque)")
        m = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    return m


class HunyuanTextTo3DGenerator:
    """
    Gera mesh 3D a partir de texto: primeiro Text2D, depois Hunyuan3D-Omni (image-to-3D).

    Por defeito os parâmetros de shape seguem ``text3d.defaults`` (perfil ~6-10GB VRAM em CUDA).
    SDNQ quantization é opcional (activada via ``sdnq_preset`` ou hw-auto em GPUs pequenas).
    O modelo 2D é sempre descarregado antes de carregar o Omni.
    """

    DEFAULT_HF_ID = "tencent/Hunyuan3D-Omni"
    DEFAULT_SDNQ_PRESET = ""

    VOLUME_DECODERS = frozenset({"vanilla", "hierarchical", "flashvdm"})
    MC_ALGOS = frozenset({"mc", "dmc"})

    def __init__(
        self,
        device: str | None = None,
        verbose: bool = False,
        cache_dir: str | None = None,
        hunyuan_model_id: str = DEFAULT_HF_ID,
        hunyuan_subfolder: str | None = None,
        sdnq_preset: str = DEFAULT_SDNQ_PRESET,
        gpu_ids: list[int] | None = None,
        volume_decoder: str = "vanilla",
        mc_algo: str | None = None,
        compile_models: bool = False,
        compile_mode: str = "default",
        sage_attention: bool = False,
        sdnq_quantized_matmul: bool = False,
        offload: bool = False,
        allow_group_offload: bool = True,
        fp8_layerwise: bool = False,
        channels_last: bool = False,
        use_ema: bool = False,
    ):
        if volume_decoder not in self.VOLUME_DECODERS:
            raise ValueError(f"volume_decoder inválido: {volume_decoder!r} (válidos: {sorted(self.VOLUME_DECODERS)})")
        if mc_algo is not None and mc_algo not in self.MC_ALGOS:
            raise ValueError(f"mc_algo inválido: {mc_algo!r} (válidos: {sorted(self.MC_ALGOS)})")
        if hunyuan_subfolder:
            _logger.warn(
                f"hunyuan_subfolder={hunyuan_subfolder!r} ignorado — Hunyuan3D-Omni usa repo flat (sem subfolder)."
            )

        self.verbose = verbose
        self.cache_dir = cache_dir
        self.hunyuan_model_id = hunyuan_model_id
        self.sdnq_preset = sdnq_preset
        self._gpu_ids = gpu_ids
        self.volume_decoder = volume_decoder
        self.mc_algo = mc_algo
        self.compile_models = compile_models
        self.compile_mode = compile_mode
        self.sdnq_quantized_matmul = sdnq_quantized_matmul
        self.offload = offload
        self.last_decode_stats: dict[str, Any] = {}
        # Cache do refresh_runtime_budget (UMS) — seed do decode; live mem_get_info manda.
        self._cached_num_chunks: int | None = None
        self.allow_group_offload = allow_group_offload
        self.fp8_layerwise = fp8_layerwise
        self.channels_last = channels_last
        self.use_ema = use_ema
        self.sage_attention = self._setup_sage_attention(sage_attention)

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self._hunyuan_pipeline: Any = None
        self._bg_remover: Any = None
        self._offload_plan: Any = None

        if self.verbose:
            _logger.info(f"device={self.device}")
            _logger.info(f"Hunyuan Omni: {self.hunyuan_model_id}")
            if gpu_ids is not None:
                _logger.info(f"Multi-GPU IDs: {gpu_ids}")
            if volume_decoder != "vanilla" or mc_algo or compile_models or self.sage_attention:
                _logger.info(
                    f"Aceleração: volume_decoder={volume_decoder} mc_algo={mc_algo} "
                    f"compile={compile_models}/{compile_mode} sage_attn={self.sage_attention} "
                    f"sdnq_matmul={sdnq_quantized_matmul} group_offload={allow_group_offload} "
                    f"fp8_layerwise={fp8_layerwise} channels_last={channels_last}"
                )

    def _setup_sage_attention(self, requested: bool) -> bool:
        """Activa SageAttention (attention INT8) via env vars.

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
        already_active = os.environ.get("USE_SAGEATTN", "0") == "1"
        if "hy3dshape.models.denoisers.hunyuan3ddit" in sys.modules and not already_active:
            _logger.warn("hy3dshape já importado sem SageAttention — flag sem efeito neste processo.")
            return False
        os.environ["CA_USE_SAGEATTN"] = "1"
        os.environ["USE_SAGEATTN"] = "1"
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
        self._log("A libertar pipeline Hunyuan Omni...")
        del self._hunyuan_pipeline
        self._hunyuan_pipeline = None
        _clear_cuda_cache()

    def unload_hunyuan(self) -> None:
        """Liberta VRAM do pipeline Omni shape (ex.: antes de Hunyuan3D-Paint)."""
        self._unload_hunyuan()

    def unload(self) -> None:
        """Alias UMS / BackendAdapter — igual a ``unload_hunyuan``."""
        self._unload_hunyuan()

    def warmup(self) -> Any:
        """Contrato UMS canónico: carrega Omni na VRAM (ou offload hooks)."""
        return self._load_hunyuan()

    def _load_hunyuan(self) -> Any:
        if self._hunyuan_pipeline is not None:
            return self._hunyuan_pipeline

        from .hy3dshape_paths import ensure_hy3dshape_on_path

        ensure_hy3dshape_on_path(quiet=True)
        from hy3dshape.pipelines import Hunyuan3DOmniSiTFlowMatchingPipeline

        hunyuan_device = self.device
        wants_quant = bool(self.sdnq_preset) and hunyuan_device == "cuda"

        load_device = "cpu"
        # fp16 em CPU é numericamente degradado (sem kernels half nativos).
        load_dtype = torch.float16 if hunyuan_device == "cuda" else torch.float32

        model_path = self._preflight_hunyuan()

        kwargs: dict[str, Any] = {
            "device": load_device,
            "dtype": load_dtype,
        }
        if self.use_ema:
            kwargs["variant"] = "ema"

        self._log(f"A carregar Hunyuan3DOmniSiTFlowMatchingPipeline ({model_path})...")
        _clear_cuda_cache()
        pipe = Hunyuan3DOmniSiTFlowMatchingPipeline.from_pretrained(model_path, **kwargs)

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

        if hunyuan_device == "cuda":
            _clear_cuda_cache()
            from gamedev_shared.hardware import cuda_gpu_free_specs
            from gamedev_shared.lowvram import get_footprint, place_pipeline

            specs = cuda_gpu_free_specs()
            if self._gpu_ids:
                keep = set(self._gpu_ids)
                specs = [s for s in specs if s[0] in keep]
            allow_multi = self._gpu_ids is None or len(self._gpu_ids) >= 2
            # Omni ~10 GiB fp16; com SDNQ int4 ~3 GiB pesos + activações.
            if wants_quant:
                from gamedev_shared.lowvram import ModelFootprint

                footprint = ModelFootprint(
                    fp16_weights_gib=3.0,
                    activation_gib=2.0,
                    largest_module_gib=2.0,
                    architecture="hunyuan3d",
                )
            else:
                footprint = get_footprint("hunyuan3d-omni")

            # Só DiT em group offload — `cond_encoder` fica na GPU (mismatch device
            # se ambos entram no stream). VAE fora (tiling).
            omni_offload_modules = ("model",)
            # hw-auto `offload` em GPUs <5GB → forçar escada group/model_cpu.
            use_group = bool(self.allow_group_offload or self.offload)
            # Qualidade > velocidade: leaf+stream reserva VRAM para octree/MC.
            # Em GPUs ≤~10 GiB (ou offload pedido) força group mesmo se pesos cabem.
            total_gib = max((s[-1] / (1024**3) for s in specs), default=0.0)
            force_group = bool(use_group and (self.offload or total_gib <= 10.0))
            prefer_leaf = bool(use_group)

            offload_plan = place_pipeline(
                pipe,
                footprint,
                specs,
                allow_quant=("none",),
                allow_multi_gpu=allow_multi and not force_group,
                model_attr="model",
                allow_group_offload=use_group,
                force_group_offload=force_group,
                prefer_leaf_offload=prefer_leaf,
                offload_modules=omni_offload_modules,
                on_status=lambda m: self._log(m),
            )
            if force_group and prefer_leaf:
                self._log(f"Omni: group leaf+stream forçado (VRAM≈{total_gib:.1f} GiB → ativação/octree)")
            self._offload_plan = offload_plan

            # hw-auto pediu cpu-offload e planner ficou em full-GPU (margem optimista).
            forced_model_cpu = False
            if (
                self.offload
                and getattr(offload_plan, "offload", "none") == "none"
                and hasattr(pipe, "enable_model_cpu_offload")
            ):
                primary = offload_plan.primary_gpu
                target = f"cuda:{primary}" if primary is not None else "cuda"
                self._log(f"hw-auto offload: enable_model_cpu_offload({target})")
                pipe.enable_model_cpu_offload(device=target)
                forced_model_cpu = True

            if self.fp8_layerwise:
                from gamedev_shared.group_offload import try_layerwise_casting

                try_layerwise_casting(
                    pipe,
                    modules=omni_offload_modules,
                    log_fn=self._log,
                )

            if self.channels_last:
                from gamedev_shared.quantization import apply_channels_last

                for attr in ("vae", "model", "cond_encoder"):
                    mod = getattr(pipe, attr, None)
                    if mod is not None:
                        apply_channels_last(mod, log_fn=lambda m, a=attr: self._log(f"{a}: {m}"))

            primary = offload_plan.primary_gpu or 0
            primary_dev = f"cuda:{primary}"
            offload_mode = getattr(offload_plan, "offload", "none")
            if offload_plan.multi_gpu_ids is not None or offload_mode == "group_stream":
                # Group: DiT em stream; conditioner + VAE residentes na GPU.
                if getattr(pipe, "cond_encoder", None) is not None:
                    pipe.cond_encoder.to(primary_dev)
                if getattr(pipe, "vae", None) is not None:
                    pipe.vae.to(primary_dev)
                pipe.device = torch.device(primary_dev)
            elif offload_mode == "none" and not forced_model_cpu:
                # Full-GPU: garantir device do pipe = GPU (prepare_image / latents usam self.device).
                if hasattr(pipe, "to"):
                    pipe.to(primary_dev)
                else:
                    pipe.device = torch.device(primary_dev)

            if torch.cuda.is_available():
                alloc = torch.cuda.memory_allocated() / (1024**3)
                self._log(f"Shape na VRAM: {alloc:.2f} GB")
                # Full-GPU sem pesos → placement no-op (bug clássico Omni sem .to).
                full_gpu = getattr(offload_plan, "offload", "none") == "none" and not forced_model_cpu
                if full_gpu and alloc < 0.5:
                    raise RuntimeError(
                        f"Placement falhou: Shape na VRAM={alloc:.2f} GB após place_pipeline "
                        f"(plano={offload_plan.summary()!r}). Pesos ficaram em CPU — abortar "
                        "antes de inferência lenta."
                    )

        self._configure_acceleration(pipe)
        self._hunyuan_pipeline = pipe
        return pipe

    def _preflight_hunyuan(self) -> str:
        """Garante snapshot Omni em disco (HF cache) e devolve path local para load.

        Best-effort: se falhar, devolve o model id e deixa ``from_pretrained`` tratar.
        """
        try:
            from gamedev_shared.model_download import ensure_model

            path = ensure_model(
                self.hunyuan_model_id,
                cache_dir=self.cache_dir,
                on_status=lambda m: self._log(f"preflight: {m}"),
            )
            if path:
                return str(path)
        except Exception as exc:
            self._log(f"preflight Omni falhou ({exc}); a deixar from_pretrained tratar")
        return self.hunyuan_model_id

    def _configure_acceleration(self, pipe: Any) -> None:
        """Liga FlashVDM (``vae.fast_decode``) e torch.compile no pipeline Omni.

        Omni VAE: ``fast_decode=True`` → extract_geometry_fast; caso contrário vanilla.
        ``hierarchical`` mapeia para vanilla (mais fiel); ``flashvdm`` → fast_decode.
        ``mc_algo`` propaga-se como ``mc_mode`` na inferência.
        """
        if self.volume_decoder == "flashvdm":
            if hasattr(pipe, "vae") and pipe.vae is not None:
                pipe.vae.fast_decode = True
            self._log("Volume decoder: flashvdm (vae.fast_decode=True)")
        elif self.volume_decoder == "hierarchical":
            if hasattr(pipe, "vae") and pipe.vae is not None:
                pipe.vae.fast_decode = False
            self._log("Volume decoder: hierarchical/vanilla (vae.fast_decode=False)")
        else:
            if hasattr(pipe, "vae") and pipe.vae is not None:
                pipe.vae.fast_decode = False

        if self.mc_algo == "dmc":
            if self.device != "cuda":
                _logger.warn("dmc requer CUDA — fallback para mc_algo='mc'.")
                self.mc_algo = "mc"
            else:
                try:
                    import diso  # noqa: F401
                except ImportError:
                    _logger.warn("diso não instalado — fallback para mc_algo='mc' (pip install diso).")
                    self.mc_algo = "mc"

        if self.compile_models:
            offload = getattr(self._offload_plan, "offload", "none") if self._offload_plan else "none"
            from gamedev_shared.quantization import apply_torch_compile, resolve_torch_compile_mode

            mode = resolve_torch_compile_mode(
                self.compile_mode,
                offload=offload,
                group_offload_active=(offload == "group_stream"),
            )
            if offload in ("model_cpu", "sequential_cpu"):
                self._log(f"torch.compile skip (offload={offload})")
            else:
                if mode != self.compile_mode:
                    self._log(f"torch.compile mode={self.compile_mode} → {mode} (offload={offload})")
                for attr in ("model", "vae", "cond_encoder"):
                    mod = getattr(pipe, attr, None)
                    if mod is None:
                        continue
                    compiled = apply_torch_compile(
                        mod,
                        mode=mode,
                        offload=offload,
                        group_offload_active=(offload == "group_stream"),
                    )
                    if compiled is not mod:
                        setattr(pipe, attr, compiled)
                self._log(f"torch.compile ({mode}) activo (DiT+VAE+cond_encoder; warmup na 1ª inferência).")

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
        mc_level: float | str = "auto",
        t2d_full_gpu: bool = False,
        return_reference_image: bool = False,
        optimize_prompt: bool = True,
        remove_bg: bool = True,
        control_type: str | None = None,
        bbox: list[float] | None = None,
        pose_file: str | Path | None = None,
        point_cloud: str | Path | None = None,
        voxel_mesh: str | Path | None = None,
        bounds_mode: str = "auto",
        auto_num_chunks: bool = True,
    ) -> trimesh.Trimesh | tuple[trimesh.Trimesh, Image.Image]:
        """
        Text-to-3D: gera imagem com Text2D, descarrega Text2D, gera mesh com Hunyuan3D-Omni.

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
            mem_eff_t2d = True
        elif t2d_full_gpu:
            mem_eff_t2d = False
        else:
            mem_eff_t2d = _defaults.DEFAULT_T2D_CPU_OFFLOAD

        self._log("Fase 1: Text2D (texto → imagem)")
        if mem_eff_t2d and self.device == "cuda":
            self._log("Text2D com CPU offload (defeito para ~6GB VRAM).")

        t2d = KleinFluxGenerator(
            device=self.device,
            memory_efficient=mem_eff_t2d,
            verbose=self.verbose,
            model_id=text2d_model_id,
            cache_dir=self.cache_dir,
        )
        try:
            pil_image, _t2d_metadata = t2d.generate(
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
            control_type=control_type,
            bbox=bbox,
            pose_file=pose_file,
            point_cloud=point_cloud,
            voxel_mesh=voxel_mesh,
            bounds_mode=bounds_mode,
            auto_num_chunks=auto_num_chunks,
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
        mc_level: float | str = "auto",
        remove_bg: bool = True,
        keep_loaded: bool = False,
        step_callback: Callable[[int, Any, Any], None] | None = None,
        control_type: str | None = None,
        bbox: list[float] | None = None,
        pose_file: str | Path | None = None,
        point_cloud: str | Path | None = None,
        voxel_mesh: str | Path | None = None,
        bounds_mode: str = "auto",
        auto_num_chunks: bool = True,
    ) -> trimesh.Trimesh:
        """Image-to-3D com Hunyuan3D-Omni (sem Text2D).

        Args:
            keep_loaded: Se True, não descarrega o pipeline após inferência.
            step_callback: Reservado; Omni actualmente não expõe callback por step.
            control_type: ``none|bbox|pose|point|voxel`` (default ``none`` → bbox neutro).
            mc_level: iso-nível MC; ``"auto"`` → ligeiro negativo ∝ 1/octree
                (fecha pinholes; ver :mod:`text3d.decode_tune`).
            bounds_mode: ``"auto"`` encolhe os bounds do grid MC ao aspecto da
                bbox Omni (voxels mais finos no eixo fino — anti-buracos em
                assets achatados); ``"cube"`` mantém o cubo clássico ±1.01.
            auto_num_chunks: dimensiona o batch de queries do decode pela VRAM
                livre no momento (``mem_get_info``) em vez do valor estático.
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
            keyed = key_uniform_background(image)
            if keyed is not None:
                self._log("Sem alpha: fundo uniforme removido por keying (anti-placa).")
                image = crop_to_content(keyed)
            else:
                _logger.warn(
                    "remove_bg desligado e imagem sem alpha com fundo não-uniforme — "
                    "risco alto de placa/pedestal fundido. Recomenda-se BiRefNet (omitir --no-remove-bg)."
                )

        self._log("Fase 2: Hunyuan3D-Omni (imagem → mesh)")
        pipe = self._load_hunyuan()

        pd = getattr(pipe, "device", self.device)
        gen_device = pd if isinstance(pd, torch.device) else (pd if pd == "cpu" else self.device)
        if str(gen_device).startswith("cuda") and not torch.cuda.is_available():
            gen_device = "cpu"
        # Controlo Omni: preferir GPU do generator quando pipe.device=cpu (cpu_offload).
        # O OmniEncoder alinha ao device de image_cond no forward; tensors em CUDA evitam
        # o caso bbox@cpu + image_cond@cuda no torch.cat.
        control_device = (
            self.device if str(self.device).startswith("cuda") and torch.cuda.is_available() else gen_device
        )
        generator = torch.Generator(
            device=gen_device if str(gen_device) == "cpu" or str(gen_device).startswith("cuda") else "cpu"
        )
        if hy_seed is not None:
            generator.manual_seed(hy_seed)

        pipe_dtype = getattr(pipe, "dtype", torch.float16)
        control_kwargs = resolve_control_kwargs(
            control_type,
            bbox=bbox,
            pose_file=pose_file,
            point_cloud=point_cloud,
            voxel_mesh=voxel_mesh,
            device=control_device,
            dtype=pipe_dtype,
        )

        from .decode_tune import (
            auto_num_chunks as _auto_num_chunks,
        )
        from .decode_tune import (
            bounds_for_bbox,
            prefer_surface_decoder,
            resolve_mc_level,
        )

        fast_decode = self.volume_decoder == "flashvdm"
        # Octree alto + decoder denso = grid a visitar o interior inteiro do
        # field (ruído) — forçar decoder surface-focused (flashvdm).
        if not fast_decode and prefer_surface_decoder(self.volume_decoder, octree_resolution):
            fast_decode = True
            self._log(
                f"Decoder denso a octree={octree_resolution} → flashvdm forçado "
                f"(surface-focused; menos lixo interno/VRAM)"
            )
        mc_mode = self.mc_algo or "mc"
        mc_level_eff = resolve_mc_level(mc_level, octree_resolution)

        # Bounds anisotrópicos: grid MC segue o aspecto da bbox Omni — voxels
        # mais finos no eixo fino (anti-buracos em portas/espadas/edifícios).
        bounds: list[float] | None = None
        if str(bounds_mode).lower() == "auto" and control_type == "bbox":
            bounds = bounds_for_bbox(bbox)
            if bounds is not None:
                self._log(f"Bounds bbox-aware: {[round(v, 3) for v in bounds]}")

        # Chunks dinâmicos: batch de queries do decode ∝ VRAM livre agora
        # (pesos já colocados/offloaded), não ao tier estático.
        chunks_static = num_chunks
        decode_free_b: int | None = None
        if auto_num_chunks and torch.cuda.is_available() and str(gen_device) != "cpu":
            try:
                decode_free_b, _total_b = torch.cuda.mem_get_info()
            except Exception as exc:
                decode_free_b = None
                self._log(f"mem_get_info falhou ({exc}) — chunks estáticos={num_chunks}")
            # Seed do UMS refresh (se houver); medição live tem prioridade.
            if self._cached_num_chunks is not None:
                num_chunks = self._cached_num_chunks
            dyn = _auto_num_chunks(decode_free_b)
            if dyn is not None and dyn != num_chunks:
                free_gib = (decode_free_b or 0) / (1024**3)
                self._log(f"num_chunks dinâmico: {num_chunks} → {dyn} (VRAM livre {free_gib:.1f} GiB)")
                num_chunks = dyn
            self._cached_num_chunks = None  # consumido neste decode

        _clear_cuda_cache()
        self._log(
            f"Inferência Omni: steps={num_inference_steps} octree={octree_resolution} "
            f"chunks={num_chunks} guidance={guidance_scale} control={control_type or 'none'} "
            f"mc_level={mc_level_eff:.5f} bounds={'aniso' if bounds else 'cube'}"
        )
        if step_callback is not None:
            _logger.warn("step_callback ignorado — Hunyuan3D-Omni não expõe callback por step.")

        with torch.inference_mode():
            pipe_kwargs: dict[str, Any] = dict(
                image=image,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                octree_resolution=octree_resolution,
                num_chunks=num_chunks,
                generator=generator,
                output_type="trimesh",
                mc_level=mc_level_eff,
                mc_mode=mc_mode,
                fast_decode=fast_decode,
                **control_kwargs,
            )
            if bounds is not None:
                pipe_kwargs["bounds"] = bounds
            raw = pipe(**pipe_kwargs)

        mesh = _as_trimesh(raw)

        # Lixo interno do field (componentes fechados dentro da shell — nunca
        # vistos, só inflam o GLB). Conservador: volume < 15% da shell.
        # Path O(F+C); storms MC (10^5+ comps) usam AABB-only (ver mesh_metrics).
        import time as _time

        from .utils.mesh_metrics import drop_internal_components

        _t_drop = _time.perf_counter()
        mesh, n_internal, pre_stats = drop_internal_components(mesh)
        _dt_drop = _time.perf_counter() - _t_drop
        self.last_decode_stats = {
            **pre_stats,
            "drop_seconds": round(_dt_drop, 3),
            "post_faces": len(mesh.faces),
            # Runtime budget efetivo do decode (visível no UMS via adapter).
            "num_chunks": int(num_chunks),
            "num_chunks_static": int(chunks_static),
            "auto_num_chunks": bool(auto_num_chunks),
            "free_vram_bytes": decode_free_b,
        }
        if n_internal or _dt_drop > 1.0:
            self._log(
                f"Decode Omni: {n_internal} componentes internos removidos "
                f"(ruído do field, {_dt_drop:.2f}s, "
                f"pre_int_vol={pre_stats.get('internal_volume_ratio', 0):.4f})"
            )

        if not keep_loaded:
            self._unload_hunyuan()

        return mesh

    def refresh_runtime_budget(self) -> dict[str, Any] | None:
        """Orça ``num_chunks`` pela VRAM livre e cacheia para o próximo decode.

        Contrato UMS (:meth:`BackendAdapter.apply_runtime_budget`): o adapter
        chama isto antes da inferência. O decode re-mede ``mem_get_info`` (live
        manda) mas usa o cache como seed se a medição falhar. Devolve ``None``
        sem sinal de VRAM.
        """
        from gamedev_shared.vram_budget import free_vram_bytes, text3d_num_chunks

        free_b = free_vram_bytes()
        n = text3d_num_chunks(free_b)
        if n is None:
            return None
        self._cached_num_chunks = int(n)
        return {"num_chunks": int(n), "free_vram_bytes": free_b, "auto": True}

    def __enter__(self) -> HunyuanTextTo3DGenerator:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._unload_hunyuan()
