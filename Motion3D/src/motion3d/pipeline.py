"""HY-Motion-1.0 inference pipeline → HumanML3D-22 joints NPZ @ 30 fps."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import torch

from .vendor_bootstrap import ensure_hymotion_on_path
from .weights import ModelVariant, WeightPaths, ensure_weights

logger = logging.getLogger(__name__)

HUMANML3D_JOINTS = 22
DEFAULT_FPS = 30
DEFAULT_DURATION_S = 4.0
DEFAULT_CFG_SCALE = 5.0


@dataclass
class MotionSample:
    """One generated motion clip (joints-first; optional rot6d/transl)."""

    prompt: str
    joints: np.ndarray
    n_frames: int
    fps: int = DEFAULT_FPS
    rot6d: np.ndarray | None = None
    transl: np.ndarray | None = None
    hml263: np.ndarray | None = None


def _to_numpy(t: Any) -> np.ndarray:
    if isinstance(t, np.ndarray):
        return np.asarray(t, dtype=np.float32)
    if torch.is_tensor(t):
        return t.detach().float().cpu().numpy().astype(np.float32, copy=False)
    return np.asarray(t, dtype=np.float32)


def _tensor_to(device: torch.device, value: Any) -> Any:
    if torch.is_tensor(value):
        return value.to(device)
    return value


class HYMotionPipeline:
    """Wrap vendored ``T2MRuntime`` / ``MotionFlowMatching.generate``.

    Staged load (Text2D-like VRAM): DiT on GPU (optional SDNQ); text encoder
    stays on CPU when ``offload_text_encoder`` — encode → move hidden to GPU →
    DiT generate. Never co-resident Qwen+DiT on ~6 GB.
    """

    def __init__(
        self,
        *,
        device: str | None = None,
        model: ModelVariant = "lite",
        sdnq_preset: str | None = None,
        memory_efficient: bool = False,
        offload_text_encoder: bool = False,
        validation_steps: int | None = None,
    ) -> None:
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model: ModelVariant = model
        self._sdnq_preset = (sdnq_preset or "").strip() or None
        if self._sdnq_preset and self._sdnq_preset.lower() in ("none", "null"):
            self._sdnq_preset = None
        self._memory_efficient = bool(memory_efficient)
        self._offload_text_encoder = bool(offload_text_encoder or memory_efficient)
        self._validation_steps = validation_steps
        self._runtime: Any = None
        self._paths: WeightPaths | None = None
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded and self._runtime is not None

    def load(self) -> None:
        if self._loaded:
            return

        ensure_hymotion_on_path()
        os.environ.setdefault("USE_HF_MODELS", "1")

        self._paths = ensure_weights(model=self._model)
        if self._paths.clip_dir is not None:
            os.environ["HY_MOTION_CLIP_PATH"] = str(self._paths.clip_dir)
        if self._paths.qwen_dir is not None:
            os.environ["HY_MOTION_QWEN_PATH"] = str(self._paths.qwen_dir)

        from hymotion.utils.loaders import load_object
        from hymotion.utils.t2m_runtime import T2MRuntime

        force_cpu = not str(self._device).startswith("cuda")
        device_ids: list[int] | None = None
        if not force_cpu and torch.cuda.is_available():
            device_ids = [0]

        # Stage 1: DiT (+WoodenMesh) without text encoder.
        self._runtime = T2MRuntime(
            config_path=str(self._paths.config),
            ckpt_name=str(self._paths.ckpt),
            skip_text=True,
            device_ids=device_ids,
            force_cpu=force_cpu,
            disable_prompt_engineering=True,
        )

        pipeline = self._runtime.pipelines[0]
        if self._validation_steps is not None:
            pipeline.validation_steps = int(self._validation_steps)
            pipeline._infer_noise_scheduler_cfg["validation_steps"] = int(self._validation_steps)

        if self._sdnq_preset and str(self._device).startswith("cuda"):
            self._apply_sdnq(pipeline)

        # Stage 2: text encoder — CPU when offload (avoid Qwen on 6GB with DiT).
        if self._offload_text_encoder:
            self._load_text_encoder_cpu(pipeline, load_object)
        else:
            self._runtime.load_text_encoder()

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        self._loaded = True
        logger.info(
            "HY-Motion %s loaded on %s (sdnq=%s text=%s staged=%s)",
            self._model,
            self._device,
            self._sdnq_preset or "none",
            "cpu" if self._offload_text_encoder else "gpu",
            True,
        )

    def _load_text_encoder_cpu(self, pipeline: Any, load_object: Any) -> None:
        """Build HYTextModel on CPU only — never ``.to(cuda)`` for Qwen on small GPUs."""
        if getattr(pipeline, "text_encoder", None) is not None:
            pipeline.text_encoder.to("cpu")
            self._runtime.skip_text = False
            return
        logger.info("Loading HY text encoder on CPU (staged; DiT stays on GPU)")
        pipeline.text_encoder = load_object(pipeline._text_encoder_module, pipeline._text_encoder_cfg)
        pipeline.text_encoder.to("cpu")
        pipeline.text_encoder.eval()
        self._runtime.skip_text = False

    def unload(self) -> None:
        self._runtime = None
        self._paths = None
        self._loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _apply_sdnq(self, pipeline: Any) -> None:
        try:
            from aigamekit_shared.sdnq import quantize_model
        except Exception as exc:
            logger.warning("SDNQ unavailable (%s) — continuing fp16/bf16 DiT", exc)
            return
        assert self._sdnq_preset is not None
        logger.info("Quantizing HY-Motion DiT with SDNQ preset=%s", self._sdnq_preset)
        device = next(pipeline.parameters()).device
        pipeline.motion_transformer.to("cpu")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        pipeline.motion_transformer = quantize_model(
            pipeline.motion_transformer,
            preset=self._sdnq_preset,
        )
        pipeline.motion_transformer.to(device)
        pipeline.motion_transformer.eval()

    def infer(
        self,
        prompt: str,
        *,
        duration: float | None = None,
        max_frames: int | None = None,
        seed: int | None = None,
        cfg_scale: float | None = None,
        temperature: float | None = None,
    ) -> list[MotionSample]:
        """Generate one motion sample from text.

        Duration (seconds) wins; ``max_frames`` converts via ``fps=30``.
        """
        del temperature
        if not self.loaded:
            self.load()
        assert self._runtime is not None

        fps = DEFAULT_FPS
        if duration is None and max_frames is not None:
            duration = float(max_frames) / float(fps)
        if duration is None:
            duration = DEFAULT_DURATION_S
        duration = max(0.5, float(duration))
        cfg = float(cfg_scale) if cfg_scale is not None else DEFAULT_CFG_SCALE
        seed_i = 42 if seed is None else int(seed)

        pipeline = self._runtime.pipelines[0]
        pipeline.eval()
        dit_device = next(pipeline.parameters()).device

        with torch.inference_mode():
            # Encode on whatever device holds the text tower (CPU when staged).
            hidden = pipeline.encode_text({"text": [prompt] * 1})
            hidden_gpu = {k: _tensor_to(dit_device, v) for k, v in hidden.items()}
            if self._offload_text_encoder and torch.cuda.is_available():
                torch.cuda.empty_cache()
            output = pipeline.generate(
                prompt,
                [seed_i],
                duration,
                cfg_scale=cfg,
                use_special_game_feat=False,
                hidden_state_dict=hidden_gpu,
            )

        k3d = output.get("keypoints3d")
        if k3d is None:
            raise RuntimeError("HY-Motion generate returned no keypoints3d")
        joints_b = _to_numpy(k3d)
        if joints_b.ndim != 4 or joints_b.shape[-1] != 3:
            raise RuntimeError(f"Unexpected keypoints3d shape {joints_b.shape}")
        joints = joints_b[0]
        # WoodenMesh / SMPL-H emits 52 joints; HML22 / apply-rigged uses body 22.
        if joints.shape[1] >= HUMANML3D_JOINTS:
            if joints.shape[1] != HUMANML3D_JOINTS:
                logger.info(
                    "Slicing keypoints3d joints %d → %d (SMPL-H body)",
                    joints.shape[1],
                    HUMANML3D_JOINTS,
                )
            joints = joints[:, :HUMANML3D_JOINTS, :]
        else:
            raise RuntimeError(f"Unexpected keypoints3d shape {joints_b.shape}")
        n_frames = int(joints.shape[0])

        rot6d = None
        if "rot6d" in output:
            rot6d = _to_numpy(output["rot6d"][0])
            if rot6d.ndim == 3 and rot6d.shape[1] >= HUMANML3D_JOINTS:
                rot6d = rot6d[:, :HUMANML3D_JOINTS, :]
        transl = _to_numpy(output["transl"][0]) if "transl" in output else None

        # HY ground-align / FK often leaves pelvis nearly fixed in keypoints3d while
        # ``transl`` still carries root travel + jump lift. Re-anchor the body so
        # apply-rigged sees real vertical motion (--in-place keeps Z height).
        if transl is not None and transl.shape[0] == n_frames:
            joints = _reanchor_joints_to_transl(joints, transl)

        return [
            MotionSample(
                prompt=prompt,
                joints=joints,
                n_frames=n_frames,
                fps=fps,
                rot6d=rot6d,
                transl=transl,
            )
        ]


def _reanchor_joints_to_transl(joints: np.ndarray, transl: np.ndarray) -> np.ndarray:
    """Move the whole skeleton so pelvis tracks ``transl`` (Y-up)."""
    delta = transl.astype(np.float32, copy=False) - joints[:, 0, :]
    return joints + delta[:, None, :]


T2MGPTPipeline = HYMotionPipeline
ModelName = Literal["lite", "full"]
