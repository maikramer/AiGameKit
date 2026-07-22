"""Adapter paint3d para o modo subprocesso (``paint3d serve --ums-worker``).

Espelha :mod:`modelserver.adapters.paint3d` mas vive no venv da tool e **não
herda de ``modelserver.adapters.base.BackendAdapter```` (Paint3D não depende do
modelserver). Mantém o mesmo contrato ``load/generate/unload`` + helpers
``report_progress``/``should_abort``/``apply_runtime_budget`` para que
:func:`gamedev_shared.worker_serve.run_worker_loop` o possa usar.

Este módulo só é importado quando o subcomando ``serve`` corre — não afecta o
import normal do ``paint3d`` (CLI interactiva, batch, etc.).
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

# Contrato do worker_serve: qualquer classe com load/generate/unload serve.
# Helpers estáticos abaixo replicam os de BackendAdapter sem dependência do
# modelserver (a tool vive no seu próprio venv, sem o supervisor).


def report_progress(request: dict[str, Any], pct: float | None = None, msg: str | None = None) -> None:
    """Reporta progresso via ``request["_progress"]`` (callback do worker)."""
    cb = request.get("_progress")
    if callable(cb):
        with contextlib.suppress(Exception):
            cb(pct, msg)


def should_abort(request: dict[str, Any]) -> bool:
    """True se o UMS pediu cancel (``request["_abort"]``)."""
    cb = request.get("_abort")
    if not callable(cb):
        return False
    try:
        return bool(cb())
    except Exception:
        return False


def cancelled_response(reason: str = "cancelled") -> dict[str, Any]:
    """Resposta canónica de cancel cooperativo."""
    return {"status": "error", "error": reason, "error_code": "CANCELLED"}


def apply_runtime_budget(
    model: Any,
    request: dict[str, Any],
    *,
    progress_pct: float | None = None,
    **hints: Any,
) -> dict[str, Any] | None:
    """Reaplica o runtime VRAM budget do PaintBatchProcessor, se suportado.

    Idêntico a :meth:`BackendAdapter.apply_runtime_budget` mas standalone.
    """
    refresh = getattr(model, "refresh_runtime_budget", None)
    if not callable(refresh):
        return None
    try:
        try:
            budget = refresh(**hints) if hints else refresh()
        except TypeError:
            budget = refresh()
    except (RuntimeError, MemoryError):
        raise
    except Exception:
        return None
    if budget and progress_pct is not None:
        summary = ", ".join(f"{k}={v}" for k, v in budget.items() if k in ("num_chunks", "max_views", "dino_device"))
        report_progress(request, progress_pct, f"vram_budget {summary}" if summary else "vram_budget")
    return budget if isinstance(budget, dict) else None


class Adapter:
    """Adapter paint3d (PaintBatchProcessor — context manager) para subprocesso.

    Mesma lógica que :class:`modelserver.adapters.paint3d.Adapter` mas com
    helpers estáticos locais (ver acima). Corre no venv do Paint3D.
    """

    name = "paint3d"

    def load(self, **kwargs: Any) -> Any:
        from paint3d import defaults as _defaults
        from paint3d.painter import PaintBatchProcessor

        # UMS-only / peak-planning keys — PaintBatchProcessor não os aceita.
        quant = kwargs.pop("sdnq_preset", None) or kwargs.pop("quant_mode", None)
        kwargs.pop("offload", None)
        # Pós-processo é por-generate, não shape de load.
        for k in ("smooth", "smooth_passes", "upscale", "upscale_factor", "preserve_origin"):
            kwargs.pop(k, None)
        mem_eff = kwargs.pop("memory_efficient", None)
        if mem_eff is None and quant is not None:
            mem_eff = str(quant).strip().lower() not in ("none", "null", "")
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
        if should_abort(request):
            return cancelled_response("cancelled before generate")

        report_progress(request, 0.0, "started")
        t_start = time.perf_counter()

        from paint3d.utils.mesh_io import load_mesh_trimesh, save_glb

        if should_abort(request):
            return cancelled_response("cancelled before load mesh")
        report_progress(request, 0.15, "loading_mesh")
        mesh_objs = load_mesh_trimesh(mesh_path)

        if should_abort(request):
            return cancelled_response("cancelled before paint")
        budget_hints: dict[str, Any] = {}
        if request.get("max_num_view"):
            budget_hints["requested_views"] = int(request["max_num_view"])
        if request.get("view_resolution"):
            budget_hints["requested_resolution"] = int(request["view_resolution"])
        try:
            budget = apply_runtime_budget(model, request, progress_pct=0.22, **budget_hints)
        except (RuntimeError, MemoryError) as exc:
            return {
                "status": "error",
                "error": str(exc),
                "error_code": "VRAM_INSUFFICIENT",
                "hint": "Runtime budget / MeshRender sem headroom — `ums evict` ou reduz views.",
            }
        report_progress(request, 0.25, "painting")
        textured = model.paint_mesh(mesh_objs, image_path)

        if should_abort(request):
            return cancelled_response("cancelled before save")
        report_progress(request, 0.85, "saving")
        saved = save_glb(textured, output)

        # Pós-processo canónico.
        from paint3d.postprocess import apply_paint_postprocess

        if should_abort(request):
            return cancelled_response("cancelled before postprocess")
        report_progress(request, 0.92, "postprocess")
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
        report_progress(request, 1.0, "done")
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
