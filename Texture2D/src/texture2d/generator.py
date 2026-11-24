"""Texture2D — gerador de texturas seamless via Stable Diffusion (SD1.5).

Seamless 2.0 — três camadas complementares:

1. **Modo ``late`` (default)**: o UNet corre com padding normal (zeros) durante
   os primeiros ~80% dos steps com *noise rolling* (``torch.roll`` dos latents
   por metade a cada step — o conteúdo não ancora na grelha do canvas), e só na
   fase final troca para ``circular``. Receita do pattern-diffusion (Apache 2.0):
   circular constante degrada FID/CLIP; late + rolling não mede degradação.
   O rolling exige um **scheduler stateless** (DDIM): o DPMSolverMultistep
   extrapola com predições anteriores cacheadas que o roll desalinha (o
   pattern-diffusion usa DDPM pela mesma razão). O modo ``full`` (circular do
   início, comportamento clássico, DPM karras) e ``off`` (SD1.5 puro)
   continuam disponíveis.
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

Precisão do pedido (2026-11-23): os prompts cabem na janela de 77 tokens do
CLIP sem perder a cauda do utilizador (:mod:`texture2d.prompt_budget`); os
parâmetros que o chamador declara explícitos (``explicit``) ganham ao preset;
e ``max_isolated_detail``/``attempts`` voltam a gerar com a seed seguinte
quando sai um pormenor isolado (um nó, uma mancha) que o tiling repetiria.

Herda a infraestrutura partilhada de ``DiffusionGeneratorBase`` (lifecycle,
logging, cache, device resolution, multi-GPU, save_image, batch generation).
"""

from __future__ import annotations

import os
from collections.abc import Collection
from typing import Any

from PIL import Image

from aigamekit_shared.base_generator import DiffusionGeneratorBase
from aigamekit_shared.logging import Logger

from .grading import match_mean_color, parse_hex_color
from .presets import get_preset_params, get_preset_prompt
from .prompt_budget import CLIP_BUDGET, NegativeFit, PromptFit, counter_for, fit_negative, fit_prompt
from .prompt_enhancer import (
    enhance_ground_negative,
    enhance_ground_prompt,
    looks_like_ground,
)
from .tileability import score_isolated_detail, score_structure, score_tileability
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

# Eixo da textura (`axis`): ``v`` = as linhas correm de cima a baixo (o veio de
# uma madeira), ``u`` = da esquerda à direita.
AXES = ("v", "u")
# Só roda quando um eixo domina mesmo: uma corda torcida (diagonal) mede 0,14
# contra 0,07 — não se roda por uma diferença sem significado.
_AXIS_MIN_DOMINANCE = 0.25
_AXIS_DOMINANCE_RATIO = 1.5

# Portões de qualidade: o valor vem dos metadados da geração
# (:func:`_gate_values`) e o limiar de ``max_<nome>``.
GATE_LABELS = {
    "isolated_detail": "Pormenor isolado",
    "banding": "Bandas por eixo",
    "tail_ratio": "Cauda pesada",
}

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
    """Difusão simétrica C¹ das bordas para suavizar uma costura residual.

    Calcula a discrepância exacta na costura (``arr[:, 0] - arr[:, -1]`` e
    ``arr[0, :] - arr[-1, :]``) e distribui-a simetricamente pelas duas bandas
    de largura ``band`` com peso em meio-cosseno (1 na costura → 0 no interior).
    Ao contrário de um cross-fade com ``flip`` (que espelha uma borda sobre a
    outra criando efeito "borboleta"), preserva o grão/textura original dos dois
    lados e zera o salto de fronteira sem descontinuidade de derivada.
    """
    import numpy

    rgb = image if image.mode == "RGB" else image.convert("RGB")
    arr = numpy.array(rgb, dtype=numpy.float32, copy=True)
    height, width = arr.shape[0], arr.shape[1]
    b = band or max(8, min(24, min(width, height) // 32))
    b_w = min(b, width // 2)
    b_h = min(b, height // 2)

    # Costura horizontal (wrap esquerda↔direita): difusão simétrica em cosseno.
    if b_w >= 2:
        t_w = numpy.linspace(0.0, 1.0, b_w, dtype=numpy.float32)
        w_in = 0.5 * (1.0 + numpy.cos(numpy.pi * t_w))  # 1 na borda → 0 no interior
        err_h = arr[:, 0, :] - arr[:, -1, :]
        half_h = (0.5 * err_h)[:, None, :]
        arr[:, :b_w, :] -= half_h * w_in[None, :, None]
        arr[:, -b_w:, :] += half_h * w_in[None, ::-1, None]

    # Costura vertical (wrap topo↔base): difusão simétrica em cosseno.
    if b_h >= 2:
        t_h = numpy.linspace(0.0, 1.0, b_h, dtype=numpy.float32)
        w_in = 0.5 * (1.0 + numpy.cos(numpy.pi * t_h))
        err_v = arr[0, :, :] - arr[-1, :, :]
        half_v = (0.5 * err_v)[None, :, :]
        arr[:b_h, :, :] -= half_v * w_in[:, None, None]
        arr[-b_h:, :, :] += half_v * w_in[::-1, None, None]

    numpy.clip(arr, 0.0, 255.0, out=arr)
    return Image.fromarray(arr.astype("uint8"), mode="RGB")


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


def normalize_axis(axis: str | None) -> str | None:
    """``axis`` pedido: ``v``/``vertical`` → ``v``, ``u``/``h``/``horizontal`` → ``u``.

    Raises:
        ValueError: Um eixo que não é um dos dois.
    """
    if axis is None:
        return None
    value = str(axis).strip().lower()
    if value in ("v", "vertical"):
        return "v"
    if value in ("u", "h", "horizontal"):
        return "u"
    raise ValueError(f"axis inválido: {axis!r} (esperado v ou u)")


def _gate_values(metadata: dict[str, Any]) -> dict[str, float]:
    """O valor de cada portão de qualidade presente nos metadados de uma geração."""
    values: dict[str, float] = {}
    detail = metadata.get("isolated_detail")
    if isinstance(detail, dict):
        values["isolated_detail"] = float(detail["spot"])
    banding = metadata.get("banding")
    if isinstance(banding, dict):
        values["banding"] = max(float(banding["rows"]), float(banding["cols"]))
    tail = metadata.get("tail_ratio")
    if isinstance(tail, dict):
        values["tail_ratio"] = max(float(tail["high"]), float(tail["low"]))
    return values


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
        # True quando o swap ft-mse correu com sucesso (set em _load_pipeline).
        self._vae_swapped = False
        # Cache 1-entry dos embeddings CLIP (evita rodar CLIP 2x no hires e em re-rolls).
        self._cached_embeds: tuple[Any, ...] | None = None

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
        # Config original do checkpoint (antes do swap DPM) — base para o DDIM
        # do modo late (ver _select_scheduler). Instância DPM cacheada para
        # restaurar full/off sem reconstruir.
        self._base_scheduler_config = dict(pipe.scheduler.config)
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
        self._dpm_scheduler = pipe.scheduler

        self._status("Passo 2/4 — VAE ft-mse")
        vae_id_used = self._swap_vae(pipe)
        self._vae_swapped = bool(vae_id_used)

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
        if self.device.startswith("cuda"):
            import contextlib

            with contextlib.suppress(Exception):
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
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

    # ------------------------------------------------------------- scheduler

    def _select_scheduler(self, pipe: Any, seamless_mode: str) -> None:
        """Scheduler por modo — ``late`` exige um stepper stateless (DDIM).

        O noise rolling roda os latents (e o switch tardio troca o padding do
        UNet) a meio da trajectória; o ``DPMSolverMultistepScheduler`` extrapola
        com predições anteriores cacheadas (``model_outputs``) que ficam
        desalinhadas da orientação corrente — o solver mistura versões rodadas
        do mesmo sinal e o resultado degrada (ghost/bandas/cor destruída; foi
        o bug do A/B 2026-09-11). O DDIM é função pura de (amostra, eps, t) —
        o roll é seguro. O pattern-diffusion usa DDPM pela mesma razão.
        ``full``/``off`` não perturbam a trajectória → DPM karras.
        """
        base_cfg = getattr(self, "_base_scheduler_config", None)
        if not base_cfg:
            return  # pipeline mockado (testes) — manter o scheduler do fake
        current = getattr(pipe, "scheduler", None)
        if seamless_mode == "late":
            if getattr(self, "_ddim_scheduler", None) is None:
                from diffusers import DDIMScheduler

                self._ddim_scheduler = DDIMScheduler.from_config(base_cfg)
            if current is not self._ddim_scheduler:
                pipe.scheduler = self._ddim_scheduler
                self._log("Scheduler: DDIM (stateless) para o modo late — noise rolling seguro")
        elif current is not getattr(self, "_dpm_scheduler", current):
            pipe.scheduler = self._dpm_scheduler
            self._log("Scheduler: DPMSolverMultistep(karras)")

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

    def unload(self) -> None:
        """Descarrega pipeline e limpa cache de embeddings CLIP."""
        self._cached_embeds = None
        super().unload()

    def _encode_prompt_cached(
        self,
        pipe: Any,
        *,
        prompt: str,
        negative_prompt: str,
        do_cfg: bool,
        device: Any,
    ) -> tuple[Any, Any]:
        """Codifica prompt no CLIP com cache de 1 entrada (hires + re-rolls)."""
        key = (prompt, negative_prompt, do_cfg, str(device))
        cached = self._cached_embeds
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        prompt_embeds, negative_embeds = pipe.encode_prompt(
            prompt=prompt,
            device=device,
            num_images_per_prompt=1,
            do_classifier_free_guidance=do_cfg,
            negative_prompt=negative_prompt,
        )
        self._cached_embeds = (key, prompt_embeds, negative_embeds)
        return prompt_embeds, negative_embeds

    @staticmethod
    def _circular_upscale_latents(latents: Any, target_h: int, target_w: int) -> Any:
        """Upscale bilinear circular-aware para latents de texturas tileable.

        ``F.interpolate`` padrão faz clamp nas bordas, criando um salto até 4x
        maior na costura antes mesmo do refine. Pad circular de 1 latent +
        ``grid_sample`` garante continuidade periódica exata em qualquer razão.
        """
        import torch
        import torch.nn.functional as F

        _, _, h, w = latents.shape
        padded = F.pad(latents, (1, 1, 1, 1), mode="circular")
        ys = (torch.arange(target_h, device=latents.device, dtype=torch.float32) + 0.5) * (h / target_h) + 0.5
        xs = (torch.arange(target_w, device=latents.device, dtype=torch.float32) + 0.5) * (w / target_w) + 0.5
        gy = 2.0 * (ys + 0.5) / (h + 2) - 1.0
        gx = 2.0 * (xs + 0.5) / (w + 2) - 1.0
        grid_y, grid_x = torch.meshgrid(gy, gx, indexing="ij")
        grid = torch.stack([grid_x, grid_y], dim=-1).unsqueeze(0).expand(latents.shape[0], -1, -1, -1)
        return F.grid_sample(padded.float(), grid, mode="bilinear", padding_mode="border", align_corners=False).to(
            latents.dtype
        )

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
        prompt_embeds, negative_embeds = self._encode_prompt_cached(
            pipe,
            prompt=prompt,
            negative_prompt=negative_prompt,
            do_cfg=do_cfg,
            device=device,
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
        circular-padding do latent + crop (wrap-preserving) — e mesmo assim
        deixa manchas: a attention do bloco médio do VAE vê uma janela por
        tile e os tiles discordam nos limites (2026-11-23, 1024² em tiles:
        pormenor isolado 0,40 contra 0,08 no integral). Por isso a folga
        mede-se depois de esvaziar o cache: cair no tiling por uma leitura
        pessimista é pagar manchas por nada.
        """
        import torch

        vae = pipe.vae
        scaling = float(getattr(vae.config, "scaling_factor", 0.18215))
        z = latents / scaling

        # O VAE original do SD1.5 em fp16 tem o overflow clássico do decoder
        # (cores invertidas/saturadas — é por isso que o ft-mse é default).
        # Sem o swap (``TEXTURE2D_VAE_ID=none``), decodificar em fp32 e repor o
        # dtype no fim — o VAE só corre uma vez por geração, o custo é aceitável.
        upcast = not self._vae_swapped and self.device.startswith("cuda") and "float16" in str(self.torch_dtype)
        if upcast:
            vae = vae.to(torch.float32)
            z = z.to(torch.float32)
            self._log("Decode: VAE do checkpoint em fp16 → upcast fp32 (overflow clássico do decoder)")

        def _integral() -> Image.Image:
            vae.disable_tiling()
            sample = vae.decode(z, return_dict=False)[0]
            return pipe.image_processor.postprocess(sample, output_type="pil")[0]

        def _tiled_circular() -> Image.Image:
            # Pad circular SIMÉTRICO nos 4 lados (4 latents = 32 px em cada borda,
            # mesmo tamanho total h+8 / w+8 do pad unilateral antigo). Antes só
            # fazia pad em bottom/right, deixando topo e esquerda sem contexto
            # circular do lado oposto (score 0.00 → 0.986 medido em 1024²).
            import torch.nn.functional as F

            pad = 4
            pad_px = pad * 8
            zp = F.pad(z, (pad, pad, pad, pad), mode="circular")
            vae.enable_tiling()
            sample = vae.decode(zp, return_dict=False)[0]
            img = pipe.image_processor.postprocess(sample, output_type="pil")[0]
            return img.crop((pad_px, pad_px, pad_px + width, pad_px + height))

        use_tiling = bool(vae_tiling) if vae_tiling is not None else False
        if vae_tiling is None and max(width, height) > 768 and self.device.startswith("cuda"):
            # O cache reservado do PyTorch conta como ocupado para o driver: sem o
            # esvaziar antes de medir, uma GPU de 6 GB parecia sem folga e caía no
            # tiling — que deixa manchas (medido a 1024²: pormenor isolado 0,40
            # em tiles contra 0,08 no integral, qualquer que fosse o refine).
            self._clear_cache()
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
        finally:
            if upcast:
                pipe.vae.to(self.torch_dtype)  # repor fp16 para o resto do worker

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
        explicit: Collection[str] | None = None,
        max_isolated_detail: float | None = None,
        max_banding: float | None = None,
        max_tail_ratio: float | None = None,
        axis: str | None = None,
        match_color: str | None = None,
        attempts: int = 1,
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
                últimos ~20% — melhor FID; corre em DDIM stateless porque o
                rolling desalinha a história do DPM multistep),
                ``"full"`` (circular do início, comportamento clássico),
                ``"off"`` (SD1.5 puro, sem toro).
            refine_steps: Steps do refine hires quando o alvo é >512² (default 12).
            vae_tiling: ``None`` = auto (integral sempre que cabe); ``True``
                força tiling circular-aware; ``False`` força integral.
            seam_heal: Auto-heal da costura quando o score fica < 0.85.
            hires: Alvos >512² são gerados à nativa + latent upscale + refine
                (hires-fix). ``False`` gera directamente à resolução pedida.
            should_abort: Callback cooperativo de cancelamento.
            on_step: Callback ``(step_1based, total)`` de progresso.
            explicit: Parâmetros que o chamador definiu a sério (entre
                ``"guidance_scale"``, ``"num_inference_steps"``, ``"width"`` e
                ``"height"``): o preset só preenche os restantes. ``None``/vazio
                mantém o comportamento antigo — o preset ganha sempre.
            max_isolated_detail: Limiar (0..1) do pormenor isolado
                (:func:`texture2d.tileability.score_isolated_detail`); ``None``
                (default) não avalia.
            max_banding: Limiar das bandas por eixo
                (:func:`texture2d.tileability.score_axis_banding`): tábuas com
                frestas, linhas que atravessam a textura. Medido: 0,85 em
                tábuas, 0,26-0,44 em veio de madeira direito. ``None`` não avalia.
            max_tail_ratio: Limiar da cauda pesada
                (:func:`texture2d.tileability.score_tail_ratio`): flocos ou
                pontos sobre um fundo liso. Medido: 24 numa cal com flocos,
                1,5-3,2 no resto. ``None`` não avalia.
            axis: ``"v"`` ou ``"u"`` quando a textura tem uma direção: roda 90°
                se as linhas saírem no eixo errado (o SD1.5 troca o veio da
                madeira). ``None`` (default) nunca roda.
            match_color: Cor média pedida, ``#RRGGBB``
                (:func:`texture2d.grading.match_mean_color`): o SD1.5 tem um
                prior de tom médio e não sai do castanho só com o prompt.
                ``None`` não gradua.
            attempts: Número máximo de gerações quando há limiar: cada
                reprovação volta a gerar com ``seed + 1``; se nenhuma passar,
                devolve a que passa mais perto (menor razão valor/limiar). Sem
                limiar só gera uma vez.
            **_ignored: Parâmetros legacy aceites e ignorados (compat de chamada).

        Returns:
            Tuple (imagem PIL, metadata dict).

        Raises:
            ValueError: ``axis`` inválido ou ``match_color`` que não é ``#RRGGBB``.
        """
        attempts = max(1, int(attempts))
        axis = normalize_axis(axis)
        if match_color is not None:
            parse_hex_color(match_color)
        explicit_keys = frozenset(explicit or ())
        limits = {
            name: max(0.0, float(limit))
            for name, limit in (
                ("isolated_detail", max_isolated_detail),
                ("banding", max_banding),
                ("tail_ratio", max_tail_ratio),
            )
            if limit is not None
        }
        seed_requested = self._resolve_seed(seed)
        count = counter_for(self._load_pipeline())
        call = dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            guidance_scale=guidance_scale,
            num_inference_steps=num_inference_steps,
            width=width,
            height=height,
            preset=preset,
            ground=ground,
            seamless_mode=seamless_mode,
            refine_steps=refine_steps,
            vae_tiling=vae_tiling,
            seam_heal=seam_heal,
            hires=hires,
            should_abort=should_abort,
            on_step=on_step,
            explicit=explicit_keys,
            axis=axis,
            match_color=match_color,
            count=count,
        )

        best: tuple[Image.Image, dict[str, Any], float] | None = None
        current = seed_requested
        for attempt in range(1, attempts + 1):
            image, metadata = self._generate_once(seed=current, **call)
            if not limits:
                return image, metadata
            values = _gate_values(metadata)
            # A razão ao limiar: ≤ 1 passa em todos; a melhor de todas é a de
            # menor razão máxima (não há ordem entre um nó e uma fresta).
            worst = max((values.get(name, 0.0) / limit for name, limit in limits.items()), default=0.0)
            if best is None or worst < best[2]:
                best = (image, metadata, worst)
            passed = worst <= 1.0
            metadata["quality_gates"] = {
                "passed": passed,
                **{
                    name: {"value": values.get(name), "max": limit, "passed": values.get(name, 0.0) <= limit}
                    for name, limit in limits.items()
                },
            }
            if "isolated_detail" in limits:
                metadata["isolated_detail_gate"] = {
                    "max": limits["isolated_detail"],
                    "passed": metadata["quality_gates"]["isolated_detail"]["passed"],
                }
            if passed:
                if attempt > 1:
                    self._log(f"Portões de qualidade passaram na tentativa {attempt} (seed {current})")
                return image, metadata
            failed = [
                f"{GATE_LABELS[name]} {values.get(name, 0.0):.3f} > {limit:.3f}"
                for name, limit in limits.items()
                if values.get(name, 0.0) > limit
            ]
            if attempt < attempts:
                self._log(f"{'; '.join(failed)} — a repetir com a seed {current + 1}")
            current += 1

        # Nenhuma passou: a mais perto, com os portões assinalados como falhados.
        assert best is not None
        image, metadata, _ = best
        metadata["seed_requested"] = seed_requested
        metadata["attempts"] = attempts
        metadata["quality_gates"] = {**metadata["quality_gates"], "passed": False, "best_of": attempts}
        if "isolated_detail" in limits:
            metadata["isolated_detail_gate"] = {
                "max": limits["isolated_detail"],
                "passed": metadata["quality_gates"]["isolated_detail"]["passed"],
                "best_of": attempts,
            }
        return image, metadata

    def _generate_once(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        guidance_scale: float,
        num_inference_steps: int,
        seed: int,
        width: int,
        height: int,
        preset: str | None,
        ground: str,
        seamless_mode: str,
        refine_steps: int,
        vae_tiling: bool | None,
        seam_heal: bool,
        hires: bool,
        should_abort: Any,
        on_step: Any,
        explicit: Collection[str],
        axis: str | None,
        match_color: str | None,
        count: Any,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Uma geração com a seed já resolvida (ver :meth:`generate`)."""
        import torch

        if seamless_mode not in SEAMLESS_MODES:
            raise ValueError(f"seamless_mode inválido: {seamless_mode!r} (esperado {SEAMLESS_MODES})")
        if self._requested_torch_compile and seamless_mode == "late":
            # torch.compile "assava" o padding_mode no grafo — o switch tardio não
            # teria efeito; cair para o modo clássico.
            self._log("torch.compile ativo: seamless_mode late → full (compile fixa o padding)")
            seamless_mode = "full"

        pipe = self._load_pipeline()
        self._select_scheduler(pipe, seamless_mode)
        user_prompt = (prompt or "").strip()
        user_negative = negative_prompt or ""

        # Preset — prompt base e defaults de guidance/steps/resolução. Só preenche
        # o que o chamador NÃO declarou explícito: um `-s 28` ou um `--quality
        # high` escolhido a sério não pode ser pisado pelo preset.
        preset_prompt = ""
        preset_negative = ""
        preset_applied: list[str] = []
        if preset and preset != "None":
            preset_prompt = get_preset_prompt(preset) or ""
            preset_params = get_preset_params(preset) or {}
            defaults = {
                "guidance_scale": float,
                "num_inference_steps": int,
                "width": int,
                "height": int,
            }
            filled: dict[str, Any] = {}
            for key, cast in defaults.items():
                if key in preset_params and key not in explicit:
                    filled[key] = cast(preset_params[key])
                    preset_applied.append(key)
            guidance_scale = filled.get("guidance_scale", guidance_scale)
            num_inference_steps = filled.get("num_inference_steps", num_inference_steps)
            width = filled.get("width", width)
            height = filled.get("height", height)
            preset_negative = str(preset_params.get("negative_prompt") or "")

        # Composição antiga (preset à cabeça): é o texto final sempre que couber
        # na janela do CLIP — um asset gerado antes reproduz-se com a mesma seed.
        if preset_prompt and user_prompt:
            legacy_prompt = f"{preset_prompt}, {user_prompt}"
        else:
            legacy_prompt = user_prompt or preset_prompt

        # Ground enhancer (top-down viewpoint / flat lighting / superfície próxima).
        ground_active = ground == "on" or (ground == "auto" and looks_like_ground(legacy_prompt))
        if ground_active:
            legacy_prompt = enhance_ground_prompt(legacy_prompt)
            own_prompt = enhance_ground_prompt(user_prompt)
            user_negative = enhance_ground_negative(user_negative)
        else:
            needs_texture = "texture" not in legacy_prompt.lower()
            own_prompt = f"{user_prompt}, seamless texture" if needs_texture and user_prompt else user_prompt
            if needs_texture:
                legacy_prompt = f"{legacy_prompt}, seamless texture"

        # Não cabe: o pedido do utilizador vem inteiro e o preset só empresta o
        # que couber (ver prompt_budget). O negativo só muda quando não cabe.
        if count(legacy_prompt) <= CLIP_BUDGET:
            p = legacy_prompt
            prompt_fit = PromptFit(p, count(p))
        else:
            prompt_fit = fit_prompt(own_prompt, preset_prompt, count)
            p = prompt_fit.text
            lost = []
            if prompt_fit.dropped:
                lost.append(f"o preset perde {len(prompt_fit.dropped)} cláusulas")
            if prompt_fit.user_truncated:
                lost.append("o prompt do utilizador foi cortado")
            self._log(
                f"Prompt: {count(legacy_prompt)} tokens > {CLIP_BUDGET} — o utilizador vem primeiro ({'; '.join(lost)})"
            )

        # Prioridade quando não cabe: a base da ferramenta (costura, texto,
        # marca de água) é o que torna a textura utilizável — um negativo de 60
        # termos do utilizador não a pode pôr fora; o preset é o primeiro a ceder.
        legacy_negative = merge_negative_prompt(SD_BASE_NEGATIVE, merge_negative_prompt(preset_negative, user_negative))
        if count(legacy_negative) <= CLIP_BUDGET:
            negative_fit = NegativeFit(legacy_negative, count(legacy_negative))
        else:
            negative_fit = fit_negative([(SD_BASE_NEGATIVE, 2), (user_negative, 1), (preset_negative, 0)], count)
            self._log(
                f"Negative: {count(legacy_negative)} tokens > {CLIP_BUDGET} — sem repetições, "
                f"{len(negative_fit.dropped)} cláusulas de fora"
            )
        negative_prompt = negative_fit.text

        is_valid, error = validate_prompt(p, max_length=1200)
        if not is_valid:
            p = p[:1200]

        params = {
            "prompt": p,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "seed": seed,
            "width": width,
            "height": height,
        }
        sched = getattr(pipe, "scheduler", None)

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

        generator = self._build_generator(seed)

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
                    seamless_mode=seamless_mode,
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

        # Eixo: o SD1.5 não obedece a "vertical grain" — mede-se em vez de
        # pedir. Roda 90° quando as linhas saíram no eixo errado; a rotação
        # preserva a tileabilidade (o toro é simétrico) e troca os eixos.
        rotated = False
        if axis is not None:
            banding = score_structure(image).banding
            wanted, other = (banding.rows, banding.cols) if axis == "v" else (banding.cols, banding.rows)
            if image.width != image.height:
                self._log("Eixo: a imagem não é quadrada — não roda")
            elif wanted >= _AXIS_MIN_DOMINANCE and wanted >= _AXIS_DOMINANCE_RATIO * other:
                self._log(f"Eixo {axis}: as linhas saíram no eixo errado ({banding.summary()}) — roda 90°")
                image = image.transpose(Image.Transpose.ROTATE_90)
                rotated = True

        # Cor: o prior de tom médio do SD1.5 pisa a paleta do prompt.
        graded = None
        if match_color is not None:
            image, graded = match_mean_color(image, match_color)

        # Pormenor isolado (um nó, uma mancha): o tiling repete-o em cada célula.
        detail = score_isolated_detail(image)
        # Estrutura: tábuas com frestas e flocos passam os portões de cima.
        structure = score_structure(image)

        metadata = {
            "backend": "sd-circular",
            "model_id": self.model_id,
            "vae_id": self._vae_id or None,
            "seed": seed,
            "prompt_final": p,
            "prompt": p,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "width": width,
            "height": height,
            "seamless_mode": seamless_mode,
            "scheduler": type(sched).__name__ if sched is not None else None,
            "hires": do_hires,
            "gen_width": gen_w if do_hires else None,
            "gen_height": gen_h if do_hires else None,
            "refine_steps": refine_steps if do_hires else None,
            "vae_tiled_decode": used_tiling,
            "tileability": report.to_dict(),
            "seam_healed": healed,
            "isolated_detail": detail.to_dict(),
            "banding": structure.banding.to_dict(),
            "tail_ratio": structure.tail.to_dict(),
            "axis": {"wanted": axis, "rotated": rotated} if axis is not None else None,
            "match_color": graded.to_dict() if graded is not None else None,
            "preset": preset or None,
            "preset_params_applied": preset_applied or None,
            "prompt_tokens": prompt_fit.tokens,
            "negative_prompt_tokens": negative_fit.tokens,
            "prompt_user_truncated": True if prompt_fit.user_truncated else None,
            "preset_dropped": list(prompt_fit.dropped) or None,
            "negative_dropped": list(negative_fit.dropped) or None,
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
        seamless_mode: str = "late",
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

        pipe_kwargs = pipe_kwargs_builder()
        # Em hires, pré-codifica o prompt 1 única vez no cache e reutiliza no
        # pipe + _refine_latents (evita 2º pass CLIP / swap de group offload).
        if do_hires and callable(getattr(pipe, "encode_prompt", None)):
            exec_dev = getattr(pipe, "_execution_device", None) or getattr(pipe, "device", self.device)
            do_cfg = guidance_scale > 1.0
            p_emb, n_emb = self._encode_prompt_cached(
                pipe,
                prompt=prompt,
                negative_prompt=negative_prompt,
                do_cfg=do_cfg,
                device=exec_dev,
            )
            pipe_kwargs.pop("prompt", None)
            pipe_kwargs.pop("negative_prompt", None)
            pipe_kwargs["prompt_embeds"] = p_emb
            if do_cfg and n_emb is not None:
                pipe_kwargs["negative_prompt_embeds"] = n_emb

        out = pipe(**pipe_kwargs)
        # output_type="latent": o SD devolve o tensor direto; FLUX-like
        # devolvem lista — normalizar para (1, 4, h, w).
        latents = out.images
        if isinstance(latents, (list, tuple)):
            latents = latents[0]
        if latents is None or latents.dim() != 4:
            raise RuntimeError("Nenhum latent devolvido pelo pipeline")

        if do_hires:
            target_h, target_w = height // 8, width // 8
            if seamless_mode != "off":
                latents = self._circular_upscale_latents(latents, target_h, target_w)
            else:
                latents = torch.nn.functional.interpolate(
                    latents,
                    size=(target_h, target_w),
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
