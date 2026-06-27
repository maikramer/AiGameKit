"""Processamento de imagem para Texture2D — diffuse tileable + metadata JSON.

Este módulo grava apenas o diffuse RGB tileable e o sidecar JSON com os
parâmetros de geração. PBR maps (normal/height/metallic/roughness/AO) são
gerados separadamente pelo CLI Materialize (`materialize diffuse.png
--output-dir pbr/`), não por este módulo. O CLI Texture2D pode invocar o
`materialize` como subprocesso depois de gravar o diffuse; esse passo
opcional vive no CLI, não aqui.

Image I/O reutiliza o helper partilhado ``gamedev_shared.image_utils``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from gamedev_shared.image_utils import save_image_with_metadata
from gamedev_shared.logging import Logger

DEFAULT_OUTPUT_DIR = Path("outputs") / "textures"

logger = Logger()


def save_image(
    image: Image.Image,
    prompt: str,
    params: dict[str, Any],
    output_dir: Path | None = None,
    filename: str | None = None,
) -> Path:
    """Grava o diffuse tileable RGB PNG + sidecar JSON de metadata.

    Não gera PBR maps — esses são produzidos pelo CLI Materialize a partir
    deste diffuse (ver docstring do módulo).

    Args:
        image: Imagem PIL (diffuse tileable). Convertida para RGB se necessário.
        prompt: Prompt original fornecido pelo utilizador.
        params: Parâmetros de geração (seed, model, seamless_method, quant,
            prompt_final, steps, guidance, etc.).
        output_dir: Diretório de saída (default ``DEFAULT_OUTPUT_DIR``).
        filename: Nome do ficheiro PNG (auto-gerado pelo helper se ``None``).

    Returns:
        Caminho do ficheiro PNG gravado.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    extra_metadata: dict[str, Any] = {
        "prompt_final": params.get("prompt_final", prompt),
        "seed": params.get("seed"),
        "model": params.get("model"),
        "seamless_method": params.get("seamless_method"),
        "quant": params.get("quant"),
    }

    saved = save_image_with_metadata(
        image,
        prompt,
        params,
        output_dir=output_dir or DEFAULT_OUTPUT_DIR,
        filename=filename,
        metadata=extra_metadata,
        image_format="PNG",
    )
    logger.info(f"Diffuse tileable + metadata JSON gravados em {saved}")
    return saved
