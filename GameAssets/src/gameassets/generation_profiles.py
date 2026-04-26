"""Generation quality profiles: fast, low, medium, high, highest.

Each profile tunes all tools (Text2D, Text3D shape, Paint3D texture,
simplification, Text2Sound) for a quality/speed trade-off.

Usage in ``game.yaml`` (root level)::

    generation: medium

Per-row override in ``manifest.yaml``::

    - id: hero
      idea: "fantasy hero"
      generation: high

Explicit tool settings in ``game.yaml`` always win over the profile default.
The profile only fills in ``None`` / default fields.
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_GENERATION_PROFILES = ("fast", "low", "medium", "high", "highest")


@dataclass(frozen=True)
class GenerationProfile:
    name: str
    # --- Text2D ---
    text2d_width: int
    text2d_height: int
    text2d_steps: int
    text2d_guidance: float
    # --- Text3D (Hunyuan shape) ---
    text3d_preset: str
    text3d_guidance: float
    # --- Paint3D texture ---
    paint_max_views: int
    paint_view_resolution: int
    paint_render_size: int
    paint_texture_size: int
    paint_bake_exp: int
    paint_smooth: bool
    paint_smooth_passes: int
    # --- Simplification (text3d remesh-textured) ---
    simplify_face_ratio: float
    simplify_texture_size: int
    # --- Text2Sound ---
    text2sound_steps: int


PROFILES: dict[str, GenerationProfile] = {
    "fast": GenerationProfile(
        name="fast",
        text2d_width=512,
        text2d_height=512,
        text2d_steps=4,
        text2d_guidance=1.0,
        text3d_preset="fast",
        text3d_guidance=5.0,
        paint_max_views=2,
        paint_view_resolution=384,
        paint_render_size=1024,
        paint_texture_size=1024,
        paint_bake_exp=6,
        paint_smooth=True,
        paint_smooth_passes=2,
        simplify_face_ratio=0.25,
        simplify_texture_size=1024,
        text2sound_steps=4,
    ),
    "low": GenerationProfile(
        name="low",
        text2d_width=768,
        text2d_height=768,
        text2d_steps=4,
        text2d_guidance=1.0,
        text3d_preset="fast",
        text3d_guidance=5.0,
        paint_max_views=4,
        paint_view_resolution=384,
        paint_render_size=1024,
        paint_texture_size=2048,
        paint_bake_exp=6,
        paint_smooth=True,
        paint_smooth_passes=3,
        simplify_face_ratio=0.5,
        simplify_texture_size=1024,
        text2sound_steps=8,
    ),
    "medium": GenerationProfile(
        name="medium",
        text2d_width=1024,
        text2d_height=1024,
        text2d_steps=4,
        text2d_guidance=1.0,
        text3d_preset="balanced",
        text3d_guidance=5.0,
        paint_max_views=6,
        paint_view_resolution=512,
        paint_render_size=2048,
        paint_texture_size=2048,
        paint_bake_exp=6,
        paint_smooth=True,
        paint_smooth_passes=3,
        simplify_face_ratio=1.0,
        simplify_texture_size=2048,
        text2sound_steps=16,
    ),
    "high": GenerationProfile(
        name="high",
        text2d_width=1024,
        text2d_height=1024,
        text2d_steps=8,
        text2d_guidance=1.0,
        text3d_preset="hq",
        text3d_guidance=5.0,
        paint_max_views=8,
        paint_view_resolution=512,
        paint_render_size=2048,
        paint_texture_size=4096,
        paint_bake_exp=6,
        paint_smooth=True,
        paint_smooth_passes=3,
        simplify_face_ratio=2.0,
        simplify_texture_size=2048,
        text2sound_steps=24,
    ),
    "highest": GenerationProfile(
        name="highest",
        text2d_width=1024,
        text2d_height=1024,
        text2d_steps=8,
        text2d_guidance=1.0,
        text3d_preset="hq",
        text3d_guidance=5.0,
        paint_max_views=10,
        paint_view_resolution=512,
        paint_render_size=2048,
        paint_texture_size=4096,
        paint_bake_exp=6,
        paint_smooth=True,
        paint_smooth_passes=3,
        simplify_face_ratio=0.0,
        simplify_texture_size=4096,
        text2sound_steps=32,
    ),
}


def get_profile(name: str) -> GenerationProfile:
    if name not in PROFILES:
        raise ValueError(f"Perfil de geração desconhecido: {name!r}. Válidos: {', '.join(VALID_GENERATION_PROFILES)}")
    return PROFILES[name]
