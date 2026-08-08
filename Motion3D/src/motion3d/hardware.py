"""Hardware profile — HY-Motion Lite/Full via ``plan_offload`` (padrão Text2D).

Escada:
- Preferir **Full** quando o planner diz que cabe (SDNQ + group/sequential offload).
- Senão **Lite**. Em ~6 GB Full ainda é candidato (encode Qwen em CPU; DiT SDNQ na GPU).
- Soft-tune: ``validation_steps``, ``duration_cap_s``, ``cfg_scale`` conforme pressão VRAM.
- Sempre ``staged_load`` (DiT ↔ text encoder um de cada vez).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled
from aigamekit_shared.lowvram import OFFLOAD_NONE, get_footprint, plan_offload

HW_AUTO_ENV = "MOTION3D_HW_AUTO"

ModelVariant = Literal["lite", "full"]

# SDNQ ladder for HY DiT (runtime quant; no pre-quant ckpt).
_ALLOW_QUANT = ("none", "sdnq-uint8", "sdnq-int4")


@dataclass(frozen=True)
class HardwareProfile(HardwareProfileBase):
    model: ModelVariant
    sdnq_preset: str | None
    memory_efficient: bool
    offload_text_encoder: bool
    allow_group_offload: bool
    staged_load: bool
    validation_steps: int
    duration_cap_s: float | None
    cfg_scale: float
    offload_mode: str
    est_peak_gib: float

    def summary(self) -> str:
        parts = [
            self.name,
            f"model={self.model}",
            f"sdnq={self.sdnq_preset or 'none'}",
            f"offload={self.offload_mode}",
            f"steps={self.validation_steps}",
        ]
        if self.duration_cap_s is not None:
            parts.append(f"dur≤{self.duration_cap_s:.1f}s")
        if self.memory_efficient:
            parts.append("mem-eff")
        if self.offload_text_encoder:
            parts.append("text-cpu")
        if self.staged_load:
            parts.append("staged")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        parts.append(f"peak~{self.est_peak_gib:.1f}GiB")
        return " | ".join(parts)


def hw_auto_enabled() -> bool:
    return _hw_auto_enabled(HW_AUTO_ENV)


def footprint_key_for(model: ModelVariant) -> str:
    return "hy-motion-full" if model == "full" else "hy-motion-lite"


def model_footprint(model: ModelVariant):
    return get_footprint(footprint_key_for(model))


def _soft_tune(plan, *, model: ModelVariant) -> tuple[int, float | None, float]:
    """validation_steps, duration_cap_s, cfg_scale from VRAM pressure."""
    usable = max(plan.usable_vram_gib, 0.1)
    pressure = plan.est_peak_gib / usable
    tight = plan.offload != OFFLOAD_NONE or usable < 8.0
    very_tight = usable < 6.5 or pressure > 0.85

    if very_tight:
        steps = 20 if model == "full" else 30
        dur_cap = 3.0 if model == "full" else 4.0
        cfg = 4.0
    elif tight:
        steps = 30 if model == "full" else 40
        dur_cap = 5.0
        cfg = 5.0
    else:
        steps = 50
        dur_cap = None
        cfg = 5.0
    return steps, dur_cap, cfg


def _pick_model_and_plan(gpus: list[tuple[int, int]]):
    """Prefer Full (Text2D-style: bigger model when planner fits), else Lite."""
    primary = max(gpus, key=lambda t: t[1])
    last_plan = None
    for model in ("full", "lite"):
        plan = plan_offload(
            [primary],
            model_footprint(model),  # type: ignore[arg-type]
            allow_multi_gpu=False,
            allow_quant=_ALLOW_QUANT,
            allow_group_offload=True,
        )
        last_plan = plan
        if plan.device == "cuda":
            return model, plan  # type: ignore[return-value]
    # CPU fallback — still Lite.
    return "lite", last_plan or plan_offload([], model_footprint("lite"))


def profile_from_specs(gpus: list[tuple[int, int]]) -> HardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável."""
    if not gpus:
        return HardwareProfile(
            name="cpu",
            device="cpu",
            gpu_ids=None,
            model="lite",
            sdnq_preset=None,
            memory_efficient=True,
            offload_text_encoder=True,
            allow_group_offload=False,
            staged_load=True,
            validation_steps=20,
            duration_cap_s=3.0,
            cfg_scale=4.0,
            offload_mode="cpu",
            est_peak_gib=0.0,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    if multi and total_gib >= 20.0:
        # Multi-GPU: Full split when budget allows (rare for HY text tower).
        plan = plan_offload(gpus, model_footprint("full"), allow_quant=_ALLOW_QUANT)
        steps, dur_cap, cfg = _soft_tune(plan, model="full")
        quant = None if plan.quant_mode == "none" else plan.quant_mode
        return HardwareProfile(
            name=name,
            device="cuda",
            gpu_ids=[idx for idx, _ in gpus],
            model="full",
            sdnq_preset=quant,
            memory_efficient=plan.memory_efficient,
            offload_text_encoder=plan.memory_efficient or plan.offload != OFFLOAD_NONE,
            allow_group_offload=plan.offload != OFFLOAD_NONE,
            staged_load=True,
            validation_steps=steps,
            duration_cap_s=dur_cap,
            cfg_scale=cfg,
            offload_mode=plan.offload,
            est_peak_gib=plan.est_peak_gib,
            total_vram_gib=round(total_gib, 1),
        )

    model, plan = _pick_model_and_plan(gpus)
    steps, dur_cap, cfg = _soft_tune(plan, model=model)
    quant = None if plan.quant_mode == "none" else plan.quant_mode
    # ~6GB always stages text on CPU — Qwen bf16 never co-resides with DiT.
    text_cpu = plan.memory_efficient or largest_gib < 20.0 or plan.offload != OFFLOAD_NONE

    return HardwareProfile(
        name=name,
        device="cuda",
        gpu_ids=None,
        model=model,  # type: ignore[arg-type]
        sdnq_preset=quant,
        memory_efficient=plan.memory_efficient or text_cpu,
        offload_text_encoder=text_cpu,
        allow_group_offload=plan.offload != OFFLOAD_NONE,
        staged_load=True,
        validation_steps=steps,
        duration_cap_s=dur_cap,
        cfg_scale=cfg,
        offload_mode=plan.offload,
        est_peak_gib=plan.est_peak_gib,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> HardwareProfile:
    return detect_profile(profile_from_specs)


def estimate_peak_mib(
    *,
    half: bool = False,
    model: ModelVariant = "lite",
    sdnq_preset: str | None = None,
    memory_efficient: bool = False,
) -> int:
    """vramd admit hint — mirrors plan_offload peak when possible."""
    del half
    # Synthetic 6GB when no real GPU in doctor — use planner with 6GiB budget.
    fake_6g = [(0, int(6 * GIB))]
    fp = model_footprint(model)
    plan = plan_offload(
        fake_6g if memory_efficient else [(0, int(24 * GIB))],
        fp,
        allow_multi_gpu=False,
        allow_quant=_ALLOW_QUANT if (sdnq_preset or memory_efficient) else ("none",),
    )
    if sdnq_preset and str(sdnq_preset).lower() not in ("", "none", "null"):
        # Re-plan forcing that quant at end of ladder.
        plan = plan_offload(
            fake_6g if memory_efficient or str(sdnq_preset).startswith("sdnq") else [(0, int(24 * GIB))],
            fp,
            allow_multi_gpu=False,
            allow_quant=("none", str(sdnq_preset)),
        )
    return max(1024, int(plan.est_peak_gib * 1024) + 512)
