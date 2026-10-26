"""Extração de silhueta a partir de uma imagem — rembg + threshold de alpha.

Não existe silhouette conditioning nativo no FLUX.3 Image Klein: a silhueta
entra como uma **imagem de referência** normal do workflow
``image_conditioned`` (o modelo copia a forma; o prompt descreve o estilo).
Fluxo típico para animação 2D — consistência de personagem entre poses:

    text2d silhouette hero.png -o hero_sil.png                 # extrair
    text2d generate "same character running, flat colors" \
        --ref-silhouette hero.png -o hero_run.png              # gerar pose nova

Import lazy do ``rembg`` — igual ao :mod:`text2d.bg_removal`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from aigamekit_shared.logging import Logger

_logger = Logger()

# Alpha >= ALPHA_THRESHOLD conta como sujeito (fundo removido → 0).
ALPHA_THRESHOLD = 128


def extract_silhouette(image: Image.Image, *, session: Any = None, threshold: int = ALPHA_THRESHOLD) -> Image.Image:
    """Extrai a silhueta binária de uma imagem.

    Pipeline: rembg (U2Net) devolve RGBA → alpha binarizado no threshold →
    preenchimento branco. A silhueta resultante (RGBA, fundo transparente,
    sujeito branco) serve de referência de forma para gerações novas.

    Args:
        image: Imagem PIL de entrada (RGB ou RGBA).
        session: Sessão rembg reutilizada (opcional).
        threshold: Limiar de alpha (0-255) para separar sujeito/fundo.

    Returns:
        Imagem PIL ``RGBA`` — sujeito branco opaco, fundo transparente.

    Raises:
        ImportError: Se o ``rembg`` não estiver instalado.
    """
    from .bg_removal import remove_background

    cutout = remove_background(image.convert("RGB"), session=session)
    alpha = cutout.getchannel("A").point(lambda a: 255 if a >= threshold else 0)
    white = Image.new("RGBA", cutout.size, (255, 255, 255, 255))
    white.putalpha(alpha)
    return white


def extract_silhouette_file(
    input_path: str | Path, output_path: str | Path, *, threshold: int = ALPHA_THRESHOLD
) -> Path:
    """Carrega ``input_path``, extrai a silhueta e grava em ``output_path`` (PNG)."""
    src = Image.open(input_path).convert("RGB")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    extract_silhouette(src, threshold=threshold).save(out, format="PNG")
    _logger.info(f"silhueta guardada em {out}")
    return out
