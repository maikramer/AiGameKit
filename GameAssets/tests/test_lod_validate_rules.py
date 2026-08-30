"""Regras de validação por nível de LOD numa ladder rigada.

A ladder gera os níveis acima de ``RIG_MAX_LEVEL`` **estáticos de propósito**
(``text3d lod --rig-max-level``). Validá-los com ``rigged.yaml`` /
``animated.yaml`` exigia armature + actions + ``JOINTS_0`` que nunca lá estão,
por isso ``validate-lod2`` falhava em qualquer personagem.
"""

from __future__ import annotations

from gameassets.pipeline import RIG_MAX_LEVEL


def _rule_for(promotion_kind: str) -> dict[int, str]:
    """Espelha a escolha de regras do master pipeline."""
    static_rules = {0: "lod0.yaml", 1: "lod1.yaml", 2: "lod2.yaml"}
    promoted_rules = {"animated": "animated.yaml", "rigged": "rigged.yaml"}.get(promotion_kind)
    return {
        lvl: (promoted_rules if promoted_rules and lvl <= RIG_MAX_LEVEL else static_rules[lvl]) for lvl in (0, 1, 2)
    }


class TestLodRuleSelection:
    def test_levels_above_rig_max_use_static_rules(self) -> None:
        for kind in ("animated", "rigged"):
            rules = _rule_for(kind)
            for lvl in (0, 1, 2):
                if lvl > RIG_MAX_LEVEL:
                    assert rules[lvl] == f"lod{lvl}.yaml", f"{kind} lvl={lvl}"
                else:
                    assert rules[lvl].startswith(kind), f"{kind} lvl={lvl}"

    def test_animated_ladder_keeps_animated_rules_up_to_rig_max(self) -> None:
        rules = _rule_for("animated")
        assert rules[0] == "animated.yaml"
        assert rules[RIG_MAX_LEVEL] == "animated.yaml"
        assert rules[2] == "lod2.yaml"

    def test_rigged_ladder_keeps_rigged_rules_up_to_rig_max(self) -> None:
        rules = _rule_for("rigged")
        assert rules[0] == "rigged.yaml"
        assert rules[RIG_MAX_LEVEL] == "rigged.yaml"
        assert rules[2] == "lod2.yaml"

    def test_static_asset_uses_lod_rules_everywhere(self) -> None:
        assert _rule_for("none") == {0: "lod0.yaml", 1: "lod1.yaml", 2: "lod2.yaml"}

    def test_pipeline_source_matches_this_contract(self) -> None:
        """Regressão: o pipeline não pode voltar a mapear os 3 níveis fixos."""
        import inspect

        from gameassets import pipeline

        src = inspect.getsource(pipeline)
        assert 'rule_for = {0: "rigged.yaml", 1: "rigged.yaml", 2: "rigged.yaml"}' not in src
        assert 'rule_for = {0: "animated.yaml", 1: "animated.yaml", 2: "animated.yaml"}' not in src
        assert "lvl <= rig_max_level" in src
