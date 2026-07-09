"""Thin wrapper do text2icon sobre ``gamedev_shared.model_server``.

Regista o loader (cria ``SanaIconGenerator``) e o generator (chama ``gen.generate``
+ guarda a imagem) específicos do text2icon. Toda a lógica de socket, liveness,
release e coordenação de VRAM vive no Shared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.model_server import (
    ModelServer,
    is_server_running,
    send_request,
    server_socket_path,
)

TOOL_NAME = "text2icon"


def _default_socket() -> Path:
    return server_socket_path(TOOL_NAME)


def _make_loader(gen_kwargs: dict[str, Any]) -> Any:
    """Cria uma função loader que devolve um ``SanaIconGenerator`` carregado."""

    def _loader() -> Any:
        from .generator import SanaIconGenerator

        gen = SanaIconGenerator(verbose=gen_kwargs.get("verbose", False), **gen_kwargs)
        gen.warmup()
        return gen

    return _loader


def _generator(gen: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Generator: chama ``gen.generate`` e guarda a imagem em disco."""
    import time

    prompt = request.get("prompt", "")
    output = request.get("output")
    if not prompt or not output:
        return {"status": "error", "error": "prompt e output são obrigatórios"}

    t_start = time.perf_counter()
    image, metadata = gen.generate(
        prompt=prompt,
        negative_prompt=request.get("negative_prompt", ""),
        guidance_scale=float(request.get("guidance", 4.5)),
        num_inference_steps=int(request.get("steps", 20)),
        seed=request.get("seed"),
        width=int(request.get("width", 512)),
        height=int(request.get("height", 512)),
        remove_background=bool(request.get("transparent", False)),
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
    """Arranca o model server do text2icon."""
    spath = Path(socket_path) if socket_path else _default_socket()
    server = ModelServer(
        socket_path=spath,
        loader=_make_loader(gen_kwargs),
        generator=_generator,
        idle_timeout_min=idle_timeout_min,
        verbose=verbose,
        tool_name=TOOL_NAME,
    )
    server.serve_forever()


# Re-exportar funções do Shared para compatibilidade com o cli.py existente.
# O cli.py importa de ``.server`` e de ``.client``; centralizamos tudo aqui.
def is_available() -> bool:
    """Verifica se o server text2icon está acessível."""
    return is_server_running(_default_socket())


def send_generate_request(
    prompt: str,
    output: str,
    *,
    width: int = 512,
    height: int = 512,
    steps: int = 20,
    guidance: float = 4.5,
    seed: int | None = None,
    transparent: bool = False,
    negative_prompt: str = "",
) -> dict[str, Any] | None:
    """Envia um pedido de geração ao server text2icon."""
    request: dict[str, Any] = {
        "cmd": "generate",
        "prompt": prompt,
        "output": str(Path(output).resolve()),
        "width": width,
        "height": height,
        "steps": steps,
        "guidance": guidance,
        "transparent": transparent,
        "negative_prompt": negative_prompt,
    }
    if seed is not None:
        request["seed"] = seed
    return send_request(request, _default_socket())
