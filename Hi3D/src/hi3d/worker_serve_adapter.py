"""Adapter do worker persistente do vramd para o Hi3D."""

from __future__ import annotations

import gc
from typing import Any

from .align import face_count, load_mesh
from .refine import (
    _Cancelled,
    _open_image,
    finalize_mesh,
    load_pipeline,
    run_pipeline,
)


def _resolve_resolution(raw: Any) -> int:
    """``"auto"``/vazio → política de VRAM; caso contrário o inteiro pedido."""
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "auto")):
        from .capability import pick_gpu, plan_for_gpu, query_gpus

        return int(plan_for_gpu(pick_gpu(query_gpus())).resolution)
    return int(raw)


class Adapter:
    """Contrato do worker (load/generate/unload) sem herdar da Shared.

    O venv do Hi3D é 3.10 e o ``aigamekit_shared`` exige 3.13; a classe segue o
    mesmo duck-type de ``aigamekit_shared.worker_serve_adapter_base.WorkerAdapter``.
    """

    name = "hi3d"

    def vram_mib(self) -> int:
        """VRAM reservada pelo processo (0 quando o torch ainda não existe)."""
        try:
            import torch

            if not torch.cuda.is_available():
                return 0
            return int(torch.cuda.memory_reserved() // (1024 * 1024))
        except Exception:
            return 0

    def load(self, **kwargs: Any) -> Any:
        """Carrega o pipeline e devolve o model object do worker."""
        pipeline = load_pipeline(
            model=kwargs.get("model"),
            offload=kwargs.get("offload"),
            staging=kwargs.get("staging"),
            fast_postprocess=bool(kwargs.get("fast_postprocess", False)),
            local_files_only=bool(kwargs.get("local_files_only", False)),
            dino_model=kwargs.get("dino_model"),
            clip_model=kwargs.get("clip_model"),
        )
        return {"pipeline": pipeline}

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        """Refina um mesh e devolve métricas (``result`` do evento ``done``)."""
        pipeline = model["pipeline"]
        missing = [k for k in ("coarse_mesh", "image", "output") if not request.get(k)]
        if missing:
            raise ValueError("campos obrigatórios em falta: " + ", ".join(missing))

        emit = request.get("_progress")
        should_abort = request.get("_abort")
        coarse = load_mesh(request["coarse_mesh"])
        image = _open_image(request["image"], bool(request.get("remove_background", False)))
        resolution = _resolve_resolution(request.get("resolution"))
        steps = int(request.get("num_inference_steps", 25))
        seed = request.get("seed", 0)

        try:
            result = run_pipeline(
                pipeline,
                coarse,
                image,
                resolution=resolution,
                num_inference_steps=steps,
                seed=seed,
                emit=emit or (lambda pct, msg: None),
                should_abort=should_abort,
            )
        except _Cancelled:
            raise RuntimeError("cancelado") from None

        out, faces, raw_faces = finalize_mesh(
            result.mesh,
            coarse,
            request["output"],
            align_to_coarse=bool(request.get("align_to_coarse", True)),
            target_faces=int(request.get("target_faces", 0) or 0),
        )
        # ``status`` é contrato do vramd: ``Job.mark_finished`` decide
        # done/failed por ``result["status"]`` (default "error" quando ausente).
        return {
            "status": "ok",
            "output": str(out),
            "resolution": int(getattr(result, "resolution", resolution)),
            "faces": faces,
            "raw_faces": raw_faces,
            "coarse_faces": face_count(coarse),
        }

    def unload(self, model: Any) -> None:
        """Liberta pesos e cache CUDA (idempotente)."""
        try:
            pipeline = model.get("pipeline") if isinstance(model, dict) else None
            if pipeline is not None:
                del pipeline
            del model
        except Exception:
            pass
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
