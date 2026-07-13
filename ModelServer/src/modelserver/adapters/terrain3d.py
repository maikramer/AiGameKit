"""Adapter do Terrain3D — terrain-diffusion para geração de heightmaps.

O Terrain3D é procedural: ``generate_terrain(config)`` carrega+a pipeline
internamente e fecha-a no fim (``pipeline.close()`` no finally). Não há modelo
persistente para manter em VRAM entre chamadas.

O adapter trata o ``TerrainConfig`` como "model object" (na verdade é só config).
``load`` devolve a config; ``generate`` chama ``generate_terrain`` + export;
``unload`` é no-op (a pipeline já foi fechada dentro de ``generate_terrain``).

Nota: por ser procedural, o benefício de manter este backend "carregado" é nulo
em VRAM. Ainda assim é útil registá-lo para orquestração uniforme (todos os
backends GPU passam pelo mesmo protocolo).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
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

        # Aplicar overrides do request à config.
        for field in ("seed", "size", "world_size", "max_height", "num_inference_steps", "mode"):
            if field in request:
                setattr(model, field, request[field])

        t_start = time.perf_counter()

        from terrain3d.export import export_heightmap, export_metadata
        from terrain3d.generator import generate_terrain

        result = generate_terrain(model)

        out_path = Path(output)
        saved = export_heightmap(result.heightmap, out_path, size=model.size)

        # Metadata sidecar opcional.
        metadata_path = request.get("metadata_path")
        if metadata_path:
            export_metadata(result, metadata_path)

        elapsed = time.perf_counter() - t_start
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        # No-op: a pipeline é fechada dentro de generate_terrain.
        pass
