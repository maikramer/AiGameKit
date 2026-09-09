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
        assert "_transfer_textures_direct" in src

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

        src = inspect.getsource(mrt._transfer_textures_direct)
        assert "s_bary_sum" in src
        assert "s_bary_u /=" in src


class TestRebakeKeepsMetallicRoughness:
    """O rebake (atlas refeito) tem de levar a MR do paint, não só o albedo.

    O paint (Pós-fix PBR) sai com baseColor + metallicRoughness no grafo glTF
    (Image non-color → Separate Color → Metallic/Roughness). Quando o LOD cai
    na rota de rebake (piso de costuras), o material é reconstruído — sem isto
    o LOD rebakeado voltava a fatores planos, perendo o PBR do paint.
    """

    @staticmethod
    def _build_painted_glb(path, *, size: int = 16) -> None:
        """GLB tipo _painted enriquecido: albedo + ORM + normal + occlusion.

        ORM (R=AO 0.5, G=roughness 0.75, B=metallic 0.25) partilhada por
        metallicRoughnessTexture e occlusionTexture; normal plana via Normal
        Map node — o mesmo contrato que o paint enriquecido (Materialize) sai.
        """
        import bpy
        import numpy as np

        mesh = bpy.data.meshes.new("P")
        verts = [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)]
        mesh.vertices.add(4)
        mesh.vertices.foreach_set("co", np.array(verts, dtype=np.float64).ravel())
        mesh.loops.add(6)
        mesh.loops.foreach_set("vertex_index", np.array([0, 1, 2, 0, 2, 3], dtype=np.int32))
        mesh.polygons.add(2)
        mesh.polygons.foreach_set("loop_start", np.array([0, 3], dtype=np.int32))
        mesh.polygons.foreach_set("loop_total", np.array([3, 3], dtype=np.int32))
        uv = mesh.uv_layers.new(name="UVMap")
        uv.data.foreach_set("uv", np.array([0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1], dtype=np.float32))
        mesh.update()

        mat = bpy.data.materials.new("M")
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = nt.nodes.get("Principled BSDF")
        albedo = np.full((size, size, 3), 0.8, dtype=np.float64)
        orm = np.zeros((size, size, 3), dtype=np.float64)
        orm[..., 0] = 0.5  # R = AO
        orm[..., 1] = 0.75  # G = roughness
        orm[..., 2] = 0.25  # B = metallic
        normal = np.zeros((size, size, 3), dtype=np.float64)
        normal[..., 0] = 0.5
        normal[..., 1] = 0.5
        normal[..., 2] = 1.0

        import os
        import tempfile

        fd, tex_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        fd, orm_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        fd, normal_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        from PIL import Image as PILImage

        PILImage.fromarray((albedo * 255).astype("uint8"), mode="RGB").save(tex_path)
        PILImage.fromarray((orm * 255).astype("uint8"), mode="RGB").save(orm_path)
        PILImage.fromarray((normal * 255).astype("uint8"), mode="RGB").save(normal_path)
        tex_img = bpy.data.images.load(tex_path)
        orm_img = bpy.data.images.load(orm_path)
        orm_img.colorspace_settings.name = "Non-Color"
        normal_img = bpy.data.images.load(normal_path)
        normal_img.colorspace_settings.name = "Non-Color"
        t1 = nt.nodes.new("ShaderNodeTexImage")
        t1.image = tex_img
        nt.links.new(t1.outputs["Color"], bsdf.inputs["Base Color"])
        t2 = nt.nodes.new("ShaderNodeTexImage")
        t2.image = orm_img
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(t2.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
        nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
        t3 = nt.nodes.new("ShaderNodeTexImage")
        t3.image = normal_img
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.inputs["Strength"].default_value = 1.0
        nt.links.new(t3.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        mesh.materials.append(mat)

        obj = bpy.data.objects.new("P", mesh)
        bpy.context.scene.collection.objects.link(obj)
        from aigamekit_shared.bpy_mesh import save_glb
        from aigamekit_shared.gltf_occlusion import ensure_occlusion_texture

        save_glb([obj], path)
        # O exporter bpy não emite occlusionTexture do grafo — patch na origem,
        # igual ao que o export do paint faz.
        ensure_occlusion_texture(path)
        os.unlink(tex_path)
        os.unlink(orm_path)
        os.unlink(normal_path)

    def test_extract_source_data_finds_mr_and_normal(self, tmp_path) -> None:
        import bpy
        import numpy as np
        import pytest

        pytest.importorskip("bpy")
        from aigamekit_shared.bpy_mesh import clear_scene, import_gltf
        from text3d.utils.mesh_remesh_textured import _extract_source_data

        glb = tmp_path / "painted.glb"
        self._build_painted_glb(glb)
        clear_scene()
        import_gltf(glb)
        obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
        source = _extract_source_data(obj)
        assert source.texture_image is not None
        assert source.mr_image is not None
        assert source.normal_image is not None
        h, w = source.mr_image.shape[:2]
        px = source.mr_image[h // 2, w // 2].astype(np.float64) / 255.0
        assert px[0] == pytest.approx(0.5, abs=2 / 255.0)  # R = AO (ORM)
        assert px[1] == pytest.approx(0.75, abs=2 / 255.0)  # G = roughness
        assert px[2] == pytest.approx(0.25, abs=2 / 255.0)  # B = metallic
        npx = source.normal_image[h // 2, w // 2].astype(np.float64) / 255.0
        assert npx[2] == pytest.approx(1.0, abs=2 / 255.0)  # normal plana +Z

    def test_rebake_output_keeps_mr_texture(self, tmp_path) -> None:
        import bpy
        import numpy as np
        import pytest

        pytest.importorskip("bpy")
        cv2 = pytest.importorskip("cv2")
        from aigamekit_shared.bpy_mesh import clear_scene, import_gltf, save_glb
        from text3d.utils.mesh_remesh_textured import _extract_source_data, _rebake_textured_lod

        glb = tmp_path / "painted.glb"
        self._build_painted_glb(glb)
        clear_scene()
        import_gltf(glb)
        obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
        source = _extract_source_data(obj)

        new_obj, temps = _rebake_textured_lod(obj, source, target_faces=2, texture_size=32)
        out = tmp_path / "rebaked.glb"
        save_glb([new_obj], out)
        # O pipeline repõe occlusionTexture no export da sessão
        # (remesh_textured_glb → _remesh_textured_session); o teste compõe os
        # passos à mão, por isso replica o patch aqui.
        from aigamekit_shared.gltf_occlusion import ensure_occlusion_texture

        ensure_occlusion_texture(out)
        for t in temps:
            Path(t).unlink(missing_ok=True)

        with open(out, "rb") as f:
            f.read(12)
            jlen, _ = struct.unpack("<II", f.read(8))
            js = json.loads(f.read(jlen))
        mat = js["materials"][0]
        pbr = mat["pbrMetallicRoughness"]
        assert "baseColorTexture" in pbr
        assert "metallicRoughnessTexture" in pbr
        assert "normalTexture" in mat
        assert "occlusionTexture" in mat
        assert mat["occlusionTexture"]["index"] == pbr["metallicRoughnessTexture"]["index"]

        # Pixel do MR embutido: roughness/metálico sobrevivem ao rebake.
        def image_bytes(index: int) -> bytes:
            img = js["images"][index]
            view = js["bufferViews"][img["bufferView"]]
            with open(out, "rb") as fh:
                fh.read(12)
                jl, _ = struct.unpack("<II", fh.read(8))
                fh.read(jl)
                bl, _ = struct.unpack("<II", fh.read(8))
                data = fh.read(bl)
            off = view.get("byteOffset", 0)
            return data[off : off + view["byteLength"]]

        mr_index = pbr["metallicRoughnessTexture"]["index"]
        src = js["textures"][mr_index]["source"]
        bgr = cv2.imdecode(np.frombuffer(image_bytes(src), dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = bgr.shape[:2]
        center = bgr[h // 2, w // 2].astype(np.float64) / 255.0
        assert center[1] == pytest.approx(0.75, abs=6 / 255.0)  # G = roughness
        assert center[0] == pytest.approx(0.25, abs=6 / 255.0)  # B = metallic (cv2=BGR)
        assert center[2] == pytest.approx(0.5, abs=6 / 255.0)  # R = AO (ORM)
