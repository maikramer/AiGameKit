"""
Resolve o código ``hy3dshape`` vendored em ``text3d.hy3dshape``.

O código vem de https://github.com/Tencent-Hunyuan/Hunyuan3D-Omni (pasta ``hy3dshape/``),
integrado directamente no pacote — sem submodule.

Os **modelos** (pesos) são descarregados sob demanda via ``huggingface_hub`` a partir
de ``tencent/Hunyuan3D-Omni`` (repo flat: ``model/``, ``vae/``, ``cond_encoder/``).

O import principal é relativo (``from .hy3dshape.pipelines import ...``) dentro do
pacote ``text3d``. Esta função utilitária garante que ``import hy3dshape`` absoluto
também funciona (necessário para módulos internos do upstream que usam import absoluto).

Com ``quiet=True`` (default vramd/adapter): redireciona ``print`` do vendor para o
logger ``hy3dshape`` (nível DEBUG), alinhado ao logging do monorepo.
"""

from __future__ import annotations

import builtins
import logging
import os
import sys
from pathlib import Path

_logger = logging.getLogger("hy3dshape")
_print_patched = False


def resolve_hy3dshape_root() -> Path:
    """Return the vendored ``hy3dshape`` directory inside this package."""
    return Path(__file__).resolve().parent / "hy3dshape"


def _align_hy3dgen_cache() -> None:
    """Fallback download do vendor → sob HF_HOME (evita ``~/.cache/hy3dgen`` órfão).

    O wrapper Text3D usa ``ensure_model`` e passa path local; isto só cobre o
    ramo ``from_pretrained`` quando o path ainda não existe.
    """
    if os.environ.get("HY3DGEN_MODELS"):
        return
    hf_home = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    os.environ["HY3DGEN_MODELS"] = str(Path(hf_home) / "hy3dgen")


def _install_quiet_print() -> None:
    """``print`` dentro de ``hy3dshape.*`` → ``logging.getLogger("hy3dshape").debug``."""
    global _print_patched
    if _print_patched:
        return
    _orig_print = builtins.print

    def _hy3d_print(*args: object, **kwargs: object) -> None:
        try:
            frame = sys._getframe(1)
            mod = frame.f_globals.get("__name__") or ""
        except (ValueError, AttributeError):
            mod = ""
        if isinstance(mod, str) and (mod == "hy3dshape" or mod.startswith("hy3dshape.")):
            msg = " ".join(str(a) for a in args)
            if msg.strip():
                _logger.debug(msg)
            return
        _orig_print(*args, **kwargs)  # type: ignore[misc]

    builtins.print = _hy3d_print  # type: ignore[assignment]
    _print_patched = True


def ensure_hy3dshape_on_path(*, quiet: bool = True) -> Path:
    """Make top-level ``import hy3dshape`` resolve to the vendored copy.

    Adds the parent of the vendored ``hy3dshape/`` package to ``sys.path``
    so that absolute imports like ``from hy3dshape.pipelines import ...``
    work. Idempotent — safe to call multiple times.

    Args:
        quiet: Se True, ``print`` do vendor vai para logger DEBUG (padrão vramd).
    """
    parent = str(resolve_hy3dshape_root().parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    _align_hy3dgen_cache()
    if quiet:
        _install_quiet_print()
    return resolve_hy3dshape_root()
