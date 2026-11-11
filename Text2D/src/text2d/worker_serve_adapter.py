"""Adapter text2d para o modo subprocesso (``text2d serve --ums-worker``).

Herdam de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.text2d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from aigamekit_shared.diffusion_control import GenerationAborted
from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do text2d (KleinFluxGenerator)."""

    name = "text2d"

    def load(self, **kwargs: Any) -> Any:
        from aigamekit_shared.group_offload import apply_alloc_conf_for_request
        from text2d.generator import KleinFluxGenerator
        from text2d.hardware import POLICY
        from text2d.vramd_load import map_vramd_load_kwargs

        # Alloc conf por-request (o serve pode ter arrancado com o conf
        # clássico/max_split e o request pedir GO); ``allow_group_offload``
        # viaja no request e o map_vramd_load repassa-o ao ctor.
        apply_alloc_conf_for_request(POLICY, bool(kwargs.get("allow_group_offload", True)))

        # Peak/offload: só do request (CLI hw_auto / with_vramd_peak_opts).
        load_kwargs = map_vramd_load_kwargs(kwargs)
        gen = KleinFluxGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=16)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")

        # Categoria icon: styling de app-icon no prompt (idempotente — o CLI
        # já pode ter augmentado antes de delegar ao vramd).
        if request.get("category") == "icon":
            from text2d.icons import augment_prompt_for_icon

            prompt = augment_prompt_for_icon(prompt)

        # Referências/boxes/num_images opcionais (workflow image_conditioned,
        # layout por boxes, múltiplas imagens por pedido).
        refs = request.get("refs") or None
        boxes = request.get("boxes") or None
        num_images = int(request.get("num_images") or 1)

        # W/H ausentes = auto (o pipeline deriva o tamanho da 1.ª referência).
        width = request.get("width")
        height = request.get("height")

        # Observabilidade: shape da geração (admit já usou quant; aqui diagnóstico).
        runtime_budget = {
            "width": width if width is not None else "auto",
            "height": height if height is not None else "auto",
            "steps": steps,
            "refs": len(refs) if refs else 0,
            "boxes": len(boxes) if boxes else 0,
            "memory_efficient": bool(getattr(model, "memory_efficient", False)),
            "group_offload": bool(getattr(model, "group_offload", False)),
            "quant_preset": getattr(model, "quant_preset", None),
            "model_id": getattr(model, "model_id", None),
        }

        t_start = time.perf_counter()
        try:
            image, _metadata = model.generate(
                prompt=prompt,
                height=int(height) if height is not None else None,
                width=int(width) if width is not None else None,
                num_inference_steps=steps,
                seed=request.get("seed"),
                refs=refs,
                boxes=boxes,
                num_images=num_images,
                should_abort=should_abort,
                on_step=on_step,
            )
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from text2d.generator import KleinFluxGenerator

        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        # ``save_image`` usa o format= explícito: um pedido .webp era gravado
        # como PNG (bytes PNG num ficheiro .webp). Mantém-se o default PNG só
        # para extensões desconhecidas.
        img_format = {"jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}.get(ext, "PNG")

        all_images = _metadata.get("images") or [image]

        # Ícone transparente: RGBA exige PNG/WebP (recusar JPEG antes do save)
        # e remoção de fundo via rembg (U2Net) sobre a imagem gerada.
        if request.get("transparent"):
            if img_format == "JPEG":
                return {"status": "error", "error": "--transparent requer saída .png ou .webp"}
            from text2d.bg_removal import remove_background

            all_images = [remove_background(img) for img in all_images]

        self.report_progress(request, 0.95, "saving")
        for idx, img in enumerate(all_images):
            part_path = out_path if idx == 0 else out_path.with_name(f"{out_path.stem}_{idx}{out_path.suffix}")
            KleinFluxGenerator.save_image(img, part_path, image_format=img_format)

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        # ``output`` é o path pedido (a 1.ª imagem). Devolver a última parte
        # fazia o GameAssets procurar ``foo_3.png`` em vez de ``foo.png``.
        return self.finish_response(output=out_path, seconds=elapsed, runtime_budget=runtime_budget)

    def unload(self, model: Any) -> None:
        # Contrato do WorkerAdapter: unload idempotente e à prova de exceções.
        # O unload do gerador (e o cache do allocator) têm de correr sempre —
        # mesmo que um levante, a VRAM tem de voltar ao driver antes da fase
        # seguinte (text3d/paint3d eram recusados com ~5 GiB presos).
        with contextlib.suppress(Exception):
            unload = getattr(model, "unload", None)
            if callable(unload):
                unload()
        try:
            import torch

            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        except Exception:
            pass
