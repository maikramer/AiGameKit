"""Finalização padrão de GLBs para o jogo (Round 2).

Pipeline canónico aplicado a todo output ``meshes/``:

1. Shade-smooth + NORMAL + tangents (MikkTSpace) via bpy quando há UVs.
2. ``gltf-transform dedup``  — remove buffers/imagens duplicadas.
3. ``gltf-transform prune --keep-attributes`` — limpa nós órfãos **sem**
   apagar TANGENT/NORMAL (prune default do CLI 4.x remove TANGENT).
4. ``gltf-transform uastc``  — comprime texturas para KTX2/UASTC (ainda via npx).
5. Meshopt — **preferir bpy 5.2+** (``export_meshopt_compression_enable``);
   fallback ``gltf-transform meshopt`` quando bpy/runtime indisponível ou quando
   o GLB já tem KTX2 (re-export bpy arrisca re-encodar texturas).

Cada passo é opcional via flag. Falhas em passos individuais são warnings;
não abortam (graceful degradation: GLB sai válido mesmo sem npx / sem libmeshoptimizer).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Locais usuais do binário ``ktx`` (KTX-Software) quando instalado via
# text3d extras / tarball user-local — gltf-transform uastc precisa dele no PATH.
_KTX_PATH_CANDIDATES = (
    Path.home() / ".local" / "bin",
    Path.home() / ".local" / "opt" / "KTX-Software" / "bin",
)


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


def _ensure_finish_path() -> None:
    """Garante ~/.local/bin (+ opt/KTX-Software) no PATH do processo actual."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    prepend: list[str] = []
    for cand in _KTX_PATH_CANDIDATES:
        s = str(cand)
        if cand.is_dir() and s not in parts and s not in prepend:
            prepend.append(s)
    if prepend:
        os.environ["PATH"] = os.pathsep.join([*prepend, *parts])


def _has_npx() -> bool:
    _ensure_finish_path()
    return shutil.which("npx") is not None


def _has_ktx() -> bool:
    """True quando o CLI ``ktx`` (KTX-Software) está no PATH — requisito do uastc."""
    _ensure_finish_path()
    return shutil.which("ktx") is not None


def _run_gltf_transform(
    subcmd: str,
    src: Path,
    dst: Path,
    extra_args: list[str] | None = None,
    *,
    timeout: int = 600,
) -> tuple[bool, str]:
    _ensure_finish_path()
    if not _has_npx():
        return False, "npx ausente no PATH"
    if subcmd == "uastc" and not _has_ktx():
        return (
            False,
            "ktx (KTX-Software) ausente no PATH — necessário para UASTC/KTX2; "
            "instale https://github.com/KhronosGroup/KTX-Software/releases "
            "ou `./install.sh text3d` (extras)",
        )
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
        if subcmd == "uastc" and ("command -v ktx" in snippet or "ktx" in snippet.lower()):
            return (
                False,
                f"gltf-transform uastc precisa do CLI `ktx` (KTX-Software) no PATH — {snippet.strip()}",
            )
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


def _glb_has_skins(path: Path) -> bool:
    """True quando o GLB tem skinning (armature / JOINTS)."""
    meta = _glb_json(path)
    if not meta:
        return False
    return bool(meta.get("skins"))


def glb_mesh_totals(path: Path) -> tuple[int, int]:
    """``(faces, vertices)`` lidos do chunk JSON do GLB (sem bpy). ``(-1, -1)`` se ilegível."""
    meta = _glb_json(Path(path))
    if not meta:
        return -1, -1
    accessors = meta.get("accessors") or []
    faces = verts = 0
    for mesh in meta.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            idx = prim.get("indices")
            if idx is not None and idx < len(accessors):
                faces += int(accessors[idx].get("count", 0)) // 3
            pos = (prim.get("attributes") or {}).get("POSITION")
            if pos is not None and pos < len(accessors):
                verts += int(accessors[pos].get("count", 0))
    return faces, verts


def glb_face_count(path: Path) -> int:
    """Triângulos totais lidos do chunk JSON do GLB (sem bpy). ``-1`` se ilegível."""
    return glb_mesh_totals(path)[0]


def glb_v_per_tri(path: Path) -> float:
    """Vértices por triângulo — proxy barato para costuras/loops partidos."""
    faces, verts = glb_mesh_totals(path)
    if faces <= 0:
        return -1.0
    return verts / faces


# Tolerância aceite acima do alvo antes de desistir do caminho meshopt.
MESHOPT_SIMPLIFY_SLACK = 1.10
# Tecto de V/Tri do atlas preservado. Acima disto o custo em vértices das
# costuras originais supera o ganho de manter o atlas — compensa refazer o UV
# (xatlas empacota com muito menos costura).
#
# Medido **no intermédio**, antes do re-export. O `smooth_shade_scene` a 60graus
# transforma creases em arestas duras e o exporter parte loops: malhas sãs
# inflacionam 2-3% (V/Tri ~1.0), as cheias de costura 11-15% (spear 1.451 -> 1.662,
# swamp_shack 1.533 -> 1.702). 1.35 * 1.15 = 1.55, dentro do tecto 1.6 das regras
# LOD do GameAssets.
MESHOPT_MAX_V_PER_TRI = 1.35
# Passes de refinamento do rácio (o simplificador subestima quando há costuras).
_MESHOPT_SIMPLIFY_PASSES = 3


def meshopt_simplify_glb(
    src: Path,
    dst: Path,
    *,
    target_faces: int,
    weld: bool = True,
) -> tuple[bool, int, str]:
    """Simplifica geometria com o meshoptimizer via ``gltf-transform simplify``.

    Ao contrário do Decimate COLLAPSE do bpy, o meshoptimizer trata as costuras
    de atributos (UV/normal) como fronteiras bloqueadas: o atlas do paint
    sobrevive intacto, sem os rasgos de textura que o COLLAPSE produz em rácios
    agressivos. Em troca existe um **piso de costuras** — o alvo pode não ser
    atingível e o caller precisa de rebake do atlas para descer abaixo dele.

    Args:
        src: GLB de entrada.
        dst: GLB de saída (só escrito em caso de sucesso).
        target_faces: Orçamento de triângulos.
        weld: Corre ``gltf-transform weld`` antes (recomendado pelo upstream).

    Returns:
        ``(ok, faces, erro)`` — ``ok`` é False quando o CLI falhou; ``faces`` é a
        contagem obtida (pode ficar acima de ``target_faces`` no piso de costuras).
    """
    src = Path(src)
    dst = Path(dst)
    n_before = glb_face_count(src)
    if n_before <= 0:
        return False, -1, "não foi possível contar faces do GLB"
    target = max(4, int(target_faces))
    if n_before <= target:
        return False, n_before, "mesh já abaixo do alvo"

    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meshopt_simplify_") as tmpdir:
        tmp = Path(tmpdir)
        current = src
        if weld:
            welded = tmp / "welded.glb"
            ok, err = _run_gltf_transform("weld", current, welded)
            if not ok:
                return False, -1, f"weld: {err}"
            current = welded

        best: Path | None = None
        best_faces = -1
        ratio = target / float(n_before)
        for _ in range(_MESHOPT_SIMPLIFY_PASSES):
            out = tmp / "simplified.glb"
            ok, err = _run_gltf_transform(
                "simplify",
                current,
                out,
                ["--ratio", f"{max(1e-4, min(1.0, ratio)):.6f}", "--error", "1"],
            )
            if not ok:
                return False, -1, f"simplify: {err}"
            faces = glb_face_count(out)
            if faces <= 0:
                return False, -1, "GLB simplificado ilegível"
            keep = tmp / f"best_{faces}.glb"
            shutil.copy2(out, keep)
            best, best_faces = keep, faces
            if faces <= target:
                break
            # Piso de costuras: outra passagem com o mesmo rácio não desce mais.
            next_ratio = ratio * (target / float(faces))
            if next_ratio >= ratio * 0.95:
                break
            ratio = next_ratio

        if best is None or best_faces <= 0:
            return False, -1, "simplify não produziu resultado"
        shutil.copy2(best, dst)
        return True, best_faces, ""


def _glb_vertex_attrs(path: Path) -> set[str]:
    """Conjunto de attrs de vértice presentes em qualquer primitive do GLB."""
    meta = _glb_json(path)
    if not meta:
        return set()
    found: set[str] = set()
    for mesh in meta.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            attrs = prim.get("attributes") or {}
            found.update(str(k) for k in attrs)
    return found


def _recalc_tangents_inplace(glb_path: Path) -> bool:
    """Garante NORMAL+TANGENT no GLB via bpy (smooth-by-angle + MikkTSpace).

    Sempre aplica ``smooth_shade_scene`` antes do export — import de GLB sem
    NORMAL fica flat e o exporter escreve V/Tri≈3 (edges vivos). Tangents só
    quando há UVs. Devolve True se o export correu com UVs (tangents pedidos).
    """
    try:
        import bpy

        from aigamekit_shared.bpy_mesh import clear_scene, smooth_shade_scene
    except ImportError:
        log.debug("gltf_finish: bpy ausente — tangents não recalculados")
        return False

    clear_scene()
    try:
        from aigamekit_shared.bpy_mesh import import_gltf

        import_gltf(glb_path)
    except Exception as exc:
        log.warning("gltf_finish: import bpy falhou: %s", exc)
        return False

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not mesh_objs:
        return False

    # Anti V/Tri=3: mesh flat (sem NORMAL no ficheiro) → exporter per-corner.
    smooth_shade_scene(mesh_objs)

    has_uv = False
    for m in mesh_objs:
        if m.data.uv_layers:
            has_uv = True
            try:
                m.data.calc_tangents()
            except Exception as exc:
                log.debug("calc_tangents falhou em %s: %s", m.name, exc)

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
        "export_tangents": has_uv,
        "export_texcoords": True,
        "export_materials": "EXPORT",
        "export_image_format": "AUTO",
        "export_animations": bool(arm_objs),
        "export_skins": bool(arm_objs),
    }
    try:
        from aigamekit_shared.bpy_mesh import gltf_export_supports_meshopt

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
    return has_uv


def _apply_meshopt_bpy(glb_in: Path, glb_out: Path) -> tuple[bool, str]:
    """Re-import + re-export com meshopt nativo (bpy 5.2+).

    Evitar quando o input já tem KTX2 — o roundtrip bpy pode re-encodar imagens.
    """
    try:
        import bpy

        from aigamekit_shared.bpy_mesh import (
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
        from aigamekit_shared.bpy_mesh import import_gltf

        import_gltf(glb_in)
    except Exception as exc:
        return False, f"import falhou: {exc}"

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not mesh_objs:
        return False, "sem meshes"

    from aigamekit_shared.bpy_mesh import smooth_shade_scene

    smooth_shade_scene(mesh_objs)
    for m in mesh_objs:
        if m.data.uv_layers:
            with contextlib.suppress(Exception):
                m.data.calc_tangents()

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
    apply_meshopt: bool = True,
    uastc_level: int = 2,
    uastc_rdo: float = 1.0,
    meshopt_level: str = "high",
) -> FinishResult:
    """Pipeline padrão de finalização de GLB.

    Ordem fixa: shade+tangents → dedup → prune(--keep-attributes) → uastc →
    meshopt. Cada passo é opcional. Quando ``glb_in == glb_out``, escreve
    in-place após pipeline em tempdir.

    ``prune`` **tem** de usar ``--keep-attributes true`` — sem isso o
    gltf-transform remove TANGENT (medido: goblin_lod0_animated).

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

        attrs_before = _glb_vertex_attrs(current)
        had_tangent = "TANGENT" in attrs_before

        if apply_tangents:
            # bpy 5.x sem KHR_texture_basisu falha o import de KTX2
            # ("Extension KHR_texture_basisu is not available") e o passe de
            # shade/tangents fica no-op — V/Tri≈3 e sem TANGENT. Descomprimir
            # para PNG antes; uastc mais abaixo volta a KTX2.
            if _glb_has_ktx2(current):
                staged = tmp / "ktxdecompress.glb"
                ok_d, err_d = _run_gltf_transform("ktxdecompress", current, staged)
                if ok_d:
                    current = staged
                    log.info("gltf_finish: KTX2→PNG (ktxdecompress) antes de shade/tangents")
                else:
                    log.warning("gltf_finish: ktxdecompress falhou — %s", err_d)

            # Sempre shade+export: import flat (sem NORMAL) ou V/Tri≈3 legado
            # precisam do passe; smooth_shade_scene é idempotente em mesh boa.
            ok = _recalc_tangents_inplace(current)
            res.tangents_added = ok or "TANGENT" in _glb_vertex_attrs(current)

        steps: list[tuple[str, str, list[str] | None]] = []
        if apply_dedup:
            steps.append(("dedup", "dedup", None))
        if apply_prune:
            # keep-attributes: sem isto prune apaga TANGENT (gltf-transform 4.x).
            steps.append(("prune", "prune", ["--keep-attributes", "true"]))
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
                # gltf-transform meshopt aplica KHR_mesh_quantization com volume
                # "mesh" e recentra o bbox (ex. Y∈[-1,1]). Em GLBs skinned isso
                # desloca a origem para o centro do personagem — pés deixam de
                # ficar em y=0 (medido: bandit_lod0 vs bandit_lod0_animated).
                if _glb_has_skins(current):
                    # Assets rigados saem SEM meshopt e falham a regra
                    # `compression: meshopt` das rules do GameAssets — é
                    # deliberado, os dois backends estão bloqueados:
                    #   * bpy recusa depois do KTX2 (re-encode de imagens) e o
                    #     uastc corre antes; aplicá-lo antes do uastc também não
                    #     serve — o uastc descomprime o EXT_meshopt_compression;
                    #   * gltf-transform recentra o bbox e tira os pés do y=0.
                    # `--quantization-volume scene` foi testado (2026-07-26) e
                    # NÃO evita o recentrar: npc_scout_lod0 saiu com
                    # `origin.y_min: -1.0` (lod0.yaml apanha, lod1.yaml não).
                    # Entregável 3x maior > entregável com os pés no ar.
                    log.warning(
                        "gltf_finish: meshopt gltf-transform omitido (GLB skinned) — "
                        "KHR_mesh_quantization desloca origem/pés (%s)",
                        bpy_err or "bpy indisponível",
                    )
                else:
                    ok, err = _run_gltf_transform("meshopt", current, staged, ["--level", meshopt_level])
                    if ok:
                        current = staged
                        res.meshopt_applied = True
                        res.meshopt_backend = "gltf-transform"
                    else:
                        log.warning("gltf_finish: passo meshopt falhou — %s", err)

        attrs_after = _glb_vertex_attrs(current)
        # Restaurar se prune/meshopt (legado / flags erradas) mataram attrs.
        # Só quando ainda não há KTX2 — roundtrip bpy após uastc re-encoda imagens.
        need_n = apply_tangents and "NORMAL" not in attrs_after
        need_t = (
            apply_tangents
            and "TANGENT" not in attrs_after
            and (had_tangent or res.tangents_added or "TEXCOORD_0" in attrs_after)
        )
        if (need_n or need_t) and not _glb_has_ktx2(current):
            log.warning(
                "gltf_finish: attrs perdidos após transform (N=%s T=%s) — a restaurar",
                "NORMAL" in attrs_after,
                "TANGENT" in attrs_after,
            )
            if _recalc_tangents_inplace(current):
                res.tangents_added = True
        elif need_t and _glb_has_ktx2(current):
            log.warning(
                "gltf_finish: TANGENT ausente após KTX2 — não dá para restaurar sem re-encode; "
                "verifique prune --keep-attributes"
            )

        shutil.copy2(current, glb_out)

    return res
