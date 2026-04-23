"""Variáveis de ambiente partilhadas do monorepo GameDev."""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Nomes canónicos das variáveis de ambiente
# ---------------------------------------------------------------------------

TEXT2D_BIN = "TEXT2D_BIN"
TEXT3D_BIN = "TEXT3D_BIN"
TEXT2SOUND_BIN = "TEXT2SOUND_BIN"
TEXTURE2D_BIN = "TEXTURE2D_BIN"
SKYMAP2D_BIN = "SKYMAP2D_BIN"
RIGGING3D_BIN = "RIGGING3D_BIN"
GAMEASSETS_BIN = "GAMEASSETS_BIN"
GAMEDEVLAB_BIN = "GAMEDEVLAB_BIN"
MATERIALIZE_BIN = "MATERIALIZE_BIN"
PAINT3D_BIN = "PAINT3D_BIN"
ANIMATOR3D_BIN = "ANIMATOR3D_BIN"
PART3D_BIN = "PART3D_BIN"
TERRAINGEN_BIN = "TERRAINGEN_BIN"
VIBEGAME_BIN = "VIBEGAME_BIN"
HF_HOME = "HF_HOME"
PYTORCH_CUDA_ALLOC_CONF = "PYTORCH_CUDA_ALLOC_CONF"

TOOL_BINS = {
    "text2d": TEXT2D_BIN,
    "text3d": TEXT3D_BIN,
    "text2sound": TEXT2SOUND_BIN,
    "texture2d": TEXTURE2D_BIN,
    "skymap2d": SKYMAP2D_BIN,
    "rigging3d": RIGGING3D_BIN,
    "gameassets": GAMEASSETS_BIN,
    "gamedevlab": GAMEDEVLAB_BIN,
    "paint3d": PAINT3D_BIN,
    "animator3d": ANIMATOR3D_BIN,
    "part3d": PART3D_BIN,
    "terraingen": TERRAINGEN_BIN,
    "materialize": MATERIALIZE_BIN,
    "vibegame": VIBEGAME_BIN,
}
"""Mapeamento tool_name → nome da variável de ambiente do binário.

Inclui ferramentas Python, ``materialize`` (Rust) e ``vibegame`` (Bun/Node); o valor é o nome da env var
(``MATERIALIZE_BIN``, ``VIBEGAME_BIN``, etc.), não o caminho do binário.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_pytorch_cuda_alloc_conf(
    value: str = "expandable_segments:True",
) -> None:
    """Define ``PYTORCH_CUDA_ALLOC_CONF`` se ainda não estiver definido.

    Reduz fragmentação de VRAM em GPUs com pouca memória.
    """
    if os.environ.get(PYTORCH_CUDA_ALLOC_CONF):
        return
    os.environ[PYTORCH_CUDA_ALLOC_CONF] = value


def subprocess_gpu_env(
    extra: dict[str, str] | None = None,
    gpu_ids: list[int] | None = None,
) -> dict[str, str]:
    """Ambiente para subprocessos GPU: copia env e aplica CUDA alloc se vazio.

    Útil para o GameAssets ao lançar text2d/text3d como filhos.

    Args:
        extra: Additional env vars to merge into the returned dict.
        gpu_ids: GPU device IDs to expose via ``CUDA_VISIBLE_DEVICES``.
            When provided and non-empty, sets ``CUDA_VISIBLE_DEVICES`` to a
            comma-separated string (e.g. ``[0, 1]`` → ``"0,1"``).
            Pass ``None`` (default) to omit the variable.

    Returns:
        Environment dict ready for ``subprocess.run(env=…)``.
    """
    env = os.environ.copy()
    if not env.get(PYTORCH_CUDA_ALLOC_CONF):
        env[PYTORCH_CUDA_ALLOC_CONF] = "expandable_segments:True"
    if gpu_ids:
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in gpu_ids)
    if extra:
        env.update(extra)
    return env


def get_tool_bin(tool_name: str) -> str | None:
    """Retorna o caminho do binário de uma ferramenta via variável de ambiente.

    Returns:
        Caminho se a variável estiver definida, ``None`` caso contrário.
    """
    env_name = TOOL_BINS.get(tool_name)
    if env_name is None:
        return None
    return os.environ.get(env_name, "").strip() or None
