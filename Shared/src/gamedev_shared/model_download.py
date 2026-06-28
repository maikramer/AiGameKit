"""Preflight de download de modelos HuggingFace: garante que o checkpoint base
está presente **antes** de carregar o pipeline, com resume e progresso, de forma
não-interativa.

Motivação: ``from_pretrained`` baixa on-demand a meio da inferência — se a rede
falhar, perde-se o trabalho e o utilizador vê a GPU "presa". Fazer o download como
um passo explícito (em ``doctor`` e no início de ``warmup``) dá progresso claro,
permite resume e separa "a baixar" de "a inferir".

Resume é o comportamento por defeito do ``snapshot_download`` do ``huggingface_hub``;
este módulo só o expõe com uma API estável e mensagens de estado consistentes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

StatusCallback = Callable[[str], None]


def _hf_cache_dir(cache_dir: str | None) -> str | None:
    """Diretório de cache efetivo (arg explícito ganha; senão HF_HOME/default)."""
    if cache_dir:
        return cache_dir
    return os.environ.get("HF_HUB_CACHE") or None


def is_model_cached(
    repo_id: str,
    *,
    cache_dir: str | None = None,
    revision: str | None = None,
) -> bool:
    """True se o snapshot do ``repo_id`` já está totalmente em cache local.

    Usa ``try_to_load_from_cache``/``snapshot_download(local_files_only=True)`` — não
    toca na rede. Conservador: qualquer falha → ``False`` (assume não-cacheado).
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import LocalEntryNotFoundError

        try:
            snapshot_download(
                repo_id,
                revision=revision,
                cache_dir=_hf_cache_dir(cache_dir),
                local_files_only=True,
            )
            return True
        except (LocalEntryNotFoundError, FileNotFoundError):
            return False
    except Exception:
        return False


def ensure_model(
    repo_id: str,
    *,
    cache_dir: str | None = None,
    revision: str | None = None,
    allow_patterns: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
    on_status: StatusCallback | None = None,
) -> Path:
    """Garante que o modelo está em disco; baixa (com resume) se faltar.

    Não-interativo: nunca pede input. Em ambiente sem rede, se já estiver em cache
    devolve o caminho; caso contrário propaga a exceção do ``huggingface_hub`` para
    o chamador decidir (fallback gracioso fica a cargo da ferramenta).

    Args:
        repo_id: ``org/model`` no HuggingFace Hub.
        cache_dir: cache local (default: cache HF padrão / ``HF_HUB_CACHE``).
        revision: branch/tag/commit (default: ``main``).
        allow_patterns: só baixa ficheiros que casem (ex.: ``["*.safetensors", "*.json"]``).
        ignore_patterns: exclui ficheiros (ex.: ``["*.bin"]`` para evitar duplicados pickle).
        on_status: callback de mensagens de estado (ex.: Rich/Logger).

    Returns:
        ``Path`` do diretório do snapshot local.
    """
    from huggingface_hub import snapshot_download

    # Barras de progresso do hub ligadas salvo se o utilizador as desligou.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

    cached = is_model_cached(repo_id, cache_dir=cache_dir, revision=revision)
    if on_status:
        on_status(f"{'em cache' if cached else 'a baixar'}: {repo_id}")

    # Resume é sempre feito pelo snapshot_download (o antigo arg ``resume_download`` foi
    # depreciado e ignorado) — não o passamos para evitar o UserWarning.
    path = snapshot_download(
        repo_id,
        revision=revision,
        cache_dir=_hf_cache_dir(cache_dir),
        allow_patterns=allow_patterns,
        ignore_patterns=ignore_patterns,
    )
    if on_status and not cached:
        on_status(f"download completo: {repo_id}")
    return Path(path)
