"""Rotas do LOD texturado: atlas preservado (meshopt) vs atlas refeito (rebake).

O Decimate COLLAPSE do bpy não conhece as ilhas UV: em rácios agressivos os
vértices colapsam através das costuras e a textura sai esticada/rasgada. As duas
rotas cobertas aqui existem para evitar isso.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path


def _synthetic_glb(path: Path, *, tri_count: int, skinned: bool = False) -> Path:
    attributes: dict[str, int] = {"POSITION": 1}
    doc: dict = {
        "asset": {"version": "2.0"},
        "accessors": [
            {"componentType": 5125, "count": tri_count * 3, "type": "SCALAR"},
            {"componentType": 5126, "count": tri_count * 3, "type": "VEC3"},
        ],
        "meshes": [{"primitives": [{"indices": 0, "attributes": attributes}]}],
    }
    if skinned:
        doc["skins"] = [{"joints": [0]}]
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(raw))
    path.write_bytes(header + struct.pack("<II", len(raw), 0x4E4F534A) + raw)
    return path


class TestMeshoptPreserveAtlas:
    def test_uses_meshopt_result_when_target_reached(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import mesh_remesh_textured as mrt

        glb = _synthetic_glb(tmp_path / "src.glb", tri_count=40_000)

        def fake_simplify(src, dst, *, target_faces, weld=True):
            Path(dst).write_bytes(b"glTF-simplified")
            return True, target_faces, ""

        monkeypatch.setattr("text3d.utils.gltf_finish.meshopt_simplify_glb", fake_simplify)
        out, floored = mrt._meshopt_preserve_atlas(glb, 1440, tmp_path)

        assert out is not None and out.exists()
        assert floored is False

    def test_signals_rebake_when_seam_floor_blocks_target(self, tmp_path: Path, monkeypatch) -> None:
        """Piso de costuras: COLLAPSE atingiria o alvo, mas rasgando a textura."""
        from text3d.utils import mesh_remesh_textured as mrt

        glb = _synthetic_glb(tmp_path / "src.glb", tri_count=74_758)

        def floored_simplify(src, dst, *, target_faces, weld=True):
            Path(dst).write_bytes(b"glTF-floored")
            return True, 2402, ""

        monkeypatch.setattr("text3d.utils.gltf_finish.meshopt_simplify_glb", floored_simplify)
        out, floored = mrt._meshopt_preserve_atlas(glb, 1440, tmp_path)

        assert out is None
        assert floored is True

    def test_rebakes_when_preserved_atlas_costs_too_many_verts(self, tmp_path: Path, monkeypatch) -> None:
        """Atlas intacto mas V/Tri acima do tecto das regras LOD → repackar UV."""
        from text3d.utils import gltf_finish
        from text3d.utils import mesh_remesh_textured as mrt

        glb = _synthetic_glb(tmp_path / "src.glb", tri_count=40_000)
        monkeypatch.setattr(
            gltf_finish,
            "meshopt_simplify_glb",
            lambda src, dst, *, target_faces, weld=True: (Path(dst).write_bytes(b"x"), (True, target_faces, ""))[1],
        )
        monkeypatch.setattr(gltf_finish, "glb_v_per_tri", lambda p: 1.6)
        out, floored = mrt._meshopt_preserve_atlas(glb, 1440, tmp_path)

        assert out is None
        assert floored is True

    def test_v_per_tri_cap_leaves_headroom_for_export_inflation(self) -> None:
        """O tecto é medido no intermédio; o re-export a 60° ainda parte loops."""
        from text3d.utils.gltf_finish import MESHOPT_MAX_V_PER_TRI

        worst_observed_inflation = 1.15
        lod_rules_cap = 1.6
        assert MESHOPT_MAX_V_PER_TRI * worst_observed_inflation < lod_rules_cap

    def test_keeps_atlas_when_vert_cost_is_healthy(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import gltf_finish
        from text3d.utils import mesh_remesh_textured as mrt

        glb = _synthetic_glb(tmp_path / "src.glb", tri_count=40_000)
        monkeypatch.setattr(
            gltf_finish,
            "meshopt_simplify_glb",
            lambda src, dst, *, target_faces, weld=True: (Path(dst).write_bytes(b"x"), (True, target_faces, ""))[1],
        )
        monkeypatch.setattr(gltf_finish, "glb_v_per_tri", lambda p: 0.72)
        out, floored = mrt._meshopt_preserve_atlas(glb, 1440, tmp_path)

        assert out is not None
        assert floored is False

    def test_falls_back_to_legacy_when_cli_missing(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import mesh_remesh_textured as mrt

        glb = _synthetic_glb(tmp_path / "src.glb", tri_count=40_000)
        monkeypatch.setattr(
            "text3d.utils.gltf_finish.meshopt_simplify_glb",
            lambda src, dst, *, target_faces, weld=True: (False, -1, "npx ausente no PATH"),
        )
        out, floored = mrt._meshopt_preserve_atlas(glb, 1440, tmp_path)

        assert out is None
        assert floored is False

    def test_skips_skinned_glb(self, tmp_path: Path) -> None:
        """Rigged/animated seguem o caminho geométrico — meshopt não os toca."""
        from text3d.utils import mesh_remesh_textured as mrt

        glb = _synthetic_glb(tmp_path / "rigged.glb", tri_count=40_000, skinned=True)
        assert mrt._meshopt_preserve_atlas(glb, 1440, tmp_path) == (None, False)

    def test_skips_when_already_below_target(self, tmp_path: Path) -> None:
        from text3d.utils import mesh_remesh_textured as mrt

        glb = _synthetic_glb(tmp_path / "tiny.glb", tri_count=300)
        assert mrt._meshopt_preserve_atlas(glb, 1440, tmp_path) == (None, False)


class TestRebakeRoute:
    def test_rebake_does_not_voxel_remesh(self) -> None:
        """Voxel remesh punha a casca do lado errado de paredes duplas (chapel)."""
        import inspect

        from text3d.utils import mesh_remesh_textured as mrt

        src = inspect.getsource(mrt._rebake_textured_lod)
        assert "_bpy_remesh" not in src
        assert "_uv_unwrap" in src
        assert "_transfer_texture_direct" in src

    def test_rebake_welds_before_decimate(self) -> None:
        """Costuras partidas pelo glTF fazem o COLLAPSE rasgar ilhas soltas."""
        import inspect

        from text3d.utils import mesh_remesh_textured as mrt

        src = inspect.getsource(mrt._rebake_textured_lod)
        assert src.index("remove_doubles") < src.index("decimate_mesh_object")

    def test_rebake_carries_surface_params(self) -> None:
        """BSDF default lava a cor face ao painted (specular alto)."""
        import inspect

        from text3d.utils import mesh_remesh_textured as mrt

        assert "surface_params" in inspect.signature(mrt._build_textured_bpy_mesh).parameters
        assert "surface_params" in inspect.getsource(mrt._rebake_textured_lod)

    def test_build_textured_mesh_smooth_shades(self) -> None:
        import inspect

        from text3d.utils import mesh_remesh_textured as mrt

        assert "apply_smooth_by_angle" in inspect.getsource(mrt._build_textured_bpy_mesh)


class TestSessionWiring:
    def test_session_skips_collapse_after_meshopt_or_rebake(self) -> None:
        """Decimar de novo depois do alvo atingido só degradaria a silhueta."""
        import inspect

        from text3d.utils import mesh_remesh_textured as mrt

        src = inspect.getsource(mrt._remesh_textured_session)
        assert "if meshopt_glb is not None or rebaked:" in src
        assert "_meshopt_preserve_atlas" in src

    def test_public_entrypoint_cleans_scratch(self) -> None:
        import inspect

        from text3d.utils import mesh_remesh_textured as mrt

        src = inspect.getsource(mrt.remesh_textured_glb)
        assert "mkdtemp" in src
        assert "rmtree" in src


class TestClosestPointNumerics:
    """Regressão do epsilon relativo + gating por normal no closest-point.

    O limiar absoluto ``|denom| < 1e-6`` colapsava triângulos milimétricos
    (denom ~1e-10 em unidades métricas) para denominador 1.0 — distâncias de
    candidatas garbage e o rebake escolhia faces a centímetros com UVs de
    outra região do atlas: a «textura despedaçada» dos LODs village
    (round-trip da fonte consigo própria media ~15% de amostras erradas;
    após o fix, 0.3%).
    """

    def test_millimeter_triangle_closest_point_is_exact(self) -> None:
        import numpy as np

        from text3d.utils.mesh_remesh_textured import _closest_point_batch

        # dois triângulos de ~5 mm afastados 2 cm — o query está NO primeiro
        verts = np.array(
            [
                [0.0, 0.0, 0.0],
                [0.005, 0.0, 0.0],
                [0.0, 0.005, 0.0],
                [0.0, 0.02, 0.0],
                [0.005, 0.02, 0.0],
                [0.0, 0.02, 0.005],
            ],
            dtype=np.float64,
        )
        faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
        q = np.array([[0.001, 0.001, 0.0]], dtype=np.float64)

        closest, face_ids = _closest_point_batch(verts, faces, q)
        assert face_ids[0] == 0
        assert np.allclose(closest[0], q[0], atol=1e-9)

    def test_degenerate_triangle_does_not_nan(self) -> None:
        import numpy as np

        from text3d.utils.mesh_remesh_textured import _closest_point_batch

        # segundo triângulo colinear (área zero) — não pode produzir NaN/inf
        verts = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [2.0, 2.0, 0.0],
                [3.0, 3.0, 0.0],
                [4.0, 4.0, 0.0],
            ],
            dtype=np.float64,
        )
        faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
        q = np.array([[0.1, 0.1, 0.0]], dtype=np.float64)

        closest, face_ids = _closest_point_batch(verts, faces, q)
        assert np.all(np.isfinite(closest))
        assert face_ids[0] == 0

    def test_normal_gate_blocks_opposite_side_of_thin_gap(self) -> None:
        import numpy as np

        from text3d.utils.mesh_remesh_textured import _closest_point_batch

        # duas placas paralelas afastadas 1 mm; o query está 0.4 mm ACIMA da
        # placa de cima (mais perto da de baixo? não — mais perto da de cima).
        # Para forçar o lado errado: query mais perto da placa de BAixo mas
        # com a normal da de CIMA (drift de decimação atravessa o gap).
        top = np.array([[0, 0, 0.001], [1, 0, 0.001], [0, 1, 0.001]], dtype=np.float64)
        bottom = np.array([[0, 0, 0.0], [1, 0, 0.0], [0, 1, 0.0]], dtype=np.float64)
        verts = np.vstack([top, bottom])
        # baixo com winding invertido (casca dupla: face virada para -Z)
        faces = np.array([[0, 1, 2], [5, 4, 3]], dtype=np.int32)
        q = np.array([[0.2, 0.2, 0.0004]])  # mais perto da placa de baixo (0.0004 < 0.0006)
        up = np.array([[0.0, 0.0, 1.0]])

        _, face_plain = _closest_point_batch(verts, faces, q)
        assert face_plain[0] == 1  # sem normal: escolhe a placa de baixo (mais perto)

        _, face_gated = _closest_point_batch(verts, faces, q, query_normals=up)
        assert face_gated[0] == 0  # com normal: obrigado a ficar na placa de cima

    def test_normal_gate_falls_back_when_no_compatible_candidate(self) -> None:
        import numpy as np

        from text3d.utils.mesh_remesh_textured import _closest_point_batch

        # só existe a placa de baixo; normal aponta para longe — fallback ao
        # closest-point cru em vez de devolver lixo
        verts = np.array([[0, 0, 0.0], [1, 0, 0.0], [0, 1, 0.0]], dtype=np.float64)
        faces = np.array([[0, 1, 2]], dtype=np.int32)
        q = np.array([[0.2, 0.2, 0.5]])
        up = np.array([[0.0, 0.0, 1.0]])

        closest, face_ids = _closest_point_batch(verts, faces, q, query_normals=up)
        assert face_ids[0] == 0
        assert np.allclose(closest[0], [0.2, 0.2, 0.0])

    def test_phase4_renormalizes_clipped_bary(self) -> None:
        """Clip sem renormalizar extrapola a UV para fora do triângulo.

        Quando o closest-point cai fora da face escolhida (~27% das queries em
        meshes decimadas), os pesos após clip somam >1 e a UV amostrava texels
        não pintados do atlas — a «textura despedaçada» dos LODs rebakeados
        (sintético world-continuous: 44.6% → 5.2% de amostras erradas).
        """
        import inspect

        from text3d.utils import mesh_remesh_textured as mrt

        src = inspect.getsource(mrt._transfer_texture_direct)
        assert "s_bary_sum" in src
        assert "s_bary_u /=" in src
