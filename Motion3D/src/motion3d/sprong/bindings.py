"""ctypes bindings for the SprongMotion C runtime (``include/sprongmotion.h``).

The C API is deliberately small: ``sm_model`` / ``sm_motion`` are structs
returned by value whose fields are the data (numpy views here), and failures
return an empty value plus ``sm_get_error()`` (one message per thread).

Nothing here imports torch, bpy or the GPU stack.
"""

from __future__ import annotations

import contextlib
import ctypes as C
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from .runtime import ensure_runtime

# ---------------------------------------------------------------------------
# C structures (must mirror include/sprongmotion.h exactly, field for field)
# ---------------------------------------------------------------------------


class SmPathPoint(C.Structure):
    _fields_ = [
        ("time", C.c_float),
        ("x", C.c_float),
        ("z", C.c_float),
        ("heading", C.c_float),
    ]


class SmModel(C.Structure):
    _fields_ = [
        ("id", C.c_char_p),
        ("fps", C.c_int),
        ("frames_per_token", C.c_int),
        ("max_seconds", C.c_float),
        ("code_layers", C.c_int),
        ("feature_size", C.c_int),
        ("text_vector_size", C.c_int),
        ("joint_count", C.c_int),
        ("joint_names", C.POINTER(C.c_char_p)),
        ("joint_parents", C.POINTER(C.c_int)),
        ("rest_offsets", C.POINTER(C.c_float)),
        ("has_generator", C.c_bool),
        ("has_encoder", C.c_bool),
        ("follows_path", C.c_bool),
        ("internal", C.c_void_p),
    ]


class SmText(C.Structure):
    _fields_ = [
        ("vector", C.POINTER(C.c_float)),
        ("vector_size", C.c_int),
        ("unknown_word_count", C.c_int),
        ("unknown_words", C.c_char_p),
        ("internal", C.c_void_p),
    ]


class SmMotion(C.Structure):
    _fields_ = [
        ("frame_count", C.c_int),
        ("fps", C.c_int),
        ("seed", C.c_int),
        ("joints", C.POINTER(C.c_float)),
        ("rotations", C.POINTER(C.c_float)),
        ("offsets", C.POINTER(C.c_float)),
        ("root", C.POINTER(C.c_float)),
        ("speed", C.POINTER(C.c_float)),
        ("contacts", C.POINTER(C.c_uint8)),
        ("features", C.POINTER(C.c_float)),
        ("codes", C.POINTER(C.c_int16)),
        ("confidence", C.POINTER(C.c_float)),
        ("token_count", C.c_int),
        ("path_error", C.c_float),
        ("block_start_frames", C.POINTER(C.c_int)),
        ("block_count", C.c_int),
        ("internal", C.c_void_p),
    ]


class SmBlock(C.Structure):
    _fields_ = [
        ("prompt", C.c_char_p),
        ("text", C.POINTER(SmText)),
        ("seconds", C.c_float),
        ("seed", C.c_int),
        ("kept_codes", C.POINTER(C.c_int16)),
    ]


_PROGRESS_FN = C.CFUNCTYPE(C.c_bool, C.c_void_p, C.c_int, C.c_int)


class SmOptions(C.Structure):
    _fields_ = [
        ("seconds", C.c_float),
        ("seed", C.c_int),
        ("steps", C.c_int),
        ("residual_layers", C.c_int),
        ("text_guidance", C.c_float),
        ("residual_guidance", C.c_float),
        ("temperature", C.c_float),
        ("top_k", C.c_int),
        ("foot_lock", C.c_bool),
        ("in_place", C.c_bool),
        ("loop", C.c_bool),
        ("path_warp", C.c_bool),
        ("path", C.POINTER(SmPathPoint)),
        ("path_count", C.c_int),
        ("previous", C.POINTER(SmMotion)),
        ("pinned_codes", C.POINTER(C.c_int16)),
        ("pinned_token_count", C.c_int),
        ("text", C.POINTER(SmText)),
        ("progress", _PROGRESS_FN),
        ("user", C.c_void_p),
    ]



class SprongError(RuntimeError):
    """A runtime call failed; the message is ``sm_get_error()``."""


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------


def _configure(lib: Any) -> None:
    """Declare prototypes (restype/argtypes) once per loaded library."""
    lib.sm_load_model.restype = SmModel
    lib.sm_load_model.argtypes = [C.c_char_p]
    lib.sm_load_model_from_memory.restype = SmModel
    lib.sm_load_model_from_memory.argtypes = [C.c_void_p, C.c_size_t]
    lib.sm_is_model_valid.restype = C.c_bool
    lib.sm_is_model_valid.argtypes = [SmModel]
    lib.sm_unload_model.restype = None
    lib.sm_unload_model.argtypes = [SmModel]
    lib.sm_get_model_meta.restype = C.c_char_p
    lib.sm_get_model_meta.argtypes = [SmModel, C.c_char_p]
    lib.sm_default_options.restype = SmOptions
    lib.sm_default_options.argtypes = [SmModel]
    lib.sm_generate_motion.restype = SmMotion
    lib.sm_generate_motion.argtypes = [SmModel, C.c_char_p, C.c_float, C.c_int]
    lib.sm_generate_motion_ex.restype = SmMotion
    lib.sm_generate_motion_ex.argtypes = [SmModel, C.c_char_p, SmOptions]
    lib.sm_generate_timeline.restype = SmMotion
    lib.sm_generate_timeline.argtypes = [SmModel, C.POINTER(SmBlock), C.c_int, SmOptions]
    lib.sm_is_motion_valid.restype = C.c_bool
    lib.sm_is_motion_valid.argtypes = [SmMotion]
    lib.sm_unload_motion.restype = None
    lib.sm_unload_motion.argtypes = [SmMotion]
    lib.sm_export_motion_bvh.restype = C.c_bool
    lib.sm_export_motion_bvh.argtypes = [SmMotion, C.c_char_p]
    lib.sm_export_motion_bvh_text.restype = C.c_void_p
    lib.sm_export_motion_bvh_text.argtypes = [SmMotion, C.c_float]
    lib.sm_encode_text.restype = SmText
    lib.sm_encode_text.argtypes = [SmModel, C.c_char_p]
    lib.sm_is_text_valid.restype = C.c_bool
    lib.sm_is_text_valid.argtypes = [SmText]
    lib.sm_unload_text.restype = None
    lib.sm_unload_text.argtypes = [SmText]
    lib.sm_get_error.restype = C.c_char_p
    lib.sm_get_error.argtypes = []
    lib.sm_get_version.restype = C.c_char_p
    lib.sm_get_version.argtypes = []
    lib.sm_get_build_info.restype = C.c_char_p
    lib.sm_get_build_info.argtypes = []
    lib.sm_set_thread_count.restype = None
    lib.sm_set_thread_count.argtypes = [C.c_int]
    lib.sm_free.restype = None
    lib.sm_free.argtypes = [C.c_void_p]


def load_library(path: str | Path | None = None) -> Any:
    """Load and configure ``libsprongmotion`` (builds it on first use)."""
    resolved = Path(path).expanduser() if path is not None else ensure_runtime()
    lib = C.CDLL(str(resolved))
    _configure(lib)
    return lib


def last_error(lib: Any) -> str:
    """``sm_get_error()`` for the calling thread (``""`` when the call worked)."""
    raw = lib.sm_get_error()
    return raw.decode("utf-8", "replace") if raw else ""


def set_thread_count(lib: Any, count: int) -> None:
    """Set the thread count for models loaded *after* this call (0 = all cores)."""
    lib.sm_set_thread_count(int(count))


# ---------------------------------------------------------------------------
# Generate options
# ---------------------------------------------------------------------------


@dataclass
class GenerateOptions:
    """Optional overrides for :meth:`Model.generate` (``None`` = model default)."""

    seconds: float | None = None
    seed: int | None = None
    steps: int | None = None
    residual_layers: int | None = None
    text_guidance: float | None = None
    residual_guidance: float | None = None
    temperature: float | None = None
    top_k: int | None = None
    foot_lock: bool | None = None
    in_place: bool | None = None
    loop: bool | None = None

    def apply_to(self, options: SmOptions) -> SmOptions:
        """Copy non-``None`` fields onto a ``sm_options`` struct (C checks the ranges)."""
        for name in (
            "seconds",
            "seed",
            "steps",
            "residual_layers",
            "text_guidance",
            "residual_guidance",
            "temperature",
            "top_k",
            "foot_lock",
            "in_place",
            "loop",
        ):
            value = getattr(self, name)
            if value is not None:
                setattr(options, name, value)
        return options

    def describe(self) -> dict[str, Any]:
        """Only the fields the caller actually set (for NPZ/JSON provenance)."""
        return {
            name: getattr(self, name)
            for name in (
                "seconds",
                "seed",
                "steps",
                "residual_layers",
                "text_guidance",
                "residual_guidance",
                "temperature",
                "top_k",
                "foot_lock",
                "in_place",
                "loop",
            )
            if getattr(self, name) is not None
        }


# ---------------------------------------------------------------------------
# Clip (owned numpy copy of a sm_motion)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Clip:
    """One generated motion. All arrays are C-contiguous and own their memory."""

    prompt: str
    joints: np.ndarray  # (T, J, 3) float32, metres, Y-up, facing +Z at t=0
    rotations: np.ndarray  # (T, J, 4) float32, x y z w, local to parent
    offsets: np.ndarray  # (J, 3) float32, this clip's skeleton
    root: np.ndarray  # (T, 3) float32, full travel
    speed: np.ndarray  # (T,) float32, m/s
    contacts: np.ndarray  # (T, 4) uint8 (l_ankle, l_foot, r_ankle, r_foot)
    fps: int
    seed: int
    model_id: str
    token_count: int
    path_error: float
    codes: np.ndarray = field(default_factory=lambda: np.zeros((0, 6), dtype=np.int16))
    confidence: np.ndarray = field(default_factory=lambda: np.zeros((0,), dtype=np.float32))

    @property
    def frame_count(self) -> int:
        return int(self.joints.shape[0])

    @property
    def seconds(self) -> float:
        return self.frame_count / float(self.fps) if self.fps else 0.0

    @property
    def joint_count(self) -> int:
        return int(self.joints.shape[1])

    def joints64(self) -> np.ndarray:
        """Joint positions as float64 (the export/retarget math path)."""
        return self.joints.astype(np.float64, copy=False)


def _array(pointer: Any, shape: tuple[int, ...], dtype: Any) -> np.ndarray:
    """Copy ``shape`` items from a C pointer into an owned numpy array."""
    if pointer is None or 0 in shape:
        return np.zeros(shape, dtype=dtype)
    buffer = np.ctypeslib.as_array(pointer, shape=(int(np.prod(shape)),))
    return np.array(buffer, dtype=dtype, copy=True).reshape(shape)


def _motion_to_clip(
    lib: Any,
    motion: SmMotion,
    *,
    prompt: str,
    model_id: str,
    code_layers: int = 6,
    bvh_path: str | Path | None = None,
) -> Clip:
    if not lib.sm_is_motion_valid(motion):
        raise SprongError(last_error(lib) or "sm_generate_motion devolveu um motion inválido")
    frames = int(motion.frame_count)
    tokens = int(motion.token_count)
    try:
        if bvh_path is not None:
            destination = Path(bvh_path).expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not lib.sm_export_motion_bvh(motion, str(destination).encode()):
                raise SprongError(last_error(lib) or f"sm_export_motion_bvh falhou: {destination}")
        clip = Clip(
            prompt=prompt,
            joints=_array(motion.joints, (frames, 22, 3), np.float32),
            rotations=_array(motion.rotations, (frames, 22, 4), np.float32),
            offsets=_array(motion.offsets, (22, 3), np.float32),
            root=_array(motion.root, (frames, 3), np.float32),
            speed=_array(motion.speed, (frames,), np.float32),
            contacts=_array(motion.contacts, (frames, 4), np.uint8),
            fps=int(motion.fps),
            seed=int(motion.seed),
            model_id=model_id,
            token_count=tokens,
            path_error=float(motion.path_error),
            codes=_array(motion.codes, (tokens, code_layers), np.int16),
            confidence=_array(motion.confidence, (tokens,), np.float32),
        )
    finally:
        lib.sm_unload_motion(motion)
    return clip


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class Model:
    """Loaded ``sm_model``: metadata + generation.

    Use as a context manager so the model is always unloaded::

        with Model(path) as model:
            clip = model.generate("a person walks forward", seconds=3)
    """

    def __init__(self, path: str | Path, *, library: Any | None = None, thread_count: int | None = None) -> None:
        self._lib = library if library is not None else load_library()
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"modelo SprongMotion não encontrado: {resolved}")
        if thread_count is not None:
            set_thread_count(self._lib, thread_count)
        self.path = resolved
        self._model = self._lib.sm_load_model(str(resolved).encode())
        if not self._lib.sm_is_model_valid(self._model):
            raise SprongError(last_error(self._lib) or f"sm_load_model falhou: {resolved}")
        self._closed = False

        model = self._model
        self.joint_count = int(model.joint_count)
        self.fps = int(model.fps)
        self.frames_per_token = int(model.frames_per_token)
        self.max_seconds = float(model.max_seconds)
        self.code_layers = int(model.code_layers)
        self.feature_size = int(model.feature_size)
        self.text_vector_size = int(model.text_vector_size)
        self.has_encoder = bool(model.has_encoder)
        self.follows_path = bool(model.follows_path)
        self.id = (model.id or b"").decode("utf-8", "replace")
        self.joint_names = [(model.joint_names[i] or b"").decode("utf-8", "replace") for i in range(self.joint_count)]
        self.joint_parents = [int(model.joint_parents[i]) for i in range(self.joint_count)]
        self.rest_offsets = _array(model.rest_offsets, (self.joint_count, 3), np.float32)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if not self._closed:
            self._lib.sm_unload_model(self._model)
            self._closed = True

    def __enter__(self) -> Model:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - best effort
        with contextlib.suppress(Exception):
            self.close()

    # -- metadata ----------------------------------------------------------
    def meta(self, key: str) -> str | None:
        """GGUF string metadata (``general.license``, ``sprongmotion.skeleton.name``, …)."""
        raw = self._lib.sm_get_model_meta(self._model, key.encode())
        return raw.decode("utf-8", "replace") if raw else None

    def version(self) -> str:
        return (self._lib.sm_get_version() or b"").decode("utf-8", "replace")

    def build_info(self) -> str:
        return (self._lib.sm_get_build_info() or b"").decode("utf-8", "replace")

    # -- generation --------------------------------------------------------
    def default_options(self) -> SmOptions:
        return self._lib.sm_default_options(self._model)

    def _options(self, options: GenerateOptions | None) -> SmOptions:
        native = self.default_options()
        (options or GenerateOptions()).apply_to(native)
        return native

    def generate(
        self,
        prompt: str,
        *,
        options: GenerateOptions | None = None,
        seconds: float | None = None,
        seed: int | None = None,
        bvh_path: str | Path | None = None,
    ) -> Clip:
        """Generate one motion from a prompt.

        Args:
            prompt: English action description ("a person walks forward").
            options: Extra runtime options (steps, guidance, loop, foot_lock, …).
            seconds: Clip length in seconds (0.5 … ``max_seconds``); overrides
                ``options.seconds`` when given.
            seed: RNG seed (same seed + same build = identical motion).
            bvh_path: Also write the runtime's own BVH export there.

        Returns:
            A :class:`Clip` owning copies of every array.
        """
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        base = options or GenerateOptions()
        overrides = {}
        if seconds is not None:
            overrides["seconds"] = float(seconds)
        if seed is not None:
            overrides["seed"] = int(seed)
        if overrides:
            base = replace(base, **overrides)
        motion = self._lib.sm_generate_motion_ex(self._model, prompt.encode("utf-8"), self._options(base))
        return _motion_to_clip(
            self._lib,
            motion,
            prompt=prompt,
            model_id=self.id,
            code_layers=self.code_layers,
            bvh_path=bvh_path,
        )

    def generate_timeline(
        self,
        blocks: list[tuple[str, float]],
        *,
        options: GenerateOptions | None = None,
        seed: int = 0,
        bvh_path: str | Path | None = None,
    ) -> Clip:
        """Generate several prompt blocks back to back (seams pinned by the runtime).

        Args:
            blocks: ``[(prompt, seconds), …]`` — 1 to 256 blocks.
            options: Base options; each block's seconds/seed come from the list.
            seed: First block's seed (each next block gets ``seed + i``).
            bvh_path: Also write the runtime's own BVH export there.
        """
        if not blocks:
            raise ValueError("blocks cannot be empty")
        prompts = [str(prompt).encode("utf-8") for prompt, _ in blocks]
        native = self._options(options)
        native.seconds = float(blocks[0][1])
        native.seed = int(seed)
        array = (SmBlock * len(blocks))()
        for index, (_prompt, seconds) in enumerate(blocks):
            array[index].prompt = prompts[index]
            array[index].seconds = float(seconds)
            array[index].seed = int(seed) + index
        motion = self._lib.sm_generate_timeline(self._model, array, len(blocks), native)
        label = " | ".join(f"{prompt} ({seconds:g}s)" for prompt, seconds in blocks)
        return _motion_to_clip(
            self._lib,
            motion,
            prompt=label,
            model_id=self.id,
            code_layers=self.code_layers,
            bvh_path=bvh_path,
        )
