"""Adapter do Text2Sound — Stable Audio Open 1.0 para text-to-audio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text2sound (AudioGenerator)."""

    name = "text2sound"

    def load(self, **kwargs: Any) -> Any:
        from text2sound.generator import AudioGenerator

        gen = AudioGenerator.get_instance(
            model_id=kwargs.get("model_id") or kwargs.get("model") or "stabilityai/stable-audio-open-1.0",
            gpu_ids=kwargs.get("gpu_ids"),
            half_precision=kwargs.get("half_precision"),
            chunked_vae=kwargs.get("chunked_vae"),
            torch_compile=kwargs.get("torch_compile"),
            torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
            channels_last=bool(kwargs.get("channels_last", False)),
        )
        if not getattr(gen, "_loaded", False):
            gen.load()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")

        steps = int(request.get("steps", 100))
        should_abort, on_step = self.abort_hooks(request, num_inference_steps=steps)
        self.report_progress(request, 0.0, "started")

        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        fmt = ext if ext in ("wav", "flac", "ogg") else "ogg"

        t_start = time.perf_counter()
        try:
            # Preferir generate com hooks se o generator os aceitar.
            gen_kwargs: dict[str, Any] = {
                "prompt": prompt,
                "duration": float(request.get("duration", 30.0)),
                "steps": steps,
                "cfg_scale": float(request.get("cfg_scale", 7.0)),
                "seed": request.get("seed"),
                "sigma_min": float(request.get("sigma_min", 0.3)),
                "sigma_max": float(request.get("sigma_max", 500.0)),
                "sampler_type": request.get("sampler_type", "dpmpp-3m-sde"),
                "prompt_hints": request.get("prompt_hints"),
                "negative_prompt": request.get("negative_prompt"),
            }
            # Passar hooks se a assinatura os aceitar.
            import inspect

            sig = inspect.signature(model.generate)
            if "should_abort" in sig.parameters:
                gen_kwargs["should_abort"] = should_abort
            if "on_step" in sig.parameters:
                gen_kwargs["on_step"] = on_step
            result = model.generate(**gen_kwargs)
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")
        except TypeError:
            # Fallback sem hooks.
            if self.should_abort(request):
                return self.cancelled_response("cancelled before generate")
            result = model.generate(
                prompt=prompt,
                duration=float(request.get("duration", 30.0)),
                steps=steps,
                cfg_scale=float(request.get("cfg_scale", 7.0)),
                seed=request.get("seed"),
                sigma_min=float(request.get("sigma_min", 0.3)),
                sigma_max=float(request.get("sigma_max", 500.0)),
                sampler_type=request.get("sampler_type", "dpmpp-3m-sde"),
                prompt_hints=request.get("prompt_hints"),
                negative_prompt=request.get("negative_prompt"),
            )

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from text2sound.audio_processor import save_audio

        self.report_progress(request, 0.95, "saving")
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
        self.report_progress(request, 1.0, "done")
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
