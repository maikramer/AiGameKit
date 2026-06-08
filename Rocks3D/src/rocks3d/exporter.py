"""GLB export via trimesh with PBR material embedding.

Exports a :class:`trimesh.Trimesh` to GLB with a proper glTF
:class:`~trimesh.visual.material.PBRMaterial` carrying base-color, normal,
metallic-roughness and occlusion textures.  Crucially, any UV coordinates
already present on the mesh (from the UV-mapping stage) are **preserved** —
without them the textures would not map onto the surface at all.

The mesh origin is translated so the lowest vertex sits at ``y = 0``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import trimesh


def _load_image(path: "Path | None"):  # noqa: UP037
    """Open an image path if it exists, else return ``None``."""
    from PIL import Image

    if path is not None and path.exists():
        return Image.open(path).convert("RGB")
    return None


def _build_metallic_roughness(textures: dict[str, Path | None]):
    """Pack a glTF metallic-roughness texture (G=roughness, B=metallic).

    Accepts either a ``"roughness"`` map directly or a ``"smoothness"`` map
    (which is inverted).  Rocks are dielectric, so the metallic (blue)
    channel is left at zero.

    Args:
        textures: Texture path mapping.

    Returns:
        A PIL ``Image`` in RGB mode, or ``None`` if no roughness data.
    """
    import numpy as np
    from PIL import Image

    rough_img = _load_image(textures.get("roughness"))
    if rough_img is None:
        smooth_img = _load_image(textures.get("smoothness"))
        if smooth_img is None:
            return None
        rough = 255 - np.asarray(smooth_img)[:, :, 0]
    else:
        rough = np.asarray(rough_img)[:, :, 0]

    h, w = rough.shape
    mr = np.zeros((h, w, 3), dtype=np.uint8)
    mr[:, :, 1] = rough  # G = roughness
    # B (metallic) stays 0 — stone is non-metallic.
    return Image.fromarray(mr, mode="RGB")


def export_glb(
    mesh: trimesh.Trimesh,
    textures: dict[str, Path | None],
    output_path: Path,
    material_name: str = "rock",
) -> Path:
    """Export a mesh to GLB with a full PBR material.

    Translates the mesh so the lowest vertex sits at ``y = 0``, builds a
    :class:`~trimesh.visual.material.PBRMaterial` from whatever textures
    are supplied, re-attaches the mesh's existing UV coordinates, and
    writes a GLB file.

    Args:
        mesh: The mesh to export (may already carry UVs from UV mapping).
        textures: Mapping with optional keys ``"albedo"``, ``"normal"``,
            ``"roughness"`` / ``"smoothness"``, ``"ao"``.  Values are paths
            to PNG images or ``None``.
        output_path: Destination file path (should end in ``.glb``).
        material_name: Name for the generated material.

    Returns:
        The *output_path* that was written.
    """
    import trimesh

    y_min = mesh.vertices[:, 1].min()
    mesh.vertices[:, 1] -= y_min

    # Preserve UVs produced by the UV-mapping stage; without them textures
    # are sampled at (0, 0) and the mesh looks untextured.
    existing_uv = getattr(mesh.visual, "uv", None)

    albedo_img = _load_image(textures.get("albedo"))
    normal_img = _load_image(textures.get("normal"))
    mr_img = _build_metallic_roughness(textures)
    ao_img = _load_image(textures.get("ao"))

    material = trimesh.visual.material.PBRMaterial(
        name=material_name,
        baseColorTexture=albedo_img,
        baseColorFactor=[255, 255, 255, 255] if albedo_img is not None else [128, 128, 128, 255],
        metallicFactor=0.0,
        roughnessFactor=1.0,
        metallicRoughnessTexture=mr_img,
        normalTexture=normal_img,
        occlusionTexture=ao_img,
    )

    mesh.visual = trimesh.visual.texture.TextureVisuals(uv=existing_uv, material=material)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    scene = trimesh.Scene(geometry=mesh)
    scene.export(str(output_path), file_type="glb")

    return output_path
