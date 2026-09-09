"""Adapter paint3d para o modo subprocesso (``paint3d serve --ums-worker``).

Herda de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver) — os helpers estáticos
(``report_progress``/``should_abort``/``cancelled_response``/``abort_hooks``/
``apply_runtime_budget``) vêm da base, sem cópias locais. Mesma lógica do
``modelserver.adapters.paint3d.Adapter`` mas vive no venv da tool.

Este módulo só é importado quando o subcomando ``serve`` corre — não afecta o
import normal do ``paint3d`` (CLI interactiva, batch, etc.).
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any

from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter paint3d (PaintBatchProcessor — context manager) para subprocesso.

    Mesma lógica que :class:`modelserver.adapters.paint3d.Adapter`. Corre no
    venv do Paint3D.
    """

    name = "paint3d"

    def load(self, **kwargs: Any) -> Any:
        import torch

        from paint3d import defaults as _defaults
        from paint3d.painter import PaintBatchProcessor, cuda_alloc_conf_for

        # O worker herda o env do supervisor vramd — que pode ter arrancado com
        # um PYTORCH_CUDA_ALLOC_CONF stale (max_split_size_mb fragmenta o
        # allocator com o churn de onloads do group offload: reserved medido
        # 1.9→5.4 GB em 2 s). Só eficaz antes da primeira alocação CUDA.
        if not torch.cuda.is_initialized():
            os.environ["PYTORCH_CUDA_ALLOC_CONF"] = cuda_alloc_conf_for(bool(kwargs.get("allow_group_offload")))

        # vramd-only / peak-planning keys — PaintBatchProcessor não os aceita.
        quant = kwargs.pop("sdnq_preset", None) or kwargs.pop("quant_mode", None)
        kwargs.pop("offload", None)
        # Pós-processo é por-generate, não shape de load.
        for k in ("smooth", "smooth_passes", "upscale", "upscale_factor", "preserve_origin"):
            kwargs.pop(k, None)
        from aigamekit_shared.vramd_load import normalize_quant_preset

        mem_eff = kwargs.pop("memory_efficient", None)
        if mem_eff is None and quant is not None:
            mem_eff = normalize_quant_preset(quant) is not None
        mem_eff = bool(mem_eff)

        if mem_eff:
            default_views = _defaults.MEMORY_EFFICIENT_MAX_VIEWS
            default_res = _defaults.MEMORY_EFFICIENT_VIEW_RESOLUTION
        else:
            default_views = _defaults.DEFAULT_PAINT_MAX_VIEWS
            default_res = _defaults.DEFAULT_PAINT_VIEW_RESOLUTION
        max_views = int(kwargs.get("max_num_view") or default_views)
        view_res = int(kwargs.get("view_resolution") or default_res)
        render_size = kwargs.get("render_size")
        texture_size = kwargs.get("texture_size")
        bake_exp = kwargs.get("bake_exp")

        proc = PaintBatchProcessor(
            max_num_view=max_views,
            view_resolution=view_res,
            render_size=int(render_size) if render_size else None,
            texture_size=int(texture_size) if texture_size else None,
            bake_exp=int(bake_exp) if bake_exp else _defaults.DEFAULT_PAINT_BAKE_EXP,
            verbose=bool(kwargs.get("verbose", False)),
            preserve_origin=False,
            memory_efficient=mem_eff,
            gpu_ids=kwargs.get("gpu_ids"),
            torch_compile=bool(kwargs.get("torch_compile", False)),
            torch_compile_mode=str(kwargs.get("torch_compile_mode", "default")),
            channels_last=bool(kwargs.get("channels_last", False)),
            allow_group_offload=bool(kwargs.get("allow_group_offload", False)),
        )
        return proc.__enter__()

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        mesh_path = request.get("mesh_path") or request.get("mesh")
        image_path = request.get("image_path") or request.get("image")
        output = request.get("output")
        if not mesh_path or not image_path or not output:
            return {"status": "error", "error": "mesh_path, image_path e output são obrigatórios"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")

        self.report_progress(request, 0.0, "started")
        t_start = time.perf_counter()

        from paint3d.utils.mesh_io import load_mesh_trimesh, save_glb

        if self.should_abort(request):
            return self.cancelled_response("cancelled before load mesh")
        self.report_progress(request, 0.15, "loading_mesh")
        mesh_objs = load_mesh_trimesh(mesh_path)

        if self.should_abort(request):
            return self.cancelled_response("cancelled before paint")
        budget_hints: dict[str, Any] = {}
        if request.get("max_num_view"):
            budget_hints["requested_views"] = int(request["max_num_view"])
        if request.get("view_resolution"):
            budget_hints["requested_resolution"] = int(request["view_resolution"])
        try:
            budget = self.apply_runtime_budget(model, request, progress_pct=0.22, **budget_hints)
        except (RuntimeError, MemoryError) as exc:
            return {
                "status": "error",
                "error": str(exc),
                "error_code": "VRAM_INSUFFICIENT",
                "hint": "Runtime budget / MeshRender sem headroom — `vramd evict` ou reduz views.",
            }
        self.report_progress(request, 0.25, "painting")

        # As fases do pipeline (xatlas, seleção de vistas, bake, inpaint) são
        # silenciosas por dentro — ligar os hooks _step do vendor ao progresso
        # do vramd para o watchdog de idle distinguir "a trabalhar" de
        # "pendurado" (o village_house demorava 10+ min em fases mudas e era
        # morto como timeout/OOM-spin).
        phase_bounds = {
            "uv_unwrap": (0.25, 0.40),
            "view_selection": (0.40, 0.45),
            "multiview_render": (0.45, 0.60),
            "enhance": (0.60, 0.65),
            "bake": (0.65, 0.80),
            "inpaint": (0.80, 0.85),
            "save": (0.85, 0.90),
        }

        def _phase_progress(phase: str, pct: float) -> None:
            lo, hi = phase_bounds.get(str(phase), (0.25, 0.90))
            self.report_progress(request, lo + (hi - lo) * max(0.0, min(1.0, pct / 100.0)), f"paint_{phase}")

        textured = model.paint_mesh(mesh_objs, image_path, step_callback=_phase_progress)

        if self.should_abort(request):
            return self.cancelled_response("cancelled before save")
        self.report_progress(request, 0.85, "saving")
        saved = save_glb(textured, output)

        # Pós-processo canónico.
        from paint3d.postprocess import apply_paint_postprocess

        if self.should_abort(request):
            return self.cancelled_response("cancelled before postprocess")
        self.report_progress(request, 0.92, "postprocess")
        post = apply_paint_postprocess(
            saved,
            mesh_path=mesh_path,
            preserve_origin=bool(request.get("preserve_origin", False)),
            smooth=bool(request.get("smooth", False)),
            smooth_passes=request.get("smooth_passes"),
            upscale=bool(request.get("upscale", False)),
            upscale_factor=request.get("upscale_factor"),
            verbose=bool(request.get("verbose", False)),
        )

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        out: dict[str, Any] = {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }
        if budget:
            out["runtime_budget"] = {**budget, "postprocess": post} if post else budget
        elif post:
            out["runtime_budget"] = {"postprocess": post}
        return out

    def unload(self, model: Any) -> None:
        exit_method = getattr(model, "__exit__", None)
        if callable(exit_method):
            with contextlib.suppress(Exception):
                model.__exit__(None, None, None)
