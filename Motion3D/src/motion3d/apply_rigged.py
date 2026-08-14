"""Apply Motion3D HML joints onto a SkinTokens rigged GLB via Animator3D retarget.

Owns the happy path: NPZ/joints → HML22 source GLB → ``retarget_animation`` →
skinned output. One clip (``apply_motion_to_rigged``) or many clips in one asset
(``apply_motions_to_rigged`` / CLI ``pack-rigged``). Mesh ops stay out of
GameAssets; retarget math stays in Animator3D.
"""

from __future__ import annotations

import contextlib
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_PROFILE = "hml22"
DEFAULT_SOURCE_TRACK = "t2m_motion"

# ``name=path.npz`` — clip name is a simple identifier (game clip names).
_MOTION_SPEC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)=(.+)$")


@dataclass(frozen=True)
class MotionClip:
    """One named motion to bake onto a rigged GLB.

    Provide ``joints`` **or** ``npz_path`` (exactly one). Pack-level defaults
    fill any ``None`` constraint field; set a field to override for that clip.
    """

    name: str
    joints: np.ndarray | None = None
    npz_path: Path | None = None
    fps: int | None = None
    in_place: bool | None = None
    arm_neutral: str | None = None
    max_lean_deg: float | None = None
    hands_together_m: float | None = None
    plant_feet: bool | None = None
    # When True, this clip opts into max_lean/hands/plant even if the value is
    # the type default — used only via ``parse_motion_spec`` suffixes later.
    # For now pack defaults win when these are None.


def parse_motion_spec(spec: str) -> MotionClip:
    """Parse ``name=/path/to.npz`` into a :class:`MotionClip`.

    Raises:
        ValueError: Malformed spec or empty path.
    """
    text = (spec or "").strip()
    match = _MOTION_SPEC_RE.match(text)
    if match is None:
        raise ValueError(f"motion spec must be name=path.npz; got {spec!r}")
    name, path_s = match.group(1), match.group(2).strip()
    if not path_s:
        raise ValueError(f"motion spec missing path: {spec!r}")
    return MotionClip(name=name, npz_path=Path(path_s).expanduser())


def _load_npz_joints(path: Path | str) -> tuple[np.ndarray, int | None]:
    """Lê ``joints``/``fps`` de um NPZ com validação defensiva.

    - ``with np.load`` fecha o NpzFile (o handle ficava preso até GC);
    - ``allow_pickle=False`` — os arrays guardados (joints/fps/prompt/model)
      são dtypes plain, pickle é desnecessário e é vetor de código arbitrário
      em NPZ de fonte desconhecida;
    - NaN/inf em ``joints`` propagariam para bone.head/tail e keyframes —
      falhar cedo com mensagem clara.
    """
    with np.load(path, allow_pickle=False) as data:
        if "joints" not in data:
            raise ValueError(f"NPZ missing 'joints': {path}")
        joints = np.asarray(data["joints"], dtype=np.float64)
        fps = int(data["fps"]) if "fps" in data else None
    if joints.ndim != 3 or joints.shape[1] == 0 or joints.shape[2] < 3:
        raise ValueError(f"NPZ 'joints' com shape inesperado {joints.shape}: {path}")
    if not np.isfinite(joints).all():
        raise ValueError(f"NPZ 'joints' contém NaN/inf: {path}")
    if fps is not None and fps <= 0:
        raise ValueError(f"NPZ 'fps' inválido ({fps}): {path}")
    return joints, fps


def load_clip_joints(clip: MotionClip) -> tuple[np.ndarray, int | None]:
    """Return ``(joints, fps_or_None)`` from a clip's arrays or NPZ."""
    if clip.joints is not None and clip.npz_path is not None:
        raise ValueError(f"clip {clip.name!r}: set joints or npz_path, not both")
    if clip.joints is not None:
        joints = np.asarray(clip.joints, dtype=np.float64)
        if not np.isfinite(joints).all():
            raise ValueError(f"clip {clip.name!r}: joints contêm NaN/inf")
        return joints, clip.fps
    if clip.npz_path is None:
        raise ValueError(f"clip {clip.name!r}: need joints or npz_path")
    path = Path(clip.npz_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    joints, npz_fps = _load_npz_joints(path)
    fps = clip.fps
    if fps is None:
        fps = npz_fps
    return joints, fps


def _bpy() -> Any:
    import bpy

    return bpy


def target_rest_aims(
    rigged_glb: str | Path,
    bone_map: dict[str, list[str]],
    bone_names: Sequence[str],
) -> dict[str, tuple[float, float, float]]:
    """Rest direction of each mapped target bone, in the target's armature space.

    Optional helper for callers that want a subset of bones calibrated to the
    target rest. Feet are no longer in ``HML22_TARGET_REST_BONES`` — ankle hinge
    clamps + look-at at ``ball_*`` own plantigrade limits without a constant
    median→rest rotation.
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


def _strip_debug_meshes(bpy: Any) -> None:
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            continue
        name = obj.name.lower()
        if name.startswith("ico") or "empty" in name or name.startswith("axis"):
            bpy.data.objects.remove(obj, do_unlink=True)


def _pick(clip_val: Any, pack_val: Any) -> Any:
    return pack_val if clip_val is None else clip_val


def apply_motions_to_rigged(
    clips: Sequence[MotionClip | tuple[str, np.ndarray] | tuple[str, str | Path]],
    rigged_glb: str | Path,
    output: str | Path,
    *,
    fps: int = 20,
    profile_name: str = DEFAULT_PROFILE,
    keep_sources_dir: Path | None = None,
    in_place: bool = True,
    arm_neutral: str = "auto",
    max_lean_deg: float | None = None,
    hands_together_m: float | None = None,
    plant_feet: bool = False,
    active_clip: str | None = None,
) -> dict[str, Any]:
    """Bake many named motions onto one ``rigged_glb`` (multi-clip deliverable).

    Each clip is exported to a temporary HML22 source, retargeted with the
    Animator3D ``hml22`` profile, and left as its own action. The GLB is written
    with ``export_animation_mode='ACTIONS'`` so every clip survives reimport
    (same contract as Animator3D ``game-pack``).

    Args:
        clips: Sequence of :class:`MotionClip`, or ``(name, joints)`` /
            ``(name, npz_path)`` tuples.
        rigged_glb: Target SkinTokens / hero ``*_rigged.glb``.
        output: Destination animated GLB (mesh + all retargeted clips).
        fps: Default bake FPS when a clip/NPZ omits it.
        profile_name: Animator3D retarget profile (default ``hml22``).
        keep_sources_dir: If set, write each intermediate HML22 source GLB here
            as ``{clip}_hml22_source.glb``.
        in_place: Default loopable clip (no travel/yaw drift).
        arm_neutral: Default soft hang arm calibration policy.
        max_lean_deg / hands_together_m / plant_feet: Pack-level constraints
            (opt-in; see Motion3D findings). Per-clip fields override when set.
        active_clip: Clip promoted to the armature's active action (viewers that
            ignore NLA). Defaults to the first clip.

    Returns:
        Dict with ``output``, ``clips`` (per-clip retarget results), ``active``.
    """
    normalized = [_coerce_clip(c) for c in clips]
    if not normalized:
        raise ValueError("clips must be non-empty")
    names = [c.name for c in normalized]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate clip names: {names}")

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rigged = Path(rigged_glb).expanduser().resolve()
    if not rigged.is_file():
        raise FileNotFoundError(rigged)

    from aigamekit_shared.bpy_mesh import import_gltf, save_glb
    from animator3d import retarget as rt
    from motion3d.bpy_export import (
        HML22_BONE_NAMES,
        HML22_NEUTRAL_AIM,
        HML22_TARGET_REST_BONES,
        export_joints_glb,
        filter_neutral_aim_for_clip,
    )

    profile = rt.load_profile(profile_name)
    foot_names = [HML22_BONE_NAMES[i] for i in sorted(HML22_TARGET_REST_BONES)]
    # Rest aims need a throwaway bpy session — cache once before the pack scene.
    foot_aims = target_rest_aims(rigged, profile.bone_map, foot_names)

    if keep_sources_dir is not None:
        keep_sources_dir = Path(keep_sources_dir).expanduser().resolve()
        keep_sources_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="motion3d_pack_") as tmp:
        tmp_path = Path(tmp)
        # Pre-bake every source GLB (constraints + look-at) before the pack scene.
        prepared: list[tuple[MotionClip, Path, int, np.ndarray]] = []
        for clip in normalized:
            joints, clip_fps = load_clip_joints(clip)
            if joints.ndim != 3 or joints.shape[1] != 22 or joints.shape[2] != 3:
                raise ValueError(f"clip {clip.name!r}: joints must be (T, 22, 3); got {joints.shape}")
            use_fps = int(clip_fps if clip_fps is not None else fps)
            src_name = f"{clip.name}_hml22_source.glb"
            src_path = (keep_sources_dir / src_name) if keep_sources_dir else tmp_path / src_name
            base_neutral = {HML22_BONE_NAMES[i]: d for i, d in HML22_NEUTRAL_AIM.items()}
            base_neutral.update(foot_aims)
            use_arm = str(_pick(clip.arm_neutral, arm_neutral))
            neutral_aim = filter_neutral_aim_for_clip(joints, base_neutral, arm_neutral=use_arm)
            export_joints_glb(
                joints,
                src_path,
                fps=use_fps,
                clip_name=DEFAULT_SOURCE_TRACK,
                in_place=bool(_pick(clip.in_place, in_place)),
                neutral_aim=neutral_aim,
                max_lean_deg=_pick(clip.max_lean_deg, max_lean_deg),
                hands_together_m=_pick(clip.hands_together_m, hands_together_m),
                plant_feet=bool(_pick(clip.plant_feet, plant_feet)),
            )
            prepared.append((clip, src_path, use_fps, joints))

        bpy = _bpy()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        # Scene FPS = first clip; each source was baked at its own fps already.
        bpy.context.scene.render.fps = int(prepared[0][2])
        import_gltf(rigged)
        targets = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if not targets:
            raise RuntimeError(f"No armature in {rigged}")
        target = targets[0]
        target.name = "Target"
        rt.ensure_feet_root_bone(target)

        if target.animation_data is not None:
            while target.animation_data.nla_tracks:
                target.animation_data.nla_tracks.remove(target.animation_data.nla_tracks[0])
            target.animation_data.action = None

        results: list[dict[str, Any]] = []
        kept_actions: set[str] = set()
        for clip, src_path, use_fps, _joints in prepared:
            bpy.context.scene.render.fps = int(use_fps)
            before = {o.name for o in bpy.data.objects}
            before_actions = {a.name for a in bpy.data.actions}
            import_gltf(src_path)
            sources = [o for o in bpy.data.objects if o.type == "ARMATURE" and o.name not in before]
            if not sources:
                sources = [o for o in bpy.data.objects if o.type == "ARMATURE" and o.name != "Target"]
            if not sources:
                raise RuntimeError(f"No source armature from {src_path}")
            source = sources[0]
            source.name = "HML22_Source"

            src_action = _resolve_source_action(bpy, source, DEFAULT_SOURCE_TRACK)
            if src_action is None:
                raise RuntimeError(f"Source action missing after import ({clip.name})")
            src_action.name = DEFAULT_SOURCE_TRACK
            if source.animation_data is None:
                source.animation_data_create()
            source.animation_data.action = src_action

            # Drop colliding target clip name before retarget creates a fresh one.
            old = bpy.data.actions.get(clip.name)
            if old is not None:
                with contextlib.suppress(RuntimeError):
                    bpy.data.actions.remove(old)

            res = rt.retarget_animation(
                "Target",
                "HML22_Source",
                profile.bone_map,
                DEFAULT_SOURCE_TRACK,
                clip.name,
            )
            results.append(res)
            kept_actions.add(clip.name)

            bpy.data.objects.remove(source, do_unlink=True)
            # Remove only actions that arrived with this source import.
            for act in list(bpy.data.actions):
                if act.name in kept_actions:
                    continue
                if act.name in before_actions:
                    continue
                with contextlib.suppress(RuntimeError):
                    bpy.data.actions.remove(act)

        active_name = active_clip or normalized[0].name
        if active_name not in kept_actions:
            raise ValueError(f"active_clip {active_name!r} not in pack {sorted(kept_actions)}")
        clip_act = bpy.data.actions.get(active_name)
        if target.animation_data is None:
            target.animation_data_create()
        # Keep NLA strips (retarget pushed each clip) AND set active for viewers
        # that only play the active action.
        if clip_act is not None:
            target.animation_data.action = clip_act

        _strip_debug_meshes(bpy)
        # O glTF exporter converte TODAS as actions com o fps de cena único.
        # O loop re-definia o fps por clip e o save corria com o do ÚLTIMO —
        # packs com fps mistos exportavam durações erradas. Voltar ao fps do
        # primeiro clip (contrato "Scene FPS = first clip") e avisar quando o
        # pack mistura fps (conversão única não os pode representar a todos).
        fps_set = {int(f) for _, _, f, _ in prepared}
        if len(fps_set) > 1:
            import warnings

            warnings.warn(
                f"apply_motions_to_rigged: pack com fps mistos {sorted(fps_set)} — "
                "a exportação usa um fps de cena único; clips com fps diferente "
                "do primeiro terão duração ajustada no GLB final.",
                stacklevel=2,
            )
        bpy.context.scene.render.fps = int(prepared[0][2])
        objs = [o for o in bpy.context.scene.objects if o.type in ("ARMATURE", "MESH")]
        save_glb(
            objs,
            out,
            export_animations=True,
            export_animation_mode="ACTIONS",
            export_skins=True,
            export_apply=False,
            skip_verify=True,
        )

    return {
        "output": out,
        "clips": results,
        "active": active_name,
        "clip_names": [c.name for c in normalized],
    }


def _coerce_clip(clip: MotionClip | tuple[str, Any]) -> MotionClip:
    if isinstance(clip, MotionClip):
        return clip
    if not isinstance(clip, tuple) or len(clip) != 2:
        raise TypeError(f"clip must be MotionClip or (name, joints|path); got {type(clip)!r}")
    name, payload = clip
    name_s = str(name)
    if isinstance(payload, (str, Path)):
        return MotionClip(name=name_s, npz_path=Path(payload))
    return MotionClip(name=name_s, joints=np.asarray(payload, dtype=np.float64))


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
    arm_neutral: str = "auto",
    max_lean_deg: float | None = None,
    hands_together_m: float | None = None,
    plant_feet: bool = False,
) -> dict[str, Any]:
    """Bake one Y-up HML motion onto ``rigged_glb`` (thin wrapper over the pack).

    ``source_track`` is accepted for API compatibility; pack sources always use
    :data:`DEFAULT_SOURCE_TRACK` internally.
    """
    del source_track  # pack path owns the source action name
    keep_dir = Path(keep_source).expanduser().resolve().parent if keep_source is not None else None
    # If caller asked for an exact source path, bake via pack keep_sources then rename.
    clip = MotionClip(name=clip_name, joints=np.asarray(joints, dtype=np.float64), fps=fps)
    res = apply_motions_to_rigged(
        [clip],
        rigged_glb,
        output,
        fps=fps,
        profile_name=profile_name,
        keep_sources_dir=keep_dir,
        in_place=in_place,
        arm_neutral=arm_neutral,
        max_lean_deg=max_lean_deg,
        hands_together_m=hands_together_m,
        plant_feet=plant_feet,
        active_clip=clip_name,
    )
    source_glb = None
    if keep_source is not None and keep_dir is not None:
        baked = keep_dir / f"{clip_name}_hml22_source.glb"
        dest = Path(keep_source).expanduser().resolve()
        if baked.is_file() and baked.resolve() != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            baked.replace(dest)
            source_glb = dest
        elif baked.is_file():
            source_glb = baked
    return {
        "output": res["output"],
        "retarget": res["clips"][0],
        "source_glb": source_glb,
    }


def apply_npz_to_rigged(
    npz_path: str | Path,
    rigged_glb: str | Path,
    output: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load NPZ ``joints`` and call :func:`apply_motion_to_rigged`."""
    joints, npz_fps = _load_npz_joints(Path(npz_path))
    # pop ANTES de escolher a fonte — o fps do NPZ vem no argumento explícito
    # e o kwargs ainda o trazia → "multiple values for keyword 'fps'".
    kwargs_fps = kwargs.pop("fps", None)
    fps = int(npz_fps if npz_fps is not None else (kwargs_fps if kwargs_fps is not None else 20))
    return apply_motion_to_rigged(joints, rigged_glb, output, fps=fps, **kwargs)


def apply_npzs_to_rigged(
    motions: Sequence[str | MotionClip],
    rigged_glb: str | Path,
    output: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """Pack many ``name=path.npz`` specs (or :class:`MotionClip`) onto one GLB."""
    clips: list[MotionClip] = []
    for item in motions:
        if isinstance(item, MotionClip):
            clips.append(item)
        else:
            clips.append(parse_motion_spec(str(item)))
    return apply_motions_to_rigged(clips, rigged_glb, output, **kwargs)
