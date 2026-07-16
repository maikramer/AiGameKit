"""animator_preset_for_category — category creature defaults to humanoid mocap."""

from __future__ import annotations

from gameassets.categories import animator_preset_for_category


class TestAnimatorPresetForCategory:
    def test_humanoid(self) -> None:
        assert animator_preset_for_category("humanoid") == "humanoid"

    def test_creature_defaults_to_humanoid_mocap(self) -> None:
        # Enemies tagged category=creature are usually bipedal; non-humanoids
        # must set animate.preset explicitly (creature/flying + procedural).
        assert animator_preset_for_category("creature") == "humanoid"

    def test_unknown_is_static(self) -> None:
        assert animator_preset_for_category("vegetation") == "static"
        assert animator_preset_for_category(None) == "static"
