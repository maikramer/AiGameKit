"""Text2Sound — metadados e resolução de modelos Hugging Face (música vs efeitos)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MODEL_MUSIC_ID = "stabilityai/stable-audio-open-1.0"
MODEL_EFFECTS_ID = "stabilityai/stable-audio-open-small"

ProfileName = Literal["music", "effects"]


@dataclass(frozen=True)
class ModelSpec:
    """Parâmetros recomendados e limites por checkpoint."""

    hf_id: str
    label: str
    max_seconds: float
    default_steps: int
    default_cfg: float
    default_sampler: str
    default_sigma_min: float
    default_sigma_max: float


# Defaults alinhados com o model card do Open 1.0 (difusão condicionada).
SPEC_MUSIC = ModelSpec(
    hf_id=MODEL_MUSIC_ID,
    label="Stable Audio Open 1.0 (música / clips longos)",
    max_seconds=47.0,
    default_steps=100,
    default_cfg=7.0,
    default_sampler="dpmpp-3m-sde",
    default_sigma_min=0.3,
    default_sigma_max=500.0,
)

# Defaults do model card: steps=8, cfg=1.0. O modelo usa rf_denoiser → sample_rf; sampler euler compatível.
SPEC_EFFECTS = ModelSpec(
    hf_id=MODEL_EFFECTS_ID,
    label="Stable Audio Open Small (efeitos / clips curtos)",
    max_seconds=11.0,
    default_steps=8,
    default_cfg=1.0,
    default_sampler="euler",
    default_sigma_min=0.3,
    default_sigma_max=500.0,
)

_SPECS_BY_ID: dict[str, ModelSpec] = {
    MODEL_MUSIC_ID: SPEC_MUSIC,
    MODEL_EFFECTS_ID: SPEC_EFFECTS,
}

# Aliases (minúsculos) → ID HF canónico
MODEL_ALIASES: dict[str, str] = {
    "music": MODEL_MUSIC_ID,
    "full": MODEL_MUSIC_ID,
    "1.0": MODEL_MUSIC_ID,
    "effects": MODEL_EFFECTS_ID,
    "small": MODEL_EFFECTS_ID,
    "sfx": MODEL_EFFECTS_ID,
}


def resolve_model_id(user: str | None) -> str:
    """Resolve alias ou ID HF. ``None`` ou vazio → modelo música (Open 1.0)."""
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
    if "open-small" in hf_id or "stable-audio-open-small" in hf_id:
        return SPEC_EFFECTS
    # Modelo desconhecido: limites do Open 1.0 e defaults de música.
    return ModelSpec(
        hf_id=hf_id,
        label=f"Custom ({hf_id})",
        max_seconds=47.0,
        default_steps=SPEC_MUSIC.default_steps,
        default_cfg=SPEC_MUSIC.default_cfg,
        default_sampler=SPEC_MUSIC.default_sampler,
        default_sigma_min=SPEC_MUSIC.default_sigma_min,
        default_sigma_max=SPEC_MUSIC.default_sigma_max,
    )
