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
        from text2sound.models import MODEL_MUSIC_ID

        gen = AudioGenerator.get_instance(
            model_id=kwargs.get("model_id") or kwargs.get("model") or MODEL_MUSIC_ID,
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

        from text2sound.models import SPEC_MUSIC

        # Defaults do modelo SA3 (steps 8, cfg 1.0, pingpong) — o payload do
        # CLI/vramd traz valores explícitos na prática.
        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=SPEC_MUSIC.default_steps)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")
        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        fmt = ext if ext in ("wav", "flac", "ogg") else "ogg"

        # Seamless loop: o CLI pede geração mais longa (D + xf + 2·edge);
        # a duração do utilizador fica em "duration" (metadados/save).
        gen_duration = float(request.get("generation_duration", request.get("duration", SPEC_MUSIC.default_seconds)))

        t_start = time.perf_counter()
        try:
            # Preferir generate com hooks se o generator os aceitar.
            gen_kwargs: dict[str, Any] = {
                "prompt": prompt,
                "duration": gen_duration,
                "steps": steps,
                "cfg_scale": float(request.get("cfg_scale", SPEC_MUSIC.default_cfg)),
                "seed": request.get("seed"),
                "sigma_min": float(request.get("sigma_min", SPEC_MUSIC.default_sigma_min)),
                "sigma_max": float(request.get("sigma_max", SPEC_MUSIC.default_sigma_max)),
                "sampler_type": request.get("sampler_type", SPEC_MUSIC.default_sampler),
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
                duration=gen_duration,
                steps=steps,
                cfg_scale=float(request.get("cfg_scale", SPEC_MUSIC.default_cfg)),
                seed=request.get("seed"),
                sigma_min=float(request.get("sigma_min", SPEC_MUSIC.default_sigma_min)),
                sigma_max=float(request.get("sigma_max", SPEC_MUSIC.default_sigma_max)),
                sampler_type=request.get("sampler_type", SPEC_MUSIC.default_sampler),
                prompt_hints=request.get("prompt_hints"),
                negative_prompt=request.get("negative_prompt"),
            )

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from text2sound.audio_processor import save_audio

        def _opt(key: str, default: Any) -> Any:
            val = request.get(key)
            return default if val is None else val

        self.report_progress(request, 0.95, "saving")
        metadata: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": request.get("negative_prompt"),
            "duration": _opt("duration", 30.0),
            "steps": steps,
            "cfg_scale": float(_opt("cfg_scale", SPEC_MUSIC.default_cfg)),
            "seed": request.get("seed"),
            "seed_effective": getattr(result, "seed", request.get("seed")),
            "sampler": _opt("sampler_type", SPEC_MUSIC.default_sampler),
            "model_id": getattr(model, "model_id", None),
            "sample_rate": getattr(result, "sample_rate", None),
            "device": getattr(model, "device", None),
        }
        for extra_key in ("profile", "quality", "category", "audio_kind", "prompt_hints"):
            if request.get(extra_key) is not None:
                metadata[extra_key] = request[extra_key]
        # Espelhar a chamada in-process do CLI: o payload delegado traz todos
        # os parâmetros de pós-processamento — defaults seguros garantem que
        # clientes diretos do vramd que omitam campos opcionais não quebram.
        saved = save_audio(
            audio=result.audio,
            sample_rate=result.sample_rate,
            output_path=out_path,
            fmt=fmt,
            trim=bool(_opt("trim", False)),
            metadata=metadata,
            trim_buffer_ms=int(_opt("trim_buffer_ms", 200)),
            trim_threshold_db=float(_opt("trim_threshold_db", -60.0)),
            apply_fade=bool(_opt("apply_fade", True)),
            seamless_loop=bool(_opt("seamless_loop", False)),
            crossfade_ms=float(_opt("crossfade_ms", 500.0)),
            loop_edge_trim_s=float(_opt("loop_edge_trim_s", 0.0)),
            loop_target_seconds=request.get("loop_target_seconds"),
            crop_seconds=request.get("crop_seconds"),
            fade_out_seconds=float(_opt("fade_out_seconds", 0.06)),
            lufs_target=request.get("lufs_target"),
            high_pass_hz=request.get("high_pass_hz"),
            compressor_preset=request.get("compressor_preset"),
            compressor_enabled=request.get("compressor_enabled"),
            true_peak_db=request.get("true_peak_db"),
            headroom_db=float(_opt("headroom_db", 0.3)),
            bit_depth=int(_opt("bit_depth", 16)),
            ogg_quality=request.get("ogg_quality"),
        )

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return self.finish_response(output=saved, seconds=elapsed)

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
