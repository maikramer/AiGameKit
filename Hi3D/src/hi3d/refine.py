"""Núcleo de refinamento (importa sparc3d/torch apenas quando chamado).

O CLI usa :func:`refine_mesh` (uma execução e sai). O worker do vramd usa
:func:`load_pipeline` + :func:`run_pipeline` para manter os pesos residentes
entre jobs.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .align import (
    decimate,
    export_glb,
    face_count,
    frame_of,
    load_mesh,
    mesh_vertices,
    restore_frame,
    snap_to_bounds,
    strip_debris,
)
from .paths import resolve_model_ref
from .perf import (
    STAGING_MIN_FREE_MIB,
    PerfConfig,
    apply_group_offload_staging,
    apply_phase_staging,
    configure_torch,
    staging_supported,
    use_sequential_offload,
)

ProgressFn = Callable[[float | None, str | None], None]

log = logging.getLogger(__name__)

#: Resoluções aceites pelo upstream (não há valor contínuo).
VALID_RESOLUTIONS = (512, 1024, 1536)


@dataclass
class RefineResult:
    """Resultado de um refine (paths + métricas para o stage do GameAssets)."""

    output: Path
    resolution: int
    faces: int
    coarse_faces: int
    elapsed_sec: float
    offloaded: bool
    model: str
    #: ``group`` (diffusers hooks), ``staged`` (residência por fase),
    #: ``sequential`` (accelerate) ou ``full``.
    mode: str = "sequential"
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "resolution": self.resolution,
            "faces": self.faces,
            "coarse_faces": self.coarse_faces,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "offloaded": self.offloaded,
            "mode": self.mode,
            "model": self.model,
            **self.extra,
        }


def stack_status() -> dict[str, Any]:
    """Estado da stack pesada sem a importar (``find_spec`` não executa torch)."""
    mods = ("torch", "flash_attn", "spconv", "sparc3d", "trimesh", "accelerate")
    status: dict[str, Any] = {}
    for name in mods:
        try:
            status[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            status[name] = False
    return status


def stack_ready() -> tuple[bool, str]:
    """True quando os quatro módulos do upstream estão importáveis."""
    st = stack_status()
    missing = [m for m in ("torch", "flash_attn", "spconv", "sparc3d") if not st.get(m)]
    if missing:
        return False, "faltam módulos: " + ", ".join(missing)
    return True, ""


def resolve_offload(offload: bool | None) -> bool:
    """Omitido = auto pela VRAM livre (offload em GPUs apertadas)."""
    if offload is not None:
        return bool(offload)
    from .capability import pick_gpu, query_gpus, wants_offload

    gpu = pick_gpu(query_gpus())
    return wants_offload(gpu.free_mib) if gpu is not None else True


def _free_vram_mib() -> int:
    """VRAM livre (NVML/nvidia-smi); 0 quando não há GPU visível."""
    from .capability import pick_gpu, query_gpus

    gpu = pick_gpu(query_gpus())
    return int(gpu.free_mib) if gpu is not None else 0


def _apply_pass_budget_override(log: Any = None) -> None:
    """``HI3D_PASS_BUDGET`` limita o orçamento de pontos de **todos** os degraus.

    O decode do VAE escala com os pontos da estrutura; num building de toldo
    fino (market_stall) precisa de ~5 GiB de ativação e estoura os 6 GiB mesmo
    com os pesos em group offload. Um orçamento menor (ex. 30000) reduz a
    ativação proporcionalmente. Aplicado como tecto (``min``) a toda a ladder:
    para 512 o primeiro degrau é o único e basta, mas a 1024/1536 o
    ``_adaptive_voxelize`` do passe seguinte usa ``PASS_BUDGETS[1]`` — baixar só
    o [0] não reduzia nada e o knob não tinha efeito.
    """
    raw = os.environ.get("HI3D_PASS_BUDGET", "").strip()
    if not raw:
        return
    try:
        budget = int(raw)
    except ValueError:
        if log is not None:
            log.warning("HI3D_PASS_BUDGET=%r inválido (inteiro esperado) — ignorado", raw)
        return
    if budget < 1000:
        if log is not None:
            log.warning("HI3D_PASS_BUDGET=%d abaixo de 1000 — ignorado", budget)
        return
    try:
        import sparc3d.pipelines.sparc3d_pipeline as sp
    except ImportError:
        return
    sp.PASS_BUDGETS = tuple(min(int(b), budget) for b in sp.PASS_BUDGETS)


def load_pipeline(
    *,
    model: str | None = None,
    offload: bool | None = None,
    staging: bool | None = None,
    fast_postprocess: bool = False,
    local_files_only: bool = False,
    dino_model: str | None = None,
    clip_model: str | None = None,
) -> Any:
    """Carrega o ``Sparc3DPipeline`` e escolhe a política de residência.

    ``offload`` explícito manda: ``False`` = tudo na GPU, ``True`` = offload
    sequencial do accelerate. Sem ``offload``, tenta a **residência por fase**
    (default, ~2.4x mais rápida) e cai para o offload sequencial quando a VRAM
    não chega ou o upstream não expõe as peças esperadas.
    """
    ready, why = stack_ready()
    if not ready:
        raise RuntimeError(f"stack Hi3D indisponível: {why}")
    from sparc3d import Sparc3DPipeline

    _apply_pass_budget_override(log)
    configure_torch(PerfConfig(staging=staging is not False, fast_postprocess=fast_postprocess))
    model_ref = resolve_model_ref(model)
    pipeline = Sparc3DPipeline.from_pretrained(
        model_ref,
        local_files_only=local_files_only,
        dino_model_id=dino_model,
        clip_model_id=clip_model,
    )

    mode = _apply_residency(
        pipeline,
        offload=offload,
        staging=staging,
        fast_postprocess=fast_postprocess,
    )
    pipeline._hi3d_offloaded = mode != "full"
    pipeline._hi3d_mode = mode
    pipeline._hi3d_model_ref = model_ref
    return pipeline


def _apply_residency(
    pipeline: Any,
    *,
    offload: bool | None,
    staging: bool | None,
    fast_postprocess: bool,
) -> str:
    """Resolve e instala o modo de residência; devolve ``staged|sequential|full``."""
    if offload is False:
        pipeline.to("cuda")
        return "full"
    # ``offload=True`` é o pedido explícito do offload sequencial do upstream
    # (usado como escape quando o group offload tem bugs num build): não pode
    # ser silenciosamente substituído por "group".
    # Group offload (diffusers hooks) é o automático: o transformer (1.8B) fica
    # em CPU e só um leaf está na GPU, libertando VRAM para o spconv da malha
    # grossa. É o que permite refinar buildings que estouravam os 6 GiB na
    # residência por fase. HI3D_GROUP_OFFLOAD=0 volta ao comportamento antigo.
    if offload is None and apply_group_offload_staging(pipeline, device="cuda"):
        return "group"
    if offload is True:
        pipeline.enable_sequential_cpu_offload("cuda")
        return "sequential"

    want_staging = staging
    if want_staging is None:
        want_staging = _free_vram_mib() >= STAGING_MIN_FREE_MIB and staging_supported(pipeline)
    if want_staging and apply_phase_staging(pipeline, fast_postprocess=fast_postprocess):
        return "staged"
    pipeline.enable_sequential_cpu_offload("cuda")
    return "sequential"


def _progress_bridge(
    pipeline: Any,
    emit: ProgressFn,
    should_abort: Callable[[], bool] | None,
) -> Callable[[Any], None]:
    """Traduz ``Sparc3DProgressEvent`` em ``(pct, msg)`` e honra o abort."""

    def _on_event(event: Any) -> None:
        if should_abort is not None and should_abort():
            raise _Cancelled("cancelado pelo vramd")
        passes = max(int(getattr(event, "num_passes", 1) or 1), 1)
        steps = max(int(getattr(event, "num_inference_steps", 1) or 1), 1)
        idx = int(getattr(event, "pass_index", 0) or 0)
        step = int(getattr(event, "step", 0) or 0)
        pct = 0.12 + 0.78 * (idx + step / steps) / passes
        emit(pct, f"passe {idx + 1}/{passes} — passo {step}/{steps}")

    return _on_event


class _Cancelled(RuntimeError):
    """Abort cooperativo pedido pelo supervisor."""


def run_pipeline(
    pipeline: Any,
    coarse: Any,
    image: Any,
    *,
    resolution: int,
    num_inference_steps: int,
    seed: int | None,
    emit: ProgressFn,
    should_abort: Callable[[], bool] | None = None,
) -> Any:
    """Executa o refine sobre um pipeline já carregado e devolve a malha crua."""
    pipeline.progress_callback = _progress_bridge(pipeline, emit, should_abort)

    def _invoke() -> Any:
        return pipeline(
            image,
            coarse,
            resolution=int(resolution),
            num_inference_steps=int(num_inference_steps),
            seed=seed,
        )

    try:
        return _invoke()
    except Exception as exc:
        # OOM degrada para o offload sequencial do upstream (pico ~0.9 GiB) em
        # vez de falhar o asset. O spconv/cumm levanta RuntimeError com
        # "out of memory" (não OutOfMemoryError) — sem o teste da mensagem o
        # fallback nunca corria e chapel/market_stall falhavam o refine.
        message = str(exc).lower()
        is_oom = (
            type(exc).__name__ == "OutOfMemoryError"
            or "out of memory" in message
            or "cuda failed with error 2" in message
        )
        offloaded = getattr(pipeline, "_hi3d_staged", False) or getattr(pipeline, "_hi3d_group_offload", False)
        if not is_oom or not offloaded:
            raise
        import torch

        torch.cuda.empty_cache()
        if not use_sequential_offload(pipeline):
            raise
        pipeline._hi3d_mode = "sequential"
        pipeline._hi3d_offloaded = True
        emit(0.05, "OOM no refine — a repetir com offload sequencial")
        return _invoke()


def finalize_mesh(
    mesh: Any,
    coarse: Any,
    output_path: str | Path,
    *,
    align_to_coarse: bool,
    target_faces: int,
) -> tuple[Path, int, int]:
    """Alinha ao enquadramento do coarse, decima e exporta.

    Returns:
        ``(path, faces_finais, faces_do_refine_cru)``.
    """
    if align_to_coarse:
        frame = frame_of(mesh_vertices(coarse))
        mesh = restore_frame(mesh, frame)
    raw_faces = face_count(mesh)
    if target_faces and target_faces > 0:
        mesh = decimate(mesh, int(target_faces))
        mesh = strip_debris(mesh)
    if align_to_coarse:
        # Depois do strip: lascas soltas não podem esticar a AABB.
        mesh = snap_to_bounds(mesh, coarse)
    return export_glb(mesh, output_path), face_count(mesh), raw_faces


def refine_mesh(
    coarse_path: str | Path,
    image_path: str | Path,
    output_path: str | Path,
    *,
    resolution: int = 512,
    num_inference_steps: int = 25,
    seed: int | None = 0,
    model: str | None = None,
    offload: bool | None = None,
    staging: bool | None = None,
    fast_postprocess: bool = False,
    remove_background: bool = False,
    target_faces: int = 0,
    align_to_coarse: bool = True,
    local_files_only: bool = False,
    dino_model: str | None = None,
    clip_model: str | None = None,
    progress: ProgressFn | None = None,
) -> RefineResult:
    """Refina ``coarse_path`` com a imagem de referência e escreve um GLB.

    O resultado é re-escalado/re-centrado no enquadramento do coarse mesh
    (``align_to_coarse``) e opcionalmente decimado para ``target_faces`` —
    sem isto o GLB sairia com raio 1 na origem e partiria o pipeline a jusante.
    """
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError(f"resolution deve ser um de {VALID_RESOLUTIONS}, recebido {resolution!r}")

    def _emit(pct: float | None, msg: str | None) -> None:
        if progress is not None:
            progress(pct, msg)

    started = time.time()
    coarse = load_mesh(coarse_path)
    coarse_faces = face_count(coarse)

    _emit(0.02, "a carregar modelo Hi3D")
    pipeline = load_pipeline(
        model=model,
        offload=offload,
        staging=staging,
        fast_postprocess=fast_postprocess,
        local_files_only=local_files_only,
        dino_model=dino_model,
        clip_model=clip_model,
    )
    image = _open_image(image_path, remove_background)
    _emit(0.12, f"a refinar @{resolution}³ ({num_inference_steps} passos)")
    result = run_pipeline(
        pipeline,
        coarse,
        image,
        resolution=resolution,
        num_inference_steps=num_inference_steps,
        seed=seed,
        emit=_emit,
    )

    _emit(0.92, "a alinhar ao enquadramento do coarse")
    out, faces, _raw = finalize_mesh(
        result.mesh,
        coarse,
        output_path,
        align_to_coarse=align_to_coarse,
        target_faces=target_faces,
    )
    elapsed = time.time() - started
    _emit(1.0, f"{faces} faces em {elapsed:.0f}s")
    return RefineResult(
        output=out,
        resolution=int(getattr(result, "resolution", resolution)),
        faces=faces,
        coarse_faces=coarse_faces,
        elapsed_sec=elapsed,
        offloaded=bool(getattr(pipeline, "_hi3d_offloaded", False)),
        model=str(getattr(pipeline, "_hi3d_model_ref", model or "")),
        mode=str(getattr(pipeline, "_hi3d_mode", "sequential")),
    )


def _open_image(image_path: str | Path, remove_background: bool) -> Any:
    from PIL import Image

    image = Image.open(str(image_path))
    if remove_background:
        from sparc3d.utils.image import remove_background as _rm_bg

        image = _rm_bg(image)
    return image
