"""Retarget: root nos pés + location pelvis (anti hang-from-waist)."""

from __future__ import annotations

from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

from animator3d.retarget import (  # noqa: E402
    _LOCATION_SRC_BONES,
    ensure_feet_root_bone,
    load_profile,
)


def _import_glb(path: Path) -> object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.import_scene.gltf(filepath=str(path))
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    assert arms, f"sem armature em {path}"
    return arms[0]


class TestEnsureFeetRootBone:
    def test_bandit_gains_root_parenting_pelvis(self) -> None:
        # Rig SkinTokens cru (pelvis-as-root) — não o lod público já animado.
        glb = Path(
            "/home/maikeu/GitClones/GameDev/VibeGame/examples/simple-rpg/"
            "public/assets/meshes/_intermediate/bandit_lod0_rigged.glb"
        )
        if not glb.is_file():
            pytest.skip("bandit_lod0_rigged.glb ausente")
        arm = _import_glb(glb)
        assert "root" not in arm.data.bones
        assert arm.data.bones["pelvis"].parent is None
        assert ensure_feet_root_bone(arm) is True
        assert "root" in arm.data.bones
        assert arm.data.bones["pelvis"].parent is not None
        assert arm.data.bones["pelvis"].parent.name == "root"
        assert arm.data.bones["root"].parent is None
        # idempotent
        assert ensure_feet_root_bone(arm) is False


class TestProfileMapsRoot:
    def test_quaternius_profiles_keep_root_static(self) -> None:
        for name in ("quaternius", "quaternius-hero"):
            p = load_profile(name)
            # root must NOT be retargeted (avoids ±90° jump to waist on play)
            assert "root" not in p.bone_map
            assert "pelvis" in p.bone_map
            assert "pelvis" in _LOCATION_SRC_BONES
            assert "root" not in _LOCATION_SRC_BONES
