# Hunyuan 3D is licensed under the TENCENT HUNYUAN NON-COMMERCIAL LICENSE AGREEMENT
# except for the third-party components listed below.
# Hunyuan 3D does not impose any additional limitations beyond what is outlined
# in the repsective licenses of these third-party components.
# Users must comply with all terms and conditions of original licenses of these third-party
# components and must ensure that the usage of the third party components adheres to
# all relevant laws and regulations.

# For avoidance of doubts, Hunyuan 3D means the large language models and
# their software and algorithms, including trained model weights, parameters (including
# optimizer states), machine-learning model code, inference-enabling code, training-enabling code,
# fine-tuning enabling code and other elements of the foregoing made publicly available
# by Tencent in accordance with TENCENT HUNYUAN COMMUNITY LICENSE AGREEMENT.

import numpy as np
import xatlas

# O xatlas pode ficar preso em malhas do refine Hi3D (campfire_pit_refined:
# >170 s) e a chamada é C — não dá para interromper in-process. O unwrap corre
# num subprocesso com timeout; em timeout a linha falha (o pack continua) em
# vez de pendurar o lote. O mesh do paint deve vir topology-fixado do
# GameAssets (refix pós-refine) — aí o xatlas termina (~60 s no caso patológico,
# com atlas utilizável).
_XATLAS_TIMEOUT_S = 300.0


def _triangulate_in_place(mesh_obj) -> None:
    import bpy

    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
    bpy.ops.object.mode_set(mode="OBJECT")


def _rebuild_with_per_vertex_uvs(mesh, new_verts, new_faces, new_uvs) -> None:
    """Reconstrói a malha com UVs por vértice (vértices divididos nas seams).

    O ``load_mesh`` do renderer lê ``visual.uv`` **por vértice** (média dos
    loops); sem dividir nas seams, UVs diferentes do mesmo vértice colapsavam.
    """
    mesh.clear_geometry()
    mesh.from_pydata(np.asarray(new_verts).tolist(), [], np.asarray(new_faces).tolist())
    uv_layer = mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else mesh.uv_layers[0]
    flat_uvs = np.asarray(new_uvs, dtype=np.float32)[np.asarray(new_faces).ravel()].ravel()
    uv_layer.data.foreach_set("uv", flat_uvs.astype(np.float32))
    mesh.update()


def xatlas_parametrize_bounded(verts, faces, *, timeout_s: float = _XATLAS_TIMEOUT_S, **chart_opts):
    """``xatlas.parametrize`` num subprocesso com timeout.

    O filho corre só numpy+xatlas (sem bpy/CUDA) e devolve os arrays. Em
    timeout levanta ``RuntimeError`` — falhar a linha é melhor do que um lote
    pendurado ou um atlas sobreposto (o Smart UV Project do Blender sobrepõe
    ilhas nestas malhas: área UV ~5000 vs 1).
    """
    import os
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory(prefix="xatlas_") as td:
        inp = os.path.join(td, "in.npz")
        out = os.path.join(td, "out.npz")
        payload = {
            "verts": np.asarray(verts, dtype=np.float32),
            "faces": np.asarray(faces, dtype=np.uint32),
        }
        payload.update({k: float(v) for k, v in chart_opts.items() if v is not None})
        np.savez(inp, **payload)
        try:
            subprocess.run(
                [sys.executable, "-m", "paint3d.hy3dpaint.utils._xatlas_worker", inp, out],
                check=True,
                timeout=float(timeout_s),
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"xatlas excedeu {float(timeout_s):.0f}s nesta malha ({len(faces):,} faces) — "
                "o paint precisa de um mesh topology-fixado (refix)"
            ) from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"xatlas falhou no subprocesso: {exc}") from exc
        if not os.path.isfile(out):
            raise RuntimeError("xatlas não produziu o atlas (ficheiro de saída ausente)")
        data = np.load(out)
        return data["vmapping"], data["indices"], data["uvs"]


def mesh_uv_wrap(mesh_objects, *, texture_size: int = 2048):
    import bpy

    if isinstance(mesh_objects, list):
        if len(mesh_objects) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in mesh_objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = mesh_objects[0]
            bpy.ops.object.join()
            mesh_obj = bpy.context.active_object
        else:
            mesh_obj = mesh_objects[0]
    else:
        mesh_obj = mesh_objects

    mesh = mesh_obj.data
    if len(mesh.loops) != 3 * len(mesh.polygons):
        _triangulate_in_place(mesh_obj)
        mesh = mesh_obj.data

    verts = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", verts)
    verts = verts.reshape(-1, 3)
    faces = np.empty(len(mesh.polygons) * 3, dtype=np.int32)
    mesh.polygons.foreach_get("vertices", faces)
    faces = faces.reshape(-1, 3)

    if len(faces) > 500_000_000:
        raise ValueError("The mesh has more than 500,000,000 faces, which is not supported.")

    vmapping, indices, uvs = xatlas_parametrize_bounded(verts, faces)
    new_verts = verts[vmapping]
    _rebuild_with_per_vertex_uvs(mesh, new_verts, indices, uvs)

    return mesh_obj
