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
import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: VRAM livre mínima para a residência por fase (pico medido 3.74 GiB + folga).
STAGING_MIN_FREE_MIB = 4600

#: Gate do group offload. ``HI3D_GROUP_OFFLOAD=0`` volta à residência por fase;
#: ``AIGAMEKIT_GROUP_OFFLOAD=0`` desliga em todo o monorepo.
GROUP_OFFLOAD_ENV = "HI3D_GROUP_OFFLOAD"

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


def _group_offload_enabled() -> bool:
    """Gate do group offload (tool > global), como nas outras tools."""
    value = os.environ.get(GROUP_OFFLOAD_ENV, "").strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    if value in ("1", "true", "yes", "on"):
        return True
    return os.environ.get("AIGAMEKIT_GROUP_OFFLOAD", "1") != "0"


def group_offload_supported(pipeline: Any) -> bool:
    """True quando diffusers.hooks existe e o pipeline expõe as peças."""
    try:
        from diffusers.hooks import apply_group_offloading  # noqa: F401
    except ImportError:
        return False
    return all(hasattr(pipeline, name) for name in ("transformer_512", "vae", "conditioner", "_device"))


def _apply_group_offloading(module: Any, target: Any) -> None:
    """Leaf-level + CUDA streams: um leaf na GPU de cada vez, com prefetch."""
    import torch
    from diffusers.hooks import apply_group_offloading

    apply_group_offloading(
        module,
        onload_device=target,
        offload_device=torch.device("cpu"),
        offload_type="leaf_level",
        use_stream=True,
    )


class _StagedConditioner:
    """Move o conditioner inteiro na chamada (chamado uma vez por refine)."""

    def __init__(self, module: Any, device: Any) -> None:
        self._module = module
        self._device = device

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        import torch

        self._module.to(self._device)
        try:
            return self._module(*args, **kwargs)
        finally:
            self._module.to("cpu")
            with contextlib.suppress(RuntimeError):
                torch.cuda.empty_cache()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)


def apply_group_offload_staging(pipeline: Any, *, device: str = "cuda") -> bool:
    """Group offload do transformer (diffusers hooks) + fase para conditioner/VAE.

    O ``Sparc3DTransformer`` (1.8B, ~3.6 GB em fp16) era movido inteiro para a
    GPU na residência por fase; com o spconv da malha grosa o pico passava dos
    6 GiB e o refine falhava (era o caso do chapel/market_stall). Com group
    offload os pesos ficam em CPU e só um leaf/bloco está na GPU de cada vez
    (streams sobrepõem a transferência), libertando VRAM para a ativação.

    O conditioner é chamado uma vez por refine e continua em fase (mover o
    DINO+CLIP inteiro é mais barato que hooks leaf-level num modelo HF). O VAE
    fica em fase porque group offload em autoencoders conflitua com tiling/decode.
    """
    if getattr(pipeline, "_hi3d_group_offload", False):
        return True
    if not _group_offload_enabled() or not group_offload_supported(pipeline):
        return False

    import torch

    target = torch.device(device)
    try:
        _apply_group_offloading(pipeline.transformer_512, target)
        pipeline.transformer_512._hi3d_group_offload_applied = True
    except Exception as exc:
        # Hooks a meio deixam o módulo inconsistente e, sobretudo, ``_device``
        # não pode ficar em cuda: o fallback ``enable_sequential_cpu_offload``
        # do upstream exige todos os componentes em CPU ("enable offload while
        # all components are still on CPU") e o refine falhava no load em vez
        # de degradar.
        _clear_module_hooks(pipeline.transformer_512)
        pipeline._device = torch.device("cpu")
        log.warning("Hi3D: group offload no transformer_512 falhou (%s)", exc)
        return False
    # Só depois do transformer ficar gerido é que o dispatch passa a cuda.
    pipeline._device = target
    # VAE também em group offload: o decode a 512 com os pesos do decoder
    # inteiros na GPU estourava a seguir ao sampler (market_stall). O aviso do
    # diffusers sobre VAE/tiling é para autoencoders densos com tiling; o
    # SparconvVAE é sparse e não usa esse caminho.
    vae_grouped = False
    try:
        _apply_group_offloading(pipeline.vae, target)
        pipeline.vae._hi3d_group_offload_applied = True
        vae_grouped = True
    except Exception as exc:
        log.warning("Hi3D: group offload no VAE falhou (%s) — decode fica em fase", exc)

    originals: dict[str, Any] = {}
    # ``__dict__`` diz o que repor no restore (None = apagar o override e
    # voltar ao método da classe); o callable a chamar vem sempre do getattr —
    # com ``__dict__`` directo o wrapper nunca era instalado num pipeline
    # normal e o ``transformer_1024`` lazy (resolução 1024/1536) era
    # materializado inteiro na GPU a meio do refine (OOM em 6 GB).
    original_sample = getattr(pipeline, "_sample_latents", None)
    if original_sample is not None:

        def _group_sample(transformer: Any, *args: Any, **kwargs: Any) -> Any:
            if not getattr(transformer, "_hi3d_group_offload_applied", False):
                # transformer_1024 é lazy; o property materializa-o (e move-o
                # para cuda quando _device já é cuda) — devolve-se a CPU antes
                # de instalar os hooks para não duplicar pesos na GPU.
                try:
                    if next(transformer.parameters()).device.type != "cpu":
                        transformer.to("cpu")
                    _apply_group_offloading(transformer, target)
                    transformer._hi3d_group_offload_applied = True
                except Exception as exc:
                    log.warning("Hi3D: group offload no transformer materializado falhou (%s)", exc)
            return original_sample(transformer, *args, **kwargs)

        originals["_sample_latents"] = pipeline.__dict__.get("_sample_latents")
        pipeline._sample_latents = _group_sample

    if not vae_grouped:
        originals["_decode_latents"] = pipeline.__dict__.get("_decode_latents")
        pipeline._decode_latents = _staged_call(
            pipeline._decode_latents,
            lambda *a, **k: pipeline.vae,
            target,
            autocast=True,
        )
    originals["conditioner"] = pipeline.conditioner
    pipeline.conditioner = _StagedConditioner(pipeline.conditioner, target)
    pipeline._hi3d_group_offload = True
    pipeline._hi3d_group_originals = originals
    log.info("Hi3D: group offload (leaf_level, streams) no transformer; conditioner/vae por fase")
    return True


def _clear_module_hooks(module: Any) -> None:
    """Remove os hooks de group offload do HookRegistry (diffusers 0.38).

    Os hooks vivem em ``sub._diffusers_hook`` (registro por módulo) e trocam o
    ``forward``; limpar só ``_forward_pre_hooks`` (como antes) deixava o hook
    ativo e o fallback sequential combinava accelerate + diffusers no mesmo
    forward (KeyError em parâmetros meta).

    Best-effort: o caminho de falha chama isto antes de degradar para a
    residência por fase — um módulo sem ``modules()`` (stub/upstream diferente)
    não pode transformar o fallback num AttributeError.
    """
    named_modules = getattr(module, "modules", None)
    if not callable(named_modules):
        return
    try:
        subs = module.modules()
    except Exception:
        return
    for sub in subs:
        registry = getattr(sub, "_diffusers_hook", None)
        if registry is None:
            continue
        for name in ("lazy_prefetch_group_offloading", "group_offloading"):
            with contextlib.suppress(Exception):
                registry.remove_hook(name, recurse=False)


def restore_group_offload(pipeline: Any) -> bool:
    """Remove o group offload e devolve os componentes à CPU (fallback OOM)."""
    if not getattr(pipeline, "_hi3d_group_offload", False):
        return False
    originals = getattr(pipeline, "_hi3d_group_originals", {}) or {}
    for attr in ("_sample_latents", "_decode_latents", "conditioner"):
        previous = originals.get(attr)
        if previous is None:
            with contextlib.suppress(AttributeError):
                delattr(pipeline, attr)
        else:
            setattr(pipeline, attr, previous)
    for module in (
        getattr(pipeline, "transformer_512", None),
        getattr(pipeline, "_transformer_1024", None),
        getattr(pipeline, "vae", None),
    ):
        if module is not None:
            _clear_module_hooks(module)
    for module in (
        getattr(pipeline, "transformer_512", None),
        getattr(pipeline, "conditioner", None),
        getattr(pipeline, "vae", None),
    ):
        if module is not None:
            with contextlib.suppress(Exception):
                module.to("cpu")
    pipeline._hi3d_group_offload = False
    return True


def use_sequential_offload(pipeline: Any, *, device: str = "cuda") -> bool:
    """Fallback do upstream (lento mas com pico de ~0.9 GiB)."""
    import torch

    restore_phase_staging(pipeline)
    restore_group_offload(pipeline)
    try:
        # O upstream exige os componentes em CPU antes de instalar os hooks;
        # ``enable_sequential_cpu_offload`` põe ``_device`` no fim.
        pipeline._device = torch.device("cpu")
        pipeline.enable_sequential_cpu_offload(device)
        return True
    except Exception:
        return False
