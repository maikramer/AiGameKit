"""Texture2D — gerador de texturas seamless via FLUX.1-dev + LoRA local."""

from __future__ import annotations

import gc
import os
import re
from collections.abc import Callable, Generator
from typing import Any

import torch
from PIL import Image

from gamedev_shared.logging import Logger

from .presets import get_preset_params, get_preset_prompt
from .prompt_enhancer import (
    enhance_ground_negative,
    enhance_ground_prompt,
    looks_like_ground,
)
from .utils import generate_seed, validate_params, validate_prompt

_logger = Logger()

DEFAULT_LORA_MODEL_ID = "gokaygokay/Flux-Seamless-Texture-LoRA"
DEFAULT_BASE_MODEL_ID = "Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32"
# Alias compat: o "modelo" do Texture2D é a LoRA seamless aplicada sobre o FLUX base.
DEFAULT_MODEL_ID = DEFAULT_LORA_MODEL_ID

# Trigger word da LoRA gokaygokay/Flux-Seamless-Texture-LoRA (treinada via FAL Fast
# LoRA Trainer). O model card pede o formato exato "smlstxtr, <prompt>, seamless
# texture" — sem o trigger, a LoRA carrega mas o comportamento de tiling aprendido
# não é ativado de forma fiável (validado empiricamente: score_tileability médio
# ~0.0-0.1 sem o trigger vs melhoria mensurável com ele). Isto sozinho não garante
# costuras perfeitas — para isso ver ``tileability.make_seamless`` (post-process
# determinístico aplicado por defeito em ``TextureGenerator.generate``).
LORA_TRIGGER_WORD = "smlstxtr"

DEFAULT_PARAMS: dict[str, Any] = {
    "guidance_scale": 3.5,
    "num_inference_steps": 28,
    "seed": None,
    "width": 1024,
    "height": 1024,
    "cfg_scale": 3.5,
    "negative_prompt": "",
    "lora_strength": 1.0,
}


def _base_model_id() -> str:
    return os.environ.get("TEXTURE2D_BASE_MODEL_ID", DEFAULT_BASE_MODEL_ID)


def _lora_model_id() -> str:
    return os.environ.get("TEXTURE2D_MODEL_ID", DEFAULT_LORA_MODEL_ID)


def default_model_id() -> str:
    """Modelo LoRA por defeito (ou TEXTURE2D_MODEL_ID)."""
    return _lora_model_id()


def _torch_dtype_for(device: str) -> torch.dtype:
    d = device.lower()
    if d.startswith("cpu"):
        return torch.float32
    if d.startswith("cuda") and torch.cuda.is_available():
        if getattr(torch.cuda, "is_bf16_supported", lambda: False)():
            return torch.bfloat16
        return torch.float16
    return torch.float32


def augment_prompt_for_seamless(prompt: str) -> str:
    """Acrescenta trigger word + instruções de textura seamless/tileable.

    Formato do model card da LoRA: ``"smlstxtr, <prompt>, seamless texture"``.
    Não duplica se o utilizador já incluiu o trigger word ou termos seamless/tileable.
    """
    p = (prompt or "").strip()
    if not p:
        return p
    if re.search(rf"\b{LORA_TRIGGER_WORD}\b", p, flags=re.IGNORECASE):
        return p
    if re.search(
        r"\b(seamless|tileable|tiling|repeatable|repeating|repeat)\b",
        p,
        flags=re.IGNORECASE,
    ):
        return f"{LORA_TRIGGER_WORD}, {p}"
    return f"{LORA_TRIGGER_WORD}, {p}, seamless texture"


def merge_negative_prompt(preset_neg: str, user_neg: str) -> str:
    """Combina negative prompt do preset com o do utilizador."""
    preset_neg = (preset_neg or "").strip()
    user_neg = (user_neg or "").strip()
    if not preset_neg:
        return user_neg
    if not user_neg:
        return preset_neg
    if preset_neg.lower() in user_neg.lower():
        return user_neg
    if user_neg.lower() in preset_neg.lower():
        return preset_neg
    return f"{preset_neg}, {user_neg}"


class TextureGenerator:
    """Gerador de texturas seamless via FLUX.1-dev + LoRA (inferência local)."""

    def __init__(
        self,
        device: str | None = None,
        memory_efficient: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        sequential_offload: bool = False,
        vae_slicing: bool = False,
    ) -> None:
        self.verbose = verbose
        self.memory_efficient = memory_efficient
        self.model_id = model_id or _lora_model_id()
        self.base_model_id = _base_model_id()
        self.cache_dir = cache_dir
        self.gpu_ids = gpu_ids
        # Otimizações de memória para GPUs pequenas (<8 GiB). ``sequential_offload``
        # migra cada sub-módulo individualmente (mais lento que ``enable_model_cpu_offload``
        # mas o único que estavelmente cabe em ~6 GiB). Combinado com ``vae_slicing``
        # reduz o pico de VRAM durante a decodificação do VAE.
        self.sequential_offload = sequential_offload
        self.vae_slicing = vae_slicing

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.torch_dtype = _torch_dtype_for(self.device)
        self._pipe: Any = None
        self._on_status: Callable[[str], None] | None = None
        self._multi_gpu: bool = False

        if self.verbose:
            opts = []
            if self.sequential_offload:
                opts.append("sequential_offload")
            if self.vae_slicing:
                opts.append("vae_slicing")
            if self.memory_efficient:
                opts.append("model_cpu_offload")
            _logger.info(
                f"device={self.device} dtype={self.torch_dtype} base={self.base_model_id} "
                f"lora={self.model_id} opts={opts or 'none'}"
            )

    def set_status_callback(self, fn: Callable[[str], None] | None) -> None:
        """Callback opcional (ex. Rich) para mensagens de fase durante o load."""
        self._on_status = fn

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(msg)

    def _clear_cache(self) -> None:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
        else:
            _logger.step(msg)

    def warmup(self) -> None:
        """Carrega o pipeline (download HF + pesos). Idempotente."""
        self._load_pipeline()

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        from gamedev_shared.sdnq import apply_quantized_matmul, register_sdnq

        triton_is_available = register_sdnq(patch_lora=True)

        from diffusers import FluxPipeline

        kwargs: dict[str, Any] = {
            "torch_dtype": self.torch_dtype,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        self._status("Passo 1/4 — from_pretrained (SDNQ uint4, ~7 GB)")
        self._log(f"Carregando {self.base_model_id}...")
        pipe = FluxPipeline.from_pretrained(self.base_model_id, **kwargs)

        self._status("Passo 2/4 — SDNQ quantized matmul")
        apply_quantized_matmul(pipe, enabled=bool(triton_is_available))

        self._status("Passo 3/4 — LoRA weights")
        self._log(f"Carregando LoRA {self.model_id}...")
        pipe.load_lora_weights(self.model_id)

        if self.device == "cpu":
            mode_label = "cpu"
        elif self.sequential_offload:
            mode_label = f"{self.device} (sequential_cpu_offload — cada sub-módulo migra)"
        elif self.memory_efficient:
            mode_label = f"{self.device} (cpu_offload — módulos migram 1 a 1)"
        else:
            mode_label = self.device
        self._status(f"Passo 4/4 — a mover o pipeline para {mode_label}")

        self._clear_cache()

        if torch.cuda.is_available() and self.device == "cuda":
            for gid in self.gpu_ids or range(torch.cuda.device_count()):
                if gid < torch.cuda.device_count():
                    torch.cuda.reset_peak_memory_stats(gid)

        # VAE slicing reduz o pico de VRAM na decodificação; compatível com todos
        # os modos de offload e barato (sem cópias extra em inferência batch=1).
        # ``pipe.enable_vae_slicing()`` está deprecado em diffusers recentes; usar
        # ``pipe.vae.enable_slicing()`` quando disponível, com fallback gracioso.
        if self.vae_slicing and self.device != "cpu":
            try:
                if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_slicing"):
                    pipe.vae.enable_slicing()
                elif hasattr(pipe, "enable_vae_slicing"):
                    pipe.enable_vae_slicing()
            except Exception as e:
                self._log(f"enable_vae_slicing indisponível: {e}")

        # VAE tiling: decodifica o VAE em mosaicos sobrepostos em vez de uma só
        # passagem. Complementar ao slicing — reduz o pico de VRAM na decodificação
        # do latent → RGB, permitindo resoluções maiores em GPUs pequenas. Sem custo
        # de qualidade visível (a sobreposição dos mosaicos esconde as costuras).
        if self.vae_slicing and self.device != "cpu":
            try:
                if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_tiling"):
                    pipe.vae.enable_tiling()
            except Exception as e:
                self._log(f"enable_vae_tiling indisponível: {e}")

        if self.device == "cpu":
            pipe.to("cpu")
        elif self.gpu_ids and len(self.gpu_ids) >= 2 and self._try_multi_gpu(pipe):
            self._status("Modelo carregado — split multi-GPU")
        elif self.sequential_offload:
            # Mais conservador que enable_model_cpu_offload: migra cada sub-módulo
            # individualmente. Essencial em GPUs <8 GiB onde o transformer + VAE
            # não cabem em simultâneo. Mutuamente exclusivo com model_cpu_offload.
            pipe.enable_sequential_cpu_offload()
        elif self.memory_efficient:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(self.device)

        if (
            torch.cuda.is_available()
            and self.device == "cuda"
            and not self.memory_efficient
            and not self.sequential_offload
        ):
            if self._multi_gpu:
                gpu_ids = self.gpu_ids or list(range(torch.cuda.device_count()))
                parts = []
                for gid in gpu_ids:
                    torch.cuda.synchronize(gid)
                    alloc = torch.cuda.memory_allocated(gid) / (1024**3)
                    peak = torch.cuda.max_memory_allocated(gid) / (1024**3)
                    parts.append(f"cuda:{gid} ~{alloc:.2f} GB (pico ~{peak:.2f} GB)")
                self._status("Modelo carregado — " + ", ".join(parts))
            else:
                torch.cuda.synchronize()
                alloc = torch.cuda.memory_allocated(0) / (1024**3)
                peak = torch.cuda.max_memory_allocated(0) / (1024**3)
                self._status(f"Modelo carregado — VRAM ~{alloc:.2f} GB (pico após load ~{peak:.2f} GB)")

        self._pipe = pipe
        return pipe

    def _try_multi_gpu(self, pipe: Any) -> bool:
        if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
            return False

        gpu_ids = self.gpu_ids
        if not gpu_ids or len(gpu_ids) < 2:
            gpu_ids = list(range(torch.cuda.device_count()))

        primary, secondary = gpu_ids[0], gpu_ids[1]

        try:
            pipe.transformer.to(f"cuda:{primary}")
            pipe.vae.to(f"cuda:{primary}")
            pipe.text_encoder.to(f"cuda:{secondary}")
            if hasattr(pipe, "text_encoder_2") and pipe.text_encoder_2 is not None:
                pipe.text_encoder_2.to(f"cuda:{secondary}")
        except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
            self._log(f"Multi-GPU placement falhou ({exc})")
            return False

        self._patch_cross_device(pipe, primary, secondary)

        for gid in gpu_ids:
            alloc = torch.cuda.memory_allocated(gid) / (1024**3)
            self._log(f"  cuda:{gid} — {alloc:.2f} GB alocados")

        self._multi_gpu = True
        return True

    def _patch_cross_device(self, pipe: Any, primary: int, secondary: int) -> None:
        primary_dev = f"cuda:{primary}"
        secondary_dev = f"cuda:{secondary}"

        pipe._texture2d_primary_device = torch.device(primary_dev)

        if not hasattr(pipe, "_orig_encode_prompt"):
            pipe._orig_encode_prompt = pipe.encode_prompt

        def _patched_encode_prompt(*args: Any, **kwargs: Any) -> Any:
            kwargs["device"] = secondary_dev
            result = pipe._orig_encode_prompt(*args, **kwargs)
            if isinstance(result, torch.Tensor):
                return result.to(primary_dev)
            if isinstance(result, (tuple, list)):
                return type(result)(r.to(primary_dev) if isinstance(r, torch.Tensor) else r for r in result)
            return result

        pipe.encode_prompt = _patched_encode_prompt

        @property  # type: ignore[misc]
        def _patched_execution_device(self: Any) -> torch.device:
            return self._texture2d_primary_device

        pipe.__class__._execution_device = _patched_execution_device

    def unload(self) -> None:
        """Descarrega o pipeline e liberta VRAM."""
        if self._pipe is None:
            return
        del self._pipe
        self._pipe = None
        self._clear_cache()

    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        guidance_scale: float = 3.5,
        num_inference_steps: int = 28,
        seed: int | None = None,
        width: int = 1024,
        height: int = 1024,
        cfg_scale: float | None = None,
        lora_strength: float = 1.0,
        preset: str | None = None,
        ground: str = "auto",
        seamless_fix: bool = True,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera uma textura seamless.

        Args:
            ground: Modo de chão top-down — ``"auto"`` deteta chão/terreno e aplica
                modificadores de viewpoint/iluminação/escala; ``"on"`` força;
                ``"off"`` desliga. Ver :mod:`texture2d.prompt_enhancer`.
            seamless_fix: Aplica :func:`texture2d.tileability.make_seamless` à saída
                (post-process determinístico; a LoRA sozinha não garante bordas
                idênticas — ver validação empírica no docstring de ``LORA_TRIGGER_WORD``).

        Returns:
            Tuple (imagem PIL, metadata dict).
        """
        pipe = self._load_pipeline()

        # Merge preset
        if preset and preset != "None":
            preset_prompt = get_preset_prompt(preset)
            preset_params = get_preset_params(preset)
            if preset_prompt:
                prompt = f"{preset_prompt}, {prompt}" if prompt else preset_prompt
            if preset_params:
                guidance_scale = float(preset_params.get("guidance_scale", guidance_scale))
                num_inference_steps = int(preset_params.get("num_inference_steps", num_inference_steps))
                width = int(preset_params.get("width", width))
                height = int(preset_params.get("height", height))
                if "negative_prompt" in preset_params:
                    negative_prompt = merge_negative_prompt(
                        str(preset_params.get("negative_prompt") or ""),
                        negative_prompt,
                    )

        # Augment seamless
        prompt = augment_prompt_for_seamless(prompt)

        # Ground enhancer (top-down viewpoint / flat lighting / medium scale).
        # Resolve "auto" por heurística de keywords; "on"/"off" é explícito.
        ground_active = ground == "on" or (ground == "auto" and looks_like_ground(prompt))
        if ground_active:
            prompt = enhance_ground_prompt(prompt)
            negative_prompt = enhance_ground_negative(negative_prompt)

        is_valid, error = validate_prompt(prompt, max_length=1200)
        if not is_valid:
            prompt = prompt[:1200]

        if cfg_scale is None:
            cfg_scale = guidance_scale

        if seed is None or seed < 0:
            seed = generate_seed()

        params = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "seed": seed,
            "width": width,
            "height": height,
            "cfg_scale": cfg_scale,
            "lora_strength": lora_strength,
        }

        is_valid, error = validate_params(params)
        if not is_valid:
            raise ValueError(f"Parâmetros inválidos: {error}")

        # Generator
        if self._multi_gpu and self.gpu_ids:
            gen_device = f"cuda:{self.gpu_ids[0]}"
        elif self.device != "cpu":
            gen_device = "cuda"
        else:
            gen_device = "cpu"
        generator = torch.Generator(device=gen_device)
        if seed is not None:
            generator.manual_seed(seed)

        self._clear_cache()

        # Pipeline kwargs — LoRA strength via joint_attention_kwargs
        pipe_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "height": height,
            "width": width,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "generator": generator,
        }
        if lora_strength != 1.0:
            pipe_kwargs["joint_attention_kwargs"] = {"scale": lora_strength}

        self._log("Inferência...")
        out = pipe(**pipe_kwargs)
        image = out.images[0]

        if image is None:
            raise RuntimeError("Nenhuma imagem devolvida pelo pipeline")

        image = image.convert("RGB")

        if seamless_fix:
            from .tileability import make_seamless

            image = make_seamless(image)

        metadata = {
            "seed": seed,
            "prompt_final": prompt,
            "seamless_fix": seamless_fix,
            **params,
        }

        return image, metadata

    def generate_batch(
        self,
        prompts: list[str],
        base_params: dict[str, Any] | None = None,
    ) -> Generator[tuple[Image.Image | None, dict[str, Any], int], None, None]:
        """Gera múltiplas texturas em batch.

        Yields:
            Tuple (imagem | None, metadata, index).
        """
        if base_params is None:
            base_params = {}

        total = len(prompts)
        _logger.info(f"Batch: {total} imagens")

        for idx, prompt in enumerate(prompts):
            try:
                merged = {**DEFAULT_PARAMS, **base_params}
                merged.pop("seed", None)
                merged.pop("prompt", None)

                image, metadata = self.generate(prompt=prompt, **merged)
                yield image, metadata, idx
            except Exception as e:
                _logger.error(f"Erro na imagem {idx + 1}/{total}: {e}")
                yield None, {"error": str(e), "index": idx}, idx

    @staticmethod
    def save_image(image: Image.Image, path: Any, image_format: str | None = None) -> Any:
        """Grava imagem em disco (compatibilidade)."""
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format=image_format)
        return path
