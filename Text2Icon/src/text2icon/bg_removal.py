"""Remoção de fundo (alpha) para ícones — via rembg (U2Net).

Import lazy do ``rembg`` para que o núcleo do text2icon funcione sem onnxruntime
instalado; a remoção de fundo só é exigida quando o utilizador passa
``--transparent``.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from aigamekit_shared.logging import Logger

_logger = Logger()


def remove_background(image: Image.Image, *, session: Any = None) -> Image.Image:
    """Remove o fundo de uma imagem RGB e devolve RGBA com alpha.

    Usa o modelo U2Net via ``rembg.remove``. A primeira invocação descarrega o
    modelo (~176 MB) e guarda-o na cache do HF/torch.

    Args:
        image: Imagem PIL de entrada (tipicamente RGB do Sana).
        session: Sessão rembg reutilizada (opcional). Se ``None``, cria uma nova.

    Returns:
        Imagem PIL em modo ``RGBA`` com o fundo transparente.

    Raises:
        ImportError: Se o ``rembg`` não estiver instalado.
    """
    try:
        from rembg import remove as _rembg_remove
    except ImportError as e:  # pragma: no cover - exige dep opcional
        raise ImportError("O pacote 'rembg' é necessário para remoção de fundo. Instale com: pip install rembg") from e

    _logger.dim("rembg: a remover fundo (U2Net)...")

    # rembg aceita numpy/PIL/bytes; devolve bytes PNG com alpha.
    out_bytes = _rembg_remove(image, session=session)

    out_image = Image.open(_bytes_io(out_bytes)) if isinstance(out_bytes, bytes) else out_bytes
    if out_image.mode != "RGBA":
        out_image = out_image.convert("RGBA")
    return out_image


def _bytes_io(data: bytes) -> Any:
    import io

    return io.BytesIO(data)
