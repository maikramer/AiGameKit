"""Adapter texture2d para o modo subprocesso (``texture2d serve --ums-worker``).

Herdam de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.texture2d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aigamekit_shared.diffusion_control import GenerationAborted
from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do texture2d (TextureGenerator — SD1.5 + circular padding)."""

    name = "texture2d"

    def load(self, **kwargs: Any) -> Any:
        from aigamekit_shared.group_offload import apply_alloc_conf_for_request, pop_allow_group_offload
        from texture2d.generator import TextureGenerator
        from texture2d.hardware import POLICY

        # Alloc conf por-request (padrão das tools 2D): o serve pode ter
        # arrancado com o conf clássico (max_split) e o request pedir GO — o
        # torch lê o env na 1ª alocação CUDA (só substitui se ainda dorme).
        allow_go = pop_allow_group_offload(kwargs)
        apply_alloc_conf_for_request(POLICY, allow_go)

        load_kwargs: dict[str, Any] = {"verbose": kwargs.get("verbose", False)}
        # GO default ON no request (padrão 2D): só o pedido explícito False desliga.
        load_kwargs["group_offload"] = allow_go
        # Sinais de peak/admit do vramd que o ctor não aceita — sem este skip,
        # um backends.yaml calibrado (quant_mode/sdnq_preset) ou preload com
        # step_cache injectava kwargs no ctor → TypeError → backend broken.
        skip = {
            "verbose",
            "group_offload",
            "sequential_offload",
            "memory_efficient",
            "sdnq_preset",
            "quant_preset",
            "quant_mode",
            "step_cache",
            "half_precision",
            "footprint_key",
        }
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in skip})
        gen = TextureGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        from texture2d.generator import DEFAULT_GUIDANCE, DEFAULT_REFINE_STEPS, DEFAULT_RESOLUTION, DEFAULT_STEPS

        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=DEFAULT_STEPS)
        if error:
            return error

        def _opt(key: str, default: Any) -> Any:
            val = request.get(key)
            return default if val is None else val

        prompt = str(_opt("prompt", ""))
        output = request.get("output")
        t_start = time.perf_counter()

        # Parâmetros que o chamador definiu: ganham ao preset no gerador. Um
        # pedido antigo não traz a chave — o preset ganha sempre, como antes.
        declared = request.get("explicit")
        explicit = {str(name) for name in declared} if declared else None
        limit = request.get("max_isolated_detail")
        axis = request.get("axis")
        match_color = request.get("match_color")
        try:
            image, metadata = model.generate(
                prompt=prompt,
                negative_prompt=str(_opt("negative_prompt", "")),
                guidance_scale=float(_opt("guidance", DEFAULT_GUIDANCE)),
                num_inference_steps=steps,
                seed=request.get("seed"),
                width=int(_opt("width", DEFAULT_RESOLUTION)),
                height=int(_opt("height", DEFAULT_RESOLUTION)),
                preset=request.get("preset"),
                ground=str(_opt("ground", "auto")),
                seamless_mode=str(_opt("seamless_mode", "late")),
                refine_steps=int(_opt("refine_steps", DEFAULT_REFINE_STEPS)),
                vae_tiling=request.get("vae_tiling"),
                seam_heal=bool(_opt("seam_heal", True)),
                hires=bool(_opt("hires", True)),
                should_abort=should_abort,
                on_step=on_step,
                explicit=explicit,
                max_isolated_detail=None if limit is None else float(limit),
                max_banding=request.get("max_banding"),
                max_tail_ratio=request.get("max_tail_ratio"),
                axis=None if axis is None else str(axis),
                match_color=None if match_color is None else str(match_color),
                attempts=int(_opt("attempts", 1)),
            )
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from texture2d.image_processor import save_image

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
