"""Download and cache Tencent HY-Motion-1.0 weights (Lite / Full)."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from huggingface_hub import snapshot_download

from .vendor_bootstrap import hymotion_stats_dir

logger = logging.getLogger(__name__)

HF_REPO = "tencent/HY-Motion-1.0"
CACHE_DIR = Path.home() / ".cache/aigamekit/models/hy-motion-1.0"

ModelVariant = Literal["lite", "full"]

VARIANT_DIR: dict[ModelVariant, str] = {
    "lite": "HY-Motion-1.0-Lite",
    "full": "HY-Motion-1.0",
}

# Encoders used by HYTextModel (downloaded into cache when requested).
CLIP_REPO = "openai/clip-vit-large-patch14"
QWEN_REPO = "Qwen/Qwen3-8B"


@dataclass(frozen=True)
class WeightPaths:
    """Resolved paths under the local HY-Motion cache."""

    root: Path
    variant: ModelVariant
    config: Path
    ckpt: Path
    mean_std_dir: Path
    clip_dir: Path | None = None
    qwen_dir: Path | None = None


def _variant_root(variant: ModelVariant) -> Path:
    return CACHE_DIR / VARIANT_DIR[variant]


def _is_lfs_pointer(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size > 1024:
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:80]
    except OSError:
        return False
    return head.startswith("version https://git-lfs.github.com")


def _rewrite_config(src: Path, dest: Path, *, mean_std_dir: Path) -> None:
    """Rewrite ``mean_std_dir`` to the packaged stats path."""
    with src.open("r", encoding="utf-8") as fh:
        cfg: dict[str, Any] = yaml.safe_load(fh) or {}
    args = cfg.setdefault("train_pipeline_args", {})
    test_cfg = args.setdefault("test_cfg", {})
    test_cfg["mean_std_dir"] = str(mean_std_dir.resolve())
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)


def ensure_weights(
    *,
    model: ModelVariant = "lite",
    force_download: bool = False,
    download_encoders: bool = False,
) -> WeightPaths:
    """Download HY-Motion checkpoint into the AiGameKit cache if missing.

    Args:
        model: ``lite`` (0.46B, default) or ``full``.
        force_download: Re-fetch from Hub.
        download_encoders: Also snapshot CLIP-L + Qwen3-8B into the cache
            (otherwise ``USE_HF_MODELS=1`` uses the transformers Hub cache).
    """
    variant = model
    sub = VARIANT_DIR[variant]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    root = Path(
        snapshot_download(
            HF_REPO,
            allow_patterns=[f"{sub}/*", "LICENSE*", "README*"],
            local_dir=str(CACHE_DIR),
            local_dir_use_symlinks=False,
            force_download=force_download,
        )
    )
    variant_dir = root / sub
    ckpt = variant_dir / "latest.ckpt"
    hub_config = variant_dir / "config.yml"
    if not hub_config.is_file():
        raise FileNotFoundError(f"HY-Motion config missing under {variant_dir}")
    if not ckpt.is_file() or _is_lfs_pointer(ckpt):
        raise FileNotFoundError(
            f"HY-Motion checkpoint missing or still a Git-LFS pointer: {ckpt}. "
            f"Re-run with network / `huggingface-cli download {HF_REPO} {sub}/latest.ckpt`."
        )

    stats = hymotion_stats_dir()
    if not (stats / "Mean.npy").is_file() or not (stats / "Std.npy").is_file():
        raise FileNotFoundError(f"HY-Motion Mean/Std missing under {stats}")

    # Rewritten config lives beside the ckpt so T2MRuntime paths stay local.
    local_config = variant_dir / "config.aigamekit.yml"
    _rewrite_config(hub_config, local_config, mean_std_dir=stats)

    clip_dir: Path | None = None
    qwen_dir: Path | None = None
    if download_encoders:
        clip_dir = Path(
            snapshot_download(
                CLIP_REPO,
                local_dir=str(CACHE_DIR / "encoders" / "clip-vit-large-patch14"),
                local_dir_use_symlinks=False,
                force_download=force_download,
            )
        )
        qwen_dir = Path(
            snapshot_download(
                QWEN_REPO,
                local_dir=str(CACHE_DIR / "encoders" / "Qwen3-8B"),
                local_dir_use_symlinks=False,
                force_download=force_download,
            )
        )

    return WeightPaths(
        root=root,
        variant=variant,
        config=local_config,
        ckpt=ckpt,
        mean_std_dir=stats,
        clip_dir=clip_dir,
        qwen_dir=qwen_dir,
    )


def load_config(path: Path | None = None, *, model: ModelVariant = "lite") -> dict[str, Any]:
    """Load rewritten or Hub config.yml."""
    cfg_path = path
    if cfg_path is None:
        candidate = _variant_root(model) / "config.aigamekit.yml"
        if not candidate.is_file():
            ensure_weights(model=model)
            candidate = _variant_root(model) / "config.aigamekit.yml"
        cfg_path = candidate
    with Path(cfg_path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return dict(data) if isinstance(data, dict) else {}


def copy_license_into_cache() -> None:
    """Best-effort: copy vendor LICENSE next to weights for redistributors."""
    src = Path(__file__).resolve().parent / "vendor" / "hymotion" / "LICENSE.txt"
    if not src.is_file():
        return
    dest = CACHE_DIR / "LICENSE.txt"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            shutil.copy2(src, dest)
    except OSError as exc:
        logger.debug("Could not copy HY-Motion license into cache: %s", exc)
