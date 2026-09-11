"""Texture2D — gerador de texturas seamless via Stable Diffusion (SD1.5).

Seamless 2.0 — três camadas complementares:

1. **Modo ``late`` (default)**: o UNet corre com padding normal (zeros) durante
   os primeiros ~80% dos steps com *noise rolling* (``torch.roll`` dos latents
   por metade a cada step — o conteúdo não ancora na grelha do canvas), e só na
   fase final troca para ``circular``. Receita do pattern-diffusion (Apache 2.0):
   circular constante degrada FID/CLIP; late + rolling não mede degradação.
   O modo ``full`` (circular do início, comportamento clássico) e ``off``
   (SD1.5 puro) continuam disponíveis.
2. **Decode controlado**: o pipeline corre com ``output_type="latent"`` e o VAE
   decodifica aqui — integral (sem tiling) sempre que a resolução o permite,
   porque o ``tiled_decode`` do diffusers fatura o latent sem wrap e parte a
   costura exacta no espaço-píxel. Quando o tiling é inevitável (1024² em VRAM
   apertada), o latent é primeiro "circular-padded" e o resultado recortado.
3. **Hires via latent upscale + refine** (hires-fix clássico): alvos >512² são
   gerados à nativa (max dim 512), o latent é upscale bilinear e um refine
   curto (strength controlado) detalha à resolução-alvo — em vez de gerar
   directamente acima da nativa do SD1.5 (duplicações/artefactos).

Extras de qualidade: VAE ``stabilityai/sd-vae-ft-mse`` por defeito
(``TEXTURE2D_VAE_ID=none`` desliga), negative prompt com termos de costura, e
score de tileability gravado no sidecar com auto-heal opcional da costura.

Herda a infraestrutura partilhada de ``DiffusionGeneratorBase`` (lifecycle,
logging, cache, device resolution, multi-GPU, save_image, batch generation).
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
from .tileability import score_tileability
from .utils import validate_params, validate_prompt

_logger = Logger()

# Modelo por defeito: Stable Diffusion v1.5 (runwaymlblab/stable-diffusion-v1-5 é o
# mirror canónico; o repo "stable-diffusion-v1-5/stable-diffusion-v1-5" também serve).
DEFAULT_MODEL_ID = "stable-diffusion-v1-5/stable-diffusion-v1-5"

# VAE fine-tuned clássico para SD1.5 — melhor decode (menos banding/artefactos
# fp16). ``TEXTURE2D_VAE_ID=none`` mantém o VAE do checkpoint.
DEFAULT_VAE_ID = "stabilityai/sd-vae-ft-mse"

# Defaults afinados para SD1.5.
DEFAULT_GUIDANCE = 7.0  # CFG real (o FLUX distilled usava 3.5 — baixo demais p/ SD).
DEFAULT_STEPS = 30
DEFAULT_RESOLUTION = 512  # Resolução nativa do SD1.5.
DEFAULT_REFINE_STEPS = 12  # Refine do hires (target > 512²).
REFINE_STRENGTH = 0.55  # Denoise parcial do refine (hires-fix clássico: 0.5-0.6).

# Seamless: fração de steps com padding normal + noise rolling antes de ligar o
# circular (receita pattern-diffusion: ~80%).
SEAMLESS_LATE_FRACTION = 0.8
SEAMLESS_MODES = ("late", "full", "off")

# Score de tileability abaixo do qual o auto-heal da costura é aplicado.
SEAM_HEAL_THRESHOLD = 0.85

# Negative base para qualidade de textura (SD1.5 beneficia sempre de um negative).
SD_BASE_NEGATIVE = "blurry, low quality, watermark, text, signature, frame, border, visible seam, tiling artifacts"

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


def _default_vae_id() -> str:
    """VAE por defeito (``TEXTURE2D_VAE_ID``; ``none``/vazio = VAE do checkpoint)."""
    vae_id = os.environ.get("TEXTURE2D_VAE_ID", DEFAULT_VAE_ID).strip()
    return "" if vae_id.lower() in ("none", "base", "") else vae_id


def patch_conv2d_circular(module: Any) -> int:
    """Troca o padding de todas as ``Conv2d`` para circular (wrap).

    Returns:
        Número de camadas ``Conv2d`` alteradas.
    """
    return set_conv2d_padding(module, "circular")


def set_conv2d_padding(module: Any, mode: str) -> int:
    """Define o ``padding_mode`` de todas as ``Conv2d`` (``circular`` | ``zeros``).

    Returns:
        Número de camadas ``Conv2d`` alteradas.
    """
    import torch

    patched = 0
    for m in module.modules():
        if isinstance(m, torch.nn.Conv2d):
            m.padding_mode = mode
            patched += 1
    return patched


def heal_seam(image: Image.Image, band: int | None = None) -> Image.Image:
    """Cross-fade das bandas de borda para suavizar uma costura residual.

    Mistura as últimas ``band`` colunas com o conteúdo das primeiras (peso em
    rampa) e o mesmo nas linhas — a descontinuidade da costura espalha-se pela
    banda em vez de concentrar-se num degrau. Só deve ser usado como recurso
    quando o score de tileability fica abaixo do threshold.
    """
    import numpy

    arr = numpy.asarray(image.convert("RGB"), dtype=numpy.float32)
    height, width = arr.shape[0], arr.shape[1]
    b = band or max(8, min(24, min(width, height) // 32))

    # Costura vertical (wrap esquerda↔direita): banda direita ← conteúdo esquerdo.
    ramp = numpy.linspace(0.0, 1.0, b, dtype=numpy.float32)[None, :, None]
    arr[:, width - b :, :] = (1.0 - ramp) * arr[:, width - b :, :] + ramp * numpy.flip(arr[:, :b, :], axis=1)

    # Costura horizontal (wrap topo↔base): banda inferior ← conteúdo superior.
    ramp_v = numpy.linspace(0.0, 1.0, b, dtype=numpy.float32)[:, None, None]
    arr[height - b :, :, :] = (1.0 - ramp_v) * arr[height - b :, :, :] + ramp_v * numpy.flip(arr[:b, :, :], axis=0)

    return Image.fromarray(arr.clip(0, 255).astype("uint8"), mode="RGB")


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
    """Gerador de texturas seamless via Stable Diffusion (modos late/full/off).

    Herda de ``DiffusionGeneratorBase``: warmup, unload, _log, _clear_cache,
    _resolve_seed, _build_generator, _report_vram, generate_batch, save_image.

    Group offload + CUDA streams default ON (padrão das tools 2D): em GPUs
    onde o full-GPU não teria folga, os pesos do UNet/CLIP streamam por grupos
    (pico ≈ ativação). Kill-switch: ``TEXTURE2D_GROUP_OFFLOAD=0``.
    """

    GROUP_OFFLOAD_ENV = "TEXTURE2D_GROUP_OFFLOAD"

    # Gate de folga comum das tools 2D: full-GPU só com pico ≤70% do orçamento.
    FULL_GPU_BUDGET_FRACTION = 0.70

    def __init__(
        self,
        device: str | None = None,
        memory_efficient: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        group_offload: bool = True,
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
        # nativo dos FLUX), mas o UNet do SD1.5 em bf16 produz NaNs em algumas
        # camadas — forçar float16.
        import torch

        self.torch_dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        self._requested_torch_compile = bool(torch_compile)
        self._unet_convs: list[Any] = []
        self._vae_convs: list[Any] = []
        self._vae_id: str = _default_vae_id()

        if self.verbose:
            _logger.info(f"device={self.device} dtype={self.torch_dtype} model={self.model_id}")

    # ------------------------------------------------------------------ load

    def _swap_vae(self, pipe: Any) -> str:
        """Troca o VAE pelo fine-tune ft-mse (best-effort, com fallback)."""
        if not self._vae_id or self._vae_id.lower() in ("none", "base"):
            return ""
        try:
            from diffusers import AutoencoderKL

            kwargs: dict[str, Any] = {"torch_dtype": self.torch_dtype}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            pipe.vae = AutoencoderKL.from_pretrained(self._vae_id, **kwargs)
            return self._vae_id
        except Exception as exc:
            self._log(f"VAE {self._vae_id} indisponível ({type(exc).__name__}: {exc}); a usar o VAE do checkpoint")
            return ""

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        import torch
        from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline

        from aigamekit_shared.lowvram import get_footprint

        kwargs: dict[str, Any] = {
            "torch_dtype": self.torch_dtype,
            "safety_checker": None,
            "requires_safety_checker": False,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        self._status("Passo 1/4 — from_pretrained (SD1.5)")
        self._log(f"Carregando {self.model_id} (SD + seamless 2.0)...")
        # Preferir a variante fp16 (metade do download/disco); nem todos os
        # repos a publicam (ex. fine-tunes) — fallback para os pesos default.
        if self.torch_dtype is not None and "float16" in str(self.torch_dtype):
            try:
                pipe = StableDiffusionPipeline.from_pretrained(self.model_id, variant="fp16", **kwargs)
            except (OSError, ValueError):
                pipe = StableDiffusionPipeline.from_pretrained(self.model_id, **kwargs)
        else:
            pipe = StableDiffusionPipeline.from_pretrained(self.model_id, **kwargs)
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)

        self._status("Passo 2/4 — VAE ft-mse")
        vae_id_used = self._swap_vae(pipe)

        # Padding por camada é controlado por generate() (modo late alterna em
        # runtime); aqui só recolhemos as listas. O VAE decodifica apenas no fim
        # (equivalente a "late" por natureza) — circular permanente.
        self._unet_convs = [m for m in pipe.unet.modules() if isinstance(m, torch.nn.Conv2d)]
        self._vae_convs = [m for m in pipe.vae.modules() if isinstance(m, torch.nn.Conv2d)]
        n_vae = set_conv2d_padding(pipe.vae, "circular")
        self._log(
            f"Circular padding: {len(self._unet_convs)} convs no UNet (runtime), "
            f"{n_vae} no VAE (sempre — decode no fim)"
        )

        # Colocação unificada (planner lowvram): full-GPU com folga, group
        # offload + streams quando não haveria margem, VAE tiling/slicing +
        # attention slicing como chunks. SD1.5: attrs unet/text_encoder (sem
        # text_encoder_2); o VAE fica de fora do GO (conflita com tiling).
        # target_resolution = nativa: o decode/tiling decide-se por-request em
        # _decode_latents (o tiled_decode do diffusers parte a costura circular).
        self._status("Passo 3/4 — colocação")
        self._clear_cache()
        self._reset_peak_mem_stats()
        plan = self._place_with_planner(
            pipe,
            get_footprint("sd15-base"),
            # SD1.5 corre sempre fp16 (sem quant runtime) — o planner não pode
            # recomendar degraus que a tool não aplica.
            allow_quant=("none",),
            offload_modules=("unet", "text_encoder"),
            target_resolution=DEFAULT_RESOLUTION,
            **self._go_planner_kwargs(full_gpu_budget_fraction=self.FULL_GPU_BUDGET_FRACTION),
        )
        self._maybe_compile_transformer(pipe, plan)
        self._maybe_apply_channels_last(pipe, plan)
        self._maybe_select_attention_backend(pipe, plan)
        self._plan = plan
        if vae_id_used:
            self._log(f"VAE: {vae_id_used}")

        self._status("Passo 4/4 — pronto")
        self._pipe = pipe
        return pipe

    # ------------------------------------------------------- padding runtime

    def _set_unet_padding(self, mode: str) -> None:
        for m in self._unet_convs:
            m.padding_mode = mode

    def _set_vae_padding(self, mode: str) -> None:
        for m in self._vae_convs:
            m.padding_mode = mode

    # -------------------------------------------------------------- callback

    def _make_step_callback(
        self,
        *,
        total_steps: int,
        seamless_mode: str,
        progress_total: int,
        progress_offset: int,
        should_abort: Any,
        on_step: Any,
    ):
        """Callback único: noise rolling + switch circular tardio + progresso/abort."""
        import contextlib

        import torch

        from aigamekit_shared.diffusion_control import GenerationAborted

        switch_idx = min(int(total_steps * SEAMLESS_LATE_FRACTION), max(total_steps - 1, 0))
        state = {"switched": seamless_mode != "late", "rolls": 0}

        def callback_on_step_end(pipeline: Any, step: Any, timestep: Any, callback_kwargs: dict) -> dict:
            i = int(step)
            if seamless_mode == "late" and not state["switched"]:
                if i < switch_idx:
                    lat = callback_kwargs.get("latents")
                    if lat is not None:
                        sh = (lat.shape[2] // 2, lat.shape[3] // 2)
                        callback_kwargs["latents"] = torch.roll(lat, shifts=sh, dims=(2, 3))
                        state["rolls"] += 1
                else:
                    # Repor a orientação original (rolls ímpares = meia-volta)
                    # antes de fixar o circular na fase de healing.
                    if state["rolls"] % 2 == 1:
                        lat = callback_kwargs.get("latents")
                        if lat is not None:
                            sh = (lat.shape[2] // 2, lat.shape[3] // 2)
                            callback_kwargs["latents"] = torch.roll(lat, shifts=sh, dims=(2, 3))
                            state["rolls"] += 1
                    self._set_unet_padding("circular")
                    state["switched"] = True
                    self._log(f"Seamless: circular ON no step {i + 1}/{total_steps}")
            if on_step is not None:
                with contextlib.suppress(Exception):
                    on_step(progress_offset + i + 1, progress_total)
            if should_abort is not None and should_abort():
                if hasattr(pipeline, "_interrupt"):
                    pipeline._interrupt = True
                raise GenerationAborted("cancelled during diffusion")
            return callback_kwargs

        return callback_on_step_end

    # ---------------------------------------------------------------- refine

    def _refine_latents(
        self,
        pipe: Any,
        latents: Any,
        *,
        prompt: str,
        negative_prompt: str,
        guidance_scale: float,
        refine_steps: int,
        generator: Any,
        should_abort: Any,
        on_step: Any,
        progress_total: int,
        progress_offset: int,
    ) -> Any:
        """Hires-fix: upscale do latent já feito; refine com denoise parcial.

        ``add_noise`` no timestep correspondente a ``REFINE_STRENGTH`` e loop
        standard (CFG manual) a partir daí — o conteúdo sobrevive e ganha detalhe
        à resolução-alvo, mantendo o toro (convs circulares ativas no modo late).
        """
        import torch

        from aigamekit_shared.diffusion_control import GenerationAborted

        device = latents.device
        do_cfg = guidance_scale > 1.0
        prompt_embeds, negative_embeds = pipe.encode_prompt(
            prompt=prompt,
            device=device,
            num_images_per_prompt=1,
            do_classifier_free_guidance=do_cfg,
            negative_prompt=negative_prompt,
        )
        embeds = torch.cat([negative_embeds, prompt_embeds]) if do_cfg else prompt_embeds

        scheduler = pipe.scheduler
        scheduler.set_timesteps(refine_steps, device=latents.device)
        timesteps = scheduler.timesteps
        start = int(len(timesteps) * (1.0 - REFINE_STRENGTH))
        start = min(max(start, 0), max(len(timesteps) - 1, 0))
        if start < 1:
            start = 1  # refine de 0 steps = manter o upscale puro

        noise = torch.randn(latents.shape, generator=generator, device=latents.device, dtype=latents.dtype)
        t_start = timesteps[start]
        if t_start.dim() == 0:
            t_start = t_start.reshape(1)  # add_noise espera 1-D (um t por amostra)
        latents = scheduler.add_noise(latents, noise, t_start)

        for i, t in enumerate(timesteps[start:], start=start):
            if should_abort is not None and should_abort():
                raise GenerationAborted("cancelled during refine")
            lat_in = torch.cat([latents] * 2) if do_cfg else latents
            lat_in = scheduler.scale_model_input(lat_in, t)
            noise_pred = pipe.unet(lat_in, t, encoder_hidden_states=embeds).sample
            if do_cfg:
                n_uncond, n_text = noise_pred.chunk(2)
                noise_pred = n_uncond + guidance_scale * (n_text - n_uncond)
            latents = scheduler.step(noise_pred, t, latents).prev_sample
            if on_step is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    on_step(progress_offset + (i - start) + 1, progress_total)
        return latents

    # ---------------------------------------------------------------- decode

    def _decode_latents(
        self, pipe: Any, latents: Any, *, width: int, height: int, vae_tiling: bool | None
    ) -> tuple[Image.Image, bool]:
        """Decodifica com política própria — o pipeline NUNCA decoda (latent out).

        Integral (sem tiling) sempre que possível: o ``tiled_decode`` do
        diffusers fatura sem wrap e parte a costura circular. Tiling apenas por
        força maior (flag explícita ou VRAM livre curta a >768²), sempre com
        circular-padding do latent + crop (wrap-preserving).
        """
        import torch

        vae = pipe.vae
        scaling = float(getattr(vae.config, "scaling_factor", 0.18215))
        z = latents / scaling

        def _integral() -> Image.Image:
            vae.disable_tiling()
            sample = vae.decode(z, return_dict=False)[0]
            return pipe.image_processor.postprocess(sample, output_type="pil")[0]

        def _tiled_circular() -> Image.Image:
            # Pad circular (bottom ← topo, right ← esquerda) para os tiles das
            # bordas verem o contexto do lado oposto; crop do pad no fim.
            pad = 8  # 8 latents = 64 px ≥ blend do tiled_decode (overlap 0.25)
            zp = torch.cat([z, z[:, :, :pad, :]], dim=2)
            zp = torch.cat([zp, zp[:, :, :, :pad]], dim=3)
            vae.enable_tiling()
            sample = vae.decode(zp, return_dict=False)[0]
            img = pipe.image_processor.postprocess(sample, output_type="pil")[0]
            return img.crop((0, 0, width, height))

        use_tiling = bool(vae_tiling) if vae_tiling is not None else False
        if vae_tiling is None and max(width, height) > 768 and self.device.startswith("cuda"):
            try:
                free_b, _total_b = torch.cuda.mem_get_info()
                use_tiling = (free_b / (1024**2)) < 3000  # decode integral 1024² ≈ 3 GiB fp16
            except Exception:
                use_tiling = True

        try:
            image = _tiled_circular() if use_tiling else _integral()
            return image.convert("RGB"), use_tiling
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower() or use_tiling:
                raise
            # OOM no decode integral → tentar o tiled circular-aware.
            self._clear_cache()
            self._log(f"OOM no decode integral ({exc}); retry com VAE tiling circular")
            return _tiled_circular().convert("RGB"), True

    # --------------------------------------------------------------- generate

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
        seamless_mode: str = "late",
        refine_steps: int = DEFAULT_REFINE_STEPS,
        vae_tiling: bool | None = None,
        seam_heal: bool = True,
        hires: bool = True,
        should_abort: Any = None,
        on_step: Any = None,
        **_ignored: Any,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera uma textura seamless.

        Args:
            prompt: Prompt do utilizador.
            negative_prompt: Prompt negativo (CFG nativo do SD1.5).
            guidance_scale: CFG scale (default 7.0).
            num_inference_steps: Passos da difusão principal (default 30).
            seed: Seed determinística; ``None`` = aleatória.
            width: Largura em pixéis (default 512, nativo do SD1.5).
            height: Altura em pixéis (default 512).
            preset: Nome de preset de material (ver ``presets.TEXTURE_PRESETS``).
            ground: Modo chão top-down — ``"auto"`` deteta chão/terreno; ``"on"``
                força; ``"off"`` desliga. Ver :mod:`texture2d.prompt_enhancer`.
            seamless_mode: ``"late"`` (default; noise rolling + circular nos
                últimos ~20% — melhor FID), ``"full"`` (circular do início,
                comportamento clássico), ``"off"`` (SD1.5 puro, sem toro).
            refine_steps: Steps do refine hires quando o alvo é >512² (default 12).
            vae_tiling: ``None`` = auto (integral sempre que cabe); ``True``
                força tiling circular-aware; ``False`` força integral.
            seam_heal: Auto-heal da costura quando o score fica < 0.85.
            hires: Alvos >512² são gerados à nativa + latent upscale + refine
                (hires-fix). ``False`` gera directamente à resolução pedida.
            should_abort: Callback cooperativo de cancelamento.
            on_step: Callback ``(step_1based, total)`` de progresso.
            **_ignored: Parâmetros legacy aceites e ignorados (compat de chamada).

        Returns:
            Tuple (imagem PIL, metadata dict).
        """
        import torch

        if seamless_mode not in SEAMLESS_MODES:
            raise ValueError(f"seamless_mode inválido: {seamless_mode!r} (esperado {SEAMLESS_MODES})")
        if self._requested_torch_compile and seamless_mode == "late":
            # torch.compile "assava" o padding_mode no grafo — o switch tardio não
            # teria efeito; cair para o modo clássico.
            self._log("torch.compile ativo: seamless_mode late → full (compile fixa o padding)")
            seamless_mode = "full"

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

        # Hires: alvo > 512² → difusão à nativa (max dim 512, aspect preservado)
        # + latent upscale + refine. Directo acima da nativa duplica conteúdo.
        do_hires = hires and max(width, height) > DEFAULT_RESOLUTION
        gen_w, gen_h = width, height
        if do_hires:
            f = max(width, height) / DEFAULT_RESOLUTION
            gen_w = max(256, round(width / f / 8) * 8)
            gen_h = max(256, round(height / f / 8) * 8)
            refine_steps = max(1, int(refine_steps))

        generator = self._build_generator(resolved_seed)

        self._clear_cache()
        mode_label = {"late": "late (roll + circular tardio)", "full": "full (circular)", "off": "off (SD puro)"}[
            seamless_mode
        ]
        self._log(
            f"Inferência (SD {seamless_mode}) — {gen_w}x{gen_h}"
            + (f" → latent upscale → refine {refine_steps} @ {width}x{height}" if do_hires else "")
            + f" [{mode_label}]"
        )

        # Estado de padding por chamada: full → circular já; late/off → zeros.
        self._set_unet_padding("circular" if seamless_mode == "full" else "zeros")
        self._set_vae_padding("zeros" if seamless_mode == "off" else "circular")

        progress_total = num_inference_steps + (refine_steps if do_hires else 0)
        try:
            # inference_mode: o decode/refine chamam o VAE/UNet diretamente (fora
            # do pipeline) — sem isto, o autograd rastreia ativações (OOM) e o
            # postprocess falha em tensores com grad.
            with torch.inference_mode():
                image, used_tiling = self._generate_and_decode(
                    pipe,
                    pipe_kwargs_builder=lambda: {
                        "prompt": p,
                        "negative_prompt": negative_prompt,
                        "guidance_scale": guidance_scale,
                        "num_inference_steps": num_inference_steps,
                        "width": gen_w,
                        "height": gen_h,
                        "generator": generator,
                        "output_type": "latent",  # decode controlado em _decode_latents
                        "callback_on_step_end": self._make_step_callback(
                            total_steps=num_inference_steps,
                            seamless_mode=seamless_mode,
                            progress_total=progress_total,
                            progress_offset=0,
                            should_abort=should_abort,
                            on_step=on_step,
                        ),
                        "callback_on_step_end_tensor_inputs": ["latents"],
                    },
                    prompt=p,
                    negative_prompt=negative_prompt,
                    guidance_scale=guidance_scale,
                    num_inference_steps=num_inference_steps,
                    refine_steps=refine_steps if do_hires else 0,
                    do_hires=do_hires,
                    width=width,
                    height=height,
                    generator=generator,
                    should_abort=should_abort,
                    on_step=on_step,
                    progress_total=progress_total,
                    vae_tiling=vae_tiling,
                )
        finally:
            # Baseline determinística entre chamadas no mesmo worker.
            self._set_unet_padding("zeros")

        # Tileability: score no sidecar + auto-heal opcional da costura.
        report = score_tileability(image)
        healed = False
        if seam_heal and report.score < SEAM_HEAL_THRESHOLD:
            self._log(f"Seam heal: score {report.score:.4f} < {SEAM_HEAL_THRESHOLD} — cross-fade da banda de borda")
            image = heal_seam(image)
            report = score_tileability(image)
            healed = True

        metadata = {
            "backend": "sd-circular",
            "model_id": self.model_id,
            "vae_id": self._vae_id or None,
            "seed": resolved_seed,
            "prompt_final": p,
            "prompt": p,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "width": width,
            "height": height,
            "seamless_mode": seamless_mode,
            "hires": do_hires,
            "gen_width": gen_w if do_hires else None,
            "gen_height": gen_h if do_hires else None,
            "refine_steps": refine_steps if do_hires else None,
            "vae_tiled_decode": used_tiling,
            "tileability": report.to_dict(),
            "seam_healed": healed,
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}
        return image, metadata

    def _generate_and_decode(
        self,
        pipe: Any,
        *,
        pipe_kwargs_builder: Any,
        prompt: str,
        negative_prompt: str,
        guidance_scale: float,
        num_inference_steps: int,
        refine_steps: int,
        do_hires: bool,
        width: int,
        height: int,
        generator: Any,
        should_abort: Any,
        on_step: Any,
        progress_total: int,
        vae_tiling: bool | None,
    ) -> tuple[Image.Image, bool]:
        """Difusão (+ refine hires) e decode — corre dentro de ``inference_mode``."""
        import torch

        out = pipe(**pipe_kwargs_builder())
        # output_type="latent": o SD devolve o tensor direto; FLUX-like
        # devolvem lista — normalizar para (1, 4, h, w).
        latents = out.images
        if isinstance(latents, (list, tuple)):
            latents = latents[0]
        if latents is None or latents.dim() != 4:
            raise RuntimeError("Nenhum latent devolvido pelo pipeline")

        if do_hires:
            latents = torch.nn.functional.interpolate(
                latents,
                size=(height // 8, width // 8),
                mode="bilinear",
                align_corners=False,
            )
            # O refine é a fase de healing do modo late — circular já ligado
            # no fim da fase principal; "full" já ligou no início.
            latents = self._refine_latents(
                pipe,
                latents,
                prompt=prompt,
                negative_prompt=negative_prompt,
                guidance_scale=guidance_scale,
                refine_steps=refine_steps,
                generator=generator,
                should_abort=should_abort,
                on_step=on_step,
                progress_total=progress_total,
                progress_offset=num_inference_steps,
            )

        return self._decode_latents(pipe, latents, width=width, height=height, vae_tiling=vae_tiling)
