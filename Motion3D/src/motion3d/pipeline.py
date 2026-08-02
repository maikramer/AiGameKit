"""T2M-GPT inference pipeline: CLIP text → GPT tokens → VQ-VAE → HumanML3D joints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from safetensors.torch import load_file

from motion3d.vendor.t2mgpt.models.t2m_trans import Text2Motion_Transformer
from motion3d.vendor.t2mgpt.models.vqvae import HumanVQVAE
from motion3d.vendor.t2mgpt.utils.motion_process import recover_from_ric

from .weights import (
    WeightPaths,
    ensure_weights,
    gpt_kwargs_from_config,
    load_config,
    vqvae_args_from_config,
)

logger = logging.getLogger(__name__)

HUMANML3D_JOINTS = 22
HML263_DIM = 263
DEFAULT_FPS = 20


@dataclass
class MotionSample:
    """One generated motion clip."""

    prompt: str
    hml263: np.ndarray
    joints: np.ndarray
    n_frames: int
    fps: int = DEFAULT_FPS


def _strip_prefixes(state: dict[str, torch.Tensor], prefixes: tuple[str, ...]) -> dict[str, torch.Tensor]:
    out: dict[str, torch.Tensor] = {}
    for key, value in state.items():
        new_key = key
        for prefix in prefixes:
            if new_key.startswith(prefix):
                new_key = new_key[len(prefix) :]
        out[new_key] = value
    return out


def _load_state_dict(path: str | Any, *, map_location: str | torch.device = "cpu") -> dict[str, torch.Tensor]:
    return load_file(str(path), device=str(map_location))


def _apply_state(module: torch.nn.Module, state: dict[str, torch.Tensor], *, label: str) -> None:
    for attempt, stripped in (
        ("raw", state),
        ("strip net./trans./module.", _strip_prefixes(state, ("net.", "trans.", "module."))),
        ("strip vqvae./vqvae.vqvae.", _strip_prefixes(state, ("vqvae.vqvae.", "vqvae.", "vqvae"))),
    ):
        missing, unexpected = module.load_state_dict(stripped, strict=False)
        if not missing:
            logger.debug("%s loaded (%s)", label, attempt)
            return
        logger.debug("%s %s missing=%d unexpected=%d", label, attempt, len(missing), len(unexpected))
    module.load_state_dict(_strip_prefixes(state, ("net.", "trans.", "module.")), strict=False)


class _ClipTextEncoder:
    """Minimal CLIP text tower wrapper with a unified encode API."""

    def __init__(self, device: torch.device) -> None:
        self.device = device
        self._backend = "unknown"
        self._clip_module: Any = None
        self._tokenizer: Any = None
        self._text_model: Any = None

    @property
    def backend(self) -> str:
        return self._backend

    def tokenize(self, texts: list[str]) -> torch.Tensor:
        if self._backend == "openai_clip":
            import clip

            return clip.tokenize(texts, truncate=True).to(self.device)
        if self._backend == "transformers":
            encoded = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=77,
                return_tensors="pt",
            )
            return encoded["input_ids"].to(self.device)
        raise RuntimeError("CLIP encoder not initialized")

    def encode_text(self, tokens: torch.Tensor) -> torch.Tensor:
        if self._backend == "openai_clip":
            feat = self._clip_module.encode_text(tokens)
            return feat.float()
        if self._backend == "transformers":
            out = self._text_model(input_ids=tokens)
            pooled = out.pooler_output
            if pooled is None:
                pooled = out.last_hidden_state[:, 0, :]
            return pooled.float()
        raise RuntimeError("CLIP encoder not initialized")


def _load_clip(device: torch.device, clip_weights: str | None) -> _ClipTextEncoder:
    """Try Motius ``clip.safetensors`` into OpenAI CLIP; else HuggingFace transformers."""
    enc = _ClipTextEncoder(device)

    if clip_weights is not None:
        try:
            import clip

            model, _ = clip.load("ViT-B/32", device=device, jit=False)
            sd = _load_state_dict(clip_weights, map_location=device)
            model.load_state_dict(sd, strict=False)
            model.eval()
            for param in model.parameters():
                param.requires_grad = False
            enc._clip_module = model
            enc._backend = "openai_clip"
            logger.info("CLIP: OpenAI ViT-B/32 + Motius safetensors (%s)", clip_weights)
            return enc
        except ImportError:
            logger.warning("openai CLIP not installed; trying transformers fallback")
        except Exception as exc:
            logger.warning("Motius clip.safetensors into openai CLIP failed (%s); trying transformers", exc)

    from transformers import CLIPTextModel, CLIPTokenizer

    tokenizer = CLIPTokenizer.from_pretrained("openai/clip-vit-base-patch32")
    text_model = CLIPTextModel.from_pretrained("openai/clip-vit-base-patch32")
    if clip_weights is not None:
        try:
            sd = _load_state_dict(clip_weights, map_location="cpu")
            text_model.load_state_dict(sd, strict=False)
            logger.info("CLIP: transformers text tower + Motius safetensors (partial)")
        except Exception as exc:
            logger.warning("Could not load Motius clip weights into transformers (%s); using HF pretrained", exc)
    text_model.to(device)
    text_model.eval()
    enc._tokenizer = tokenizer
    enc._text_model = text_model
    enc._backend = "transformers"
    logger.info("CLIP: transformers openai/clip-vit-base-patch32 text encoder")
    return enc


class T2MGPTPipeline:
    """Load Motius T2M-GPT weights and run text → motion inference."""

    def __init__(self, device: str | torch.device | None = None) -> None:
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self._loaded = False
        self._vqvae: HumanVQVAE | None = None
        self._gpt: Text2Motion_Transformer | None = None
        self._clip: _ClipTextEncoder | None = None
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None
        self._vqvae_args: Any = None
        self._weights: WeightPaths | None = None

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self, *, weights: WeightPaths | None = None) -> None:
        """Download weights (if needed), build models, load safetensors."""
        if self._loaded:
            return

        paths = weights or ensure_weights()
        config = load_config(paths.config)
        vq_args = vqvae_args_from_config(config)
        gpt_kw = gpt_kwargs_from_config(config, vqvae_args=vq_args)

        vqvae = HumanVQVAE(
            vq_args,
            nb_code=int(getattr(vq_args, "nb_code", 512)),
            code_dim=int(getattr(vq_args, "code_dim", 512)),
            output_emb_width=int(getattr(vq_args, "output_emb_width", 512)),
            down_t=int(getattr(vq_args, "down_t", 2)),
            stride_t=int(getattr(vq_args, "stride_t", 2)),
            width=int(getattr(vq_args, "width", 512)),
            depth=int(getattr(vq_args, "depth", 3)),
            dilation_growth_rate=int(getattr(vq_args, "dilation_growth_rate", 3)),
            activation=str(getattr(vq_args, "vq_act", "relu")),
        )
        gpt = Text2Motion_Transformer(**gpt_kw)

        _apply_state(vqvae, _load_state_dict(paths.vq), label="HumanVQVAE")
        _apply_state(gpt, _load_state_dict(paths.gpt), label="Text2Motion_Transformer")

        vqvae.to(self.device)
        gpt.to(self.device)
        vqvae.eval()
        gpt.eval()

        clip_path = str(paths.clip) if paths.clip is not None else None
        clip_enc = _load_clip(self.device, clip_path)

        mean = np.load(paths.mean)
        std = np.load(paths.std)

        self._vqvae = vqvae
        self._gpt = gpt
        self._clip = clip_enc
        self._mean = mean
        self._std = std
        self._vqvae_args = vq_args
        self._weights = paths
        self._loaded = True

    def unload(self) -> None:
        """Release models and clear CUDA cache."""
        self._vqvae = None
        self._gpt = None
        self._clip = None
        self._mean = None
        self._std = None
        self._loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _denorm(self, motion: np.ndarray) -> np.ndarray:
        assert self._mean is not None and self._std is not None
        return motion * self._std + self._mean

    def infer(
        self,
        prompts: list[str] | str,
        *,
        max_frames: int | None = None,
        seed: int | None = None,
        temperature: float | None = None,
    ) -> list[MotionSample]:
        """Generate HumanML3D motions for one or more text prompts."""
        if not self._loaded:
            self.load()
        assert self._vqvae is not None and self._gpt is not None and self._clip is not None

        if isinstance(prompts, str):
            prompts = [prompts]

        if seed is not None:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)

        use_sample = bool(temperature is not None and temperature > 0)
        results: list[MotionSample] = []

        for prompt in prompts:
            tokens = self._clip.tokenize([prompt])
            feat = self._clip.encode_text(tokens)

            with torch.no_grad():
                index_motion = self._gpt.sample(feat, if_categorial=use_sample)
                pred_pose = self._vqvae.forward_decoder(index_motion)
                n_frames = int(pred_pose.shape[1])
                if max_frames is not None:
                    n_frames = min(n_frames, max_frames)
                    pred_pose = pred_pose[:, :n_frames]

                pred_np = pred_pose.detach().cpu().numpy()
                pred_denorm = self._denorm(pred_np)
                joints_t = recover_from_ric(
                    torch.from_numpy(pred_denorm).float().to(self.device),
                    HUMANML3D_JOINTS,
                )
                joints = joints_t.detach().cpu().numpy()

            hml263 = pred_denorm[0, :n_frames].astype(np.float32)
            joints_out = joints[0, :n_frames].astype(np.float32)

            results.append(
                MotionSample(
                    prompt=prompt,
                    hml263=hml263,
                    joints=joints_out,
                    n_frames=n_frames,
                    fps=DEFAULT_FPS,
                )
            )

        return results
