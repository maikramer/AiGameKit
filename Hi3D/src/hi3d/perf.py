"""Otimizações de execução do Hi3D (medidas; ver docs/findings/HI3D_REFINE_FINDINGS.md).

O upstream oferece dois modos: tudo na GPU (`.to("cuda")`, 7.7 GB — não cabe em
6 GB) ou `enable_sequential_cpu_offload` (accelerate move **cada sub-módulo** a
cada forward). Numa RTX 4050 de 6 GB o offload sequencial gasta 92 s no sampler
de 25 passos com um pico de só 238 MiB: a GPU fica quase vazia à espera de PCIe.

A alternativa medida é **residência por fase**: os quatro componentes
(conditioner, transformer, vae) cabem sozinhos na GPU, por isso movemos um de
cada vez, no início da sua fase, e devolvemos à CPU no fim. Mesmo asset, mesmos
25 passos: sampler 92 s -> 27 s, total 136 s -> 58 s (-58%), pico 3.74 GiB.

O que foi medido e rejeitado (não repetir sem bench novo):

* `torch.autocast(fp16)` sobre a residência por fase: 27.1 s vs 27.7 s (ruído) —
  o modelo já é misto fp16/fp32 e o gargalo é largura de banda/overhead.
* Pesos em fp16 (`model.half()`): rebenta em `polar_cuda` (RoPE) — não suportado.
* `torch.compile(mode="default")`: 5 passos custam 60 s (cold ~50 s) sem ganho
  hot; `reduce-overhead` falha com CUDA graphs ("input tensor deallocate during
  graph recording").
* `load_state_dict(assign=True)`: o custo do load é a **construção** do modelo
  (17.6 s de init de 1.8B params), não a cópia (0.4 s). Meta-device não serve:
  ficam 24 tensores meta fora do state_dict e `.to("cpu")` falha.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import Any

#: VRAM livre mínima para a residência por fase (pico medido 3.74 GiB + folga).
STAGING_MIN_FREE_MIB = 4600

#: Alocador: reduz fragmentação nos picos de 3.7 GB (não sobrepõe valor do utilizador).
ALLOC_CONF = "expandable_segments:True"


@dataclass(frozen=True)
class PerfConfig:
    """Liga/desliga as otimizações de execução."""

    staging: bool = True
    tf32: bool = True
    cudnn_benchmark: bool = True
    #: Salta o filtro de componentes do upstream (~2.8 s em 540k faces); nós já
    #: corremos ``strip_debris`` depois da decimação. Opt-in: muda a ordem do
    #: filtro (antes vs depois da simplificação).
    fast_postprocess: bool = False


def configure_torch(config: PerfConfig | None = None) -> PerfConfig:
    """Aplica os knobs globais do torch (idempotente; chamar antes do load).

    ``PYTORCH_CUDA_ALLOC_CONF`` tem de ser definido **antes** do primeiro
    contexto CUDA — se o utilizador já o definiu, é respeitado.
    """
    cfg = config or PerfConfig()
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", ALLOC_CONF)
    try:
        import torch

        if cfg.tf32:
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            with contextlib.suppress(RuntimeError, AttributeError):
                torch.set_float32_matmul_precision("high")
        if cfg.cudnn_benchmark:
            torch.backends.cudnn.benchmark = True
    except Exception:
        pass
    return cfg


def staging_supported(pipeline: Any) -> bool:
    """True quando o pipeline expõe as peças que a residência por fase toca."""
    return all(
        hasattr(pipeline, name) for name in ("conditioner", "vae", "_sample_latents", "_decode_latents", "_device")
    )


def _move(module: Any, device: Any) -> None:
    module.to(device)


def _staged_call(
    fn: Any,
    module_of: Any,
    device: Any,
    *,
    autocast: bool = False,
) -> Any:
    """Corre ``fn`` com o módulo na GPU e devolve-o à CPU no fim."""

    def inner(*args: Any, **kwargs: Any) -> Any:
        import torch

        module = module_of(*args, **kwargs)
        _move(module, device)
        try:
            if autocast and getattr(device, "type", str(device)) == "cuda":
                with torch.autocast("cuda", dtype=torch.float16):
                    return fn(*args, **kwargs)
            return fn(*args, **kwargs)
        finally:
            _move(module, "cpu")
            with contextlib.suppress(RuntimeError):
                torch.cuda.empty_cache()

    return inner


def apply_phase_staging(
    pipeline: Any,
    *,
    device: str = "cuda",
    fast_postprocess: bool = False,
) -> bool:
    """Instala a residência por fase (um componente na GPU de cada vez).

    Args:
        pipeline: ``Sparc3DPipeline`` carregado em CPU.
        device: dispositivo de execução.
        fast_postprocess: salta ``_filter_components`` do upstream.

    Returns:
        ``True`` quando instalado; ``False`` se o upstream não expõe as peças
        esperadas (o caller deve cair para ``enable_sequential_cpu_offload``).
    """
    if getattr(pipeline, "_hi3d_staged", False):
        return True
    if not staging_supported(pipeline):
        return False

    import torch

    target = torch.device(device)
    # ``_device`` é o flag de dispatch do upstream: ``__call__`` coloca a
    # condição nele e ``_adaptive_voxelize`` recusa CPU.
    pipeline._device = target

    originals: dict[str, Any] = {}

    # ``_sample_latents(transformer, ...)`` recebe o transformer como 1.º
    # argumento; ``_decode_latents(latents, res)`` usa ``self.vae``.
    originals["_sample_latents"] = pipeline.__dict__.get("_sample_latents")
    pipeline._sample_latents = _staged_call(
        pipeline._sample_latents,
        lambda transformer, *a, **k: transformer,
        target,
    )
    originals["_decode_latents"] = pipeline.__dict__.get("_decode_latents")
    pipeline._decode_latents = _staged_call(
        pipeline._decode_latents,
        lambda *a, **k: pipeline.vae,
        target,
        autocast=True,
    )

    conditioner = pipeline.conditioner

    class _StagedConditioner:
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            _move(conditioner, target)
            try:
                return conditioner(*args, **kwargs)
            finally:
                _move(conditioner, "cpu")
                with contextlib.suppress(RuntimeError):
                    torch.cuda.empty_cache()

        def __getattr__(self, name: str) -> Any:
            return getattr(conditioner, name)

    originals["conditioner"] = conditioner
    pipeline.conditioner = _StagedConditioner()

    if fast_postprocess:
        _install_fast_postprocess(pipeline, originals)

    pipeline._hi3d_staged = True
    pipeline._hi3d_staged_originals = originals
    return True


def _install_fast_postprocess(pipeline: Any, originals: dict[str, Any]) -> None:
    """Substitui o filtro de componentes do upstream por um no-op.

    Guardado: se o módulo não expuser ``_filter_components``, não toca em nada.
    """
    try:
        from sparc3d.sparcubes import postprocess as postprocess_mod

        original_filter = postprocess_mod._filter_components
    except (ImportError, AttributeError):
        return

    def _identity_filter(mesh: Any, _ratio: float) -> Any:
        return postprocess_mod.validate_mesh(mesh)

    originals["_filter_components"] = original_filter
    postprocess_mod._filter_components = _identity_filter


def restore_phase_staging(pipeline: Any) -> bool:
    """Remove a residência por fase e devolve os componentes à CPU."""
    if not getattr(pipeline, "_hi3d_staged", False):
        return False
    originals = getattr(pipeline, "_hi3d_staged_originals", {}) or {}
    for attr in ("_sample_latents", "_decode_latents", "conditioner"):
        previous = originals.get(attr)
        if previous is None:
            with contextlib.suppress(AttributeError):
                delattr(pipeline, attr)
        else:
            setattr(pipeline, attr, previous)
    if "_filter_components" in originals:
        with contextlib.suppress(ImportError, AttributeError):
            from sparc3d.sparcubes import postprocess as postprocess_mod

            postprocess_mod._filter_components = originals["_filter_components"]
    for module in (getattr(pipeline, "conditioner", None), getattr(pipeline, "transformer_512", None)):
        if module is not None:
            with contextlib.suppress(Exception):
                module.to("cpu")
    pipeline._hi3d_staged = False
    return True


def use_sequential_offload(pipeline: Any, *, device: str = "cuda") -> bool:
    """Fallback do upstream (lento mas com pico de ~0.9 GiB)."""
    import torch

    restore_phase_staging(pipeline)
    try:
        pipeline._device = torch.device(device)
        pipeline.enable_sequential_cpu_offload(device)
        return True
    except Exception:
        return False
