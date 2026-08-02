"""Apply Motion3D HML joints onto a SkinTokens rigged GLB via Animator3D retarget.

Owns the happy path: NPZ/joints → HML22 source GLB → ``retarget_animation`` →
skinned output. Mesh ops stay out of GameAssets; retarget math stays in Animator3D.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_PROFILE = "hml22"
DEFAULT_SOURCE_TRACK = "t2m_motion"


def _bpy() -> Any:
    import bpy

    return bpy


def merge_neutral_aim(
    target_overrides: dict[str, tuple[float, float, float]] | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Soft A-pose defaults, with optional per-bone overrides (feet from target rest)."""
    from motion3d.bpy_export import HML22_BONE_NAMES, HML22_NEUTRAL_AIM

    merged = {HML22_BONE_NAMES[i]: d for i, d in HML22_NEUTRAL_AIM.items()}
    if target_overrides:
        merged.update(target_overrides)
    return merged


def target_rest_aims(
    rigged_glb: str | Path,
    bone_map: dict[str, list[str]],
    bone_names: Sequence[str],
) -> dict[str, tuple[float, float, float]]:
    """Rest direction of each mapped target bone, in the target's armature space.

    Used for feet only (``HML22_TARGET_REST_BONES``): Soft A-pose constants get
    the boot pitch wrong on SkinTokens, but the same rest on the *arms* is a full
    T-pose and reopens the walk. Callers merge this on top of ``HML22_NEUTRAL_AIM``.
    """
    from aigamekit_shared.bpy_mesh import import_gltf

    bpy = _bpy()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_gltf(Path(rigged_glb).expanduser().resolve())
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not arms:
        return {}
    arm = arms[0]
    basis = arm.matrix_world.to_3x3()
    aims: dict[str, tuple[float, float, float]] = {}
    for name in bone_names:
        for candidate in bone_map.get(name, [name]):
            bone = arm.data.bones.get(candidate)
            if bone is None:
                continue
            direction = basis @ (bone.tail_local - bone.head_local)
            if direction.length > 1e-6:
                direction.normalize()
                aims[name] = (direction.x, direction.y, direction.z)
            break
    return aims


def apply_motion_to_rigged(
    joints: np.ndarray,
    rigged_glb: str | Path,
    output: str | Path,
    *,
    fps: int = 20,
    clip_name: str = "walk",
    source_track: str = DEFAULT_SOURCE_TRACK,
    profile_name: str = DEFAULT_PROFILE,
    keep_source: Path | None = None,
    in_place: bool = True,
) -> dict[str, Any]:
    """Bake Y-up HML joints onto ``rigged_glb`` using Animator3D retarget.

    Args:
        joints: ``(T, 22, 3)`` HumanML3D Y-up positions.
        rigged_glb: Target SkinTokens / hero ``*_rigged.glb``.
        output: Destination animated GLB (mesh + retargeted clips).
        fps: Source bake FPS.
        clip_name: Output clip name on the target.
        source_track: Action name written on the HML22 source.
        profile_name: Animator3D retarget profile (default ``hml22``).
        keep_source: If set, also write the intermediate HML22 source GLB here.
        in_place: Loopable clip — no horizontal travel / yaw drift baked in.

    Returns:
        Dict with ``output``, ``retarget`` result, ``source_glb``.
    """
    from aigamekit_shared.bpy_mesh import import_gltf, save_glb
    from animator3d import retarget as rt
    from motion3d.bpy_export import HML22_BONE_NAMES, HML22_TARGET_REST_BONES, export_joints_glb

    if joints.ndim != 3 or joints.shape[1] != 22 or joints.shape[2] != 3:
        raise ValueError(f"joints must be (T, 22, 3); got {joints.shape}")

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rigged = Path(rigged_glb).expanduser().resolve()
    if not rigged.is_file():
        raise FileNotFoundError(rigged)

    profile = rt.load_profile(profile_name)

    with tempfile.TemporaryDirectory(prefix="motion3d_src_") as tmp:
        src_path = Path(keep_source) if keep_source is not None else Path(tmp) / "hml22_source.glb"
        if keep_source is not None:
            src_path.parent.mkdir(parents=True, exist_ok=True)
        # Soft A-pose for arms/spine/neck; only feet borrow the target rest pitch.
        # Full SkinTokens rest on the arms = T-pose → walk with arms wide open.
        foot_names = [HML22_BONE_NAMES[i] for i in sorted(HML22_TARGET_REST_BONES)]
        neutral_aim = merge_neutral_aim(target_rest_aims(rigged, profile.bone_map, foot_names))
        export_joints_glb(
            joints,
            src_path,
            fps=fps,
            clip_name=source_track,
            in_place=in_place,
            neutral_aim=neutral_aim,
        )

        bpy = _bpy()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        # Match motion FPS before glTF import — else 20fps clips stretch to 24fps
        # scene frames (116 → 139) and retarget bakes the wrong length.
        bpy.context.scene.render.fps = int(fps)
        import_gltf(rigged)
        targets = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if not targets:
            raise RuntimeError(f"No armature in {rigged}")
        target = targets[0]
        target.name = "Target"
        rt.ensure_feet_root_bone(target)

        before = {o.name for o in bpy.data.objects}
        import_gltf(src_path)
        sources = [o for o in bpy.data.objects if o.type == "ARMATURE" and o.name not in before]
        if not sources:
            sources = [o for o in bpy.data.objects if o.type == "ARMATURE" and o.name != "Target"]
        if not sources:
            raise RuntimeError(f"No source armature from {src_path}")
        source = sources[0]
        source.name = "HML22_Source"

        # Clear target animation; keep source track named for retarget.
        if target.animation_data is not None:
            while target.animation_data.nla_tracks:
                target.animation_data.nla_tracks.remove(target.animation_data.nla_tracks[0])
            target.animation_data.action = None

        src_action = _resolve_source_action(bpy, source, source_track)
        if src_action is None:
            raise RuntimeError(f"Source action {source_track!r} missing after import")
        src_action.name = source_track
        if source.animation_data is None:
            source.animation_data_create()
        source.animation_data.action = src_action

        res = rt.retarget_animation(
            "Target",
            "HML22_Source",
            profile.bone_map,
            source_track,
            clip_name,
        )

        bpy.data.objects.remove(source, do_unlink=True)
        # Drop source action so export only carries the retargeted clip.
        for act in list(bpy.data.actions):
            if act.name != clip_name:
                with contextlib.suppress(RuntimeError):
                    bpy.data.actions.remove(act)
        objs = [o for o in bpy.context.scene.objects if o.type in ("ARMATURE", "MESH")]
        save_glb(
            objs,
            out,
            export_animations=True,
            export_skins=True,
            export_apply=False,
            skip_verify=True,
        )

    return {"output": out, "retarget": res, "source_glb": src_path if keep_source else None}


def _resolve_source_action(bpy: Any, source: Any, source_track: str) -> Any:
    """Find the imported action (active, NLA strip, or by name)."""
    act = bpy.data.actions.get(source_track)
    if act is not None:
        return act
    if source.animation_data is not None:
        if source.animation_data.action is not None:
            return source.animation_data.action
        for track in source.animation_data.nla_tracks:
            for strip in track.strips:
                if strip.action is not None:
                    return strip.action
    # Last resort: single action in the blend.
    if len(bpy.data.actions) == 1:
        return bpy.data.actions[0]
    return None


def apply_npz_to_rigged(
    npz_path: str | Path,
    rigged_glb: str | Path,
    output: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load NPZ ``joints`` and call :func:`apply_motion_to_rigged`."""
    data = np.load(npz_path, allow_pickle=True)
    if "joints" not in data:
        raise ValueError("NPZ missing 'joints'")
    joints = np.asarray(data["joints"], dtype=np.float64)
    fps = int(data["fps"]) if "fps" in data else int(kwargs.pop("fps", 20))
    return apply_motion_to_rigged(joints, rigged_glb, output, fps=fps, **kwargs)
