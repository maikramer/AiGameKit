"""Helpers para CLIs das ferramentas GPU — reduz boilerplate duplicado.

Extrai padrões que eram copy-pasted across 9 CLIs:

  - ``prepare_gpu_exclusive()``: sequência ensure_vram → kill → clear → enforce
    (era inline em Text3D e Paint3D._prepare_gpu, 5 call sites).
  - ``apply_quality_defaults()``: resolve defaults do QualityEngine só quando o
    user não explicitou (era ~15 linhas x 13 call sites).
  - ``try_ums_delegation()`` / ``add_ums_options``: delegação UMS com prioridade,
    stream e ``--no-ums``.
  - ``make_profiler()``: envolve o padrão ProfilerSession + prof_log.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from rich.console import Console

from .logging import Logger
from .model_server import delegate_to_ums, send_request_stream

_logger = Logger()

F = TypeVar("F", bound=Callable[..., Any])


def add_ums_options(fn: F) -> F:
    """Decorator Click: acrescenta ``--ums-priority``, ``--no-ums``, ``--ums-stream``.

    Usar por cima do ``@click.pass_context`` (ou por baixo das outras options)::

        @cli.command("generate")
        @click.option(...)
        @add_ums_options
        @click.pass_context
        def generate_cmd(ctx, ..., ums_priority, no_ums, ums_stream):
            ...
    """
    try:
        import rich_click as click
    except ImportError:  # pragma: no cover
        import click  # type: ignore[no-redef]

    fn = click.option(
        "--ums-stream",
        is_flag=True,
        default=False,
        help="Mostra eventos NDJSON do UMS (fila, started, progress) durante a geração.",
    )(fn)
    fn = click.option(
        "--no-ums",
        is_flag=True,
        default=False,
        help="Não delegar no Unified Model Server; forçar geração in-process.",
    )(fn)
    fn = click.option(
        "--ums-priority",
        type=click.Choice(["interactive", "batch"], case_sensitive=False),
        default=None,
        help=("Prioridade na fila UMS (default: interactive, ou GAMEDEV_UMS_PRIORITY). GameAssets batch usa 'batch'."),
    )(fn)
    return fn  # type: ignore[return-value]


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
    from .model_server import (
        UMS_DO_NOT_KILL_TIP,
        ensure_vram_available,
        fetch_ums_queue_snapshot,
        format_ums_holding_summary,
        is_ums_running,
        ums_is_busy,
    )

    # Pedir aos model servers / UMS para descarregar (libertar VRAM).
    ensure_vram_available(needed_mib=needed_mib)

    kill = env_bool(kill_others_env, kill_others) if kill_others_env else kill_others
    allow = allow_shared or (env_bool(allow_shared_env, False) if allow_shared_env else False)

    if kill:
        snap = fetch_ums_queue_snapshot() if is_ums_running() else None
        if ums_is_busy(snap):
            hold = format_ums_holding_summary(snap) if snap else "UMS busy"
            if console is not None:
                from rich.panel import Panel

                console.print(
                    Panel(
                        f"[bold yellow]Kill GPU recusado — UMS tem jobs[/bold yellow]\n"
                        f"{hold}\n[dim]{UMS_DO_NOT_KILL_TIP}[/dim]",
                        border_style="yellow",
                    )
                )
            raise click.ClickException(f"Kill GPU recusado: UMS tem jobs na fila ({hold}). {UMS_DO_NOT_KILL_TIP}")
        if console is not None:
            from rich.panel import Panel

            console.print(
                Panel(
                    "[bold]Terminar processos GPU alvo[/bold]\n"
                    f"[dim]Desliga com --no-gpu-kill-others ou {kill_others_env}=0[/dim]\n"
                    f"[dim]{UMS_DO_NOT_KILL_TIP}[/dim]",
                    border_style="yellow",
                )
            )
        for line in kill_gpu_compute_processes_aggressive(exclude_pid=os.getpid()):
            if console is not None:
                console.print(f"[dim]{line}[/dim]")
            if line.startswith("[recusado]"):
                raise click.ClickException(line)
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


def _ums_debug_enabled() -> bool:
    """``GAMEDEV_UMS_DEBUG=1`` imprime o bloco ums_debug completo."""
    return os.environ.get("GAMEDEV_UMS_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def format_ums_debug_line(result: dict[str, Any]) -> str:
    """Linha compacta de debug a partir de ``ums_debug`` / campos top-level."""
    dbg = result.get("ums_debug") or {}
    backend = dbg.get("backend") or result.get("backend") or "?"
    job = str(dbg.get("job_id") or result.get("job_id") or "")[:8]
    wait = dbg.get("queue_wait_sec")
    gen = dbg.get("generate_sec")
    cuts = dbg.get("affinity_cuts")
    pri = dbg.get("priority") or result.get("priority") or "?"
    parts = [f"backend={backend}", f"pri={pri}"]
    if job:
        parts.append(f"job={job}…")
    if wait is not None:
        parts.append(f"wait={wait}s")
    if gen is not None:
        parts.append(f"gen={gen}s")
    if cuts is not None:
        parts.append(f"cuts={cuts}")
    loaded = dbg.get("loaded_backends")
    if loaded is not None:
        parts.append(f"loaded={loaded}")
    return " | ".join(parts)


def _print_ums_success(
    console: Console,
    *,
    noun: str,
    result: dict[str, Any],
    t_start: float,
    output_key: str = "output",
) -> None:
    from .gpu import format_bytes

    elapsed = time.time() - t_start
    out = result.get(output_key) or result.get("output")
    try:
        sz = format_bytes(Path(str(out)).stat().st_size) if out else "?"
    except OSError:
        sz = "?"
    console.print(f"[bold green]\u2713[/bold green] {noun} (via UMS): [cyan]{out}[/cyan] [dim]({sz})[/dim]")
    if result.get("seed") is not None:
        console.print(f"[dim]Seed: {result.get('seed', '?')}[/dim]")
    console.print(f"[dim]UMS: {format_ums_debug_line(result)}[/dim]")
    console.print(f"[dim]Tempo total (cliente): {elapsed:.1f}s[/dim]")
    if _ums_debug_enabled() and result.get("ums_debug"):
        import json

        console.print(f"[dim]ums_debug: {json.dumps(result['ums_debug'], ensure_ascii=False)}[/dim]")


def _ums_generate_stream(
    tool: str,
    payload: dict[str, Any],
    *,
    priority: str | None,
    timeout_sec: float,
    console: Console,
) -> dict[str, Any] | None:
    """Gera via UMS com ``stream: true`` e imprime eventos de fila/progresso."""
    from .model_server import UMS_SOCKET, ensure_ums_running, resolve_ums_priority

    if not ensure_ums_running():
        return None
    pri = resolve_ums_priority(priority if priority is not None else payload.get("priority"))
    req = {"cmd": "generate", "backend": tool, "stream": True, "priority": pri, **payload}
    req["priority"] = pri
    final: dict[str, Any] | None = None
    for event in send_request_stream(req, UMS_SOCKET, timeout_sec=timeout_sec):
        if "event" in event and "status" not in event:
            ev = event.get("event")
            if ev == "queued":
                pos = event.get("queue_position", "?")
                jid = str(event.get("job_id", ""))[:8]
                console.print(
                    f"[dim]UMS fila: pos={pos} pri={event.get('priority', pri)} "
                    f"job={jid or '?'}… backend={event.get('backend', tool)}[/dim]"
                )
            elif ev == "started":
                wait = event.get("queue_wait_sec")
                cuts = event.get("affinity_cuts")
                extra = []
                if wait is not None:
                    extra.append(f"wait={wait}s")
                if cuts is not None:
                    extra.append(f"cuts={cuts}")
                suffix = f" ({', '.join(extra)})" if extra else ""
                console.print(f"[dim]UMS started: {event.get('backend', tool)}{suffix}[/dim]")
            elif ev == "progress":
                pct = event.get("pct")
                msg = event.get("message") or ""
                pct_s = f"{pct:.0%}" if isinstance(pct, (int, float)) else "?"
                console.print(f"[dim]UMS progresso: {pct_s} {msg}[/dim]")
            continue
        final = event
    return final


def with_ums_load_opts(
    payload: dict[str, Any],
    *,
    gpu_ids: list[int] | str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Copia ``payload`` e injeta kwargs de load UMS (``gpu_ids``, etc.).

    Usar antes de ``try_ums_delegation`` / ``call_ums`` para o BackendManager
    passar ``gpu_ids`` ao ``adapter.load``.
    """
    out = dict(payload)
    if gpu_ids is not None:
        if isinstance(gpu_ids, str):
            parsed = [int(x.strip()) for x in gpu_ids.split(",") if x.strip()]
        else:
            parsed = [int(x) for x in gpu_ids]
        if parsed:
            out["gpu_ids"] = parsed
    for key, value in extra.items():
        if value is not None:
            out[key] = value
    return out


def call_ums(
    tool: str,
    payload: dict[str, Any],
    *,
    priority: str | None = None,
    stream: bool = False,
    timeout_sec: float = 600.0,
    console: Console | None = None,
) -> dict[str, Any] | None:
    """Chamada UMS de baixo nível (sync ou stream). Não faz fallback.

    Returns:
        Dict de resposta, ou ``None`` se o UMS não estiver disponível.
    """
    if stream and console is not None:
        return _ums_generate_stream(tool, payload, priority=priority, timeout_sec=timeout_sec, console=console)
    return delegate_to_ums(tool, payload, timeout_sec=timeout_sec, priority=priority)


def try_ums_delegation(
    tool: str,
    payload: dict[str, Any],
    *,
    t_start: float,
    noun: str,
    console: Console,
    output_key: str = "output",
    timeout_sec: float = 600.0,
    enabled: bool = True,
    priority: str | None = None,
    stream: bool = False,
) -> bool:
    """Delega uma geração no UMS se ativo. Retorna ``True`` se handled.

    Helper para CLIs: no início do comando ``generate``, chamar esta função.
    Se retornar ``True``, a geração foi feita pelo UMS — o caller deve ``return``.
    Se ``False``, fazer fallback in-process.

    ``queue_full`` **não** faz fallback (evita carregar o modelo em paralelo) —
    levanta ``click.ClickException``.

    Args:
        tool: Nome do backend (ex: ``text2icon``).
        payload: Parâmetros do pedido (prompt, output, steps, ...).
        t_start: Timestamp de início (``time.time()``) para calcular elapsed.
        noun: Substantivo para a mensagem de sucesso (ex: ``"Ícone"``, ``"Textura"``).
        console: Console Rich para output.
        output_key: Chave do payload que contém o path de output (default ``"output"``).
        enabled: Se ``False`` (``--no-ums``), salta a delegação.
        priority: ``interactive`` | ``batch`` (ou None → env / default).
        stream: Se ``True``, imprime eventos de fila/progresso (``--ums-stream``).

    Returns:
        ``True`` se o UMS handle o pedido (caller deve return);
        ``False`` se deve fazer fallback in-process (UMS down ou erro).
    """
    if not enabled:
        console.print("[dim]UMS: --no-ums → geração in-process[/dim]")
        return False

    output = payload.get(output_key)
    if output is None:
        return False

    # Opt-in stream via env (GameAssets batch --ums-stream → GAMEDEV_UMS_STREAM=1).
    if not stream:
        stream = os.environ.get("GAMEDEV_UMS_STREAM", "").strip().lower() in ("1", "true", "yes", "on")

    from .model_server import (
        UMS_DO_NOT_KILL_TIP,
        fetch_ums_queue_snapshot,
        format_ums_holding_summary,
        is_ums_running,
        ums_is_busy,
    )

    if is_ums_running():
        snap = fetch_ums_queue_snapshot()
        if ums_is_busy(snap) and snap is not None:
            console.print(f"[dim]UMS ocupado: {format_ums_holding_summary(snap)}[/dim]")
        if not stream:
            console.print("[dim]UMS: a enfileirar… (progresso: --ums-stream | gamedev-model-server queue)[/dim]")
    else:
        console.print("[dim]UMS: a garantir supervisor (auto-start se GAMEDEV_UMS_AUTO_START≠0)…[/dim]")

    result = call_ums(
        tool,
        payload,
        priority=priority,
        stream=stream,
        timeout_sec=timeout_sec,
        console=console if stream else None,
    )
    if result is None:
        console.print(
            "[yellow]UMS indisponível — fallback in-process[/yellow]\n"
            f"[dim]Arranca com: gamedev-model-server start · {UMS_DO_NOT_KILL_TIP}[/dim]"
        )
        return False
    status = result.get("status")
    if status == "ok":
        _print_ums_success(console, noun=noun, result=result, t_start=t_start, output_key=output_key)
        return True
    if status == "queue_full":
        raise_if_ums_queue_full(result)
        return False  # pragma: no cover
    if status == "error":
        code = result.get("error_code", "?")
        hint = result.get("hint")
        console.print(f"[yellow]UMS erro [{code}]: {result.get('error', '?')} — fallback in-process[/yellow]")
        if hint:
            console.print(f"[dim]hint: {hint}[/dim]")
        console.print(f"[dim]UMS: {format_ums_debug_line(result)}[/dim]")
        if _ums_debug_enabled() and result.get("ums_debug"):
            import json

            console.print(f"[dim]ums_debug: {json.dumps(result['ums_debug'], ensure_ascii=False)}[/dim]")
    return False


def raise_if_ums_queue_full(result: dict[str, Any] | None) -> None:
    """Levanta ClickException se a resposta UMS for ``queue_full`` (para call sites legacy)."""
    if result is not None and result.get("status") == "queue_full":
        import click

        depth = result.get("queue_depth", "?")
        max_d = result.get("max_depth", "?")
        code = result.get("error_code", "QUEUE_FULL")
        hint = result.get("hint") or "Espera ou aumenta GAMEDEV_UMS_MAX_QUEUE_DEPTH."
        dbg = format_ums_debug_line(result)
        raise click.ClickException(
            f"UMS [{code}] fila cheia ({depth}/{max_d}): {result.get('error', 'queue_full')}. {hint} ({dbg})"
        )


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
