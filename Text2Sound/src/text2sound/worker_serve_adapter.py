"""Adapter text2sound para o modo subprocesso (``text2sound serve --ums-worker``).

Herdam de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.text2sound.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aigamekit_shared.diffusion_control import GenerationAborted
from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
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

        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=100)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")
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
        # Espelhar a chamada in-process do CLI: o payload delegado traz todos
        # os parâmetros de pós-processamento — lê-los aqui garante que o
        # resultado pelo vramd é idêntico ao in-process.
        saved = save_audio(
            audio=result.audio,
            sample_rate=result.sample_rate,
            output_path=out_path,
            fmt=fmt,
            trim=bool(request.get("trim", False)),
            metadata=metadata,
            trim_buffer_ms=request.get("trim_buffer_ms", 200),
            trim_threshold_db=request.get("trim_threshold_db", -60.0),
            seamless_loop=bool(request.get("seamless_loop", False)),
            crossfade_ms=request.get("crossfade_ms"),
            loop_edge_trim_s=request.get("loop_edge_trim_s"),
            crop_seconds=request.get("crop_seconds"),
            fade_out_seconds=request.get("fade_out_seconds"),
            lufs_target=request.get("lufs_target"),
            high_pass_hz=request.get("high_pass_hz"),
            compressor_preset=request.get("compressor_preset"),
            compressor_enabled=request.get("compressor_enabled"),
            true_peak_db=request.get("true_peak_db"),
            bit_depth=request.get("bit_depth"),
            ogg_quality=request.get("ogg_quality"),
        )

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return self.finish_response(output=saved, seconds=elapsed)

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
