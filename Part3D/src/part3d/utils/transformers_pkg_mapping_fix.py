"""Workaround para ``transformers`` sem entrada ``flash_attn`` em ``PACKAGE_DISTRIBUTION_MAPPING``.

Em alguns Python/pip, ``importlib.metadata.packages_distributions()`` não inclui
``flash_attn``; funções como ``is_flash_attn_2_available`` fazem KeyError ao
importar ``transformers.integrations``. Isto quebra ``optimum.quanto`` (importa
transformers). Aplicar antes de importar ``optimum.quanto``.
"""

from __future__ import annotations


def apply() -> None:
    try:
        import transformers.utils.import_utils as iu

        m = iu.PACKAGE_DISTRIBUTION_MAPPING
        if not isinstance(m, dict):
            return
        # Nomes canónicos de distribuição (PEP 503); evita KeyError e tuple vazio
        # em ``_is_package_available(..., return_version=True)`` quando o pacote
        # não está no mapa devolvido por ``packages_distributions()``.
        m.setdefault("flash_attn", ("flash-attn",))
        m.setdefault("flash_attn_interface", ("flash-attn-3",))
    except Exception:
        pass
