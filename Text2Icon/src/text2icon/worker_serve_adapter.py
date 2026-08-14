"""Adapter text2icon para o modo subprocesso (``text2icon serve --ums-worker``).

Herdam de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.text2icon.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aigamekit_shared.diffusion_control import GenerationAborted
from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do text2icon (SanaIconGenerator)."""

    name = "text2icon"

    def load(self, **kwargs: Any) -> Any:
        from text2icon.generator import SanaIconGenerator

        # vramd: channels_last on (bench 6GB: ~-13% hot). Compile piora hot — off.
        # low_vram/cpu_offload: só se o request trouxer (CLI hw_auto) — sem re-decidir.
        load_kwargs: dict[str, Any] = {
            "verbose": kwargs.get("verbose", False),
            "channels_last": kwargs.get("channels_last", True),
            "torch_compile": kwargs.get("torch_compile", False),
        }
        # memory_efficient (peak) → ctor low_vram (cpu offload interno).
        if "low_vram" not in kwargs and kwargs.get("memory_efficient") is not None:
            load_kwargs["low_vram"] = bool(kwargs.get("memory_efficient"))
        skip = {
            "verbose",
            "low_vram",
            "memory_efficient",
            "channels_last",
            "torch_compile",
            "sdnq_preset",
            "quant_preset",
            # Perfil da tool (CLI/hw_auto) — o ctor SanaIconGenerator não o
            # aceita; o offload interno é controlado por ``low_vram`` acima.
            "cpu_offload",
        }
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in skip})
        if "low_vram" in kwargs:
            load_kwargs["low_vram"] = bool(kwargs["low_vram"])
        # Quant explícito do CLI (--quant-transformer sdnq-fp8 / hw_auto) chega
        # como sinal de peak `quant_preset`/`sdnq_preset` — mapear para o ctor
        # em vez de deixar o worker re-decidir pela VRAM (flag era ignorada).
        qp = kwargs.get("quant_preset") or kwargs.get("sdnq_preset")
        if qp and str(qp).strip().lower() not in ("", "none", "null", "auto"):
            load_kwargs["transformer_quant_preset"] = str(qp).strip()
        gen = SanaIconGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=20)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")
        t_start = time.perf_counter()
        try:
            image, metadata = model.generate(
                prompt=prompt,
                negative_prompt=request.get("negative_prompt", ""),
                guidance_scale=float(request.get("guidance", 4.5)),
                num_inference_steps=steps,
                seed=request.get("seed"),
                width=int(request.get("width", 512)),
                height=int(request.get("height", 512)),
                remove_background=bool(request.get("transparent", False)),
                should_abort=should_abort,
                on_step=on_step,
            )
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from text2icon.image_processor import save_image

        out_path = Path(output)
        self.report_progress(request, 0.95, "saving")
        saved = save_image(
            image,
            prompt=metadata.get("prompt_final", prompt),
            params=metadata,
            output_dir=out_path.parent,
            filename=out_path.name,
        )

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return self.finish_response(output=saved, seconds=elapsed, seed=metadata.get("seed"))

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
