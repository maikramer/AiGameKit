"""Motion3D generator — SprongMotion runtime → NPZ / BVH / source GLB.

CPU only: no torch, no vramd, no GPU. The model is memory-mapped by the C
runtime (~50 ms) and a 2 s clip takes ~0.2 s on a laptop CPU, so multi-seed
"attempts" are the practical way to dodge the model's known seed variance.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from .npz_io import write_clip
from .quality import ClipQuality, best_clip, score_clip
from .skeleton import DEFAULT_FPS, MAX_SECONDS, MIN_SECONDS, REFERENCE_OFFSETS, SOURCE_TRACK
from .sprong import Clip, GenerateOptions, Model
from .weights import ensure_model_path

DEFAULT_ATTEMPTS = 3


def _status_logger(on_status: Any) -> Any:
    return on_status or (lambda message: None)


class MotionGenerator:
    """Owns a loaded :class:`motion3d.sprong.Model` and writes artifacts."""

    _instance: MotionGenerator | None = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        library: Any = None,
        thread_count: int | None = None,
        on_status: Any = None,
    ) -> None:
        self._model_path = Path(model_path).expanduser() if model_path is not None else None
        self._library = library
        self._thread_count = thread_count
        self._on_status = _status_logger(on_status)
        self._model: Model | None = None
        self.last_quality: ClipQuality | None = None
        self.last_seed: int | None = None

    # -- singleton (CLI-friendly) -----------------------------------------
    @classmethod
    def get_instance(
        cls,
        model_path: str | Path | None = None,
        *,
        thread_count: int | None = None,
        on_status: Any = None,
    ) -> MotionGenerator:
        """Process-wide generator so repeated CLI calls reuse the mapped model."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(model_path, thread_count=thread_count, on_status=on_status)
            elif model_path is not None and cls._instance._model_path != Path(model_path).expanduser():
                cls._instance.unload()
                cls._instance._model_path = Path(model_path).expanduser()
            return cls._instance

    # -- lifecycle --------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> Model:
        """Load (or download + load) the GGUF model."""
        if self._model is not None:
            return self._model
        path = self._model_path or ensure_model_path(on_status=self._on_status)
        self._on_status(f"A carregar {path.name}")
        self._model = Model(path, library=self._library, thread_count=self._thread_count)
        self._on_status(
            f"SprongMotion pronto · {self._model.joint_count} joints @ {self._model.fps} fps "
            f"· {self._model.version()} ({self._model.build_info()})"
        )
        return self._model

    def unload(self) -> None:
        if self._model is not None:
            self._model.close()
            self._model = None

    def __enter__(self) -> MotionGenerator:
        self.load()
        return self

    def __exit__(self, *exc: object) -> None:
        self.unload()

    # -- generation -------------------------------------------------------
    @staticmethod
    def resolve_seconds(
        *,
        seconds: float | None = None,
        duration: float | None = None,
        max_frames: int | None = None,
        fps: int = DEFAULT_FPS,
    ) -> float:
        """Pick the clip length from ``seconds`` / ``duration`` / ``max_frames``."""
        value = seconds if seconds is not None else duration
        if value is None and max_frames is not None:
            value = float(max_frames) / float(fps)
        if value is None:
            return 4.0
        return float(min(max(MIN_SECONDS, float(value)), MAX_SECONDS))

    def generate_clip(
        self,
        prompt: str,
        *,
        seconds: float | None = None,
        seed: int | None = None,
        attempts: int = 1,
        options: GenerateOptions | None = None,
        bvh_path: str | Path | None = None,
    ) -> Clip:
        """Generate the best of ``attempts`` seeds and remember its quality."""
        if not prompt or not prompt.strip():
            raise ValueError("prompt cannot be empty")
        model = self.load()
        base_seed = 0 if seed is None else int(seed)
        rounds = max(1, int(attempts))
        clips: list[Clip] = []
        for index in range(rounds):
            clip_seed = base_seed + index
            if rounds > 1:
                self._on_status(f"Tentativa {index + 1}/{rounds} (seed {clip_seed})")
            clips.append(
                model.generate(
                    prompt,
                    options=options,
                    seconds=seconds,
                    seed=clip_seed,
                    # only the chosen clip gets a BVH; rewrite below if it changed
                    bvh_path=None if rounds > 1 else bvh_path,
                )
            )
        clip, quality = best_clip(clips)
        self.last_quality = quality
        self.last_seed = int(clip.seed)
        if rounds > 1:
            self._on_status(f"Escolhida a seed {clip.seed} (score {quality.score:.3f})")
            if bvh_path is not None:
                # Regenerate the winner with BVH export (deterministic: same seed/options).
                clip = model.generate(prompt, options=options, seconds=seconds, seed=clip.seed, bvh_path=bvh_path)
        return clip

    def generate(
        self,
        prompt: str,
        output: str | Path,
        *,
        seconds: float | None = None,
        duration: float | None = None,
        max_frames: int | None = None,
        seed: int | None = None,
        attempts: int = 1,
        options: GenerateOptions | None = None,
        also_npz: bool = False,
        bvh: str | Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Generate and write ``.npz`` (default), ``.glb`` (source armature) or ``.bvh``."""
        out = Path(output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        suffix = out.suffix.lower()
        length = self.resolve_seconds(seconds=seconds, duration=duration, max_frames=max_frames)

        bvh_path: str | Path | None = None
        if suffix == ".bvh":
            bvh_path = out
        elif bvh:
            bvh_path = Path(bvh).expanduser()

        clip = self.generate_clip(
            prompt,
            seconds=length,
            seed=seed,
            attempts=attempts,
            options=options,
            bvh_path=bvh_path,
        )
        quality = score_clip(clip)

        if suffix == ".glb":
            from .bpy_export import export_joints_glb

            saved = export_joints_glb(
                clip.joints64(),
                out,
                fps=clip.fps,
                offsets=clip.offsets,
                clip_name=SOURCE_TRACK,
            )
            if also_npz:
                self._write_npz(out.with_suffix(".npz"), clip, quality, metadata=metadata)
            return saved
        if suffix == ".bvh":
            return out.resolve()
        return self._write_npz(out, clip, quality, metadata=metadata)

    def _write_npz(
        self,
        out: Path,
        clip: Clip,
        quality: ClipQuality,
        *,
        metadata: dict[str, Any] | None,
    ) -> Path:
        return write_clip(
            out,
            joints=clip.joints,
            offsets=clip.offsets if clip.offsets.size else REFERENCE_OFFSETS,
            fps=clip.fps,
            prompt=clip.prompt,
            model=clip.model_id,
            seed=clip.seed,
            seconds=clip.seconds,
            rotations=clip.rotations,
            root=clip.root,
            contacts=clip.contacts,
            speed=clip.speed,
            codes=clip.codes,
            confidence=clip.confidence,
            quality=quality.as_dict(),
            extra=metadata,
        )


def write_arrays(
    out: str | Path,
    *,
    joints: np.ndarray,
    offsets: np.ndarray | None = None,
    fps: int = DEFAULT_FPS,
    prompt: str = "",
    model: str = "",
    seed: int = 0,
    **kwargs: Any,
) -> Path:
    """Small helper for tests/tools that already own arrays."""
    joints = np.asarray(joints)
    return write_clip(
        out,
        joints=joints,
        offsets=REFERENCE_OFFSETS if offsets is None else offsets,
        fps=fps,
        prompt=prompt,
        model=model,
        seed=seed,
        seconds=kwargs.pop("seconds", joints.shape[0] / max(fps, 1)),
        **kwargs,
    )
