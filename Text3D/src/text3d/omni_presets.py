"""Presets Omni embutidos: pose Quaternius T-pose + bbox aspect ratios.

Assets vivem em ``text3d/data/omni/`` (package-data). Runtime usa
``importlib.resources`` — paths absolutos estáveis via ``as_file``.
"""

from __future__ import annotations

import atexit
from contextlib import ExitStack
from importlib import resources
from pathlib import Path
from typing import Any

# Mantém TemporaryDirectory de Traversable.as_file vivos até ao exit do processo.
_RESOURCE_STACK = ExitStack()
atexit.register(_RESOURCE_STACK.close)

POSE_PRESETS: dict[str, str] = {
    "quaternius-tpose": "quaternius_tpose_bone.txt",
}

# [length, height, width] normalizados 0-1 (eixo maior = 1.0 na intencao do preset).
BBOX_PRESETS: dict[str, tuple[float, float, float]] = {
    "cube": (1.0, 1.0, 1.0),
    "humanoid": (0.45, 1.0, 0.35),
    "humanoid-child": (0.71, 1.0, 0.57),
    "quadruped": (1.0, 0.55, 0.4),
    "sword": (0.12, 1.0, 0.06),
    "shield": (0.7, 1.0, 0.15),
    "crate": (1.0, 1.0, 1.0),
    "door": (0.55, 1.0, 0.12),
    "barrel": (0.7, 1.0, 0.7),
    "tree": (0.35, 1.0, 0.35),
    "chest": (1.0, 0.61, 0.61),
    "furniture": (1.0, 0.85, 0.7),
    # Capela / casa de culto pequena: ~6 m profundidade x 7 m altura x 4.5 m largura.
    "building": (0.86, 1.0, 0.64),
    "chapel": (0.86, 1.0, 0.64),
}

# Soft defaults por categoria de asset (GameAssets / QualityEngine).
CATEGORY_OMNI_DEFAULTS: dict[str, dict[str, Any]] = {
    "humanoid": {"control_type": "pose", "pose_preset": "quaternius-tpose"},
    "weapon": {"control_type": "bbox", "bbox_preset": "sword"},
    "tool": {"control_type": "bbox", "bbox_preset": "sword"},
    "door": {"control_type": "bbox", "bbox_preset": "door"},
    "chest": {"control_type": "bbox", "bbox_preset": "chest"},
    "furniture": {"control_type": "bbox", "bbox_preset": "furniture"},
    "building": {"control_type": "bbox", "bbox_preset": "building"},
}


def omni_data_path(filename: str) -> Path:
    """Path absoluto a um ficheiro em ``text3d/data/omni/``."""
    trav = resources.files("text3d.data.omni").joinpath(filename)
    return Path(_RESOURCE_STACK.enter_context(resources.as_file(trav)))


def list_pose_presets() -> list[str]:
    return sorted(POSE_PRESETS)


def list_bbox_presets() -> list[str]:
    return sorted(BBOX_PRESETS)


def resolve_pose_preset(name: str) -> Path:
    """Resolve preset de pose → path do bone.txt.

    Raises:
        KeyError: preset desconhecido.
        FileNotFoundError: ficheiro em falta no package-data.
    """
    key = (name or "").strip().lower()
    if key not in POSE_PRESETS:
        raise KeyError(f"pose_preset desconhecido: {name!r} (válidos: {sorted(POSE_PRESETS)})")
    path = omni_data_path(POSE_PRESETS[key])
    if not path.is_file():
        raise FileNotFoundError(f"Asset Omni em falta: {path}")
    return path


def resolve_bbox_preset(name: str) -> list[float]:
    """Resolve preset de bbox → ``[L, H, W]``."""
    key = (name or "").strip().lower()
    if key not in BBOX_PRESETS:
        raise KeyError(f"bbox_preset desconhecido: {name!r} (válidos: {sorted(BBOX_PRESETS)})")
    return list(BBOX_PRESETS[key])


def quaternius_tpose_glb() -> Path:
    """GLB de referência (armature visual) do T-pose Quaternius."""
    return omni_data_path("quaternius_tpose.glb")


def parse_bbox_csv(raw: str) -> list[float]:
    """Parse ``L,H,W`` ou AABB 6-float."""
    vals = [float(x.strip()) for x in str(raw).split(",") if x.strip()]
    if len(vals) not in (3, 6):
        raise ValueError(f"bbox espera 3 ou 6 floats, recebeu {len(vals)}")
    return vals


def size_m_to_bbox(size_m: list[float] | tuple[float, ...]) -> list[float]:
    """Metros ``[L,H,W]`` → dims Omni normalizadas pelo eixo maior."""
    arr = [float(v) for v in size_m]
    if len(arr) != 3:
        raise ValueError(f"size_m espera 3 floats, recebeu {len(arr)}")
    m = max(arr)
    if m <= 0:
        return [1.0, 1.0, 1.0]
    return [v / m for v in arr]


def category_omni_defaults(category: str | None) -> dict[str, Any]:
    """Defaults soft por categoria (cópia rasa)."""
    if not category:
        return {}
    key = str(category).strip().lower()
    base = CATEGORY_OMNI_DEFAULTS.get(key)
    return dict(base) if base else {}


def merge_omni_controls(
    *,
    control_type: str | None = None,
    bbox: list[float] | None = None,
    bbox_preset: str | None = None,
    size: list[float] | None = None,
    size_m: list[float] | None = None,
    pose_file: str | Path | None = None,
    pose_preset: str | None = None,
    point_cloud: str | Path | None = None,
    voxel_mesh: str | Path | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Resolve presets/aliases → kwargs para ``generate_from_image`` / CLI.

    Soft-fill a partir de ``category`` só quando o caller não explicitou controlo.
    """
    ct = (control_type or "none").strip().lower()
    explicit = (
        ct != "none"
        or bbox is not None
        or bbox_preset
        or size is not None
        or size_m is not None
        or pose_file is not None
        or pose_preset
        or point_cloud is not None
        or voxel_mesh is not None
    )
    if not explicit:
        defaults = category_omni_defaults(category)
        ct = str(defaults.get("control_type", "none"))
        bbox_preset = bbox_preset or defaults.get("bbox_preset")
        pose_preset = pose_preset or defaults.get("pose_preset")

    if pose_preset and not pose_file:
        pose_file = resolve_pose_preset(pose_preset)
        if ct == "none":
            ct = "pose"

    if bbox is None and size is not None:
        bbox = list(size)
        if ct == "none":
            ct = "bbox"
    if bbox is None and size_m is not None:
        bbox = size_m_to_bbox(size_m)
        if ct == "none":
            ct = "bbox"
    if bbox is None and bbox_preset:
        bbox = resolve_bbox_preset(bbox_preset)
        if ct == "none":
            ct = "bbox"

    out: dict[str, Any] = {
        "control_type": ct,
        "bbox": bbox,
        "pose_file": str(pose_file) if pose_file else None,
        "point_cloud": str(point_cloud) if point_cloud else None,
        "voxel_mesh": str(voxel_mesh) if voxel_mesh else None,
        "pose_preset": pose_preset,
        "bbox_preset": bbox_preset,
        # Metros absolutos (escala mundo) — fingerprint/resume; distinto do bbox
        # normalizado Omni.
        "size_m": list(size_m) if size_m is not None else None,
    }
    return out


def omni_fingerprint(controls: dict[str, Any]) -> dict[str, Any]:
    """Payload estável para sidecar ``*_shape.omni.json`` (resume).

    Com ``pose_preset`` / ``bbox_preset``, ignora paths absolutos voláteis
    (``importlib.resources.as_file`` pode mudar entre runs).
    """
    pose_preset = controls.get("pose_preset")
    bbox_preset = controls.get("bbox_preset")
    pose_file = None if pose_preset else controls.get("pose_file")
    if pose_file:
        pose_file = Path(pose_file).name
    point = controls.get("point_cloud")
    voxel = controls.get("voxel_mesh")
    # Knobs de decode que mudam a geometria (resume invalida quando mudam).
    # Valores equivalentes ao legado normalizam para None — sidecars antigos
    # sem estas chaves continuam a coincidir com runs "clássicos".
    bounds_mode = controls.get("bounds_mode")
    # Default actual = auto; cube é opt-out explícito. Sidecars antigos sem
    # chave ≡ auto (None).
    if bounds_mode in ("auto", "", None):
        bounds_mode = None
    mc_level = controls.get("mc_level")
    if mc_level in (None, "", 0, 0.0, "auto"):
        # "auto" = default actual — sidecars antigos sem chave continuam ok.
        mc_level = None
    elif not isinstance(mc_level, str):
        mc_level = float(mc_level)
    size_m = controls.get("size_m")
    if size_m is not None:
        try:
            size_m = [round(float(v), 4) for v in size_m]
            if len(size_m) != 3:
                size_m = None
        except (TypeError, ValueError):
            size_m = None
    return {
        "control_type": controls.get("control_type") or "none",
        "bbox": None if bbox_preset else controls.get("bbox"),
        "bbox_preset": bbox_preset,
        "pose_preset": pose_preset,
        "pose_file": pose_file,
        "point_cloud": Path(point).name if point else None,
        "voxel_mesh": Path(voxel).name if voxel else None,
        "point_from": controls.get("point_from"),
        "bounds_mode": bounds_mode,
        "mc_level": mc_level,
        "size_m": size_m,
    }


def shape_omni_sidecar_path(shape_glb: str | Path) -> Path:
    """``foo_shape.glb`` → ``foo_shape.glb.omni.json``."""
    return Path(str(shape_glb) + ".omni.json")


def write_omni_fingerprint(shape_glb: str | Path, controls: dict[str, Any]) -> Path:
    """Grava sidecar JSON junto do shape (resume / invalidação)."""
    import json

    path = shape_omni_sidecar_path(shape_glb)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(omni_fingerprint(controls), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_omni_fingerprint(shape_glb: str | Path) -> dict[str, Any] | None:
    """Lê sidecar; ``None`` se ausente/inválido."""
    import json

    path = shape_omni_sidecar_path(shape_glb)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def omni_fingerprint_matches(shape_glb: str | Path, controls: dict[str, Any]) -> bool:
    """True se shape existe e sidecar coincide com ``controls``."""
    existing = read_omni_fingerprint(shape_glb)
    if existing is None:
        return False
    return omni_fingerprint(controls) == omni_fingerprint(existing)
