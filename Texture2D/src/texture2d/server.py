"""Model server do Texture2D — mantém o pipeline SD1.5 + circular padding carregado.

Réplica do padrão do text2icon: um servidor long-lived (Unix socket) que segura o
``TextureGenerator`` na VRAM. Invocações subsequentes do CLI detetam o servidor e
delegam automaticamente (~3-5s vs cold start).

Protocolo: JSON sobre Unix socket (uma linha de pedido, uma linha de resposta).
Comandos: ``generate``, ``release``, ``status``, ``shutdown``.

Arranque manual::

    texture2d server            # foreground; pipeline carrega no 1.º pedido
    texture2d server-status     # PID, modelo carregado, pedidos servidos
    texture2d server-stop       # graceful shutdown (liberta VRAM)

Coordenação de VRAM: outras tools pesadas chamam ``ensure_vram_available`` que
envia ``release`` a este socket — o pipeline descarrega mas o servidor continua.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.model_server import (
    ModelServer,
    get_server_status,
    is_server_running,
    send_request,
    server_socket_path,
    stop_server,
)

# Reexportados para o CLI aceder via ``server.get_server_status`` / ``server.stop_server``
# (o text2icon tem um bug latente por NÃO os reexportar — replicamos a versão correta).
__all__ = [
    "TOOL_NAME",
    "get_server_status",
    "is_available",
    "is_server_running",
    "send_generate_request",
    "server_socket_path",
    "start_server",
    "stop_server",
]

TOOL_NAME = "texture2d"


def _default_socket() -> Path:
    return server_socket_path(TOOL_NAME)


def _make_loader(gen_kwargs: dict[str, Any]) -> Any:
    """Cria uma função loader que devolve um ``TextureGenerator`` carregado."""

    def _loader() -> Any:
        from .generator import TextureGenerator

        gen = TextureGenerator(verbose=gen_kwargs.get("verbose", False), **gen_kwargs)
        gen.warmup()
        return gen

    return _loader


def _generator(gen: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Generator: chama ``gen.generate`` e guarda a imagem em disco."""
    import time

    from .generator import DEFAULT_GUIDANCE, DEFAULT_RESOLUTION, DEFAULT_STEPS

    prompt = request.get("prompt", "")
    output = request.get("output")
    if not prompt or not output:
        return {"status": "error", "error": "prompt e output são obrigatórios"}

    t_start = time.perf_counter()
    image, metadata = gen.generate(
        prompt=prompt,
        negative_prompt=request.get("negative_prompt", ""),
        guidance_scale=float(request.get("guidance", DEFAULT_GUIDANCE)),
        num_inference_steps=int(request.get("steps", DEFAULT_STEPS)),
        seed=request.get("seed"),
        width=int(request.get("width", DEFAULT_RESOLUTION)),
        height=int(request.get("height", DEFAULT_RESOLUTION)),
        preset=request.get("preset"),
        ground=request.get("ground", "auto"),
    )

    from .image_processor import save_image

    out_path = Path(output)
    saved = save_image(
        image,
        prompt=metadata.get("prompt_final", prompt),
        params=metadata,
        output_dir=out_path.parent,
        filename=out_path.name,
    )

    elapsed = time.perf_counter() - t_start
    return {
        "status": "ok",
        "output": str(saved),
        "seconds": round(elapsed, 2),
        "seed": metadata.get("seed"),
    }


def start_server(
    socket_path: Path | str | None = None,
    idle_timeout_min: int = 30,
    verbose: bool = False,
    **gen_kwargs: Any,
) -> None:
    """Arranca o model server do Texture2D."""
    spath = Path(socket_path) if socket_path else _default_socket()
    srv = ModelServer(
        socket_path=spath,
        loader=_make_loader(gen_kwargs),
        generator=_generator,
        idle_timeout_min=idle_timeout_min,
        verbose=verbose,
        tool_name=TOOL_NAME,
    )
    srv.serve_forever()


# --- Client helpers (usados pelo CLI generate para delegar) ---


def is_available() -> bool:
    """True se o model server está ativo no socket por defeito."""
    return is_server_running(_default_socket())


def send_generate_request(
    prompt: str,
    output: str,
    *,
    width: int = 512,
    height: int = 512,
    steps: int = 30,
    guidance: float = 7.0,
    seed: int | None = None,
    negative_prompt: str = "",
    preset: str | None = None,
    ground: str = "auto",
) -> dict[str, Any] | None:
    """Envia um pedido de geração ao model server ativo.

    Returns:
        Dict de resposta do servidor (``{"status": "ok", ...}``) ou ``None`` se
        o servidor não responder.
    """
    request: dict[str, Any] = {
        "cmd": "generate",
        "prompt": prompt,
        "output": output,
        "width": width,
        "height": height,
        "steps": steps,
        "guidance": guidance,
        "negative_prompt": negative_prompt,
        "preset": preset,
        "ground": ground,
    }
    if seed is not None:
        request["seed"] = seed
    return send_request(request, _default_socket())
