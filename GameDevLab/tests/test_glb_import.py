"""Tests for gamedev_lab.glb_import — decode-aware GLB import wiring."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from gamedev_lab.glb_import import import_glb


class TestImportGlb:
    def test_imports_decoded_path(self, tmp_path):
        src = tmp_path / "asset.glb"
        src.write_bytes(b"raw")
        decoded = tmp_path / "decoded.glb"
        decoded.write_bytes(b"decoded")

        seen: list[Path] = []

        @contextmanager
        def fake_readable(path):
            seen.append(Path(path))
            yield decoded

        fake_bpy = MagicMock()
        with (
            patch.dict(sys.modules, {"bpy": fake_bpy}),
            patch("gamedev_shared.gltf_decode.bpy_readable_glb", fake_readable),
        ):
            import_glb(src)

        # Decode acontece uma única vez, no ``import_gltf`` do Shared.
        assert seen == [src.resolve()]
        fake_bpy.ops.import_scene.gltf.assert_called_once_with(filepath=str(decoded), bone_heuristic="TEMPERANCE")

    def test_renderer_and_debug_tools_use_import_glb(self):
        """Regression guard: no direct import_scene.gltf calls left in tool modules."""
        import inspect

        import gamedev_lab.cut_review as cut_review
        import gamedev_lab.debug_tools as debug_tools
        import gamedev_lab.renderer as renderer

        for mod in (renderer, debug_tools, cut_review):
            source = inspect.getsource(mod)
            assert "bpy.ops.import_scene.gltf" not in source, mod.__name__
            assert "import_glb" in source, mod.__name__
