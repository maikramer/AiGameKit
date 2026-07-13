"""Helpers para CLIs das ferramentas GPU — reduz boilerplate duplicado.

Extrai padrões que eram copy-pasted across 9 CLIs:

  - ``prepare_gpu_exclusive()``: sequência ensure_vram → kill → clear → enforce
    (era inline em Text3D e Paint3D._prepare_gpu, 5 call sites).
  - ``apply_quality_defaults()``: resolve defaults do QualityEngine só quando o
    user não explicitou (era ~15 linhas x 13 call sites).
  - ``try_ums_delegation()``: delega no UMS, imprime, retorna se handled.
  - ``make_profiler()``: envolve o padrão ProfilerSession + prof_log.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from .logging import Logger
from .model_server import delegate_to_ums

_logger = Logger()


def env_bool(env_var: str, cli_wants: bool) -> bool:
    """Resolve um boolean: env var tem precedência sobre o flag CLI.

    Args:
        env_var: Nome da variável de ambiente (ex: ``TEXT3D_ALLOW_SHARED_GPU``).
        cli_wants: Valor pretendido pelo flag CLI (``--allow-shared-gpu``).

    Returns:
        ``True``/``False`` segundo o env var, ou ``cli_wants`` se não definido.
    """
    v = os.environ.get(env_var, "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return cli_wants


def prepare_gpu_exclusive(
    *,
    needed_mib: int = 4000,
    allow_shared: bool = False,
    kill_others: bool = True,
    allow_shared_env: str = "",
    kill_others_env: str = "",
    console: Console | None = None,
) -> None:
    """Sequência completa de preparação de GPU para tools pesadas.

    Ordem: ``ensure_vram_available`` (pede a model servers para libertar) →
    ``kill_gpu_compute_processes_aggressive`` (SIGTERM outros processos GPU) →
    ``clear_cuda_memory`` → ``enforce_exclusive_gpu`` (gate de VRAM ocupada).

    Args:
        needed_mib: VRAM necessária para o model server libertar (default 4000).
        allow_shared: Permitir GPU partilhada (``--allow-shared-gpu``).
        kill_others: Matar processos GPU alheios (``--gpu-kill-others``).
        allow_shared_env: Env var que override allow_shared (ex: ``TEXT3D_ALLOW_SHARED_GPU``).
        kill_others_env: Env var que override kill_others (ex: ``TEXT3D_GPU_KILL_OTHERS``).
        console: Console Rich para output (opcional).

    Raises:
        click.ClickException: Se ``enforce_exclusive_gpu`` falhar (VRAM ocupada).
    """
    import click

    from .gpu import (
        clear_cuda_memory,
        enforce_exclusive_gpu,
        kill_gpu_compute_processes_aggressive,
        warn_if_vram_occupied,
    )
    from .model_server import ensure_vram_available

    # Pedir aos model servers ativos para descarregar (libertar VRAM).
    ensure_vram_available(needed_mib=needed_mib)

    kill = env_bool(kill_others_env, kill_others) if kill_others_env else kill_others
    allow = allow_shared or (env_bool(allow_shared_env, False) if allow_shared_env else False)

    if kill:
        if console is not None:
            from rich.panel import Panel

            console.print(
                Panel(
                    "[bold]Terminar processos GPU alvo[/bold]\n"
                    f"[dim]Desliga com --no-gpu-kill-others ou {kill_others_env}=0[/dim]",
                    border_style="yellow",
                )
            )
        for line in kill_gpu_compute_processes_aggressive(exclude_pid=os.getpid()):
            if console is not None:
                console.print(f"[dim]{line}[/dim]")
        clear_cuda_memory()
        time.sleep(0.5)

    try:
        enforce_exclusive_gpu(allow_shared=allow)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    warn_if_vram_occupied()


def apply_quality_defaults(
    ctx: Any,
    tool: str,
    quality: str,
    param_map: dict[str, str],
    *,
    category: str | None = None,
) -> dict[str, Any]:
    """Aplica defaults do QualityEngine só quando o user não explicitou.

    Remove o boilerplate ``_user_set_X = ctx.get_parameter_source(...)`` que era
    copy-pasted em cada CLI (~15 linhas x 13 call sites).

    Args:
        ctx: Click context (com ``get_parameter_source``).
        tool: Nome da tool para o QualityEngine (ex: ``texture2d``).
        quality: Nível de qualidade (``fast|low|medium|high|highest``).
        param_map: Mapeamento ``{click_param_name: local_var}`` ex:
            ``{"width": "width", "steps": "steps", "guidance": "guidance_scale"}``.
            Para cada entrada, se o user não explicitou e o QualityEngine tem o
            param na resolução, o valor é incluído no dict retornado.
        category: Categoria de asset opcional (ex: ``lod0``, ``texture``).

    Returns:
        Dict ``{local_var: resolved_value}`` APENAS para os params que foram
        preenchidos pelo QualityEngine (não explicitados pelo user). O caller
        deve aplicar: ``locals().update(resolved)`` ou atribuir individualmente.
    """
    import click

    from .quality import QualityEngine

    _src = click.core.ParameterSource
    qengine = QualityEngine()
    qresolved = qengine.resolve(tool, quality=quality, category=category)

    resolved: dict[str, Any] = {}
    for click_param, local_var in param_map.items():
        try:
            user_set = ctx.get_parameter_source(click_param) not in (_src.DEFAULT,)
        except (KeyError, AttributeError):
            user_set = True  # se não dá para determinar, assumir que user explicitou
        if not user_set:
            # O QualityEngine usa chaves sem sufixo (ex: "guidance", "steps", "width").
            if local_var in qresolved.params:
                resolved[local_var] = qresolved.params[local_var]
            elif click_param in qresolved.params:
                resolved[local_var] = qresolved.params[click_param]
    return resolved


def try_ums_delegation(
    tool: str,
    payload: dict[str, Any],
    *,
    t_start: float,
    noun: str,
    console: Console,
    output_key: str = "output",
    timeout_sec: float = 600.0,
) -> bool:
    """Delega uma geração no UMS se ativo. Retorna ``True`` se handled.

    Helper para CLIs: no início do comando ``generate``, chamar esta função.
    Se retornar ``True``, a geração foi feita pelo UMS — o caller deve ``return``.
    Se ``False``, fazer fallback in-process.

    Args:
        tool: Nome do backend (ex: ``text2icon``).
        payload: Parâmetros do pedido (prompt, output, steps, ...).
        t_start: Timestamp de início (``time.time()``) para calcular elapsed.
        noun: Substantivo para a mensagem de sucesso (ex: ``"Ícone"``, ``"Textura"``).
        console: Console Rich para output.
        output_key: Chave do payload que contém o path de output (default ``"output"``).

    Returns:
        ``True`` se o UMS handle o pedido (caller deve return);
        ``False`` se deve fazer fallback in-process (UMS down ou erro).
    """
    from .gpu import format_bytes

    output = payload.get(output_key)
    if output is None:
        return False

    result = delegate_to_ums(tool, payload, timeout_sec=timeout_sec)
    if result and result.get("status") == "ok":
        elapsed = time.time() - t_start
        try:
            sz = format_bytes(Path(result["output"]).stat().st_size)
        except OSError:
            sz = "?"
        console.print(
            f"[bold green]\u2713[/bold green] {noun} (via UMS): [cyan]{result['output']}[/cyan] [dim]({sz})[/dim]"
        )
        if result.get("seed") is not None:
            console.print(f"[dim]Seed: {result.get('seed', '?')}[/dim]")
        console.print(f"[dim]Tempo total: {elapsed:.1f}s[/dim]")
        return True
    if result and result.get("status") == "error":
        console.print(f"[yellow]UMS erro: {result.get('error', '?')} — fallback in-process[/yellow]")
    return False


def make_profiler(
    tool: str,
    *,
    cli_flag: bool = False,
    model_id: str = "",
    params: dict[str, Any] | None = None,
) -> tuple[Any, Path | None]:
    """Constrói um ProfilerSession e resolve o path de log.

    Envolve o padrão duplicado em Text2D/Text3D/Paint3D:
        ``log_p = env_profile_log_path(); prof_log = Path(log_p) if log_p else None``
        ``with ProfilerSession(tool, log_path=prof_log, cli_profile=cli_flag, ...) as prof:``

    Returns:
        Tuple ``(profiler_context, prof_log_path)``. O caller usa:
        ``prof, prof_log = make_profiler("text2d", cli_flag=profile, ...)``
        ``with prof: ...``
    """
    from .profiler import ProfilerSession
    from .profiler.env import env_profile_log_path

    log_p = env_profile_log_path()
    prof_log = Path(log_p) if log_p else None
    profiler = ProfilerSession(
        tool,
        log_path=prof_log,
        cli_profile=cli_flag,
        model_id=model_id,
        params=params,
    )
    return profiler, prof_log
