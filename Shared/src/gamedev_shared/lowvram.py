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

from dataclasses import dataclass, field
from typing import Any

GIB = 1024**3

# Fração da VRAM total considerada utilizável (resto: contexto CUDA, fragmentação,
# desktop/compositor). Conservador para não cair em OOM no limiar.
USABLE_VRAM_FRACTION = 0.90

# Fatores de redução de peso por modo de quantização (peso_quant ~= peso_fp16 * fator).
# Aproximações práticas; o objetivo é ordenar a escada, não prever bytes exatos.
# Convenção: a quantização é feita em **runtime** a partir do modelo base (SDNQ et al.),
# não checkpoints pré-quantizados — assim seguimos as melhorias do SDNQ upstream.
QUANT_WEIGHT_FACTOR: dict[str, float] = {
    "none": 1.0,
    "fp8": 0.55,
    "sdnq-fp8": 0.55,
    "int8": 0.55,
    "sdnq-uint8": 0.55,
    "sdnq-int8": 0.55,
    "int4": 0.32,
    "sdnq-int4": 0.32,
}

# Ordem de preferência (qualidade desce, poupança sobe). "none" primeiro; int4 por
# último. SDNQ-first: uint8 é o preset mais testado; int4 só quando é preciso caber.
_QUANT_LADDER: tuple[str, ...] = ("none", "sdnq-uint8", "sdnq-int8", "sdnq-int4")

# Offload por ordem de agressividade. "none" = tudo na GPU.
OFFLOAD_NONE = "none"
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
    """

    fp16_weights_gib: float
    activation_gib: float = 1.5
    largest_module_gib: float = 0.0

    def weights_gib(self, quant_mode: str) -> float:
        return self.fp16_weights_gib * QUANT_WEIGHT_FACTOR.get(quant_mode, 1.0)

    def largest_gib(self, quant_mode: str) -> float:
        base = self.largest_module_gib or (self.fp16_weights_gib * 0.4)
        return base * QUANT_WEIGHT_FACTOR.get(quant_mode, 1.0)


@dataclass(frozen=True)
class OffloadPlan:
    """Resultado do planner: como carregar o modelo para caber na VRAM."""

    device: str  # "cuda" | "cpu"
    quant_mode: str  # "none" | "fp8" | "sdnq-int8" | "sdnq-int4" | ...
    offload: str  # OFFLOAD_NONE | OFFLOAD_MODEL | OFFLOAD_SEQUENTIAL
    vae_slicing: bool
    vae_tiling: bool
    attention_slicing: bool
    multi_gpu_ids: list[int] | None
    primary_gpu: int | None
    usable_vram_gib: float
    est_peak_gib: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def low_vram(self) -> bool:
        return self.offload != OFFLOAD_NONE

    def summary(self) -> str:
        parts = [self.device]
        if self.multi_gpu_ids:
            parts.append(f"multi-gpu={self.multi_gpu_ids}")
        if self.quant_mode != "none":
            parts.append(f"quant={self.quant_mode}")
        if self.offload != OFFLOAD_NONE:
            parts.append(self.offload)
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
    gpu_specs: list[tuple[int, int]],
    footprint: ModelFootprint,
    *,
    allow_quant: tuple[str, ...] | None = None,
    allow_multi_gpu: bool = True,
    usable_fraction: float = USABLE_VRAM_FRACTION,
) -> OffloadPlan:
    """Resolve um :class:`OffloadPlan` por escada determinística.

    Escada (single-GPU), do mais rápido/qualidade ao mais poupador:

    1. Tudo na GPU sem quantização (``pesos + ativação`` cabem no orçamento).
    2. Quantizar (fp8 → sdnq-int8 → sdnq-int4), tudo na GPU.
    3. Quantizar + ``model_cpu`` offload (pico ≈ maior módulo + ativação).
    4. Quantizar (mais agressivo) + ``sequential_cpu`` offload + VAE tiling +
       attention slicing (pico ≈ ativação).
    5. CPU (sem GPU disponível ou nada cabe).

    Multi-GPU: se >1 GPU e a soma dos orçamentos couber com os pesos (fp16 ou
    quantizados) divididos, devolve split sem offload.

    Args:
        gpu_specs: lista ``(índice, bytes de VRAM total)`` — de ``cuda_gpu_specs()``.
        footprint: pegada do modelo.
        allow_quant: subconjunto/ordem de modos de quantização permitidos pela
            ferramenta. ``None`` = escada por defeito. Use p.ex. ``("none", "sdnq-int4")``
            se a ferramenta só suporta SDNQ.
        allow_multi_gpu: permitir split multi-GPU.
        usable_fraction: fração da VRAM total considerada utilizável.

    Returns:
        :class:`OffloadPlan`. Puro: nenhum acesso a torch/CUDA.
    """
    if not gpu_specs:
        return _cpu_plan(("sem GPU CUDA — execução em CPU",))

    ladder = tuple(q for q in (allow_quant or _QUANT_LADDER) if q in QUANT_WEIGHT_FACTOR)
    if not ladder:
        ladder = ("none",)

    budgets = [(idx, (mem / GIB) * usable_fraction) for idx, mem in gpu_specs]
    budgets.sort(key=lambda t: t[1], reverse=True)
    primary, primary_budget = budgets[0]
    total_budget = sum(b for _, b in budgets)
    act = footprint.activation_gib

    # --- Multi-GPU: split dos pesos por todas as GPUs (accelerate device_map) ---
    if allow_multi_gpu and len(budgets) > 1:
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
    # Passo 1-2: tudo na GPU, quant crescente.
    for quant in ladder:
        peak = footprint.weights_gib(quant) + act
        if peak <= primary_budget:
            note = "full-GPU" if quant == "none" else f"full-GPU + {quant}"
            return OffloadPlan(
                device="cuda",
                quant_mode=quant,
                offload=OFFLOAD_NONE,
                vae_slicing=False,
                vae_tiling=False,
                attention_slicing=False,
                multi_gpu_ids=None,
                primary_gpu=primary,
                usable_vram_gib=round(primary_budget, 2),
                est_peak_gib=round(peak, 2),
                notes=(note,),
            )

    # Passo 3: quant + model_cpu offload (pico ≈ maior módulo + ativação).
    most_quant = ladder[-1]
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

    # Passo 4: sequential offload — pico ≈ ativação (cabe em praticamente tudo).
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


def apply_offload_plan(pipe: Any, plan: OffloadPlan, *, device: str | None = None) -> None:
    """Aplica o offload/slicing de um :class:`OffloadPlan` a um pipeline diffusers.

    Não trata da quantização (cada ferramenta aplica ``plan.quant_mode`` no seu
    ``from_pretrained``/SDNQ) nem do split multi-GPU (delegado ao
    :class:`~gamedev_shared.multi_gpu.MultiGPUPlanner`). Trata só do passo de
    colocação na GPU + otimizações de memória de ativação.

    Args:
        pipe: pipeline diffusers.
        plan: plano resolvido por :func:`plan_offload`.
        device: device alvo (default: ``cuda:{primary_gpu}`` ou ``"cuda"``).
    """
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
        return

    target = device or (f"cuda:{plan.primary_gpu}" if plan.primary_gpu is not None else "cuda")

    if plan.offload == OFFLOAD_MODEL:
        enable_model_cpu_offload_optimized(pipe, device=target, use_sequential=False)
    elif plan.offload == OFFLOAD_SEQUENTIAL:
        enable_model_cpu_offload_optimized(pipe, device=target, use_sequential=True)
    elif plan.multi_gpu_ids is None and hasattr(pipe, "to"):
        # Split multi-GPU é responsabilidade do chamador (MultiGPUPlanner); aqui só
        # colocamos o pipeline inteiro quando não há offload nem split.
        pipe.to(target)

    if plan.vae_slicing or plan.vae_tiling:
        vae = getattr(pipe, "vae", None)
        if vae is not None:
            enable_vae_optimizations(vae, enable_slicing=plan.vae_slicing, enable_tiling=plan.vae_tiling)
    if plan.attention_slicing:
        enable_attention_optimizations(pipe, enable_slicing=True)
