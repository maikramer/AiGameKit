"""Text2Sound — enriquecimento determinístico de prompt.

Expande o prompt do utilizador com descritores sonoros curados (extraídos dos
60 presets) que o Stable Audio Open compreende bem. Sem LLM, sem rede — puro
Python + YAML. Integra-se por quality tier (``enhance: true/false``) e pode ser
forçado via ``--enhance``/``--no-enhance`` no CLI.

Pipeline:
  1. ``validate_and_correct`` — limpa whitespace, deteta prompts fracos/vazios.
  2. ``detect_sound_type`` — match de keywords ou pista do audio_kind.
  3. ``add_descriptors`` — anexa descritores de textura/dinâmica/contexto.
  4. ``enhance_negative`` — anexa anti-descritores genéricos ao negative prompt.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Mesma convenção do QualityEngine (quality.py:24): data files ao lado do módulo
# em Shared/src/aigamekit_shared/data/. Resolvido em runtime para funcionar tanto
# em editable install como empacotado.
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "Shared" / "src" / "aigamekit_shared" / "data"
_DESCRIBERS_FALLBACK_PATH = _DATA_DIR / "audio-descriptors.yaml"

# Mapa estável audio_kind → sound_type (1:1, não vale a pena pôr no YAML).
_KIND_TO_TYPE: dict[str, str] = {
    "sfx_impact": "impact",
    "sfx_weapon": "weapon",
    "sfx_magic": "magic",
    "sfx_movement": "movement",
    "sfx_ui": "ui",
    "sfx_creature": "creature",
    "sfx_destruction": "destruction",
    "sfx_mechanical": "mechanical",
    "sfx_elemental": "elemental",
    "sfx_vocal": "vocal",
    "sfx_collectible": "collectible",
    "sfx_alarm": "alarm",
    "sfx_ambient_sfx": "ambient_loop",
    "sfx_short": "generic",
    "sfx_interact": "generic",
    "music_loop": "music_loop",
    "ambient_loop": "ambient_loop",
    "ambient_one_shot": "ambient_loop",
}

# Palavras significativas (len > 3) usadas no teste anti-duplicação.
_MIN_WORD_LEN = 4


def load_descriptor_data(path: Path | None = None) -> dict[str, Any]:
    """Carrega o YAML de descritores. Cacheado (o ficheiro não muda em runtime)."""
    return _load_descriptor_data_cached(path or _DESCRIBERS_FALLBACK_PATH)


@lru_cache(maxsize=4)
def _load_descriptor_data_cached(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("audio-descriptors.yaml malformado")
        return data
    except (OSError, ValueError):
        # Fallback gracioso: dados mínimos inline para nunca partir a geração.
        return {
            "keywords": {},
            "descriptors": {
                "generic": {"texture": ["high quality", "detailed", "clear"], "dynamics": [], "context": []}
            },
            "negative_descriptors": ["low quality", "distortion", "clipping"],
            "weak_terms": [],
        }


def validate_and_correct(prompt: str, data: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """Limpa e valida o prompt do utilizador.

    Args:
        prompt: Prompt cru do utilizador.
        data: Dados carregados (opcional; se None carrega do YAML).

    Returns:
        ``(clean_prompt, corrections)`` — prompt limpo e lista de correções
        aplicadas (para metadata). O prompt nunca fica vazio.
    """
    data = data or load_descriptor_data()
    corrections: list[str] = []
    weak_terms = [t.lower() for t in data.get("weak_terms", [])]

    clean = prompt.strip()
    # Colapsar whitespace múltiplo.
    import re

    clean = re.sub(r"\s+", " ", clean).strip()

    if not clean or len(clean) < 3:
        clean = "ambient sound"
        corrections.append("empty_or_short_fallback")
        return clean, corrections

    # Detetar weak terms (case-insensitive). Se encontrar, marca como fraco.
    prompt_lower = clean.lower()
    for term in weak_terms:
        if term in prompt_lower:
            corrections.append(f"weak_term:{term}")
            break

    # Capitalizar primeira letra (cosmético; Stable Audio é case-insensitive,
    # mas prompts capitalizados ficam mais legíveis no metadata).
    clean = clean[0].upper() + clean[1:] if clean else clean

    return clean, corrections


def detect_sound_type(prompt: str, audio_kind: str | None = None, data: dict[str, Any] | None = None) -> str:
    """Deteta o tipo de som do prompt.

    Precedência: audio_kind (pista forte do ``--category``) > keyword match > generic.

    Args:
        prompt: Prompt (já limpo recomendado, mas funciona com cru).
        audio_kind: Nome do audio_kind resolvido pelo QualityEngine (ex: sfx_weapon).
        data: Dados carregados (opcional).

    Returns:
        Nome do sound_type (chave em ``descriptors`` do YAML).
    """
    data = data or load_descriptor_data()

    # 1. audio_kind wins (sinal mais forte — vem de --category explícito).
    if audio_kind and audio_kind in _KIND_TO_TYPE:
        return _KIND_TO_TYPE[audio_kind]

    # 2. keyword match (primeiro tipo cuja keyword aparece no prompt).
    prompt_lower = prompt.lower()
    keywords: dict[str, list[str]] = data.get("keywords", {})
    for sound_type, kws in keywords.items():
        for kw in kws:
            if kw.lower() in prompt_lower:
                return sound_type

    # 3. fallback.
    return "generic"


def _already_present(prompt_lower: str, descriptor: str) -> bool:
    """True se uma palavra significativa do descritor já está no prompt."""
    words = [w for w in descriptor.lower().split() if len(w) >= _MIN_WORD_LEN]
    if not words:
        return descriptor.lower() in prompt_lower
    return any(w in prompt_lower for w in words)


def _pick_descriptors(sound_type: str, prompt_lower: str, data: dict[str, Any], *, max_per_group: int = 2) -> list[str]:
    """Seleciona descritores do tipo, saltando os já presentes no prompt."""
    type_desc = data.get("descriptors", {}).get(sound_type) or data.get("descriptors", {}).get("generic", {})
    picked: list[str] = []
    for group in ("texture", "dynamics", "context"):
        for desc in type_desc.get(group, [])[:max_per_group]:
            if not _already_present(prompt_lower, desc):
                picked.append(desc)
    return picked


def enhance_prompt(
    prompt: str,
    audio_kind: str | None = None,
    data: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Enriquece o prompt positivo com descritores curados.

    Args:
        prompt: Prompt cru do utilizador.
        audio_kind: Pista de tipo (do ``--category`` via QualityEngine).
        data: Dados carregados (opcional; teste-friendly).

    Returns:
        ``(enhanced_prompt, enhancement_meta)`` onde ``enhancement_meta`` é um
        dict para o metadata JSON: sound_type, descriptors_added, corrections.
    """
    data = data or load_descriptor_data()

    clean, corrections = validate_and_correct(prompt, data)
    sound_type = detect_sound_type(clean, audio_kind=audio_kind, data=data)
    prompt_lower = clean.lower()

    # Prompt fraco (weak term detetado) → mais descritores por grupo.
    max_per_group = 3 if corrections else 2
    picked = _pick_descriptors(sound_type, prompt_lower, data, max_per_group=max_per_group)

    enhanced = f"{clean}, {', '.join(picked)}" if picked else clean

    return enhanced, {
        "sound_type": sound_type,
        "descriptors_added": picked,
        "corrections": corrections,
        "original_prompt": prompt,
    }


def enhance_negative(
    negative: str,
    data: dict[str, Any] | None = None,
) -> str:
    """Anexa anti-descritores genéricos ao negative prompt (sem duplicar).

    Args:
        negative: Negative prompt atual (do audio_kind ou --negative).
        data: Dados carregados (opcional).

    Returns:
        Negative prompt enriquecido (ou inalterado se todos já presentes).
    """
    data = data or load_descriptor_data()
    neg_lower = negative.lower()
    to_add = [d for d in data.get("negative_descriptors", []) if not _already_present(neg_lower, d)]
    if not to_add:
        return negative
    return f"{negative}, {', '.join(to_add)}"
