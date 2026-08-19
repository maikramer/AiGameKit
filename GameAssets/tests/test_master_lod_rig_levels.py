"""Ladder rigada: só os níveis próximos levam esqueleto.

Um LOD que carrega o seu próprio rig custa no runtime mesmo escondido — o
VibeGame percorre e recompõe todos os nós da cena por frame. No ``simple-rpg``
eram ~11k ossos, 12.3k de 15.4k nós da cena, quase todos em níveis de LOD que
ninguém vê animar. A ladder passa ``--rig-max-level`` ao ``text3d lod`` e o
gate/validação deixa de exigir ``skins[]`` nos níveis acima do limite.
"""

from __future__ import annotations

import inspect

import pytest


class TestConstant:
    def test_default_keeps_rig_through_lod1(self) -> None:
        from gameassets.pipeline import RIG_MAX_LEVEL

        assert RIG_MAX_LEVEL == 1

    def test_matches_text3d_default(self) -> None:
        pytest.importorskip("text3d")
        from gameassets.pipeline import RIG_MAX_LEVEL
        from text3d.utils.mesh_lod import DEFAULT_RIG_MAX_LEVEL

        assert RIG_MAX_LEVEL == DEFAULT_RIG_MAX_LEVEL


class TestRiggedLadderWiring:
    def _master_source(self) -> str:
        from gameassets import pipeline

        source = inspect.getsource(pipeline)
        start = source.index("# Stage 9 - lod0 + ladder a partir da fonte rigada.")
        return source[start : start + 4000]

    def test_passes_rig_max_level_to_text3d(self) -> None:
        source = self._master_source()

        assert '"--rig-max-level"' in source
        assert "str(RIG_MAX_LEVEL)" in source

    def test_gate_accepts_a_static_far_level(self) -> None:
        """Sem isto, exigir skins[] no lod2 punha a ladder a regenerar-se sempre."""
        source = self._master_source()

        assert "if level > RIG_MAX_LEVEL:" in source
        assert "expect_rigged" in source

    def test_finish_rollback_is_level_aware(self) -> None:
        """O rollback do finish não pode restaurar por 'faltam skins' num nível estático."""
        source = self._master_source()

        assert "_finish_lod_with_rollback(p, lvl, lambda q, _lvl=lvl: expect(q, _lvl), res)" in source


class TestValidationRules:
    @pytest.mark.parametrize("level", [1, 2])
    def test_lod_rules_do_not_require_skin_attributes(self, level: int) -> None:
        from pathlib import Path

        import yaml

        import gameassets

        rules_path = Path(gameassets.__file__).parent / "data" / "rules" / f"lod{level}.yaml"
        rules = yaml.safe_load(rules_path.read_text())
        required = rules.get("attributes_required", [])

        assert "JOINTS_0" not in required
        assert "WEIGHTS_0" not in required
