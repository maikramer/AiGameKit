"""Presets Omni embutidos: pose Quaternius T-pose + bbox aspect ratios.

Assets vivem em ``text3d/data/omni/`` (package-data). Runtime usa
``importlib.resources`` — paths absolutos estáveis via ``as_file``.
"""

from __future__ import annotations

import atexit
import contextlib
from contextlib import ExitStack
from importlib import resources
from pathlib import Path
from typing import Any

# Mantém TemporaryDirectory de Traversable.as_file vivos até ao exit do processo.
_RESOURCE_STACK = ExitStack()
atexit.register(_RESOURCE_STACK.close)

POSE_PRESETS: dict[str, str] = {
    "quaternius-tpose": "quaternius_tpose_bone.txt",
    # Anão / chibi / cabeça grande: tronco curto, ombros mais baixos.
    "quaternius-tpose-dwarf": "quaternius_tpose_dwarf_bone.txt",
    # A-pose (braços -45°, mãos abertas): humanoides musculados/gordos — a
    # T-pose horizontal estica músculo/gordura (ogre/merchant). Ver
    # scripts/derive_openhand_apose.py.
    "quaternius-apose": "quaternius_apose_bone.txt",
    # A-pose chibi (braços -45° no esqueleto dwarf).
    "quaternius-apose-dwarf": "quaternius_apose_dwarf_bone.txt",
    # Aliases
    "dwarf-tpose": "quaternius_tpose_dwarf_bone.txt",
    "chibi-tpose": "quaternius_tpose_dwarf_bone.txt",
    "a-pose": "quaternius_apose_bone.txt",
    "apose": "quaternius_apose_bone.txt",
    "dwarf-apose": "quaternius_apose_dwarf_bone.txt",
    "chibi-apose": "quaternius_apose_dwarf_bone.txt",
}

# [L, H, W] = aspect Omni → após export Y-up WebGL/Three.js mapeia para [X, Y, Z].
#   L → X  (esquerda/direita na vista de frente, −Z)
#   H → Y  (cima; Y+ up no exhibit / VibeGame)
#   W → Z  (profundidade, afastamento da câmara)
# Eixo maior = 1.0 (docs Omni 0-1). Cantos ±0.5; grid MC ±1.01 → margem.
# NÃO escalar a 2.0 (enche e clipa). NÃO chamar L de “profundidade”.
BBOX_PRESETS: dict[str, tuple[float, float, float]] = {
    "cube": (1.0, 1.0, 1.0),
    "humanoid": (0.45, 1.0, 0.35),
    # Chibi / cabeça grande: ombros mais largos.
    "humanoid-child": (0.71, 1.0, 0.57),
    "quadruped": (1.0, 0.55, 0.4),
    # Slime / blob: quase cúbico (isótropo). Aspect L≠W ou H baixo demais →
    # «esticado como carro» / achatado. Cube exacto (1,1,1) também ok.
    "blob": (1.0, 1.0, 1.0),
    "slime": (1.0, 1.0, 1.0),
    # Insecto / voador achatado (L≈W >> H).
    "flat": (1.0, 0.4375, 1.0),
    "flying": (1.0, 0.4375, 1.0),
    # Lâmina: L=largura da lâmina (X), H=altura (Y), W=espessura (Z).
    "sword": (0.12, 1.0, 0.04),
    "shield": (0.7, 1.0, 0.15),
    "crate": (1.0, 1.0, 1.0),
    # Porta: L=largura folha (X), H=altura, W=espessura (Z).
    "door": (0.55, 1.0, 0.12),
    "barrel": (0.7, 1.0, 0.7),
    # Árvore: H dominante mas L=W gordos o bastante p/ tronco cilíndrico.
    # 0.35 era papel fino (frente ok, lado laminado) + galhos esticados.
    "tree": (0.55, 1.0, 0.55),
    # Coluna / cactus / pilar fino L=W (mais magro que tree; ainda L=W anti-papel).
    "column": (0.4, 1.0, 0.4),
    "cactus": (0.4, 1.0, 0.4),
    "chest": (1.0, 0.61, 0.61),
    "furniture": (1.0, 0.85, 0.7),
    # Casa/capela: L=largura fachada (X), H=altura (Y), W=profundidade (Z).
    # Ex. ~6 m largura × 7 m altura × 4.5 m profundidade → aspect ≈ (0.86, 1, 0.64).
    "building": (0.86, 1.0, 0.64),
    "chapel": (0.86, 1.0, 0.64),
}

# Soft defaults por categoria de asset (GameAssets / QualityEngine).
# humanoid → A-pose Omni (braços -45°; T-pose estica ombros/mãos em corpos gordos).
# ``creature`` sem soft pose (quadruped/slime/voador ≠ Quaternius) — manifesto explícito.
CATEGORY_OMNI_DEFAULTS: dict[str, dict[str, Any]] = {
    "humanoid": {"control_type": "pose", "pose_preset": "quaternius-apose"},
    "weapon": {"control_type": "bbox", "bbox_preset": "sword"},
    "tool": {"control_type": "bbox", "bbox_preset": "sword"},
    "door": {"control_type": "bbox", "bbox_preset": "door"},
    "chest": {"control_type": "bbox", "bbox_preset": "chest"},
    "furniture": {"control_type": "bbox", "bbox_preset": "furniture"},
    "building": {"control_type": "bbox", "bbox_preset": "building"},
    "vegetation": {"control_type": "bbox", "bbox_preset": "tree"},
    "tree": {"control_type": "bbox", "bbox_preset": "tree"},
    "prop": {"control_type": "bbox", "bbox_preset": "crate"},
    "terrain": {"control_type": "bbox", "bbox_preset": "cube"},
    "rock": {"control_type": "bbox", "bbox_preset": "cube"},
    "item": {"control_type": "bbox", "bbox_preset": "cube"},
    "shield": {"control_type": "bbox", "bbox_preset": "shield"},
    "barrel": {"control_type": "bbox", "bbox_preset": "barrel"},
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
    """Metros ``[L,H,W]`` → dims Omni (eixo maior = ``OMNI_BBOX_AXIS_MAX``)."""
    from .utils.omni_controls import OMNI_BBOX_AXIS_MAX

    arr = [float(v) for v in size_m]
    if len(arr) != 3:
        raise ValueError(f"size_m espera 3 floats, recebeu {len(arr)}")
    m = max(arr)
    if m <= 0:
        return [OMNI_BBOX_AXIS_MAX, OMNI_BBOX_AXIS_MAX, OMNI_BBOX_AXIS_MAX]
    return [OMNI_BBOX_AXIS_MAX * (v / m) for v in arr]


def size_m_near_cube(size_m: list[float] | tuple[float, ...], *, tol: float = 1.12) -> bool:
    """True se L/H/W são quase cúbicos (max/min ≤ ``tol``)."""
    arr = [float(v) for v in size_m]
    if len(arr) != 3 or min(arr) <= 0:
        return False
    return (max(arr) / min(arr)) <= tol


def size_m_from_height(
    height_m: float,
    *,
    footprint_m: float | None = None,
    bbox_aspect: list[float] | tuple[float, ...] | None = None,
) -> list[float]:
    """Metros ``[L,H,W]`` a partir da altura alvo (e opcionalmente footprint).

    Omni **não** gera em metros — enche o aspect da bbox. Este helper só
    materializa um ``size_m`` coerente; em modo bbox, ``merge_omni_controls``
    usa o aspect (via ``size_m_to_bbox``) como **molde** que o modelo preenche.

    Precedência footprint:
    1. ``footprint_m`` → L=W=footprint (coluna / prop)
    2. ``bbox_aspect`` → L,W = height x (aspect_L/H, aspect_W/H)
    3. senão footprint = ``0.4 * height`` (coluna genérica)
    """
    h = float(height_m)
    if h <= 0:
        raise ValueError(f"height_m deve ser > 0, recebeu {height_m!r}")
    if footprint_m is not None:
        fp = float(footprint_m)
        if fp <= 0:
            raise ValueError(f"footprint_m deve ser > 0, recebeu {footprint_m!r}")
        return [fp, h, fp]
    if bbox_aspect is not None:
        arr = [float(v) for v in bbox_aspect]
        if len(arr) != 3 or arr[1] <= 0:
            raise ValueError(f"bbox_aspect inválido: {bbox_aspect!r}")
        return [h * (arr[0] / arr[1]), h, h * (arr[2] / arr[1])]
    return [0.4 * h, h, 0.4 * h]


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
    height_m: float | None = None,
    footprint_m: float | None = None,
    pose_file: str | Path | None = None,
    pose_preset: str | None = None,
    point_cloud: str | Path | None = None,
    voxel_mesh: str | Path | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Resolve presets/aliases → kwargs para ``generate_from_image`` / CLI.

    Soft-fill a partir de ``category`` só quando o caller não explicitou controlo.

    ``height_m`` / ``footprint_m`` (authoring): expandem para ``size_m`` se
    ausente. Com ``control_type=bbox`` e ``footprint_m`` definido, o aspect
    heightxfootprint vira **bbox Omni** (molde que o modelo preenche) —
    não é só escala pós-mesh. Com pose, só ``size_m`` mundo (esqueleto manda).
    """
    ct = (control_type or "none").strip().lower()
    # size_m / height_m = metros mundo.
    # - Com pose: NÃO contam como controlo geométrico (não bloqueiam soft-fill;
    #   não injectam bbox — senão personagens engordam a encher a caixa).
    # - Com bbox + size_m aniso: MAIS ABAIXO viram molde Omni (size_m_to_bbox),
    #   prevalecendo sobre bbox_preset — ver bloco author_mold / aniso.
    has_geom = (
        ct != "none"
        or bbox is not None
        or bbox_preset
        or size is not None
        or pose_file is not None
        or pose_preset
        or point_cloud is not None
        or voxel_mesh is not None
    )
    # height+footprint sem outro controlo → intenção de molde bbox (coluna/prop).
    if not has_geom and height_m is not None and footprint_m is not None:
        ct = "bbox"
        has_geom = True
    if not has_geom:
        defaults = category_omni_defaults(category)
        ct = str(defaults.get("control_type", "none"))
        bbox_preset = bbox_preset or defaults.get("bbox_preset")
        pose_preset = pose_preset or defaults.get("pose_preset")

    if pose_preset and not pose_file:
        pose_file = resolve_pose_preset(pose_preset)
        if ct == "none":
            ct = "pose"

    # Bbox/--size explícitos do caller — nunca sobrescrever com mold height/footprint.
    user_bbox = bbox is not None or size is not None
    if bbox is None and size is not None:
        bbox = list(size)
        if ct == "none":
            ct = "bbox"
    # Bbox Omni: bbox explícito, bbox_preset, ou (modo bbox) aspect de size_m.
    # size_m com pose continua só escala mundo — nunca injecta bbox.
    if bbox is None and bbox_preset and ct in ("none", "bbox"):
        bbox = resolve_bbox_preset(bbox_preset)
        if ct == "none":
            ct = "bbox"

    # height_m → size_m (metros). Aspect hint = bbox já resolvido / preset.
    if size_m is None and height_m is not None:
        aspect_hint = bbox
        if aspect_hint is None and bbox_preset:
            try:
                aspect_hint = resolve_bbox_preset(bbox_preset)
            except KeyError:
                aspect_hint = None
        size_m = size_m_from_height(
            height_m,
            footprint_m=footprint_m,
            bbox_aspect=aspect_hint,
        )

    # Molde do modelo (bbox Omni): Hunyuan enche este aspect. Escala pós-mesh
    # só mapeia unidades→metros (uniforme no eixo maior). Nunca em pose; nunca se
    # user passou bbox/--size explícito.
    # - height+footprint → aspect size_m prevalece sobre preset
    # - size_m anisotrópico → prevalece sobre bbox_preset (gate 10×5.5×2.2 vs
    #   building 0.86×1×0.64 — senão o preset engole o authoring)
    # - cube + size_m não-cúbico (slime/shade/mosquito)
    # - bbox ainda None + size_m
    author_mold = height_m is not None and footprint_m is not None
    if ct == "bbox" and size_m is not None and not user_bbox:
        aniso = not size_m_near_cube(size_m)
        if author_mold or aniso:
            bbox = size_m_to_bbox(size_m)
            bbox_preset = None
        elif bbox is None:
            bbox = size_m_to_bbox(size_m)

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
        "height_m": float(height_m) if height_m is not None else None,
        "footprint_m": float(footprint_m) if footprint_m is not None else None,
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
    if mc_level in (None, "", "auto"):
        # "auto" = default actual — sidecars antigos sem chave continuam ok.
        mc_level = None
    elif not isinstance(mc_level, str):
        # Explícito fica distinto de auto (0 literal ≠ auto=-1/octree) — senão
        # mudar auto→0 nunca invalidava o shape.
        mc_level = float(mc_level)
    size_m = controls.get("size_m")
    if size_m is not None:
        try:
            size_m = [round(float(v), 4) for v in size_m]
            if len(size_m) != 3:
                size_m = None
        except (TypeError, ValueError):
            size_m = None
    # Seed de RE-ROLL explícito (manifest ``seed:`` via GameAssets). O seed
    # determinístico (seed_base+hash) NÃO entra aqui — senão sidecars antigos
    # sem a chave ficavam todos stale. Override ausente ≡ None.
    seed = controls.get("seed")
    if seed in (None, ""):
        seed = None
    else:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = None
    # Octree efectivo do generate (autotune ou override). Gravado no sidecar
    # para QA/debug; no stale-check, ``None`` no esperado ignora o valor
    # gravado (autotune VRAM-dependente não invalida). Override explícito no
    # manifest (``text3d.octree_resolution``) entra no expected e invalida.
    octree = controls.get("octree_resolution")
    if octree in (None, ""):
        octree = None
    else:
        try:
            octree = int(octree)
        except (TypeError, ValueError):
            octree = None
    # Escala canónica dos presets bbox (docs Omni 0-1). Mudar isto tem de
    # invalidar sidecars — shapes gerados com max=2 clipavam no MC.
    from .utils.omni_controls import OMNI_BBOX_AXIS_MAX

    # Sempre gravar bbox resolvido: mudar valores em BBOX_PRESETS (ex. tree
    # 0.35→0.55) tem de invalidar sidecars que só tinham o nome do preset.
    bbox_vals = controls.get("bbox")
    if bbox_vals is None and bbox_preset:
        try:
            bbox_vals = resolve_bbox_preset(str(bbox_preset))
        except KeyError:
            bbox_vals = None
    if bbox_vals is not None:
        try:
            bbox_vals = [round(float(v), 4) for v in bbox_vals]
        except (TypeError, ValueError):
            bbox_vals = None

    return {
        "control_type": controls.get("control_type") or "none",
        "bbox": bbox_vals,
        "bbox_preset": bbox_preset,
        "pose_preset": pose_preset,
        "pose_file": pose_file,
        "point_cloud": Path(point).name if point else None,
        "voxel_mesh": Path(voxel).name if voxel else None,
        "point_from": controls.get("point_from"),
        "bounds_mode": bounds_mode,
        "mc_level": mc_level,
        "size_m": size_m,
        "seed": seed,
        "octree_resolution": octree,
        "bbox_axis_max": float(OMNI_BBOX_AXIS_MAX),
    }


def shape_omni_sidecar_path(shape_glb: str | Path) -> Path:
    """``foo_shape.glb`` → ``foo_shape.glb.omni.json``."""
    return Path(str(shape_glb) + ".omni.json")


# Schema do bloco ``debug`` no sidecar — bump quando a forma mudar de forma
# incompatível (só documentação; leitores devem tolerar chaves extra).
OMNI_DEBUG_SCHEMA = 1


def json_safe(obj: Any) -> Any:
    """Converte valores para JSON (Path→str, dataclass→dict, numpy→python)."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
            return None
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "_asdict"):
        return json_safe(obj._asdict())
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return json_safe(asdict(obj))
    # numpy / torch scalars
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except Exception:
            pass
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        try:
            return json_safe(tolist())
        except Exception:
            pass
    return str(obj)


def bbox_tune_to_debug(bt: Any) -> dict[str, Any] | None:
    """Serializa ``BBoxTuneResult`` (ou dict) para o bloco debug."""
    if bt is None:
        return None
    if hasattr(bt, "__dataclass_fields__"):
        from dataclasses import asdict

        raw = asdict(bt)
    elif isinstance(bt, dict):
        raw = dict(bt)
    else:
        return None
    return json_safe(raw)


def build_generate_debug(
    *,
    seconds: float | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    bbox_tune: Any = None,
    morph_close_m: float | None = None,
    morph_close_voxels: float | None = None,
    morph_source: str | None = None,
    decode: dict[str, Any] | None = None,
    generation: dict[str, Any] | None = None,
    omni_resolved: dict[str, Any] | None = None,
    hardware: dict[str, Any] | None = None,
    accel: dict[str, Any] | None = None,
    mesh: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta bloco ``debug`` completo do sidecar ``*.omni.json``.

    Tudo é opcional — callers passam o que tiverem. Tamanho sem limite
    (QA/reprodução). Não entra no fingerprint de resume.
    """
    from datetime import UTC, datetime

    bt_dbg = bbox_tune_to_debug(bbox_tune)
    morph: dict[str, Any] | None = None
    close_m = morph_close_m
    if close_m is None and bt_dbg is not None:
        close_m = bt_dbg.get("morph_close")
    voxel_m = bt_dbg.get("voxel_m") if bt_dbg else None
    char_m = bt_dbg.get("char_m") if bt_dbg else None
    octree = bt_dbg.get("octree") if bt_dbg else None
    if close_m is not None or morph_close_voxels is not None or voxel_m:
        morph = {
            "close_m": close_m,
            "close_voxels": morph_close_voxels,
            "voxel_m": voxel_m,
            "char_m": char_m,
            "octree": octree,
            "source": morph_source or ("bbox_tune" if bt_dbg and bt_dbg.get("morph_close") is not None else None),
        }

    out: dict[str, Any] = {
        "schema": OMNI_DEBUG_SCHEMA,
        "written_at": datetime.now(UTC).isoformat(),
        "tool": "text3d",
        "seconds": round(float(seconds), 3) if seconds is not None else None,
        "timing": {
            "total_s": round(float(seconds), 3) if seconds is not None else None,
            "started_at": started_at,
            "finished_at": finished_at or datetime.now(UTC).isoformat(),
        },
        "bbox_tune": bt_dbg,
        "morph": morph,
        "decode": json_safe(decode) if decode else None,
        "generation": json_safe(generation) if generation else None,
        "omni_resolved": json_safe(omni_resolved) if omni_resolved else None,
        "hardware": json_safe(hardware) if hardware else None,
        "accel": json_safe(accel) if accel else None,
        "mesh": json_safe(mesh) if mesh else None,
        "request": json_safe(request) if request else None,
    }
    if extra:
        for k, v in extra.items():
            if k not in out or out[k] is None:
                out[k] = json_safe(v)
    # Drop Nones de topo (bloco interno pode ter nulls — ok para debug).
    return {k: v for k, v in out.items() if v is not None}


def mesh_stats_for_debug(mesh: Any, *, path: Path | str | None = None) -> dict[str, Any]:
    """Faces/verts/extents/bytes a partir de trimesh ou path GLB."""
    stats: dict[str, Any] = {}
    if mesh is not None:
        faces = getattr(mesh, "faces", None)
        verts = getattr(mesh, "vertices", None)
        try:
            stats["faces"] = len(faces) if faces is not None else None
        except TypeError:
            stats["faces"] = None
        try:
            stats["vertices"] = len(verts) if verts is not None else None
        except TypeError:
            stats["vertices"] = None
        extents = getattr(mesh, "extents", None)
        if extents is not None:
            with contextlib.suppress(TypeError, ValueError):
                stats["extents"] = [round(float(v), 6) for v in extents]
        bounds = getattr(mesh, "bounds", None)
        if bounds is not None:
            with contextlib.suppress(Exception):
                stats["bounds"] = json_safe(bounds)
    if path is not None:
        p = Path(path)
        stats["path"] = str(p)
        with contextlib.suppress(OSError):
            stats["bytes"] = int(p.stat().st_size)
    return {k: v for k, v in stats.items() if v is not None}


def write_omni_fingerprint(
    shape_glb: str | Path,
    controls: dict[str, Any],
    *,
    debug: dict[str, Any] | None = None,
) -> Path:
    """Grava sidecar JSON junto do shape (resume + debug completo).

    Chaves de fingerprint (resume) ficam no topo. ``debug`` (opcional) é
    ignorado por :func:`omni_fingerprint` / stale-check — pode ser grande.
    """
    import json

    path = shape_omni_sidecar_path(shape_glb)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = omni_fingerprint(controls)
    if debug:
        # Preserve topology_fix de um patch anterior se o caller regravar
        # só o bloco de generate (raro; patch_omni_debug é o caminho normal).
        existing = read_omni_fingerprint(shape_glb)
        prev_dbg = existing.get("debug") if isinstance(existing, dict) else None
        merged_dbg = dict(debug)
        if isinstance(prev_dbg, dict) and "topology_fix" in prev_dbg and "topology_fix" not in merged_dbg:
            merged_dbg["topology_fix"] = prev_dbg["topology_fix"]
        payload["debug"] = json_safe(merged_dbg)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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


def patch_omni_debug(shape_glb: str | Path, patch: dict[str, Any]) -> Path | None:
    """Merge raso em ``debug`` do sidecar existente (ex.: topology-fix).

    Se não houver sidecar, cria um mínimo só com ``debug`` (sem fingerprint
    geométrico — resume trata ausência de chaves Omni como «não stale»).
    """
    import json
    from datetime import UTC, datetime

    path = shape_omni_sidecar_path(shape_glb)
    existing = read_omni_fingerprint(shape_glb) or {}
    dbg = existing.get("debug")
    if not isinstance(dbg, dict):
        dbg = {"schema": OMNI_DEBUG_SCHEMA, "tool": "text3d"}
    for k, v in json_safe(patch).items():
        if isinstance(v, dict) and isinstance(dbg.get(k), dict):
            merged = dict(dbg[k])
            merged.update(v)
            dbg[k] = merged
        else:
            dbg[k] = v
    dbg["patched_at"] = datetime.now(UTC).isoformat()
    existing["debug"] = dbg
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def omni_fingerprint_matches(shape_glb: str | Path, controls: dict[str, Any]) -> bool:
    """True se shape existe e sidecar coincide com ``controls``."""
    existing = read_omni_fingerprint(shape_glb)
    if existing is None:
        return False
    # Sidecars pré-``bbox_axis_max`` usavam escala max=2 (clip no MC) — regenerar.
    if "bbox_axis_max" not in existing:
        return False
    return omni_fingerprint(controls) == omni_fingerprint(existing)
