"""Regressão: ``memory_efficient`` tem de chegar ao planner de colocação.

O ``SkymapGenerator`` aceitava ``memory_efficient`` e guardava-o no base, mas
**nunca o consultava** ao colocar o pipeline. O planner decidia só pela pegada
declarada (``flux-dev-uint4``: 2.2 GiB de pesos, que descreve o transformer e
ignora os text encoders e a LoRA já fundida), concluía "4.2 de 5.1 GiB, cabe",
escolhia full-GPU — e o load rebentava com 5.31 GiB alocados numa placa de 6 GB.

Com a correção, ``ums calibrate skymap2d`` passa a carregar e a gerar
(pico medido 4624 MiB).
"""

from __future__ import annotations

import inspect

from skymap2d.generator import SkymapGenerator


class TestPlacementHonoursMemoryEfficient:
    def test_planner_call_forwards_memory_efficient(self):
        source = inspect.getsource(SkymapGenerator._load_pipeline)
        assert "force_group_offload=bool(self.memory_efficient)" in source

    def test_memory_efficient_is_stored_on_the_instance(self):
        gen = SkymapGenerator.__new__(SkymapGenerator)
        gen.memory_efficient = True
        assert gen.memory_efficient is True

    def test_constructor_accepts_memory_efficient(self):
        params = inspect.signature(SkymapGenerator.__init__).parameters
        assert "memory_efficient" in params
        # Default False: quem quer o caminho poupado tem de o pedir (hw-auto pede).
        assert params["memory_efficient"].default is False

    def test_planner_still_receives_the_footprint_and_target_resolution(self):
        """A correção não pode ter descartado os outros argumentos."""
        source = inspect.getsource(SkymapGenerator._load_pipeline)
        assert "_flux_dev_uint4_footprint()" in source
        assert "target_resolution=2048" in source
        assert 'model_attr="transformer"' in source


class TestPlacementGroupOffloadGate:
    """Group offload + CUDA streams default ON (gate de folga no planner)."""

    def test_class_attrs_env_and_budget(self):
        assert SkymapGenerator.GROUP_OFFLOAD_ENV == "SKYMAP2D_GROUP_OFFLOAD"
        assert SkymapGenerator.FULL_GPU_BUDGET_FRACTION == 0.70

    def test_constructor_accepts_group_offload_default_on(self):
        params = inspect.signature(SkymapGenerator.__init__).parameters
        assert "group_offload" in params
        assert params["group_offload"].default is True

    def test_planner_call_uses_go_planner_kwargs(self):
        """O placement despeja ``_go_planner_kwargs`` (gate de folga full-GPU)."""
        source = inspect.getsource(SkymapGenerator._load_pipeline)
        assert "_go_planner_kwargs(" in source
        assert "FULL_GPU_BUDGET_FRACTION" in source
        # O force histórico (mem_eff → GO direto) mantém-se.
        assert "force_group_offload=bool(self.memory_efficient)" in source
