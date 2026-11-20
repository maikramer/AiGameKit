"""CLI machine-readable para o AiGameKit Studio (Rust/Bevy).

O Studio não replica os paths nem os gates do master pipeline: este módulo
expõe o estado real de um projeto de um único asset para o frontend, e a
invalidação em cascata usada pelos re-rolls por etapa.

Comandos:
    gameassets studio check --project <dir> [--json]
    gameassets studio invalidate --project <dir> --from <stage> [--json]

O layout de projeto assume ``game.yaml`` + ``manifest.yaml`` (ou ``.yml``)
na raiz do projeto. ``check`` é read-only e não toca em GPU; ``invalidate``
só remove ficheiros de artefactos.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
from pathlib import Path
from typing import Any

from .cli_rich import click

STUDIO_VERSION = 1
VALID_STAGES = (
    "image",
    "shape",
    "clean",
    "refine",
    "to_paint",
    "paint",
    "rig",
    "animate",
    "lod",
    "collision",
    "all",
)

# Artefactos que vivem fora dos paths canónicos do master pipeline.
_SIDECAR_SUFFIXES = (".omni.json", ".multiview.json")


def _project_profile_path(project_dir: Path) -> Path:
    return project_dir / "game.yaml"


def _project_manifest_path(project_dir: Path) -> Path:
    for name in ("manifest.yaml", "manifest.yml"):
        path = project_dir / name
        if path.is_file():
            return path
    return project_dir / "manifest.yaml"


def _load_context(project_dir: Path) -> tuple[Any, Any, dict[str, Any], Path, Path]:
    """Carrega profile, row e preset com a mesma resolução do batch."""
    from .helpers import _apply_manifest_config, _resolve_manifest_path
    from .manifest import load_manifest_bundle
    from .presets import get_preset, load_presets_bundle
    from .profile import apply_generation_profile, load_profile

    profile_path = _project_profile_path(project_dir)
    if not profile_path.is_file():
        raise ValueError(f"game.yaml ausente em {project_dir}")
    manifest_path = _resolve_manifest_path(_project_manifest_path(project_dir))
    if not manifest_path.is_file():
        raise ValueError(f"manifest.yaml ausente em {project_dir}")

    profile = load_profile(profile_path)
    if profile.generation:
        profile = apply_generation_profile(profile, profile.generation)
    rows, manifest_config = load_manifest_bundle(manifest_path)
    profile = _apply_manifest_config(profile, manifest_config)
    if len(rows) != 1:
        raise ValueError(f"o Studio espera um manifest com um único asset; encontrei {len(rows)}")
    preset = get_preset(load_presets_bundle(None), profile.style_preset)
    return profile, rows[0], preset, profile_path, manifest_path


def _artifact_paths(profile: Any, project_dir: Path, row: Any) -> dict[str, Path]:
    """Paths canónicos e resolvidos (legacy ``meshes/<id>_x.glb`` incluído)."""
    from .multiview import views_sidecar_path
    from .paths import (
        _animated_existing,
        _animated_path,
        _clean_existing,
        _clean_path,
        _collision_path,
        _intermediate_dir,
        _lod_path,
        _painted_existing,
        _painted_path,
        _paths_for_row_manifest,
        _precompute_path,
        _refined_existing,
        _refined_path,
        _rigged_existing,
        _rigged_path,
        _shape_existing,
        _shape_path,
        _split_path,
        _stump_path,
        _to_paint_existing,
        _to_paint_path,
        _top_path,
    )

    image, mesh = _paths_for_row_manifest(profile, project_dir, row)
    canonical = {
        "image": image,
        "mesh": mesh,
        "shape": _shape_path(mesh),
        "clean": _clean_path(mesh),
        "refined": _refined_path(mesh),
        "to_paint": _to_paint_path(mesh),
        "painted": _painted_path(mesh),
        "rigged": _rigged_path(mesh),
        "animated": _animated_path(mesh),
        "lod0": _lod_path(mesh, 0),
        "lod1": _lod_path(mesh, 1),
        "lod2": _lod_path(mesh, 2),
        "collision": _collision_path(mesh),
        "precompute": _precompute_path(mesh),
        "split": _split_path(mesh),
        "stump": _stump_path(mesh),
        "top": _top_path(mesh),
    }
    resolved = {
        "image": image,
        "mesh": mesh,
        "shape": _shape_existing(mesh) or canonical["shape"],
        "clean": _clean_existing(mesh) or canonical["clean"],
        "refined": _refined_existing(mesh) or canonical["refined"],
        "to_paint": _to_paint_existing(mesh) or canonical["to_paint"],
        "painted": _painted_existing(mesh) or canonical["painted"],
        "rigged": _rigged_existing(mesh) or canonical["rigged"],
        "animated": _animated_existing(mesh) or canonical["animated"],
        "lod0": canonical["lod0"],
        "lod1": canonical["lod1"],
        "lod2": canonical["lod2"],
        "collision": canonical["collision"],
        "precompute": canonical["precompute"],
        "split": canonical["split"],
        "stump": canonical["stump"],
        "top": canonical["top"],
    }
    # Sidecars derivados dos paths resolvidos.
    resolved["shape_omni"] = Path(str(resolved["shape"]) + ".omni.json")
    resolved["clean_omni"] = Path(str(resolved["clean"]) + ".omni.json")
    resolved["refined_multiview"] = Path(str(resolved["refined"]) + ".multiview.json")
    resolved["views"] = views_sidecar_path(mesh)
    resolved["intermediate_dir"] = _intermediate_dir(mesh)
    base = _base_stem(mesh.stem)
    resolved["stump_collision"] = mesh.with_name(f"{base}_stump_collision.glb")
    resolved["top_collision"] = mesh.with_name(f"{base}_top_collision.glb")
    return {**canonical, **{f"resolved_{k}": v for k, v in resolved.items()}}


def _base_stem(name: str) -> str:
    from .paths import _base_stem as base_stem

    return base_stem(name)


def _read_glb_json(path: Path) -> dict[str, Any] | None:
    from aigamekit_shared.glb_verify import read_glb_json

    chunk, _size, err = read_glb_json(path)
    return chunk if err is None else None


def _glb_summary(path: Path) -> dict[str, Any]:
    """Resumo barato do GLB para os cartões de etapa do Studio.

    Lê só o JSON chunk (uma vez); não descodifica buffers. O viewport usa o
    ``bevy_ext_gltf::GlbBounds`` para o framing, por isso os bounds não entram
    aqui.
    """
    data = _read_glb_json(path)
    if not data:
        return {"parsed": False, "byte_size": path.stat().st_size}
    accessors = data.get("accessors") or []
    attributes: set[str] = set()
    total_v = 0
    total_i = 0
    primitives = 0
    for mesh in data.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            primitives += 1
            attrs = prim.get("attributes") or {}
            attributes.update(str(k) for k in attrs)
            pos = attrs.get("POSITION")
            if isinstance(pos, int) and 0 <= pos < len(accessors):
                total_v += int(accessors[pos].get("count") or 0)
            indices = prim.get("indices")
            if isinstance(indices, int) and 0 <= indices < len(accessors):
                total_i += int(accessors[indices].get("count") or 0)
    triangles = total_i // 3 if total_i else 0
    materials = data.get("materials") or []
    animations = [str(a.get("name") or "") for a in (data.get("animations") or [])]
    base_color = any("baseColorTexture" in (m.get("pbrMetallicRoughness") or {}) for m in materials)
    return {
        "parsed": True,
        "materials": len(materials),
        "skins": len(data.get("skins") or []),
        "animations": len(animations),
        "animation_names": animations,
        "base_color_texture": base_color,
        "extensions_used": list(data.get("extensionsUsed") or []),
        "extensions_required": list(data.get("extensionsRequired") or []),
        "meshes": len(data.get("meshes") or []),
        "primitive_count": primitives,
        "vertex_count_total": total_v,
        "triangle_count_total": triangles,
        "v_per_tri": round(total_v / triangles, 4) if triangles else None,
        "attributes_present": sorted(attributes),
        "has_normals": "NORMAL" in attributes,
        "has_tangents": "TANGENT" in attributes,
        "has_uv": "TEXCOORD_0" in attributes,
        "has_joints": "JOINTS_0" in attributes,
        "has_weights": "WEIGHTS_0" in attributes,
        "byte_size": path.stat().st_size,
    }


def _glb_gates(path: Path) -> dict[str, bool]:
    from .pipeline import (
        _json_has_animations,
        _json_has_duplicate_clips,
        _json_has_materials,
        _json_has_skin,
    )

    j = _read_glb_json(path)
    return {
        "skins": _json_has_skin(j),
        "animations": _json_has_animations(j),
        "materials": _json_has_materials(j),
        "duplicate_clips": _json_has_duplicate_clips(j),
    }


def _artifact_entry(path: Path, canonical: Path | None = None) -> dict[str, Any]:
    try:
        stat = path.stat()
        exists = path.is_file() and stat.st_size > 0
    except OSError:
        stat = None
        exists = False
    entry: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "size": stat.st_size if exists and stat is not None else None,
        "mtime": stat.st_mtime if exists and stat is not None else None,
    }
    if canonical is not None and canonical != path:
        entry["canonical"] = str(canonical)
    if exists and path.suffix.lower() == ".glb":
        entry["glb"] = _glb_summary(path)
        entry["gates"] = _glb_gates(path)
    return entry


def _promotion(lod0: Path) -> str:
    from .paths import _glb_is_promoted_animated, _glb_is_promoted_rigged

    if not (lod0.is_file() and lod0.stat().st_size > 0):
        return "none"
    if _glb_is_promoted_animated(lod0):
        return "animated"
    if _glb_is_promoted_rigged(lod0):
        return "rigged"
    from .paths import _glb_has_base_color

    return "painted" if _glb_has_base_color(lod0) else "none"


def _pid_alive(pid: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(project_dir: Path) -> dict[str, Any] | None:
    path = project_dir / ".gameassets_batch.lock"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "invalid": True, "live": False}
    if not isinstance(payload, dict):
        return {"path": str(path), "invalid": True, "live": False}
    entry = dict(payload)
    entry["path"] = str(path)
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        pid = None
    host = str(entry.get("host") or "")
    entry["live"] = bool(pid and host == socket.gethostname() and _pid_alive(pid))
    return entry


def _tools() -> dict[str, str | None]:
    from aigamekit_shared.subprocess_utils import resolve_binary

    tools: dict[str, str | None] = {}
    for key, env_name, default_name in (
        ("text2d", "TEXT2D_BIN", "text2d"),
        ("text3d", "TEXT3D_BIN", "text3d"),
        ("paint3d", "PAINT3D_BIN", "paint3d"),
        ("rigging3d", "RIGGING3D_BIN", "rigging3d"),
        ("animator3d", "ANIMATOR3D_BIN", "animator3d"),
        ("motion3d", "MOTION3D_BIN", "motion3d"),
        ("hi3d", "HI3D_BIN", "hi3d"),
        ("aigamekit_lab", "AIGAMEKITLAB_BIN", "aigamekit-lab"),
        ("vramd", "VRAMD_BIN", "vramd"),
    ):
        try:
            tools[key] = resolve_binary(env_name, default_name)
        except FileNotFoundError:
            tools[key] = None
    tools["npx"] = shutil.which("npx")
    tools["ktx"] = shutil.which("ktx")
    return tools


def _seed(profile: Any, row: Any) -> int | None:
    from .helpers import _seed_for_manifest_row

    return _seed_for_manifest_row(profile, row)


def _pipeline(row: Any) -> list[str]:
    stages: list[str] = []
    if row.generate_3d:
        stages.append("3d")
    if row.generate_paint:
        stages.append("paint")
    if row.generate_rig:
        stages.append("rig")
    if row.generate_animate:
        stages.append("animate")
    if row.generate_lod:
        stages.append("lod")
    if row.generate_collision:
        stages.append("collision")
    if row.generate_audio:
        stages.append("audio")
    return stages


def _check_payload(project_dir: Path) -> dict[str, Any]:
    from .categories import get_target_faces
    from .manifest import row_mc_level, row_octree_resolution
    from .multiview import configured_views, refine_stale, row_skips_refine
    from .omni_ctrl import shape_omni_stale
    from .paths import _classify_row_state_master
    from .pipeline import _resolve_paint_texture_size, resolve_row_omni
    from .prompt_builder import build_prompt

    project_dir = project_dir.resolve()
    profile, row, preset, profile_path, manifest_path = _load_context(project_dir)
    paths = _artifact_paths(profile, project_dir, row)
    mesh = paths["resolved_mesh"]
    shape = paths["resolved_shape"]

    omni = resolve_row_omni(profile, row, manifest_dir=project_dir)
    text3d = profile.text3d
    omni_stale = bool(
        shape.is_file()
        and shape_omni_stale(
            shape,
            omni,
            category=row.category or None,
            bounds_mode=getattr(text3d, "bounds_mode", None) if text3d else None,
            mc_level=row_mc_level(row, getattr(text3d, "mc_level", None) if text3d else None),
            seed=row.seed,
            octree_resolution=row_octree_resolution(
                row, getattr(text3d, "octree_resolution", None) if text3d else None
            ),
        )
    )
    refine_changed = refine_stale(profile, row, mesh, project_dir)
    state = _classify_row_state_master(
        img_final=paths["resolved_image"],
        mesh_final=mesh,
        want_texture=row.generate_paint,
        wants_rig=row.generate_rig,
        wants_animate=row.generate_animate,
        wants_lod=row.generate_lod,
        wants_collision=row.generate_collision,
        omni_stale=omni_stale,
        refine_stale=refine_changed,
    )
    # O classificador master não devolve ``need_collision``; para o cartão da
    # UI a colisão em falta continua a ser um estágio pendente depois do done.
    collision_p = paths["collision"]
    if state == "done" and row.generate_collision and not (collision_p.is_file() and collision_p.stat().st_size > 0):
        state = "need_collision"

    canonical_by_name = {
        "image": paths["image"],
        "mesh": paths["mesh"],
        "shape": paths["shape"],
        "clean": paths["clean"],
        "refined": paths["refined"],
        "to_paint": paths["to_paint"],
        "painted": paths["painted"],
        "rigged": paths["rigged"],
        "animated": paths["animated"],
        "lod0": paths["lod0"],
        "lod1": paths["lod1"],
        "lod2": paths["lod2"],
        "collision": paths["collision"],
        "precompute": paths["precompute"],
        "split": paths["split"],
        "stump": paths["stump"],
        "top": paths["top"],
        "shape_omni": paths["resolved_shape_omni"],
        "clean_omni": paths["resolved_clean_omni"],
        "refined_multiview": paths["resolved_refined_multiview"],
        "views": paths["resolved_views"],
        "stump_collision": paths["resolved_stump_collision"],
        "top_collision": paths["resolved_top_collision"],
    }
    artifacts: dict[str, Any] = {}
    for name, resolved in {
        "image": paths["resolved_image"],
        "shape": paths["resolved_shape"],
        "clean": paths["resolved_clean"],
        "refined": paths["resolved_refined"],
        "to_paint": paths["resolved_to_paint"],
        "painted": paths["resolved_painted"],
        "rigged": paths["resolved_rigged"],
        "animated": paths["resolved_animated"],
        "lod0": paths["lod0"],
        "lod1": paths["lod1"],
        "lod2": paths["lod2"],
        "collision": paths["collision"],
        "precompute": paths["precompute"],
        "split": paths["split"],
        "stump": paths["stump"],
        "top": paths["top"],
        "shape_omni": paths["resolved_shape_omni"],
        "clean_omni": paths["resolved_clean_omni"],
        "refined_multiview": paths["resolved_refined_multiview"],
        "views": paths["resolved_views"],
        "stump_collision": paths["resolved_stump_collision"],
        "top_collision": paths["resolved_top_collision"],
    }.items():
        artifacts[name] = _artifact_entry(resolved, canonical_by_name.get(name))

    refine_profile = profile.refine
    promote = _promotion(paths["lod0"])
    target_faces = None
    if row.category:
        try:
            target_faces = get_target_faces(row.category)
        except Exception:
            target_faces = None
    texture_size = None
    try:
        texture_size = _resolve_paint_texture_size(profile, row)
    except Exception:
        texture_size = None

    return {
        "version": STUDIO_VERSION,
        "project_dir": str(project_dir),
        "profile_path": str(profile_path),
        "manifest_path": str(manifest_path),
        "asset": {
            "id": row.id,
            "idea": row.idea,
            "kind": row.kind,
            "category": row.category,
            "pipeline": _pipeline(row),
            "generate_3d": row.generate_3d,
            "generate_paint": row.generate_paint,
            "generate_rig": row.generate_rig,
            "generate_animate": row.generate_animate,
            "generate_lod": row.generate_lod,
            "generate_collision": row.generate_collision,
            "seed": _seed(profile, row),
            "gpu": getattr(row, "gpu", "auto"),
            "target_faces": target_faces,
            "paint_texture_size": texture_size,
        },
        "prompt": {
            "text2d": build_prompt(profile, preset, row, for_3d=False),
            "text3d_hint": build_prompt(profile, preset, row, for_3d=True),
        },
        "refine": {
            "enabled": bool(getattr(refine_profile, "enabled", True)) if refine_profile is not None else True,
            "views": list(configured_views(profile, row)),
            "skipped": row_skips_refine(row),
            "stale": refine_changed,
        },
        "layout": {
            "output_dir": profile.output_dir,
            "path_layout": profile.path_layout,
            "images_subdir": profile.images_subdir,
            "meshes_subdir": profile.meshes_subdir,
            "image_ext": profile.image_ext,
        },
        "state": state,
        "stale": {"omni": omni_stale, "refine": refine_changed},
        "promotion": {"lod0": promote},
        "artifacts": artifacts,
        "deliverables": {name: artifacts[name] for name in ("lod0", "lod1", "lod2", "collision", "precompute")},
        "lock": _read_lock(project_dir),
        "tools": _tools(),
        "gpu": {
            "mode_env": os.environ.get("VRAMD_GPU_MODE"),
            "remote": os.environ.get("VRAMD_REMOTE"),
        },
    }


def _sidecars(path: Path) -> list[Path]:
    out = [Path(str(path) + suffix) for suffix in _SIDECAR_SUFFIXES]
    return out


def _stage_own_artifacts(paths: dict[str, Path], stage: str) -> list[Path]:
    """Artefactos da própria etapa (para ``--include-stage``).

    ``all``/``image`` já apagam tudo no mapping principal; devolver ``[]``
    evita duplicar entradas.
    """
    if stage in ("all", "image"):
        return []
    own: dict[str, list[Path]] = {
        "shape": [paths["resolved_shape"]],
        "clean": [paths["resolved_clean"]],
        "refine": [paths["resolved_refined"]],
        "to_paint": [paths["resolved_to_paint"]],
        "paint": [paths["resolved_painted"]],
        "rig": [paths["resolved_rigged"]],
        "animate": [paths["resolved_animated"]],
        "lod": [paths["lod0"], paths["lod1"], paths["lod2"]],
        "collision": [paths["collision"], paths["precompute"]],
    }
    out: list[Path] = []
    for path in own.get(stage, []):
        out.append(path)
        out.extend(_sidecars(path))
    return out


def _downstream_stage_paths(paths: dict[str, Path], stage: str, *, include_stage: bool = False) -> list[Path]:
    """Ficheiros a apagar para invalidar a jusante da etapa ``stage``.

    O artefacto da própria etapa fica (é o resultado novo do re-roll). Com
    ``include_stage=True`` apaga-se também esse artefacto (e sidecars), que é
    o que o Studio usa para o re-roll explícito de uma etapa. ``all``/``image``
    continuam a ser um reset completo.
    """
    from .paths import _base_stem

    mesh = paths["resolved_mesh"]
    base = _base_stem(mesh.stem)
    downstream: dict[str, list[Path]] = {
        "shape": [
            paths["resolved_clean"],
            paths["resolved_refined"],
            paths["resolved_to_paint"],
            paths["resolved_painted"],
            paths["resolved_rigged"],
            paths["resolved_animated"],
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
            paths["resolved_views"],
            paths["resolved_refined_multiview"],
        ],
        "clean": [
            paths["resolved_refined"],
            paths["resolved_to_paint"],
            paths["resolved_painted"],
            paths["resolved_rigged"],
            paths["resolved_animated"],
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
            paths["resolved_views"],
            paths["resolved_refined_multiview"],
        ],
        "refine": [
            paths["resolved_to_paint"],
            paths["resolved_painted"],
            paths["resolved_rigged"],
            paths["resolved_animated"],
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
        ],
        "to_paint": [
            paths["resolved_painted"],
            paths["resolved_rigged"],
            paths["resolved_animated"],
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
        ],
        "paint": [
            paths["resolved_rigged"],
            paths["resolved_animated"],
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
        ],
        "rig": [
            paths["resolved_animated"],
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
        ],
        "animate": [
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
        ],
        "lod": [
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
        ],
        "collision": [paths["collision"], paths["precompute"]],
    }
    if stage == "all" or stage == "image":
        from .omni_ctrl import _derived_glb_paths_for_shape

        # Reset completo: imagem, shape + sidecar, todos os derivados do mesmo
        # base e sidecars de cache. A remoção é feita pelo chamador (sem
        # efeitos laterais nesta função).
        out = [
            paths["resolved_image"],
            paths["resolved_shape"],
            *_sidecars(paths["resolved_shape"]),
            paths["resolved_clean"],
            paths["resolved_refined"],
            paths["resolved_to_paint"],
            paths["resolved_painted"],
            paths["resolved_rigged"],
            paths["resolved_animated"],
            paths["lod0"],
            paths["lod1"],
            paths["lod2"],
            paths["collision"],
            paths["precompute"],
            paths["split"],
            paths["stump"],
            paths["top"],
            paths["resolved_stump_collision"],
            paths["resolved_top_collision"],
            paths["resolved_views"],
            paths["resolved_refined_multiview"],
        ]
        try:
            for path in _derived_glb_paths_for_shape(paths["resolved_shape"]):
                if path != paths["resolved_shape"]:
                    out.append(path)
                    out.extend(_sidecars(path))
        except OSError:
            pass
        return out
    if stage not in downstream:
        raise ValueError(f"etapa desconhecida: {stage!r}")
    out: list[Path] = []
    candidates = downstream[stage]
    if include_stage:
        candidates = [*_stage_own_artifacts(paths, stage), *candidates]
    for path in candidates:
        out.append(path)
        out.extend(_sidecars(path))
    # Variantes legacy no mesmo base (meshes/ e _intermediate/) para o shape.
    if stage == "shape":
        from .omni_ctrl import _derived_glb_paths_for_shape

        try:
            for path in _derived_glb_paths_for_shape(paths["resolved_shape"]):
                if path != paths["resolved_shape"]:
                    out.append(path)
                    out.extend(_sidecars(path))
        except OSError:
            pass
    # Colisões de árvores partidas por split.
    out.append(mesh.with_name(f"{base}_stump_collision.glb"))
    out.append(mesh.with_name(f"{base}_top_collision.glb"))
    return out


def _invalidate_payload(project_dir: Path, stage: str, *, include_stage: bool = False) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    profile, row, _preset, _profile_path, _manifest_path = _load_context(project_dir)
    paths = _artifact_paths(profile, project_dir, row)
    candidates = _downstream_stage_paths(paths, stage, include_stage=include_stage)

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for path in sorted({str(p): p for p in candidates}.values()):
        if not path.exists() or path.is_dir():
            continue
        try:
            path.unlink()
            deleted.append(str(path))
        except OSError as exc:
            failed.append({"path": str(path), "error": str(exc)})
    return {
        "version": STUDIO_VERSION,
        "project_dir": str(project_dir),
        "asset_id": row.id,
        "from": stage,
        "include_stage": include_stage,
        "deleted": deleted,
        "failed": failed,
    }


@click.group("studio")
def studio_group() -> None:
    """Estado e invalidação machine-readable para o AiGameKit Studio."""


@studio_group.command("check")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Pasta do projeto (game.yaml + manifest.yaml)",
)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def check_cmd(project_dir: Path, as_json: bool) -> None:
    """Valida o projeto e devolve o estado de todas as etapas."""
    try:
        payload = _check_payload(project_dir)
    except Exception as exc:
        if as_json:
            click.echo(json.dumps({"version": STUDIO_VERSION, "error": str(exc)}))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    asset = payload["asset"]
    click.echo(f"{asset['id']}  state={payload['state']}  promotion={payload['promotion']['lod0']}")
    for name, entry in payload["artifacts"].items():
        if entry["exists"]:
            click.echo(f"  {name:<18} {entry['path']}")


@studio_group.command("invalidate")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Pasta do projeto (game.yaml + manifest.yaml)",
)
@click.option(
    "--from",
    "from_stage",
    type=click.Choice(VALID_STAGES, case_sensitive=False),
    required=True,
    help="Etapa a preservar; os derivados a jusante são apagados",
)
@click.option(
    "--include-stage",
    is_flag=True,
    help="Apagar também o artefacto da etapa indicada (re-roll dessa etapa).",
)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def invalidate_cmd(
    project_dir: Path,
    from_stage: str,
    include_stage: bool,
    as_json: bool,
) -> None:
    """Apaga os derivados de uma etapa (o artefacto da etapa fica, salvo --include-stage)."""
    try:
        payload = _invalidate_payload(project_dir, from_stage.lower(), include_stage=include_stage)
    except Exception as exc:
        if as_json:
            click.echo(json.dumps({"version": STUDIO_VERSION, "error": str(exc)}))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    click.echo(f"{payload['asset_id']}: {len(payload['deleted'])} ficheiro(s) apagado(s) a partir de {payload['from']}")
    for path in payload["deleted"]:
        click.echo(f"  {path}")
