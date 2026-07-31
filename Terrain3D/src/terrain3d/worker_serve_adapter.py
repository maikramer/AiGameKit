"""Adapter terrain3d para o modo subprocesso (``terrain3d serve --ums-worker``).

Herdam de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.terrain3d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do terrain3d (generate_terrain — procedural)."""

    name = "terrain3d"

    def load(self, **kwargs: Any) -> Any:
        from terrain3d.generator import TerrainConfig

        # A "carga" é só construir a config; a pipeline carrega/fecha por generate.
        config_fields = {
            "seed",
            "size",
            "world_size",
            "max_height",
            "device",
            "num_inference_steps",
            "dtype",
            "cache_size",
            "coarse_window",
            "offset_i",
            "offset_j",
            "prompt",
            "mode",
            "island_falloff",
            "island_noise_scale",
            "island_noise_freq",
            "smooth_iterations",
            "elevation_gamma",
            "elevation_contrast",
        }
        config_kwargs = {k: v for k, v in kwargs.items() if k in config_fields}
        return TerrainConfig(**config_kwargs)

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        output = request.get("output")
        if not output:
            return {"status": "error", "error": "output é obrigatório"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")

        # Aplicar overrides do request à config (worker quente reutiliza a config
        # do load — todos os campos editáveis têm de ser re-aplicados por job).
        for field in (
            "seed",
            "size",
            "world_size",
            "max_height",
            "num_inference_steps",
            "mode",
            "offset_i",
            "offset_j",
            "island_falloff",
            "island_noise_scale",
            "island_noise_freq",
            "smooth_iterations",
            "elevation_gamma",
            "elevation_contrast",
        ):
            if field in request:
                setattr(model, field, request[field])

        self.report_progress(request, 0.0, "started")
        t_start = time.perf_counter()

        from terrain3d.export import export_ahgt, export_heightmap, export_metadata
        from terrain3d.generator import generate_terrain

        if self.should_abort(request):
            return self.cancelled_response("cancelled before diffusion")
        self.report_progress(request, 0.2, "diffusion")

        result = generate_terrain(model)

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")
        self.report_progress(request, 0.85, "export")

        out_path = Path(output)
        if str(request.get("format", "png")).lower() == "ahgt":
            saved = export_ahgt(result.heightmap, out_path, model.world_size, model.max_height)
        else:
            saved = export_heightmap(result.heightmap, out_path, size=model.size)

        # Metadata sidecar opcional.
        metadata_path = request.get("metadata_path")
        if metadata_path:
            export_metadata(result, metadata_path)

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return self.finish_response(output=saved, seconds=elapsed)

    def unload(self, model: Any) -> None:
        # No-op: a pipeline é fechada dentro de generate_terrain.
        pass
