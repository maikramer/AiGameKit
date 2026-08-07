"""Bridge: manifests GPU → specs UMS → run_gpu_wave (ou fallback).

Shape/paint + text2d/text2icon/texture2d/skymap2d/text2sound/terrain3d.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .ums_coord import (
    FALLBACK_SUBPROCESS,
    UmsJobResult,
    UmsJobSpec,
    results_as_batch_jsonl,
    run_gpu_wave,
)


def _resolve_path(manifest_dir: Path, raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (manifest_dir / p).resolve()


def resolve_text3d_vram_opts(
    sdnq_preset: str | None,
    memory_efficient: bool | None,
) -> tuple[str | None, bool]:
    """Resolve (sdnq_preset, memory_efficient) — explícito > hw_auto > admit-safe.

    ``None``/`None` → ``text3d.hardware`` (quando ``TEXT3D_HW_AUTO`` activo).
    Sem GPU/import: ``sdnq-int4`` + mem_eff para admit em ~6 GB.
    """
    if sdnq_preset is not None or memory_efficient is not None:
        preset = None if sdnq_preset in (None, "none", "") else str(sdnq_preset)
        if memory_efficient is not None:
            mem = bool(memory_efficient)
            if not mem:
                return preset, False
            return preset, True
        return preset, preset is not None

    try:
        from text3d.hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            hwp = detect_hardware_profile()
            preset = hwp.sdnq_preset
            mem = bool(preset) or bool(getattr(hwp, "offload", False))
            return preset, mem
    except Exception:
        pass
    return "sdnq-int4", True


def resolve_paint3d_vram_opts(
    memory_efficient: bool | None,
    sdnq_preset: str | None = None,
) -> tuple[str | None, bool]:
    """Resolve (sdnq_preset, memory_efficient) — explícito > hw_auto > admit-safe."""
    if memory_efficient is not None or sdnq_preset is not None:
        mem = bool(memory_efficient) if memory_efficient is not None else True
        if sdnq_preset not in (None, "none", ""):
            return str(sdnq_preset), mem
        return ("sdnq-uint8" if mem else None), mem

    try:
        from paint3d.hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            hwp = detect_hardware_profile()
            mem = bool(hwp.memory_efficient)
            return ("sdnq-uint8" if mem else None), mem
    except Exception:
        pass
    return "sdnq-uint8", True


def shape_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    steps: int | None = None,
    guidance: float = 4.5,
    octree_resolution: int | None = None,
    num_chunks: int | None = None,
    mc_level: float | str = "auto",
    bounds_mode: str = "auto",
    export_origin: str = "feet",
    quality: str | None = None,
    gpu_ids: list[int] | None = None,
    topology_fix: bool = False,
    sdnq_preset: str | None = None,
    memory_efficient: bool | None = None,
) -> list[UmsJobSpec]:
    """Converte items do shape_manifest.json em ``UmsJobSpec``.

    ``topology_fix=False`` no batch phased (clean fica no ensure_to_paint / master).
    Peak VRAM: profile explícito > hw_auto > admit-safe (não hardcode uint8).

    ``octree_resolution``/``steps``/``num_chunks`` omitidos (``None``) → não vão no
    payload UMS → ``bbox_tune`` size-based pode subir octree (ex. longhouse → 448).
    """
    try:
        from text3d.ums_payload import build_generate_request
    except ImportError:
        return []

    wave_sdnq, wave_mem = resolve_text3d_vram_opts(sdnq_preset, memory_efficient)

    def _opt_int(item: dict[str, Any], *keys: str, wave: int | None) -> int | None:
        for k in keys:
            if k in item and item[k] is not None:
                return int(item[k])
        return int(wave) if wave is not None else None

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        img = _resolve_path(manifest_dir, str(item["image"]))
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_steps = _opt_int(item, "steps", wave=steps)
        item_octree = _opt_int(item, "octree_resolution", "octree", wave=octree_resolution)
        item_chunks = _opt_int(item, "num_chunks", "chunks", wave=num_chunks)
        # Wave já resolveu via hw_auto; item pode override (ainda passa pelo resolver).
        item_sdnq, item_mem = resolve_text3d_vram_opts(
            item.get("sdnq_preset", wave_sdnq),
            item.get("memory_efficient", wave_mem),
        )
        payload = build_generate_request(
            from_image=str(img),
            output=str(out),
            steps=item_steps,
            guidance=float(item.get("guidance", guidance)),
            octree_resolution=item_octree,
            num_chunks=item_chunks,
            seed=item.get("seed"),
            seed_fingerprint=item.get("seed_fingerprint"),
            mc_level=item.get("mc_level", mc_level),
            bounds_mode=str(item.get("bounds_mode", bounds_mode)),
            origin_mode=str(item.get("export_origin", export_origin)),
            topology_fix=topology_fix,
            category=item.get("category"),
            quality=item.get("quality", quality),
            bbox_tune=item.get("bbox_tune", True) is not False,
            control_type=item.get("control_type"),
            pose_preset=item.get("pose_preset"),
            bbox_preset=item.get("bbox_preset"),
            size_m=item.get("size_m") or item.get("size"),
            bbox=item.get("bbox"),
            pose_file=str(item["pose_file"]) if item.get("pose_file") else None,
            point_cloud=str(item["point_cloud"]) if item.get("point_cloud") else None,
            voxel_mesh=str(item["voxel_mesh"]) if item.get("voxel_mesh") else None,
            gpu_ids=gpu_ids,
            sdnq_preset=item_sdnq,
            memory_efficient=bool(item_mem),
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def paint_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    max_views: int = 6,
    view_resolution: int = 512,
    render_size: int = 1024,
    texture_size: int = 1024,
    bake_exp: float | None = None,
    preserve_origin: bool = True,
    smooth: bool = True,
    smooth_passes: int | None = None,
    gpu_ids: list[int] | None = None,
    memory_efficient: bool | None = None,
    sdnq_preset: str | None = None,
) -> list[UmsJobSpec]:
    """Converte items do paint_manifest.json em ``UmsJobSpec``."""
    try:
        from paint3d.ums_payload import build_texture_request
    except ImportError:
        return []

    wave_sdnq, wave_mem = resolve_paint3d_vram_opts(memory_efficient, sdnq_preset)

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        mesh = _resolve_path(manifest_dir, str(item.get("mesh") or item.get("mesh_path")))
        image = _resolve_path(manifest_dir, str(item.get("image") or item.get("image_path")))
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_sdnq, item_mem = resolve_paint3d_vram_opts(
            item.get("memory_efficient", wave_mem),
            item.get("sdnq_preset", wave_sdnq),
        )
        payload = build_texture_request(
            mesh_path=str(mesh),
            image_path=str(image),
            output=str(out),
            max_num_view=int(item.get("max_num_view", item.get("max_views", max_views))),
            view_resolution=int(item.get("view_resolution", view_resolution)),
            render_size=int(item.get("render_size", render_size)),
            texture_size=int(item.get("texture_size", texture_size)),
            bake_exp=item.get("bake_exp", bake_exp),
            preserve_origin=bool(item.get("preserve_origin", preserve_origin)),
            smooth=bool(item.get("smooth", smooth)),
            smooth_passes=item.get("smooth_passes", smooth_passes),
            gpu_ids=gpu_ids,
            memory_efficient=bool(item_mem),
            sdnq_preset=item_sdnq,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def run_shape_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    quality: str | None = None,
    export_origin: str = "feet",
    steps: int | None = None,
    guidance: float = 4.5,
    octree_resolution: int | None = None,
    num_chunks: int | None = None,
    mc_level: float | str = "auto",
    bounds_mode: str = "auto",
    sdnq_preset: str | None = None,
    memory_efficient: bool | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Corre wave text3d via UMS. ``None`` → caller usa subprocess generate-batch."""
    if no_ums:
        return None
    if not items:
        return []
    specs = shape_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        steps=steps,
        guidance=guidance,
        octree_resolution=octree_resolution,
        num_chunks=num_chunks,
        mc_level=mc_level,
        bounds_mode=bounds_mode,
        export_origin=export_origin,
        quality=quality,
        gpu_ids=gpu_ids,
        topology_fix=False,
        sdnq_preset=sdnq_preset,
        memory_efficient=memory_efficient,
    )
    if not specs:
        return None
    # Sem preload sync: load text3d pode >10 min; timeout do cliente (600s) →
    # Broken pipe → UMS evicta o modelo acabado de carregar → VRAM residual →
    # todos os jobs falham. 1.º job da wave carrega com o shape correcto.
    wave = run_gpu_wave(
        "text3d",
        specs,
        priority="batch",
        stream=ums_stream,
        preload=False,
        on_progress=on_progress,
        no_ums=False,
    )
    if wave is FALLBACK_SUBPROCESS:
        return None
    assert isinstance(wave, list)
    return results_as_batch_jsonl(wave)


def run_paint_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    max_views: int = 6,
    view_resolution: int = 512,
    render_size: int = 1024,
    texture_size: int = 1024,
    bake_exp: float | None = None,
    preserve_origin: bool = True,
    smooth: bool = True,
    smooth_passes: int | None = None,
    memory_efficient: bool | None = None,
    sdnq_preset: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Corre wave paint3d via UMS. ``None`` → caller usa subprocess texture-batch."""
    if no_ums:
        return None
    if not items:
        return []
    specs = paint_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        max_views=max_views,
        view_resolution=view_resolution,
        render_size=render_size,
        texture_size=texture_size,
        bake_exp=bake_exp,
        preserve_origin=preserve_origin,
        smooth=smooth,
        smooth_passes=smooth_passes,
        gpu_ids=gpu_ids,
        memory_efficient=memory_efficient,
        sdnq_preset=sdnq_preset,
    )
    if not specs:
        return None

    # Idem shape: evitar preload sync (timeout/Broken pipe). 1.º job carrega.
    wave = run_gpu_wave(
        "paint3d",
        specs,
        priority="batch",
        stream=ums_stream,
        preload=False,
        on_progress=on_progress,
        no_ums=False,
    )
    if wave is FALLBACK_SUBPROCESS:
        return None
    assert isinstance(wave, list)
    return results_as_batch_jsonl(wave)


# ---------------------------------------------------------------------------
# text2d / text2icon / texture2d / skymap2d / text2sound / terrain3d
# ---------------------------------------------------------------------------


def _run_optional_wave(
    backend: str,
    specs: list[UmsJobSpec],
    *,
    no_ums: bool,
    ums_stream: bool = False,
    on_progress: Callable[[UmsJobResult], None] | None = None,
    timeout_sec: float = 1800.0,
) -> list[dict[str, Any]] | None:
    """Wave UMS genérica. ``None`` → caller usa subprocess CLI."""
    if no_ums:
        return None
    if not specs:
        return []
    wave = run_gpu_wave(
        backend,
        specs,
        priority="batch",
        stream=ums_stream,
        preload=False,
        on_progress=on_progress,
        no_ums=False,
        timeout_sec=timeout_sec,
    )
    if wave is FALLBACK_SUBPROCESS:
        return None
    assert isinstance(wave, list)
    return results_as_batch_jsonl(wave)


def resolve_text2d_vram_opts() -> tuple[str | None, bool, str | None, str | None]:
    """Resolve (quant_preset, memory_efficient, model_id, footprint_key) via hw_auto.

    Sem GPU/import: admit-safe (mem_eff + sdnq-uint8, modelo 4B).
    """
    try:
        from text2d.generator import LOW_VRAM_MODEL_ID, model_footprint_key
        from text2d.hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            hwp = detect_hardware_profile()
            mid = hwp.model_id
            return (
                None if hwp.quant_preset in (None, "none", "") else str(hwp.quant_preset),
                bool(hwp.memory_efficient),
                mid,
                model_footprint_key(mid),
            )
    except Exception:
        pass
    try:
        from text2d.generator import LOW_VRAM_MODEL_ID, model_footprint_key

        return "sdnq-uint8", True, LOW_VRAM_MODEL_ID, model_footprint_key(LOW_VRAM_MODEL_ID)
    except Exception:
        return "sdnq-uint8", True, None, None


def resolve_text2icon_vram_opts() -> tuple[str | None, bool]:
    """Resolve (transformer_quant_preset, memory_efficient) via hw_auto."""
    try:
        from text2icon.hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            hwp = detect_hardware_profile()
            quant = hwp.transformer_sdnq_preset
            mem = bool(hwp.cpu_offload) or (quant not in (None, "", "none"))
            return (None if quant in (None, "", "none") else str(quant)), mem
    except Exception:
        pass
    return "sdnq-uint8", True


def resolve_skymap2d_vram_opts() -> bool:
    """Resolve memory_efficient via hw_auto (admit-safe True sem GPU)."""
    try:
        from skymap2d.hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            return bool(detect_hardware_profile().memory_efficient)
    except Exception:
        pass
    return True


def resolve_text2sound_vram_opts(half_precision: bool | None = None) -> bool:
    """Resolve half_precision via explícito > hw_auto > admit-safe True."""
    if half_precision is not None:
        return bool(half_precision)
    try:
        from text2sound.hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            return bool(detect_hardware_profile().half)
    except Exception:
        pass
    return True


def text2d_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    guidance: float = 1.0,
    quality: str | None = None,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items text2d_manifest → ``UmsJobSpec`` (peak via hw_auto)."""
    try:
        from text2d.ums_payload import build_generate_request
    except ImportError:
        return []

    quant, mem, model_id, footprint = resolve_text2d_vram_opts()
    w, h, st, g = width, height, steps, guidance
    if quality:
        try:
            from aigamekit_shared.quality import QualityEngine

            qp = QualityEngine().resolve("text2d", quality=quality).params
            if width == 1024 and "width" in qp:
                w = int(qp["width"])
            if height == 1024 and "height" in qp:
                h = int(qp["height"])
            if steps == 4 and "steps" in qp:
                st = int(qp["steps"])
            if guidance == 1.0 and "guidance" in qp:
                g = float(qp["guidance"])
        except Exception:
            pass

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        payload = build_generate_request(
            prompt=str(item["prompt"]),
            output=str(out),
            width=int(item.get("width", w)),
            height=int(item.get("height", h)),
            steps=int(item.get("steps", st)),
            guidance=float(item.get("guidance", item.get("guidance_scale", g))),
            seed=item.get("seed"),
            model_id=item.get("model_id", model_id),
            gpu_ids=gpu_ids,
            memory_efficient=mem,
            quant_preset=quant,
            footprint_key=footprint,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def text2icon_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    width: int = 512,
    height: int = 512,
    steps: int = 2,
    guidance: float = 4.5,
    transparent: bool = False,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte prompts text2icon → ``UmsJobSpec``."""
    try:
        from text2icon.ums_payload import build_generate_request
    except ImportError:
        return []

    quant, mem = resolve_text2icon_vram_opts()
    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        payload = build_generate_request(
            prompt=str(item["prompt"]),
            output=str(out),
            width=int(item.get("width", width)),
            height=int(item.get("height", height)),
            steps=int(item.get("steps", steps)),
            guidance=float(item.get("guidance", item.get("guidance_scale", guidance))),
            seed=item.get("seed"),
            transparent=bool(item.get("transparent", transparent)),
            negative_prompt=item.get("negative_prompt"),
            transformer_quant_preset=item.get("transformer_quant_preset", quant),
            model_id=item.get("model_id"),
            gpu_ids=gpu_ids,
            memory_efficient=mem,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def texture2d_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    width: int = 512,
    height: int = 512,
    steps: int = 20,
    guidance: float = 7.5,
    negative_prompt: str | None = None,
    preset: str | None = None,
    model_id: str | None = None,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items texture2d → ``UmsJobSpec``."""
    try:
        from texture2d.ums_payload import build_generate_request
    except ImportError:
        return []

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        payload = build_generate_request(
            prompt=str(item["prompt"]),
            output=str(out),
            width=int(item.get("width", width)),
            height=int(item.get("height", height)),
            steps=int(item.get("steps", steps)),
            guidance=float(item.get("guidance", item.get("guidance_scale", guidance))),
            seed=item.get("seed"),
            negative_prompt=item.get("negative_prompt", negative_prompt),
            preset=item.get("preset", preset),
            ground=bool(item.get("ground", False)),
            model_id=item.get("model_id", model_id),
            gpu_ids=gpu_ids,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def skymap2d_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    width: int = 2048,
    height: int = 1024,
    steps: int = 28,
    guidance: float = 3.5,
    negative_prompt: str | None = None,
    cfg_scale: float | None = None,
    lora_strength: float | None = None,
    preset: str | None = None,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items skymap2d → ``UmsJobSpec``."""
    try:
        from skymap2d.ums_payload import build_generate_request
    except ImportError:
        return []

    mem = resolve_skymap2d_vram_opts()
    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        payload = build_generate_request(
            prompt=str(item["prompt"]),
            output=str(out),
            width=int(item.get("width", width)),
            height=int(item.get("height", height)),
            steps=int(item.get("steps", steps)),
            guidance=float(item.get("guidance", item.get("guidance_scale", guidance))),
            seed=item.get("seed"),
            negative_prompt=item.get("negative_prompt", negative_prompt),
            cfg_scale=item.get("cfg_scale", cfg_scale),
            lora_strength=item.get("lora_strength", lora_strength),
            preset=item.get("preset", preset),
            gpu_ids=gpu_ids,
            memory_efficient=mem,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def text2sound_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    duration: float = 10.0,
    steps: int = 100,
    cfg_scale: float = 7.0,
    half_precision: bool | None = None,
    quality: str | None = None,
    category: str | None = None,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items text2sound → ``UmsJobSpec``."""
    try:
        from text2sound.ums_payload import build_generate_request
    except ImportError:
        return []

    half = resolve_text2sound_vram_opts(half_precision)
    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_half = item.get("half_precision")
        payload = build_generate_request(
            prompt=str(item["prompt"]),
            output=str(out),
            duration=float(item.get("duration", duration)),
            steps=int(item.get("steps", steps)),
            cfg_scale=float(item.get("cfg_scale", cfg_scale)),
            seed=item.get("seed"),
            sigma_min=item.get("sigma_min"),
            sigma_max=item.get("sigma_max"),
            sampler_type=item.get("sampler_type", item.get("sampler")),
            negative_prompt=item.get("negative_prompt"),
            half_precision=bool(item_half) if item_half is not None else half,
            gpu_ids=gpu_ids,
            quality=item.get("quality", quality),
            category=item.get("category", category),
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def terrain3d_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items terrain3d → ``UmsJobSpec``."""
    try:
        from terrain3d.ums_payload import build_generate_request
    except ImportError:
        return []

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        meta = item.get("metadata_path")
        meta_s = str(_resolve_path(manifest_dir, str(meta))) if meta else None
        device = item.get("device")
        if device is None and gpu_ids:
            device = f"cuda:{gpu_ids[0]}"
        payload = build_generate_request(
            output=str(out),
            metadata_path=meta_s,
            seed=item.get("seed"),
            size=item.get("size"),
            world_size=item.get("world_size"),
            max_height=item.get("max_height"),
            mode=item.get("mode"),
            device=device,
            prompt=item.get("prompt"),
            dtype=item.get("dtype"),
            cache_size=item.get("cache_size"),
            coarse_window=item.get("coarse_window"),
            gpu_ids=gpu_ids,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def run_text2d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    guidance: float = 1.0,
    quality: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave text2d via UMS. ``None`` → caller usa subprocess generate-batch."""
    if no_ums:
        return None
    if not items:
        return []
    specs = text2d_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        quality=quality,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("text2d", specs, no_ums=False, ums_stream=ums_stream, on_progress=on_progress)


def run_text2icon_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    width: int = 512,
    height: int = 512,
    steps: int = 2,
    guidance: float = 4.5,
    transparent: bool = False,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave text2icon via UMS. ``None`` → caller usa subprocess generate."""
    if no_ums:
        return None
    if not items:
        return []
    specs = text2icon_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        transparent=transparent,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("text2icon", specs, no_ums=False, ums_stream=ums_stream, on_progress=on_progress)


def run_texture2d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    width: int = 512,
    height: int = 512,
    steps: int = 20,
    guidance: float = 7.5,
    negative_prompt: str | None = None,
    preset: str | None = None,
    model_id: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave texture2d via UMS. ``None`` → caller usa subprocess generate."""
    if no_ums:
        return None
    if not items:
        return []
    specs = texture2d_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        negative_prompt=negative_prompt,
        preset=preset,
        model_id=model_id,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("texture2d", specs, no_ums=False, ums_stream=ums_stream, on_progress=on_progress)


def run_skymap2d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    width: int = 2048,
    height: int = 1024,
    steps: int = 28,
    guidance: float = 3.5,
    negative_prompt: str | None = None,
    cfg_scale: float | None = None,
    lora_strength: float | None = None,
    preset: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave skymap2d via UMS. ``None`` → caller usa subprocess generate."""
    if no_ums:
        return None
    if not items:
        return []
    specs = skymap2d_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        negative_prompt=negative_prompt,
        cfg_scale=cfg_scale,
        lora_strength=lora_strength,
        preset=preset,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("skymap2d", specs, no_ums=False, ums_stream=ums_stream, on_progress=on_progress)


def run_text2sound_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    duration: float = 10.0,
    steps: int = 100,
    cfg_scale: float = 7.0,
    half_precision: bool | None = None,
    quality: str | None = None,
    category: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave text2sound via UMS. ``None`` → caller usa subprocess generate."""
    if no_ums:
        return None
    if not items:
        return []
    specs = text2sound_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        duration=duration,
        steps=steps,
        cfg_scale=cfg_scale,
        half_precision=half_precision,
        quality=quality,
        category=category,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("text2sound", specs, no_ums=False, ums_stream=ums_stream, on_progress=on_progress)


def run_terrain3d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave terrain3d via UMS. ``None`` → caller usa subprocess generate."""
    if no_ums:
        return None
    if not items:
        return []
    specs = terrain3d_specs_from_items(items, manifest_dir=manifest_dir, gpu_ids=gpu_ids)
    if not specs:
        return None
    return _run_optional_wave(
        "terrain3d",
        specs,
        no_ums=False,
        ums_stream=ums_stream,
        on_progress=on_progress,
        timeout_sec=1800.0,
    )


def motion3d_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    max_frames: int | None = None,
    duration: float | None = None,
    half_precision: bool | None = None,
    quality: str | None = None,
    category: str | None = None,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items motion3d → ``UmsJobSpec`` (HY-Motion duration/cfg/model)."""
    try:
        from motion3d.ums_payload import build_generate_request
    except ImportError:
        return []

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_half = item.get("half_precision")
        payload = build_generate_request(
            prompt=str(item["prompt"]),
            output=str(out),
            duration=item.get("duration", duration),
            max_frames=item.get("max_frames", item.get("frames", max_frames)),
            seed=item.get("seed"),
            cfg_scale=item.get("cfg_scale"),
            model=item.get("model"),
            sdnq_preset=item.get("sdnq_preset"),
            memory_efficient=item.get("memory_efficient"),
            half_precision=bool(item_half) if item_half is not None else half_precision,
            gpu_ids=gpu_ids,
            quality=item.get("quality", quality),
            category=item.get("category", category),
            also_npz=bool(item.get("also_npz", False)),
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out)))
    return specs


def run_motion3d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_ums: bool,
    ums_stream: bool = False,
    gpu_ids: list[int] | None = None,
    max_frames: int | None = None,
    duration: float | None = None,
    half_precision: bool | None = None,
    quality: str | None = None,
    category: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave motion3d via UMS. ``None`` → caller usa subprocess generate."""
    if no_ums:
        return None
    if not items:
        return []
    specs = motion3d_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        max_frames=max_frames,
        duration=duration,
        half_precision=half_precision,
        quality=quality,
        category=category,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("motion3d", specs, no_ums=False, ums_stream=ums_stream, on_progress=on_progress)
