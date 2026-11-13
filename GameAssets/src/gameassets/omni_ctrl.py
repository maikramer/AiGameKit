"""Resolução de controlos Omni (Text3D) a partir de profile + manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OmniControls:
    """Subset de controlos geométricos Hunyuan3D-Omni."""

    control_type: str | None = None
    bbox_preset: str | None = None
    bbox: tuple[float, ...] | None = None
    size_m: tuple[float, ...] | None = None
    # Authoring: altura (+ footprint) → size_m e, em modo bbox, molde Omni.
    height_m: float | None = None
    footprint_m: float | None = None
    pose_preset: str | None = None
    pose_file: str | None = None
    point_cloud: str | None = None
    voxel_mesh: str | None = None
    point_from: str | None = None  # sibling asset id → shape.glb


def _as_float_tuple(raw: Any, *, n: int | None = None) -> tuple[float, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        vals = tuple(float(x) for x in raw)
        if n is not None and len(vals) != n:
            raise ValueError(f"esperado {n} floats, recebeu {len(vals)}")
        return vals
    raise ValueError(f"esperado lista de floats, recebeu {type(raw).__name__}")


def omni_from_dict(raw: Any) -> OmniControls:
    """Parse bloco ``omni:`` de game.yaml / manifest."""
    if raw is None:
        return OmniControls()
    if not isinstance(raw, dict):
        raise ValueError("omni deve ser um mapeamento")
    ct = raw.get("control_type")
    ct_s = str(ct).strip().lower() if ct not in (None, "") else None
    bp = raw.get("bbox_preset")
    bp_s = str(bp).strip().lower() if bp not in (None, "") else None
    pp = raw.get("pose_preset")
    pp_s = str(pp).strip().lower() if pp not in (None, "") else None
    pf = raw.get("pose_file")
    pf_s = str(pf).strip() if pf not in (None, "") else None
    pc = raw.get("point_cloud")
    pc_s = str(pc).strip() if pc not in (None, "") else None
    vm = raw.get("voxel_mesh")
    vm_s = str(vm).strip() if vm not in (None, "") else None
    pfrom = raw.get("point_from")
    pfrom_s = str(pfrom).strip() if pfrom not in (None, "") else None
    hm = raw.get("height_m")
    fm = raw.get("footprint_m")
    return OmniControls(
        control_type=ct_s,
        bbox_preset=bp_s,
        bbox=_as_float_tuple(raw.get("bbox")),
        size_m=_as_float_tuple(raw.get("size_m"), n=3),
        height_m=float(hm) if hm is not None else None,
        footprint_m=float(fm) if fm is not None else None,
        pose_preset=pp_s,
        pose_file=pf_s,
        point_cloud=pc_s,
        voxel_mesh=vm_s,
        point_from=pfrom_s,
    )


def merge_omni(base: OmniControls, override: OmniControls) -> OmniControls:
    """Override só campos não-None do ``override``."""
    kw: dict[str, Any] = {}
    for f in fields(OmniControls):
        ov = getattr(override, f.name)
        kw[f.name] = ov if ov is not None else getattr(base, f.name)
    return OmniControls(**kw)


def resolve_point_from(
    omni: OmniControls,
    *,
    sibling_shape: Path | None,
) -> OmniControls:
    """Se ``point_from`` definido, preenche ``point_cloud`` com path do sibling."""
    if not omni.point_from:
        return omni
    if omni.point_cloud:
        return omni
    if sibling_shape is None or not sibling_shape.is_file():
        raise FileNotFoundError(f"omni.point_from={omni.point_from!r}: shape sibling em falta ({sibling_shape})")
    return replace(
        omni,
        control_type=omni.control_type or "point",
        point_cloud=str(sibling_shape.resolve()),
    )


def expand_omni_world_size(omni: OmniControls, *, category: str | None = None) -> OmniControls:
    """Resolve ``height_m``/``footprint_m`` → ``size_m`` + molde bbox (Text3D merge)."""
    if omni.height_m is None and omni.size_m is not None:
        return omni
    if omni.height_m is None:
        return omni
    try:
        from text3d.omni_presets import merge_omni_controls
    except ImportError:
        return omni
    m = merge_omni_controls(
        control_type=omni.control_type,
        bbox=list(omni.bbox) if omni.bbox is not None else None,
        bbox_preset=omni.bbox_preset,
        size_m=list(omni.size_m) if omni.size_m is not None else None,
        height_m=omni.height_m,
        footprint_m=omni.footprint_m,
        pose_file=omni.pose_file,
        pose_preset=omni.pose_preset,
        point_cloud=omni.point_cloud,
        voxel_mesh=omni.voxel_mesh,
        category=category,
    )
    bbox = m.get("bbox")
    size_m = m.get("size_m")
    return OmniControls(
        control_type=m.get("control_type") or omni.control_type,
        bbox_preset=m.get("bbox_preset"),
        bbox=tuple(float(x) for x in bbox) if bbox is not None else None,
        size_m=tuple(float(x) for x in size_m) if size_m is not None else None,
        height_m=omni.height_m,
        footprint_m=omni.footprint_m,
        pose_preset=m.get("pose_preset") or omni.pose_preset,
        pose_file=m.get("pose_file") or omni.pose_file,
        point_cloud=omni.point_cloud,
        voxel_mesh=omni.voxel_mesh,
        point_from=omni.point_from,
    )


def omni_to_cli_flags(omni: OmniControls) -> list[str]:
    """Flags para ``text3d generate`` / generate-batch item."""
    omni = expand_omni_world_size(omni)
    args: list[str] = []
    if omni.control_type and omni.control_type != "none":
        args.extend(["--control-type", omni.control_type])
    if omni.pose_preset:
        args.extend(["--pose-preset", omni.pose_preset])
    if omni.pose_file:
        args.extend(["--pose-file", omni.pose_file])
    if omni.bbox_preset:
        args.extend(["--bbox-preset", omni.bbox_preset])
    if omni.bbox is not None:
        args.extend(["--bbox", ",".join(str(x) for x in omni.bbox)])
    if omni.size_m is not None:
        # Metros absolutos: Text3D faz aspect Omni + autotune octree/steps.
        args.extend(["--size-m", ",".join(str(x) for x in omni.size_m)])
    elif omni.height_m is not None:
        args.extend(["--height-m", str(omni.height_m)])
        if omni.footprint_m is not None:
            args.extend(["--footprint-m", str(omni.footprint_m)])
    if omni.point_cloud:
        args.extend(["--point-cloud", omni.point_cloud])
    if omni.voxel_mesh:
        args.extend(["--voxel-mesh", omni.voxel_mesh])
    return args


def omni_to_batch_item(omni: OmniControls) -> dict[str, Any]:
    """Campos Omni para item do shape_manifest.json."""
    omni = expand_omni_world_size(omni)
    out: dict[str, Any] = {}
    if omni.control_type:
        out["control_type"] = omni.control_type
    if omni.pose_preset:
        out["pose_preset"] = omni.pose_preset
    if omni.pose_file:
        out["pose_file"] = omni.pose_file
    if omni.bbox_preset:
        out["bbox_preset"] = omni.bbox_preset
    if omni.bbox is not None:
        out["bbox"] = list(omni.bbox)
    if omni.size_m is not None:
        out["size_m"] = list(omni.size_m)
    if omni.height_m is not None:
        out["height_m"] = omni.height_m
    if omni.footprint_m is not None:
        out["footprint_m"] = omni.footprint_m
    if omni.point_cloud:
        out["point_cloud"] = omni.point_cloud
    if omni.voxel_mesh:
        out["voxel_mesh"] = omni.voxel_mesh
    return out


def omni_has_geom_control(omni: OmniControls) -> bool:
    """True se há sinal Omni geométrico (pose/bbox/point/voxel).

    ``size_m`` / ``height_m`` são metros mundo — não bloqueiam soft-fill de pose/bbox
    (excepto ``height_m``+``footprint_m`` sem outro controlo → molde bbox no merge).
    """
    return bool(
        (omni.control_type and omni.control_type != "none")
        or omni.pose_preset
        or omni.pose_file
        or omni.bbox_preset
        or omni.bbox is not None
        or omni.point_cloud
        or omni.voxel_mesh
        or omni.point_from
        or (omni.height_m is not None and omni.footprint_m is not None)
    )


def omni_is_active(omni: OmniControls) -> bool:
    """True se há qualquer campo Omni (inclui ``size_m``/``height_m``)."""
    return omni_has_geom_control(omni) or omni.size_m is not None or omni.height_m is not None


# Mirror de ``text3d.omni_presets.CATEGORY_OMNI_DEFAULTS`` — fallback quando Text3D
# não está instalado (CI GameAssets / orquestração sem o pacote GPU).
_CATEGORY_OMNI_DEFAULTS_FALLBACK: dict[str, dict[str, str]] = {
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


def _category_omni_defaults(category: str | None) -> dict[str, str]:
    """Defaults soft por categoria; Text3D se disponível, senão fallback local."""
    if not category:
        return {}
    try:
        from text3d.omni_presets import category_omni_defaults

        return dict(category_omni_defaults(category))
    except ImportError:
        key = str(category).strip().lower()
        base = _CATEGORY_OMNI_DEFAULTS_FALLBACK.get(key)
        return dict(base) if base else {}


def softfill_omni_from_category(omni: OmniControls, category: str | None) -> OmniControls:
    """Preenche Omni inactivo com defaults por categoria (Text3D CATEGORY_OMNI).

    Não sobrescreve campos já definidos. ``size_m`` sozinho ainda recebe soft-fill
    (pose humanoid / bbox prop) — senão vira só escala sem controlo geométrico.
    """
    if omni_has_geom_control(omni):
        return expand_omni_world_size(omni, category=category)
    defaults = _category_omni_defaults(category)
    if not defaults:
        return expand_omni_world_size(omni, category=category)
    filled = OmniControls(
        control_type=defaults.get("control_type"),
        bbox_preset=defaults.get("bbox_preset"),
        pose_preset=defaults.get("pose_preset"),
        bbox=None,
        size_m=omni.size_m,
        height_m=omni.height_m,
        footprint_m=omni.footprint_m,
        pose_file=None,
        point_cloud=None,
        voxel_mesh=None,
        point_from=omni.point_from,
    )
    return expand_omni_world_size(filled, category=category)


def _norm_control_type(raw: Any) -> str:
    return str(raw).strip().lower() if raw not in (None, "") else "none"


def _expected_control_type(omni: OmniControls) -> str:
    """Controlo que o ``merge_omni_controls`` resolveria (soft-fill mínimo)."""
    ct = _norm_control_type(omni.control_type)
    if ct != "none":
        return ct
    if omni.point_cloud:
        return "point"
    if omni.voxel_mesh:
        return "voxel"
    if omni.pose_preset or omni.pose_file:
        return "pose"
    if omni.bbox is not None or omni.bbox_preset:
        return "bbox"
    return "none"


def _round_floats(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    try:
        return [round(float(v), 4) for v in raw]
    except (TypeError, ValueError):
        return None


# Eixo canónico do bbox Omni (docs 0-1) — espelho de
# ``text3d.utils.omni_controls.OMNI_BBOX_AXIS_MAX`` para o fallback.
_FALLBACK_BBOX_AXIS_MAX = 1.0


def _shape_omni_stale_without_text3d(
    shape_glb: Path,
    omni: OmniControls,
    *,
    bounds_mode: str | None,
    mc_level: float | str | None,
    seed: int | None,
    octree_resolution: int | None,
) -> bool:
    """Fallback de :func:`shape_omni_stale` quando ``text3d`` não importa.

    O GameAssets corre no seu próprio venv (as tools são venvs separados): sem
    ``text3d``, ``merge_omni_controls``/``omni_fingerprint`` não existem e o
    caminho antigo devolvia ``False`` — o resume **reutilizava shapes com Omni
    diferente** (mudar ``pose_preset``/``size_m`` no manifest não regenerava
    nada; visto no npc_merchant a 2026-11-13). Aqui comparam-se os campos do
    sidecar que mudam a geometria, espelhando as regras do merge. Conservador:
    só invalida o que o manifest define; ``pose_digest`` (identidade do
    esqueleto) fica de fora porque exige resolver o preset.
    """
    try:
        existing = json.loads(Path(str(shape_glb) + ".omni.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(existing, dict):
        return False

    if _norm_control_type(existing.get("control_type")) != _expected_control_type(omni):
        return True
    ex_pose = (existing.get("pose_preset") or "").strip().lower() or None
    want_pose = (omni.pose_preset or "").strip().lower() or None
    if ex_pose != want_pose:
        return True
    if want_pose is None and omni.pose_file:
        ex_pf = existing.get("pose_file")
        if not ex_pf or Path(str(ex_pf)).name != Path(str(omni.pose_file)).name:
            return True
    if omni.bbox is not None and _round_floats(existing.get("bbox")) != _round_floats(omni.bbox):
        return True
    # ``bbox_preset`` só quando ambos existem: com size_m anisotrópico o merge
    # descarta o preset (o size_m vira o molde) e o sidecar grava None.
    want_bp = (omni.bbox_preset or "").strip().lower() or None
    ex_bp = (existing.get("bbox_preset") or "").strip().lower() or None
    if want_bp and ex_bp and want_bp != ex_bp:
        return True
    ex_sm = _round_floats(existing.get("size_m"))
    want_sm = _round_floats(omni.size_m)
    # Remover size_m também invalida: o shape antigo foi escalado (e o
    # bbox_tune calculado) com ele.
    if ex_sm != want_sm:
        return True
    for key, want in (("point_cloud", omni.point_cloud), ("voxel_mesh", omni.voxel_mesh)):
        if want:
            ex_v = existing.get(key)
            if not ex_v or Path(str(ex_v)).name != Path(str(want)).name:
                return True

    def _norm_bounds(raw: Any) -> str | None:
        val = str(raw).strip().lower() if raw not in (None, "") else ""
        return None if val in ("", "auto") else val

    if bounds_mode is not None and _norm_bounds(existing.get("bounds_mode")) != _norm_bounds(bounds_mode):
        return True

    def _norm_mc(raw: Any) -> float | None:
        if raw in (None, "") or (isinstance(raw, str) and raw.strip().lower() == "auto"):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    if mc_level is not None and _norm_mc(existing.get("mc_level")) != _norm_mc(mc_level):
        return True
    if seed is not None:
        ex_seed = existing.get("seed")
        try:
            ex_seed_i = int(ex_seed) if ex_seed not in (None, "") else None
        except (TypeError, ValueError):
            ex_seed_i = None
        if ex_seed_i != int(seed):
            return True
    if octree_resolution is not None:
        ex_oct = existing.get("octree_resolution")
        try:
            ex_oct_i = int(ex_oct) if ex_oct not in (None, "") else None
        except (TypeError, ValueError):
            ex_oct_i = None
        if ex_oct_i != int(octree_resolution):
            return True
    try:
        ex_axis = existing.get("bbox_axis_max")
        if ex_axis is not None and abs(float(ex_axis) - _FALLBACK_BBOX_AXIS_MAX) > 1e-6:
            return True
    except (TypeError, ValueError):
        pass
    return False


def shape_omni_stale(
    shape_glb: Path,
    omni: OmniControls,
    *,
    category: str | None = None,
    bounds_mode: str | None = None,
    mc_level: float | str | None = None,
    seed: int | None = None,
    octree_resolution: int | None = None,
) -> bool:
    """True se shape existe mas fingerprint Omni ≠ esperado (precisa regenerar).

    Sem sidecar → **não** stale (mantém shape; evita apagar mesh boa no resume).
    Mudança de Omni sem sidecar: usar ``--force``. Com sidecar: compara fingerprint.
    ``bounds_mode`` / ``mc_level`` vêm do ``text3d:`` profile (decode knobs).
    ``seed`` é o override explícito do manifest (``seed:``) — mudá-lo invalida
    só este shape (re-roll cirúrgico); o determinístico nunca entra aqui.
    ``octree_resolution``: só o override explícito do manifest entra no expected;
    autotune (``None``) ignora o valor gravado no sidecar (VRAM-dependente).
    Sem ``text3d`` importável (venv do GameAssets) → compara os campos do
    sidecar diretamente; ver :func:`_shape_omni_stale_without_text3d`.
    """
    if not shape_glb.is_file():
        return True
    try:
        from text3d.omni_presets import merge_omni_controls, omni_fingerprint, read_omni_fingerprint
    except ImportError:
        return _shape_omni_stale_without_text3d(
            shape_glb,
            omni,
            bounds_mode=bounds_mode,
            mc_level=mc_level,
            seed=seed,
            octree_resolution=octree_resolution,
        )

    expected = merge_omni_controls(
        control_type=omni.control_type,
        bbox=list(omni.bbox) if omni.bbox is not None else None,
        bbox_preset=omni.bbox_preset,
        size_m=list(omni.size_m) if omni.size_m is not None else None,
        height_m=omni.height_m,
        footprint_m=omni.footprint_m,
        pose_file=omni.pose_file,
        pose_preset=omni.pose_preset,
        point_cloud=omni.point_cloud,
        voxel_mesh=omni.voxel_mesh,
        category=category,
    )
    if bounds_mode is not None:
        expected["bounds_mode"] = bounds_mode
    if mc_level is not None:
        expected["mc_level"] = mc_level
    expected["seed"] = seed
    if octree_resolution is not None:
        expected["octree_resolution"] = int(octree_resolution)
    existing = read_omni_fingerprint(shape_glb)
    if existing is None:
        return False
    # Normaliza o sidecar pelo **mesmo** merge do expected. O generate grava
    # `bbox_preset` (a flag CLI), mas o merge descarta-o quando `size_m` é
    # anisotrópico (o size_m vira o bbox Omni e o preset é metadata) — sem esta
    # normalização o resume via "preset chapel ≠ None" e regenerava o shape de
    # TODOS os edifícios em cada resume (2026-10-30, chapel).
    # O sidecar grava o bbox **resolvido** (não é override do utilizador). Só
    # o passamos ao merge quando o manifest tem um bbox explícito; caso
    # contrário o merge vê `user_bbox=True`, mantém o preset e continua a
    # divergir do expected (que não tem bbox explícito).
    _bbox_norm = existing.get("bbox") if getattr(omni, "bbox", None) is not None else None
    existing_norm = merge_omni_controls(
        control_type=existing.get("control_type"),
        bbox=_bbox_norm,
        bbox_preset=existing.get("bbox_preset"),
        size_m=existing.get("size_m"),
        height_m=existing.get("height_m"),
        footprint_m=existing.get("footprint_m"),
        pose_file=existing.get("pose_file"),
        pose_preset=existing.get("pose_preset"),
        point_cloud=existing.get("point_cloud"),
        voxel_mesh=existing.get("voxel_mesh"),
        category=category,
    )
    # Preserva também a identidade do esqueleto e a escala Omni gravadas: se o
    # path do preset já não resolver, o merge devolve pose_bones=None e o asset
    # ficava stale para sempre; um bbox_axis_max antigo (max=2) tem de invalidar.
    for _k in ("bounds_mode", "mc_level", "seed", "octree_resolution", "bbox_axis_max", "pose_bones"):
        _v = existing.get(_k)
        if _v not in (None, ""):
            existing_norm[_k] = _v
    # O digest da pose tem de vir do sidecar, não do merge: o merge re-resolve
    # o preset *atual* e um shape antigo passaria por atual. Ausente = gerado
    # antes da chave existir → regenerar.
    existing_norm["pose_digest"] = existing.get("pose_digest")
    fp_exp = omni_fingerprint(expected)
    fp_got = omni_fingerprint(existing_norm)
    # Autotune: sidecar pode ter o octree efectivo mas o esperado não pinou —
    # não invalidar por diferença VRAM/máquina.
    if fp_exp.get("octree_resolution") is None:
        fp_exp.pop("octree_resolution", None)
        fp_got.pop("octree_resolution", None)
    return fp_exp != fp_got


def _derived_glb_paths_for_shape(shape_glb: Path) -> list[Path]:
    """GLBs derivados do asset deste ``*_shape.glb`` (meshes/ + _intermediate/).

    ``rock_shape.glb`` → base ``rock``; apanha ``rock.glb``, ``rock_clean``,
    ``_painted``, ``_to_paint``, ``_rigged*``, ``_lod*``, ``_collision`` …
    **Sem** prefixo cego: ``rock_mossy_clean`` tem base ``rock_mossy`` ≠
    ``rock`` e fica de fora.
    """
    from .paths import _base_stem

    base = _base_stem(shape_glb.stem)
    if not base:
        return []
    meshes_dir = shape_glb.parent
    if meshes_dir.name == "_intermediate":
        meshes_dir = meshes_dir.parent
    dirs = [meshes_dir, meshes_dir / "_intermediate"]
    out: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for glb in d.glob("*.glb"):
            if glb == shape_glb:
                continue
            if _base_stem(glb.stem) == base:
                out.append(glb)
    return out


def _unlink_shape_outputs(shape_glb: Path) -> None:
    """Apaga shape + sidecar **e os derivados** (clean/painted/lods/collision/rig).

    Shape regenerado com geometria nova torna todos os intermediários a jusante
    stale — reutilizá-los (clean órfão → paint/bake sobre mesh antiga) era
    regressão silenciosa. A cascata faz o resume/batch reconstruir o DAG todo
    só deste asset (topology-fix → paint → bake → LOD → rig → animate).
    """
    for derived in _derived_glb_paths_for_shape(shape_glb):
        derived.unlink(missing_ok=True)
    shape_glb.unlink(missing_ok=True)
    Path(str(shape_glb) + ".omni.json").unlink(missing_ok=True)


# Tolerância do warning de constraint bbox vs size_m (review simple-rpg 2026-07).
BBOX_CONSTRAINT_RATIO = 0.8

# Categorias com presets propositadamente finos (sword/door): as protrusões
# (cabeça de machado, punho) vazam da caixa sem distorcer — o modelo tolera
# excesso. Constraint aí é o design do preset, não defeito de authoring.
_BBOX_THIN_PRESET_CATEGORIES = frozenset({"weapon", "tool"})


def omni_bbox_constraint_warnings(omni: OmniControls, category: str | None = None) -> list[str]:
    """Avisos de authoring quando a bbox Omni efetiva **contrai** eixos vs ``size_m``.

    Regra empírica (review de shapes simple-rpg): a bbox é um condicionador
    soft — conteúdo que excede levemente a caixa sai bem (goblin T-pose, cabeça
    de machado), mas caixa **menor** que o conteúdo num eixo pode esmagar/cortar
    esse eixo (tree_oak 0.35 vs copa larga → coroa plana; mushroom_red blob
    0.785H → caule stubby) e inverter a hierarquia de aspecto pode esticar o
    modelo (scorpion_nest monte 0.48H em bbox building 1.0H → tenda cónica).
    Folga a mais é inócua — só se avisa no sentido de constraint, nunca de folga.

    ``size_m`` é só proxy do conteúdo (a imagem manda): há falsos positivos
    tolerados pelo modelo (scorpion, dead_willow). Aviso é condicional —
    investigar **se** o shape sair esmagado/cortado.

    Categorias weapon/tool são ignoradas: os presets finos (sword/door) são
    intencionalmente mais estreitos que o conteúdo.

    Compara aspectos normalizados (max=1) por eixo: dispara quando
    ``bbox_aspect[i] < BBOX_CONSTRAINT_RATIO * size_m_aspect[i]``. Sem
    ``size_m`` ou sem controlo bbox → sem aviso.

    Returns:
        Lista de avisos (vazia se OK ou não aplicável).
    """
    if (category or "").strip().lower() in _BBOX_THIN_PRESET_CATEGORIES:
        return []
    if omni.size_m is None or len(omni.size_m) != 3:
        return []
    bbox = omni.bbox
    if bbox is None and omni.bbox_preset:
        try:
            from text3d.omni_presets import resolve_bbox_preset
        except ImportError:
            return []
        try:
            bbox = tuple(resolve_bbox_preset(omni.bbox_preset))
        except KeyError:
            return []
    if bbox is None or len(bbox) != 3 or min(bbox) <= 0:
        return []
    s_max = max(omni.size_m)
    b_max = max(bbox)
    if s_max <= 0 or b_max <= 0:
        return []
    size_aspect = [float(v) / s_max for v in omni.size_m]
    bbox_aspect = [float(v) / b_max for v in bbox]
    out: list[str] = []
    for i, axis in enumerate(("L", "H", "W")):
        if bbox_aspect[i] < BBOX_CONSTRAINT_RATIO * size_aspect[i]:
            hint = ", ".join(f"{v:g}" for v in size_aspect)
            out.append(
                f"bbox {list(bbox)} < size_m aspect no eixo {axis} (size_m {list(omni.size_m)}) — "
                f"se o shape sair esmagado/cortado nesse eixo, considera bbox explícito [{hint}] "
                f"(= aspecto size_m)"
            )
    return out


def prepare_shape_for_generation(
    shape_glb: Path,
    omni: OmniControls,
    *,
    force: bool = False,
    category: str | None = None,
    clean_glb: Path | None = None,
    bounds_mode: str | None = None,
    mc_level: float | str | None = None,
    seed: int | None = None,
    octree_resolution: int | None = None,
) -> bool:
    """Decide se o shape deve ir ao ``generate-batch`` e limpa output stale.

    ``text3d generate-batch`` faz skip silencioso se o GLB existe e não há
    ``--force``. Por isso, quando vamos regenerar (force / omni stale),
    **apagamos** o shape existente antes de enfileirar — senão o Stage 1 fica
    no-op e o pipeline continua com mesh antiga.

    Shape ausente → sempre gerar (mesmo com ``clean`` órfão). Clean sem shape
    era skip silencioso e deixava meshes gordas/stale a avançar para paint.

    ``seed`` = override ``seed:`` do manifest (fingerprint — ver
    :func:`shape_omni_stale`).

    Returns:
        ``True`` — incluir no manifest de shape (gerar).
        ``False`` — shape fresco; skip Stage 1.
    """
    del clean_glb  # API estável; orphan clean já não faz skip
    if force:
        if shape_glb.is_file():
            _unlink_shape_outputs(shape_glb)
        return True
    if not shape_glb.is_file():
        return True
    if shape_omni_stale(
        shape_glb,
        omni,
        category=category,
        bounds_mode=bounds_mode,
        mc_level=mc_level,
        seed=seed,
        octree_resolution=octree_resolution,
    ):
        _unlink_shape_outputs(shape_glb)
        return True
    return False
