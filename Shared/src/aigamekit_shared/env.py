"""Variáveis de ambiente partilhadas do monorepo AiGameKit."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

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
AIGAMEKITLAB_BIN = "AIGAMEKITLAB_BIN"
MATERIALIZE_BIN = "MATERIALIZE_BIN"
PAINT3D_BIN = "PAINT3D_BIN"
ANIMATOR3D_BIN = "ANIMATOR3D_BIN"
TERRAIN3D_BIN = "TERRAIN3D_BIN"
ROCKS3D_BIN = "ROCKS3D_BIN"
PART3D_BIN = "PART3D_BIN"
MOTION3D_BIN = "MOTION3D_BIN"
VIBEGAME_BIN = "VIBEGAME_BIN"
VRAMD_BIN = "VRAMD_BIN"
HF_HOME = "HF_HOME"
PYTORCH_CUDA_ALLOC_CONF = "PYTORCH_CUDA_ALLOC_CONF"
VRAMD_CLIENT_SOCKET = "VRAMD_CLIENT_SOCKET"
AIGAMEKIT_PREFER_MONOREPO = "AIGAMEKIT_PREFER_MONOREPO"

TOOL_BINS = {
    "text2d": TEXT2D_BIN,
    "text2icon": TEXT2ICON_BIN,
    "text3d": TEXT3D_BIN,
    "text2sound": TEXT2SOUND_BIN,
    "texture2d": TEXTURE2D_BIN,
    "skymap2d": SKYMAP2D_BIN,
    "rigging3d": RIGGING3D_BIN,
    "gameassets": GAMEASSETS_BIN,
    "aigamekitlab": AIGAMEKITLAB_BIN,
    "paint3d": PAINT3D_BIN,
    "animator3d": ANIMATOR3D_BIN,
    "terrain3d": TERRAIN3D_BIN,
    "rocks3d": ROCKS3D_BIN,
    "part3d": PART3D_BIN,
    "motion3d": MOTION3D_BIN,
    "materialize": MATERIALIZE_BIN,
    "vibegame": VIBEGAME_BIN,
    "vramd": VRAMD_BIN,
}
"""Mapeamento tool_name → nome da variável de ambiente do binário.

Inclui ferramentas Python, ``materialize`` (Rust) e ``vibegame`` (Bun/Node); o valor é o nome da env var
(``MATERIALIZE_BIN``, ``VIBEGAME_BIN``, etc.), não o caminho do binário.
"""

ENV_TO_TOOL = {env_name: tool for tool, env_name in TOOL_BINS.items()}
"""Reverse map: ``TEXT3D_BIN`` → ``text3d``."""


@dataclass(frozen=True)
class ToolLayout:
    """Layout no checkout do monorepo para resolver CLIs locais."""

    folder: str
    cli_name: str
    kind: str = "python"  # python | rust | bun


TOOL_LAYOUT: dict[str, ToolLayout] = {
    "text2d": ToolLayout("Text2D", "text2d"),
    "text2icon": ToolLayout("Text2Icon", "text2icon"),
    "text3d": ToolLayout("Text3D", "text3d"),
    "text2sound": ToolLayout("Text2Sound", "text2sound"),
    "texture2d": ToolLayout("Texture2D", "texture2d"),
    "skymap2d": ToolLayout("Skymap2D", "skymap2d"),
    "rigging3d": ToolLayout("Rigging3D", "rigging3d"),
    "gameassets": ToolLayout("GameAssets", "gameassets"),
    "aigamekitlab": ToolLayout("AiGameKitLab", "aigamekit-lab"),
    "paint3d": ToolLayout("Paint3D", "paint3d"),
    "animator3d": ToolLayout("Animator3D", "animator3d"),
    "terrain3d": ToolLayout("Terrain3D", "terrain3d"),
    "rocks3d": ToolLayout("Rocks3D", "rocks3d"),
    "part3d": ToolLayout("Part3D", "part3d"),
    "motion3d": ToolLayout("Motion3D", "motion3d"),
    "vramd": ToolLayout("Vramd", "vramd"),
    "materialize": ToolLayout("Materialize", "materialize", kind="rust"),
    "vibegame": ToolLayout("VibeGame", "vibegame", kind="bun"),
}
"""tool_name → pasta / CLI no monorepo (alinhado a ``tools.yaml``)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def prefer_monorepo_tools() -> bool:
    """Se ``True``, ``resolve_binary`` / env de filhos preferem ``<pkg>/.venv`` do checkout.

    Default ``True``. Desliga com ``AIGAMEKIT_PREFER_MONOREPO=0`` (instalação só via
    ``~/.local/bin`` / catálogo remoto sem checkout).
    """
    raw = os.environ.get(AIGAMEKIT_PREFER_MONOREPO, "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _venv_scripts_dir(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts"
    return venv_dir / "bin"


def _is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    if sys.platform == "win32":
        return True
    return os.access(path, os.X_OK)


def discover_monorepo_tool_bin(
    tool_name: str,
    *,
    monorepo: Path | None = None,
) -> str | None:
    """Resolve CLI no checkout: ``<root>/<Folder>/.venv/bin/<cli>`` (ou equivalente).

    Args:
        tool_name: Chave em :data:`TOOL_LAYOUT` (ex. ``text3d``, ``aigamekitlab``).
        monorepo: Raiz do monorepo; se ``None``, tenta detetar a partir deste ficheiro.

    Returns:
        Caminho absoluto do executável, ou ``None`` se o monorepo/venv/bin não existir.
    """
    layout = TOOL_LAYOUT.get(tool_name)
    if layout is None:
        return None

    if monorepo is None:
        from .monorepo import try_find_monorepo_root

        monorepo = try_find_monorepo_root(Path(__file__).resolve())
    if monorepo is None:
        return None

    root = monorepo.resolve()
    folder = root / layout.folder

    if layout.kind == "python":
        scripts = _venv_scripts_dir(folder / ".venv")
        candidates = [
            scripts / layout.cli_name,
            scripts / f"{layout.cli_name}.exe",
            scripts / f"{layout.cli_name}.cmd",
        ]
        for cand in candidates:
            if _is_executable(cand):
                return str(cand.resolve())
        return None

    if layout.kind == "rust":
        for cand in (
            folder / "target" / "release" / layout.cli_name,
            folder / "target" / "release" / f"{layout.cli_name}.exe",
            folder / "target" / "release" / "materialize-cli",
            folder / "target" / "release" / "materialize-cli.exe",
        ):
            if _is_executable(cand):
                return str(cand.resolve())
        return None

    if layout.kind == "bun":
        script = folder / "scripts" / "vibegame-cli.mjs"
        if script.is_file():
            return str(script.resolve())
        return None

    return None


def discover_monorepo_tool_python(
    tool_name: str,
    *,
    monorepo: Path | None = None,
) -> str | None:
    """Resolve o interpretador Python do venv da tool no checkout.

    Útil para o vramd spawnar workers subprocesso no venv canónico de cada tool:
    ``Text3D/.venv/bin/python -m text3d serve --ums-worker``.

    Args:
        tool_name: Chave em :data:`TOOL_LAYOUT` (ex. ``text3d``, ``paint3d``).
        monorepo: Raiz do monorepo; se ``None``, tenta detetar automaticamente.

    Returns:
        Caminho absoluto do ``python`` do venv, ou ``None`` se o venv não existir.
    """
    layout = TOOL_LAYOUT.get(tool_name)
    if layout is None or layout.kind != "python":
        return None

    if monorepo is None:
        from .monorepo import try_find_monorepo_root

        monorepo = try_find_monorepo_root(Path(__file__).resolve())
    if monorepo is None:
        return None

    scripts = _venv_scripts_dir(monorepo.resolve() / layout.folder / ".venv")
    for cand in (scripts / "python", scripts / "python.exe"):
        if _is_executable(cand):
            # NÃO resolver o symlink: ``Paint3D/.venv/bin/python`` é um link
            # para o python base UV, mas é o symlink que activa o venv (sys.path
            # aponta para os site-packages da tool). ``.resolve()`` quebraria
            # isto — o python base NU não tem os packages da tool.
            return str(cand)
    return None


def apply_monorepo_tool_bins(env: dict[str, str]) -> dict[str, str]:
    """Preenche ``*_BIN`` em falta com CLIs do checkout (quando preferência monorepo activa).

    Não sobrescreve variáveis já definidas. No-op se ``AIGAMEKIT_PREFER_MONOREPO=0``
    ou se a raiz do monorepo não for encontrada.
    """
    if not prefer_monorepo_tools():
        return env

    from .monorepo import try_find_monorepo_root

    root = try_find_monorepo_root(Path(__file__).resolve())
    if root is None:
        return env

    for tool_name, env_name in TOOL_BINS.items():
        if env.get(env_name, "").strip():
            continue
        local = discover_monorepo_tool_bin(tool_name, monorepo=root)
        if local:
            env[env_name] = local
    return env


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

    Quando o checkout do monorepo está presente, preenche ``*_BIN`` em falta com
    ``<Tool>/.venv/bin/<cli>`` para que edições locais (install editável) sejam
    usadas em vez de wrappers stale em ``~/.local/bin``. Desliga com
    ``AIGAMEKIT_PREFER_MONOREPO=0``.

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

    apply_monorepo_tool_bins(env)

    # Se houver model servers ativos, propagar o socket path aos children e
    # desligar o kill-others para não matarem o server. As tools pesadas usam
    # ``ensure_vram_available`` (pede release gracioso) antes de precisar disto,
    # mas esta é a rede de segurança para children que chamam kill_gpu_compute.
    try:
        from .vramd_client import discover_active_sockets

        if discover_active_sockets():
            # Propagar o socket path (se definido via env)
            sock = os.environ.get(VRAMD_CLIENT_SOCKET, "").strip()
            if sock:
                env.setdefault(VRAMD_CLIENT_SOCKET, sock)
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

    Uses Shared GPU helpers (NVML → ``nvidia-smi``).  Returns ``False`` if
    detection fails (conservative: assume sufficient memory).

    Args:
        threshold_mb: VRAM threshold in MiB.  GPUs below this are considered
            memory-constrained.

    Returns:
        ``True`` if the primary GPU has less than *threshold_mb* MiB total VRAM.
    """
    try:
        from .gpu import query_gpu_snapshot

        snap = query_gpu_snapshot(0)
        if snap is None:
            return False
        return int(snap.total_mib) < int(threshold_mb)
    except Exception:
        return False
