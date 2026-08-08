"""Rota geométrica LOD: meshopt-first, piso de costuras aceite, weld off em skins."""

from __future__ import annotations

import json
import struct
from pathlib import Path


def _synthetic_glb(path: Path, *, tri_count: int, skinned: bool = False) -> Path:
    doc: dict = {
        "asset": {"version": "2.0"},
        "accessors": [
            {"componentType": 5125, "count": tri_count * 3, "type": "SCALAR"},
            {"componentType": 5126, "count": tri_count * 3, "type": "VEC3"},
        ],
        "meshes": [{"primitives": [{"indices": 0, "attributes": {"POSITION": 1}}]}],
    }
    if skinned:
        doc["skins"] = [{"joints": [0]}]
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(raw))
    path.write_bytes(header + struct.pack("<II", len(raw), 0x4E4F534A) + raw)
    return path


class TestMeshoptSimplifyLevel:
    def test_copies_when_already_below_target(self, tmp_path: Path) -> None:
        from text3d.utils.mesh_lod import _meshopt_simplify_level

        src = _synthetic_glb(tmp_path / "src.glb", tri_count=100)
        dst = tmp_path / "out.glb"
        faces = _meshopt_simplify_level(src, dst, 500, weld=True)
        assert faces == 100
        assert dst.is_file()

    def test_accepts_seam_floor_above_target(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import gltf_finish, mesh_lod

        src = _synthetic_glb(tmp_path / "src.glb", tri_count=100_000)
        dst = tmp_path / "out.glb"

        def floored(s, d, *, target_faces, weld=True):
            Path(d).write_bytes(b"x")
            return True, 29_000, ""

        monkeypatch.setattr(gltf_finish, "meshopt_simplify_glb", floored)
        faces = mesh_lod._meshopt_simplify_level(src, dst, 6_000, weld=False)
        assert faces == 29_000
        assert dst.is_file()

    def test_returns_none_when_cli_missing(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import gltf_finish, mesh_lod

        src = _synthetic_glb(tmp_path / "src.glb", tri_count=100_000)
        monkeypatch.setattr(
            gltf_finish,
            "meshopt_simplify_glb",
            lambda s, d, *, target_faces, weld=True: (False, -1, "npx ausente"),
        )
        assert mesh_lod._meshopt_simplify_level(src, tmp_path / "o.glb", 1000, weld=True) is None


class TestGeometricLadderRouting:
    def test_skinned_disables_weld(self) -> None:
        import inspect

        from text3d.utils import mesh_lod

        src = inspect.getsource(mesh_lod._generate_lod_glb_triplet_impl)
        assert "weld = not skinned" in src
        assert "_glb_has_skins" in src

    def test_falls_back_to_collapse_when_meshopt_unavailable(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import mesh_lod

        src = _synthetic_glb(tmp_path / "src.glb", tri_count=50_000, skinned=True)
        called: list[str] = []

        monkeypatch.setattr(mesh_lod, "_meshopt_simplify_level", lambda *a, **k: None)

        def fake_collapse(*args, **kwargs):
            called.append("collapse")
            out = Path(args[1])
            paths = [out / f"{args[2]}_lod{i}.glb" for i in range(3)]
            for p in paths:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"glb")
            return paths

        monkeypatch.setattr(mesh_lod, "_generate_lod_glb_triplet_bpy_collapse", fake_collapse)
        paths = mesh_lod._generate_lod_glb_triplet_impl(
            src,
            tmp_path / "lod",
            "hero",
            0.4,
            0.22,
            500,
            150,
            False,
            None,
            8000,
        )
        assert called == ["collapse"]
        assert len(paths) == 3

    def test_reuses_previous_level_on_same_seam_floor(self, tmp_path: Path, monkeypatch) -> None:
        """boss_ogre: lod0/1/2 no mesmo piso → copiar, não COLLAPSE abaixo."""
        from text3d.utils import mesh_lod

        src = _synthetic_glb(tmp_path / "src.glb", tri_count=175_000, skinned=True)
        out_dir = tmp_path / "lod"
        floor_faces = 29_000

        def fake_simplify(s, d, target_faces, *, weld):
            Path(d).write_bytes(b"raw")
            return floor_faces

        finalize_calls: list[tuple[str, int | None]] = []

        def fake_finalize(s, d, *, texture_size, meshfix):
            d = Path(d)
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_bytes(b"final")
            finalize_calls.append((d.name, texture_size))

        monkeypatch.setattr(mesh_lod, "_meshopt_simplify_level", fake_simplify)
        monkeypatch.setattr(mesh_lod, "_finalize_geometric_lod", fake_finalize)
        # Import local dentro do impl — patch no módulo fonte.
        monkeypatch.setattr("text3d.utils.gltf_finish.glb_face_count", lambda p: floor_faces)

        paths = mesh_lod._generate_lod_glb_triplet_impl(
            src,
            out_dir,
            "boss_ogre",
            0.4,
            0.22,
            500,
            150,
            False,
            None,
            7000,
        )
        assert [p.name for p in paths] == [
            "boss_ogre_lod0.glb",
            "boss_ogre_lod1.glb",
            "boss_ogre_lod2.glb",
        ]
        assert all(p.is_file() for p in paths)
        assert len(finalize_calls) == 3

    def test_docstring_promises_accept_seam_floor(self) -> None:
        from text3d.utils.mesh_lod import generate_lod_glb_triplet

        assert "piso" in (generate_lod_glb_triplet.__doc__ or "").lower()
        assert "meshoptimizer" in (generate_lod_glb_triplet.__doc__ or "").lower()


class TestCollapseFallbackIntact:
    def test_collapse_helper_still_exported_for_fallback(self) -> None:
        from text3d.utils import mesh_lod

        assert callable(mesh_lod._generate_lod_glb_triplet_bpy_collapse)
        assert callable(mesh_lod._meshopt_simplify_level)


class TestTextureByteMonotonicity:
    def test_downscale_packs_jpeg_not_png(self) -> None:
        """PNG no downscale fazia lod1 (atlas novo) > lod0 (JPEG intacto)."""
        import inspect

        from text3d.utils.mesh_remesh_textured import downscale_image_replace

        src = inspect.getsource(downscale_image_replace)
        assert 'file_format = "JPEG"' in src
        assert 'file_format = "PNG"' not in src

    def test_geometric_export_forces_jpeg(self) -> None:
        import inspect

        from text3d.utils import mesh_lod

        src = inspect.getsource(mesh_lod._export_textured_glb)
        assert 'export_image_format="JPEG"' in src
