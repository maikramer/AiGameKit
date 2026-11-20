"""Remoção de fundo (alpha) para ícones — via rembg (U2Net).

Import lazy do ``rembg`` para que o núcleo do text2d funcione sem onnxruntime
instalado; a remoção de fundo só é exigida quando o utilizador passa
``--transparent``.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from aigamekit_shared.logging import Logger

_logger = Logger()

# Cache de sessões rembg por modelo (evita recarregar U2Net ~176 MB em cada
# imagem quando um worker ou `-n > 1` processa várias imagens seguidas).
_SESSION_CACHE: dict[str, Any] = {}


def clear_session_cache() -> None:
    """Limpa sessões rembg cacheadas em memória (útil para testes/unload)."""
    _SESSION_CACHE.clear()


def _get_or_create_session(rembg_mod: Any, model_name: str = "u2net") -> Any:
    """Devolve sessão cacheada se ``rembg.new_session`` existir; senão ``None``."""
    cached = _SESSION_CACHE.get(model_name)
    if cached is not None:
        return cached
    new_session = getattr(rembg_mod, "new_session", None)
    if not callable(new_session):
        return None
    session = new_session(model_name)
    _SESSION_CACHE[model_name] = session
    return session


def remove_background(image: Image.Image, *, session: Any = None, model_name: str = "u2net") -> Image.Image:
    """Remove o fundo de uma imagem RGB e devolve RGBA com alpha.

    Usa o modelo U2Net via ``rembg.remove``. A primeira invocação descarrega o
    modelo (~176 MB) e guarda-o na cache do HF/torch; a sessão ONNX fica
    cacheada por processo quando ``session`` não é fornecida.

    Args:
        image: Imagem PIL de entrada (tipicamente RGB do FLUX).
        session: Sessão rembg reutilizada (opcional). Se ``None``, reutiliza a
            sessão cacheada do processo (ou cria uma nova).
        model_name: Nome do modelo rembg (default ``"u2net"``).

    Returns:
        Imagem PIL em modo ``RGBA`` com o fundo transparente.

    Raises:
        ImportError: Se o ``rembg`` não estiver instalado.
    """
    try:
        import rembg as _rembg_mod
        from rembg import remove as _rembg_remove
    except ImportError as e:  # pragma: no cover - exige dep opcional
        raise ImportError("O pacote 'rembg' é necessário para remoção de fundo. Instale com: pip install rembg") from e

    _logger.dim("rembg: a remover fundo (U2Net)...")

    active_session = session if session is not None else _get_or_create_session(_rembg_mod, model_name)

    # rembg aceita numpy/PIL/bytes; devolve bytes PNG com alpha.
    out_bytes = _rembg_remove(image, session=active_session)

    out_image = Image.open(_bytes_io(out_bytes)) if isinstance(out_bytes, bytes) else out_bytes
    if out_image.mode != "RGBA":
        out_image = out_image.convert("RGBA")
    return out_image


def _bytes_io(data: bytes) -> Any:
    import io

    return io.BytesIO(data)
