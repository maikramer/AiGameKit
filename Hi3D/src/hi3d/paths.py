"""Model artifact location and completeness checks."""

from __future__ import annotations

import os
from pathlib import Path

from . import DEFAULT_MODEL_ID

#: Ficheiros que o loader do sparc3d lê do artefacto local (o card fixa-os como
#: layout mínimo). A ausência de qualquer um impede `local_files_only`.
REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "model_index.json",
    "scheduler/scheduler_config.json",
    "vae/config.json",
    "vae/model.safetensors",
    "transformer_512/config.json",
    "transformer_512/model.safetensors",
    "transformer_1024/config.json",
    "transformer_1024/model.safetensors",
)

#: Tamanho total esperado dos pesos (bytes) — usado para detetar downloads parciais.
EXPECTED_WEIGHT_BYTES = 7_729_000_000

#: Nome de pasta estável para o artefacto por omissão. O basename do repo HF
#: (``Hi3Dv1.0``) vira ``hi3dv1.0`` ao lowercase, que é ilegível e não bate com
#: o caminho documentado — o slug explícito é o contrato.
DEFAULT_MODEL_SLUG = "hi3d-v1.0"


def cache_root() -> Path:
    """Raiz de cache de modelos do AiGameKit (``AIGAMEKIT_MODELS_DIR`` ou XDG)."""
    raw = os.environ.get("AIGAMEKIT_MODELS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(cache).expanduser() if cache else Path.home() / ".cache"
    return base / "aigamekit" / "models"


def default_model_dir(model_id: str = DEFAULT_MODEL_ID) -> Path:
    """Diretório local canónico dos pesos (``<cache>/hi3d-v1.0``)."""
    if model_id == DEFAULT_MODEL_ID:
        return cache_root() / DEFAULT_MODEL_SLUG
    slug = model_id.rsplit("/", 1)[-1].lower().replace("/", "-")
    return cache_root() / slug


def resolve_model_ref(model: str | None) -> str:
    """Resolve ``--model`` para um diretório local completo ou o repo id HF.

    Aceita ``None`` (defeito do monorepo), um diretório local, ou um repo id.
    Quando o diretório canónico já está completo é preferido ao Hub — evita
    revalidar 7.7 GB de cache em cada refine.
    """
    if not model:
        cached = default_model_dir()
        return str(cached) if is_complete(cached) else DEFAULT_MODEL_ID
    path = Path(model).expanduser()
    if path.is_dir():
        return str(path)
    return model


def is_complete(model_dir: Path) -> bool:
    """True quando todos os artefactos obrigatórios existem e não estão vazios."""
    if not model_dir.is_dir():
        return False
    for rel in REQUIRED_ARTIFACTS:
        f = model_dir / rel
        if not f.is_file() or f.stat().st_size <= 0:
            return False
    return True


def weight_bytes(model_dir: Path) -> int:
    """Soma dos ``*.safetensors`` presentes (0 se o diretório não existir)."""
    if not model_dir.is_dir():
        return 0
    total = 0
    for f in model_dir.rglob("*.safetensors"):
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def missing_artifacts(model_dir: Path) -> list[str]:
    """Lista relativa dos artefactos em falta (vazia quando completo)."""
    if not model_dir.is_dir():
        return list(REQUIRED_ARTIFACTS)
    missing: list[str] = []
    for rel in REQUIRED_ARTIFACTS:
        f = model_dir / rel
        if not f.is_file() or f.stat().st_size <= 0:
            missing.append(rel)
    return missing
