"""Motion3D generator singleton — NPZ + bpy GLB export."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from .pipeline import DEFAULT_FPS, T2MGPTPipeline


class MotionGenerator:
    """Singleton wrapper around :class:`T2MGPTPipeline`."""

    _instance: MotionGenerator | None = None
    _lock = threading.Lock()

    def __init__(self, device: str | None = None) -> None:
        self._device = device
        self._pipeline = T2MGPTPipeline(device=device)
        self._loaded = False

    @classmethod
    def get_instance(cls, *, device: str | None = None) -> MotionGenerator:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(device=device)
            elif device is not None and cls._instance._device != device:
                cls._instance.unload()
                cls._instance = cls(device=device)
            return cls._instance

    @property
    def loaded(self) -> bool:
        return self._loaded and self._pipeline.loaded

    def load(self) -> None:
        self._pipeline.load()
        self._loaded = True

    def unload(self) -> None:
        self._pipeline.unload()
        self._loaded = False

    def generate(
        self,
        prompt: str,
        output: str | Path,
        *,
        max_frames: int | None = None,
        seed: int | None = None,
        temperature: float | None = None,
        metadata: dict[str, Any] | None = None,
        also_npz: bool = False,
    ) -> Path:
        """Run inference and write NPZ and/or animated GLB (bpy).

        Extension selects format:
        - ``.npz`` → HumanML3D ``hml263`` + ``joints``
        - ``.glb`` → **HML22 source** armature via :mod:`motion3d.bpy_export`
          (not skinned — use :func:`motion3d.apply_rigged.apply_motion_to_rigged`)
        - other → treated as NPZ

        When ``also_npz`` and output is ``.glb``, also write sibling ``.npz``.
        """
        if not self.loaded:
            self.load()

        samples = self._pipeline.infer(
            prompt,
            max_frames=max_frames,
            seed=seed,
            temperature=temperature,
        )
        if not samples:
            raise RuntimeError("No motion samples produced")

        sample = samples[0]
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = out_path.suffix.lower()

        if suffix == ".glb":
            from .bpy_export import export_joints_glb

            saved = export_joints_glb(
                sample.joints,
                out_path,
                fps=sample.fps or DEFAULT_FPS,
                in_place=True,
            )
            if also_npz:
                npz_path = out_path.with_suffix(".npz")
                self._write_npz(npz_path, sample, metadata=metadata)
            return saved

        return self._write_npz(
            out_path if suffix == ".npz" else out_path.with_suffix(".npz"), sample, metadata=metadata
        )

    def _write_npz(
        self,
        out_path: Path,
        sample: Any,
        *,
        metadata: dict[str, Any] | None,
    ) -> Path:
        extra: dict[str, Any] = {}
        if metadata:
            for key, value in metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    extra[key] = value

        np.savez_compressed(
            out_path,
            hml263=sample.hml263,
            joints=sample.joints,
            fps=np.int32(sample.fps or DEFAULT_FPS),
            prompt=np.array(sample.prompt),
            n_frames=np.int32(sample.n_frames),
            **extra,
        )
        return out_path.resolve()
