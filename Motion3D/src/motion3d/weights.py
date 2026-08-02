"""Download and cache Motius T2M-GPT HumanML3D weights."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from huggingface_hub import snapshot_download

HF_REPO = "ZeyuLing/Motius-T2M-GPT-HumanML3D"
HF_REPO_FALLBACK = "ZeyuLing/hftrainer-t2mgpt-humanml3d"

CACHE_DIR = Path.home() / ".cache/aigamekit/models/motius-t2mgpt-humanml3d"

REQUIRED_FILES = (
    "t2mgpt_config.json",
    "Mean.npy",
    "Std.npy",
)

WEIGHT_ALIASES: dict[str, tuple[str, ...]] = {
    "vq": ("vq.safetensors", "vqvae.safetensors", "net.safetensors"),
    "gpt": ("gpt.safetensors", "t2m_trans.safetensors", "trans.safetensors"),
    "clip": ("clip.safetensors",),
}


@dataclass(frozen=True)
class WeightPaths:
    """Resolved paths under the local cache."""

    root: Path
    config: Path
    mean: Path
    std: Path
    vq: Path
    gpt: Path
    clip: Path | None


def _resolve_weight(root: Path, aliases: tuple[str, ...]) -> Path | None:
    for name in aliases:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def ensure_weights(*, force_download: bool = False) -> WeightPaths:
    """Download Motius artifact into the AiGameKit cache if missing."""
    last_err: Exception | None = None
    for repo in (HF_REPO, HF_REPO_FALLBACK):
        try:
            root = Path(
                snapshot_download(
                    repo,
                    local_dir=str(CACHE_DIR),
                    local_dir_use_symlinks=False,
                    force_download=force_download,
                )
            )
            break
        except Exception as exc:
            last_err = exc
            root = None
    else:
        msg = f"Failed to download Motius weights from {HF_REPO!r} (fallback {HF_REPO_FALLBACK!r})"
        raise RuntimeError(msg) from last_err

    config = root / "t2mgpt_config.json"
    mean = root / "Mean.npy"
    std = root / "Std.npy"
    missing = [p.name for p in (config, mean, std) if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Motius artifact incomplete under {root}: missing {missing}")

    vq = _resolve_weight(root, WEIGHT_ALIASES["vq"])
    gpt = _resolve_weight(root, WEIGHT_ALIASES["gpt"])
    if vq is None or gpt is None:
        raise FileNotFoundError(f"Motius VQ/GPT safetensors not found under {root}")

    clip = _resolve_weight(root, WEIGHT_ALIASES["clip"])
    return WeightPaths(root=root, config=config, mean=mean, std=std, vq=vq, gpt=gpt, clip=clip)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load ``t2mgpt_config.json`` from cache or explicit path."""
    cfg_path = path or (CACHE_DIR / "t2mgpt_config.json")
    if not cfg_path.is_file():
        ensure_weights()
        cfg_path = CACHE_DIR / "t2mgpt_config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _config_block(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return ``vqvae``/``gpt`` block — Motius nests under ``config``."""
    nested = config.get("config")
    if isinstance(nested, dict) and isinstance(nested.get(name), dict):
        return dict(nested[name])
    top = config.get(name)
    return dict(top) if isinstance(top, dict) else {}


def vqvae_args_from_config(config: dict[str, Any]) -> SimpleNamespace:
    """Build HumanVQVAE ``args`` namespace from config ``vqvae`` block."""
    block = _config_block(config, "vqvae")
    defaults = {
        "dataname": "t2m",
        "quantizer": "ema_reset",
        "mu": 0.99,
        "nb_code": 512,
        "code_dim": 512,
        "output_emb_width": 512,
        "down_t": 2,
        "stride_t": 2,
        "width": 512,
        "depth": 3,
        "dilation_growth_rate": 3,
        "vq_act": "relu",
    }
    defaults.update(block)
    return SimpleNamespace(**defaults)


def gpt_kwargs_from_config(config: dict[str, Any], *, vqvae_args: SimpleNamespace | None = None) -> dict[str, Any]:
    """Build Text2Motion_Transformer kwargs from config ``gpt`` block."""
    block = _config_block(config, "gpt")
    vq = vqvae_args or vqvae_args_from_config(config)
    defaults: dict[str, Any] = {
        "num_vq": int(getattr(vq, "nb_code", 512)),
        "embed_dim": 1024,
        "clip_dim": 512,
        "block_size": 51,
        "num_layers": 9,
        "n_head": 16,
        "drop_out_rate": 0.1,
        "fc_rate": 4,
    }
    key_map = {
        "embed_dim_gpt": "embed_dim",
        "n_head_gpt": "n_head",
        "ff_rate": "fc_rate",
        "nb_code": "num_vq",
    }
    for src, dst in key_map.items():
        if src in block:
            block[dst] = block.pop(src)
    # Drop unknown keys that are not ctor kwargs.
    allowed = set(defaults)
    cleaned = {k: v for k, v in block.items() if k in allowed}
    defaults.update(cleaned)
    return defaults
