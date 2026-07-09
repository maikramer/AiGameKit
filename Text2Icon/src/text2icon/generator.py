"""Text2Icon — gerador de ícones via Clark Air Sana 1.6B (1.58-bit ternário).

O transformer Sana 1.6B é comprimido a ternário (~1.85 bits/weight) pelo Clark
Labs — 8.6x mais pequeno que fp16, com qualidade quase fp16. Carrega como
drop-in no ``diffusers`` (sem SDNQ nem quantização runtime):

    1. ``SanaTransformer2DModel`` de ``clark-labs/clark-air-sana-1.6b-1.58bit``
    2. ``SanaPipeline`` de ``Efficient-Large-Model/Sana_1600M_512px_diffusers``
       com o transformer custom injetado.

Pipeline nativo 512px, 20 passos, guidance 4.5. Total ~6 GB fp16 (transformer
3.2 GB + Gemma text encoder ~2.5 GB + VAE ~0.5 GB) → ``model_cpu`` offload em
GPUs modestas.

Para ícones transparentes (RGBA), o gerador pode aplicar ``rembg`` (U2Net) após
a inferência, ativado por ``remove_background=True``.
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

from .utils import generate_seed, validate_params, validate_prompt

_logger = Logger()

# Transformer Clark Air (ternário 1.58-bit, descompactado bf16, drop-in diffusers).
DEFAULT_TRANSFORMER_ID = "clark-labs/clark-air-sana-1.6b-1.58bit"
# Pipeline base (VAE + Gemma text encoder + scheduler) — Sana 1.6B 512px.
DEFAULT_PIPELINE_ID = "Efficient-Large-Model/Sana_1600M_512px_diffusers"

# Alias compat: o "modelo" do text2icon é o transformer Clark Air.
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


def _torch_dtype_for(device: str) -> torch.dtype:
    """Resolve o dtype torch para o dispositivo pedido.

    Clark Air Sana usa bf16 (formato de exportação); fp32 em CPU.
    """
    d = device.lower()
    if d.startswith("cpu"):
        return torch.float32
    if d.startswith("cuda") and torch.cuda.is_available():
        # Clark Air exportado em bf16 — preferir bf16 quando suportado.
        if getattr(torch.cuda, "is_bf16_supported", lambda: False)():
            return torch.bfloat16
        return torch.float16
    return torch.float32


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


class SanaIconGenerator:
    """Gerador de ícones via Clark Air Sana 1.6B 1.58-bit (drop-in diffusers)."""

    def __init__(
        self,
        device: str | None = None,
        low_vram: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        quant_preset: str | None = None,
    ) -> None:
        self.verbose = verbose
        self.low_vram = low_vram
        self.transformer_id = model_id or _transformer_id()
        self.pipeline_id = DEFAULT_PIPELINE_ID
        self.cache_dir = cache_dir
        self.gpu_ids = gpu_ids

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.torch_dtype = _torch_dtype_for(self.device)
        self._pipe: Any = None
        self._on_status: Callable[[str], None] | None = None
        self._multi_gpu: bool = False

        # Auto-quantização do Gemma encoder em GPUs modestas (< 8 GB).
        # ``quant_preset`` explícito ganha; ``"none"`` desliga; ``None`` = auto.
        if quant_preset in (None, "auto") and self.device != "cpu":
            from gamedev_shared.gpu import gpu_total_mib

            try:
                vram_mib = gpu_total_mib(0)
                if vram_mib and vram_mib < 8192:  # < 8 GB → quantizar encoder
                    self.quant_preset = "sdnq-int4"
                    if self.verbose:
                        _logger.info(f"auto-quant encoder int4 (VRAM {vram_mib} MiB < 8192)")
                else:
                    self.quant_preset = None
            except Exception:
                self.quant_preset = quant_preset if quant_preset != "auto" else None
        elif quant_preset == "none":
            self.quant_preset = None
        else:
            self.quant_preset = quant_preset

        if self.verbose:
            _logger.info(f"device={self.device} dtype={self.torch_dtype} transformer={self.transformer_id}")

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
        quant_device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            pipe.text_encoder = quantize_model(encoder, preset, quantization_device=quant_device, return_device="cpu")
            self._log(f"SDNQ {preset} aplicado ao text_encoder")
        except Exception as exc:
            self._log(f"quantização do text_encoder falhou ({exc}); fica em fp16")
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

        # Passo 1: transformer Clark Air (ternário, bf16 drop-in).
        # O repo do transformer é privado → precisa de HF_TOKEN (lido via gamedev_shared).
        self._status(f"Passo 1/3 — transformer Clark Air 1.58-bit ({self.transformer_id})")
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
        pipe.vae.to(torch.float32)

        # VAE tiling reduz o pico de VRAM no decode (importante em GPUs modestas).
        try:
            pipe.vae.enable_tiling()
        except Exception:
            self._log("VAE tiling indisponível, a ignorar.")

        # Quantização opcional do text encoder (Gemma 2B) via SDNQ.
        # Reduz a VRAM do encoder de ~4.9 GB (fp16) para ~2.4 GB (int4) — decisivo
        # em GPUs de 6 GB. Ativada por ``quant_preset`` ou auto-detecção de VRAM.
        if self.quant_preset and self.device != "cpu":
            self._maybe_quantize_encoder(pipe, self.quant_preset)

        # Passo 3: colocação.
        self._clear_cache()
        if torch.cuda.is_available() and self.device == "cuda":
            for gid in self.gpu_ids or range(torch.cuda.device_count()):
                if gid < torch.cuda.device_count():
                    torch.cuda.reset_peak_memory_stats(gid)

        if self.device == "cpu":
            mode_label = "cpu"
        elif self.low_vram:
            mode_label = f"{self.device} (cpu_offload — módulos migram 1 a 1)"
        else:
            mode_label = self.device
        self._status(f"Passo 3/3 — a mover o pipeline para {mode_label}")

        did_offload = False
        if self.device == "cpu":
            pipe.to("cpu")
        elif self.gpu_ids and len(self.gpu_ids) >= 2 and self._try_multi_gpu(pipe):
            self._status("Modelo carregado — split multi-GPU")
        elif self.low_vram:
            pipe.enable_model_cpu_offload()
            did_offload = True
        else:
            # Tentar full-GPU; se estourar VRAM, cair para CPU offload (rede de segurança).
            try:
                pipe.to(self.device)
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                self._log(f"pipe.to({self.device}) OOM ({exc}); fallback para CPU offload")
                self._clear_cache()
                pipe.enable_model_cpu_offload()
                did_offload = True

        if torch.cuda.is_available() and self.device == "cuda" and not did_offload:
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
        """Split ad-hoc: transformer+vae → GPU0, text_encoder → GPU1."""
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

        pipe._text2icon_primary_device = torch.device(primary_dev)

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
            return self._text2icon_primary_device

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
            "remove_background": remove_background,
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

        self._log("Inferência Clark Air Sana...")
        out = pipe(**pipe_kwargs)
        image = out.images[0]

        if image is None:
            raise RuntimeError("Nenhuma imagem devolvida pelo pipeline")

        image = image.convert("RGB")

        if remove_background:
            from .bg_removal import remove_background as _rbg

            image = _rbg(image)

        metadata = {
            "seed": seed,
            "prompt_final": prompt,
            "model": self.transformer_id,
            "remove_background": remove_background,
            **params,
        }

        return image, metadata

    def generate_batch(
        self,
        prompts: list[str],
        base_params: dict[str, Any] | None = None,
    ) -> Generator[tuple[Image.Image | None, dict[str, Any], int], None, None]:
        """Gera múltiplos ícones em batch.

        Yields:
            Tuple (imagem | None, metadata, index).
        """
        if base_params is None:
            base_params = {}

        total = len(prompts)
        _logger.info(f"Batch: {total} ícones")

        for idx, prompt in enumerate(prompts):
            try:
                merged = {**DEFAULT_PARAMS, **base_params}
                merged.pop("seed", None)
                merged.pop("prompt", None)

                image, metadata = self.generate(prompt=prompt, **merged)
                yield image, metadata, idx
            except Exception as e:
                _logger.error(f"Erro no ícone {idx + 1}/{total}: {e}")
                yield None, {"error": str(e), "index": idx}, idx

    @staticmethod
    def save_image(image: Image.Image, path: Any, image_format: str | None = None) -> Any:
        """Grava imagem em disco (compatibilidade)."""
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format=image_format)
        return path
