"""Orquestração Text3D do corte horizontal stump+top (delega a Shared)."""

from __future__ import annotations

from pathlib import Path

from aigamekit_shared.mesh_split import SplitResult, split_glb_at_height


def split_at_height_glb(
    input_path: Path,
    output_path: Path,
    *,
    cut_height: float | None = None,
    cut_ratio: float | None = None,
    cap: bool = False,
    bevel_offset: float | None = None,
    bevel_segments: int = 0,
    bevel_profile: float = 0.7,
    stump_name: str = "Stump",
    top_name: str = "Top",
    split_files: bool = False,
) -> SplitResult:
    """Parte GLB no eixo vertical; exporta composição multi-mesh Stump+Top.

    Ver ``aigamekit_shared.mesh_split.split_glb_at_height``.
    """
    return split_glb_at_height(
        input_path,
        output_path,
        cut_height=cut_height,
        cut_ratio=cut_ratio,
        cap=cap,
        bevel_offset=bevel_offset,
        bevel_segments=bevel_segments,
        bevel_profile=bevel_profile,
        stump_name=stump_name,
        top_name=top_name,
        split_files=split_files,
    )
