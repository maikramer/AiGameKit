"""Texture2D — gerador de texturas seamless via pattern-diffusion (SD2-base, Apache-2.0).

Via B da modernização do Texture2D: pattern-diffusion + Materialize (PBR gerado por
subprocess Materialize separado — este gerador produz apenas RGB difuso).

A tileabilidade é garantida **por construção** (não só por prompt), combinando dois
mecanismos da receita publicada no model card da pattern-diffusion:

1. **Noise-rolling** nos primeiros 80% dos passos — ``torch.roll(latents, (64,64), (2,3))``
   para que features proeminentes sejam seamless através da borda.
2. **Late circular padding** — a partir dos 80% dos passos, ``padding_mode="circular"``
   nos ``Conv2d`` do UNet e VAE. Aplicar circular padding desde o início prejudica
   FID/CLIP; aplicá-lo tarde (depois do noise-rolling) elimina seams sem perda mensurável.

Ver: https://huggingface.co/Arrexel/pattern-diffusion
"""

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
from .utils import generate_seed, validate_params, validate_prompt

_logger = Logger()

DEFAULT_MODEL_ID = "Arrexel/pattern-diffusion"

# Texto auxiliar para reforçar tileabilidade no prompt (mecanismo secundário;
# o mecanismo principal é o noise-rolling + late circular padding).
BASE_TEXTURE_INSTRUCTIONS = (
    "seamless, tileable, repeatable, repeating pattern, perfectly looping texture, "
    "no visible seams, no borders, no frame, no text, no watermark"
)

# Defaults SD2-base nativo: 512², guidance 7.5, 50 passos.
DEFAULT_PARAMS: dict[str, Any] = {
    "guidance_scale": 7.5,
    "num_inference_steps": 50,
    "seed": None,
    "width": 512,
    "height": 512,
    "negative_prompt": "",
}

SEAMLESS_METHODS: tuple[str, ...] = ("late", "roll", "full", "none")
QUANT_MODES: tuple[str, ...] = ("none", "fp8", "nf4")

# Fração de passos (80%) até onde se faz noise-rolling e a partir de onde se ativa
# o circular padding — coincide com a receita ótima do model card da pattern-diffusion.
_SEAMLESS_THRESHOLD_FRACTION = 0.8


def _model_id() -> str:
    """ID do modelo por defeito (ou ``TEXTURE2D_MODEL_ID``)."""
    return os.environ.get("TEXTURE2D_MODEL_ID", DEFAULT_MODEL_ID)


def default_model_id() -> str:
    """Modelo por defeito: ``Arrexel/pattern-diffusion`` (SD2-base, Apache-2.0)."""
    return _model_id()


def _torch_dtype_for(device: str) -> torch.dtype:
    """Dtype por defeito conforme o device (bf16 se suportado, senão fp16)."""
    d = device.lower()
    if d.startswith("cpu"):
        return torch.float32
    if d.startswith("cuda") and torch.cuda.is_available():
        if getattr(torch.cuda, "is_bf16_supported", lambda: False)():
            return torch.bfloat16
        return torch.float16
    return torch.float32


def augment_prompt_for_seamless(prompt: str) -> str:
    """Acrescenta instruções de textura seamless/tileable automaticamente.

    Mecanismo leve/suplementar — a tileabilidade real vem do noise-rolling +
    late circular padding. Se o utilizador já menciona seamless/tileable, não duplica.
    """
    p = (prompt or "").strip()
    if not p:
        return p
    if re.search(
        r"\b(seamless|tileable|tiling|repeatable|repeating|repeat)\b",
        p,
        flags=re.IGNORECASE,
    ):
        return p
    return f"{BASE_TEXTURE_INSTRUCTIONS}, {p}"


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


def _set_conv_padding_mode(module: torch.nn.Module, mode: str) -> int:
    """Define ``padding_mode`` em todos os ``Conv2d`` (in-place, por instância).

    Não faz monkey-patch da classe ``torch.nn.Conv2d`` (rejeitado) — altera apenas o
    atributo de cada instância, pelo que é reversível e isolado a este pipeline.
    Funcionalmente equivalente ao ``asymmetricConv2DConvForward_circular`` do model
    card: é exatamente o ramo ``padding_mode != 'zeros'`` do ``nn.Conv2d._conv_forward``
    nativo do PyTorch.

    Args:
        module: módulo onde procurar Conv2d (ex.: ``pipe.unet``).
        mode: modo de padding (``"circular"`` ou ``"zeros"``).

    Returns:
        Número de Conv2d alterados.
    """
    count = 0
    for m in module.modules():
        if isinstance(m, torch.nn.Conv2d):
            m.padding_mode = mode
            count += 1
    return count


def _make_seamless(module: torch.nn.Module) -> int:
    """Ativa circular padding em todos os Conv2d do módulo (late-stage)."""
    return _set_conv_padding_mode(module, "circular")


def _disable_seamless(module: torch.nn.Module) -> int:
    """Repõe o padding por defeito (zeros) em todos os Conv2d do módulo."""
    return _set_conv_padding_mode(module, "zeros")


def _build_quantization_config(quant: str) -> Any | None:
    """Constrói a configuração de quantização para ``from_pretrained``.

    Args:
        quant: ``"none"``, ``"fp8"`` (torchao ``float8_weight_only``) ou ``"nf4"``
            (bitsandbytes 4-bit NF4).

    Returns:
        Objeto ``quantization_config`` para diffusers, ou ``None``.

    Raises:
        ImportError: Se a dependência de quantização não estiver instalada.
        ValueError: Se o modo for desconhecido.
    """
    if quant == "none":
        return None
    if quant == "fp8":
        try:
            from diffusers import PipelineQuantizationConfig
            from torchao import TorchAoConfig
        except ImportError as e:
            raise ImportError(
                "Quantização FP8 precisa de 'torchao' e diffusers>=0.34. Instale com: pip install torchao"
            ) from e
        return PipelineQuantizationConfig(
            quant_backend="torchao",
            quant_config=TorchAoConfig("float8_weight_only"),
        )
    if quant == "nf4":
        try:
            import bitsandbytes  # noqa: F401
        except ImportError as e:
            raise ImportError("Quantização NF4 precisa de 'bitsandbytes' (instale: pip install bitsandbytes).") from e
        try:
            from transformers import BitsAndBytesConfig
        except ImportError as e:
            raise ImportError(
                "BitsAndBytesConfig precisa de 'transformers' (instale: pip install transformers)."
            ) from e
        return BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4")
    raise ValueError(f"Quantização desconhecida: {quant!r} (use 'none', 'fp8' ou 'nf4')")


class TextureGenerator:
    """Gerador de texturas seamless via pattern-diffusion (SD2-base, Apache-2.0).

    Tileabilidade garantida por construção (noise-rolling + late circular padding).
    PBR (normal/AO/metallic/roughness) é gerado à parte pelo Materialize — este
    gerador produz apenas RGB difuso.

    A API pública (``generate``, ``generate_batch``, ``save_image``,
    ``set_status_callback``, ``warmup``, ``unload``) mantém-se compatível com o
    ``cli.py``.
    """

    def __init__(
        self,
        device: str | None = None,
        low_vram: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        seamless_method: str = "none",
        quant: str = "none",
        compile_flag: bool = False,
    ) -> None:
        if seamless_method not in SEAMLESS_METHODS:
            raise ValueError(f"seamless_method inválido: {seamless_method!r} (use {SEAMLESS_METHODS})")
        if quant not in QUANT_MODES:
            raise ValueError(f"quant inválido: {quant!r} (use {QUANT_MODES})")

        self.verbose = verbose
        self.low_vram = low_vram
        self.model_id = model_id or _model_id()
        self.cache_dir = cache_dir
        self.gpu_ids = gpu_ids
        self.seamless_method = seamless_method
        self.quant = quant
        self.compile_flag = compile_flag

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.torch_dtype = _torch_dtype_for(self.device)
        self._pipe: Any = None
        self._on_status: Callable[[str], None] | None = None
        self._multi_gpu: bool = False
        self._primary_gpu: int | str | None = None

        if self.verbose:
            _logger.info(
                f"device={self.device} dtype={self.torch_dtype} model={self.model_id} "
                f"seamless={self.seamless_method} quant={self.quant} compile={self.compile_flag}"
            )

    def set_status_callback(self, fn: Callable[[str], None] | None) -> None:
        """Callback opcional (ex. Rich) para mensagens de fase durante o load."""
        self._on_status = fn

    def warmup(self) -> None:
        """Carrega o pipeline (download HF + pesos). Idempotente."""
        self._load_pipeline()

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
        guidance_scale: float = 7.5,
        num_inference_steps: int = 50,
        seed: int | None = None,
        width: int = 512,
        height: int = 512,
        preset: str | None = None,
        seamless_method: str | None = None,
        quant: str | None = None,
        compile_flag: bool | None = None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera uma textura seamless.

        Args:
            prompt: Prompt de textura.
            negative_prompt: Prompt negativo.
            guidance_scale: Guidance scale (default 7.5, SD2).
            num_inference_steps: Passos de inferência (default 50).
            seed: Seed (None = aleatório).
            width: Largura em px (default 512, nativo SD2-base; múltiplo de 8).
            height: Altura em px (default 512; múltiplo de 8).
            preset: Preset de material (ex.: "Stone").
            seamless_method: Override do método seamless (``late``/``roll``/``full``/``none``).
            quant: Override da quantização (``none``/``fp8``/``nf4``) — força reload.
            compile_flag: Override do torch.compile — força reload se mudar.

        Returns:
            Tuple (imagem PIL RGB, metadata dict).
        """
        if seamless_method is not None and seamless_method in SEAMLESS_METHODS:
            self.seamless_method = seamless_method
        if quant is not None and quant in QUANT_MODES and quant != self.quant:
            self.quant = quant
            self._pipe = None  # a quantização afeta o load → força reload
        if compile_flag is not None and bool(compile_flag) != bool(self.compile_flag):
            self.compile_flag = bool(compile_flag)
            self._pipe = None

        pipe = self._load_pipeline()

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

        prompt = augment_prompt_for_seamless(prompt)

        is_valid, _err = validate_prompt(prompt, max_length=1200)
        if not is_valid:
            prompt = prompt[:1200]

        if seed is None or seed < 0:
            seed = generate_seed()

        params: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "seed": seed,
            "width": width,
            "height": height,
        }
        is_valid, error = validate_params(params)
        if not is_valid:
            raise ValueError(f"Parâmetros inválidos: {error}")

        generator = torch.Generator(device=self._generator_device())
        generator.manual_seed(seed)

        self._prepare_seamless(pipe)

        pipe_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "height": height,
            "width": width,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "generator": generator,
        }
        # O callback só é necessário quando há trabalho por-passo (late/roll).
        if self.seamless_method in ("late", "roll"):
            pipe_kwargs["callback_on_step_end"] = self._diffusion_callback
            pipe_kwargs["callback_on_step_end_inputs"] = ["latents"]

        self._clear_cache()
        self._log("Inferência...")
        out = pipe(**pipe_kwargs)
        image = out.images[0]
        if image is None:
            raise RuntimeError("Nenhuma imagem devolvida pelo pipeline")
        image = image.convert("RGB")

        metadata: dict[str, Any] = {
            **params,
            "model": self.model_id,
            "seamless_method": self.seamless_method,
            "quant": self.quant,
            "compile": self.compile_flag,
            "prompt_final": prompt,
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

        # Apenas kwargs válidos para generate() — evita quebrar se base_params
        # trouxer chaves legacy (ex.: cfg_scale, lora_strength).
        valid_keys = {
            "negative_prompt",
            "guidance_scale",
            "num_inference_steps",
            "seed",
            "width",
            "height",
            "preset",
            "seamless_method",
            "quant",
            "compile_flag",
        }

        total = len(prompts)
        _logger.info(f"Batch: {total} imagens")

        for idx, prompt in enumerate(prompts):
            try:
                merged = {**DEFAULT_PARAMS, **base_params}
                merged.pop("seed", None)
                merged["prompt"] = prompt
                merged = {k: v for k, v in merged.items() if k in valid_keys}
                image, metadata = self.generate(**merged)
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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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

    def _generator_device(self) -> str:
        if self._multi_gpu and self._primary_gpu is not None:
            return f"cuda:{self._primary_gpu}"
        if self.device != "cpu":
            return "cuda"
        return "cpu"

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        # Lazy import para manter o módulo leve (diffusers/torch são pesados).
        from diffusers import StableDiffusionPipeline

        quant_cfg = _build_quantization_config(self.quant)

        kwargs: dict[str, Any] = {"torch_dtype": self.torch_dtype}
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir
        if quant_cfg is not None:
            kwargs["quantization_config"] = quant_cfg

        self._status(f"from_pretrained ({self.model_id}, quant={self.quant})")
        self._log(f"Carregando {self.model_id}...")
        # pattern-diffusion é SD2-base standard — pipeline nativo, sem trust_remote_code.
        pipe = StableDiffusionPipeline.from_pretrained(self.model_id, **kwargs)

        # Scheduler default (DDIM) do pattern-diffusion — produz output limpo.
        # (Swap para DDPM + upcast_vae + sequential offload causavam corrupção
        # de cor neon/crushed-blacks por dtype mismatch — removidos.)
        self._clear_cache()

        if torch.cuda.is_available() and self.device == "cuda":
            for gid in self.gpu_ids or range(torch.cuda.device_count()):
                if gid < torch.cuda.device_count():
                    torch.cuda.reset_peak_memory_stats(gid)

        if self.device == "cpu":
            self._status("Pipeline em CPU")
            pipe.to("cpu")
        elif self.gpu_ids and len(self.gpu_ids) >= 2 and self._try_multi_gpu(pipe):
            self._status("Modelo carregado — split multi-GPU (MultiGPUPlanner)")
        else:
            # SD2-base é leve (~5GB fp16 em 512). pipe.to(cuda) primeiro — é o
            # caminho validado que produz output limpo. Offload só em OOM real.
            self._status(f"Pipeline em {self.device}")
            try:
                pipe.to(self.device)
            except (torch.cuda.OutOfMemoryError, RuntimeError):
                self._log("VRAM insuficiente para pipe.to — model_cpu_offload")
                try:
                    pipe.enable_model_cpu_offload()
                    self.low_vram = True
                except (torch.cuda.OutOfMemoryError, RuntimeError):
                    self._log("model_cpu_offload insuficiente — sequential offload")
                    pipe.enable_sequential_cpu_offload()
                    self.low_vram = True

        if (
            self.compile_flag
            and self.device != "cpu"
            and torch.cuda.is_available()
            and not self._multi_gpu
            and not self.low_vram
        ):
            try:
                pipe.unet = torch.compile(pipe.unet, mode="max-autotune", dynamic=False)
                self._log("torch.compile (max-autotune) aplicado ao UNet")
                self._status("torch.compile (max-autotune) ativo")
            except Exception as exc:
                self._log(f"torch.compile falhou ({exc}); UNet standard")

        if torch.cuda.is_available() and self.device == "cuda":
            self._report_vram()

        self._pipe = pipe
        return pipe

    def _try_multi_gpu(self, pipe: Any) -> bool:
        """Split do UNet entre GPUs via ``MultiGPUPlanner`` (substitui o monkey-patch).

        Sibling modules (VAE, text_encoder) ficam no dispositivo primário. O fluxo
        cross-device do latente é coordenado pelo accelerate dispatch do UNet.
        """
        if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
            return False

        from gamedev_shared.multi_gpu import MultiGPUPlanner

        gpu_ids = self.gpu_ids or list(range(torch.cuda.device_count()))
        if len(gpu_ids) < 2:
            return False

        planner = (
            MultiGPUPlanner()
            .for_model(pipe)
            .model_attr("unet")
            .with_gpus(gpu_ids)
            .architecture("unet")
            .dtype(self.torch_dtype)
        )
        plan = planner.plan()
        if plan.status != "multi_gpu":
            for w in plan.warnings:
                self._log(w)
            return False

        # apply() re-anexa o UNet dispatchado ao mesmo objeto pipe (in-place).
        pipe = planner.apply()
        primary = plan.primary_device

        for attr in ("vae", "text_encoder"):
            mod = getattr(pipe, attr, None)
            if mod is not None:
                try:
                    mod.to(f"cuda:{primary}")
                except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                    self._log(f"{attr}.to(cuda:{primary}) falhou ({exc})")

        self._multi_gpu = True
        self._primary_gpu = primary
        for gid in gpu_ids:
            try:
                alloc = torch.cuda.memory_allocated(gid) / (1024**3)
                self._log(f"  cuda:{gid} — {alloc:.2f} GB alocados")
            except Exception:
                pass
        return True

    def _apply_low_vram(self, pipe: Any) -> None:
        """Offload para VRAM baixa: group_offload (CUDA streams) ou sequential."""
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3 if torch.cuda.is_available() else 99.0
        can_group = (
            self.quant == "none" and self.device != "cpu" and hasattr(pipe, "enable_group_offload") and vram_gb >= 4.0
        )
        if can_group:
            try:
                pipe.enable_group_offload()
                self._status(f"Pipeline em group_offload (CUDA streams) — VRAM {vram_gb:.1f}GB")
                self._log("group_offload ativado (CUDA streams)")
                return
            except Exception as exc:
                self._log(f"group_offload indisponível ({exc}); sequential offload")
        self._log(f"VRAM {vram_gb:.1f}GB — sequential cpu offload")
        self._status(f"Pipeline em sequential offload — VRAM {vram_gb:.1f}GB")
        pipe.enable_sequential_cpu_offload()

    def _report_vram(self) -> None:
        if self._multi_gpu:
            gpu_ids = self.gpu_ids or list(range(torch.cuda.device_count()))
            parts: list[str] = []
            for gid in gpu_ids:
                try:
                    torch.cuda.synchronize(gid)
                    alloc = torch.cuda.memory_allocated(gid) / (1024**3)
                    peak = torch.cuda.max_memory_allocated(gid) / (1024**3)
                    parts.append(f"cuda:{gid} ~{alloc:.2f} GB (pico ~{peak:.2f} GB)")
                except Exception:
                    pass
            if parts:
                self._status("Modelo carregado — " + ", ".join(parts))
        else:
            try:
                torch.cuda.synchronize()
                alloc = torch.cuda.memory_allocated(0) / (1024**3)
                peak = torch.cuda.max_memory_allocated(0) / (1024**3)
                self._status(f"Modelo carregado — VRAM ~{alloc:.2f} GB (pico após load ~{peak:.2f} GB)")
            except Exception:
                pass

    def _prepare_seamless(self, pipe: Any) -> None:
        """Repõe o estado default do padding (zeros) e aplica o modo ``full`` se ativo.

        Garante que não há leakage de circular padding entre gerações consecutivas
        (o pipeline é cacheado em ``self._pipe``).
        """
        _disable_seamless(pipe.unet)
        # VAE NÃO recebe circular padding — causa artefactos de cor (magenta/neon) no decode.
        if self.seamless_method == "full":
            _make_seamless(pipe.unet)
            self._log("Seamless full: circular padding ativo no UNet desde o início")

    def _diffusion_callback(
        self,
        pipe_self: Any,
        step_index: int,
        timestep: int,
        callback_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Callback por-passo: noise-rolling (0-80%) e late circular padding (aos 80%).

        Regista-se via ``callback_on_step_end`` + ``callback_on_step_end_inputs=["latents"]``.
        ``pipe.num_timesteps`` reflete o número de passos efetivos da inferência.
        """
        method = self.seamless_method
        threshold = int(pipe_self.num_timesteps * _SEAMLESS_THRESHOLD_FRACTION)

        if method == "late" and step_index == threshold:
            _make_seamless(pipe_self.unet)
            self._log(f"Seamless late: circular padding ativo no UNet no passo {step_index}/{pipe_self.num_timesteps}")

        if method in ("late", "roll") and step_index < threshold:
            latents = callback_kwargs.get("latents")
            if latents is not None and latents.ndim == 4:
                # shift dinâmico: metade da dimensão latent (não hardcoded — 64 em latent 64x64 = no-op)
                sh, sw = max(1, latents.shape[-2] // 2), max(1, latents.shape[-1] // 2)
                callback_kwargs["latents"] = torch.roll(latents, shifts=(sh, sw), dims=(2, 3))

        return callback_kwargs
