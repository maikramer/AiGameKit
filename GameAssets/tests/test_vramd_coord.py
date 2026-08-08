"""Testes vramd_coord / vramd_batch (Wave A+B)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gameassets.vramd_batch import (
    paint_specs_from_items,
    results_as_batch_jsonl,
    run_paint_wave_or_fallback,
    run_shape_wave_or_fallback,
    shape_specs_from_items,
)
from gameassets.vramd_coord import (
    FALLBACK_SUBPROCESS,
    VRAMD_CHILD_ENV_KEYS,
    MasterDeferQueue,
    UmsJobResult,
    UmsJobSpec,
    apply_vramd_child_env,
    preload_backend,
    run_gpu_wave,
)


class TestApplyUmsChildEnv:
    def test_sets_batch_priority(self) -> None:
        env: dict[str, str] = {}
        apply_vramd_child_env(env, parent_environ={})
        assert env["VRAMD_PRIORITY"] == "batch"

    def test_inherits_affinity_and_inflight(self) -> None:
        parent = {
            "VRAMD_MAX_AFFINITY_CUTS": "5",
            "VRAMD_MAX_INFLIGHT": "2",
            "VRAMD_STARVATION_TIMEOUT_SEC": "120",
        }
        env: dict[str, str] = {}
        apply_vramd_child_env(env, parent_environ=parent)
        assert env["VRAMD_MAX_AFFINITY_CUTS"] == "5"
        assert env["VRAMD_MAX_INFLIGHT"] == "2"
        assert env["VRAMD_STARVATION_TIMEOUT_SEC"] == "120"

    def test_vramd_stream_and_no_vramd(self) -> None:
        env: dict[str, str] = {}
        apply_vramd_child_env(env, vramd_stream=True, no_vramd=True, parent_environ={})
        assert env["VRAMD_STREAM"] == "1"
        assert env["VRAMD_AUTO_START"] == "0"

    def test_keys_tuple_complete(self) -> None:
        assert "VRAMD_MAX_AFFINITY_CUTS" in VRAMD_CHILD_ENV_KEYS
        assert "VRAMD_MAX_INFLIGHT" in VRAMD_CHILD_ENV_KEYS


class TestMasterDeferQueue:
    def test_drain_order(self) -> None:
        q = MasterDeferQueue()
        seen: list[str] = []
        q.enqueue({"id": "a"}, Path("/meshes/a.glb"), "row_a")
        q.enqueue({"id": "b"}, Path("/meshes/b.glb"), "row_b")

        def fin(rec: dict[str, Any], mesh: Path, row: Any) -> None:
            seen.append(f"{rec['id']}:{mesh.name}:{row}")

        n = q.drain(fin)
        assert n == 2
        assert seen == ["a:a.glb:row_a", "b:b.glb:row_b"]
        assert q.items == []

    def test_enqueue_canonicalizes_painted_intermediate(self) -> None:
        q = MasterDeferQueue()
        painted = Path("/meshes/_intermediate/cactus_painted.glb")
        q.enqueue({"id": "cactus"}, painted, "row")
        assert q.items[0].mesh_final == Path("/meshes/cactus.glb")


class TestPreloadBackend:
    def test_preload_sends_cmd(self) -> None:
        with (
            patch("aigamekit_shared.vramd_client.ensure_vramd_running", return_value=True),
            patch("aigamekit_shared.vramd_client.send_to_vramd") as send,
        ):
            send.return_value = {"status": "ok"}
            out = preload_backend("text3d", load_opts={"gpu_ids": [0]})
            assert out == {"status": "ok"}
            req = send.call_args[0][0]
            assert req["cmd"] == "preload"
            assert req["backend"] == "text3d"
            assert req["gpu_ids"] == [0]

    def test_preload_none_if_ums_down(self) -> None:
        with patch("aigamekit_shared.vramd_client.ensure_vramd_running", return_value=False):
            assert preload_backend("paint3d") is None


class TestRunGpuWave:
    def test_no_vramd_returns_fallback(self) -> None:
        specs = [UmsJobSpec(asset_id="x", payload={"output": "/tmp/x.glb"})]
        assert run_gpu_wave("text3d", specs, no_vramd=True) is FALLBACK_SUBPROCESS

    def test_empty_items(self) -> None:
        assert run_gpu_wave("text3d", [], no_vramd=False) == []

    def test_submit_wait_ok(self, tmp_path: Path) -> None:
        out = tmp_path / "a.glb"
        specs = [UmsJobSpec(asset_id="a", payload={"output": str(out), "from_image": "i.png"}, output=str(out))]
        with (
            patch("aigamekit_shared.vramd_client.ensure_vramd_running", return_value=True),
            patch("gameassets.vramd_coord.preload_backend", return_value={"status": "ok"}),
            patch("aigamekit_shared.vramd_client.submit_to_vramd") as sub,
            patch("aigamekit_shared.vramd_client.wait_vramd_job") as wait,
        ):
            sub.return_value = {"status": "ok", "job_id": "j1"}
            wait.return_value = {"status": "ok", "output": str(out)}
            wave = run_gpu_wave("text3d", specs, preload=True)
            assert wave is not FALLBACK_SUBPROCESS
            assert isinstance(wave, list)
            assert len(wave) == 1
            assert wave[0].status == "ok"
            assert wave[0].asset_id == "a"
            sub.assert_called_once()
            wait.assert_called_once_with("j1", timeout_sec=1800.0, stream=False)

    def test_skip_existing_output(self, tmp_path: Path) -> None:
        out = tmp_path / "exists.glb"
        out.write_bytes(b"glb")
        specs = [UmsJobSpec(asset_id="e", payload={"output": str(out)}, output=str(out))]
        with (
            patch("aigamekit_shared.vramd_client.ensure_vramd_running", return_value=True),
            patch("gameassets.vramd_coord.preload_backend", return_value=None),
            patch("aigamekit_shared.vramd_client.submit_to_vramd") as sub,
        ):
            wave = run_gpu_wave("text3d", specs, preload=False)
            assert isinstance(wave, list)
            assert wave[0].status == "skipped"
            sub.assert_not_called()

    def test_submit_none_fallback_cancels(self) -> None:
        specs = [
            UmsJobSpec(asset_id="a", payload={"output": "/no/a.glb"}),
            UmsJobSpec(asset_id="b", payload={"output": "/no/b.glb"}),
        ]
        with (
            patch("aigamekit_shared.vramd_client.ensure_vramd_running", return_value=True),
            patch("gameassets.vramd_coord.preload_backend", return_value=None),
            patch("aigamekit_shared.vramd_client.submit_to_vramd") as sub,
            patch("aigamekit_shared.vramd_client.cancel_vramd_job") as cancel,
        ):
            sub.side_effect = [{"status": "ok", "job_id": "j1"}, None]
            assert run_gpu_wave("text3d", specs, preload=False) is FALLBACK_SUBPROCESS
            cancel.assert_called_once_with("j1")

    def test_partial_error_continues(self) -> None:
        specs = [
            UmsJobSpec(asset_id="a", payload={"output": "/no/a.glb"}),
            UmsJobSpec(asset_id="b", payload={"output": "/no/b.glb"}),
        ]
        with (
            patch("aigamekit_shared.vramd_client.ensure_vramd_running", return_value=True),
            patch("gameassets.vramd_coord.preload_backend", return_value=None),
            patch("aigamekit_shared.vramd_client.submit_to_vramd") as sub,
            patch("aigamekit_shared.vramd_client.wait_vramd_job") as wait,
        ):
            sub.side_effect = [
                {"status": "ok", "job_id": "j1"},
                {"status": "ok", "job_id": "j2"},
            ]
            wait.side_effect = [
                {"status": "error", "error": "boom"},
                {"status": "ok", "output": "/no/b.glb"},
            ]
            wave = run_gpu_wave("text3d", specs, preload=False)
            assert isinstance(wave, list)
            assert wave[0].status == "error"
            assert wave[1].status == "ok"


class TestResultsAsBatchJsonl:
    def test_format(self) -> None:
        r = UmsJobResult(asset_id="x", status="ok", output="/o.glb", seconds=1.5, raw={"faces": 10})
        js = results_as_batch_jsonl([r])
        assert js[0]["id"] == "x"
        assert js[0]["faces"] == 10


class TestShapePaintSpecs:
    def test_shape_specs_import_or_empty(self, tmp_path: Path) -> None:
        items = [{"id": "h", "image": "i.png", "output": "o.glb"}]
        try:
            import text3d.vramd_payload  # noqa: F401
        except ImportError:
            assert shape_specs_from_items(items, manifest_dir=tmp_path) == []
            return
        specs = shape_specs_from_items(items, manifest_dir=tmp_path, steps=40)
        assert len(specs) == 1
        assert specs[0].asset_id == "h"
        assert "from_image" in specs[0].payload
        assert specs[0].payload["steps"] == 40

    def test_paint_specs_import_or_empty(self, tmp_path: Path) -> None:
        items = [{"id": "p", "mesh": "m.glb", "image": "i.png", "output": "o.glb"}]
        try:
            import paint3d.vramd_payload  # noqa: F401
        except ImportError:
            assert paint_specs_from_items(items, manifest_dir=tmp_path) == []
            return
        specs = paint_specs_from_items(items, manifest_dir=tmp_path, max_views=4)
        assert len(specs) == 1
        assert specs[0].payload["max_num_view"] == 4


class TestWaveOrFallback:
    def test_no_vramd_shape(self) -> None:
        assert run_shape_wave_or_fallback([{"id": "a"}], manifest_dir=Path("."), no_vramd=True) is None

    def test_no_vramd_paint(self) -> None:
        assert run_paint_wave_or_fallback([{"id": "a"}], manifest_dir=Path("."), no_vramd=True) is None

    def test_shape_wave_mocked(self, tmp_path: Path) -> None:
        items = [{"id": "a", "image": "i.png", "output": "o.glb"}]
        fake_specs = [UmsJobSpec(asset_id="a", payload={"output": str(tmp_path / "o.glb")})]
        fake_results = [UmsJobResult(asset_id="a", status="ok", output=str(tmp_path / "o.glb"), seconds=2.0)]
        with (
            patch("gameassets.vramd_batch.shape_specs_from_items", return_value=fake_specs),
            patch("gameassets.vramd_batch.run_gpu_wave", return_value=fake_results),
        ):
            out = run_shape_wave_or_fallback(items, manifest_dir=tmp_path, no_vramd=False)
            assert out is not None
            assert out[0]["status"] == "ok"
            assert out[0]["id"] == "a"

    def test_shape_wave_fallback_sentinel(self, tmp_path: Path) -> None:
        items = [{"id": "a", "image": "i.png", "output": "o.glb"}]
        with (
            patch("gameassets.vramd_batch.shape_specs_from_items", return_value=[MagicMock()]),
            patch("gameassets.vramd_batch.run_gpu_wave", return_value=FALLBACK_SUBPROCESS),
        ):
            assert run_shape_wave_or_fallback(items, manifest_dir=tmp_path, no_vramd=False) is None


@pytest.mark.parametrize(
    "backend",
    ["text3d", "paint3d", "text2d", "text2icon", "texture2d", "skymap2d", "text2sound", "terrain3d"],
)
def test_payload_builders_smoke(backend: str) -> None:
    if backend == "text3d":
        pytest.importorskip("text3d.vramd_payload")
        from text3d.vramd_payload import build_generate_request

        p = build_generate_request(from_image="/i.png", output="/o.glb", steps=10)
        assert p["from_image"] == "/i.png"
        assert "sdnq_preset" in p or "memory_efficient" in p
    elif backend == "paint3d":
        pytest.importorskip("paint3d.vramd_payload")
        from paint3d.vramd_payload import build_texture_request

        p = build_texture_request(mesh_path="/m.glb", image_path="/i.png", output="/o.glb")
        assert p["mesh_path"] == "/m.glb"
    elif backend == "text2d":
        pytest.importorskip("text2d.vramd_payload")
        from text2d.vramd_payload import build_generate_request

        p = build_generate_request(prompt="x", output="/o.png", memory_efficient=True, quant_preset="sdnq-uint8")
        assert p["prompt"] == "x"
        assert p.get("memory_efficient") is True
    elif backend == "text2icon":
        pytest.importorskip("text2icon.vramd_payload")
        from text2icon.vramd_payload import build_generate_request

        p = build_generate_request(prompt="icon", output="/o.png")
        assert p["prompt"] == "icon"
    elif backend == "texture2d":
        pytest.importorskip("texture2d.vramd_payload")
        from texture2d.vramd_payload import build_generate_request

        p = build_generate_request(prompt="brick", output="/o.png")
        assert p["prompt"] == "brick"
    elif backend == "skymap2d":
        pytest.importorskip("skymap2d.vramd_payload")
        from skymap2d.vramd_payload import build_generate_request

        p = build_generate_request(prompt="sky", output="/o.png", memory_efficient=True)
        assert p["prompt"] == "sky"
    elif backend == "text2sound":
        pytest.importorskip("text2sound.vramd_payload")
        from text2sound.vramd_payload import build_generate_request

        p = build_generate_request(prompt="sfx", output="/o.wav", half_precision=True)
        assert p["prompt"] == "sfx"
    else:
        pytest.importorskip("terrain3d.vramd_payload")
        from terrain3d.vramd_payload import build_generate_request

        p = build_generate_request(output="/h.png", prompt="hills", seed=1)
        assert p["output"] == "/h.png"
