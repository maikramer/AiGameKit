"""Testes do VRAMPlanner — lógica pura de evicção peso+LRU (sem GPU)."""

from __future__ import annotations

from modelserver.vram_planner import LoadedBackend, plan_eviction


def _backend(name: str, *, vram: int, priority: int, ref: int = 0, last_used: float = 0.0) -> LoadedBackend:
    return LoadedBackend(name=name, vram_mib=vram, priority=priority, ref_count=ref, last_used=last_used)


class TestPlanEviction:
    """plan_eviction: decidir quais backends evictar."""

    def test_no_eviction_when_already_free(self) -> None:
        loaded = [_backend("a", vram=1000, priority=10)]
        assert plan_eviction(loaded, needed_mib=500, free_mib=2000) == []

    def test_evict_single_backend(self) -> None:
        loaded = [_backend("a", vram=2000, priority=10, last_used=1.0)]
        result = plan_eviction(loaded, needed_mib=1500, free_mib=0)
        assert result == ["a"]

    def test_lru_order_same_priority(self) -> None:
        """Com mesma prioridade, LRU (last_used menor) é evicted primeiro."""
        loaded = [
            _backend("recent", vram=1000, priority=10, last_used=100.0),
            _backend("old", vram=1000, priority=10, last_used=1.0),
        ]
        result = plan_eviction(loaded, needed_mib=1000, free_mib=0)
        assert result == ["old"]

    def test_priority_order_different_priorities(self) -> None:
        """Priority menor (leve) é evicted antes do priority maior (pesado)."""
        loaded = [
            _backend("heavy", vram=3000, priority=50, last_used=1.0),
            _backend("light", vram=1000, priority=10, last_used=100.0),
        ]
        # Precisa 1000 MiB — evicta o leve (priority 10) mesmo sendo mais recente.
        result = plan_eviction(loaded, needed_mib=1000, free_mib=0)
        assert result == ["light"]

    def test_never_evict_referenced(self) -> None:
        """Backends com ref_count > 0 (em uso) nunca são evicted."""
        loaded = [
            _backend("busy", vram=5000, priority=10, ref=1, last_used=1.0),
            _backend("idle", vram=1000, priority=50, last_used=100.0),
        ]
        # Precisa 1000 — só "idle" está disponível, mesmo tendo priority maior.
        result = plan_eviction(loaded, needed_mib=1000, free_mib=0)
        assert result == ["idle"]

    def test_stops_when_enough_freed(self) -> None:
        loaded = [
            _backend("a", vram=1000, priority=10, last_used=1.0),
            _backend("b", vram=2000, priority=20, last_used=2.0),
            _backend("c", vram=3000, priority=30, last_used=3.0),
        ]
        # Precisa 2500 MiB — "a" (1000) não chega, evicta "a" + "b" (=3000 ≥ 2500).
        result = plan_eviction(loaded, needed_mib=2500, free_mib=0)
        assert result == ["a", "b"]

    def test_empty_loaded_returns_empty(self) -> None:
        assert plan_eviction([], needed_mib=1000, free_mib=0) == []

    def test_all_referenced_returns_empty(self) -> None:
        loaded = [_backend("a", vram=5000, priority=10, ref=1)]
        assert plan_eviction(loaded, needed_mib=1000, free_mib=0) == []

    def test_insufficient_loaded_returns_partial(self) -> None:
        """Se os backends idle não chegam, retorna o que há (caller decide)."""
        loaded = [_backend("small", vram=500, priority=10, last_used=1.0)]
        result = plan_eviction(loaded, needed_mib=5000, free_mib=0)
        assert result == ["small"]  # não checa, mas evicta tudo o que pode

    def test_mixed_priority_and_lru(self) -> None:
        """Ordenação: priority primeiro, depois LRU dentro da mesma priority."""
        loaded = [
            _backend("p10_new", vram=1000, priority=10, last_used=10.0),
            _backend("p10_old", vram=1000, priority=10, last_used=1.0),
            _backend("p20", vram=1000, priority=20, last_used=5.0),
        ]
        # Precisa 2000 — evicta p10_old, depois p10_new (soma 2000).
        result = plan_eviction(loaded, needed_mib=2000, free_mib=0)
        assert result == ["p10_old", "p10_new"]

    def test_efficiency_prefers_high_vram_low_priority(self) -> None:
        """Footprint-aware: evicta primeiro o backend com melhor efficiency
        (mais VRAM libertada por unidade de prioridade perdida)."""
        loaded = [
            # small_prio30: efficiency = 500/30 = 16.7
            _backend("small_prio30", vram=500, priority=30, last_used=1.0),
            # big_prio30: efficiency = 4000/30 = 133.3 (muito melhor — liberta muito mais)
            _backend("big_prio30", vram=4000, priority=30, last_used=2.0),
            # medium_prio10: efficiency = 1000/10 = 100
            _backend("medium_prio10", vram=1000, priority=10, last_used=3.0),
        ]
        # Precisa 3500. big_prio30 (efficiency 133) liberta 4000 sozinho → basta.
        result = plan_eviction(loaded, needed_mib=3500, free_mib=0)
        assert result == ["big_prio30"]

    def test_efficiency_minimizes_priority_lost(self) -> None:
        """Quando dois backends pequenos somam o mesmo que um grande, prefere
        evictar os de prioridade baixa (menos valor perdido)."""
        loaded = [
            # Dois backends priority=10, vram=2000 cada (efficiency = 200)
            _backend("low_a", vram=2000, priority=10, last_used=1.0),
            _backend("low_b", vram=2000, priority=10, last_used=2.0),
            # Um backend priority=40, vram=4000 (efficiency = 100)
            _backend("high", vram=4000, priority=40, last_used=3.0),
        ]
        # Precisa 4000. efficiency: low_a/low_b = 200 > high = 100.
        # Evicta low_a (2000) + low_b (4000) → 4000. Prefere não tocar no high.
        result = plan_eviction(loaded, needed_mib=4000, free_mib=0)
        assert "high" not in result
        assert set(result) == {"low_a", "low_b"}
