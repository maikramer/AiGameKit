"""Auto-recuperação da wave de paint: respawn do worker + degradação to_paint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from gameassets.vramd_batch import make_paint_degrader, run_paint_wave_or_fallback
from gameassets.vramd_coord import UmsJobResult


def _res(aid: str, status: str) -> UmsJobResult:
    return UmsJobResult(asset_id=aid, status=status, output=f"/tmp/{aid}.glb" if status == "ok" else None)


ITEMS = [
    {"id": "casa", "mesh": "/m/casa_to_paint.glb", "image": "/i/casa.png", "output": "/o/casa_painted.glb"},
    {"id": "balde", "mesh": "/m/balde_clean.glb", "image": "/i/balde.png", "output": "/o/balde_painted.glb"},
]


def _run(waves: list[list[UmsJobResult]]):
    """Corre a wave com run_gpu_wave a devolver `waves` por ordem de chamada."""
    calls: list[list] = []

    def fake_wave(_backend, specs, **_kw):
        calls.append(list(specs))
        return waves.pop(0) if waves else []

    def fake_specs(items, **_kw):
        return [MagicMock(asset_id=it["id"], payload={}, output=it["output"]) for it in items]

    progress: list[UmsJobResult] = []
    with (
        patch("gameassets.vramd_batch.run_gpu_wave", side_effect=fake_wave),
        patch("gameassets.vramd_coord.run_gpu_wave", side_effect=fake_wave),
        patch("gameassets.vramd_batch.paint_specs_from_items", side_effect=fake_specs),
        patch("gameassets.vramd_batch._respawn_paint_worker") as respawn,
    ):
        out = run_paint_wave_or_fallback(
            ITEMS,
            manifest_dir=Path("/m"),
            no_vramd=False,
            on_progress=progress.append,
        )
    return out, calls, respawn, progress


class TestRound1WorkerReset:
    def test_failed_items_retry_on_fresh_worker(self) -> None:
        # 1ª wave: balde ok, casa erro (abort colateral). 2ª wave (retry): casa ok.
        out, calls, respawn, _p = _run([[_res("casa", "error"), _res("balde", "ok")], [_res("casa", "ok")]])
        assert out is not None
        by_id = {r["id"]: r for r in out}
        assert by_id["casa"]["status"] == "ok"
        assert by_id["balde"]["status"] == "ok"
        assert len(calls) == 2
        assert [s.asset_id for s in calls[1]] == ["casa"]  # só o falhado recorre
        respawn.assert_called_once()

    def test_all_ok_skips_recovery(self) -> None:
        out, calls, respawn, _p = _run([[_res("casa", "ok"), _res("balde", "ok")]])
        assert out is not None and all(r["status"] == "ok" for r in out)
        assert len(calls) == 1
        respawn.assert_not_called()


class TestRound2Degrade:
    def test_stubborn_item_degrades_to_paint(self) -> None:
        reprep_calls: list[str] = []

        def reprep(item):
            reprep_calls.append(item["id"])
            return {**item, "mesh": "/m/casa_to_paint.glb.degraded"}

        def fake_wave(_backend, specs, **_kw):
            ids = [s.asset_id for s in specs]
            if len(ids) == 2:  # wave inicial
                return [_res("casa", "error"), _res("balde", "ok")]
            if any(s.payload.get("degraded") for s in specs) or len(reprep_calls) > 0:  # ronda 2
                return [_res("casa", "ok")]
            return [_res("casa", "error")]  # ronda 1 (worker novo ainda falha)

        def fake_specs(items, **_kw):
            return [
                MagicMock(asset_id=it["id"], payload={"degraded": ".degraded" in it["mesh"]}, output=it["output"])
                for it in items
            ]

        with (
            patch("gameassets.vramd_batch.run_gpu_wave", side_effect=fake_wave),
            patch("gameassets.vramd_coord.run_gpu_wave", side_effect=fake_wave),
            patch("gameassets.vramd_batch.paint_specs_from_items", side_effect=fake_specs),
            patch("gameassets.vramd_batch._respawn_paint_worker"),
        ):
            out = run_paint_wave_or_fallback(ITEMS, manifest_dir=Path("/m"), no_vramd=False, reprep_item=reprep)
        assert out is not None
        assert {r["id"]: r["status"] for r in out}["casa"] == "ok"
        assert reprep_calls == ["casa"]

    def test_reprep_failure_keeps_error(self) -> None:
        def reprep(_item):
            return None  # degrader não conseguiu regenerar

        out = _run_with_reprep(reprep)
        assert out is not None
        casa = {r["id"]: r for r in out}["casa"]
        assert casa["status"] == "error"


def _run_with_reprep(reprep):
    def fake_wave(_backend, specs, **_kw):
        ids = [s.asset_id for s in specs]
        if len(ids) == 2:
            return [_res("casa", "error"), _res("balde", "ok")]
        return [_res("casa", "error")]

    def fake_specs(items, **_kw):
        return [MagicMock(asset_id=it["id"], payload={}, output=it["output"]) for it in items]

    with (
        patch("gameassets.vramd_batch.run_gpu_wave", side_effect=fake_wave),
        patch("gameassets.vramd_coord.run_gpu_wave", side_effect=fake_wave),
        patch("gameassets.vramd_batch.paint_specs_from_items", side_effect=fake_specs),
        patch("gameassets.vramd_batch._respawn_paint_worker"),
    ):
        return run_paint_wave_or_fallback(ITEMS, manifest_dir=Path("/m"), no_vramd=False, reprep_item=reprep)


class TestMakePaintDegrader:
    def test_regenerates_at_factor_of_budget(self, tmp_path: Path) -> None:
        mesh_final = tmp_path / "meshes" / "casa.glb"
        item = {"id": "casa", "mesh": "/m/casa_to_paint.glb", "image": "i", "output": "o"}
        profile = MagicMock()
        row = MagicMock()

        def fake_resolve(prof, r=None) -> int:
            return 320_000

        def fake_ensure(mesh_final, **kw) -> Path:
            assert kw["target_faces"] == 192_000  # 0.6 x 320k
            assert kw["force"] is True
            return tmp_path / "degraded.glb"

        with (
            patch("gameassets.pipeline._resolve_to_paint_faces", side_effect=fake_resolve),
            patch("gameassets.pipeline.ensure_to_paint_for_paint", side_effect=fake_ensure),
        ):
            reprep = make_paint_degrader(
                text3d_bin="text3d",
                profile=profile,
                child_env={},
                manifest_dir=tmp_path,
                mesh_final_by_id={"casa": mesh_final},
                row_by_id={"casa": row},
            )
            new_item = reprep(item)
        assert new_item is not None
        assert new_item["mesh"] == str(tmp_path / "degraded.glb")

    def test_unknown_id_returns_none(self, tmp_path: Path) -> None:
        reprep = make_paint_degrader(
            text3d_bin="text3d",
            profile=MagicMock(),
            child_env={},
            manifest_dir=tmp_path,
            mesh_final_by_id={},
        )
        assert reprep({"id": "fantasma"}) is None
