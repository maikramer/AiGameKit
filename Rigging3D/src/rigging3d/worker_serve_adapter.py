"""Adapter rigging3d para o worker subprocesso do vramd.

``python -m rigging3d serve --ums-worker`` arranca o loop JSONL canónico
(:func:`aigamekit_shared.worker_serve.run_worker_loop`) com este adapter. O
modelo SkinTokens é carregado **uma vez** (``load``) e cada ``generate`` corre
``run_rig_inprocess`` sobre o GLB do sandbox, reutilizando os pesos em VRAM.

O contrato ``remote:`` do backend é ``input``/``output``: o cliente reescreve
os paths para o sandbox e o supervisor devolve o GLB no fim.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any

from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """SkinTokens in-process, um job de cada vez (sem bpy_server/HTTP)."""

    name = "rigging3d"

    def load(self, **_kwargs: Any) -> Any:
        from .skintokens_runner import DEFAULT_TOKENRIG_CKPT, _load_model, ensure_checkpoints

        root = ensure_checkpoints()
        # O config do TokenRig referencia o VAE por um caminho relativo
        # (``experiments/skin_vae_.../last.ckpt``): o load tem de correr com
        # cwd na raiz dos checkpoints, como faz o run_rig_inprocess.
        previous = os.getcwd()
        try:
            os.chdir(str(root))
            _load_model(str(root / DEFAULT_TOKENRIG_CKPT))
        finally:
            os.chdir(previous)
        return {"root": str(root), "ckpt": DEFAULT_TOKENRIG_CKPT}

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        missing = [field for field in ("input", "output") if not request.get(field)]
        if missing:
            return {"status": "error", "error": f"campos obrigatórios em falta: {', '.join(missing)}"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before rigging")

        from .skintokens_runner import run_rig_inprocess

        self.report_progress(request, 0.0, "rigging")
        started = time.monotonic()
        try:
            run_rig_inprocess(
                str(request["input"]),
                str(request["output"]),
                use_skeleton=bool(request.get("use_skeleton", False)),
                use_transfer=bool(request.get("use_transfer", True)),
                use_postprocess=bool(request.get("use_postprocess", False)),
                top_k=int(request.get("top_k", 5)),
                top_p=float(request.get("top_p", 0.95)),
                temperature=float(request.get("temperature", 1.0)),
                repetition_penalty=float(request.get("repetition_penalty", 2.0)),
                num_beams=int(request.get("num_beams", 10)),
                group_per_vertex=int(request.get("groups_per_vertex", 4)),
                seed=int(request.get("seed", 123)),
            )
        except Exception as exc:  # erro do modelo/GLB não pode matar o worker
            return {
                "status": "error",
                "error": f"rigging falhou: {exc}",
                "seconds": round(time.monotonic() - started, 2),
            }
        if self.should_abort(request):
            # O job já produziu o GLB; o cancel chegou tarde e é reportado como
            # cancelado na mesma (o supervisor decide o que fazer com o output).
            return self.cancelled_response("cancelled after rigging")
        self.report_progress(request, 1.0, "done")
        return self.finish_response(
            output=str(request["output"]),
            seconds=time.monotonic() - started,
        )

    def unload(self, model: Any) -> None:
        # Liberta os pesos e o allocator; o próximo ``load`` recarrega do cache.
        with contextlib.suppress(Exception):
            from . import skintokens_runner

            skintokens_runner._model = None
            skintokens_runner._tokenizer = None
            skintokens_runner._transform = None
            skintokens_runner._loaded_ckpt = None
        with contextlib.suppress(Exception):
            import torch

            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
