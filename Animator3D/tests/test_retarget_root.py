"""Retarget: root nos pés + location pelvis (anti hang-from-waist)."""

from __future__ import annotations

from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

from animator3d import retarget as rt  # noqa: E402
from animator3d.retarget import (  # noqa: E402
    _resolve_location_pair,
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
            "/home/maikeu/GitClones/AiGameKit/VibeGame/examples/simple-rpg/"
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

    def test_location_pair_resolves_by_role_not_name(self) -> None:
        """O hips recebe location seja qual for o naming do source (anti hang-from-waist)."""
        # Quaternius: source ``pelvis`` → target ``pelvis``.
        assert _resolve_location_pair({"pelvis": "pelvis", "thigh_l": "thigh_l"}) == ("pelvis", "pelvis")
        # KevDev: source ``B-hips`` mapeado para o candidato pelvis do target.
        assert _resolve_location_pair({"pelvis": "B-hips"}) == ("pelvis", "B-hips")
        # Target Mixamo-legacy (Hips como root animável).
        assert _resolve_location_pair({"Hips": "Hips"}) == ("Hips", "Hips")
        # Sem hips mapeado: nada recebe location.
        assert _resolve_location_pair({"thigh_l": "thigh_l"}) == (None, None)

    def test_villager_profile_maps_kevdev_hips(self) -> None:
        p = load_profile("villager")
        assert "root" not in p.bone_map
        assert "B-root" not in p.bone_map
        assert p.bone_map["B-hips"] == ["pelvis", "Hips"]
        # O par de location resolve pelo papel: B-hips é quem planta o gait.
        tgt_to_src = {"pelvis": "B-hips", "thigh_l": "B-thigh.L", "thigh_r": "B-thigh.R"}
        assert _resolve_location_pair(tgt_to_src) == ("pelvis", "B-hips")


def _mk_profile(name: str, clips: dict[str, str], replace_keys: list[str] | None) -> object:
    from animator3d.retarget import RetargetProfile

    return RetargetProfile(name=name, bone_map={}, clip_map=clips, replace_keys=replace_keys)


class TestPlanPackPasses:
    """Combinação de packs: quem substitui quem, sem duplicar tracks."""

    def test_blanket_profiles_replace_all_collisions(self) -> None:
        ual1 = _mk_profile("a", {"idle": "Idle_A", "gather": "Gather_A"}, None)
        ual2 = _mk_profile("b", {"gather": "Gather_B", "chop": "Chop_B"}, None)
        plan = rt.plan_pack_passes([ual1, ual2])
        assert plan[0][1] == {"idle": "Idle_A", "gather": "Gather_A"}
        # UAL2 substitui gather e acrescenta chop.
        assert plan[1][1] == {"gather": "Gather_B", "chop": "Chop_B"}

    def test_replace_keys_limits_replacements(self) -> None:
        """Pack add-on (villager): substitui só replace_keys, resto acrescenta."""
        ual = _mk_profile("ual", {"idle": "Idle_Loop", "gather": "Farm_Harvest"}, None)
        villager = _mk_profile(
            "villager",
            {"idle": "HumanM@Idle01", "gather": "Gathering01", "mine": "Mining01"},
            ["mine", "gather"],
        )
        plan = rt.plan_pack_passes([ual, villager])
        # idle colide mas NÃO está em replace_keys -> villager não o retargetiza.
        assert plan[1][1] == {"gather": "Gathering01", "mine": "Mining01"}

    def test_addon_profile_with_empty_replace_keys_never_replaces(self) -> None:
        ual = _mk_profile("ual", {"idle": "Idle_Loop"}, None)
        addon = _mk_profile("addon", {"idle": "Other_Idle", "wave": "Wave"}, [])
        plan = rt.plan_pack_passes([ual, addon])
        assert plan[1][1] == {"wave": "Wave"}

    def test_first_pass_always_full(self) -> None:
        villager = _mk_profile("villager", {"idle": "HumanM@Idle01"}, ["mine"])
        plan = rt.plan_pack_passes([villager])
        assert plan[0][1] == {"idle": "HumanM@Idle01"}

    def test_all_order_ual_wins_shared_keys(self) -> None:
        """Cadeia all (villager→UAL1→UAL2): UAL substitui idle/gather; mine sobrevive."""
        villager = _mk_profile(
            "villager",
            {"idle": "V_Idle", "gather": "V_Gather", "mine": "V_Mine", "plow": "V_Plow"},
            ["mine", "gather"],
        )
        ual1 = _mk_profile("ual1", {"idle": "Idle_Loop", "gather": "Fixing"}, None)
        ual2 = _mk_profile("ual2", {"gather": "Farm_Harvest", "harvest": "Farm_Harvest"}, None)
        plan = rt.plan_pack_passes([villager, ual1, ual2])
        # villager corre primeiro: clip_map completo (nada para substituir).
        assert plan[0][1]["mine"] == "V_Mine"
        # UAL1 substitui idle/gather; mine/plow sobrevivem (não definidos na UAL).
        assert plan[1][1] == {"idle": "Idle_Loop", "gather": "Fixing"}
        # UAL2 substitui gather de novo; mine/plow continuam na cadeia.
        assert plan[2][1] == {"gather": "Farm_Harvest", "harvest": "Farm_Harvest"}

    def test_remove_only_replaced_keys(self) -> None:
        """Guarda-chuva do remove_clips: nunca remover key fora do efectivo."""
        ual = _mk_profile("ual", {"idle": "Idle_Loop", "plow": "V_Plow"}, None)
        addon = _mk_profile("addon", {"idle": "Other", "wave": "Wave"}, ["wave"])
        plan = rt.plan_pack_passes([ual, addon])
        _addon, eff = plan[1]
        already = set(ual.clip_map)
        to_remove = [k for k in eff if k in already]
        assert to_remove == []  # idle salto: o da UAL fica intacto
