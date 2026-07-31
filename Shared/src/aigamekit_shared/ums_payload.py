"""Corpo de request UMS partilhado — montagem de payload sem wrap por-tool.

Antes, cada tool (text2d, text2icon, texture2d, skymap2d, text2sound, text3d,
paint3d, terrain3d) duplicava o mesmo esqueleto no seu ``ums_payload.py``:

1. dict core com coerções (``int(width)``, ``float(guidance)``…);
2. bloco ``if x is not None: payload["x"] = …`` para as keys opcionais;
3. ``if extra: payload.update(extra)`` (o caller manda em tudo);
4. wrap final com ``with_ums_peak_opts(with_ums_load_opts(payload, gpu_ids=…))``
   — este fica por-tool porque a derivação de ``memory_efficient``/quant difere
   (ver ``build_generate_request`` de cada tool).

:func:`build_request_body` cobre os passos 1-3; cada tool mantém a assinatura
explícita (CLI/GameAssets chamam com kwargs por-tool) e o wrap de 4-6 linhas.
"""

from __future__ import annotations

from typing import Any


def build_request_body(
    *,
    prompt: str | None = None,
    output: str | None = None,
    core: dict[str, Any] | None = None,
    optional: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta o corpo de um request UMS de geração.

    Ordem de precedência (a última vence):
    ``prompt``/``output`` → ``core`` → ``optional`` → ``extra``.

    Args:
        prompt: Texto do prompt (coercido a ``str``). Tools sem prompt
            (ex. paint3d/text3d usam ``from_image``/``mesh_path``) passam
            ``None`` e metem o campo no ``core``.
        output: Caminho de saída (coercido a ``str``).
        core: Keys sempre presentes, já com os tipos coerciados.
        optional: Keys opcionais — valores ``None`` são omitidos.
        extra: Overrides arbitrários do caller (ex. GameAssets), aplicados
            por último.

    Returns:
        Dict pronto para o wrap com ``with_ums_peak_opts``/``with_ums_load_opts``.
    """
    payload: dict[str, Any] = {}
    if prompt is not None:
        payload["prompt"] = str(prompt)
    if output is not None:
        payload["output"] = str(output)
    if core:
        payload.update(core)
    if optional:
        for key, value in optional.items():
            if value is not None:
                payload[key] = value
    if extra:
        payload.update(extra)
    return payload
