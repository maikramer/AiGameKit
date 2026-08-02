"""Tests for the no-bpy ``precompute`` collider estimator.

Covers the three fit paths (stump / trunk-slice / AABB), the world-space
vertex decode (node transforms + KHR_mesh_quantization), the collectible
hint per category, and the soft-failure contract (``{"error": ...}``).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_glb_meta import base_gltf, build_glb

from aigamekit_lab.cli import main
from aigamekit_lab.glb_meta import glb_extract_meta
from aigamekit_lab.precompute import precompute_asset


def positions_bin(verts: list[tuple[float, float, float]]) -> bytes:
    """Bin chunk com os vértices float32 empacotados."""
    return struct.pack(f"<{len(verts) * 3}f", *[v for xyz in verts for v in xyz])


def positions_glb(verts: list[tuple[float, float, float]]) -> bytes:
    """GLB float32 com os vértices dados (espaço local do mesh)."""
    bin_data = positions_bin(verts)
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    gltf = base_gltf(
        accessors=[
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(verts),
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            }
        ],
        bufferViews=[{"buffer": 0, "byteOffset": 0, "byteLength": len(bin_data)}],
        buffers=[{"byteLength": len(bin_data)}],
        meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
        nodes=[{"mesh": 0}],
        scenes=[{"nodes": [0]}],
    )
    return build_glb(gltf, bin_data)


def box_verts(cx: float, cy: float, cz: float, sx: float, sy: float, sz: float) -> list[tuple[float, float, float]]:
    """8 cantos de um cubo centrado em (cx, cy, cz) com extents (sx, sy, sz)."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    return [
        (cx - hx, cy - hy, cz - hz),
        (cx + hx, cy - hy, cz - hz),
        (cx + hx, cy - hy, cz + hz),
        (cx - hx, cy - hy, cz + hz),
        (cx - hx, cy + hy, cz - hz),
        (cx + hx, cy + hy, cz - hz),
        (cx + hx, cy + hy, cz + hz),
        (cx - hx, cy + hy, cz + hz),
    ]


def tree_verts() -> list[tuple[float, float, float]]:
    """Árvore sintética: tronco 0.4x2.0 (base) + copa 2.0x3.0 (topo)."""
    trunk = box_verts(0.0, 1.0, 0.0, 0.4, 2.0, 0.4)
    canopy = box_verts(0.0, 3.5, 0.0, 2.0, 3.0, 2.0)
    return trunk + canopy


def write_bytes(tmp_path: Path, data: bytes, name: str = "mesh.glb") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestGlbMetaWorldBounds:
    """glb_extract_meta devolve o AABB completo (3 eixos) em espaço mundo."""

    def test_world_bounds_three_axes(self, tmp_path: Path) -> None:
        glb = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.0, 0.0, 2.0, 3.0, 4.0)))
        meta = glb_extract_meta(glb)
        assert meta["world_bounds_min"] == pytest.approx([-1.0, -1.5, -2.0])
        assert meta["world_bounds_max"] == pytest.approx([1.0, 1.5, 2.0])

    def test_world_bounds_respect_node_transform(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 8, "min": [-1.0, 0.0, -1.0], "max": [1.0, 2.0, 1.0]}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
            nodes=[{"mesh": 0, "translation": [2.0, 0.0, -1.0]}],
            scenes=[{"nodes": [0]}],
        )
        meta = glb_extract_meta(write_bytes(tmp_path, build_glb(gltf)))
        assert meta["world_bounds_min"] == pytest.approx([1.0, 0.0, -2.0])
        assert meta["world_bounds_max"] == pytest.approx([3.0, 2.0, 0.0])

    def test_world_bounds_none_without_position(self, tmp_path: Path) -> None:
        gltf = base_gltf(meshes=[{"primitives": [{"attributes": {"NORMAL": 0}}]}])
        meta = glb_extract_meta(write_bytes(tmp_path, build_glb(gltf)))
        assert meta["world_bounds_min"] is None
        assert meta["world_bounds_max"] is None


class TestPrecomputeStump:
    """Árvores split: a cápsula vem do AABB do stump (= tronco exato)."""

    def test_capsule_from_stump_aabb(self, tmp_path: Path) -> None:
        stump = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.75, 0.0, 0.6, 1.5, 0.6)), "stump.glb")
        result = precompute_asset(stump, stump_glb=stump, category="vegetation", asset_id="pine")
        assert result["source"] == "stump"
        assert result["collider"]["shape"] == "capsule"
        assert result["collider"]["radius"] == pytest.approx(0.3)
        assert result["collider"]["height"] == pytest.approx(1.5)
        assert result["collider"]["base_y"] == pytest.approx(0.0)
        assert result["collectible_hint"]["kind"] == "wood"

    def test_stump_wide_base_radius_uses_flare_not_height(self, tmp_path: Path) -> None:
        # Stump largo e baixo (alargamento de raízes, ex. carvalho 1.86x0.6):
        # o raio NÃO é capado por dy/2 — senão o jogador entra na base.
        stump = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.3, 0.0, 1.8, 0.6, 1.5)), "stump.glb")
        result = precompute_asset(stump, stump_glb=stump, category="vegetation")
        assert result["collider"]["shape"] == "capsule"
        # max(1.8, 1.5)/2 = 0.9 — o alargamento manda, não os 0.3 do dy/2.
        assert result["collider"]["radius"] == pytest.approx(0.9)
        assert result["collider"]["height"] == pytest.approx(0.6)

    def test_stump_ignores_canopy_shape(self, tmp_path: Path) -> None:
        # O main GLB pode ser largo (copa); o stump estreito manda.
        main_glb = write_bytes(tmp_path, positions_glb(tree_verts()), "tree.glb")
        stump = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.75, 0.0, 0.6, 1.5, 0.6)), "stump.glb")
        result = precompute_asset(main_glb, stump_glb=stump, category="vegetation")
        assert result["source"] == "stump"
        assert result["collider"]["radius"] == pytest.approx(0.3)

    def test_stump_missing_glb_is_soft_error(self, tmp_path: Path) -> None:
        main_glb = write_bytes(tmp_path, positions_glb(tree_verts()))
        result = precompute_asset(main_glb, stump_glb=tmp_path / "nope.glb", category="vegetation")
        assert "error" in result


class TestPrecomputeTrunkSlice:
    """Vegetation sem stump: largura do colisor vem só do tronco."""

    def test_trunk_slice_radius_is_narrow(self, tmp_path: Path) -> None:
        glb = write_bytes(tmp_path, positions_glb(tree_verts()))
        result = precompute_asset(glb, category="vegetation")
        assert result["source"] == "trunk-slice"
        assert result["collider"]["shape"] == "capsule"
        # Copa tem 2.0 de largura → AABB ingénuo daria raio 1.0; o tronco manda.
        assert result["collider"]["radius"] == pytest.approx(0.21)
        assert result["collider"]["height"] == pytest.approx(5.0)
        assert result["collectible_hint"]["kind"] == "wood"

    def test_trunk_slice_respects_node_scale(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": len(tree_verts()),
                }
            ],
            bufferViews=[{"buffer": 0, "byteOffset": 0, "byteLength": 0}],
            buffers=[{"byteLength": 0}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
            nodes=[{"mesh": 0, "scale": [2.0, 2.0, 2.0]}],
            scenes=[{"nodes": [0]}],
        )
        glb = write_bytes(
            tmp_path,
            build_glb(gltf, positions_bin(tree_verts())),
            "scaled.glb",
        )
        result = precompute_asset(glb, category="vegetation")
        assert result["collider"]["radius"] == pytest.approx(0.42)
        assert result["collider"]["height"] == pytest.approx(10.0)

    def test_degenerate_slice_falls_back_to_aabb(self, tmp_path: Path) -> None:
        # 2 vértices (linha): fatia com < 3 vértices → fallback AABB.
        glb = write_bytes(tmp_path, positions_glb([(0.0, 0.0, 0.0), (0.0, 4.0, 0.0)]))
        result = precompute_asset(glb, category="vegetation")
        assert result["source"] == "aabb"
        assert result["collider"]["shape"] == "capsule"
        assert result["collider"]["height"] == pytest.approx(4.0)

    def test_quantized_uint16_positions_decode(self, tmp_path: Path) -> None:
        # KHR_mesh_quantization uint16 normalizado: raw 32767 → 0.5.
        raw_vals = [
            0,
            0,
            0,
            32767,
            0,
            0,
            0,
            32767,
            0,
            0,
            0,
            32767,
        ]
        bin_data = struct.pack(f"<{len(raw_vals)}H", *raw_vals)
        gltf = base_gltf(
            accessors=[
                {
                    "bufferView": 0,
                    "componentType": 5123,
                    "normalized": True,
                    "count": 4,
                }
            ],
            bufferViews=[{"buffer": 0, "byteOffset": 0, "byteLength": len(bin_data)}],
            buffers=[{"byteLength": len(bin_data)}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
            nodes=[{"mesh": 0}],
            scenes=[{"nodes": [0]}],
            extensionsUsed=["KHR_mesh_quantization"],
            extensionsRequired=["KHR_mesh_quantization"],
        )
        glb = write_bytes(tmp_path, build_glb(gltf, bin_data), "quant.glb")
        result = precompute_asset(glb, category="rock")
        assert result["collider"]["height"] == pytest.approx(0.5)
        assert result["collider"]["radius"] == pytest.approx(0.25)


class TestPrecomputeAabb:
    """Rock → cilindro do AABB; outros → cápsula do AABB."""

    def test_rock_cylinder(self, tmp_path: Path) -> None:
        glb = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.6, 0.0, 2.0, 1.2, 1.6)))
        result = precompute_asset(glb, category="rock")
        assert result["source"] == "aabb"
        assert result["collider"]["shape"] == "cylinder"
        # max(2.0, 1.6)/2 = 1.0 — círculo que contém o footprint (sem cap dy/2)
        assert result["collider"]["radius"] == pytest.approx(1.0)
        assert result["collider"]["height"] == pytest.approx(1.2)
        assert result["collectible_hint"]["kind"] == "stone"

    def test_prop_capsule_no_hint(self, tmp_path: Path) -> None:
        glb = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.5, 0.0, 1.0, 1.0, 1.0)))
        result = precompute_asset(glb, category="prop")
        assert result["collider"]["shape"] == "capsule"
        assert result["collider"]["radius"] == pytest.approx(0.5)
        assert result["collider"]["height"] == pytest.approx(1.0)
        assert result["collectible_hint"]["kind"] is None

    def test_terrain_category_is_cylinder_with_stone_hint(self, tmp_path: Path) -> None:
        # As pedras do simple-rpg usam category: terrain — mesmo tratamento do rock.
        glb = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.5, 0.0, 2.0, 1.0, 1.6)))
        result = precompute_asset(glb, category="terrain")
        assert result["collider"]["shape"] == "cylinder"
        assert result["collider"]["radius"] == pytest.approx(1.0)
        assert result["collectible_hint"]["kind"] == "stone"

    def test_base_y_offsets_above_origin(self, tmp_path: Path) -> None:
        glb = write_bytes(tmp_path, positions_glb(box_verts(0.0, 2.0, 0.0, 1.0, 1.0, 1.0)))
        result = precompute_asset(glb, category="rock")
        assert result["collider"]["base_y"] == pytest.approx(1.5)
        assert result["aabb"]["min"][1] == pytest.approx(1.5)


class TestPrecomputeSoftFailures:
    """Contrato de falha soft: dict com ``error``, sem exceção."""

    def test_missing_file(self, tmp_path: Path) -> None:
        result = precompute_asset(tmp_path / "missing.glb", category="rock")
        assert "error" in result

    def test_not_a_glb(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.glb"
        bad.write_bytes(b"not a glb at all")
        result = precompute_asset(bad, category="rock")
        assert "error" in result

    def test_glb_without_position(self, tmp_path: Path) -> None:
        gltf = base_gltf(meshes=[{"primitives": [{"attributes": {}}]}])
        glb = write_bytes(tmp_path, build_glb(gltf))
        result = precompute_asset(glb, category="rock")
        assert "error" in result

    def test_empty_position_buffer(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"bufferView": 0, "componentType": 5126, "count": 0}],
            bufferViews=[{"buffer": 0, "byteOffset": 0, "byteLength": 0}],
            buffers=[{"byteLength": 0}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
        )
        glb = write_bytes(tmp_path, build_glb(gltf))
        result = precompute_asset(glb, category="rock")
        assert "error" in result


class TestPrecomputeCli:
    """Comando ``aigamekit-lab precompute``: saída JSON + exit 0."""

    def test_cli_writes_json_file(self, tmp_path: Path) -> None:
        glb = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.5, 0.0, 1.0, 1.0, 1.0)), "rock.glb")
        out = tmp_path / "pre.json"
        r = CliRunner().invoke(
            main,
            ["precompute", str(glb), "-o", str(out), "--category", "rock", "--asset-id", "rock_mossy"],
        )
        assert r.exit_code == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["asset_id"] == "rock_mossy"
        assert payload["collider"]["shape"] == "cylinder"

    def test_cli_stdout_on_missing_output(self, tmp_path: Path) -> None:
        glb = write_bytes(tmp_path, positions_glb(box_verts(0.0, 0.5, 0.0, 1.0, 1.0, 1.0)))
        r = CliRunner().invoke(main, ["precompute", str(glb), "--category", "rock"])
        assert r.exit_code == 0
        payload = json.loads(r.output)
        assert payload["source"] == "aabb"

    def test_cli_soft_error_keeps_exit_zero(self, tmp_path: Path) -> None:
        # GLB válido mas sem POSITION → exit 0 com {"error": ...}.
        gltf = base_gltf(meshes=[{"primitives": [{"attributes": {}}]}])
        glb = write_bytes(tmp_path, build_glb(gltf))
        r = CliRunner().invoke(main, ["precompute", str(glb)])
        assert r.exit_code == 0
        assert "error" in json.loads(r.output)
