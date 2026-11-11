"""Multiview Hi3D: vistas Text2D, cache de vistas e sidecar do refine.

O Hi3D v1.0 é mono-imagem por passe. O "multiview" local é uma orquestração:
uma imagem por vista (frontal + 5 geradas com a frontal como referência) e um
passe Hi3D encadeado por imagem — a saída de cada passe é o coarse do seguinte.

Este módulo concentra o que o ``resume``/``batch`` (geração das vistas em
falta) e o stage de refine (resolução, cache e invalidação) têm de partilhar.
Nada aqui altera o contrato do Hi3D.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manifest import ManifestRow
    from .profile import GameProfile

log = logging.getLogger(__name__)

#: Categorias em que o Hi3D muda a pose (o refine é saltado em pipeline.py).
_SKIP_CATEGORIES = frozenset({"humanoid", "creature"})


def _paths():
    """Import tardio de ``paths`` (``paths`` importa ``profile``; evita ciclo)."""
    from . import paths as P

    return P


def _profile_refine(profile: GameProfile):
    from .profile import RefineProfile

    return profile.refine or RefineProfile()


def row_skips_refine(row: ManifestRow | None) -> bool:
    """Mesmos gates de pose do stage de refine (riggados/humanoid/creature)."""
    if row is None:
        return False
    if bool(getattr(row, "generate_rig", False)):
        return True
    cat = str(getattr(row, "category", None) or getattr(row, "kind", None) or "").strip().lower()
    return cat in _SKIP_CATEGORIES


def configured_views(profile: GameProfile, row: ManifestRow | None) -> tuple[str, ...]:
    """Ordem de vistas configurada (default: só a frontal)."""
    rp = _profile_refine(profile)
    rr = getattr(row, "refine", None) if row is not None else None
    views = rr.views if rr is not None and rr.views is not None else rp.views
    return tuple(views) if views else ("front",)


def views_to_generate(profile: GameProfile, row: ManifestRow | None) -> tuple[str, ...]:
    """Vistas extra (sem a frontal) que vale a pena gerar por Text2D.

    Devolve vazio quando o refine está desligado, a row o desliga ou a categoria
    é saltada — nesses casos um passe multiview nunca correria.
    """
    if row is None or row_skips_refine(row):
        return ()
    rp = _profile_refine(profile)
    if not rp.enabled:
        return ()
    rr = getattr(row, "refine", None)
    if rr is not None and rr.enabled is False:
        return ()
    return tuple(v for v in configured_views(profile, row) if v != "front")


def images_root_for(mesh_final: Path, profile: GameProfile) -> tuple[Path, Path]:
    """``(raiz do layout split, pasta meshes/)`` para a mesh canónica.

    Espelha a lógica de ``_reference_image_for``: o ``meshes_subdir`` do perfil
    pode ter vários níveis (``meshes/characters``), e a raiz é o que fica acima.
    """
    P = _paths()
    canon = P._canonical_mesh_final(mesh_final)
    meshes_dir = canon.parent
    meshes_sub = Path(getattr(profile, "meshes_subdir", "meshes") or "meshes").parts
    root = meshes_dir.parent
    if meshes_sub and meshes_dir.parts[-len(meshes_sub) :] == meshes_sub:
        root = Path(*meshes_dir.parts[: -len(meshes_sub)])
    return root, meshes_dir


def _image_ext(profile: GameProfile, front: Path | None = None) -> str:
    if front is not None and front.suffix:
        return front.suffix
    ext = str(getattr(profile, "image_ext", "png") or "png").lstrip(".")
    return f".{ext}"


def view_image_path(
    mesh_final: Path,
    profile: GameProfile,
    view: str,
    *,
    front: Path | None = None,
) -> Path:
    """Destino canónico de uma vista: ao lado da frontal e com o mesmo sufixo."""
    P = _paths()
    base = P._base_stem(Path(mesh_final).stem)
    ext = _image_ext(profile, front)
    if front is not None:
        return front.parent / f"{base}_{view}{ext}"
    root, _meshes_dir = images_root_for(mesh_final, profile)
    imgs_sub = str(getattr(profile, "images_subdir", "images") or "images")
    return root / imgs_sub / f"{base}_{view}{ext}"


def resolve_view_image(
    mesh_final: Path,
    profile: GameProfile,
    view: str,
    *,
    front: Path | None = None,
) -> Path | None:
    """Primeira vista existente para ``view`` (escrita nova ou layouts antigos)."""
    P = _paths()
    base = P._base_stem(Path(mesh_final).stem)
    canon = P._canonical_mesh_final(mesh_final)
    meshes_dir = canon.parent
    root, _ = images_root_for(mesh_final, profile)
    imgs_sub = str(getattr(profile, "images_subdir", "images") or "images")
    exts: list[str] = []
    for candidate_ext in (_image_ext(profile, front), ".png", ".jpg", ".jpeg"):
        if candidate_ext and candidate_ext not in exts:
            exts.append(candidate_ext)
    candidates: list[Path] = []
    if front is not None:
        for ext in exts:
            candidates.append(front.parent / f"{base}_{view}{ext}")
    for ext in exts:
        candidates.append(root / imgs_sub / f"{base}_{view}{ext}")
        # Pack do layout split (``meshes/<pack>`` → ``images/<pack>``).
        candidates.append(meshes_dir.parent.parent / imgs_sub / meshes_dir.name / f"{base}_{view}{ext}")
        candidates.append(meshes_dir / f"{base}_{view}{ext}")
        candidates.append(P._intermediate_dir(canon) / f"{base}_{view}{ext}")
    for cand in candidates:
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    return None


def _stamp(path: Path) -> dict[str, Any]:
    # resolve() normaliza o symlink do checkout (GitClones -> /media/...);
    # sem isto um path relativo/symlink e o sidecar absoluto nunca batiam.
    resolved = path.resolve()
    try:
        st = path.stat()
    except OSError:
        return {"path": str(resolved), "missing": True}
    return {"path": str(resolved), "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _stamp_matches(expected: dict[str, Any], actual: Any) -> bool:
    if not isinstance(actual, dict):
        return False
    for key in ("path", "size", "mtime_ns"):
        if key in expected and actual.get(key) != expected.get(key):
            return False
    return not actual.get("missing")


def _entry_file_ok(entry: Any) -> bool:
    """Entrada de sidecar só vale se o ficheiro referido ainda existe e tem bytes."""
    if not isinstance(entry, dict):
        return False
    raw = entry.get("path")
    if not raw:
        return False
    try:
        target = Path(str(raw))
        return target.is_file() and target.stat().st_size > 0
    except OSError:
        return False


def _merge_sidecar_views(
    current: dict[str, Any],
    updates: dict[str, dict[str, Any]],
    keep: set[str],
    *,
    mesh_final: Path,
    front: Path,
    params: dict[str, Any],
) -> None:
    """Funde as vistas atuais, mantendo só as configuradas e com ficheiro.

    Uma vista que falhou (o ficheiro stale foi apagado antes do job) não pode
    continuar no sidecar: ``refine_stale``/``view_is_current`` viam uma entrada
    sem ficheiro e o próximo resume repetia o refine sem saber porquê.
    """
    merged = dict(current.get("views") or {})
    for view, entry in updates.items():
        path = Path(str(entry.get("path") or ""))
        if path.is_file() and path.stat().st_size > 0:
            merged[view] = {**_stamp(path), **entry}
    merged = {v: e for v, e in merged.items() if v in keep and _entry_file_ok(e)}
    write_views_sidecar(
        mesh_final,
        front=front,
        params=params,
        entries=merged,
    )


def _prompt_digest(prompt: str) -> str:
    import hashlib

    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


# --- Sidecar das vistas -----------------------------------------------------


def views_sidecar_path(mesh_final: Path) -> Path:
    P = _paths()
    canon = P._canonical_mesh_final(mesh_final)
    base = P._base_stem(canon.stem)
    return P._intermediate_dir(canon) / f"{base}_views.json"


def load_views_sidecar(mesh_final: Path) -> dict[str, Any]:
    path = views_sidecar_path(mesh_final)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_tmp_path(path: Path) -> Path:
    """Nome de temporário único (pid+thread+uuid) para escritas concorrentes.

    Um ``<nome>.tmp`` fixo fazia dois escritores (batch + resume, ou o pool de
    prefetch) intercalarem bytes no mesmo ficheiro e o ``replace`` final podia
    apanhar JSON a meio.
    """
    return path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _atomic_tmp_path(path)
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def write_views_sidecar(
    mesh_final: Path,
    *,
    front: Path,
    params: dict[str, Any],
    entries: dict[str, dict[str, Any]],
) -> None:
    """Grava o estado das vistas geradas (``entries`` só com as bem-sucedidas)."""
    payload = {
        "version": 1,
        "front": _stamp(front),
        "params": params,
        "views": entries,
    }
    _write_json_atomic(views_sidecar_path(mesh_final), payload)


def view_is_current(
    mesh_final: Path,
    view: str,
    *,
    front: Path,
    path: Path,
    prompt: str,
    seed: int | None,
    params: dict[str, Any],
) -> bool:
    """Vista reutilizável? mtime ≥ frontal e sidecar compatível (se existir)."""
    try:
        view_st, front_st = path.stat(), front.stat()
    except OSError:
        return False
    if view_st.st_size <= 0 or view_st.st_mtime_ns < front_st.st_mtime_ns:
        return False
    sidecar = load_views_sidecar(mesh_final)
    if not sidecar:
        return True
    if not _stamp_matches(_stamp(front), sidecar.get("front")):
        return False
    if params and sidecar.get("params") != params:
        return False
    entry = (sidecar.get("views") or {}).get(view)
    if not isinstance(entry, dict):
        return False
    if entry.get("prompt_sha") != _prompt_digest(prompt):
        return False
    if entry.get("seed") != seed:
        return False
    return _stamp_matches(_stamp(path), entry)


# --- Geração das vistas em falta -------------------------------------------


def _text2d_fallback_manifest(jobs: list[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def ensure_multiview_images_for_rows(
    rows: Iterable[Any],
    *,
    profile: GameProfile,
    preset: dict[str, Any],
    manifest_dir: Path,
    **kwargs: Any,
) -> dict[str, int]:
    """Atalho para ``resume``/``batch``: resolve front/mesh por row e gera vistas."""
    from .paths import _paths_for_row_manifest

    entries: list[dict[str, Any]] = []
    for row in rows:
        if not bool(getattr(row, "generate_3d", False)):
            continue
        img, mesh = _paths_for_row_manifest(profile, manifest_dir, row)
        entries.append({"row": row, "mesh_final": mesh, "front": img})
    return ensure_multiview_images(
        entries,
        profile=profile,
        preset=preset,
        manifest_dir=manifest_dir,
        **kwargs,
    )


def ensure_multiview_images(
    entries: Iterable[dict[str, Any]],
    *,
    profile: GameProfile,
    preset: dict[str, Any],
    manifest_dir: Path,
    text2d_bin: str | None,
    child_env: dict[str, str] | None = None,
    no_vramd: bool = False,
    vramd_stream: bool = False,
    gpu_ids: list[int] | None = None,
    force: bool = False,
    seed_for_row: Callable[[Any], int | None] | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    quality: str | None = None,
    on_progress: Callable[[Any], None] | None = None,
) -> dict[str, int]:
    """Gera por Text2D as vistas em falta para as rows com multiview.

    ``entries``: iterável de ``{"row", "mesh_final", "front"}`` (a frontal já
    tem de existir — esta fase corre depois da fase de imagens). Falhas são
    não fatais: o refine usa as vistas que existirem e o sidecar registra o
    resultado para o próximo resume tentar de novo.
    """
    from .helpers import _append_text2d_profile_args, _seed_for_manifest_row
    from .prompt_builder import build_view_prompt
    from .runner import run_cmd
    from .vramd_batch import run_text2d_wave_or_fallback

    t2 = getattr(profile, "text2d", None)
    w = int(width if width is not None else (getattr(t2, "width", None) or 768))
    h = int(height if height is not None else (getattr(t2, "height", None) or 768))
    st = int(steps if steps is not None else (getattr(t2, "steps", None) or 16))
    gd = float(guidance if guidance is not None else (getattr(t2, "guidance_scale", None) or 1.0))
    params = {
        "width": w,
        "height": h,
        "steps": st,
        "guidance": gd,
        "quality": quality or getattr(profile, "generation", None),
    }

    jobs: list[dict[str, Any]] = []
    sidecars: dict[Path, dict[str, Any]] = {}
    reused = 0
    seed_fn = seed_for_row or (lambda row: _seed_for_manifest_row(profile, row))

    for entry in entries:
        row = entry.get("row")
        front = entry.get("front")
        mesh_final = entry.get("mesh_final")
        if row is None or front is None or mesh_final is None:
            continue
        front = Path(front)
        if not (front.is_file() and front.stat().st_size > 0):
            continue
        views = views_to_generate(profile, row)
        if not views:
            continue
        seed = seed_fn(row)
        side = sidecars.setdefault(
            Path(mesh_final),
            {"front": front, "views": {}, "params": params, "keep": set(views)},
        )
        for view in views:
            path = view_image_path(Path(mesh_final), profile, view, front=front)
            prompt = build_view_prompt(profile, preset, row, view)
            if not force and view_is_current(
                Path(mesh_final),
                view,
                front=front,
                path=path,
                prompt=prompt,
                seed=seed,
                params=params,
            ):
                reused += 1
                side["views"][view] = {
                    "path": str(path),
                    "prompt_sha": _prompt_digest(prompt),
                    "seed": seed,
                }
                continue
            # O worker vramd e o generate-batch saltam outputs existentes; sem
            # apagar, uma vista stale (ou um force) nunca é reescrita.
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            jobs.append(
                {
                    "id": f"{row.id}__{view}",
                    "prompt": prompt,
                    "output": str(path),
                    "refs": [str(front)],
                    "seed": seed,
                    "_row": row,
                    "_view": view,
                    "_mesh_final": Path(mesh_final),
                    "_path": path,
                    "_prompt_sha": _prompt_digest(prompt),
                }
            )

    if not jobs:
        # Nada a gerar: atualiza stamps das vistas reutilizadas.
        for mesh_final, side in sidecars.items():
            if side["views"]:
                _merge_sidecar_views(
                    load_views_sidecar(mesh_final),
                    side["views"],
                    side["keep"],
                    mesh_final=mesh_final,
                    front=side["front"],
                    params=params,
                )
        return {"generated": 0, "reused": reused, "failed": 0}

    payload_jobs = [{k: v for k, v in job.items() if not k.startswith("_")} for job in jobs]
    results = run_text2d_wave_or_fallback(
        payload_jobs,
        manifest_dir=manifest_dir,
        no_vramd=no_vramd,
        vramd_stream=vramd_stream,
        gpu_ids=gpu_ids,
        width=w,
        height=h,
        steps=st,
        guidance=gd,
        quality=quality,
        on_progress=on_progress,
    )
    if results is None and text2d_bin is None:
        log.warning("multiview: text2d ausente — vistas não geradas")
        return {"generated": 0, "reused": reused, "failed": len(jobs)}
    if results is None:
        # Fallback subprocess: generate-batch já lê ``refs`` por item.
        assert text2d_bin is not None
        manifest_path = _text2d_fallback_manifest(
            payload_jobs,
            manifest_dir / ".gameassets_work" / "text2d_views_manifest.json",
        )
        # --no-compile: o generate-batch in-process compila o transformer e o
        # pipeline modular rebenta em `len(OptimizedModule)`; o caminho vramd
        # já corre sem compile.
        argv = [text2d_bin, "generate-batch", str(manifest_path), "--force", "--no-compile"]
        _append_text2d_profile_args(profile, argv)
        # Os valores resolvidos ganham do perfil (o fallback tem de espelhar a
        # wave: um override 512/fast não pode cair para 768/medium).
        if quality:
            argv.extend(["--quality", str(quality)])
        argv.extend(["-W", str(w), "-H", str(h), "-s", str(st), "-g", str(gd)])
        if gpu_ids:
            argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
        if no_vramd:
            argv.append("--no-vramd")
        r = run_cmd(argv, extra_env=child_env or {}, cwd=manifest_dir)
        if r.returncode != 0:
            log.warning("multiview: text2d generate-batch falhou (%s)", r.returncode)
        results = []

    by_id = {str(x.get("id")): x for x in (results or []) if isinstance(x, dict)}
    generated = 0
    failed = 0
    for job in jobs:
        out = Path(job["_path"])
        res = by_id.get(str(job["id"]))
        if res is not None:
            # "skipped" com ficheiro existente era o worker a não reescrever
            # uma vista stale; só "ok" conta como vista nova.
            ok = res.get("status") == "ok" and out.is_file() and out.stat().st_size > 0
        else:
            # Fallback subprocess: sem resultados parseados, vale o ficheiro
            # (o generate-batch corre com --force).
            ok = out.is_file() and out.stat().st_size > 0
        mesh_final = Path(job["_mesh_final"])
        side = sidecars[mesh_final]
        if ok:
            generated += 1
            side["views"][job["_view"]] = {
                "path": str(out),
                "prompt_sha": job["_prompt_sha"],
                "seed": job.get("seed"),
            }
        else:
            failed += 1
            log.warning("multiview: vista %s de %s falhou", job["_view"], job["_row"].id)

    for mesh_final, side in sidecars.items():
        # Só mantém vistas ainda configuradas para esta row — e com ficheiro
        # (uma geração falhada apagou a vista stale antes do job).
        _merge_sidecar_views(
            load_views_sidecar(mesh_final),
            side["views"],
            side["keep"],
            mesh_final=mesh_final,
            front=side["front"],
            params=params,
        )

    return {"generated": generated, "reused": reused, "failed": failed}


# --- Sidecar do refine ------------------------------------------------------


def refine_sidecar_path(mesh_final: Path) -> Path:
    P = _paths()
    canon = P._canonical_mesh_final(mesh_final)
    base = P._base_stem(canon.stem)
    return P._intermediate_dir(canon) / f"{base}_refined.glb.multiview.json"


def load_refine_sidecar(mesh_final: Path) -> dict[str, Any]:
    path = refine_sidecar_path(mesh_final)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_refine_sidecar(
    mesh_final: Path,
    *,
    views: list[tuple[str, Path]],
    clean_path: Path,
    input_path: Path,
    params: dict[str, Any],
) -> None:
    payload = {
        "version": 1,
        "views": [name for name, _p in views],
        "images": {name: _stamp(path) for name, path in views},
        "clean": _stamp(clean_path),
        "input": _stamp(input_path),
        "params": params,
    }
    _write_json_atomic(refine_sidecar_path(mesh_final), payload)


def refine_cache_ok(
    mesh_final: Path,
    *,
    views: list[tuple[str, Path]],
    clean_path: Path,
    input_path: Path | None = None,
    params: dict[str, Any],
) -> bool:
    """True se o ``_refined.glb`` atual corresponde às vistas/params pedidos."""
    sidecar = load_refine_sidecar(mesh_final)
    if not sidecar or int(sidecar.get("version") or 0) != 1:
        return False
    if list(sidecar.get("views") or []) != [name for name, _p in views]:
        return False
    images = sidecar.get("images") or {}
    for name, path in views:
        if not _stamp_matches(_stamp(path), images.get(name)):
            return False
    if not _stamp_matches(_stamp(clean_path), sidecar.get("clean")):
        return False
    if input_path is not None and not _stamp_matches(_stamp(input_path), sidecar.get("input")):
        return False
    actual_params = sidecar.get("params") or {}
    return all(not (key in actual_params and actual_params[key] != value) for key, value in params.items())


def _expected_refine_params(profile: GameProfile, row: ManifestRow | None) -> dict[str, Any]:
    from .helpers import _seed_for_row

    rp = _profile_refine(profile)
    seed = rp.seed
    if seed is None and row is not None:
        seed = getattr(row, "seed", None)
    if seed is None:
        seed = _seed_for_row(profile, row.id if row is not None else "")
    return {
        "resolution": rp.resolution,
        "steps": rp.steps,
        "seed": seed,
        "model": rp.model,
        "remove_background": rp.remove_background,
    }


def refine_stale(
    profile: GameProfile,
    row: ManifestRow | None,
    mesh_final: Path,
    manifest_dir: Path | None = None,
    *,
    front: Path | None = None,
) -> bool:
    """Multiview ativo e cache do refine ausente/desatualizado?

    Só interessa quando há mais de uma vista: em mono-vista o reuso legacy por
    mtime continua a mandar (não re-refinar o pool inteiro). Uma vista em falta
    também conta como stale — o resume volta a tentar gerá-la e re-refina.
    """
    del manifest_dir  # reservado para resolução futura de layouts
    views = configured_views(profile, row)
    if len(views) <= 1:
        return False
    if row_skips_refine(row):
        return False
    rp = _profile_refine(profile)
    if not rp.enabled:
        return False
    rr = getattr(row, "refine", None) if row is not None else None
    if rr is not None and rr.enabled is False:
        return False

    resolved: list[tuple[str, Path]] = []
    for view in views:
        if view == "front":
            path = front
            if path is None:
                from .pipeline import _reference_image_for

                path = _reference_image_for(mesh_final, profile)
        else:
            path = resolve_view_image(mesh_final, profile, view, front=front)
        if path is None or not (path.is_file() and path.stat().st_size > 0):
            return True
        resolved.append((view, path))

    P = _paths()
    clean = P._clean_existing(mesh_final)
    if clean is None:
        return True
    return not refine_cache_ok(
        mesh_final,
        views=resolved,
        clean_path=clean,
        params=_expected_refine_params(profile, row),
    )
