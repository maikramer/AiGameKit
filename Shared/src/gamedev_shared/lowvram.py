"""Planner unificado de low-VRAM: escada "always-fit" para correr modelos
maiores que a VRAM disponível (alvo de referência: 6 GB).

Separação de responsabilidades:

- :class:`ModelFootprint` — estimativa (GiB) do peso fp16 do modelo + overhead de
  ativação no pico, fornecida por cada ferramenta (sabe que checkpoint usa).
- :class:`LowVramPlanner` / :func:`plan_offload` — **puro** (sem torch); a partir das
  specs das GPUs decide quantização + modo de offload + VAE/attention slicing + split
  multi-GPU, numa escada determinística. Testável sem GPU.
- :func:`apply_offload_plan` — aplica o plano ao pipeline diffusers chamando as
  primitivas já existentes em :mod:`gamedev_shared.quantization` (um único sítio, em vez
  de cada gerador ter o seu if/elif).

A quantização em si (SDNQ vs ``quantization_config`` no ``from_pretrained``) continua a
cargo de cada ferramenta — o planner só **recomenda** ``quant_mode``; a ferramenta mapeia
para o seu mecanismo. Isto mantém o planner agnóstico do backend de quantização.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

GIB = 1024**3

# Fração da VRAM total considerada utilizável (resto: contexto CUDA, fragmentação,
# desktop/compositor). Conservador para não cair em OOM no limiar.
USABLE_VRAM_FRACTION = 0.90

# Fração da VRAM LIVRE considerada utilizável (quando o planner recebe 3-tuples com
# free VRAM). ligeiramente abaixo de 1.0 para margem de segurança contra flutuações.
FREE_SAFETY_FRACTION = 0.95

# Fatores de redução de peso por modo de quantização (peso_quant ~= peso_fp16 * fator).
# Aproximações práticas; o objetivo é ordenar a escada, não prever bytes exatos.
# Convenção: a quantização é feita em **runtime** a partir do modelo base (SDNQ et al.),
# não checkpoints pré-quantizados — assim seguimos as melhorias do SDNQ upstream.
QUANT_WEIGHT_FACTOR: dict[str, float] = {
    "none": 1.0,
    "fp8": 0.55,
    "fp8-layerwise": 0.55,  # diffusers.hooks layerwise casting (storage fp8, compute bf16)
    "sdnq-fp8": 0.55,
    "int8": 0.55,
    "sdnq-uint8": 0.55,
    "sdnq-int8": 0.55,
    "int4": 0.32,
    "sdnq-int4": 0.32,
}

# Ordem de preferência (qualidade desce, poupança sobe). "none" primeiro; int4 por
# último. fp8-layerwise antes de SDNQ (melhor qualidade, sem needing Triton/kernels);
# SDNQ-first para int8/int4 (uint8 é o preset mais testado; int4 só quando é preciso caber).
_QUANT_LADDER: tuple[str, ...] = ("none", "fp8-layerwise", "sdnq-uint8", "sdnq-int8", "sdnq-int4")

# Offload por ordem de agressividade. "none" = tudo na GPU.
OFFLOAD_NONE = "none"
OFFLOAD_GROUP_STREAM = "group_stream"  # group offload + CUDA streams (preferido)
OFFLOAD_MODEL = "model_cpu"  # módulos inteiros migram 1 a 1 (rápido)
OFFLOAD_SEQUENTIAL = "sequential_cpu"  # sub-módulos migram (lento, mínimo VRAM)


@dataclass(frozen=True)
class ModelFootprint:
    """Pegada de memória estimada de um modelo, em GiB.

    Args:
        fp16_weights_gib: Peso dos pesos do modelo em fp16 (sem quantização).
        activation_gib: Overhead de ativação/runtime no pico, à resolução-alvo.
            Para difusão de imagem ~1.0-2.0; para 3D/DiT pode ser maior.
        largest_module_gib: Maior sub-módulo individual (define o pico em
            ``model_cpu`` offload, onde um módulo de cada vez está na GPU). Se 0,
            estima-se como 40% dos pesos fp16.
        architecture: Nome da arquitetura (ex: ``"flux"``, ``"hunyuan3d"``) para
            ligar ao ``no_split_module_classes`` do registry multi-GPU. ``None``
            se irrelevante (offload only, sem multi-GPU).
    """

    fp16_weights_gib: float
    activation_gib: float = 1.5
    largest_module_gib: float = 0.0
    architecture: str | None = None

    def weights_gib(self, quant_mode: str) -> float:
        return self.fp16_weights_gib * QUANT_WEIGHT_FACTOR.get(quant_mode, 1.0)

    def largest_gib(self, quant_mode: str) -> float:
        base = self.largest_module_gib or (self.fp16_weights_gib * 0.4)
        return base * QUANT_WEIGHT_FACTOR.get(quant_mode, 1.0)


# Registry centralizado de pegadas por modelo/família. Cada tool consulta aqui
# em vez de inline literais dispersos. Valores calibrados das tools de produção.
# "flux-dev-uint4": fp16_weights_gib reflete o tamanho JÁ quantizado (uint4 ~7.4 GiB);
# usar com allow_quant=("none",) para não duplicar a redução.
FOOTPRINTS: dict[str, ModelFootprint] = {
    "flux-klein-4b": ModelFootprint(14.0, 1.5, 5.0, architecture="flux"),
    "flux-klein-9b": ModelFootprint(26.0, 1.5, 9.0, architecture="flux"),
    "flux-dev-uint4": ModelFootprint(7.4, 2.0, 3.0, architecture="flux"),
    "hunyuan3d-2.1-dit": ModelFootprint(6.5, 1.5, 5.0, architecture="hunyuan3d"),
    # Hunyuan3D-Omni (~3.3B): DiT + ShapeVAE + OmniEncoder/DINOv2; README ~10 GB fp16.
    "hunyuan3d-omni": ModelFootprint(10.0, 2.0, 6.0, architecture="hunyuan3d"),
    # Hunyuan3D-Part: DiT ~3.3 + conditioner ~0.9 + ShapeVAE ~0.3 + P3-SAM ~0.2 + overhead.
    "hunyuan3d-part": ModelFootprint(4.75, 1.5, 5.2, architecture="dit"),
    "hunyuan-paint": ModelFootprint(6.0, 2.0, 5.0, architecture="unet"),
    "stable-audio-open": ModelFootprint(3.5, 1.5, 2.0, architecture="stable-audio"),
    # Sana Sprint 600M transformer + Gemma 2B encoder (~7.3 GiB fp16 total).
    "sana-sprint-600m": ModelFootprint(7.3, 1.5, 3.0, architecture="sana"),
}

# Footprint genérico de fallback (modelo médio ~8 GiB) quando a chave é desconhecida.
_DEFAULT_FOOTPRINT = ModelFootprint(8.0, 1.5, 3.2)


def get_footprint(key: str) -> ModelFootprint:
    """Consulta o registry de pegadas por chave canónica.

    Args:
        key: Chave do modelo (ex: ``"flux-klein-9b"``, ``"hunyuan3d-2.1-dit"``).

    Returns:
        :class:`ModelFootprint` do registry, ou um footprint genérico de fallback
        com um warning (para que tools novas não partam se a chave não existir).
    """
    fp = FOOTPRINTS.get(key)
    if fp is not None:
        return fp
    import logging

    logging.getLogger("gamedev_shared.lowvram").warning(
        "Footprint '%s' não registry — a usar footprint genérico de fallback.", key
    )
    return _DEFAULT_FOOTPRINT


@dataclass(frozen=True)
class OffloadPlan:
    """Resultado do planner: como carregar o modelo para caber na VRAM."""

    device: str  # "cuda" | "cpu"
    quant_mode: str  # "none" | "fp8" | "sdnq-int8" | "sdnq-int4" | ...
    offload: str  # OFFLOAD_NONE | OFFLOAD_GROUP_STREAM | OFFLOAD_MODEL | OFFLOAD_SEQUENTIAL
    vae_slicing: bool
    vae_tiling: bool
    attention_slicing: bool
    multi_gpu_ids: list[int] | None
    primary_gpu: int | None
    usable_vram_gib: float
    est_peak_gib: float
    notes: tuple[str, ...] = field(default_factory=tuple)
    group_config: Any | None = None  # GroupOffloadConfig quando offload == OFFLOAD_GROUP_STREAM

    @property
    def memory_efficient(self) -> bool:
        return self.offload != OFFLOAD_NONE

    def summary(self) -> str:
        parts = [self.device]
        if self.multi_gpu_ids:
            parts.append(f"multi-gpu={self.multi_gpu_ids}")
        if self.quant_mode != "none":
            parts.append(f"quant={self.quant_mode}")
        if self.offload != OFFLOAD_NONE:
            parts.append(self.offload)
        if self.group_config is not None:
            parts.append(self.group_config.summary())
        if self.vae_tiling:
            parts.append("vae-tiling")
        if self.attention_slicing:
            parts.append("attn-slice")
        parts.append(f"pico~{self.est_peak_gib:.1f}/{self.usable_vram_gib:.1f}GiB")
        return " | ".join(parts)


def _cpu_plan(notes: tuple[str, ...]) -> OffloadPlan:
    return OffloadPlan(
        device="cpu",
        quant_mode="none",
        offload=OFFLOAD_NONE,
        vae_slicing=False,
        vae_tiling=False,
        attention_slicing=False,
        multi_gpu_ids=None,
        primary_gpu=None,
        usable_vram_gib=0.0,
        est_peak_gib=0.0,
        notes=notes,
    )


def plan_offload(
    gpu_specs: list[tuple[int, int]] | list[tuple[int, int, int]],
    footprint: ModelFootprint,
    *,
    allow_quant: tuple[str, ...] | None = None,
    allow_multi_gpu: bool = True,
    allow_group_offload: bool = True,
    force_group_offload: bool = False,
    prefer_leaf_offload: bool = False,
    target_resolution: int | None = None,
    usable_fraction: float = USABLE_VRAM_FRACTION,
) -> OffloadPlan:
    """Resolve um :class:`OffloadPlan` por escada determinística.

    Escada (single-GPU), do mais rápido/qualidade ao mais poupador:

    1. Tudo na GPU sem quantização (``pesos + ativação`` cabem no orçamento).
    2. Quantizar (fp8 → sdnq-int8 → sdnq-int4), tudo na GPU.
    3. Quantizar + **group offload com CUDA streams** (pico ≈ ativação, mas com
       overlap de streams — 2-4x mais rápido que sequential). **Preferido** assim
       que o modelo não cabe todo.
    4. Quantizar + ``model_cpu`` offload (pico ≈ maior módulo + ativação). Fallback
       para diffusers antigo / pipelines não-diffusers sem group offload.
    5. Quantizar (mais agressivo) + ``sequential_cpu`` offload + VAE tiling +
       attention slicing (pico ≈ ativação).
    6. CPU (sem GPU disponível ou nada cabe).

    Multi-GPU: se >1 GPU e a soma dos orçamentos couber com os pesos (fp16 ou
    quantizados) divididos, devolve split sem offload.

    Args:
        gpu_specs: lista ``(índice, bytes total)`` (de ``cuda_gpu_specs()``) ou
            ``(índice, bytes livres, bytes totais)`` (de ``cuda_gpu_free_specs()``).
            Com VRAM livre, o budget respeita GPUs ocupadas — mais seguro em rigs
            partilhadas.
        footprint: pegada do modelo.
        allow_quant: subconjunto/ordem de modos de quantização permitidos pela
            ferramenta. ``None`` = escada por defeito. Use p.ex. ``("none", "sdnq-int4")``
            se a ferramenta só suporta SDNQ.
        allow_multi_gpu: permitir split multi-GPU.
        force_group_offload: saltar full-GPU e ir directo a group+stream
            (reservar VRAM para ativação — octree alto / qualidade high).
        prefer_leaf_offload: forçar ``leaf_level`` (grupos mínimos) no group offload.
        usable_fraction: fração da VRAM total considerada utilizável.

    Returns:
        :class:`OffloadPlan`. Puro: nenhum acesso a torch/CUDA.
    """
    if not gpu_specs:
        return _cpu_plan(("sem GPU CUDA — execução em CPU",))

    ladder = tuple(q for q in (allow_quant or _QUANT_LADDER) if q in QUANT_WEIGHT_FACTOR)
    if not ladder:
        ladder = ("none",)

    # Budgets por GPU: aceita 2-tuple (idx, total) ou 3-tuple (idx, free, total).
    # Com free VRAM disponível, o budget = min(total * usable_fraction, free * safety)
    # — respeita GPUs ocupadas por outros processos (rigs partilhadas, desktop).
    budgets = []
    for spec in gpu_specs:
        idx = spec[0]
        total_gib = (spec[-1] / GIB) * usable_fraction
        if len(spec) == 3:
            free_gib = (spec[1] / GIB) * FREE_SAFETY_FRACTION
            budgets.append((idx, min(total_gib, free_gib)))
        else:
            budgets.append((idx, total_gib))
    budgets.sort(key=lambda t: t[1], reverse=True)
    primary, primary_budget = budgets[0]
    total_budget = sum(b for _, b in budgets)
    act = footprint.activation_gib

    # Resolução alta (≥1024px): VAE tiling + attention slicing reduzem o pico de
    # ativação mesmo em full-GPU (attention é O(n²); VAE decode é caro a 2MP+).
    high_res = target_resolution is not None and target_resolution >= 1024

    # --- Multi-GPU: split dos pesos por todas as GPUs (accelerate device_map) ---
    # force_group_offload: single-GPU com stream de pesos (não split).
    if allow_multi_gpu and len(budgets) > 1 and not force_group_offload:
        for quant in ladder:
            weights = footprint.weights_gib(quant)
            # Pesos divididos + ativação na primária têm de caber.
            if weights <= total_budget and (weights / len(budgets)) + act <= primary_budget:
                return OffloadPlan(
                    device="cuda",
                    quant_mode=quant,
                    offload=OFFLOAD_NONE,
                    vae_slicing=False,
                    vae_tiling=False,
                    attention_slicing=False,
                    multi_gpu_ids=[idx for idx, _ in budgets],
                    primary_gpu=primary,
                    usable_vram_gib=round(total_budget, 2),
                    est_peak_gib=round((weights / len(budgets)) + act, 2),
                    notes=(f"split multi-GPU x{len(budgets)}",),
                )

    # --- Single-GPU: escada quant → quant+offload ---
    # Passo 1-2: tudo na GPU, quant crescente (salvo force_group_offload).
    if not force_group_offload:
        for quant in ladder:
            peak = footprint.weights_gib(quant) + act
            if peak <= primary_budget:
                note = "full-GPU" if quant == "none" else f"full-GPU + {quant}"
                # Em resolução alta, activar VAE tiling + attention slicing mesmo em
                # full-GPU — reduzem o pico de ativação sem custo de offload.
                return OffloadPlan(
                    device="cuda",
                    quant_mode=quant,
                    offload=OFFLOAD_NONE,
                    vae_slicing=high_res,
                    vae_tiling=high_res,
                    attention_slicing=high_res,
                    multi_gpu_ids=None,
                    primary_gpu=primary,
                    usable_vram_gib=round(primary_budget, 2),
                    est_peak_gib=round(peak, 2),
                    notes=(note + (" + vae-tiling/attn-slice (high-res)" if high_res else ""),),
                )

    # Passo 3: group offload com CUDA streams (preferido a model_cpu/sequential).
    # Assim que o modelo não cabe todo na GPU, o offload preferido é group+stream:
    # mesma pegada de VRAM que sequential mas 2-4x mais rápido via overlap de streams.
    # A fórmula decide leaf_level (VRAM mínima) vs block_level (menos sync points).
    # Algumas pipelines custom (Hunyuan3D vendored) não são compatíveis com group
    # offload — allow_group_offload=False salta para model_cpu (passo 4).
    most_quant = ladder[-1]
    from .group_offload import plan_group_offload  # lazy: evita import circular

    group_cfg = (
        plan_group_offload(
            primary_budget,
            footprint,
            most_quant,
            prefer_leaf=prefer_leaf_offload,
            force=force_group_offload,
        )
        if allow_group_offload
        else None
    )
    if group_cfg is not None:
        # group offload: pico ≈ ativação (só as layers necessárias onloaded).
        leaf_note = group_cfg.offload_type
        force_note = " (force, VRAM→ativação)" if force_group_offload else ""
        return OffloadPlan(
            device="cuda",
            quant_mode=most_quant,
            offload=OFFLOAD_GROUP_STREAM,
            vae_slicing=True,
            vae_tiling=True,
            attention_slicing=True,
            multi_gpu_ids=None,
            primary_gpu=primary,
            usable_vram_gib=round(primary_budget, 2),
            est_peak_gib=round(act, 2),
            notes=(f"group {leaf_note}+streams + {most_quant} + vae-tiling/attn-slice{force_note}",),
            group_config=group_cfg,
        )

    # Passo 4: quant + model_cpu offload (pico ≈ maior módulo + ativação).
    # Fallback: diffusers antigo sem group offload, ou pipeline não-diffusers.
    peak_model = footprint.largest_gib(most_quant) + act
    if peak_model <= primary_budget:
        return OffloadPlan(
            device="cuda",
            quant_mode=most_quant,
            offload=OFFLOAD_MODEL,
            vae_slicing=True,
            vae_tiling=True,
            attention_slicing=True,
            multi_gpu_ids=None,
            primary_gpu=primary,
            usable_vram_gib=round(primary_budget, 2),
            est_peak_gib=round(peak_model, 2),
            notes=(f"model_cpu offload + {most_quant} + vae-tiling/attn-slice",),
        )

    # Passo 5: sequential offload — pico ≈ ativação (cabe em praticamente tudo).
    return OffloadPlan(
        device="cuda",
        quant_mode=most_quant,
        offload=OFFLOAD_SEQUENTIAL,
        vae_slicing=True,
        vae_tiling=True,
        attention_slicing=True,
        multi_gpu_ids=None,
        primary_gpu=primary,
        usable_vram_gib=round(primary_budget, 2),
        est_peak_gib=round(act, 2),
        notes=(f"sequential offload + {most_quant} + vae-tiling/attn-slice (lento, VRAM mínima)",),
    )


def apply_offload_plan(
    pipe: Any,
    plan: OffloadPlan,
    *,
    device: str | None = None,
    offload_modules: tuple[str, ...] | None = None,
) -> bool:
    """Aplica o offload/slicing de um :class:`OffloadPlan` a um pipeline diffusers.

    Não trata da quantização (cada ferramenta aplica ``plan.quant_mode`` no seu
    ``from_pretrained``/SDNQ) nem do split multi-GPU (delegado ao
    :class:`~gamedev_shared.multi_gpu.MultiGPUPlanner`). Trata só do passo de
    colocação na GPU + otimizações de memória de ativação.

    Args:
        pipe: pipeline diffusers.
        plan: plano resolvido por :func:`plan_offload`.
        device: device alvo (default: ``cuda:{primary_gpu}`` ou ``"cuda"``).

    Returns:
        ``True`` se aplicou group offload com sucesso; ``False`` caso contrário
        (caller pode fazer fallback manual para ``enable_model_cpu_offload``).
    """
    from gamedev_shared.group_offload import try_group_offloading
    from gamedev_shared.quantization import (
        enable_attention_optimizations,
        enable_model_cpu_offload_optimized,
        enable_vae_optimizations,
        set_memory_optimization_env,
    )

    set_memory_optimization_env()

    if plan.device == "cpu":
        if hasattr(pipe, "to"):
            pipe.to("cpu")
        return False

    target = device or (f"cuda:{plan.primary_gpu}" if plan.primary_gpu is not None else "cuda")
    applied_group = False
    applied_any = False

    if plan.offload == OFFLOAD_GROUP_STREAM:
        # Preferido: group offload + CUDA streams. Se falhar (diffusers antigo,
        # pipeline não-diffusers), cai para model_cpu como rede de segurança.
        applied_group = try_group_offloading(pipe, config=plan.group_config, modules=offload_modules)
        if not applied_group:
            if hasattr(pipe, "enable_model_cpu_offload"):
                enable_model_cpu_offload_optimized(pipe, device=target, use_sequential=False)
                applied_any = True
            else:
                logger.warning(
                    "apply_offload_plan: group_stream falhou e pipeline sem "
                    "enable_model_cpu_offload — placement pode ser no-op"
                )
        else:
            applied_any = True
            # Group offload aplica-se só ao transformer/text_encoders (VAE é excluído
            # — conflitua com tiling/decode). Colocar o VAE na GPU directamente (é
            # pequeno) para que o decode funcione sem device mismatch.
            vae = getattr(pipe, "vae", None)
            if vae is not None and hasattr(vae, "to"):
                with contextlib.suppress(Exception):
                    vae.to(target)
    elif plan.offload == OFFLOAD_MODEL:
        if hasattr(pipe, "enable_model_cpu_offload"):
            enable_model_cpu_offload_optimized(pipe, device=target, use_sequential=False)
            applied_any = True
        else:
            logger.warning("apply_offload_plan: model_cpu pedido mas pipeline sem enable_model_cpu_offload")
    elif plan.offload == OFFLOAD_SEQUENTIAL:
        if hasattr(pipe, "enable_sequential_cpu_offload") or hasattr(pipe, "enable_model_cpu_offload"):
            enable_model_cpu_offload_optimized(pipe, device=target, use_sequential=True)
            applied_any = True
        else:
            logger.warning("apply_offload_plan: sequential pedido mas pipeline sem offload hooks")
    elif plan.multi_gpu_ids is None:
        # Split multi-GPU é responsabilidade do chamador (MultiGPUPlanner); aqui só
        # colocamos o pipeline inteiro quando não há offload nem split.
        if hasattr(pipe, "to"):
            pipe.to(target)
            applied_any = True
        else:
            # Fallback custom (attrs Omni/Hunyuan): mover nn.Modules conhecidos.
            moved = False
            for attr in offload_modules or ("model", "vae", "cond_encoder", "conditioner", "transformer"):
                mod = getattr(pipe, attr, None)
                if mod is not None and hasattr(mod, "to"):
                    with contextlib.suppress(Exception):
                        mod.to(target)
                        moved = True
            if moved:
                if hasattr(pipe, "device"):
                    with contextlib.suppress(Exception):
                        pipe.device = __import__("torch").device(target)
                applied_any = True
            else:
                logger.warning("apply_offload_plan: OFFLOAD_NONE no-op — pipeline sem .to() e sem módulos conhecidos")

    if plan.vae_slicing or plan.vae_tiling:
        vae = getattr(pipe, "vae", None)
        if vae is not None:
            enable_vae_optimizations(vae, enable_slicing=plan.vae_slicing, enable_tiling=plan.vae_tiling)
    if plan.attention_slicing:
        enable_attention_optimizations(pipe, enable_slicing=True)

    if plan.device != "cpu" and plan.multi_gpu_ids is None and not applied_any and not applied_group:
        logger.warning(
            "apply_offload_plan: nenhum path de placement aplicou move/offload (plano=%s)",
            plan.summary(),
        )

    return applied_group


def apply_multi_gpu(
    pipe: Any,
    plan: OffloadPlan,
    *,
    model_attr: str | None = None,
    no_split_classes: list[str] | None = None,
    log_fn: Any | None = None,
) -> bool:
    """Aplica split multi-GPU via accelerate (engine unificada).

    Quando o ``plan`` recomenda multi-GPU (``plan.multi_gpu_ids`` set), delega ao
    :class:`~gamedev_shared.multi_gpu.MultiGPUPlanner` que usa
    ``accelerate.dispatch_model`` para dividir os pesos reais pelas GPUs. Isto
    substitui as 3 implementações manuais (``_try_multi_gpu`` 2D, bloco Text3D,
    ``_apply_paint_multi_gpu``) por uma só engine.

    Cascade: se o split falhar (accelerate indisponível, OOM, attrs inesperados),
    retorna ``False`` — o caller deve re-planeiar sem multi-GPU (fallback para
    offload).

    Args:
        pipe: pipeline ou modelo a colocar.
        plan: plano resolvido por :func:`plan_offload` com ``multi_gpu_ids`` set.
        model_attr: atributo que contém o ``nn.Module`` pesado (ex: ``"model"``
            no Hunyuan3D, ``"transformer"`` no FLUX). Se ``None``, dispatch do pipe.
        no_split_classes: classes de módulos que não devem ser partidos (ex:
            ``["FluxTransformerBlock"]``). Se ``None``, deriva do
            ``footprint.architecture`` via registry.
        log_fn: callback de logging opcional.

    Returns:
        ``True`` se o split foi aplicado com sucesso; ``False`` caso contrário.
    """
    if not plan.multi_gpu_ids:
        return False

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    try:
        from gamedev_shared.multi_gpu import MultiGPUPlanner

        builder = MultiGPUPlanner().for_model(pipe).with_gpus(plan.multi_gpu_ids)
        if model_attr:
            builder = builder.model_attr(model_attr)
        if no_split_classes:
            builder = builder.no_split(no_split_classes)
        device_plan = builder.plan()
        if device_plan.status != "multi_gpu":
            _log(f"Multi-GPU recusado pelo accelerate ({device_plan.status}) — fallback para offload")
            return False
        builder.apply()
        _log(f"Multi-GPU aplicado: {plan.multi_gpu_ids} (primary={device_plan.primary_device})")
        return True
    except Exception as exc:
        _log(f"Multi-GPU falhou ({exc}) — fallback para offload")
        return False


def place_pipeline(
    pipe: Any,
    footprint: ModelFootprint,
    gpu_specs: list[tuple[int, int]] | list[tuple[int, int, int]],
    *,
    quant_mode: str = "none",
    allow_quant: tuple[str, ...] | None = None,
    allow_multi_gpu: bool = True,
    allow_group_offload: bool = True,
    force_group_offload: bool = False,
    prefer_leaf_offload: bool = False,
    target_resolution: int | None = None,
    model_attr: str | None = None,
    no_split_classes: list[str] | None = None,
    offload_modules: tuple[str, ...] | None = None,
    on_status: Any | None = None,
) -> OffloadPlan:
    """**Entry point único** para colocar um pipeline na GPU (resolve + aplica).

    Unifica o fluxo que cada tool tinha duplicado: resolve o :class:`OffloadPlan`
    via :func:`plan_offload` (escada multi-GPU → full-GPU → quant → **group+stream**
    → model_cpu → sequential → CPU) e aplica-o — multi-GPU via accelerate
    (:func:`apply_multi_gpu`), offload via diffusers hooks (:func:`apply_offload_plan`).

    Cascade: multi-GPU e offload são mutuamente exclusivos (hooks competem). Se o
    planner recomenda multi-GPU mas o split falha em runtime, re-planeia sem
    multi-GPU (cai para offload). Devolve o plano efetivamente aplicado.

    Cada tool fornece só: ``pipe``, ``footprint`` (sabe o tamanho do checkpoint)
    e ``gpu_specs`` (de ``cuda_gpu_specs()`` ou ``cuda_gpu_free_specs()``).

    Args:
        pipe: pipeline diffusers (FluxPipeline, Hunyuan3DDiTFlowMatchingPipeline, etc.).
        footprint: pegada do modelo. Se tem ``architecture``, usa-o para resolver
            ``no_split_classes`` do registry multi-GPU automaticamente.
        gpu_specs: 2-tuple ``(idx, total)`` ou 3-tuple ``(idx, free, total)``.
        quant_mode: quantização já aplicada/a aplicar (afeta a pegada).
        allow_quant: ``("none",)`` para checkpoints pré-quantizados (FLUX uint4).
        allow_multi_gpu: permitir split multi-GPU.
        force_group_offload: saltar full-GPU → group+stream (VRAM para ativação).
        prefer_leaf_offload: forçar ``leaf_level`` (grupos mínimos).
        model_attr: attr do ``nn.Module`` pesado (ex: ``"model"`` Hunyuan3D).
            Necessário para accelerate dispatch em pipelines custom.
        no_split_classes: override das classes no-split. Se ``None``, deriva de
            ``footprint.architecture``.
        on_status: callback opcional para mensagens de fase.

    Returns:
        :class:`OffloadPlan` resolvido e **efetivamente aplicado**.
    """
    from .multi_gpu import ModelArchitectureRegistry

    # Resolver no_split_classes do footprint.architecture se não vier explícito.
    if no_split_classes is None and footprint.architecture:
        no_split_classes = ModelArchitectureRegistry().get(footprint.architecture) or None

    plan = plan_offload(
        gpu_specs,
        footprint,
        allow_quant=allow_quant,
        allow_multi_gpu=allow_multi_gpu,
        allow_group_offload=allow_group_offload,
        force_group_offload=force_group_offload,
        prefer_leaf_offload=prefer_leaf_offload,
        target_resolution=target_resolution,
    )

    if on_status:
        on_status(f"Colocação: {plan.summary()}")

    if plan.multi_gpu_ids is not None:
        # Multi-GPU via accelerate. Se falhar, cascade: re-planeia sem multi-GPU.
        if apply_multi_gpu(pipe, plan, model_attr=model_attr, no_split_classes=no_split_classes, log_fn=on_status):
            return plan
        # Cascade: re-planeia proibindo multi-GPU → cai para offload.
        plan = plan_offload(
            gpu_specs,
            footprint,
            allow_quant=allow_quant,
            allow_multi_gpu=False,
            allow_group_offload=allow_group_offload,
            force_group_offload=force_group_offload,
            prefer_leaf_offload=prefer_leaf_offload,
            target_resolution=target_resolution,
        )
        if on_status:
            on_status(f"Cascade para offload: {plan.summary()}")

    apply_offload_plan(pipe, plan, offload_modules=offload_modules)
    return plan


def apply_quant_mode(pipe: Any, quant_mode: str, *, log_fn: Any | None = None) -> bool:
    """Aplica o modo de quantização recomendado pelo planner ao pipeline.

    O planner recomenda ``plan.quant_mode``; a tool chama isto para aplicar a
    técnica correspondente antes do placement. Por agora suporta:
      - ``"fp8-layerwise"``: diffusers.hooks layerwise casting (fp8 storage).
      - Modos SDNQ (``sdnq-*``): continuam a cargo de cada tool (aplica antes do load).

    Args:
        pipe: pipeline diffusers.
        quant_mode: modo recomendado pelo planner.
        log_fn: callback de logging.

    Returns:
        True se aplicou uma técnica; False se o modo é "none" ou não suportado.
    """
    if quant_mode == "none":
        return False

    if quant_mode == "fp8-layerwise":
        from .group_offload import try_layerwise_casting

        return try_layerwise_casting(pipe, log_fn=log_fn)

    # SDNQ et al. são aplicados pela tool (quantização runtime layer-by-layer
    # durante o from_pretrained), não aqui. Retornar False = não aplicado neste helper.
    return False
