"""Text2Sound — metadados e resolução de modelos Hugging Face (música vs efeitos).

Modelos default: família **Stable Audio 3 Small** (difusão rectified-flow
destilada + T5Gemma) com um checkpoint dedicado por domínio:

- ``stabilityai/stable-audio-3-small-music`` — música / clips longos (variável)
- ``stabilityai/stable-audio-3-small-sfx``   — efeitos / clips curtos

Os modelos Stable Audio Open (1.0 / small) ficam como legado via alias
``open-1.0`` / ``open-small`` ou ID HF explícito.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MODEL_MUSIC_ID = "stabilityai/stable-audio-3-small-music"
MODEL_EFFECTS_ID = "stabilityai/stable-audio-3-small-sfx"

# Legado (aliases open-1.0 / open-small ou ID HF explícito)
LEGACY_MODEL_MUSIC_ID = "stabilityai/stable-audio-open-1.0"
LEGACY_MODEL_EFFECTS_ID = "stabilityai/stable-audio-open-small"

ProfileName = Literal["music", "effects"]


@dataclass(frozen=True)
class ModelSpec:
    """Parâmetros recomendados e limites por checkpoint."""

    hf_id: str
    label: str
    max_seconds: float
    default_seconds: float
    default_steps: int
    default_cfg: float
    default_sampler: str
    default_sigma_min: float
    default_sigma_max: float


# Defaults do model card SA3: steps=8, cfg=1.0, sampler "pingpong"
# (adversarial post-training → poucos steps, sem guidance). Duração é
# variável; o teto por perfil é produto (clips de jogo), não limite do modelo.
# Música: 120 s = buffer do model_config (sample_size 5 292 032 @ 44.1 kHz);
# pedir mais devolveria um clip mais curto que o pedido.
SPEC_MUSIC = ModelSpec(
    hf_id=MODEL_MUSIC_ID,
    label="Stable Audio 3 Small Music (música / clips longos)",
    max_seconds=120.0,
    default_seconds=30.0,
    default_steps=8,
    default_cfg=1.0,
    default_sampler="pingpong",
    default_sigma_min=0.3,
    default_sigma_max=500.0,
)

SPEC_EFFECTS = ModelSpec(
    hf_id=MODEL_EFFECTS_ID,
    label="Stable Audio 3 Small SFX (efeitos / clips curtos)",
    max_seconds=30.0,
    default_seconds=10.0,
    default_steps=8,
    default_cfg=1.0,
    default_sampler="pingpong",
    default_sigma_min=0.3,
    default_sigma_max=500.0,
)

# Defaults alinhados com o model card do Open 1.0 (difusão condicionada).
SPEC_LEGACY_MUSIC = ModelSpec(
    hf_id=LEGACY_MODEL_MUSIC_ID,
    label="Stable Audio Open 1.0 (música / clips longos) — legado",
    max_seconds=47.0,
    default_seconds=30.0,
    default_steps=100,
    default_cfg=7.0,
    default_sampler="dpmpp-3m-sde",
    default_sigma_min=0.3,
    default_sigma_max=500.0,
)

# Defaults do model card: steps=8, cfg=1.0. O modelo usa rf_denoiser → sample_rf; sampler euler compatível.
SPEC_LEGACY_EFFECTS = ModelSpec(
    hf_id=LEGACY_MODEL_EFFECTS_ID,
    label="Stable Audio Open Small (efeitos / clips curtos) — legado",
    max_seconds=11.0,
    default_seconds=11.0,
    default_steps=8,
    default_cfg=1.0,
    default_sampler="euler",
    default_sigma_min=0.3,
    default_sigma_max=500.0,
)

_SPECS_BY_ID: dict[str, ModelSpec] = {
    MODEL_MUSIC_ID: SPEC_MUSIC,
    MODEL_EFFECTS_ID: SPEC_EFFECTS,
    LEGACY_MODEL_MUSIC_ID: SPEC_LEGACY_MUSIC,
    LEGACY_MODEL_EFFECTS_ID: SPEC_LEGACY_EFFECTS,
}

# Aliases (minúsculos) → ID HF canónico
MODEL_ALIASES: dict[str, str] = {
    "music": MODEL_MUSIC_ID,
    "full": MODEL_MUSIC_ID,
    "effects": MODEL_EFFECTS_ID,
    "small": MODEL_EFFECTS_ID,
    "sfx": MODEL_EFFECTS_ID,
    # Legado Stable Audio Open
    "open-1.0": LEGACY_MODEL_MUSIC_ID,
    "open-1.0-music": LEGACY_MODEL_MUSIC_ID,
    "1.0": LEGACY_MODEL_MUSIC_ID,
    "open-small": LEGACY_MODEL_EFFECTS_ID,
    "open-small-sfx": LEGACY_MODEL_EFFECTS_ID,
}


def resolve_model_id(user: str | None) -> str:
    """Resolve alias ou ID HF. ``None`` ou vazio → modelo música (SA3)."""
    if user is None or not str(user).strip():
        return MODEL_MUSIC_ID
    s = str(user).strip()
    key = s.lower()
    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key]
    if "/" in s:
        return s
    raise ValueError(
        f"Modelo desconhecido: {user!r}. "
        f"Use um ID Hugging Face (ex.: {MODEL_MUSIC_ID}) ou um alias: "
        f"{', '.join(sorted(MODEL_ALIASES.keys()))}."
    )


def resolve_model_from_profile(
    profile: ProfileName,
    model_override: str | None,
) -> str:
    """Define o ID HF: ``--model`` tem prioridade; senão depende do perfil."""
    if model_override is not None and str(model_override).strip():
        return resolve_model_id(model_override)
    if profile == "effects":
        return MODEL_EFFECTS_ID
    return MODEL_MUSIC_ID


def get_spec(hf_id: str) -> ModelSpec:
    """Retorna spec conhecida ou heurística conservadora para IDs custom."""
    if hf_id in _SPECS_BY_ID:
        return _SPECS_BY_ID[hf_id]
    if "stable-audio-3-small-music" in hf_id:
        return SPEC_MUSIC
    if "stable-audio-3-small-sfx" in hf_id or "stable-audio-3-small" in hf_id:
        return SPEC_EFFECTS
    if "open-small" in hf_id or "stable-audio-open-small" in hf_id:
        return SPEC_LEGACY_EFFECTS
    # Modelo desconhecido: limites do SA3 (mais permissivos) e defaults SA3.
    return ModelSpec(
        hf_id=hf_id,
        label=f"Custom ({hf_id})",
        max_seconds=47.0,
        default_seconds=30.0,
        default_steps=SPEC_MUSIC.default_steps,
        default_cfg=SPEC_MUSIC.default_cfg,
        default_sampler=SPEC_MUSIC.default_sampler,
        default_sigma_min=SPEC_MUSIC.default_sigma_min,
        default_sigma_max=SPEC_MUSIC.default_sigma_max,
    )
