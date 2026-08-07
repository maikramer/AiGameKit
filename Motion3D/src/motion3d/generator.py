"""Motion3D generator singleton — HY-Motion → NPZ + bpy GLB export."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np

from .pipeline import DEFAULT_FPS, HYMotionPipeline

ModelVariant = Literal["lite", "full"]


class MotionGenerator:
    """Singleton wrapper around :class:`HYMotionPipeline`."""

    _instance: MotionGenerator | None = None
    _lock = threading.Lock()

    def __init__(
        self,
        device: str | None = None,
        *,
        model: ModelVariant = "lite",
        sdnq_preset: str | None = None,
        memory_efficient: bool = False,
        offload_text_encoder: bool = False,
        validation_steps: int | None = None,
    ) -> None:
        self._device = device
        self._model = model
        self._sdnq_preset = sdnq_preset
        self._memory_efficient = memory_efficient
        self._offload_text_encoder = offload_text_encoder
        self._validation_steps = validation_steps
        self._pipeline = HYMotionPipeline(
            device=device,
            model=model,
            sdnq_preset=sdnq_preset,
            memory_efficient=memory_efficient,
            offload_text_encoder=offload_text_encoder,
            validation_steps=validation_steps,
        )
        self._loaded = False

    @classmethod
    def get_instance(
        cls,
        *,
        device: str | None = None,
        model: ModelVariant = "lite",
        sdnq_preset: str | None = None,
        memory_efficient: bool = False,
        offload_text_encoder: bool = False,
        validation_steps: int | None = None,
    ) -> MotionGenerator:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(
                    device=device,
                    model=model,
                    sdnq_preset=sdnq_preset,
                    memory_efficient=memory_efficient,
                    offload_text_encoder=offload_text_encoder,
                    validation_steps=validation_steps,
                )
            else:
                inst = cls._instance
                shape_changed = (
                    (device is not None and inst._device != device)
                    or inst._model != model
                    or (inst._sdnq_preset or None) != (sdnq_preset or None)
                    or bool(inst._memory_efficient) != bool(memory_efficient)
                )
                if shape_changed:
                    inst.unload()
                    cls._instance = cls(
                        device=device,
                        model=model,
                        sdnq_preset=sdnq_preset,
                        memory_efficient=memory_efficient,
                        offload_text_encoder=offload_text_encoder,
                        validation_steps=validation_steps,
                    )
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
        duration: float | None = None,
        max_frames: int | None = None,
        seed: int | None = None,
        cfg_scale: float | None = None,
        temperature: float | None = None,
        metadata: dict[str, Any] | None = None,
        also_npz: bool = False,
    ) -> Path:
        """Run inference and write NPZ and/or animated GLB (bpy).

        Extension selects format:
        - ``.npz`` → ``joints`` (+ optional ``rot6d``/``transl``)
        - ``.glb`` → armature animation via :mod:`motion3d.bpy_export`
        """
        if not self.loaded:
            self.load()

        samples = self._pipeline.infer(
            prompt,
            duration=duration,
            max_frames=max_frames,
            seed=seed,
            cfg_scale=cfg_scale,
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

            saved = export_joints_glb(sample.joints, out_path, fps=sample.fps or DEFAULT_FPS)
            if also_npz:
                npz_path = out_path.with_suffix(".npz")
                self._write_npz(npz_path, sample, metadata=metadata)
            return saved

        return self._write_npz(
            out_path if suffix == ".npz" else out_path.with_suffix(".npz"),
            sample,
            metadata=metadata,
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

        payload: dict[str, Any] = {
            "joints": sample.joints,
            "fps": np.int32(sample.fps or DEFAULT_FPS),
            "prompt": np.array(sample.prompt),
            "n_frames": np.int32(sample.n_frames),
            "model": np.array(self._model),
        }
        if sample.rot6d is not None:
            payload["rot6d"] = sample.rot6d
        if sample.transl is not None:
            payload["transl"] = sample.transl
        # Optional legacy key for old consumers (empty placeholder not written).
        if sample.hml263 is not None:
            payload["hml263"] = sample.hml263
        payload.update(extra)
        np.savez_compressed(out_path, **payload)
        return out_path.resolve()
