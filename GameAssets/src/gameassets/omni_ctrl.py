"""Resolução de controlos Omni (Text3D) a partir de profile + manifest."""

from __future__ import annotations

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
    return OmniControls(
        control_type=ct_s,
        bbox_preset=bp_s,
        bbox=_as_float_tuple(raw.get("bbox")),
        size_m=_as_float_tuple(raw.get("size_m"), n=3),
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


def omni_to_cli_flags(omni: OmniControls) -> list[str]:
    """Flags para ``text3d generate`` / generate-batch item."""
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
    if omni.point_cloud:
        args.extend(["--point-cloud", omni.point_cloud])
    if omni.voxel_mesh:
        args.extend(["--voxel-mesh", omni.voxel_mesh])
    return args


def omni_to_batch_item(omni: OmniControls) -> dict[str, Any]:
    """Campos Omni para item do shape_manifest.json."""
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
    if omni.point_cloud:
        out["point_cloud"] = omni.point_cloud
    if omni.voxel_mesh:
        out["voxel_mesh"] = omni.voxel_mesh
    return out


def omni_is_active(omni: OmniControls) -> bool:
    return bool(
        (omni.control_type and omni.control_type != "none")
        or omni.pose_preset
        or omni.pose_file
        or omni.bbox_preset
        or omni.bbox is not None
        or omni.size_m is not None
        or omni.point_cloud
        or omni.voxel_mesh
        or omni.point_from
    )


def shape_omni_stale(
    shape_glb: Path,
    omni: OmniControls,
    *,
    category: str | None = None,
    bounds_mode: str | None = None,
    mc_level: float | str | None = None,
) -> bool:
    """True se shape existe mas fingerprint Omni ≠ esperado (precisa regenerar).

    Sem sidecar → **não** stale (mantém shape; evita apagar mesh boa no resume).
    Mudança de Omni sem sidecar: usar ``--force``. Com sidecar: compara fingerprint.
    ``bounds_mode`` / ``mc_level`` vêm do ``text3d:`` profile (decode knobs).
    """
    if not shape_glb.is_file():
        return True
    try:
        from text3d.omni_presets import merge_omni_controls, omni_fingerprint, read_omni_fingerprint
    except ImportError:
        return False

    expected = merge_omni_controls(
        control_type=omni.control_type,
        bbox=list(omni.bbox) if omni.bbox is not None else None,
        bbox_preset=omni.bbox_preset,
        size_m=list(omni.size_m) if omni.size_m is not None else None,
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
    existing = read_omni_fingerprint(shape_glb)
    if existing is None:
        return False
    return omni_fingerprint(expected) != omni_fingerprint(existing)


def _unlink_shape_outputs(shape_glb: Path) -> None:
    shape_glb.unlink(missing_ok=True)
    Path(str(shape_glb) + ".omni.json").unlink(missing_ok=True)


def prepare_shape_for_generation(
    shape_glb: Path,
    omni: OmniControls,
    *,
    force: bool = False,
    category: str | None = None,
    clean_glb: Path | None = None,
    bounds_mode: str | None = None,
    mc_level: float | str | None = None,
) -> bool:
    """Decide se o shape deve ir ao ``generate-batch`` e limpa output stale.

    ``text3d generate-batch`` faz skip silencioso se o GLB existe e não há
    ``--force``. Por isso, quando vamos regenerar (force / omni stale),
    **apagamos** o shape existente antes de enfileirar — senão o Stage 1 fica
    no-op e o pipeline continua com mesh antiga.

    Resume: shape ausente mas ``clean_glb`` válido → skip Stage 1 (paint no
    clean). Regenerar shape: ``--force``.

    Returns:
        ``True`` — incluir no manifest de shape (gerar).
        ``False`` — shape fresco / clean resume; skip Stage 1.
    """
    if force:
        if shape_glb.is_file():
            _unlink_shape_outputs(shape_glb)
        return True
    if not shape_glb.is_file():
        return not (clean_glb is not None and clean_glb.is_file())
    if shape_omni_stale(
        shape_glb,
        omni,
        category=category,
        bounds_mode=bounds_mode,
        mc_level=mc_level,
    ):
        _unlink_shape_outputs(shape_glb)
        return True
    return False
