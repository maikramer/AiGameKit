"""Texture2D — gerador de texturas seamless via Stable Diffusion + circular padding.

Backend único: um UNet convolucional (SD1.5) permite o truque clássico de trocar o
``padding_mode`` de todas as ``Conv2d`` (UNet + VAE) para ``"circular"`` — o campo
recetivo dá a volta na imagem e a saída ladrilha **por construção**, sem
pós-processamento. O negative prompt funciona nativamente (CFG real, sem o custo
2x do true-CFG do FLUX distilled) e SD1.5 fp16 cabe inteiro numa GPU de 6 GiB.

Herda a infraestrutura partilhada de ``DiffusionGeneratorBase`` (lifecycle, logging,
cache, device resolution, multi-GPU, save_image, batch generation).
"""

from __future__ import annotations

import os
from typing import Any

from PIL import Image

from aigamekit_shared.base_generator import DiffusionGeneratorBase
from aigamekit_shared.logging import Logger

from .presets import get_preset_params, get_preset_prompt
from .prompt_enhancer import (
    enhance_ground_negative,
    enhance_ground_prompt,
    looks_like_ground,
)
from .utils import validate_params, validate_prompt

_logger = Logger()

# Modelo por defeito: Stable Diffusion v1.5 (runwaymlblab/stable-diffusion-v1-5 é o
# mirror canónico; o repo "stable-diffusion-v1-5/stable-diffusion-v1-5" também serve).
DEFAULT_MODEL_ID = "stable-diffusion-v1-5/stable-diffusion-v1-5"

# Defaults afinados para SD1.5 + circular padding.
DEFAULT_GUIDANCE = 7.0  # CFG real (o FLUX distilled usava 3.5 — baixo demais p/ SD).
DEFAULT_STEPS = 30
DEFAULT_RESOLUTION = 512  # Resolução nativa do SD1.5.

# Negative base para qualidade de textura (SD1.5 beneficia sempre de um negative).
SD_BASE_NEGATIVE = "blurry, low quality, watermark, text, signature, frame, border"

DEFAULT_PARAMS: dict[str, Any] = {
    "guidance_scale": DEFAULT_GUIDANCE,
    "num_inference_steps": DEFAULT_STEPS,
    "seed": None,
    "width": DEFAULT_RESOLUTION,
    "height": DEFAULT_RESOLUTION,
    "negative_prompt": "",
}


def _default_model_id() -> str:
    return os.environ.get("TEXTURE2D_MODEL_ID", DEFAULT_MODEL_ID)


def default_model_id() -> str:
    """Modelo SD por defeito (ou ``TEXTURE2D_MODEL_ID``)."""
    return _default_model_id()


def patch_conv2d_circular(module: Any) -> int:
    """Troca o padding de todas as ``Conv2d`` para circular (wrap).

    Aplicado ao UNet e ao VAE, faz a convolução "dar a volta" nas bordas — a
    imagem gerada é tileable por construção em ambos os eixos.

    Returns:
        Número de camadas ``Conv2d`` alteradas.
    """
    import torch

    patched = 0
    for m in module.modules():
        if isinstance(m, torch.nn.Conv2d):
            m.padding_mode = "circular"
            patched += 1
    return patched


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


class TextureGenerator(DiffusionGeneratorBase):
    """Gerador de texturas seamless via Stable Diffusion + circular padding.

    Herda de ``DiffusionGeneratorBase``: warmup, unload, _log, _clear_cache,
    _resolve_seed, _build_generator, _report_vram, generate_batch, save_image.
    """

    def __init__(
        self,
        device: str | None = None,
        memory_efficient: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        group_offload: bool = False,
        torch_compile: bool | None = None,
        torch_compile_mode: str = "default",
        channels_last: bool = False,
    ) -> None:
        super().__init__(
            device=device,
            verbose=verbose,
            model_id=model_id or _default_model_id(),
            cache_dir=cache_dir,
            gpu_ids=gpu_ids,
            memory_efficient=memory_efficient,
            group_offload=group_offload,
            torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            channels_last=channels_last,
        )
        # SD1.5 é treinado em fp16/float32. A base resolve bfloat16 em CUDA (formato
        # nativo dos FLUX/Sana), mas o UNet do SD1.5 em bf16 produz NaNs em algumas
        # camadas — forçar float16.
        import torch

        self.torch_dtype = torch.float16 if self.device.startswith("cuda") else torch.float32

        if self.verbose:
            _logger.info(f"device={self.device} dtype={self.torch_dtype} model={self.model_id}")

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline

        kwargs: dict[str, Any] = {
            "torch_dtype": self.torch_dtype,
            "safety_checker": None,
            "requires_safety_checker": False,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        self._status("Passo 1/3 — from_pretrained (SD1.5)")
        self._log(f"Carregando {self.model_id} (SD + circular padding)...")
        # Preferir a variante fp16 (metade do download/disco); nem todos os
        # repos a publicam (ex. fine-tunes) — fallback para os pesos default.
        if self.torch_dtype is not None and "float16" in str(self.torch_dtype):
            try:
                pipe = StableDiffusionPipeline.from_pretrained(self.model_id, variant="fp16", **kwargs)
            except (OSError, ValueError):
                pipe = StableDiffusionPipeline.from_pretrained(self.model_id, **kwargs)
        else:
            pipe = StableDiffusionPipeline.from_pretrained(self.model_id, **kwargs)

        self._status("Passo 2/3 — scheduler + circular padding")
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
        n_unet = patch_conv2d_circular(pipe.unet)
        n_vae = patch_conv2d_circular(pipe.vae)
        self._log(f"Circular padding: {n_unet} convs no UNet, {n_vae} no VAE")

        self._status("Passo 3/3 — mover pipeline para device")
        self._clear_cache()
        self._reset_peak_mem_stats()
        pipe.to(self.device)
        if self.device.startswith("cuda"):
            self._report_vram()

        # SD1.5 full-GPU: plan fictício offload=none para helpers partilhados.
        from types import SimpleNamespace

        plan = SimpleNamespace(offload="none")
        self._maybe_compile_transformer(pipe, plan)
        self._maybe_apply_channels_last(pipe, plan)
        self._maybe_select_attention_backend(pipe, plan)

        self._pipe = pipe
        return pipe

    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        guidance_scale: float = DEFAULT_GUIDANCE,
        num_inference_steps: int = DEFAULT_STEPS,
        seed: int | None = None,
        width: int = DEFAULT_RESOLUTION,
        height: int = DEFAULT_RESOLUTION,
        preset: str | None = None,
        ground: str = "auto",
        should_abort: Any = None,
        on_step: Any = None,
        **_ignored: Any,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera uma textura seamless (tileable por construção via circular padding).

        Args:
            prompt: Prompt do utilizador.
            negative_prompt: Prompt negativo (CFG nativo do SD1.5).
            guidance_scale: CFG scale (default 7.0).
            num_inference_steps: Passos de inferência (default 30).
            seed: Seed determinística; ``None`` = aleatória.
            width: Largura em pixéis (default 512, nativo do SD1.5).
            height: Altura em pixéis (default 512).
            preset: Nome de preset de material (ver ``presets.TEXTURE_PRESETS``).
            ground: Modo chão top-down — ``"auto"`` deteta chão/terreno; ``"on"``
                força; ``"off"`` desliga. Ver :mod:`texture2d.prompt_enhancer`.
            **_ignored: Parâmetros legacy aceites e ignorados (compat de chamada).

        Returns:
            Tuple (imagem PIL, metadata dict).
        """
        pipe = self._load_pipeline()
        p = (prompt or "").strip()

        # Merge preset — o preset pode definir prompt base, guidance/steps/resolução
        # e negative prompt. O prompt do utilizador é sempre prefixado ao do preset.
        if preset and preset != "None":
            preset_prompt = get_preset_prompt(preset)
            preset_params = get_preset_params(preset)
            if preset_prompt:
                p = f"{preset_prompt}, {p}" if p else preset_prompt
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

        # Ground enhancer (top-down viewpoint / flat lighting / superfície próxima).
        # "auto" resolve por heurística de keywords; "on"/"off" é explícito.
        ground_active = ground == "on" or (ground == "auto" and looks_like_ground(p))
        if ground_active:
            p = enhance_ground_prompt(p)
            negative_prompt = enhance_ground_negative(negative_prompt)
        elif "texture" not in p.lower():
            p = f"{p}, seamless texture"
        negative_prompt = f"{negative_prompt}, {SD_BASE_NEGATIVE}" if negative_prompt.strip() else SD_BASE_NEGATIVE

        is_valid, error = validate_prompt(p, max_length=1200)
        if not is_valid:
            p = p[:1200]

        resolved_seed = self._resolve_seed(seed)

        params = {
            "prompt": p,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "seed": resolved_seed,
            "width": width,
            "height": height,
        }

        is_valid, error = validate_params(params)
        if not is_valid:
            raise ValueError(f"Parâmetros inválidos: {error}")

        generator = self._build_generator(resolved_seed)

        from aigamekit_shared.diffusion_control import attach_step_hooks

        self._clear_cache()
        self._log("Inferência (SD circular)...")
        pipe_kwargs: dict[str, Any] = {
            "prompt": p,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "width": width,
            "height": height,
            "generator": generator,
        }
        attach_step_hooks(
            pipe_kwargs,
            num_inference_steps=num_inference_steps,
            should_abort=should_abort,
            on_step=on_step,
        )
        out = pipe(**pipe_kwargs)
        image = out.images[0]
        if image is None:
            raise RuntimeError("Nenhuma imagem devolvida pelo pipeline")

        image = image.convert("RGB")
        metadata = {
            "backend": "sd-circular",
            "model_id": self.model_id,
            "seed": resolved_seed,
            "prompt_final": p,
            "prompt": p,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "width": width,
            "height": height,
        }
        return image, metadata
