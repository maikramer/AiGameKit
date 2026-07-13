"""Adapter do Text2Sound — Stable Audio Open 1.0 para text-to-audio.

O ``AudioGenerator`` é um singleton (``get_instance``) com ``load()``/``unload()``.
``generate()`` retorna um ``GenerationResult`` com ``.audio`` (torch.Tensor) e
``.sample_rate``. O adapter trata o save via ``save_audio``.

Parâmetros notáveis: usa ``cfg_scale`` (não ``guidance_scale``) e ``steps``
(não ``num_inference_steps``) — alinhar com o resto do monorepo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text2sound (AudioGenerator)."""

    name = "text2sound"

    def load(self, **kwargs: Any) -> Any:
        from text2sound.generator import AudioGenerator

        gen = AudioGenerator(verbose=kwargs.get("verbose", False)) if "verbose" in kwargs else AudioGenerator()
        gen.load()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}

        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        fmt = ext if ext in ("wav", "flac", "ogg") else "ogg"

        t_start = time.perf_counter()
        result = model.generate(
            prompt=prompt,
            duration=float(request.get("duration", 30.0)),
            steps=int(request.get("steps", 100)),
            cfg_scale=float(request.get("cfg_scale", 7.0)),
            seed=request.get("seed"),
            sigma_min=float(request.get("sigma_min", 0.3)),
            sigma_max=float(request.get("sigma_max", 500.0)),
            sampler_type=request.get("sampler_type", "dpmpp-3m-sde"),
            prompt_hints=request.get("prompt_hints"),
            negative_prompt=request.get("negative_prompt"),
        )

        from text2sound.audio_processor import save_audio

        metadata = {"prompt": prompt, "duration": request.get("duration", 30.0)}
        saved = save_audio(
            audio=result.audio,
            sample_rate=result.sample_rate,
            output_path=out_path,
            fmt=fmt,
            trim=bool(request.get("trim", False)),
            metadata=metadata,
        )

        elapsed = time.perf_counter() - t_start
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
