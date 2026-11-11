"""Fixtures partilhados dos testes do GameAssets."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_live_vramd_topology(monkeypatch):
    """Nunca consultar a topologia de um vramd real que esteja a correr na máquina.

    Os builders de specs e o ``run_gpu_wave`` perguntam ao vramd quantas GPUs
    tem; sem isto um teste daria resultados diferentes com e sem um supervisor
    vivo. Os testes do router ligam a topologia explicitamente.
    """
    from aigamekit_shared import vramd_topology

    monkeypatch.setenv("VRAMD_TOPOLOGY", "0")
    vramd_topology.clear_cache()
    yield
    vramd_topology.clear_cache()
