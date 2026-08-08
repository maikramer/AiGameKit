"""Helpers para CLIs das ferramentas GPU — reduz boilerplate duplicado.

Extrai padrões que eram copy-pasted across 9 CLIs:

  - ``prepare_gpu_exclusive()``: sequência ensure_vram → kill → clear → enforce
    (era inline em Text3D e Paint3D._prepare_gpu, 5 call sites).
  - ``apply_quality_defaults()``: resolve defaults do QualityEngine só quando o
    user não explicitou (era ~15 linhas x 13 call sites).
  - ``try_vramd_delegation()`` / ``add_vramd_options``: delegação vramd com prioridade,
    stream e ``--no-vramd``.
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
from .vramd_client import delegate_to_vramd, send_request_stream

_logger = Logger()

F = TypeVar("F", bound=Callable[..., Any])


def add_vramd_options(fn: F) -> F:
    """Decorator Click: acrescenta ``--vramd-priority``, ``--no-vramd``, ``--vramd-stream``.

    Usar por cima do ``@click.pass_context`` (ou por baixo das outras options)::

        @cli.command("generate")
        @click.option(...)
        @add_vramd_options
        @click.pass_context
        def generate_cmd(ctx, ..., ums_priority, no_ums, ums_stream):
            ...
    """
    try:
        import rich_click as click
    except ImportError:  # pragma: no cover
        import click

    fn = click.option(
        "--vramd-stream",
        is_flag=True,
        default=False,
        help="Mostra eventos NDJSON do vramd (fila, started, progress) durante a geração.",
    )(fn)
    fn = click.option(
        "--no-vramd",
        is_flag=True,
        default=False,
        help="Não delegar no Unified Model Server; forçar geração in-process.",
    )(fn)
    fn = click.option(
        "--vramd-priority",
        type=click.Choice(["interactive", "batch"], case_sensitive=False),
        default=None,
        help=(
            "Prioridade na fila vramd (default: interactive, ou VRAMD_PRIORITY). GameAssets batch usa 'batch'."
        ),
    )(fn)
    return fn


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


# Backend vramd → chave em ``aigamekit_shared.lowvram.FOOTPRINTS`` (ou None = YAML/heuristic).
BACKEND_FOOTPRINT_KEYS: dict[str, str] = {
    "text2d": "flux-klein-9b",
    "text2icon": "sana-sprint-600m",
    "skymap2d": "flux-dev-uint4",
    "text3d": "hunyuan3d-omni",
    "paint3d": "hunyuan-paint",
    "part3d": "hunyuan3d-part",
    "text2sound": "stable-audio-open",
    "motion3d": "hy-motion-lite",
}

# Fallbacks quando não há footprint (MiB) — alinhados a ``backends.yaml`` vram_mib.
_BACKEND_NEEDED_FALLBACK_MIB: dict[str, int] = {
    "texture2d": 2500,
    "terrain3d": 3200,
}


def legacy_server_allowed() -> bool:
    """``VRAMD_ALLOW_LEGACY_SERVER=1`` — opt-in servers per-tool / ensure_vram legacy."""
    return os.environ.get("VRAMD_ALLOW_LEGACY_SERVER", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def needed_mib_for_backend(
    backend: str,
    *,
    quant_mode: str | None = None,
    memory_efficient: bool = False,
) -> int:
    """Estima MiB para ``ensure_vram`` / ``prepare_gpu_exclusive`` (fallback in-process).

    Usa FOOTPRINTS (pesos+act). Admit vramd continua autoridade; isto só liberta
    headroom antes do load local.
    """
    from .lowvram import get_footprint

    key = BACKEND_FOOTPRINT_KEYS.get(backend)
    if key is None:
        return _BACKEND_NEEDED_FALLBACK_MIB.get(backend, 4000)

    mode = (quant_mode or "").strip().lower()
    if mode in ("", "none", "null"):
        if memory_efficient:
            if backend in ("paint3d", "text2d", "part3d", "text2icon"):
                mode = "sdnq-uint8"
            elif backend in ("text3d", "motion3d"):
                mode = "sdnq-int4"
            else:
                # skymap2d / text2sound: mem_eff = offload; footprint já reflecte quant.
                mode = "none"
        else:
            mode = "none"
    fp = get_footprint(key)
    peak_gib = fp.weights_gib(mode) + fp.activation_gib
    return max(512, int(peak_gib * 1024))


def prepare_gpu_exclusive(
    *,
    needed_mib: int = 4000,
    allow_shared: bool = False,
    kill_others: bool = True,
    allow_shared_env: str = "",
    kill_others_env: str = "",
    console: Console | None = None,
    backend: str | None = None,
    quant_mode: str | None = None,
) -> None:
    """Sequência completa de preparação de GPU para tools pesadas.

    Ordem: ``ensure_vram_available`` (pede a model servers para libertar) →
    ``kill_gpu_compute_processes_aggressive`` (SIGTERM outros processos GPU) →
    ``clear_cuda_memory`` → ``enforce_exclusive_gpu`` (gate de VRAM ocupada).

    Só para fallback **in-process** (depois de ``try_vramd_delegation`` falhar /
    ``--no-vramd``). Nunca chamar antes de enfileirar no vramd.

    Args:
        needed_mib: VRAM necessária para o model server libertar (default 4000).
        allow_shared: Permitir GPU partilhada (``--allow-shared-gpu``).
        kill_others: Matar processos GPU alheios (``--gpu-kill-others``).
        allow_shared_env: Env var que override allow_shared (ex: ``TEXT3D_ALLOW_SHARED_GPU``).
        kill_others_env: Env var que override kill_others (ex: ``TEXT3D_GPU_KILL_OTHERS``).
        console: Console Rich para output (opcional).
        backend: Nome vramd (ex: ``text3d``) para peak admit no ensure-vram.
        quant_mode: Quantização assumida no pico (ex: ``sdnq-int4``).

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
    from .vramd_client import (
        VRAMD_DO_NOT_KILL_TIP,
        ensure_vram_available,
        fetch_vramd_queue_snapshot,
        format_vramd_holding_summary,
        is_vramd_running,
        vramd_is_busy,
    )

    # Pedir aos model servers / vramd para descarregar (libertar VRAM).
    if not ensure_vram_available(needed_mib=needed_mib, backend=backend, quant_mode=quant_mode):
        raise click.ClickException(
            f"VRAM insuficiente após ensure_vram (preciso ~{needed_mib} MiB). {VRAMD_DO_NOT_KILL_TIP}"
        )

    kill = env_bool(kill_others_env, kill_others) if kill_others_env else kill_others
    allow = allow_shared or (env_bool(allow_shared_env, False) if allow_shared_env else False)

    if kill:
        snap = fetch_vramd_queue_snapshot() if is_vramd_running() else None
        # vramd up mas snapshot falhou → fail-closed (não matar às cegas).
        if is_vramd_running() and (snap is None or vramd_is_busy(snap)):
            hold = format_vramd_holding_summary(snap) if snap else "vramd ativo (snapshot indisponível)"
            if console is not None:
                from rich.panel import Panel

                console.print(
                    Panel(
                        f"[bold yellow]Kill GPU recusado — vramd tem jobs[/bold yellow]\n"
                        f"{hold}\n[dim]{VRAMD_DO_NOT_KILL_TIP}[/dim]",
                        border_style="yellow",
                    )
                )
            raise click.ClickException(f"Kill GPU recusado: vramd tem jobs na fila ({hold}). {VRAMD_DO_NOT_KILL_TIP}")
        if console is not None:
            from rich.panel import Panel

            console.print(
                Panel(
                    "[bold]Terminar processos GPU alvo[/bold]\n"
                    f"[dim]Desliga com --no-gpu-kill-others ou {kill_others_env}=0[/dim]\n"
                    f"[dim]{VRAMD_DO_NOT_KILL_TIP}[/dim]",
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


def _vramd_debug_enabled() -> bool:
    """``VRAMD_DEBUG=1`` imprime o bloco ``ums_debug`` completo (campo do protocolo do vramd)."""
    return os.environ.get("VRAMD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def format_vramd_debug_line(result: dict[str, Any]) -> str:
    """Linha compacta de debug a partir de ``ums_debug`` / campos top-level."""
    # O vramd emite o bloco com o nome histórico ``ums_debug`` (wire contract) —
    # ler também ``vramd_debug`` para compatibilidade com releases futuros.
    dbg = result.get("ums_debug") or result.get("vramd_debug") or {}
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


def _print_vramd_success(
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
    console.print(f"[bold green]\u2713[/bold green] {noun} (via vramd): [cyan]{out}[/cyan] [dim]({sz})[/dim]")
    if result.get("seed") is not None:
        console.print(f"[dim]Seed: {result.get('seed', '?')}[/dim]")
    console.print(f"[dim]vramd: {format_vramd_debug_line(result)}[/dim]")
    console.print(f"[dim]Tempo total (cliente): {elapsed:.1f}s[/dim]")
    if _vramd_debug_enabled() and result.get("ums_debug"):
        import json

        console.print(f"[dim]ums_debug: {json.dumps(result['ums_debug'], ensure_ascii=False)}[/dim]")


def _vramd_generate_stream(
    tool: str,
    payload: dict[str, Any],
    *,
    priority: str | None,
    timeout_sec: float,
    console: Console,
) -> dict[str, Any] | None:
    """Gera via vramd com ``stream: true`` e imprime eventos de fila/progresso."""
    from .vramd_client import VRAMD_SOCKET, ensure_vramd_running, resolve_vramd_priority

    if not ensure_vramd_running():
        return None
    pri = resolve_vramd_priority(priority if priority is not None else payload.get("priority"))
    req = {"cmd": "generate", "backend": tool, "stream": True, "priority": pri, **payload}
    req["priority"] = pri
    final: dict[str, Any] | None = None
    for event in send_request_stream(req, VRAMD_SOCKET, timeout_sec=timeout_sec):
        if "event" in event and "status" not in event:
            ev = event.get("event")
            if ev == "queued":
                pos = event.get("queue_position", "?")
                jid = str(event.get("job_id", ""))[:8]
                console.print(
                    f"[dim]vramd fila: pos={pos} pri={event.get('priority', pri)} "
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
                console.print(f"[dim]vramd started: {event.get('backend', tool)}{suffix}[/dim]")
            elif ev == "progress":
                pct = event.get("pct")
                msg = event.get("message") or ""
                pct_s = f"{pct:.0%}" if isinstance(pct, (int, float)) else "?"
                console.print(f"[dim]vramd progresso: {pct_s} {msg}[/dim]")
            continue
        final = event
    return final


def with_vramd_load_opts(
    payload: dict[str, Any],
    *,
    gpu_ids: list[int] | str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Copia ``payload`` e injeta kwargs de load vramd (``gpu_ids``, etc.).

    Usar antes de ``try_vramd_delegation`` / ``call_vramd`` para o BackendManager
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


def with_vramd_peak_opts(
    payload: dict[str, Any],
    *,
    backend: str,
    memory_efficient: bool | None = None,
    sdnq_preset: str | None = None,
    quant_preset: str | None = None,
    footprint_key: str | None = None,
) -> dict[str, Any]:
    """Injeta sinais de pico VRAM para admit vramd (evita assume-fp16).

    Sem ``sdnq_preset`` / ``memory_efficient``, o BackendManager assume pesos
    fp16 e recusa GPUs ~6 GB. Text2D usa ``quant_preset`` no ctor — mapeamos
    também para ``sdnq_preset`` (admit partilha a mesma tabela).

    Args:
        payload: Request vramd (mutado via cópia).
        backend: ``text2d`` | ``text3d`` | ``paint3d`` (defaults por tool).
        memory_efficient: Flag CLI / hw-auto.
        sdnq_preset: Preset explícito (``none`` / ``sdnq-int4`` / …).
        quant_preset: Alias Text2D (copiado para ``quant_preset`` + ``sdnq_preset``).
        footprint_key: Override da pegada do modelo BASE (ex: ``flux-klein-4b``
            vs ``9b``) — o admit usa a chave do descriptor (estática) sem isto.
    """
    out = dict(payload)
    if footprint_key is not None:
        out["footprint_key"] = str(footprint_key)
    if memory_efficient is not None:
        out["memory_efficient"] = bool(memory_efficient)

    def _norm(raw: str | None) -> str | None:
        if raw is None:
            return None
        s = str(raw).strip().lower()
        if s in ("", "none", "null"):
            return "none"
        return str(raw).strip()

    preset = _norm(sdnq_preset)
    qpreset = _norm(quant_preset)
    if qpreset is not None:
        out["quant_preset"] = qpreset
        if preset is None:
            preset = qpreset
    if preset is not None:
        out["sdnq_preset"] = preset
    elif out.get("memory_efficient") and "sdnq_preset" not in out:
        # Defaults honestos por backend quando mem_eff sem preset explícito.
        if backend in ("paint3d", "text2d", "part3d", "text2icon"):
            out["sdnq_preset"] = "sdnq-uint8"
        elif backend in ("text3d", "motion3d"):
            out["sdnq_preset"] = "sdnq-int4"
        elif backend == "skymap2d":
            # Pesos já uint4 no footprint; mem_eff = cpu-offload, não SDNQ extra.
            out["sdnq_preset"] = "none"
    return out


def call_vramd(
    tool: str,
    payload: dict[str, Any],
    *,
    priority: str | None = None,
    stream: bool = False,
    timeout_sec: float = 600.0,
    console: Console | None = None,
) -> dict[str, Any] | None:
    """Chamada vramd de baixo nível (sync ou stream). Não faz fallback.

    Returns:
        Dict de resposta, ou ``None`` se o vramd não estiver disponível.
    """
    if stream and console is not None:
        return _vramd_generate_stream(tool, payload, priority=priority, timeout_sec=timeout_sec, console=console)
    return delegate_to_vramd(tool, payload, timeout_sec=timeout_sec, priority=priority)


def try_vramd_delegation(
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
    """Delega uma geração no vramd se ativo. Retorna ``True`` se handled.

    Helper para CLIs: no início do comando ``generate``, chamar esta função.
    Se retornar ``True``, a geração foi feita pelo vramd — o caller deve ``return``.
    Se ``False``, fazer fallback in-process.

    ``queue_full`` e ``VRAM_INSUFFICIENT`` **não** fazem fallback (evitam OOM /
    carga paralela) — levantam ``click.ClickException``.

    Args:
        tool: Nome do backend (ex: ``text2icon``).
        payload: Parâmetros do pedido (prompt, output, steps, ...).
        t_start: Timestamp de início (``time.time()``) para calcular elapsed.
        noun: Substantivo para a mensagem de sucesso (ex: ``"Ícone"``, ``"Textura"``).
        console: Console Rich para output.
        output_key: Chave do payload que contém o path de output (default ``"output"``).
        enabled: Se ``False`` (``--no-vramd``), salta a delegação.
        priority: ``interactive`` | ``batch`` (ou None → env / default).
        stream: Se ``True``, imprime eventos de fila/progresso (``--vramd-stream``).

    Returns:
        ``True`` se o vramd handle o pedido (caller deve return);
        ``False`` se deve fazer fallback in-process (vramd down ou erro).
    """
    if not enabled:
        console.print("[dim]vramd: --no-vramd → geração in-process[/dim]")
        return False

    output = payload.get(output_key)
    if output is None:
        return False

    # Opt-in stream via env (GameAssets batch --vramd-stream → VRAMD_STREAM=1).
    if not stream:
        stream = os.environ.get("VRAMD_STREAM", "").strip().lower() in ("1", "true", "yes", "on")

    from .vramd_client import (
        VRAMD_DO_NOT_KILL_TIP,
        fetch_vramd_queue_snapshot,
        format_vramd_holding_summary,
        is_vramd_running,
        vramd_is_busy,
    )

    if is_vramd_running():
        snap = fetch_vramd_queue_snapshot()
        if vramd_is_busy(snap) and snap is not None:
            console.print(f"[dim]vramd ocupado: {format_vramd_holding_summary(snap)}[/dim]")
        if not stream:
            console.print("[dim]vramd: a enfileirar… (progresso: --vramd-stream | vramd queue)[/dim]")
    else:
        console.print("[dim]vramd: a garantir supervisor (auto-start se VRAMD_AUTO_START≠0)…[/dim]")

    result = call_vramd(
        tool,
        payload,
        priority=priority,
        stream=stream,
        timeout_sec=timeout_sec,
        console=console if stream else None,
    )
    if result is None:
        # Timeout/socket morreu com vramd ainda up → GPU pode estar a meio de generate.
        # Fallback in-process corre em paralelo e OOMa / mata o job errado.
        if is_vramd_running():
            import click

            snap = fetch_vramd_queue_snapshot()
            hold = format_vramd_holding_summary(snap) if snap else "vramd ativo (sem snapshot)"
            raise click.ClickException(
                f"vramd sem resposta (timeout/socket) com supervisor ainda ativo. "
                f"Sem fallback in-process. {hold}. {VRAMD_DO_NOT_KILL_TIP}"
            )
        console.print(
            "[yellow]vramd indisponível — fallback in-process[/yellow]\n"
            f"[dim]Arranca com: vramd start · {VRAMD_DO_NOT_KILL_TIP}[/dim]"
        )
        return False
    status = result.get("status")
    if status == "ok":
        _print_vramd_success(console, noun=noun, result=result, t_start=t_start, output_key=output_key)
        return True
    if status == "queue_full":
        raise_if_vramd_queue_full(result)
        return False  # pragma: no cover
    if status == "error":
        code = str(result.get("error_code", "?") or "?")
        hint = result.get("hint")
        err = result.get("error", "?")
        console.print(f"[dim]vramd: {format_vramd_debug_line(result)}[/dim]")
        if _vramd_debug_enabled() and result.get("ums_debug"):
            import json

            console.print(f"[dim]ums_debug: {json.dumps(result['ums_debug'], ensure_ascii=False)}[/dim]")
        # vramd ainda com jobs → não competir in-process pela mesma GPU.
        if vramd_is_busy():
            import click

            tip = hint or VRAMD_DO_NOT_KILL_TIP
            raise click.ClickException(f"vramd erro [{code}]: {err}. Sem fallback in-process (vramd ocupado). {tip}")
        console.print(f"[yellow]vramd erro [{code}]: {err} — fallback in-process[/yellow]")
        if hint:
            console.print(f"[dim]hint: {hint}[/dim]")
    return False


def raise_if_vramd_queue_full(result: dict[str, Any] | None) -> None:
    """Levanta ClickException se a resposta vramd for ``queue_full`` (para call sites legacy)."""
    if result is not None and result.get("status") == "queue_full":
        import click

        depth = result.get("queue_depth", "?")
        max_d = result.get("max_depth", "?")
        code = result.get("error_code", "QUEUE_FULL")
        hint = result.get("hint") or "Espera ou aumenta VRAMD_MAX_QUEUE_DEPTH."
        dbg = format_vramd_debug_line(result)
        raise click.ClickException(
            f"vramd [{code}] fila cheia ({depth}/{max_d}): {result.get('error', 'queue_full')}. {hint} ({dbg})"
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


def delegate_or_prepare(
    backend: str,
    *,
    payload: dict[str, Any],
    t_start: float,
    noun: str,
    console: Console,
    enabled: bool = True,
    priority: str | None = None,
    stream: bool = False,
    timeout_sec: float = 600.0,
    gpu_ids: list[int] | str | None = None,
    memory_efficient: bool | None = None,
    sdnq_preset: str | None = None,
    quant_preset: str | None = None,
    footprint_key: str | None = None,
    prepare: Callable[[], None] | None = None,
) -> bool:
    """Tenta delegar no vramd; se não, chama o fallback in-process.

    Padrão dos 9 CLIs de generate: montar o request da tool (payload),
    envolver com ``with_vramd_peak_opts(with_vramd_load_opts(...))`` e delegar via
    :func:`try_vramd_delegation`. Se o vramd não tratar, o caller prepara a GPU
    exclusiva (``prepare_gpu_exclusive``) — o ``prepare`` callback encapsula
    esse passo (com os args per-tool: quant_mode, env flags, etc.).

    Args:
        backend: Nome do backend vramd (ex: ``texture2d``).
        payload: Corpo do request da tool (sem gpu_ids/peak opts — o helper
            injeta).
        t_start, noun, console, enabled, priority, stream, timeout_sec: Ver
            :func:`try_vramd_delegation`.
        gpu_ids: IDs de GPU para load (multi-GPU).
        memory_efficient, sdnq_preset, quant_preset, footprint_key: Sinais de
            pico VRAM (ver :func:`with_vramd_peak_opts`).
        prepare: Callback do fallback in-process (ex: ``prepare_gpu_exclusive``
            com os args da tool). Chamado só se o vramd não tratar.

    Returns:
        ``True`` se o vramd tratou (o caller deve ``return``); ``False`` se o
        caller deve seguir para o fallback.
    """
    if try_vramd_delegation(
        backend,
        with_vramd_peak_opts(
            with_vramd_load_opts(payload, gpu_ids=gpu_ids),
            backend=backend,
            memory_efficient=memory_efficient,
            sdnq_preset=sdnq_preset,
            quant_preset=quant_preset,
            footprint_key=footprint_key,
        ),
        t_start=t_start,
        noun=noun,
        console=console,
        enabled=enabled,
        priority=priority,
        stream=stream,
        timeout_sec=timeout_sec,
    ):
        return True
    if prepare is not None:
        prepare()
    return False
