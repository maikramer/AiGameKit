"""Resolução de pesos do compphoto/Intrinsic com compat de release.

Bug upstream (release v2.1 do repo): ``load_models('v2.1')`` descarrega
``v2.1/stage_N.pt`` mas os assets da release chamam-se ``stage_N_v21.pt``
(HTTP 404). Este módulo tenta o caminho canónico primeiro e, se falhar,
descarrega os assets com os nomes certos (cache do torch.hub) e passa a
LISTA de ficheiros ao ``load_models`` — replicando ``alb_residual=True``,
que o ramo 'v2.1' ativa internamente mas a API por lista não aplica.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

V21_BASE_URL = "https://github.com/compphoto/Intrinsic/releases/download/v2.1/"
V21_FILENAMES = [f"stage_{n}_v21.pt" for n in range(5)]


def torch_hub_dir() -> Path:
    import os

    import torch

    hub_dir = os.environ.get("TORCH_HOME")
    if hub_dir:
        return Path(hub_dir) / "hub" / "checkpoints"
    return Path(torch.hub.get_dir()) / "checkpoints"


def download_v21_weights() -> list[Path]:
    """Descarrega (se ausentes) os assets v2.1 com os nomes corretos e devolve
    os caminhos em cache (ordenados por estágio)."""
    from torch.hub import load_state_dict_from_url

    checkpoints = torch_hub_dir()
    checkpoints.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fname in V21_FILENAMES:
        # O dict devolvido é irrelevante aqui; o que importa é o ficheiro
        # cacheado sob checkpoints/<fname>.
        load_state_dict_from_url(V21_BASE_URL + fname, map_location="cpu", progress=False)
        p = checkpoints / fname
        if not p.is_file():
            raise RuntimeError(f"peso {fname} não apareceu na cache {checkpoints}")
        paths.append(p)
    return paths


def load_models_compat(release: str = "v2.1", device: str = "cuda") -> Any:
    """``intrinsic.pipeline.load_models`` com tolerância ao bug da v2.1.

    Ordem: caminho canónico (funciona para 'v2' e para 'v2.1' se a cache já
    tiver os ficheiros sob os nomes antigos); em falha, fallback por lista.
    """
    from intrinsic.pipeline import load_models

    if release != "v2.1":
        return load_models(release, device=device)

    try:
        return load_models(release, device=device)
    except Exception as canonical_error:
        try:
            paths = download_v21_weights()
        except Exception as download_error:
            raise RuntimeError(
                f"load_models('v2.1') falhou ({canonical_error}) e o fallback "
                f"de download também ({download_error}); tenta release='v2'"
            ) from download_error
        # O ramo 'v2.1' define alb_residual=True internamente; a API por
        # lista NÃO aplica — replicar aqui.
        return load_models(paths, device=device, alb_residual=True)
