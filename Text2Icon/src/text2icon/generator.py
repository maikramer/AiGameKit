"""Text2Icon — gerador de ícones via pipeline Sana (diffusers).

Dois transformers suportados, escolhidos por ``hw_auto`` (ver ``hardware.py``)
conforme a VRAM disponível:

    - **Standard** (default, >4 GB VRAM): ``STANDARD_TRANSFORMER_ID``
      (``Efficient-Large-Model/Sana_600M_512px_diffusers``), fp16/bf16 nativo,
      opcionalmente comprimido em runtime via SDNQ (int4/uint8) sobre o
      transformer — ``transformer_quant_preset``.
    - **Ternário** (fallback, hardware modesto ≤4 GB VRAM):
      ``TERNARY_TRANSFORMER_ID`` (``clark-labs/clark-air-sana-1.6b-1.58bit``),
      comprimido a ~1.85 bits/weight pelo Clark Labs — 8.6x mais pequeno que
      fp16. Já vem pré-comprimido no checkpoint; não faz sentido empilhar SDNQ
      por cima.

Ambos carregam como drop-in no ``diffusers``:

    1. ``SanaTransformer2DModel`` de ``self.transformer_id``.
    2. ``SanaPipeline`` de ``Efficient-Large-Model/Sana_1600M_512px_diffusers``
       (VAE + Gemma text encoder + scheduler, partilhado entre variantes) com
       o transformer injetado.

Pipeline nativo 512px, 20 passos, guidance 4.5. O Gemma text encoder (~4.9 GB
fp16, o componente mais pesado) pode também ser quantizado via SDNQ
(``quant_preset``) — decisivo em GPUs de 6-8 GB.

Para ícones transparentes (RGBA), o gerador pode aplicar ``rembg`` (U2Net) após
a inferência, ativado por ``remove_background=True``.

Herda infraestrutura de ``DiffusionGeneratorBase`` (lifecycle, logging, cache,
multi-GPU, save_image, batch). Mantém apenas a lógica única: arquitetura
two-model (transformer + pipeline), quantização SDNQ runtime do encoder Gemma
e do transformer standard, e o pós-processamento de remoção de fundo via rembg.
"""

from __future__ import annotations

import os
import re
from typing import Any

from PIL import Image

from gamedev_shared.base_generator import DiffusionGeneratorBase

# Re-export para backward compat (testes/módulos antigos importam de text2icon.generator).
from gamedev_shared.base_generator import torch_dtype_for as _torch_dtype_for  # noqa: F401

from .utils import validate_params, validate_prompt

# Transformer standard (fp16/bf16 nativo, 512px) — default para hardware normal.
STANDARD_TRANSFORMER_ID = "Efficient-Large-Model/Sana_600M_512px_diffusers"
# Transformer Clark Air (ternário 1.58-bit, descompactado bf16, drop-in diffusers)
# — fallback para hardware modesto (≤4 GB VRAM), decidido por hw_auto.
TERNARY_TRANSFORMER_ID = "clark-labs/clark-air-sana-1.6b-1.58bit"
# Pipeline base (VAE + Gemma text encoder + scheduler) — Sana 1.6B 512px,
# partilhado por ambos os transformers (só o transformer é trocado).
DEFAULT_PIPELINE_ID = "Efficient-Large-Model/Sana_1600M_512px_diffusers"

DEFAULT_TRANSFORMER_ID = STANDARD_TRANSFORMER_ID

# Alias compat: o "modelo" do text2icon é o transformer.
DEFAULT_MODEL_ID = DEFAULT_TRANSFORMER_ID

BASE_ICON_INSTRUCTIONS = (
    "app icon, simple, centered, bold, clean background, high contrast, "
    "flat design, crisp edges, single subject, readable at small size"
)

DEFAULT_PARAMS: dict[str, Any] = {
    "guidance_scale": 4.5,
    "num_inference_steps": 20,
    "seed": None,
    "width": 512,
    "height": 512,
    "negative_prompt": "",
}


def _transformer_id() -> str:
    """ID do transformer Clark Air (env ``TEXT2ICON_MODEL_ID`` ganha)."""
    return os.environ.get("TEXT2ICON_MODEL_ID", DEFAULT_TRANSFORMER_ID)


def default_model_id() -> str:
    """Modelo por defeito (ou ``TEXT2ICON_MODEL_ID``)."""
    return _transformer_id()


def augment_prompt_for_icon(prompt: str) -> str:
    """Acrescenta instruções de ícone app-icon automaticamente.

    Se o utilizador já menciona "icon" / "app icon" / "logo", não duplica.
    """
    p = (prompt or "").strip()
    if not p:
        return p
    if re.search(
        r"\b(icon|app icon|logo|emblem|badge|glyph)\b",
        p,
        flags=re.IGNORECASE,
    ):
        return p
    return f"{BASE_ICON_INSTRUCTIONS}, {p}"


class SanaIconGenerator(DiffusionGeneratorBase):
    """Gerador de ícones via pipeline Sana (two-model: transformer + pipeline).

    Herda de ``DiffusionGeneratorBase``: warmup, unload, _log, _clear_cache,
    _status, _place_with_planner, save_image, generate_batch.
    """

    def __init__(
        self,
        device: str | None = None,
        low_vram: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        quant_preset: str | None = None,
        transformer_quant_preset: str | None = None,
        torch_compile: bool | None = None,
        torch_compile_mode: str = "default",
        step_cache: str | None = None,
        channels_last: bool = False,
    ) -> None:
        super().__init__(
            device=device,
            verbose=verbose,
            model_id=model_id or _transformer_id(),
            cache_dir=cache_dir,
            gpu_ids=gpu_ids,
            memory_efficient=low_vram,
            torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            step_cache=step_cache,
            channels_last=channels_last,
        )
        # Text2Icon usa ``low_vram`` (vs ``memory_efficient`` nas outras tools).
        self.low_vram = low_vram

        # Two-model: o transformer é swappable; o pipeline (VAE + Gemma + scheduler)
        # é partilhado. ``self.model_id`` (do base) fica = transformer_id para compat.
        self.transformer_id = self.model_id
        self.pipeline_id = DEFAULT_PIPELINE_ID

        # Auto-quantização do Gemma encoder em GPUs modestas (< 8 GB).
        # ``quant_preset`` explícito ganha; ``"none"`` desliga; ``None`` = auto.
        if quant_preset in (None, "auto") and self.device != "cpu":
            from gamedev_shared.gpu import gpu_total_mib

            try:
                vram_mib = gpu_total_mib(0)
                if vram_mib and vram_mib < 8192:  # < 8 GB → quantizar encoder
                    self.quant_preset = "sdnq-int4"
                    if self.verbose:
                        self._log(f"auto-quant encoder int4 (VRAM {vram_mib} MiB < 8192)")
                else:
                    self.quant_preset = None
            except Exception:
                self.quant_preset = quant_preset if quant_preset != "auto" else None
        elif quant_preset == "none":
            self.quant_preset = None
        else:
            self.quant_preset = quant_preset

        # Auto-quantização do transformer principal (só faz sentido no standard —
        # o ternário já vem pré-comprimido a ~1.85 bits/weight no checkpoint).
        is_ternary = self.transformer_id == TERNARY_TRANSFORMER_ID
        if transformer_quant_preset in (None, "auto") and self.device != "cpu" and not is_ternary:
            from gamedev_shared.gpu import gpu_total_mib

            try:
                vram_mib = gpu_total_mib(0)
                if vram_mib and vram_mib < 6144:
                    self.transformer_quant_preset = "sdnq-int4"
                elif vram_mib and vram_mib < 10240:
                    self.transformer_quant_preset = "sdnq-uint8"
                else:
                    self.transformer_quant_preset = None
                if self.verbose and self.transformer_quant_preset:
                    self._log(f"auto-quant transformer {self.transformer_quant_preset} (VRAM {vram_mib} MiB)")
            except Exception:
                self.transformer_quant_preset = transformer_quant_preset if transformer_quant_preset != "auto" else None
        elif transformer_quant_preset == "none" or is_ternary:
            self.transformer_quant_preset = None
        else:
            self.transformer_quant_preset = transformer_quant_preset

        if self.verbose:
            self._log(f"device={self.device} dtype={self.torch_dtype} transformer={self.transformer_id}")

    def _maybe_quantize_encoder(self, pipe: Any, preset: str) -> None:
        """Quantiza o text encoder (Gemma 2B) via SDNQ em runtime.

        O Gemma 2B é o componente mais pesado do pipeline (~4.9 GB em fp16).
        SDNQ int4 reduz para ~2.4 GB — decisivo em GPUs de 6-8 GB.
        """
        try:
            from gamedev_shared.sdnq import is_available, quantize_model
        except ImportError:
            self._log("gamedev_shared.sdnq indisponível; encoder fica em fp16")
            return

        if not is_available():
            self._log("SDNQ indisponível; encoder fica em fp16")
            return

        encoder = getattr(pipe, "text_encoder", None)
        if encoder is None:
            return

        self._status(f"Quantização SDNQ {preset} do Gemma text encoder (poupa ~2.5 GB)")
        quant_device = "cuda" if self.device != "cpu" else "cpu"
        try:
            pipe.text_encoder = quantize_model(encoder, preset, quantization_device=quant_device, return_device="cpu")
            self._log(f"SDNQ {preset} aplicado ao text_encoder")
        except Exception as exc:
            self._log(f"quantização do text_encoder falhou ({exc}); fica em fp16")
        self._clear_cache()

    def _maybe_quantize_transformer(self, pipe: Any, preset: str) -> None:
        """Quantiza o transformer principal via SDNQ em runtime (só no standard).

        Não é chamado para o transformer ternário (já pré-comprimido) — ver
        guarda em ``__init__``.
        """
        try:
            from gamedev_shared.sdnq import is_available, quantize_model
        except ImportError:
            self._log("gamedev_shared.sdnq indisponível; transformer fica em fp16/bf16")
            return

        if not is_available():
            self._log("SDNQ indisponível; transformer fica em fp16/bf16")
            return

        transformer = getattr(pipe, "transformer", None)
        if transformer is None:
            return

        self._status(f"Quantização SDNQ {preset} do transformer")
        quant_device = "cuda" if self.device != "cpu" else "cpu"
        try:
            pipe.transformer = quantize_model(
                transformer, preset, quantization_device=quant_device, return_device="cpu"
            )
            self._log(f"SDNQ {preset} aplicado ao transformer")
        except Exception as exc:
            self._log(f"quantização do transformer falhou ({exc}); fica em fp16/bf16")
        self._clear_cache()

    def _preflight_download(self, repo_id: str) -> None:
        """Garante o checkpoint em disco antes do load (download com resume/progresso)."""
        try:
            from gamedev_shared.model_download import ensure_model

            ensure_model(repo_id, cache_dir=self.cache_dir, on_status=self._status)
        except Exception as exc:
            self._log(f"preflight download falhou ({exc}); a deixar from_pretrained tratar")

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        # Allocator com expandable_segments ANTES do 1º alloc CUDA — reduz fragmentação.
        from gamedev_shared.quantization import set_memory_optimization_env

        set_memory_optimization_env()

        from diffusers import SanaPipeline, SanaTransformer2DModel

        self._preflight_download(self.transformer_id)
        self._preflight_download(self.pipeline_id)

        # Passo 1: transformer (standard fp16/bf16 ou ternário Clark Air, drop-in).
        # O repo do transformer ternário é privado → precisa de HF_TOKEN (gamedev_shared).
        self._status(f"Passo 1/3 — transformer ({self.transformer_id})")
        self._log(f"Carregando transformer {self.transformer_id}...")
        try:
            from gamedev_shared.hf import get_hf_token

            hf_token = get_hf_token()
        except Exception:
            hf_token = None
        transformer_kwargs: dict[str, Any] = {"subfolder": "transformer", "torch_dtype": self.torch_dtype}
        if hf_token:
            transformer_kwargs["token"] = hf_token
        if self.cache_dir:
            transformer_kwargs["cache_dir"] = self.cache_dir
        transformer = SanaTransformer2DModel.from_pretrained(self.transformer_id, **transformer_kwargs)

        # Passo 2: pipeline base com o transformer custom injetado.
        self._status(f"Passo 2/3 — pipeline Sana 1600M 512px + Gemma ({self.pipeline_id})")
        kwargs: dict[str, Any] = {"transformer": transformer, "torch_dtype": self.torch_dtype}
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir
        if hf_token:
            kwargs["token"] = hf_token
        pipe = SanaPipeline.from_pretrained(self.pipeline_id, **kwargs)

        # VAE em float32 para estabilidade de decode (referência: app.py Clark Air).
        # O VAE do Sana produz artefactos em bf16/fp16; fp32 garante decode limpo.
        import torch

        pipe.vae.to(torch.float32)

        # VAE tiling reduz o pico de VRAM no decode (importante em GPUs modestas).
        try:
            pipe.vae.enable_tiling()
        except Exception:
            self._log("VAE tiling indisponível, a ignorar.")

        # Quantização opcional do transformer principal via SDNQ (só standard;
        # o ternário já vem pré-comprimido — ver guarda em __init__).
        if self.transformer_quant_preset and self.device != "cpu":
            self._maybe_quantize_transformer(pipe, self.transformer_quant_preset)

        # Quantização opcional do text encoder (Gemma 2B) via SDNQ.
        # Reduz a VRAM do encoder de ~4.9 GB (fp16) para ~2.4 GB (int4) — decisivo
        # em GPUs de 6 GB. Ativada por ``quant_preset`` ou auto-detecção de VRAM.
        if self.quant_preset and self.device != "cpu":
            self._maybe_quantize_encoder(pipe, self.quant_preset)

        # Passo 3: colocação unificada via planner (multi-GPU / group offload / full-GPU).
        self._clear_cache()
        self._reset_peak_mem_stats()
        self._status("Passo 3/3 — colocação via planner")

        from gamedev_shared.lowvram import get_footprint

        # Footprint: se SDNQ foi aplicado (quant_preset/transformer_quant_preset),
        # o modelo é menor. Usar allow_quant=("none",) para não duplicar redução.
        quantized = bool(self.quant_preset or self.transformer_quant_preset)
        footprint = get_footprint("sana-sprint-600m")
        if quantized:
            # Modelo já quantizado (int4/uint8) — footprint real é menor.
            from gamedev_shared.lowvram import ModelFootprint

            footprint = ModelFootprint(
                fp16_weights_gib=footprint.weights_gib("sdnq-int4"),
                activation_gib=1.5,
                largest_module_gib=footprint.largest_gib("sdnq-int4"),
                architecture="sana",
            )

        placement_plan = self._place_with_planner(
            pipe,
            footprint,
            allow_quant=("none",),
            model_attr="transformer",
        )

        # Kernel opts: compile + step-cache + channels_last + attention (sage/flash).
        self._maybe_compile_transformer(pipe, placement_plan)
        self._maybe_apply_step_cache(pipe, placement_plan)
        self._maybe_apply_channels_last(pipe, placement_plan)
        self._maybe_select_attention_backend(pipe, placement_plan)

        self._pipe = pipe
        return pipe

    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        guidance_scale: float = 4.5,
        num_inference_steps: int = 20,
        seed: int | None = None,
        width: int = 512,
        height: int = 512,
        remove_background: bool = False,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera um ícone a partir do prompt.

        Args:
            prompt: Descrição textual do ícone.
            negative_prompt: Prompt negativo (ex. "blurry, low quality").
            guidance_scale: Guidance scale (CFG). Default 4.5.
            num_inference_steps: Passos de difusão. Default 20.
            seed: Seed reprodutível. ``None`` = aleatório.
            width: Largura da imagem (múltiplo de 8). Default 512.
            height: Altura da imagem (múltiplo de 8). Default 512.
            remove_background: Se ``True``, aplica ``rembg`` para alpha RGBA.

        Returns:
            Tuple (imagem PIL, metadata dict).
        """
        pipe = self._load_pipeline()

        # Augment icon instructions
        prompt = augment_prompt_for_icon(prompt)

        is_valid, _error = validate_prompt(prompt, max_length=1000)
        if not is_valid:
            prompt = prompt[:1000]

        resolved_seed = self._resolve_seed(seed)

        params = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "seed": resolved_seed,
            "width": width,
            "height": height,
            "remove_background": remove_background,
        }

        is_valid, error = validate_params(params)
        if not is_valid:
            raise ValueError(f"Parâmetros inválidos: {error}")

        generator = self._build_generator(resolved_seed)

        self._clear_cache()

        # Pipeline kwargs
        pipe_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "height": height,
            "width": width,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "generator": generator,
        }
        if negative_prompt:
            pipe_kwargs["negative_prompt"] = negative_prompt

        self._log("Inferência Sana...")
        out = pipe(**pipe_kwargs)
        image = out.images[0]

        if image is None:
            raise RuntimeError("Nenhuma imagem devolvida pelo pipeline")

        image = image.convert("RGB")

        if remove_background:
            from .bg_removal import remove_background as _rbg

            image = _rbg(image)

        metadata = {
            "seed": resolved_seed,
            "prompt_final": prompt,
            "model": self.transformer_id,
            "transformer_quant_preset": self.transformer_quant_preset,
            "encoder_quant_preset": self.quant_preset,
            "remove_background": remove_background,
            **params,
        }

        return image, metadata
