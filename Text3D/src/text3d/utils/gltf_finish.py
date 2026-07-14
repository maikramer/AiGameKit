"""Finalização padrão de GLBs para o jogo (Round 2).

Pipeline canónico aplicado a todo output ``meshes/``:

1. Recalcular tangents (MikkTSpace) via bpy quando há UVs.
2. ``gltf-transform dedup``  — remove buffers/imagens duplicadas.
3. ``gltf-transform prune``  — remove nós/materiais/texturas não-referenciados.
4. ``gltf-transform uastc``  — comprime texturas para KTX2/UASTC (ainda via npx).
5. Meshopt — **preferir bpy 5.2+** (``export_meshopt_compression_enable``);
   fallback ``gltf-transform meshopt`` quando bpy/runtime indisponível ou quando
   o GLB já tem KTX2 (re-export bpy arrisca re-encodar texturas).

Cada passo é opcional via flag. Falhas em passos individuais são warnings;
não abortam (graceful degradation: GLB sai válido mesmo sem npx / sem libmeshoptimizer).
"""

from __future__ import annotations

import json
import logging
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class FinishResult:
    output_path: Path
    tangents_added: bool = False
    dedup_applied: bool = False
    prune_applied: bool = False
    ktx2_applied: bool = False
    meshopt_applied: bool = False
    meshopt_backend: str = ""  # "bpy" | "gltf-transform" | ""
    skipped_reason: str = ""

    def fully_optimized(self) -> bool:
        return self.dedup_applied and self.prune_applied and self.ktx2_applied and self.meshopt_applied


def _has_npx() -> bool:
    return shutil.which("npx") is not None


def _run_gltf_transform(
    subcmd: str,
    src: Path,
    dst: Path,
    extra_args: list[str] | None = None,
    *,
    timeout: int = 600,
) -> tuple[bool, str]:
    if not _has_npx():
        return False, "npx ausente no PATH"
    args = ["npx", "--yes", "@gltf-transform/cli", subcmd, str(src), str(dst)]
    if extra_args:
        args.extend(extra_args)
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, f"gltf-transform {subcmd} timeout"
    if r.returncode != 0:
        snippet = (r.stderr or r.stdout or "")[-400:]
        return False, snippet
    return True, ""


def _glb_json(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
        if raw[:4] != b"glTF" or len(raw) < 20:
            return None
        json_len = struct.unpack_from("<I", raw, 12)[0]
        return json.loads(raw[20 : 20 + json_len])
    except Exception:
        return None


def _glb_has_ktx2(path: Path) -> bool:
    meta = _glb_json(path)
    if not meta:
        return False
    for img in meta.get("images", []):
        mime = str(img.get("mimeType") or "")
        if "ktx" in mime.lower():
            return True
    return False


def _glb_has_meshopt(path: Path) -> bool:
    meta = _glb_json(path)
    if not meta:
        return False
    used = set(meta.get("extensionsUsed") or [])
    return "EXT_meshopt_compression" in used or "KHR_meshopt_compression" in used


def _recalc_tangents_inplace(glb_path: Path) -> bool:
    """Re-importa o GLB no bpy, calcula tangents e re-exporta no mesmo path.

    Devolve True se tangents foram adicionados (UVs presentes), False c.c.
    Falha graciosa em ausência de bpy.
    """
    try:
        import bpy

        from gamedev_shared.bpy_mesh import clear_scene
    except ImportError:
        log.debug("gltf_finish: bpy ausente — tangents não recalculados")
        return False

    clear_scene()
    try:
        bpy.ops.import_scene.gltf(filepath=str(glb_path))
    except Exception as exc:
        log.warning("gltf_finish: import bpy falhou: %s", exc)
        return False

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not mesh_objs:
        return False

    has_uv = False
    for m in mesh_objs:
        if m.data.uv_layers:
            has_uv = True
            try:
                m.data.calc_tangents()
            except Exception as exc:
                log.debug("calc_tangents falhou em %s: %s", m.name, exc)

    if not has_uv:
        return False

    bpy.ops.object.select_all(action="DESELECT")
    for o in [*mesh_objs, *arm_objs]:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm_objs[0] if arm_objs else mesh_objs[0]

    export_kwargs: dict = {
        "filepath": str(glb_path),
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": False,
        "export_normals": True,
        "export_tangents": True,
        "export_texcoords": True,
        "export_materials": "EXPORT",
        "export_image_format": "AUTO",
        "export_animations": bool(arm_objs),
        "export_skins": bool(arm_objs),
    }
    try:
        from gamedev_shared.bpy_mesh import gltf_export_supports_meshopt

        if gltf_export_supports_meshopt():
            props = bpy.ops.export_scene.gltf.get_rna_type().properties
            if "export_optimize_disable_viewport" in props:
                export_kwargs["export_optimize_disable_viewport"] = True
    except Exception:
        pass

    try:
        bpy.ops.export_scene.gltf(**export_kwargs)
    except Exception as exc:
        log.warning("gltf_finish: export bpy falhou: %s", exc)
        return False
    return True


def _apply_meshopt_bpy(glb_in: Path, glb_out: Path) -> tuple[bool, str]:
    """Re-import + re-export com meshopt nativo (bpy 5.2+).

    Evitar quando o input já tem KTX2 — o roundtrip bpy pode re-encodar imagens.
    """
    try:
        import bpy

        from gamedev_shared.bpy_mesh import (
            clear_scene,
            gltf_meshopt_export_kwargs,
            meshopt_runtime_available,
        )
    except ImportError:
        return False, "bpy ausente"

    if not meshopt_runtime_available():
        return (
            False,
            "libmeshoptimizer.so ausente — instale libmeshoptimizer-dev (Debian/Ubuntu)",
        )

    if _glb_has_ktx2(glb_in):
        return False, "input tem KTX2 — meshopt bpy arrisca re-encode; usar gltf-transform"

    clear_scene()
    try:
        bpy.ops.import_scene.gltf(filepath=str(glb_in))
    except Exception as exc:
        return False, f"import falhou: {exc}"

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not mesh_objs:
        return False, "sem meshes"

    bpy.ops.object.select_all(action="DESELECT")
    for o in [*mesh_objs, *arm_objs]:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm_objs[0] if arm_objs else mesh_objs[0]

    export_kwargs: dict = {
        "filepath": str(glb_out),
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": False,
        "export_normals": True,
        "export_tangents": True,
        "export_texcoords": True,
        "export_materials": "EXPORT",
        "export_image_format": "AUTO",
        "export_animations": bool(arm_objs),
        "export_skins": bool(arm_objs),
        **gltf_meshopt_export_kwargs(enable=True),
    }
    props = bpy.ops.export_scene.gltf.get_rna_type().properties
    if "export_optimize_disable_viewport" in props:
        export_kwargs["export_optimize_disable_viewport"] = True

    try:
        bpy.ops.export_scene.gltf(**export_kwargs)
    except Exception as exc:
        return False, f"export meshopt falhou: {exc}"

    if not glb_out.is_file() or not _glb_has_meshopt(glb_out):
        return False, "export sem EXT_meshopt_compression"
    return True, ""


def gltf_transform_decompress(
    glb_in: Path,
    glb_out: Path,
) -> bool:
    """Descompressa um GLB (remove EXT_meshopt_compression).

    Em bpy ≥ 5.2 o importador já descodifica meshopt — esta função só é
    necessária para pipelines legados ou ferramentas sem bpy 5.2. Usa
    ``gltf-transform copy``; cai para cópia binária se ``npx`` ausente.
    """
    glb_in = Path(glb_in).resolve()
    glb_out = Path(glb_out).resolve()
    glb_out.parent.mkdir(parents=True, exist_ok=True)

    if not glb_in.is_file():
        return False

    if _has_npx():
        ok, err = _run_gltf_transform("copy", glb_in, glb_out)
        if ok:
            return True
        log.warning("gltf_finish: descompress falhou — %s", err)

    try:
        shutil.copy2(glb_in, glb_out)
    except OSError as exc:
        log.warning("gltf_finish: cópia fallback falhou: %s", exc)
        return False
    return False


def gltf_transform_finish(
    glb_in: Path,
    glb_out: Path,
    *,
    apply_tangents: bool = True,
    apply_dedup: bool = True,
    apply_prune: bool = True,
    apply_uastc: bool = True,
    apply_meshopt: bool = False,
    uastc_level: int = 2,
    uastc_rdo: float = 1.0,
    meshopt_level: str = "high",
) -> FinishResult:
    """Pipeline padrão de finalização de GLB.

    Ordem fixa: tangents → dedup → prune → uastc → meshopt. Cada passo é
    opcional. Quando ``glb_in == glb_out``, escreve in-place após pipeline
    em tempdir.

    ``apply_meshopt`` ativa ``EXT_meshopt_compression``. Preferência:
    bpy 5.2+ nativo (quando runtime ``libmeshoptimizer`` OK e sem KTX2 no
    input do passo); senão ``@gltf-transform/cli meshopt``.
    """
    glb_in = Path(glb_in).resolve()
    glb_out = Path(glb_out).resolve()
    glb_out.parent.mkdir(parents=True, exist_ok=True)

    if not glb_in.is_file():
        return FinishResult(output_path=glb_out, skipped_reason=f"input ausente: {glb_in}")

    res = FinishResult(output_path=glb_out)

    with tempfile.TemporaryDirectory(prefix="gltf_finish_") as tdir:
        tmp = Path(tdir)
        current = tmp / "0.glb"
        shutil.copy2(glb_in, current)

        if apply_tangents:
            ok = _recalc_tangents_inplace(current)
            res.tangents_added = ok

        steps: list[tuple[str, str, list[str] | None]] = []
        if apply_dedup:
            steps.append(("dedup", "dedup", None))
        if apply_prune:
            steps.append(("prune", "prune", None))
        if apply_uastc:
            steps.append(("uastc", "uastc", ["--level", str(uastc_level), "--rdo", str(uastc_rdo)]))

        for idx, (label, subcmd, extra) in enumerate(steps, start=1):
            staged = tmp / f"{idx}.glb"
            ok, err = _run_gltf_transform(subcmd, current, staged, extra)
            if ok:
                current = staged
                if label == "dedup":
                    res.dedup_applied = True
                elif label == "prune":
                    res.prune_applied = True
                elif label == "uastc":
                    res.ktx2_applied = True
            else:
                log.warning("gltf_finish: passo %s falhou — %s", label, err)

        if apply_meshopt:
            staged = tmp / "meshopt.glb"
            bpy_ok, bpy_err = _apply_meshopt_bpy(current, staged)
            if bpy_ok:
                current = staged
                res.meshopt_applied = True
                res.meshopt_backend = "bpy"
            else:
                if bpy_err:
                    log.debug("gltf_finish: meshopt bpy skip — %s", bpy_err)
                ok, err = _run_gltf_transform("meshopt", current, staged, ["--level", meshopt_level])
                if ok:
                    current = staged
                    res.meshopt_applied = True
                    res.meshopt_backend = "gltf-transform"
                else:
                    log.warning("gltf_finish: passo meshopt falhou — %s", err)

        shutil.copy2(current, glb_out)

    return res
