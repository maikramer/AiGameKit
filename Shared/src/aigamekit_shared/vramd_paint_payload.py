"""Builders de request vramd para Paint3D texture (CLI + GameAssets).

Vive no Shared (e não em ``paint3d.vramd_payload``) para o coordenador de
waves do GameAssets poder montar os payloads sem importar o pacote Paint3D —
o ``paint3d/__init__`` puxa torch/trimesh e não existe no venv do GameAssets.
O Paint3D re-exporta esta API no seu ``vramd_payload``.
"""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.vramd_payload import build_request_body


def _coerce_ref_azims(image_azims: Any) -> list[float] | None:
    """Azimutes em graus, normalizados a [0, 360).

    Aceita lista de números ou a string ``"0,180"`` (payload de cliente
    externo). Valores fora do círculo (``-90``, ``450``) passam a equivalentes
    — o blend compara diferenças e um ``450`` dava o mesmo, mas o payload
    ficava inconsistente para quem o lê.
    """
    if not image_azims:
        return None
    if isinstance(image_azims, str):
        chunks = [chunk.strip() for chunk in image_azims.replace(";", ",").split(",")]
        return [float(chunk) % 360.0 for chunk in chunks if chunk]
    return [float(value) % 360.0 for value in image_azims]


def _validate_multi_ref(images: list[str] | None, blend_refs: bool, azims: list[float] | None) -> None:
    """Falha cedo num pedido multi-ref inconsistente.

    Sem isto o erro só aparecia no ``textureGenPipeline`` (depois do
    uv_unwrap/xatlas), ou pior: ``--blend-refs`` com uma só imagem era um
    no-op silencioso e o asset saía com a vista frontal em todas as faces.
    """
    if not blend_refs:
        return
    if not images or len(images) < 2:
        raise ValueError("blend_refs precisa de 2+ imagens em `images` (uma única referência não mistura)")
    if azims is not None and len(azims) != len(images):
        raise ValueError(f"image_azims tem {len(azims)} valores para {len(images)} imagens")


def build_texture_request(
    *,
    mesh_path: str,
    image_path: str,
    output: str,
    images: list[str] | None = None,
    blend_refs: bool = False,
    image_azims: Any = None,
    max_num_view: int = 6,
    view_resolution: int = 512,
    render_size: int = 1024,
    texture_size: int = 1024,
    bake_exp: float | None = None,
    verbose: bool = False,
    preserve_origin: bool = True,
    smooth: bool = True,
    smooth_passes: int | None = None,
    upscale: bool = False,
    upscale_factor: float | None = None,
    gpu_ids: list[int] | str | None = None,
    torch_compile: bool = False,
    torch_compile_mode: str | None = None,
    channels_last: bool = False,
    allow_group_offload: bool = True,
    memory_efficient: bool = False,
    sdnq_preset: str | None = None,
    pbr_enrich: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload vramd paint3d com peak opts."""
    ref_images = [str(p) for p in images] if images else None
    azims = _coerce_ref_azims(image_azims)
    _validate_multi_ref(ref_images, blend_refs, azims)
    payload = build_request_body(
        core={
            "mesh_path": str(mesh_path),
            "image_path": str(image_path),
            "output": str(output),
            "max_num_view": int(max_num_view),
            "view_resolution": int(view_resolution),
            "render_size": int(render_size),
            "texture_size": int(texture_size),
            "verbose": bool(verbose),
            "preserve_origin": bool(preserve_origin),
            "smooth": bool(smooth),
            "torch_compile": bool(torch_compile),
            "channels_last": bool(channels_last),
            "allow_group_offload": bool(allow_group_offload),
        },
        optional={
            # Multi-ref: o worker passa a lista ao paint_mesh (blend por vista).
            "images": ref_images,
            "blend_refs": True if blend_refs else None,
            "image_azims": azims,
            "bake_exp": bake_exp,
            "smooth_passes": None if smooth_passes is None else int(smooth_passes),
            "upscale": True if upscale else None,
            "upscale_factor": None if upscale_factor is None else float(upscale_factor),
            "torch_compile_mode": torch_compile_mode,
            "pbr_enrich": pbr_enrich,
        },
        extra=extra,
    )

    mem_eff = bool(memory_efficient)
    preset = sdnq_preset
    if preset is None and mem_eff:
        preset = "sdnq-uint8"
    elif preset in (None, "none", ""):
        preset = "none" if not mem_eff else "sdnq-uint8"

    return with_vramd_peak_opts(
        with_vramd_load_opts(payload, gpu_ids=gpu_ids),
        backend="paint3d",
        memory_efficient=mem_eff,
        sdnq_preset=None if preset in (None, "none", "") else preset,
    )
