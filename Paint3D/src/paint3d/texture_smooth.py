"""
Suavização de texturas UV por filtro bilateral edge-preserving.

Remove artefatos de costura do bake multiview (bordas serrilhadas, ruído
entre regiões de cor, pixels inconsistentes nas transições de vista) sem
alterar resolução nem borrar bordas nítidas.

Usa ``cv2.bilateralFilter`` — O(N) com parâmetros moderados.
Roda em CPU, sem custo de VRAM.
"""

from __future__ import annotations

import cv2
import numpy as np
import trimesh
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


def smooth_trimesh_texture(
    mesh: trimesh.Trimesh,
    *,
    passes: int = 2,
    diameter: int = 9,
    sigma_color: float = 50.0,
    sigma_space: float = 50.0,
    verbose: bool = False,
) -> trimesh.Trimesh:
    """
    Aplica suavização bilateral à textura baseColor de uma mesh.

    Retorna a mesma mesh com textura suavizada (in-place no material).
    """
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"Expected Trimesh, got {type(mesh)}")

    vis = mesh.visual
    if not hasattr(vis, "material"):
        if verbose:
            _logger.dim("Mesh sem material — nada a fazer.")
        return mesh

    mat = vis.material
    texture = None

    if hasattr(mat, "baseColorTexture") and mat.baseColorTexture is not None:
        texture = mat.baseColorTexture
    elif hasattr(mat, "image") and mat.image is not None:
        texture = mat.image

    if texture is None:
        if verbose:
            _logger.dim("Mesh sem textura baseColor — nada a fazer.")
        return mesh

    smoothed = smooth_texture(
        texture,
        passes=passes,
        diameter=diameter,
        sigma_color=sigma_color,
        sigma_space=sigma_space,
        verbose=verbose,
    )

    if hasattr(mat, "baseColorTexture"):
        mat.baseColorTexture = smoothed
    elif hasattr(mat, "image"):
        mat.image = smoothed

    return mesh
