"""Variáveis de ambiente partilhadas do monorepo GameDev."""

from __future__ import annotations

import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Nomes canónicos das variáveis de ambiente
# ---------------------------------------------------------------------------

TEXT2D_BIN = "TEXT2D_BIN"
TEXT2ICON_BIN = "TEXT2ICON_BIN"
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
TERRAIN3D_BIN = "TERRAIN3D_BIN"
ROCKS3D_BIN = "ROCKS3D_BIN"
PART3D_BIN = "PART3D_BIN"
VIBEGAME_BIN = "VIBEGAME_BIN"
MODELSERVER_BIN = "MODELSERVER_BIN"
HF_HOME = "HF_HOME"
PYTORCH_CUDA_ALLOC_CONF = "PYTORCH_CUDA_ALLOC_CONF"
GAMEDEV_MODEL_SERVER_SOCKET = "GAMEDEV_MODEL_SERVER_SOCKET"

TOOL_BINS = {
    "text2d": TEXT2D_BIN,
    "text2icon": TEXT2ICON_BIN,
    "text3d": TEXT3D_BIN,
    "text2sound": TEXT2SOUND_BIN,
    "texture2d": TEXTURE2D_BIN,
    "skymap2d": SKYMAP2D_BIN,
    "rigging3d": RIGGING3D_BIN,
    "gameassets": GAMEASSETS_BIN,
    "gamedevlab": GAMEDEVLAB_BIN,
    "paint3d": PAINT3D_BIN,
    "animator3d": ANIMATOR3D_BIN,
    "terrain3d": TERRAIN3D_BIN,
    "rocks3d": ROCKS3D_BIN,
    "part3d": PART3D_BIN,
    "materialize": MATERIALIZE_BIN,
    "vibegame": VIBEGAME_BIN,
    "modelserver": MODELSERVER_BIN,
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

    Reduz fragmentação de VRAM em GPUs com pouca memória (frequentemente a diferença
    entre OOM e fit em GPUs pequenas).

    Guards:
      - **Windows**: ``expandable_segments`` tem causado OOMs/erros em alguns setups
        com certas versões do PyTorch — é skipado em Windows a menos que o utilizador
        o defina explicitamente.
      - Se já definido pelo utilizador, respeita o valor.
    """
    if os.environ.get(PYTORCH_CUDA_ALLOC_CONF):
        return  # utilizador já definiu — respeitar
    # Guard Windows: expandable_segments pode causar OOM em alguns drivers Windows.
    if sys.platform == "win32" and "expandable_segments" in value:
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

    # Não forçar HF_HUB_ENABLE_HF_TRANSFER — removido no hub 1.x (hf_transfer).
    # Downloads rápidos via hf-xet / Xet nativo do huggingface_hub>=1.5.

    if gpu_ids:
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in gpu_ids)

    # Se houver model servers ativos, propagar o socket path aos children e
    # desligar o kill-others para não matarem o server. As tools pesadas usam
    # ``ensure_vram_available`` (pede release gracioso) antes de precisar disto,
    # mas esta é a rede de segurança para children que chamam kill_gpu_compute.
    try:
        from .model_server import discover_active_sockets

        if discover_active_sockets():
            # Propagar o socket path (se definido via env)
            sock = os.environ.get(GAMEDEV_MODEL_SERVER_SOCKET, "").strip()
            if sock:
                env.setdefault(GAMEDEV_MODEL_SERVER_SOCKET, sock)
            # Desligar kill-others nos children para não matarem o server
            env.setdefault("TEXT3D_GPU_KILL_OTHERS", "0")
            env.setdefault("PAINT3D_GPU_KILL_OTHERS", "0")
    except Exception:
        pass  # model_server indisponível; continuar sem proteção

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


def detect_low_memory(threshold_mb: int = 8192) -> bool:
    """Detect if the primary GPU has less than *threshold_mb* MiB of total VRAM.

    Uses ``nvidia-smi`` to query total memory.  Returns ``False`` if
    ``nvidia-smi`` is not available or parsing fails (conservative: assume
    sufficient memory when detection fails).

    Args:
        threshold_mb: VRAM threshold in MiB.  GPUs below this are considered
            memory-constrained.

    Returns:
        ``True`` if the primary GPU has less than *threshold_mb* MiB total VRAM.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        total_mb = int(result.stdout.strip().split("\n", 1)[0].strip())
        return total_mb < threshold_mb
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError, OSError):
        return False
