"""Testes thin do orquestrador vramd_generate (sem GPU)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from text3d.vramd_generate import run_generate


class TestRunGenerateValidation:
    def test_requires_output(self) -> None:
        out = run_generate(MagicMock(), {"prompt": "x"})
        assert out["status"] == "error"
        assert "output" in out["error"]

    def test_requires_prompt_or_image(self) -> None:
        out = run_generate(MagicMock(), {"output": "/tmp/x.glb"})
        assert out["status"] == "error"

    def test_abort_before_generate(self) -> None:
        out = run_generate(
            MagicMock(),
            {"output": "/tmp/x.glb", "prompt": "x"},
            should_abort=lambda: True,
            cancelled_response=lambda r: {"status": "cancelled", "error": r},
        )
        assert out["status"] == "cancelled"


class TestRunGenerateHappyPath:
    def test_text_path_saves(self, tmp_path) -> None:
        mesh = MagicMock()
        mesh.extents = [1.0, 2.0, 1.0]
        mesh.apply_scale = MagicMock()

        model = MagicMock()
        model.generate.return_value = mesh
        model.last_decode_stats = {"num_chunks": 12000, "auto_num_chunks": True}

        out_glb = tmp_path / "out.glb"
        with (
            patch("text3d.utils.export.save_mesh", return_value=out_glb),
            patch("text3d.utils.mesh_lod.prepare_mesh_topology", side_effect=lambda m, **_k: m),
            patch("text3d.omni_presets.write_omni_fingerprint"),
            patch(
                "text3d.omni_presets.merge_omni_controls",
                return_value={
                    "control_type": None,
                    "bbox": None,
                    "pose_file": None,
                    "point_cloud": None,
                    "voxel_mesh": None,
                    "pose_preset": None,
                    "bbox_preset": None,
                },
            ),
        ):
            out = run_generate(
                model,
                {
                    "prompt": "hero",
                    "output": str(out_glb),
                    "steps": 20,
                    "octree_resolution": 256,
                    "num_chunks": 10000,
                    "topology_fix": False,
                    "bbox_tune": False,
                },
            )
        assert out["status"] == "ok"
        assert out["runtime_budget"]["num_chunks"] == 12000
        model.generate.assert_called_once()
