"""Motion NPZ contract (write/read) shared by ``generate`` and ``apply-rigged``.

The file is self-describing so a rigged GLB can be produced on another machine
without the GGUF: joints, the clip's own skeleton, root travel, contacts and the
runtime codes (for later in-painting) all travel together.

All arrays are plain numeric dtypes — the file is read with ``allow_pickle=False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .skeleton import (
    DEFAULT_FPS,
    JOINT_COUNT,
    REFERENCE_OFFSETS,
    SPRONG,
    SPRONG_JOINTS,
    SPRONG_PARENTS,
    SkeletonMismatchError,
)

FORMAT_VERSION = 2


class LegacyNpzError(ValueError):
    """A pre-SprongMotion (HY-Motion/HML22) NPZ — regenerate it."""


@dataclass(frozen=True)
class MotionData:
    """Everything ``apply-rigged``/``export-glb`` need from an NPZ."""

    joints: np.ndarray  # (T, 22, 3) float64, Y-up, metres
    offsets: np.ndarray  # (22, 3) float64, parent-local rest skeleton
    fps: int = DEFAULT_FPS
    prompt: str = ""
    model: str = ""
    seed: int = 0
    seconds: float = 0.0
    joint_names: tuple[str, ...] = SPRONG_JOINTS
    joint_parents: tuple[int, ...] = SPRONG_PARENTS
    rotations: np.ndarray | None = None
    root: np.ndarray | None = None
    contacts: np.ndarray | None = None
    codes: np.ndarray | None = None
    quality: dict[str, float] = field(default_factory=dict)
    path: Path | None = None

    @property
    def frame_count(self) -> int:
        return int(self.joints.shape[0])

    def check(self) -> None:
        """Validate shapes and values; raises ``ValueError`` with the file path."""
        where = f" ({self.path})" if self.path is not None else ""
        if self.joints.ndim != 3 or self.joints.shape[1:] != (JOINT_COUNT, 3):
            raise ValueError(f"NPZ 'joints' com shape {self.joints.shape}, esperado (T, {JOINT_COUNT}, 3){where}")
        if self.joints.shape[0] < 2:
            raise ValueError(f"NPZ com {self.joints.shape[0]} frames (mínimo 2){where}")
        if not np.isfinite(self.joints).all():
            raise ValueError(f"NPZ 'joints' contém NaN/inf{where}")
        if self.offsets.shape != (JOINT_COUNT, 3):
            raise ValueError(f"NPZ 'offsets' com shape {self.offsets.shape}, esperado ({JOINT_COUNT}, 3){where}")
        if self.fps <= 0:
            raise ValueError(f"NPZ 'fps' inválido ({self.fps}){where}")
        try:
            check_names = tuple(self.joint_names)
            check_parents = tuple(int(p) for p in self.joint_parents)
        except TypeError as exc:
            raise SkeletonMismatchError(f"NPZ sem nomes/parents de esqueleto válidos{where}") from exc
        if check_names != SPRONG_JOINTS or check_parents != SPRONG_PARENTS:
            raise SkeletonMismatchError(f"esqueleto do NPZ não é o SprongMotion-22{where}")


def _text(value: str) -> np.ndarray:
    return np.array(str(value))


def write_clip(
    path: str | Path,
    *,
    joints: Any,
    offsets: Any,
    fps: int,
    prompt: str,
    model: str,
    seed: int,
    seconds: float,
    rotations: Any = None,
    root: Any = None,
    contacts: Any = None,
    speed: Any = None,
    codes: Any = None,
    confidence: Any = None,
    quality: dict[str, float] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write the motion NPZ and return the resolved path."""
    out = Path(path).expanduser()
    if out.suffix.lower() != ".npz":
        out = out.with_suffix(".npz")
    out.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "format_version": np.int32(FORMAT_VERSION),
        "joints": np.asarray(joints, dtype=np.float32),
        "offsets": np.asarray(offsets, dtype=np.float32),
        "fps": np.int32(fps),
        "n_frames": np.int32(np.asarray(joints).shape[0]),
        "seconds": np.float32(seconds),
        "seed": np.int32(seed),
        "prompt": _text(prompt),
        "model": _text(model),
        "skeleton": _text(SPRONG.name),
        "joint_names": np.array(SPRONG_JOINTS, dtype="<U16"),
        "joint_parents": np.array(SPRONG_PARENTS, dtype=np.int32),
    }
    for key, value in (
        ("rotations", rotations),
        ("root", root),
        ("contacts", contacts),
        ("speed", speed),
        ("codes", codes),
        ("confidence", confidence),
    ):
        if value is not None:
            payload[key] = value
    if quality:
        for key, value in quality.items():
            payload[f"quality_{key}"] = np.float32(value)
    for key, value in (extra or {}).items():
        if isinstance(value, (str, int, float, bool)):
            payload[key] = np.array(value)
    np.savez_compressed(out, **payload)
    return out.resolve()


def _string(data: Any, key: str, default: str = "") -> str:
    if key not in data:
        return default
    value = data[key]
    try:
        return str(value.item()) if hasattr(value, "item") else str(value)
    except Exception:
        return default


def read_clip(path: str | Path) -> MotionData:
    """Read and validate a motion NPZ.

    Raises:
        FileNotFoundError: Missing file.
        LegacyNpzError: HML22/HY-Motion NPZ (no ``offsets``/``skeleton``).
        SkeletonMismatchError: Different 22-joint layout.
        ValueError: Bad shapes or non-finite values.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    with np.load(source, allow_pickle=False) as data:
        keys = set(data.keys())
        if "joints" not in keys:
            raise ValueError(f"NPZ sem 'joints': {source}")
        if "offsets" not in keys and "skeleton" not in keys:
            raise LegacyNpzError(
                f"{source} foi gerado pelo Motion3D antigo (HY-Motion/HML22). "
                'Regenera com `motion3d generate "<prompt>" -o clip.npz` (SprongMotion) e volta a aplicar.'
            )
        joints = np.asarray(data["joints"], dtype=np.float64)
        offsets = np.asarray(data["offsets"], dtype=np.float64) if "offsets" in keys else REFERENCE_OFFSETS.copy()
        fps = int(data["fps"]) if "fps" in keys else DEFAULT_FPS
        names = tuple(str(n) for n in data["joint_names"]) if "joint_names" in keys else SPRONG_JOINTS
        parents = tuple(int(p) for p in data["joint_parents"]) if "joint_parents" in keys else SPRONG_PARENTS
        quality = {
            key.removeprefix("quality_"): float(value) for key, value in data.items() if key.startswith("quality_")
        }
        motion = MotionData(
            joints=joints,
            offsets=offsets,
            fps=fps,
            prompt=_string(data, "prompt"),
            model=_string(data, "model"),
            seed=int(data["seed"]) if "seed" in keys else 0,
            seconds=float(data["seconds"]) if "seconds" in keys else joints.shape[0] / max(fps, 1),
            joint_names=names,
            joint_parents=parents,
            rotations=np.asarray(data["rotations"], dtype=np.float64) if "rotations" in keys else None,
            root=np.asarray(data["root"], dtype=np.float64) if "root" in keys else None,
            contacts=np.asarray(data["contacts"]) if "contacts" in keys else None,
            codes=np.asarray(data["codes"]) if "codes" in keys else None,
            quality=quality,
            path=source,
        )
    motion.check()
    return motion
