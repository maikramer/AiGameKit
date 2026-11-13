"""Motor StarVector-1B (image→SVG) — inferência vendorizada.

O checkpoint HF é um wrapper StarCoder-1B + CLIP ViT-L/14; o texto gerado é
código SVG. Só faz sentido em ícones/logos/diagramas: o model card diz
explicitamente que não foi treinado em ilustrações ou imagens naturais.
"""

from __future__ import annotations

import gc
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from .options import SvgEngineUnavailable, SvgOptions

DEFAULT_MODEL_ID = "starvector/starvector-1b-im2svg"
BASE_MODEL_ID = "bigcode/starcoderbase-1b"

# CLIP ViT-L/14 preprocessing (igual ao processor_config.json do checkpoint).
CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)


def _log(message: str) -> None:
    print(f"[svg:starvector] {message}", file=sys.stderr)


def _resolve_device(device: str) -> str:
    import torch

    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise SvgEngineUnavailable(
            "StarVector pediu CUDA mas não há GPU disponível; usa --svg-device cpu "
            "(muito lento) ou --svg-engine vtracer."
        )
    return device


def prepare_image(
    image: Image.Image,
    *,
    size: int = 224,
    mean: tuple[float, float, float] = CLIP_MEAN,
    std: tuple[float, float, float] = CLIP_STD,
):
    """RGBA→RGB em branco, pad quadrado, resize bicúbico, normalize CLIP."""

    from torchvision.transforms import functional as TF
    from torchvision.transforms.functional import InterpolationMode

    img = image.convert("RGBA")
    background = Image.new("RGB", img.size, (255, 255, 255))
    background.paste(img, mask=img.split()[3])
    img = background

    width, height = img.size
    max_dim = max(width, height)
    left = (max_dim - width) // 2
    top = (max_dim - height) // 2
    right = max_dim - width - left
    bottom = max_dim - height - top
    img = TF.pad(img, [left, top, right, bottom], fill=255)
    img = TF.resize(img, [size, size], interpolation=InterpolationMode.BICUBIC)

    tensor = TF.to_tensor(img)
    tensor = TF.normalize(tensor, mean=mean, std=std)
    return tensor.unsqueeze(0)


class StarVectorEngine:
    """Carrega o checkpoint uma vez e vectoriza imagens em sequência."""

    name = "starvector"

    def __init__(
        self,
        *,
        model_id: str | None = None,
        device: str = "auto",
        dtype: Any = None,
        max_tokens: int = 4000,
        verbose: bool = False,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.model_id = model_id or DEFAULT_MODEL_ID
        self.device = _resolve_device(device)
        self.max_tokens = int(max_tokens)
        self.verbose = bool(verbose)
        self.cache_dir = str(cache_dir) if cache_dir else None
        if dtype is not None:
            self.dtype = dtype
        else:
            import torch

            self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model = None
        self.image_size = 224

    # ------------------------------------------------------------------ load
    def warmup(self) -> None:
        self.load()

    def load(self) -> None:
        if self.model is not None:
            return

        from aigamekit_shared.model_download import ensure_model

        if self.verbose:
            _log(f"a garantir {self.model_id} no cache HF")
        # O checkpoint StarVector traz o LLM fine-tuned e o tokenizer; o repo
        # base (bigcode/starcoderbase-1b) é gated e não é descarregado.
        ensure_model(self.model_id, cache_dir=self.cache_dir)

        from .vendor.starvector import StarVectorForCausalLM

        if self.verbose:
            _log(f"a carregar {self.model_id} ({self.device}, {self.dtype})")
        try:
            model = StarVectorForCausalLM.from_pretrained(
                self.model_id,
                cache_dir=self.cache_dir,
                tokenizer_name=self.model_id,
                model_precision=self.dtype,
                task="im2svg",
                dtype=self.dtype,
            )
        except TypeError:
            # transformers antigos usam torch_dtype; mantém o caminho vivo.
            model = StarVectorForCausalLM.from_pretrained(
                self.model_id,
                cache_dir=self.cache_dir,
                tokenizer_name=self.model_id,
                model_precision=self.dtype,
                task="im2svg",
                torch_dtype=self.dtype,
            )

        model.eval()
        model.requires_grad_(False)
        # Garante fp16 antes de mover para a GPU (o inner LLM pode ter sido
        # criado em fp32 pelo from_config).
        model = model.to(dtype=self.dtype)
        model = model.to(self.device)
        self.model = model
        if self.verbose:
            _log("modelo carregado")

    # --------------------------------------------------------------- generate
    def vectorize(self, image: Image.Image | str | Path, opts: SvgOptions) -> str:
        if self.model is None:
            self.load()

        import torch

        if isinstance(image, (str, Path)):
            image = Image.open(image)

        tensor = prepare_image(image, size=self.image_size)
        tensor = tensor.to(device=self.device, dtype=self.dtype)

        if opts.seed is not None:
            torch.manual_seed(int(opts.seed))
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(int(opts.seed))

        max_tokens = int(opts.max_tokens or self.max_tokens)
        with torch.inference_mode():
            raw = self.model.generate_im2svg(
                {"image": tensor},
                max_length=max_tokens,
                use_nucleus_sampling=False,
                num_beams=1,
                min_length=1,
                repetition_penalty=1.0,
                length_penalty=1.0,
                use_cache=True,
                early_stopping=True,
            )

        text = raw[0] if isinstance(raw, (list, tuple)) else raw
        if self.verbose:
            _log(f"SVG gerado: {len(str(text))} chars")
        return str(text)

    # ----------------------------------------------------------------- unload
    def unload(self) -> None:
        if self.model is None:
            return
        self.model = None
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        except Exception:
            pass
