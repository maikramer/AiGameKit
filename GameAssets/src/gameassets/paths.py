"""Path helpers, classification, install helpers."""

from __future__ import annotations

import contextlib
import logging
import shutil
from pathlib import Path

from .manifest import ManifestRow
from .profile import GameProfile

log = logging.getLogger(__name__)

_ROW_DONE = "done"
_ROW_NEED_IMAGE = "need_image"
_ROW_NEED_SHAPE = "need_shape"
_ROW_NEED_PAINT = "need_paint"
_ROW_NEED_RIG = "need_rig"
_ROW_NEED_ANIMATE = "need_animate"
_ROW_NEED_LOD = "need_lod"
_ROW_NEED_COLLISION = "need_collision"

# Round 2/3 - checkpoints do master pipeline.
_ROW_NEED_TOPOLOGY_FIX = "need_topology_fix"  # tem _shape, falta _clean
_ROW_NEED_BAKE_MASTER = "need_bake_master"  # tem _painted+_clean, falta _lod0
_ROW_NEED_LOD_GEN = "need_lod_gen"  # tem _lod0, faltam _lod1/_lod2 (ou ladder sem skin/clips)
_ROW_NEED_VALIDATE = "need_validate"  # tudo gerado, falta validação
# Round 3: rig sobre painted -> animate x1 -> ladder sobre animated/rigged.
# Reutiliza _ROW_NEED_RIG / _ROW_NEED_ANIMATE (mesma semântica do classificador
# legacy). Os estados antigos need_rig_hi / need_transfer / need_animate_lod
# deixaram de existir com o fim do rigged_hi e do transfer-weights por LOD.


def _glb_has_base_color(path: Path) -> bool:
    """True se o GLB tem material com ``baseColorTexture`` (paint presente)."""
    try:
        import json
        import struct

        with open(path, "rb") as f:
            if f.read(4) != b"glTF":
                return False
            f.read(8)
            json_len = struct.unpack("<I", f.read(4))[0]
            f.read(4)
            j = json.loads(f.read(json_len))
        for m in j.get("materials") or []:
            pbr = m.get("pbrMetallicRoughness") or {}
            if "baseColorTexture" in pbr:
                return True
        return False
    except Exception:
        return False


def _glb_json(path: Path) -> dict | None:
    try:
        import json
        import struct

        with open(path, "rb") as f:
            if f.read(4) != b"glTF":
                return None
            f.read(8)
            json_len = struct.unpack("<I", f.read(4))[0]
            f.read(4)
            return json.loads(f.read(json_len))
    except Exception:
        return None


def _glb_is_promoted_animated(path: Path) -> bool:
    """True se ``lod0`` já é o entregável animado (skin+clips+paint).

    Após Stage 9.5, ``*_lod*_rigged`` / ``*_lod*_animated`` vão para
    ``_intermediate/`` - o classificador não pode exigir esses ficheiros
    em ``meshes/`` ou resume fica preso em ``need_transfer`` para sempre.
    """
    j = _glb_json(path)
    if not j:
        return False
    if not (j.get("skins") and j.get("animations")):
        return False
    if not any("skin" in n for n in (j.get("nodes") or [])):
        return False
    for m in j.get("materials") or []:
        pbr = m.get("pbrMetallicRoughness") or {}
        if "baseColorTexture" in pbr:
            return True
    return False


def _glb_is_promoted_rigged(path: Path) -> bool:
    """True se ``lod0`` já é o entregável rigged (skin+paint, sem exigir clips)."""
    j = _glb_json(path)
    if not j:
        return False
    if not (j.get("skins") and any("skin" in n for n in (j.get("nodes") or []))):
        return False
    for m in j.get("materials") or []:
        pbr = m.get("pbrMetallicRoughness") or {}
        if "baseColorTexture" in pbr:
            return True
    return False


def _paths_for_row(profile: GameProfile, row: ManifestRow) -> tuple[Path, Path]:
    root = Path(profile.output_dir)
    ext = profile.image_ext
    rid = row.id
    if profile.path_layout == "flat":
        parts = rid.split("/")
        if len(parts) >= 2:
            sub = Path(*parts[:-1])
            base = parts[-1]
            dir_ = root / sub
        else:
            dir_ = root
            base = rid
        img = dir_ / f"{base}.{ext}"
        mesh = dir_ / f"{base}.glb"
    else:
        img = root / profile.images_subdir / f"{rid}.{ext}"
        mesh = root / profile.meshes_subdir / f"{rid}.glb"
    return img, mesh


_STEM_SUFFIXES: tuple[str, ...] = (
    # Compostos primeiro (mais longos).
    "_rigged_animated",
    "_lod0_animated",
    "_lod1_animated",
    "_lod2_animated",
    "_lod0_painted",
    "_lod1_painted",
    "_lod2_painted",
    "_lod0_rigged",
    "_lod1_rigged",
    "_lod2_rigged",
    "_rigged_hi",
    "_to_paint",
    "_painted",
    "_shape",
    "_clean",
    "_rigged",
    "_segmented",
    "_collision",
    "_animated",
    "_lod0",
    "_lod1",
    "_lod2",
)


def _base_stem(name: str) -> str:
    """Strip known suffixes from a stem like 'wooden_crate_painted' -> 'wooden_crate'.

    Itera até estabilizar — ``foo_lod0_painted`` → ``foo`` (não ``foo_lod0``).
    """
    out = name
    changed = True
    while changed:
        changed = False
        for sfx in _STEM_SUFFIXES:
            if out.endswith(sfx):
                out = out[: -len(sfx)]
                changed = True
                break
    return out


def _rigging3d_output_path(mesh_final: Path, suffix: str) -> Path:
    """ex.: ``hero.glb`` + ``_rigged`` -> ``hero_rigged.glb``."""
    s = (suffix or "_rigged").strip()
    if s and not s.startswith("_"):
        s = f"_{s}"
    if not s:
        s = "_rigged"
    stem = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{stem}{s}.glb")


def _shell_path(path: Path) -> str:
    """Caminho normalizado para argv de subprocess (expande user, resolve)."""
    return str(path.expanduser().resolve())


def _animator3d_output_path(base_output: Path) -> Path:
    """ex.: ``hero_rigged.glb`` -> ``hero_rigged_animated.glb``."""
    stem = _base_stem(base_output.stem)
    return base_output.with_name(f"{stem}_rigged_animated.glb")


def publish_rigged_animated_alias(mesh_final: Path, lod0: Path) -> Path | None:
    """Deprecated no-op: entregável canónico é ``id_lod0.glb``.

    Mantido para imports legados. Preferir :func:`finalize_mesh_deliverables`.
    """
    _ = (mesh_final, lod0)
    return None


def archive_leftover_lod_rigged(mesh_final: Path) -> list[Path]:
    """Move ``id_lod*_rigged.glb`` órfãos em ``meshes/`` para ``_intermediate/``.

    Depois do promote animated, estes ficheiros não devem ficar no runtime
    (confundem handoff e aliases). Resume/re-rig parcial pode deixá-los sem
    skin - arquivar evita o jogo apontar para um rig morto.
    """
    base = _base_stem(mesh_final.stem)
    moved: list[Path] = []
    for leftover in sorted(mesh_final.parent.glob(f"{base}_lod*_rigged{mesh_final.suffix}")):
        if leftover.is_file():
            moved.append(move_to_intermediate(leftover, mesh_final))
    return moved


def archive_legacy_rig_intermediates(mesh_final: Path) -> list[Path]:
    """Arquiva intermediários do DAG pré-Round-3 que ficaram em ``meshes/``.

    O DAG antigo gerava ``_rigged_hi`` (rig sobre ``_clean`` HI, sem textura),
    ``_lodN_rigged``/``_lodN_animated`` (transfer-weights + game-pack por LOD)
    e ``_lodN_pre_promote`` (arquivos de promoção) — tudo em ``meshes/`` antes
    do move final. O DAG novo (rig sobre painted → animate x1 → ladder sobre o
    animated) não usa nenhum deles; ficam em ``_intermediate/`` para debug.

    Idempotente: corre no início de cada master pipeline; ficheiros já em
    ``_intermediate/`` são ignorados (glob só em ``meshes/``).
    """
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    suffix = mesh_final.suffix
    moved: list[Path] = []
    patterns = (
        f"{base}_rigged_hi{suffix}",
        f"{base}_lod*_rigged{suffix}",
        f"{base}_lod*_animated{suffix}",
        f"{base}_lod*_pre_promote{suffix}",
    )
    for pat in patterns:
        for leftover in sorted(mesh_final.parent.glob(pat)):
            if leftover.is_file():
                moved.append(move_to_intermediate(leftover, mesh_final))
    return moved


def finalize_mesh_deliverables(mesh_final: Path) -> list[Path]:
    """Deixa em ``meshes/`` ``id_lod{N}`` + ``id_collision`` (+ split/stump/top).

    ``mesh_final`` (``id.glb``) é âncora lógica de paths — não entregável.
    - Bare sem lod0 → rename ``id.glb`` → ``id_lod0.glb``.
    - Com lod0 → arquiva bare, ``*_rigged_animated``, ``*_rigged``, ``*_animated``.
    - ``*_split`` / ``*_stump`` / ``*_top`` permanecem em ``meshes/`` (árvores).
    """
    mesh_final = _canonical_mesh_final(mesh_final)
    lod0 = _lod_path(mesh_final, 0)
    moved: list[Path] = []
    suffix = mesh_final.suffix

    if mesh_final.is_file() and not lod0.is_file():
        try:
            if lod0.exists() or lod0.is_symlink():
                lod0.unlink()
        except OSError:
            pass
        try:
            mesh_final.rename(lod0)
        except OSError:
            shutil.copy2(mesh_final, lod0)
            with contextlib.suppress(OSError):
                mesh_final.unlink()
        moved.append(lod0)

    if not lod0.is_file():
        return moved

    def _same_as_lod0(p: Path) -> bool:
        try:
            return p.resolve() == lod0.resolve()
        except OSError:
            return False

    candidates = [
        mesh_final,
        _animator3d_output_path(mesh_final),
        _rigging3d_output_path(mesh_final, "_rigged"),
        mesh_final.with_name(f"{_base_stem(mesh_final.stem)}_animated{suffix}"),
    ]
    for cand in candidates:
        if cand.is_file() and not _same_as_lod0(cand):
            moved.append(move_to_intermediate(cand, mesh_final))
    return moved


def _canonical_mesh_final(mesh_path: Path) -> Path:
    """Resolve qualquer path da pipeline para a mesh de produção ``meshes/{id}.glb``.

    Aceita ``id.glb``, ``_intermediate/id_painted.glb``, ``id_lod0.glb``, etc.
    e devolve sempre ``<meshes_dir>/{id}.glb`` (fora de ``_intermediate/``).
    Sem isto, passar ``mesh_painted`` a ``run_master_pipeline`` fazia LOD0 /
    collision nascerem em ``_intermediate/`` e nunca chegarem a ``meshes/``.
    """
    p = Path(mesh_path)
    base = _base_stem(p.stem)
    parent = p.parent
    if parent.name == "_intermediate":
        parent = parent.parent
    return parent / f"{base}{p.suffix}"


def _intermediate_dir(mesh_final: Path) -> Path:
    """Pasta para artefactos descartáveis da pipeline (shape, clean, painted, rigged_hi).

    Convenção: ``<meshes_dir>/_intermediate/``. Não vai para o jogo.
    Idempotente: se ``mesh_final`` já estiver dentro de ``_intermediate/``,
    não aninha outro ``_intermediate`` (bug que criava ``_intermediate/_intermediate/``).
    """
    # Sempre ancorar no meshes/ canónico — nunca aninhar sob painted/lod paths.
    parent = _canonical_mesh_final(mesh_final).parent
    return parent / "_intermediate"


def _shape_path(mesh_final: Path) -> Path:
    """``id_shape.glb`` em ``_intermediate/`` - destino canónico desde Round 2.

    Antes existia ao lado da mesh canónica e era movido no fim da pipeline;
    isso fazia o ``resume`` e o ``batch`` perderem o ficheiro depois do
    move (procuravam só em ``meshes/``). Agora escrevemos directamente
    em ``_intermediate/`` desde o Stage 1 (``text3d generate``).

    ``_shape_existing`` mantém compatibilidade com layouts antigos
    (procura em ``meshes/`` primeiro, depois em ``_intermediate/``).

    Idempotente em relação a sufixos canónicos do stem.
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_shape{mesh_final.suffix}"


def _painted_path(mesh_final: Path) -> Path:
    """``id_painted.glb`` em ``_intermediate/`` - destino canónico desde Round 2.

    Mesma lógica de :func:`_shape_path`: escrever directamente em
    ``_intermediate/`` evita a corrida resume↔move ao fim do pipeline.
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_painted{mesh_final.suffix}"


def _clean_path(mesh_final: Path) -> Path:
    """``id_clean.glb`` em ``_intermediate/`` - output do Stage 2 (topology-fix).

    Sempre em ``_intermediate/`` desde a primeira escrita (artefacto novo).
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_clean{mesh_final.suffix}"


def _to_paint_path(mesh_final: Path) -> Path:
    """``id_to_paint.glb`` em ``_intermediate/`` - remesh pré-paint (orçamento atlas).

    High-poly ``_clean`` fica para bake-master/normais; o Paint3D corre sobre
    esta malha (faces ~ ``texture_size``) para unwrap/raster não explodirem.
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_to_paint{mesh_final.suffix}"


def _rigged_hi_path(mesh_final: Path) -> Path:
    """``id_rigged_hi.glb`` em ``_intermediate/`` - Stage 7 **legado** (rig sobre _clean).

    Round 3: o rig corre sobre o ``_painted`` e produz ``_rigged.glb``
    (:func:`_rigged_path`). ``_rigged_hi`` só existe em runs antigas —
    mantido para detecção/arquivo (resume migra sem reutilizar).
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_rigged_hi{mesh_final.suffix}"


def _rigged_path(mesh_final: Path) -> Path:
    """``id_rigged.glb`` em ``_intermediate/`` - rig sobre o ``_painted`` (Round 3).

    Substitui o antigo ``_rigged_hi`` (rig sobre ``_clean`` HI sem textura):
    o painted já tem a topologia final do LOD0, logo o rigged nasce com
    materiais/UVs e serve directamente de fonte para a ladder LOD.
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_rigged{mesh_final.suffix}"


def _animated_path(mesh_final: Path) -> Path:
    """``id_rigged_animated.glb`` em ``_intermediate/`` - game-pack sobre ``_rigged`` (Round 3).

    Um único game-pack por asset (antes: um por LOD). Fica arquivado em
    ``_intermediate/`` como fonte da ladder para resume — nunca vai para o
    jogo (o entregável animado é ``id_lod0.glb`` promovido).
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_rigged_animated{mesh_final.suffix}"


def _lod_path(mesh_final: Path, level: int) -> Path:
    """``id_lod{level}.glb`` em ``meshes/`` (final, vai para o jogo)."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_lod{level}{mesh_final.suffix}")


def _lod_rigged_path(mesh_final: Path, level: int) -> Path:
    """``id_lod{level}_rigged.glb`` em ``meshes/`` (pré-promote; depois archive)."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_lod{level}_rigged{mesh_final.suffix}")


def _lod_animated_path(mesh_final: Path, level: int) -> Path:
    """``id_lod{level}_animated.glb`` em ``meshes/``."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_lod{level}_animated{mesh_final.suffix}")


def _collision_path(mesh_final: Path) -> Path:
    """``id_collision.glb`` em ``meshes/`` (final)."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_collision{mesh_final.suffix}")


def _split_path(mesh_final: Path) -> Path:
    """``id_split.glb`` — composição multi-mesh Stump+Top (entregável)."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_split{mesh_final.suffix}")


def _stump_path(mesh_final: Path) -> Path:
    """``id_stump.glb`` — metade inferior do split-at-height."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_stump{mesh_final.suffix}")


def _top_path(mesh_final: Path) -> Path:
    """``id_top.glb`` — metade superior do split-at-height."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_top{mesh_final.suffix}")


def _precompute_path(mesh_final: Path) -> Path:
    """``id_precompute.json`` em ``meshes/`` — sidecar do colisor pré-calculado."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_precompute.json")


def _unsplit_lod0_path(mesh_final: Path) -> Path:
    """``_intermediate/id_lod0_unsplit.glb`` — LOD0 antes do split (árvores)."""
    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_lod0_unsplit{mesh_final.suffix}"


def move_to_intermediate(src: Path, mesh_final: Path) -> Path:
    """Move um intermediário (shape, painted, rigged_hi) para ``_intermediate/``.

    Idempotente: se o destino já existe, sobrescreve. Se ``src`` não existir,
    devolve sem erro.
    """
    if not src.is_file():
        return src
    dst_dir = _intermediate_dir(mesh_final)
    dst_dir.mkdir(parents=True, exist_ok=True)
    base = _base_stem(src.stem)
    suffix = src.stem[len(_base_stem(src.stem)) :]  # ex: "_shape", "_painted"
    if not suffix:
        suffix = ""
    dst = dst_dir / f"{base}{suffix}{src.suffix}"
    # Idempotente: se src já está no destino (resume), não faz nada.
    try:
        if dst.exists() and src.resolve() == dst.resolve():
            return dst
    except OSError:
        pass
    if dst.exists():
        with contextlib.suppress(OSError):
            dst.unlink()
    try:
        src.rename(dst)
    except OSError:
        # Cross-device fallback
        shutil.copy2(src, dst)
        with contextlib.suppress(OSError):
            src.unlink()
    return dst


def _valid_file(p: Path) -> bool:
    return p.is_file() and p.stat().st_size > 0


def _glb_has_geometry(p: Path, *, min_tris: int = 64) -> bool:
    """True se o GLB tem geometria útil (rejeita cleans vazios / quase-vazios).

    ``min_tris`` apanha colapsos parciais (ex. scorpion_clean com 13 faces) além
    dos stubs de 228 bytes sem primitives.
    """
    if not _valid_file(p):
        return False
    try:
        from aigamekit_shared.glb_verify import extract_glb_meta

        meta = extract_glb_meta(p)
        if meta.get("_error"):
            return False
        if int(meta.get("byte_size") or 0) < 200:
            return False
        verts = int(meta.get("vertex_count_total") or 0)
        tris = int(meta.get("triangle_count_total") or 0)
        return verts > 0 and tris >= int(min_tris)
    except Exception:
        return p.stat().st_size > 500


def _resolve_intermediate_or_main(canonical: Path, mesh_final: Path) -> Path | None:
    """Aceita o ficheiro no caminho canónico ou no legacy ``meshes/`` (compat).

    Desde Round 2 o caminho canónico de ``shape``/``painted``/``clean``/
    ``rigged_hi`` é ``meshes/_intermediate/``; para retro-compat também
    aceitamos a localização antiga (``meshes/<asset>_shape.glb`` etc.).
    Devolve o primeiro ficheiro válido encontrado ou ``None``.
    """
    if _valid_file(canonical):
        return canonical
    # Fallback para layouts antigos: <meshes>/<asset>_shape.glb.
    legacy = mesh_final.with_name(canonical.name)
    if _valid_file(legacy):
        return legacy
    # Fallback inverso (caso ``canonical`` ainda aponte para legacy).
    intermediate = _intermediate_dir(mesh_final) / canonical.name
    if _valid_file(intermediate):
        return intermediate
    return None


def _clean_existing(mesh_final: Path) -> Path | None:
    """Encontra o GLB ``_clean`` em ``meshes/`` ou ``_intermediate/``.

    Cleans vazios (topology-fix arrays colapsado) são ignorados para o
    resume voltar a correr topology-fix.
    """
    found = _resolve_intermediate_or_main(_clean_path(mesh_final), mesh_final)
    if found is None:
        return None
    if not _glb_has_geometry(found):
        log.warning("clean inválido/vazio ignorado (força re-topology-fix): %s", found)
        return None
    return found


def _to_paint_existing(mesh_final: Path) -> Path | None:
    """Encontra o GLB ``_to_paint`` em ``meshes/`` ou ``_intermediate/``."""
    return _resolve_intermediate_or_main(_to_paint_path(mesh_final), mesh_final)


def _rigged_hi_existing(mesh_final: Path) -> Path | None:
    """Encontra o GLB ``_rigged_hi`` em ``meshes/`` ou ``_intermediate/``."""
    return _resolve_intermediate_or_main(_rigged_hi_path(mesh_final), mesh_final)


def _rigged_existing(mesh_final: Path) -> Path | None:
    """Encontra o GLB ``_rigged`` (Round 3) em ``_intermediate/`` ou ``meshes/``."""
    return _resolve_intermediate_or_main(_rigged_path(mesh_final), mesh_final)


def _animated_existing(mesh_final: Path) -> Path | None:
    """Encontra o GLB ``_rigged_animated`` (Round 3) em ``_intermediate/`` ou ``meshes/``."""
    return _resolve_intermediate_or_main(_animated_path(mesh_final), mesh_final)


def _shape_existing(mesh_final: Path) -> Path | None:
    """Devolve o ``id_shape.glb`` existente em ``meshes/`` ou ``_intermediate/``."""
    return _resolve_intermediate_or_main(_shape_path(mesh_final), mesh_final)


def _painted_existing(mesh_final: Path) -> Path | None:
    """Devolve o ``id_painted.glb`` existente em ``meshes/`` ou ``_intermediate/``."""
    return _resolve_intermediate_or_main(_painted_path(mesh_final), mesh_final)


def _classify_row_state(
    *,
    img_final: Path,
    mesh_final: Path,
    rig_out: Path,
    anim_out: Path,
    want_texture: bool,
    wants_rig: bool,
    wants_animate: bool,
    wants_lod: bool = False,
    wants_collision: bool = False,
    lod0_path: Path | None = None,
    collision_path: Path | None = None,
) -> str:
    shape = _shape_path(mesh_final)
    painted = _painted_path(mesh_final)
    if not _valid_file(img_final):
        return _ROW_NEED_IMAGE
    final_exists = (_valid_file(painted) or _valid_file(mesh_final)) if want_texture else _valid_file(shape)

    if final_exists:
        if wants_rig and not _valid_file(rig_out):
            return _ROW_NEED_RIG
        if wants_rig and wants_animate and not _valid_file(anim_out):
            return _ROW_NEED_ANIMATE
        if wants_lod and lod0_path and not _valid_file(lod0_path):
            return _ROW_NEED_LOD
        if wants_collision and collision_path and not _valid_file(collision_path):
            return _ROW_NEED_COLLISION
        return _ROW_DONE
    if _valid_file(shape):
        return _ROW_NEED_PAINT if want_texture else _ROW_DONE
    return _ROW_NEED_SHAPE


def _classify_row_state_master(
    *,
    img_final: Path,
    mesh_final: Path,
    want_texture: bool,
    wants_rig: bool,
    wants_animate: bool,
    wants_lod: bool = True,
    wants_collision: bool = True,
    omni_stale: bool = False,
) -> str:
    """Classifica estado da row para o master pipeline (Round 3 DAG).

    Ordem de detecção espelha o DAG: image -> shape -> topology-fix (clean) ->
    paint -> rig (sobre painted) -> animate (x1) -> ladder lod0/1/2 (a partir
    do animated/rigged; estático: bake-master + lod do painted) -> validate.
    Devolve o primeiro estágio que ainda falta.

    ``omni_stale``: shape existe mas fingerprint Omni mudou -> ``need_shape``.
    """
    shape_any = _shape_existing(mesh_final)
    painted_any = _painted_existing(mesh_final)
    clean_any = _clean_existing(mesh_final)
    lod0 = _lod_path(mesh_final, 0)
    lod1 = _lod_path(mesh_final, 1)
    lod2 = _lod_path(mesh_final, 2)

    # PNG/textura é Stage 0 do DAG (text2d → i2m + paint). Em falta → sempre
    # ``need_image``, mesmo com shape/clean órfãos: senão resume saltava para
    # need_shape/topology e nunca agendava text2d (GLBs faltantes sim, PNGs não).
    if not _valid_file(img_final):
        return _ROW_NEED_IMAGE
    # Omni mudou ou shape ausente → regenerar. Clean órfão (sem shape) NÃO
    # salta Stage 1 — senão resume avança paint sobre mesh gorda/stale.
    if omni_stale or shape_any is None:
        return _ROW_NEED_SHAPE
    if clean_any is None:
        return _ROW_NEED_TOPOLOGY_FIX
    if want_texture and painted_any is None:
        return _ROW_NEED_PAINT

    promoted_anim = _valid_file(lod0) and _glb_is_promoted_animated(lod0)
    promoted_rig = _valid_file(lod0) and _glb_is_promoted_rigged(lod0)

    # Rig/animate: saltar só quando o entregável final já reflecte o estágio
    # (intermediários podem ter sido limpos pelo utilizador — não re-rigar).
    if wants_rig and not (promoted_anim or promoted_rig):
        if _rigged_existing(mesh_final) is None:
            return _ROW_NEED_RIG
        if wants_animate and _animated_existing(mesh_final) is None:
            return _ROW_NEED_ANIMATE

    if not _valid_file(lod0):
        return _ROW_NEED_BAKE_MASTER
    # lod0 branco sem baseColorTexture enquanto painted existe -> a ladder
    # não herdou o paint (bug histórico) — regenerar a partir da fonte.
    # Só flaga com GLB parseável (dummy/corrupto não bloqueia o resume).
    if painted_any is not None and _valid_file(lod0):
        lod0_json = _glb_json(lod0)
        if lod0_json is not None and lod0_json.get("meshes") and not _glb_has_base_color(lod0):
            return _ROW_NEED_LOD_GEN
    if wants_lod and (not _valid_file(lod1) or not _valid_file(lod2)):
        return _ROW_NEED_LOD_GEN
    if wants_rig:
        # Entregável tem de estar promovido ao nível esperado (animated >
        # rigged), em lod0 E na ladder — senão falta a promoção/ladder rigada.
        expect = _glb_is_promoted_animated if wants_animate else _glb_is_promoted_rigged
        if not expect(lod0):
            return _ROW_NEED_LOD_GEN
        if wants_lod and (not expect(lod1) or not expect(lod2)):
            return _ROW_NEED_LOD_GEN
    return _ROW_DONE


def _paths_for_row_manifest(
    profile: GameProfile,
    manifest_dir: Path,
    row: ManifestRow,
) -> tuple[Path, Path]:
    """
    PNG/GLB absolutos. O perfil usa muitas vezes output_dir: '.' - sem isto, caminhos relativos
    dependem do CWD do processo e o Text3D pode ler ficheiros errados (GPU "parada").
    """
    img, mesh = _paths_for_row(profile, row)
    img = (manifest_dir / img).resolve() if not img.is_absolute() else img.resolve()
    mesh = (manifest_dir / mesh).resolve() if not mesh.is_absolute() else mesh.resolve()
    return img, mesh


def _path_for_log(path: Path, manifest_dir: Path) -> str:
    """Caminho para run.jsonl: relativo ao manifest quando possível."""
    try:
        return str(path.resolve().relative_to(manifest_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def _install_file(src: Path, dst: Path) -> None:
    """Copia ficheiro para destino final (pasta do jogo); cria pais se necessário."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _install_maps_dir(src: Path, dst: Path) -> None:
    """Copia mapas PBR de uma pasta de trabalho (tmp) para a pasta final no jogo."""
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        if p.is_file():
            shutil.copy2(p, dst / p.name)
        elif p.is_dir():
            _install_maps_dir(p, dst / p.name)
