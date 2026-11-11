"""Topologia do vramd e construção de ``hw_tiers``."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from aigamekit_shared import vramd_topology as T


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("VRAMD_TOPOLOGY", raising=False)
    T.clear_cache()
    yield
    T.clear_cache()


TOPO = {
    "status": "ok",
    "capacity": 3,
    "routed": True,
    "gpu_sizes_mib": [24576, 6144, 6144, 0, "x"],
    "features": ["routing", "hw_tiers", "criticality"],
    "lanes": [{"lane": "gpu0"}],
    "peers": [],
}


class TestFetch:
    def test_parses_and_caches(self) -> None:
        calls: list[bool] = []

        def fetch(force_local: bool) -> dict[str, Any]:
            calls.append(force_local)
            return TOPO

        topo = T.fetch_topology(fetch=fetch)
        assert topo is not None and topo.capacity == 3 and topo.parallel
        assert topo.gpu_sizes_mib == (6144, 24576)
        assert topo.supports_tiers and topo.supports_criticality
        assert T.fetch_topology(fetch=fetch) is topo
        assert calls == [False]
        T.fetch_topology(remote=False, fetch=fetch)
        assert calls == [False, True]  # cache separada para o vramd local

    def test_old_server_or_down_is_none(self) -> None:
        assert T.fetch_topology(fetch=lambda _l: {"status": "error", "error": "comando desconhecido"}) is None
        assert T.fetch_topology(refresh=True, fetch=lambda _l: None) is None

        def boom(_l: bool) -> dict[str, Any]:
            raise OSError("connection refused")

        assert T.fetch_topology(refresh=True, fetch=boom) is None

    def test_kill_switch(self, monkeypatch) -> None:
        monkeypatch.setenv("VRAMD_TOPOLOGY", "0")
        assert T.fetch_topology(fetch=lambda _l: TOPO) is None

    def test_single_gpu_is_not_parallel(self) -> None:
        topo = T.Topology.from_response({"status": "ok", "capacity": 1, "gpu_sizes_mib": [6144]})
        assert not topo.parallel and not topo.supports_tiers  # sem a feature anunciada


class TestDefaultFetch:
    def test_uses_canonical_socket_and_remote_flag(self, monkeypatch) -> None:
        from aigamekit_shared import vramd_client

        calls: dict[str, Any] = {}

        def _ensure(**kwargs: Any) -> bool:
            calls["ensure"] = kwargs
            return True

        def _send(
            req: dict[str, Any],
            socket_path: Any = None,
            *,
            timeout_sec: float,
            force_local: bool = False,
        ) -> dict[str, Any]:
            calls["send"] = (req, socket_path, force_local)
            return {"status": "ok", "capacity": 1}

        monkeypatch.setattr(vramd_client, "ensure_vramd_running", _ensure)
        monkeypatch.setattr(vramd_client, "send_request", _send)

        assert T._default_fetch(True)["status"] == "ok"

        assert calls["ensure"] == {"auto_start": False, "force_local": True}
        assert calls["send"][1] == vramd_client.VRAMD_SOCKET
        assert calls["send"][2] is True

    def test_status_routing_fallback_when_topology_unknown(self, monkeypatch) -> None:
        """vramd 0.7.10: sem ``topology`` mas com ``routing.gpus`` no status.

        A wave tem de ver ``capacity=2`` (duas GPUs) em vez de 1; sem isto a
        segunda GPU remota ficava parada e os paints corriam em série.
        """
        from aigamekit_shared import vramd_client

        requests: list[dict[str, Any]] = []

        def _send(req: dict[str, Any], socket_path: Any = None, *, timeout_sec: float, force_local: bool = False):
            requests.append(req)
            if req["cmd"] == "topology":
                return {"status": "error", "error": "comando desconhecido: topology"}
            return {
                "status": "status",  # o comando status responde com o nome, não "ok"
                "routing": {
                    "version": "0.7.10",
                    "active": True,
                    "gpus": [
                        {"index": 0, "lane": "gpu0", "total_mib": 12288, "free_mib": 846},
                        {"index": 1, "lane": "gpu1", "total_mib": 12288, "free_mib": 11863},
                    ],
                },
                "lanes": [{"lane": "gpu0"}, {"lane": "gpu1"}],
                "peers": [],
            }

        monkeypatch.setattr(vramd_client, "ensure_vramd_running", lambda **_k: True)
        monkeypatch.setattr(vramd_client, "send_request", _send)

        resp = T._default_fetch(False)

        assert [r["cmd"] for r in requests] == ["topology", "status"]
        assert resp is not None
        topo = T.Topology.from_response(resp)
        assert topo.capacity == 2 and topo.parallel
        assert topo.gpu_sizes_mib == (12288,)
        assert topo.supports_tiers and topo.supports_criticality

    def test_status_without_routing_is_still_none(self, monkeypatch) -> None:
        """vramd antigo: status sem routing -> None (compatibilidade)."""
        from aigamekit_shared import vramd_client

        def _send(req: dict[str, Any], socket_path: Any = None, *, timeout_sec: float, force_local: bool = False):
            if req["cmd"] == "topology":
                return {"status": "error", "error": "comando desconhecido: topology"}
            return {"status": "status", "pid": 1, "queue": {"inflight": 0}}

        monkeypatch.setattr(vramd_client, "ensure_vramd_running", lambda **_k: True)
        monkeypatch.setattr(vramd_client, "send_request", _send)

        assert T._default_fetch(False) is None


class TestBuildHwTiers:
    BASE: ClassVar[dict[str, Any]] = {"prompt": "x", "sdnq_preset": "sdnq-int4", "memory_efficient": True}

    @staticmethod
    def variant(size: int) -> dict[str, Any]:
        if size >= 10240:
            return {"prompt": "x", "memory_efficient": False}  # fp16: o builder omite o sdnq
        return {"prompt": "x", "sdnq_preset": "sdnq-int4", "memory_efficient": True}

    def test_two_sizes_two_tiers(self) -> None:
        tiers = T.build_hw_tiers(self.BASE, self.variant, [24576, 6144])
        assert tiers == [
            {"min_vram_mib": 0, "label": "6g+", "set": {}, "rank": 0},
            {
                "min_vram_mib": 24576,
                "label": "24g+",
                "set": {"memory_efficient": False, "sdnq_preset": None},
                "rank": 1,
            },
        ]

    def test_same_profile_everywhere_means_no_tiers(self) -> None:
        assert T.build_hw_tiers(self.BASE, self.variant, [6144, 8192]) == []

    def test_consecutive_equal_profiles_merge(self) -> None:
        tiers = T.build_hw_tiers(self.BASE, self.variant, [6144, 8192, 12288, 24576])
        assert [(t["min_vram_mib"], t["rank"]) for t in tiers] == [(0, 0), (12288, 1)]

    def test_keys_restrict_the_diff(self) -> None:
        def noisy(size: int) -> dict[str, Any]:
            return {**self.variant(size), "nonce": size}

        tiers = T.build_hw_tiers(self.BASE, noisy, [6144, 24576], keys=["sdnq_preset", "memory_efficient"])
        assert all("nonce" not in t["set"] for t in tiers)

    def test_size_without_profile_disables_tiers(self) -> None:
        """Sem perfil para a GPU de 6 GB, o tier fp16 não pode ficar com min 0 e cair nela."""
        tiers = T.build_hw_tiers(self.BASE, lambda s: None if s < 10000 else self.variant(s), [6144, 24576])
        assert tiers == []


class TestAttachRouting:
    TIERS: ClassVar[list[dict[str, Any]]] = [
        {"min_vram_mib": 0, "set": {}},
        {"min_vram_mib": 10240, "set": {"sdnq_preset": None}},
    ]

    def test_only_what_the_server_understands(self) -> None:
        full = T.Topology.from_response(TOPO)
        payload = T.attach_routing({}, topology=full, tiers=self.TIERS, criticality="high")
        assert payload == {"hw_tiers": self.TIERS, "criticality": "high"}
        old = T.Topology.from_response({"status": "ok", "capacity": 2, "gpu_sizes_mib": [6144], "features": []})
        assert T.attach_routing({}, topology=old, tiers=self.TIERS, criticality="high") == {}
        assert T.attach_routing({}, topology=None, tiers=self.TIERS, criticality="high") == {}

    def test_normal_criticality_is_not_sent(self) -> None:
        full = T.Topology.from_response(TOPO)
        assert T.attach_routing({}, topology=full, criticality="normal") == {}
        assert T.attach_routing({}, topology=full, criticality="auto") == {}


class TestCriticality:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("hero", "high"), ("Critical", "high"), ("filler", "low"), (None, "auto"), ("", "auto"), ("normal", "normal")],
    )
    def test_aliases(self, raw: Any, expected: str) -> None:
        assert T.normalize_criticality(raw) == expected

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="criticality"):
            T.normalize_criticality("urgentissimo")
