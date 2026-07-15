"""CLI: pré-quantizar o DiT Hunyuan3D-Part (qint8) para cache HF.

Uso::

    python -m part3d.quantize_dit
    python -m part3d.quantize_dit --force

O instalador universal chama isto após instalar o Part3D (salvo ``--skip-models``).
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    # Antes de qualquer import que puxe ``transformers`` / ``optimum.quanto``.
    from part3d.utils.transformers_pkg_mapping_fix import apply as _transformers_pkg_fix

    _transformers_pkg_fix()

    parser = argparse.ArgumentParser(description="Pré-quantiza o DiT Part3D (optimum-quanto qint8).")
    parser.add_argument(
        "--repo-id",
        default="tencent/Hunyuan3D-Part",
        help="Repo HuggingFace dos pesos (model)",
    )
    parser.add_argument("--force", action="store_true", help="Regenerar mesmo se artefactos existirem")
    args = parser.parse_args(argv)

    try:
        from part3d.utils.dit_quantization import quantize_and_save_dit
    except ImportError as e:
        print("Erro: optimum-quanto ou dependências em falta. pip install optimum-quanto", file=sys.stderr)
        print(e, file=sys.stderr)
        return 1

    ok = quantize_and_save_dit(repo_id=args.repo_id, force=args.force)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
