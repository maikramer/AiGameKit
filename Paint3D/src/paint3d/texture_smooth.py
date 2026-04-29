"""
Suavização de texturas UV por filtro bilateral edge-preserving.

Remove artefatos de costura do bake multiview (bordas serrilhadas, ruído
entre regiões de cor, pixels inconsistentes nas transições de vista) sem
alterar resolução nem borrar bordas nítidas.

Usa ``cv2.bilateralFilter`` — O(N) com parâmetros moderados.
Roda em CPU, sem custo de VRAM.
"""

from __future__ import annotations

import bpy
import cv2
import numpy as np
from PIL import Image

from gamedev_shared.logging import Logger

_logger = Logger()


def smooth_texture(
    image: Image.Image,
    *,
    passes: int = 2,
    diameter: int = 9,
    sigma_color: float = 50.0,
    sigma_space: float = 50.0,
    verbose: bool = False,
) -> Image.Image:
    """
    Aplica filtro bilateral edge-preserving à textura.

    Parameters
    ----------
    image : PIL.Image
        Textura UV (qualquer resolução).
    passes : int
        Número de passadas (mais = mais suave, mas preserva bordas).
    diameter : int
        Diâmetro do kernel de vizinhança (pixels). 5-15 recomendado.
    sigma_color : float
        Sigma no espaço de cor: valores maiores misturam cores mais
        diferentes. 30-75 para suavização leve, 75-150 para agressiva.
    sigma_space : float
        Sigma no espaço de coordenadas: alcance espacial do filtro.
    verbose : bool
        Imprime informação de progresso.
    """
    arr = np.array(image.convert("RGB"), dtype=np.uint8)

    if verbose:
        _logger.info(
            f"{arr.shape[1]}x{arr.shape[0]} · {passes} passes · d={diameter} sc={sigma_color} ss={sigma_space}"
        )

    for i in range(passes):
        arr = cv2.bilateralFilter(arr, diameter, sigma_color, sigma_space)
        if verbose and passes > 1:
            _logger.dim(f"pass {i + 1}/{passes}")

    return Image.fromarray(arr)


def _get_base_color_image(
    obj: bpy.types.Object,
) -> tuple[bpy.types.Image | None, bpy.types.ShaderNodeTexImage | None]:
    """Extract the base color image and its texture node from a bpy mesh object."""
    if not obj.data.materials:
        return None, None
    mat = obj.data.materials[0]
    if not mat.use_nodes:
        return None, None

    # Find Principled BSDF and Image Texture linked to Base Color
    bsdf = None
    tex_node = None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED" and bsdf is None:
            bsdf = node
        if node.type == "TEX_IMAGE" and tex_node is None:
            tex_node = node

    # Prefer the texture node connected to BSDF Base Color
    if bsdf is not None:
        for link in mat.node_tree.links:
            if link.to_node == bsdf and link.to_socket.name == "Base Color" and link.from_node.type == "TEX_IMAGE":
                tex_node = link.from_node
                break

    if tex_node is None or tex_node.image is None:
        return None, None
    return tex_node.image, tex_node


def _bpy_image_to_pil(bpy_image: bpy.types.Image) -> Image.Image:
    """Convert a bpy Image to a PIL Image (RGB)."""
    w, h = bpy_image.size
    pixels = np.array(bpy_image.pixels[:]).reshape(h, w, 4)
    return Image.fromarray((pixels[:, :, :3] * 255).astype(np.uint8))


def _write_pil_to_bpy(pil_image: Image.Image, bpy_image: bpy.types.Image) -> None:
    """Write a PIL Image back into an existing bpy Image in-place."""
    new_w, new_h = pil_image.size
    arr = np.array(pil_image.convert("RGB"), dtype=np.float32) / 255.0
    if bpy_image.size[0] != new_w or bpy_image.size[1] != new_h:
        bpy_image.scale(new_w, new_h)
    rgba = np.ones((new_h, new_w, 4), dtype=np.float32)
    rgba[:, :, :3] = arr
    bpy_image.pixels = rgba.ravel().tolist()
    bpy_image.update()
    bpy_image.pack()


def smooth_trimesh_texture(
    obj: bpy.types.Object,
    *,
    passes: int = 2,
    diameter: int = 9,
    sigma_color: float = 50.0,
    sigma_space: float = 50.0,
    verbose: bool = False,
) -> bpy.types.Object:
    """Aplica suavização bilateral à textura baseColor de um mesh bpy. Aceita lista — usa o primeiro MESH."""
    if isinstance(obj, list):
        meshes = [o for o in obj if hasattr(o, "data") and o.type == "MESH"]
        if not meshes:
            return obj
        obj = meshes[0]
    if not (hasattr(obj, "data") and obj.type == "MESH"):
        raise TypeError(f"Expected bpy MESH object, got {type(obj)}")

    bpy_image, _tex_node = _get_base_color_image(obj)

    if bpy_image is None:
        if verbose:
            _logger.dim("Mesh sem textura baseColor — nada a fazer.")
        return obj

    texture = _bpy_image_to_pil(bpy_image)

    smoothed = smooth_texture(
        texture,
        passes=passes,
        diameter=diameter,
        sigma_color=sigma_color,
        sigma_space=sigma_space,
        verbose=verbose,
    )

    _write_pil_to_bpy(smoothed, bpy_image)

    return obj
