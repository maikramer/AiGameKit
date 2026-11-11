"""Bridge: manifests GPU → specs vramd → run_gpu_wave (ou fallback).

Shape/paint + text2d/texture2d/skymap2d/text2sound/terrain3d.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .vramd_coord import (
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


def _payload_gpu_ids(item: dict[str, Any], gpu_ids: list[int] | None) -> list[int] | None:
    """``gpu_ids`` só para jobs locais; no remoto o router decide as GPUs.

    ``gpu_ids`` vem do NVML da máquina que submete. Num job remoto esses IDs não
    correspondem às GPUs do servidor (podem nem existir: um portátil com
    ``0,1`` a mandar para um servidor de uma GPU). Em ``auto`` segue
    ``VRAMD_GPU_MODE``/``VRAMD_REMOTE``, como ``run_gpu_wave``.
    """
    if not gpu_ids:
        return None
    from aigamekit_shared.vramd_remote import remote_enabled

    from .vramd_coord import _gpu_remote

    remote = _gpu_remote(str(item.get("gpu") or "auto"))
    if remote is True or (remote is None and remote_enabled()):
        return None
    return gpu_ids


def _evict_backend(name: str, gpu: str | None = None) -> None:
    """Liberta os pesos de um backend antes da wave seguinte (transição de fase).

    O vramd mantém o worker do backend carregado (idle evict 120s). Na transição
    imagem→shape o text2d (~5.4 GiB) impediu o load do text3d (~4.5 GiB): o
    worker text3d morreu com "EOF no load" e o batch falhou. Uma evicção
    explícita no início de cada wave liberta a VRAM sem depender do idle evict
    ou das affinity cuts. ``VRAMD_PHASE_EVICT=0`` desliga.

    Args:
        gpu: ``remote``/``local``/``auto`` do asset — um ``release`` sem isto
            ia sempre ao vramd global e, com ``VRAMD_REMOTE`` ligado, evictava
            o servidor errado para uma wave ``gpu: local`` (e vice-versa).
    """
    import os

    if os.environ.get("VRAMD_PHASE_EVICT", "1").strip().lower() in ("0", "false", "no", "off"):
        return
    # Várias GPUs/peers: cada GPU tem o seu gestor e o vramd abre espaço onde
    # o job cai. Um ``release`` global aqui despejaria réplicas quentes noutras
    # GPUs (ou o paint de um asset prefetched) sem ganho nenhum.
    from .vramd_coord import _gpu_remote
    from .vramd_routing import is_parallel

    remote = _gpu_remote(gpu)
    if is_parallel(gpu):
        return
    try:
        from aigamekit_shared.vramd_client import send_request

        # hard=True (vramd >= 0.7.9): evicta E mata o worker. Só descarregar os
        # pesos deixava o contexto CUDA/allocator preso (~5 GiB no text2d) e a
        # wave seguinte era recusada por "VRAM insuficiente". O vramd antigo
        # ignora a chave e mantém o comportamento soft.
        send_request(
            {"cmd": "release", "backend": name, "hard": True},
            timeout_sec=30.0,
            force_local=remote is False,
        )
    except Exception:
        pass


def _evict_for_items(name: str, items: list[dict[str, Any]]) -> None:
    """Evicta ``name`` no vramd de **cada** destino (local e/ou remoto)."""
    from aigamekit_shared.vramd_remote import remote_enabled

    from .vramd_coord import _gpu_remote

    targets: set[bool] = set()
    for item in items:
        remote = _gpu_remote(str(item.get("gpu") or "auto"))
        targets.add(remote if remote is not None else remote_enabled())
    for remote in sorted(targets):
        _evict_backend(name, "remote" if remote else "local")


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
    preset: str | None = None,
    gpu_ids: list[int] | None = None,
    topology_fix: bool = False,
    sdnq_preset: str | None = None,
    memory_efficient: bool | None = None,
) -> list[UmsJobSpec]:
    """Converte items do shape_manifest.json em ``UmsJobSpec``.

    ``topology_fix=False`` no batch phased (clean fica no ensure_to_paint / master).
    Peak VRAM: profile explícito > hw_auto > admit-safe (não hardcode uint8).

    ``octree_resolution``/``steps``/``num_chunks`` omitidos (``None``) → não vão no
    payload vramd → ``bbox_tune`` size-based pode subir octree (ex. longhouse → 448).
    """
    try:
        from text3d.vramd_payload import build_generate_request
    except ImportError:
        return []

    wave_sdnq, wave_mem = resolve_text3d_vram_opts(sdnq_preset, memory_efficient)

    def _opt_int(item: dict[str, Any], *keys: str, wave: int | None) -> int | None:
        for k in keys:
            if k in item and item[k] is not None:
                return int(item[k])
        return int(wave) if wave is not None else None

    from .vramd_routing import text3d_opts_for_size, tiered_payload

    wave_hw_auto = sdnq_preset is None and memory_efficient is None

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        img = _resolve_path(manifest_dir, str(item["image"]))
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_steps = _opt_int(item, "steps", wave=steps)
        item_octree = _opt_int(item, "octree_resolution", "octree", wave=octree_resolution)
        item_chunks = _opt_int(item, "num_chunks", "chunks", wave=num_chunks)
        # Wave já resolveu via hw_auto; item pode override. ``null`` no item
        # herda a wave (era tratado como override vazio e perdia o preset/offload
        # explícito do profile).
        item_sdnq, item_mem = resolve_text3d_vram_opts(
            item.get("sdnq_preset") if item.get("sdnq_preset") is not None else wave_sdnq,
            item.get("memory_efficient") if item.get("memory_efficient") is not None else wave_mem,
        )
        item_gpu_ids = _payload_gpu_ids(item, gpu_ids)
        kwargs: dict[str, Any] = dict(
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
            preset=item.get("preset", preset),
            bbox_tune=item.get("bbox_tune", True) is not False,
            control_type=item.get("control_type"),
            pose_preset=item.get("pose_preset"),
            bbox_preset=item.get("bbox_preset"),
            size_m=item.get("size_m") or item.get("size"),
            bbox=item.get("bbox"),
            pose_file=str(item["pose_file"]) if item.get("pose_file") else None,
            point_cloud=str(item["point_cloud"]) if item.get("point_cloud") else None,
            voxel_mesh=str(item["voxel_mesh"]) if item.get("voxel_mesh") else None,
            gpu_ids=item_gpu_ids,
            sdnq_preset=item_sdnq,
            memory_efficient=bool(item_mem),
        )
        payload = build_generate_request(**kwargs)

        def _variant(size_mib: int, _kw: dict[str, Any] = kwargs) -> dict[str, Any] | None:
            opts = text3d_opts_for_size(size_mib)
            if opts is None:
                return None
            return build_generate_request(**{**_kw, "sdnq_preset": opts[0], "memory_efficient": bool(opts[1])})

        gpu = str(item.get("gpu") or "auto")
        crit = str(item.get("criticality") or "normal")
        # ``sdnq_preset: null`` no item continua a significar "usa o hw-auto da
        # wave" (o resolver trata None como omitido) — presença da chave não
        # desliga os tiers.
        item_hw_auto = item.get("sdnq_preset") is None and item.get("memory_efficient") is None
        tiered_payload(
            payload,
            gpu=gpu,
            criticality=crit,
            hw_auto=wave_hw_auto and item_hw_auto,
            variant_for_size=_variant,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out), gpu=gpu, criticality=crit))
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
    pbr_enrich: bool | None = None,
) -> list[UmsJobSpec]:
    """Converte items do paint_manifest.json em ``UmsJobSpec``."""
    try:
        # Shared: não importa o pacote Paint3D (torch/trimesh) no venv do
        # GameAssets — era o que fazia a wave cair para o texture-batch
        # sequencial e deixava a 2.ª GPU remota parada.
        from aigamekit_shared.vramd_paint_payload import build_texture_request
    except ImportError:
        return []

    from .vramd_routing import paint3d_opts_for_size, tiered_payload

    wave_sdnq, wave_mem = resolve_paint3d_vram_opts(memory_efficient, sdnq_preset)
    wave_hw_auto = memory_efficient is None and sdnq_preset is None

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        mesh = _resolve_path(manifest_dir, str(item.get("mesh") or item.get("mesh_path")))
        image = _resolve_path(manifest_dir, str(item.get("image") or item.get("image_path")))
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_sdnq, item_mem = resolve_paint3d_vram_opts(
            item.get("memory_efficient") if item.get("memory_efficient") is not None else wave_mem,
            item.get("sdnq_preset") if item.get("sdnq_preset") is not None else wave_sdnq,
        )
        item_gpu_ids = _payload_gpu_ids(item, gpu_ids)
        kwargs: dict[str, Any] = dict(
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
            gpu_ids=item_gpu_ids,
            memory_efficient=bool(item_mem),
            sdnq_preset=item_sdnq,
            pbr_enrich=item.get("pbr_enrich", pbr_enrich),
        )
        payload = build_texture_request(**kwargs)

        def _variant(size_mib: int, _kw: dict[str, Any] = kwargs) -> dict[str, Any] | None:
            opts = paint3d_opts_for_size(size_mib)
            if opts is None:
                return None
            return build_texture_request(**{**_kw, "sdnq_preset": opts[0], "memory_efficient": opts[1]})

        gpu = str(item.get("gpu") or "auto")
        crit = str(item.get("criticality") or "normal")
        item_hw_auto = item.get("sdnq_preset") is None and item.get("memory_efficient") is None
        tiered_payload(
            payload,
            gpu=gpu,
            criticality=crit,
            hw_auto=wave_hw_auto and item_hw_auto,
            variant_for_size=_variant,
        )
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out), gpu=gpu, criticality=crit))
    return specs


def run_shape_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_vramd: bool,
    vramd_stream: bool = False,
    gpu_ids: list[int] | None = None,
    quality: str | None = None,
    preset: str | None = None,
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
    """Corre wave text3d via vramd. ``None`` → caller usa subprocess generate-batch."""
    if no_vramd:
        return None
    if not items:
        return []
    # Transição imagem→shape: libertar o worker text2d (~5.4 GiB) antes de
    # carregar o text3d (~4.5 GiB); sem isto o load do text3d morria (EOF).
    # Por destino: um asset ``gpu: local`` não pode evictar o servidor remoto.
    _evict_for_items("text2d", items)
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
        preset=preset,
        gpu_ids=gpu_ids,
        topology_fix=False,
        sdnq_preset=sdnq_preset,
        memory_efficient=memory_efficient,
    )
    if not specs:
        return None
    # Sem preload sync: load text3d pode >10 min; timeout do cliente (600s) →
    # Broken pipe → vramd evicta o modelo acabado de carregar → VRAM residual →
    # todos os jobs falham. 1.º job da wave carrega com o shape correcto.
    wave = run_gpu_wave(
        "text3d",
        specs,
        priority="batch",
        stream=vramd_stream,
        preload=False,
        on_progress=on_progress,
        no_vramd=False,
    )
    if wave is FALLBACK_SUBPROCESS:
        return None
    assert isinstance(wave, list)
    return results_as_batch_jsonl(wave)


def _respawn_paint_worker() -> None:
    """Renova o worker paint3d — recovery documentado para OOM-spin do allocator."""
    try:
        from aigamekit_shared.vramd_client import respawn_vramd_backend

        respawn_vramd_backend("paint3d", lazy=True)
    except Exception:
        pass


def _merge_wave_results(
    results: list[UmsJobResult], wave: list[UmsJobResult], on_progress: Callable[[UmsJobResult], None] | None
) -> None:
    """Substitui in-place os resultados de asset_ids re-corridos (a ronda nova ganha)."""
    replaced = {r.asset_id for r in wave}
    results[:] = [r for r in results if r.asset_id not in replaced] + list(wave)
    if on_progress:
        for r in wave:
            on_progress(r)


def _retry_failed_paint_items(
    items: list[dict[str, Any]],
    results: list[UmsJobResult],
    *,
    spec_kwargs: dict[str, Any],
    vramd_stream: bool,
    on_progress: Callable[[UmsJobResult], None] | None,
    reprep_item: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
) -> list[UmsJobResult]:
    """Auto-recuperação da wave de paint (2 rondas).

    Uma wave 0/N por um item preso (worker paint3d em OOM-spin — allocator
    degradado numa GPU pequena) marcava TODOS os items como falhados e o
    fallback subprocess era recusado com o vramd ocupado. Recuperação:

    * Ronda 1 — worker novo (``vramd respawn paint3d``): recorre só os items
      falhados; a maioria dos "falhanços" eram colaterais do abort.
    * Ronda 2 — degradação do ``_to_paint`` via ``reprep_item`` (caller
      regenera a ~0.6x do budget) para o item que mesmo com worker novo
      estoura a VRAM.

    Items que falham as duas rondas seguem para o fallback do caller.
    """
    from .vramd_coord import run_gpu_wave as _run_wave

    def _failed_ids(rs: list[UmsJobResult]) -> list[str]:
        seen: list[str] = []
        for r in rs:
            if r.status not in ("ok", "skipped") and r.asset_id not in seen:
                seen.append(r.asset_id)
        return seen

    failed = _failed_ids(results)
    if not failed:
        return results

    _respawn_paint_worker()
    retry_items = [it for it in items if str(it.get("id")) in set(failed)]
    specs = paint_specs_from_items(retry_items, **spec_kwargs)
    if specs:
        wave = _run_wave(
            "paint3d",
            specs,
            priority="batch",
            stream=vramd_stream,
            preload=False,
            on_progress=on_progress,
            no_vramd=False,
            timeout_sec=3600.0,
        )
        if isinstance(wave, list) and wave:
            _merge_wave_results(results, wave, on_progress)

    if reprep_item is None:
        return results

    still = _failed_ids(results)
    degraded_items: list[dict[str, Any]] = []
    for it in retry_items:
        if str(it.get("id")) in set(still):
            try:
                new_it = reprep_item(it)
            except Exception:
                new_it = None
            if new_it:
                degraded_items.append(new_it)
    if degraded_items:
        _respawn_paint_worker()
        specs = paint_specs_from_items(degraded_items, **spec_kwargs)
        if specs:
            wave = _run_wave(
                "paint3d",
                specs,
                priority="batch",
                stream=vramd_stream,
                preload=False,
                on_progress=on_progress,
                no_vramd=False,
                timeout_sec=3600.0,
            )
            if isinstance(wave, list) and wave:
                _merge_wave_results(results, wave, on_progress)
    return results


def make_paint_degrader(
    *,
    text3d_bin: str,
    profile: Any,
    child_env: dict[str, str],
    manifest_dir: Path,
    mesh_final_by_id: dict[str, Path],
    row_by_id: dict[str, Any] | None = None,
    factor: float = 0.6,
) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Callback ``reprep_item`` que regenera o ``_to_paint`` degradado (~0.6x).

    Devolve o item com a mesh nova (ou ``None`` para manter a falha). Assets
    cujo ``_clean`` cabe no alvo degradado continuam a pintar o próprio
    ``_clean`` — só meshes genuinamente grandes são simplificadas.
    """

    def reprep(item: dict[str, Any]) -> dict[str, Any] | None:
        from .pipeline import _resolve_to_paint_faces, ensure_to_paint_for_paint

        aid = str(item.get("id"))
        mesh_final = mesh_final_by_id.get(aid)
        if mesh_final is None:
            return None
        row = (row_by_id or {}).get(aid)
        base = _resolve_to_paint_faces(profile, row)
        degraded = max(4_000, int(base * factor))
        out = ensure_to_paint_for_paint(
            mesh_final,
            text3d_bin=text3d_bin,
            profile=profile,
            child_env=child_env,
            manifest_dir=manifest_dir,
            force=True,
            row=row,
            target_faces=degraded,
        )
        return {**item, "mesh": str(out)}

    return reprep


def run_paint_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_vramd: bool,
    vramd_stream: bool = False,
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
    reprep_item: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]] | None:
    """Corre wave paint3d via vramd. ``None`` → caller usa subprocess texture-batch."""
    if no_vramd:
        return None
    if not items:
        return []
    # Transição shape/refine→paint: libertar text3d/hi3d antes do paint3d.
    # Por destino (local/remoto) — ver ``_evict_for_items``.
    for _prior in ("text3d", "hi3d"):
        _evict_for_items(_prior, items)
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
    # Timeout 2x o default: waits atras de reload/evict do worker (OOM-spin
    # recovery) + paints 320k (~5 min cada) nao devem matar jobs legitimos.
    wave = run_gpu_wave(
        "paint3d",
        specs,
        priority="batch",
        stream=vramd_stream,
        preload=False,
        on_progress=on_progress,
        no_vramd=False,
        timeout_sec=3600.0,
    )
    if wave is FALLBACK_SUBPROCESS:
        return None
    assert isinstance(wave, list)
    results: list[UmsJobResult] = list(wave)
    if any(r.status not in ("ok", "skipped") for r in results):
        spec_kwargs = dict(
            manifest_dir=manifest_dir,
            gpu_ids=gpu_ids,
            max_views=max_views,
            view_resolution=view_resolution,
            render_size=render_size,
            texture_size=texture_size,
            bake_exp=bake_exp,
            preserve_origin=preserve_origin,
            smooth=smooth,
            smooth_passes=smooth_passes,
            memory_efficient=memory_efficient,
            sdnq_preset=sdnq_preset,
        )
        results = _retry_failed_paint_items(
            items,
            results,
            spec_kwargs=spec_kwargs,
            vramd_stream=vramd_stream,
            on_progress=on_progress,
            reprep_item=reprep_item,
        )
    return results_as_batch_jsonl(results)


# ---------------------------------------------------------------------------
# text2d / texture2d / skymap2d / text2sound / terrain3d
# ---------------------------------------------------------------------------


def _run_optional_wave(
    backend: str,
    specs: list[UmsJobSpec],
    *,
    no_vramd: bool,
    vramd_stream: bool = False,
    on_progress: Callable[[UmsJobResult], None] | None = None,
    timeout_sec: float = 1800.0,
) -> list[dict[str, Any]] | None:
    """Wave vramd genérica. ``None`` → caller usa subprocess CLI."""
    if no_vramd:
        return None
    if not specs:
        return []
    wave = run_gpu_wave(
        backend,
        specs,
        priority="batch",
        stream=vramd_stream,
        preload=False,
        on_progress=on_progress,
        no_vramd=False,
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
    category: str | None = None,
    transparent: bool = False,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items text2d_manifest → ``UmsJobSpec`` (peak via hw_auto).

    ``category`` (ex.: ``icon``) e ``transparent`` suportam o modo ícone do
    text2d — a categoria resolve no QualityEngine antes dos defaults.
    """
    try:
        from text2d.vramd_payload import build_generate_request
    except ImportError:
        return []

    quant, mem, model_id, footprint = resolve_text2d_vram_opts()
    w, h, st, g = width, height, steps, guidance
    if quality or category:
        try:
            from aigamekit_shared.quality import QualityEngine

            qp = QualityEngine().resolve("text2d", quality=quality, category=category).params
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

    from .vramd_routing import text2d_opts_for_size, tiered_payload

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_gpu_ids = _payload_gpu_ids(item, gpu_ids)
        kwargs: dict[str, Any] = dict(
            prompt=str(item["prompt"]),
            output=str(out),
            width=int(item.get("width", w)),
            height=int(item.get("height", h)),
            steps=int(item.get("steps", st)),
            guidance=float(item.get("guidance", item.get("guidance_scale", g))),
            seed=item.get("seed"),
            model_id=item.get("model_id", model_id),
            gpu_ids=item_gpu_ids,
            memory_efficient=mem,
            quant_preset=quant,
            # O footprint/calibração do vramd só casa quando o load_kwargs
            # inclui o group offload do perfil de 6 GB (estava a cair no
            # fallback genérico 2430 MiB e o worker text2d OOMava).
            allow_group_offload=mem,
            footprint_key=footprint,
            category=category,
            transparent=True if transparent else None,
            refs=item.get("refs"),
        )
        payload = build_generate_request(**kwargs)

        def _variant(
            size_mib: int, _kw: dict[str, Any] = kwargs, _item: dict[str, Any] = item
        ) -> dict[str, Any] | None:
            opts = text2d_opts_for_size(size_mib)
            if opts is None:
                return None
            v_quant, v_mem, v_model, v_footprint = opts
            return build_generate_request(
                **{
                    **_kw,
                    "quant_preset": v_quant,
                    "memory_efficient": v_mem,
                    "allow_group_offload": v_mem,
                    "model_id": _kw["model_id"] if "model_id" in _item else v_model,
                    "footprint_key": v_footprint,
                }
            )

        gpu = str(item.get("gpu") or "auto")
        crit = str(item.get("criticality") or "normal")
        tiered_payload(payload, gpu=gpu, criticality=crit, hw_auto=True, variant_for_size=_variant)
        specs.append(UmsJobSpec(asset_id=aid, payload=payload, output=str(out), gpu=gpu, criticality=crit))
    return specs


def texture2d_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    width: int = 512,
    height: int = 512,
    steps: int = 28,
    guidance: float = 7.0,
    quality: str | None = None,
    negative_prompt: str | None = None,
    preset: str | None = None,
    model_id: str | None = None,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items texture2d → ``UmsJobSpec``.

    ``quality`` resolve no QualityEngine antes dos defaults (tier ``medium``
    = steps 28 / guidance 7.0, já os defaults do signature).
    """
    try:
        from texture2d.vramd_payload import build_generate_request
    except ImportError:
        return []

    from .vramd_routing import routed_payload

    w, h, st, g = width, height, steps, guidance
    if quality:
        try:
            from aigamekit_shared.quality import QualityEngine

            qp = QualityEngine().resolve("texture2d", quality=quality).params
            if width == 512 and "width" in qp:
                w = int(qp["width"])
            if height == 512 and "height" in qp:
                h = int(qp["height"])
            if steps == 28 and "steps" in qp:
                st = int(qp["steps"])
            if guidance == 7.0 and "guidance" in qp:
                g = float(qp["guidance"])
        except Exception:
            pass

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        item_gpu_ids = _payload_gpu_ids(item, gpu_ids)
        payload = build_generate_request(
            prompt=str(item["prompt"]),
            output=str(out),
            width=int(item.get("width", w)),
            height=int(item.get("height", h)),
            steps=int(item.get("steps", st)),
            guidance=float(item.get("guidance", item.get("guidance_scale", g))),
            seed=item.get("seed"),
            negative_prompt=item.get("negative_prompt", negative_prompt),
            preset=item.get("preset", preset),
            # Tri-state ("auto"/"on"/"off") em bruto — o builder normaliza;
            # bool() aqui desligava o ground-enhancer no caminho delegado.
            ground=item.get("ground", "auto"),
            model_id=item.get("model_id", model_id),
            gpu_ids=item_gpu_ids,
        )
        gpu = str(item.get("gpu") or "auto")
        crit = str(item.get("criticality") or "normal")
        routed_payload(payload, gpu=gpu, criticality=crit)
        specs.append(
            UmsJobSpec(
                asset_id=aid,
                payload=payload,
                output=str(out),
                gpu=gpu,
                criticality=crit,
            )
        )
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
        from skymap2d.vramd_payload import build_generate_request
    except ImportError:
        return []

    from .vramd_routing import routed_payload

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
            gpu_ids=_payload_gpu_ids(item, gpu_ids),
            memory_efficient=mem,
        )
        gpu = str(item.get("gpu") or "auto")
        crit = str(item.get("criticality") or "normal")
        routed_payload(payload, gpu=gpu, criticality=crit)
        specs.append(
            UmsJobSpec(
                asset_id=aid,
                payload=payload,
                output=str(out),
                gpu=gpu,
                criticality=crit,
            )
        )
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
        from text2sound.vramd_payload import build_generate_request
    except ImportError:
        return []

    from .vramd_routing import routed_payload

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
            gpu_ids=_payload_gpu_ids(item, gpu_ids),
            quality=item.get("quality", quality),
            category=item.get("category", category),
        )
        gpu = str(item.get("gpu") or "auto")
        crit = str(item.get("criticality") or "normal")
        routed_payload(payload, gpu=gpu, criticality=crit)
        specs.append(
            UmsJobSpec(
                asset_id=aid,
                payload=payload,
                output=str(out),
                gpu=gpu,
                criticality=crit,
            )
        )
    return specs


def terrain3d_specs_from_items(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    gpu_ids: list[int] | None = None,
) -> list[UmsJobSpec]:
    """Converte items terrain3d → ``UmsJobSpec``."""
    try:
        from terrain3d.vramd_payload import build_generate_request
    except ImportError:
        return []

    from .vramd_routing import routed_payload

    specs: list[UmsJobSpec] = []
    for item in items:
        aid = str(item["id"])
        out = _resolve_path(manifest_dir, str(item["output"]))
        meta = item.get("metadata_path")
        meta_s = str(_resolve_path(manifest_dir, str(meta))) if meta else None
        item_gpu_ids = _payload_gpu_ids(item, gpu_ids)
        device = item.get("device")
        if device is None and item_gpu_ids:
            device = f"cuda:{item_gpu_ids[0]}"
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
            gpu_ids=item_gpu_ids,
        )
        gpu = str(item.get("gpu") or "auto")
        crit = str(item.get("criticality") or "normal")
        routed_payload(payload, gpu=gpu, criticality=crit)
        specs.append(
            UmsJobSpec(
                asset_id=aid,
                payload=payload,
                output=str(out),
                gpu=gpu,
                criticality=crit,
            )
        )
    return specs


def run_text2d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_vramd: bool,
    vramd_stream: bool = False,
    gpu_ids: list[int] | None = None,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    guidance: float = 1.0,
    quality: str | None = None,
    category: str | None = None,
    transparent: bool = False,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave text2d via vramd. ``None`` → caller usa subprocess generate-batch."""
    if no_vramd:
        return None
    if not items:
        return []
    # Transição shape/paint→imagem: libertar os workers pesados do 3D.
    # Por destino (local/remoto) — ver ``_evict_for_items``.
    for _prior in ("text3d", "paint3d", "hi3d"):
        _evict_for_items(_prior, items)
    specs = text2d_specs_from_items(
        items,
        manifest_dir=manifest_dir,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        quality=quality,
        category=category,
        transparent=transparent,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("text2d", specs, no_vramd=False, vramd_stream=vramd_stream, on_progress=on_progress)


def run_texture2d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_vramd: bool,
    vramd_stream: bool = False,
    gpu_ids: list[int] | None = None,
    width: int = 512,
    height: int = 512,
    steps: int = 28,
    guidance: float = 7.0,
    quality: str | None = None,
    negative_prompt: str | None = None,
    preset: str | None = None,
    model_id: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave texture2d via vramd. ``None`` → caller usa subprocess generate."""
    if no_vramd:
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
        quality=quality,
        negative_prompt=negative_prompt,
        preset=preset,
        model_id=model_id,
        gpu_ids=gpu_ids,
    )
    if not specs:
        return None
    return _run_optional_wave("texture2d", specs, no_vramd=False, vramd_stream=vramd_stream, on_progress=on_progress)


def run_skymap2d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_vramd: bool,
    vramd_stream: bool = False,
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
    """Wave skymap2d via vramd. ``None`` → caller usa subprocess generate."""
    if no_vramd:
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
    return _run_optional_wave("skymap2d", specs, no_vramd=False, vramd_stream=vramd_stream, on_progress=on_progress)


def run_text2sound_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_vramd: bool,
    vramd_stream: bool = False,
    gpu_ids: list[int] | None = None,
    duration: float = 10.0,
    steps: int = 100,
    cfg_scale: float = 7.0,
    half_precision: bool | None = None,
    quality: str | None = None,
    category: str | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave text2sound via vramd. ``None`` → caller usa subprocess generate."""
    if no_vramd:
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
    return _run_optional_wave("text2sound", specs, no_vramd=False, vramd_stream=vramd_stream, on_progress=on_progress)


def run_terrain3d_wave_or_fallback(
    items: list[dict[str, Any]],
    *,
    manifest_dir: Path,
    no_vramd: bool,
    vramd_stream: bool = False,
    gpu_ids: list[int] | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Wave terrain3d via vramd. ``None`` → caller usa subprocess generate."""
    if no_vramd:
        return None
    if not items:
        return []
    specs = terrain3d_specs_from_items(items, manifest_dir=manifest_dir, gpu_ids=gpu_ids)
    if not specs:
        return None
    return _run_optional_wave(
        "terrain3d",
        specs,
        no_vramd=False,
        vramd_stream=vramd_stream,
        on_progress=on_progress,
        timeout_sec=1800.0,
    )
