"""Adapter do backend vramd `intrinsic` (modo subprocesso JSONL).

Herdam de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`.
O modelo carrega UMA vez (load) e serve muitos generates; os outputs são
ficheiros escritos pelo próprio worker (contrato UMS).
"""

from __future__ import annotations

import time
from typing import Any

from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do compphoto/Intrinsic (backend ``intrinsic``)."""

    name = "intrinsic"

    def load(self, **kwargs: Any) -> Any:
        from intrinsic.pipeline import load_models

        release = str(kwargs.get("release", "v2.1"))
        device = str(kwargs.get("device", "cuda"))
        models = load_models(release, device=device)
        return {"models": models, "device": device, "release": release}

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        from .decompose import run_decompose

        error, _steps, _should_abort, _on_step = self.begin_generate(
            request, default_steps=1, required=("image_path", "output_dir")
        )
        if error is not None:
            return error

        t0 = time.monotonic()
        paths = run_decompose(
            model["models"],
            request["image_path"],
            request["output_dir"],
        )
        elapsed = time.monotonic() - t0

        return self.finish_response(
            output=str(paths.albedo),
            seconds=elapsed,
            output_shading=str(paths.shading),
            output_specular=str(paths.specular),
        )

    def unload(self, model: Any) -> None:
        # torch liberta os pesos quando o dict perde as referências; nada
        # explícito a fazer além de derrubar a cache do modelo.
        if isinstance(model, dict):
            model.clear()
