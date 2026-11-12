"""GameAssets ↔ router do vramd: criticidade, hw_tiers, janela rolante e prefetch shape→paint."""

from __future__ import annotations

import importlib.util
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import call, patch

import pytest

from aigamekit_shared.vramd_topology import Topology
from gameassets import vramd_coord
from gameassets.manifest import _rows_from_doc
from gameassets.vramd_batch import _evict_backend, shape_specs_from_items, text2d_specs_from_items
from gameassets.vramd_coord import (
    UmsJobResult,
    UmsJobSpec,
    attach_gpu,
    register_prefetched,
    run_gpu_wave,
    take_prefetched,
    wave_window,
)
from gameassets.vramd_routing import PaintPrefetcher, effective_criticality

# `shape_specs_from_items`/`text2d_specs_from_items` usam o builder da tool
# real (text3d/text2d); o job de CI do GameAssets só instala Shared + este
# pacote, por isso os testes de tiers saltam quando a tool não está presente.
_NEEDS_TEXT3D = pytest.mark.skipif(
    importlib.util.find_spec("text3d") is None, reason="text3d não instalado"
)
_NEEDS_TEXT2D = pytest.mark.skipif(
    importlib.util.find_spec("text2d") is None, reason="text2d não instalado"
)

TWO_SIZES = Topology(
    capacity=3,
    routed=True,
    gpu_sizes_mib=(6144, 24576),
    features=frozenset({"routing", "hw_tiers", "criticality"}),
)
ONE_GPU = Topology(capacity=1, gpu_sizes_mib=(6144,), features=frozenset({"routing", "hw_tiers", "criticality"}))


@pytest.fixture(autouse=True)
def _clean_registry():
    vramd_coord.clear_prefetched()
    yield
    vramd_coord.clear_prefetched()


def parse_manifest_rows(entries: list[dict[str, Any]]) -> list[Any]:
    return _rows_from_doc({"assets": entries}, Path("manifest.yaml"))


def _row(**over: Any) -> SimpleNamespace:
    base = {"id": "a", "category": "", "kind": None, "criticality": "auto", "gpu": "auto"}
    base.update(over)
    return SimpleNamespace(**base)


class TestCriticality:
    @pytest.mark.parametrize(
        ("row", "expected"),
        [
            (_row(category="humanoid"), "high"),
            (_row(category="rock"), "low"),
            (_row(category="vegetation"), "low"),
            (_row(category="building"), "normal"),
            (_row(category="knight"), "high"),  # alias da inferência de categorias
            (_row(category=""), "normal"),
            (_row(category="rock", criticality="high"), "high"),  # o manifesto manda
            (_row(category="humanoid", criticality="filler"), "low"),
        ],
    )
    def test_effective(self, row: SimpleNamespace, expected: str) -> None:
        assert effective_criticality(row) == expected

    def test_manifest_field(self) -> None:
        rows = parse_manifest_rows(
            [
                {"id": "boss", "idea": "dragon boss", "criticality": "hero"},
                {"id": "pebble", "idea": "pebble"},
            ]
        )
        assert [r.criticality for r in rows] == ["high", "auto"]
        with pytest.raises(ValueError, match="criticality"):
            parse_manifest_rows([{"id": "x", "idea": "x", "criticality": "urgente"}])

    @pytest.mark.parametrize(
        ("category", "expected"),
        [
            ("ancient_stone_rock", "low"),
            ("boss_dragon", "high"),
            ("hero_knight", "high"),
        ],
    )
    def test_compound_category_matches_keyword_substring(self, category: str, expected: str) -> None:
        """Categorias compostas (``ancient_stone_rock``) usam o mapa por substring."""
        assert effective_criticality(_row(category=category)) == expected

    def test_attach_gpu_carries_criticality(self) -> None:
        items = [{"id": "boss"}, {"id": "pebble"}]
        attach_gpu(items, [_row(id="boss", category="creature"), _row(id="pebble", category="rock", gpu="local")])
        assert items == [
            {"id": "boss", "gpu": "auto", "criticality": "high"},
            {"id": "pebble", "gpu": "local", "criticality": "low"},
        ]


@_NEEDS_TEXT3D
class TestTiersInSpecs:
    def _shape(self, tmp_path: Path, topo: Topology | None, **kw: Any) -> dict[str, Any]:
        items = [
            {"id": "boss", "image": str(tmp_path / "a.png"), "output": str(tmp_path / "a.glb"), "criticality": "high"}
        ]
        with patch("gameassets.vramd_routing.fetch_topology", return_value=topo):
            (spec,) = shape_specs_from_items(items, manifest_dir=tmp_path, **kw)
        return spec.payload

    def test_text3d_payload_carries_a_tier_per_gpu_size(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("TEXT3D_HW_AUTO", raising=False)
        payload = self._shape(tmp_path, TWO_SIZES)
        tiers = payload["hw_tiers"]
        assert [t["min_vram_mib"] for t in tiers] == [0, 24576]
        # 6 GB: hw-auto do text3d = int4; 24 GB: fp16 (sdnq removido do payload).
        assert tiers[1]["set"].get("sdnq_preset", "absent") is None
        assert tiers[1]["set"]["memory_efficient"] is False
        assert payload["criticality"] == "high"

    def test_explicit_quant_means_no_tiers(self, tmp_path: Path) -> None:
        payload = self._shape(tmp_path, TWO_SIZES, sdnq_preset="sdnq-int4")
        assert "hw_tiers" not in payload and payload["criticality"] == "high"

    def test_old_vramd_gets_the_old_payload(self, tmp_path: Path) -> None:
        payload = self._shape(tmp_path, None)
        assert "hw_tiers" not in payload and "criticality" not in payload

    def test_hw_auto_off_means_no_tiers(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("TEXT3D_HW_AUTO", "0")
        assert "hw_tiers" not in self._shape(tmp_path, TWO_SIZES)

    def test_none_override_still_gets_tiers(self, tmp_path: Path, monkeypatch) -> None:
        """``sdnq_preset: null``/``memory_efficient: null`` = usar o hw-auto da wave."""
        monkeypatch.delenv("TEXT3D_HW_AUTO", raising=False)
        items = [
            {
                "id": "boss",
                "image": str(tmp_path / "a.png"),
                "output": str(tmp_path / "a.glb"),
                "criticality": "high",
                "sdnq_preset": None,
                "memory_efficient": None,
            }
        ]
        with patch("gameassets.vramd_routing.fetch_topology", return_value=TWO_SIZES):
            (spec,) = shape_specs_from_items(items, manifest_dir=tmp_path)
        assert "hw_tiers" in spec.payload

    @_NEEDS_TEXT2D
    def test_text2d_specs(self, tmp_path: Path) -> None:
        items = [{"id": "i", "prompt": "a sword", "output": str(tmp_path / "i.png"), "criticality": "low"}]
        with patch("gameassets.vramd_routing.fetch_topology", return_value=TWO_SIZES):
            (spec,) = text2d_specs_from_items(items, manifest_dir=tmp_path)
        assert spec.criticality == "low" and spec.payload["criticality"] == "low"
        # Tiers só se o hw-auto do text2d escolher perfis diferentes para 6 e 24 GB.
        for tier in spec.payload.get("hw_tiers", []):
            assert set(tier) == {"min_vram_mib", "label", "set", "rank"}


class _FakeVramd:
    """submit/wait com relógio: regista a ordem e quantos jobs estão em voo."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.n = 0

    def submit(self, backend: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.n += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        job = f"j{self.n}"
        self.events.append(("submit", payload.get("id") or job))
        return {"status": "ok", "job_id": job}

    def wait(self, job_id: str, **_: Any) -> dict[str, Any]:
        self.in_flight -= 1
        self.events.append(("wait", job_id))
        return {"status": "ok", "output": f"/out/{job_id}"}


def _wave(specs: list[UmsJobSpec], fake: _FakeVramd, topo: Topology | None = None, max_depth: int = 32):
    with (
        patch("aigamekit_shared.vramd_client.ensure_vramd_running", return_value=True),
        patch("aigamekit_shared.vramd_client.submit_to_vramd", side_effect=fake.submit),
        patch("aigamekit_shared.vramd_client.wait_vramd_job", side_effect=fake.wait),
        patch("aigamekit_shared.vramd_client.fetch_vramd_queue_snapshot", return_value={"max_depth": max_depth}),
        patch("aigamekit_shared.vramd_topology.fetch_topology", return_value=topo),
    ):
        return run_gpu_wave("text3d", specs, preload=False)


class TestRunGpuWave:
    def _specs(self, n: int, **kw: Any) -> list[UmsJobSpec]:
        return [UmsJobSpec(asset_id=f"a{i}", payload={"id": f"a{i}"}, **kw) for i in range(n)]

    def test_window_scales_with_capacity(self) -> None:
        assert wave_window(1, 32) == 16
        assert wave_window(8, 64) == 32
        assert wave_window(8, 20) == 19  # nunca passa do max_depth do servidor
        assert wave_window(None) == 16

    def test_rolling_window_keeps_the_queue_full(self) -> None:
        """Bloco de 16: antes esperava pelos 16 antes de submeter o 17.º (GPUs paradas na cauda)."""
        fake = _FakeVramd()
        results = _wave(self._specs(20), fake)
        assert len(results) == 20 and all(r.status == "ok" for r in results)
        # Depois de encher a janela, cada wait abre logo espaço para um submit.
        first_wait = fake.events.index(("wait", "j1"))
        assert fake.events[first_wait + 1] == ("submit", "a16")
        assert fake.max_in_flight == 16

    def test_bigger_window_with_more_gpus(self) -> None:
        fake = _FakeVramd()
        _wave(self._specs(40), fake, topo=Topology(capacity=8, features=frozenset()), max_depth=64)
        assert fake.max_in_flight == 32

    def test_critical_assets_are_submitted_first(self) -> None:
        fake = _FakeVramd()
        specs = [
            UmsJobSpec(asset_id="rock", payload={"id": "rock"}, criticality="low"),
            UmsJobSpec(asset_id="hut", payload={"id": "hut"}, criticality="normal"),
            UmsJobSpec(asset_id="boss", payload={"id": "boss"}, criticality="high"),
        ]
        _wave(specs, fake)
        assert [e[1] for e in fake.events if e[0] == "submit"] == ["boss", "hut", "rock"]

    def test_prefetched_job_is_waited_not_resubmitted(self, tmp_path: Path) -> None:
        """O paint já está na fila (ou feito): o output existir não o torna "skipped"."""
        out = tmp_path / "painted.glb"
        out.write_bytes(b"glb")
        register_prefetched("text3d", str(out), "pre-1")
        fake = _FakeVramd()
        (result,) = _wave([UmsJobSpec(asset_id="a", payload={"id": "a"}, output=str(out))], fake)
        assert result.status == "ok" and result.job_id == "pre-1"
        assert fake.events == [("wait", "pre-1")]
        assert take_prefetched("text3d", str(out)) is None  # consumido: um retry submete de novo


class TestPhaseEvict:
    def test_release_between_phases_only_on_a_single_gpu(self) -> None:
        with (
            patch("aigamekit_shared.vramd_client.send_request") as send,
            patch("gameassets.vramd_routing.fetch_topology", return_value=ONE_GPU),
        ):
            _evict_backend("text2d")
        send.assert_called_once()
        with (
            patch("aigamekit_shared.vramd_client.send_request") as send,
            patch("gameassets.vramd_routing.fetch_topology", return_value=TWO_SIZES),
        ):
            _evict_backend("text2d")
        send.assert_not_called()

    def test_release_respects_the_asset_gpu_target(self) -> None:
        """``gpu: local`` não pode evictar o vramd remoto (e vice-versa)."""
        with (
            patch("aigamekit_shared.vramd_client.send_request") as send,
            patch("gameassets.vramd_routing.fetch_topology", return_value=ONE_GPU),
        ):
            _evict_backend("text2d", "local")
        assert send.call_args.kwargs["force_local"] is True
        with (
            patch("aigamekit_shared.vramd_client.send_request") as send,
            patch("gameassets.vramd_routing.fetch_topology", return_value=ONE_GPU),
        ):
            _evict_backend("text2d", "remote")
        assert send.call_args.kwargs["force_local"] is False

    def test_wave_evicts_each_destination_once(self) -> None:
        """Wave mista: um release no vramd local e outro no remoto."""
        from gameassets.vramd_batch import _evict_for_items

        items = [{"id": "l", "gpu": "local"}, {"id": "r", "gpu": "remote"}]
        with (
            patch("gameassets.vramd_coord._gpu_remote", side_effect=lambda gpu: gpu == "remote"),
            patch("gameassets.vramd_batch._evict_backend") as evict,
        ):
            _evict_for_items("text2d", items)
        assert evict.call_args_list == [call("text2d", "local"), call("text2d", "remote")]


class TestPaintPrefetcher:
    SPEC_KW: ClassVar[dict[str, Any]] = {}

    def _pf(self, tmp_path: Path, *, enabled: bool = True, prepare=None, submit=None) -> PaintPrefetcher:
        def default_prepare(asset_id: str) -> dict[str, Any] | None:
            return {
                "id": asset_id,
                "mesh": str(tmp_path / f"{asset_id}_to_paint.glb"),
                "image": str(tmp_path / f"{asset_id}.png"),
                "output": str(tmp_path / f"{asset_id}_painted.glb"),
                "criticality": "high",
            }

        submitted: list[dict[str, Any]] = []

        def default_submit(backend: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
            submitted.append({"backend": backend, **payload})
            return {"status": "ok", "job_id": f"p-{len(submitted)}"}

        pf = PaintPrefetcher(
            prepare or default_prepare,
            {"manifest_dir": tmp_path},
            enabled=enabled,
            submit=submit or default_submit,
        )
        pf.submitted_payloads = submitted  # type: ignore[attr-defined]
        return pf

    @staticmethod
    def _fake_specs(items: list[dict[str, Any]], **_: Any) -> list[UmsJobSpec]:
        return [
            UmsJobSpec(asset_id=i["id"], payload={"output": i["output"], "mesh_path": i["mesh"]}, output=i["output"])
            for i in items
        ]

    def test_shape_done_submits_its_paint(self, tmp_path: Path) -> None:
        with patch("gameassets.vramd_batch.paint_specs_from_items", side_effect=self._fake_specs):
            pf = self._pf(tmp_path)
            pf.on_shape(UmsJobResult(asset_id="boss", status="ok"))
            assert pf.owns("boss")
        assert pf.prepared_mesh("boss") == str(tmp_path / "boss_to_paint.glb")
        assert pf.submitted == {"boss": "p-1"}
        assert pf.submitted_payloads[0]["backend"] == "paint3d"  # type: ignore[attr-defined]
        assert take_prefetched("paint3d", str(tmp_path / "boss_painted.glb")) == ("p-1", "auto")

    def test_failed_shape_and_disabled_do_nothing(self, tmp_path: Path) -> None:
        pf = self._pf(tmp_path)
        pf.on_shape(UmsJobResult(asset_id="a", status="error"))
        assert not pf.owns("a") and pf.prepared_mesh("a") is None
        off = self._pf(tmp_path, enabled=False)
        off.on_shape(UmsJobResult(asset_id="a", status="ok"))
        assert not off.owns("a")

    def test_prepare_runs_off_the_batch_thread(self, tmp_path: Path) -> None:
        """A preparação (topology-fix, refine) não pode parar a espera pelos shapes."""
        gate = threading.Event()

        def slow_prepare(asset_id: str) -> dict[str, Any] | None:
            gate.wait(5.0)
            return None

        pf = self._pf(tmp_path, prepare=slow_prepare)
        t0 = time.monotonic()
        pf.on_shape(UmsJobResult(asset_id="a", status="ok"))
        assert time.monotonic() - t0 < 0.5
        gate.set()
        assert pf.prepared_mesh("a") is None and not pf.owns("a")
        pf.close()

    def test_prepare_error_leaves_the_asset_to_the_wave(self, tmp_path: Path) -> None:
        def broken(asset_id: str) -> dict[str, Any] | None:
            raise RuntimeError("topology-fix rebentou")

        pf = self._pf(tmp_path, prepare=broken)
        pf.on_shape(UmsJobResult(asset_id="a", status="ok"))
        assert pf.prepared_mesh("a") is None and not pf.owns("a")
        assert "rebentou" in pf.errors["a"]

    def test_resume_prefetch_and_dedupe(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def prepare(asset_id: str) -> dict[str, Any] | None:
            calls.append(asset_id)
            return None

        pf = self._pf(tmp_path, prepare=prepare)
        pf.prefetch("a")
        pf.on_shape(UmsJobResult(asset_id="a", status="skipped"))
        pf.close()
        assert calls == ["a"]

    def test_close_cancels_jobs_nobody_claimed(self, tmp_path: Path) -> None:
        with patch("gameassets.vramd_batch.paint_specs_from_items", side_effect=self._fake_specs):
            pf = self._pf(tmp_path)
            pf.on_shape(UmsJobResult(asset_id="a", status="ok"))
            pf.on_shape(UmsJobResult(asset_id="b", status="ok"))
            assert pf.owns("a") and pf.owns("b")
        take_prefetched("paint3d", str(tmp_path / "a_painted.glb"))  # a wave consumiu o "a"
        with patch("aigamekit_shared.vramd_client.cancel_vramd_job") as cancel:
            pf.close()
        assert [c.args[0] for c in cancel.call_args_list] == [pf.submitted["b"]]

    def test_close_is_idempotent_and_keeps_other_prefetchers_jobs(self, tmp_path: Path) -> None:
        """``close`` só cancela os jobs deste prefetcher (não limpa o registo global)."""
        with patch("gameassets.vramd_batch.paint_specs_from_items", side_effect=self._fake_specs):
            pf1 = self._pf(tmp_path)
            pf1.on_shape(UmsJobResult(asset_id="a", status="ok"))
            pf2 = self._pf(tmp_path)
            pf2.on_shape(UmsJobResult(asset_id="b", status="ok"))
            assert pf1.owns("a") and pf2.owns("b")
        with patch("aigamekit_shared.vramd_client.cancel_vramd_job") as cancel:
            pf1.close()
            pf1.close()  # idempotente: não repete cancels
        assert [c.args[0] for c in cancel.call_args_list] == [pf1.submitted["a"]]
        # O job do outro prefetcher continua reclamável pela wave.
        assert take_prefetched("paint3d", str(tmp_path / "b_painted.glb")) == (pf2.submitted["b"], "auto")
        with patch("aigamekit_shared.vramd_client.cancel_vramd_job") as cancel:
            pf2.close()  # já reclamado: não cancela
        cancel.assert_not_called()

    def test_spec_build_error_is_recorded(self, tmp_path: Path) -> None:
        """Erro a montar o spec fica em ``errors`` em vez de morrer no Future."""
        with patch("gameassets.vramd_batch.paint_specs_from_items", side_effect=RuntimeError("spec rebentou")):
            pf = self._pf(tmp_path)
            pf.on_shape(UmsJobResult(asset_id="a", status="ok"))
        assert pf.prepared_mesh("a") is None and not pf.owns("a")
        assert "spec rebentou" in pf.errors["a"]


class TestBatchPrefetcherFactory:
    def _rows(self, *, gpu: str = "auto") -> list[Any]:
        return parse_manifest_rows([{"id": "a", "idea": "a hut", "pipeline": ["3d", "paint"], "gpu": gpu}])

    def _make(
        self,
        tmp_path: Path,
        topo: Topology | None,
        *,
        topo_by_remote: Callable[[bool | None], Topology | None] | None = None,
        **over: Any,
    ) -> PaintPrefetcher:
        from gameassets.batch_cmd import _make_paint_prefetcher

        profile = SimpleNamespace(paint3d=SimpleNamespace(style=over.pop("style", "hunyuan")))
        kw: dict[str, Any] = dict(
            profile=profile,
            rows=self._rows(),
            manifest_dir=tmp_path,
            text3d_bin="text3d",
            child_env={},
            force=False,
            no_vramd=False,
            vramd_stream=False,
            gpu_ids=None,
            skip_if_lod0=False,
        )
        kw.update(over)
        fetch = topo_by_remote or (lambda _remote: topo)
        with (
            patch("gameassets.vramd_routing.fetch_topology", side_effect=lambda *, remote=None: fetch(remote)),
            patch("gameassets.batch_cmd._paint_wave_kwargs", return_value={"manifest_dir": tmp_path}),
        ):
            return _make_paint_prefetcher(**kw)

    def test_enabled_only_with_parallel_gpus_and_hunyuan_paint(self, tmp_path: Path) -> None:
        assert self._make(tmp_path, TWO_SIZES).enabled
        assert not self._make(tmp_path, ONE_GPU).enabled  # uma GPU: paint e shape trocariam de modelo
        assert not self._make(tmp_path, None).enabled
        assert not self._make(tmp_path, TWO_SIZES, no_vramd=True).enabled
        assert not self._make(tmp_path, TWO_SIZES, style="solid").enabled
        assert not self._make(tmp_path, TWO_SIZES, text3d_bin=None).enabled

    def test_enabled_uses_the_rows_gpu_target_not_the_global_one(self, tmp_path: Path) -> None:
        """Rows ``gpu: local`` numa GPU só desligam o prefetch mesmo com servidor remoto multi-GPU."""

        def by_remote(remote: bool | None) -> Topology:
            return ONE_GPU if remote is False else TWO_SIZES

        assert not self._make(tmp_path, None, topo_by_remote=by_remote, rows=self._rows(gpu="local")).enabled
        assert self._make(tmp_path, None, topo_by_remote=by_remote, rows=self._rows(gpu="remote")).enabled
        assert self._make(tmp_path, None, topo_by_remote=by_remote, rows=self._rows(gpu="auto")).enabled


def test_close_prefetchers_closes_all_even_if_one_fails() -> None:
    """O ``finally`` do batch não pode parar no primeiro close com erro."""
    from gameassets.batch_cmd import _close_prefetchers

    class Boom:
        def close(self) -> None:
            raise RuntimeError("close rebentou")

    class Ok:
        closed = False

        def close(self) -> None:
            self.closed = True

    ok = Ok()
    _close_prefetchers([Boom(), ok])  # type: ignore[list-item]
    assert ok.closed
