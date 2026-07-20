"""Construção do prompt final a partir do perfil, preset e linha do manifest."""

from __future__ import annotations

import re
from typing import Any

from .categories import get_category
from .manifest import ManifestRow
from .profile import GameProfile

_KIND_HINTS: dict[str, str] = {
    "prop": "isolated game prop, hero asset, clear readability",
    "character": "game character design, full figure or bust as appropriate, memorable silhouette",
    "environment": "environment art piece, readable composition, game level visual",
}

# Descrições sonoras por kind (Text2Sound — não usar hints visuais de imagem)
_AUDIO_KIND_HINTS: dict[str, str] = {
    "prop": "short game sound effect, punchy and clear",
    "character": "character-related audio, game-ready",
    "environment": "ambient soundscape, loop-friendly atmospheric audio",
}

# Quando generate_3d=true, a mesma imagem alimenta image-to-3D; sombras/pedestal viram geometria fantasma.
# Estratégia v2: framing positivo em vez de negações. FLUX ignora "no X" mas respeita
# descrições positivas do tipo de render.
_I2M_REF_LIGHTING = (
    "3D game asset reference render, "
    "flat ambient lighting from all directions equally, "
    "uniform soft diffuse illumination, "
    "pure white seamless infinite void background on all sides, "
    "single isolated object centered in frame, "
    "vibrant flat colors, completely shadowless, matte surface finish, "
    "white background visible beneath and around the object, "
    "clean silhouette"
)

_I2M_EXTRA_NEGATIVES: tuple[str, ...] = (
    "cast shadows",
    "harsh directional light",
    "contact shadow",
    "ground shadow",
    "drop shadow",
    "dark circle under object",
    "pedestal",
    "platform base",
    "ground plane",
    "spotlight",
    "rim light",
    "volumetric light",
)

_MESH_HINT_NO_FAKE_GROUND = (
    "Watertight mesh only; no spurious ground disc, pedestal ring, or base slab from shadow-like shading"
)

# Variante A-pose do hint_rig: usada quando o asset tem pose_preset A-pose
# (quaternius-apose). O hint_rig humanoid força «T-pose braços horizontais» —
# em assets A-pose isso luta contra a idea e produz braços in-between/deformados.
_HINT_RIG_APOSE = (
    "standing upright in a relaxed A-pose with both arms angled down at about 45 degrees "
    "away from the body, visible gap between arms and torso, open hands with spread fingers, "
    "legs shoulder-width apart with feet pointing forward, "
    "perfectly symmetrical left-right body, "
    "straight erect spine and neck, head facing forward, "
    "all limbs fully visible and clearly separated from torso, "
    "no crossed limbs, no bent elbows or knees, no clenched fists, "
    "no self-occlusion between arms and torso"
)


def _is_apose_preset(pose_preset: str | None) -> bool:
    """True se o pose_preset é uma variante A-pose (quaternius-apose e aliases)."""
    return bool(pose_preset) and "apose" in str(pose_preset).strip().lower().replace("-", "")

# O título do jogo no prompt tende a aparecer como texto/logótipo na imagem; não incluir.
# Restrições extra para 2D (referência) — modelos desenhadores de UI/caption.
_UI_TEXT_NEGATIVES: tuple[str, ...] = (
    "text overlay",
    "caption",
    "watermark",
    "logo",
    "title card",
    "typography",
    "UI",
    "HUD",
    "icon strip",
    "app icon",
)


def _mood_atmosphere(genre: str, tone: str) -> str:
    return ", ".join(p for p in (genre.strip(), tone.strip()) if p)


def _append_if_new(prompt: str, hint: str) -> str:
    if not hint or hint.lower() in prompt.lower():
        return prompt
    return f"{prompt} {hint}."


def enhance_prompt_for_pipeline(
    prompt: str,
    *,
    category: str = "",
    generate_3d: bool = False,
    generate_rig: bool = False,
    image_source: str = "text2d",
    for_3d: bool = False,
    pose_preset: str | None = None,
) -> str:
    cat = get_category(category)
    if cat is None:
        return prompt
    if image_source == "texture2d":
        prompt = _append_if_new(prompt, cat.hint_texture)
    elif generate_3d and not for_3d:
        # Ref i2m: framing/câmera (2d) + cues de mesh (3d). Antes só 3d →
        # ângulo de vista do hint_2d nunca entrava na imagem.
        prompt = _append_if_new(prompt, cat.hint_2d)
        prompt = _append_if_new(prompt, cat.hint_3d)
    elif for_3d:
        prompt = _append_if_new(prompt, cat.hint_3d)
    else:
        prompt = _append_if_new(prompt, cat.hint_2d)
    if generate_rig:
        # A-pose no esqueleto ⇒ hint A-pose na imagem (alinhamento imagem↔pose).
        rig_hint = _HINT_RIG_APOSE if _is_apose_preset(pose_preset) else cat.hint_rig
        prompt = _append_if_new(prompt, rig_hint)
    if cat.extra_negatives:
        lower_prompt = prompt.lower()
        new_negs = [n for n in cat.extra_negatives if n.lower() not in lower_prompt]
        if new_negs:
            prompt = f"{prompt} Avoid: {', '.join(new_negs)}."
    return prompt


def build_prompt(
    profile: GameProfile,
    preset: dict[str, Any],
    row: ManifestRow,
    *,
    for_3d: bool = False,
) -> str:
    """Monta o prompt positivo (negativos concatenados ao final como restrições)."""
    prefix = str(preset.get("prompt_prefix") or "").strip()
    label_hint = ""
    label_hint = str(preset.get("hint_3d") or "").strip() if for_3d else str(preset.get("hint_2d") or "").strip()

    kind = (row.kind or "").strip().lower()
    kind_extra = _KIND_HINTS.get(kind, "")

    mood = _mood_atmosphere(profile.genre, profile.tone)
    idea = row.idea.strip()

    chunks: list[str] = []
    if prefix:
        chunks.append(prefix)
    if mood:
        chunks.append(f"Mood and setting: {mood}.")
    chunks.append(f"{idea}.")
    if kind_extra:
        chunks.append(kind_extra + ".")
    if label_hint:
        chunks.append(label_hint + ".")
    if row.generate_3d:
        if for_3d:
            chunks.append(_MESH_HINT_NO_FAKE_GROUND + ".")
        else:
            chunks.append(_I2M_REF_LIGHTING + ".")

    main = " ".join(chunks)
    main = re.sub(r"\s+", " ", main).strip()

    neg_parts: list[str] = []
    neg_suffix = str(preset.get("negative_suffix") or "").strip()
    if neg_suffix:
        neg_parts.append(neg_suffix)
    for kw in profile.negative_keywords:
        kw = str(kw).strip()
        if kw:
            neg_parts.append(kw)
    if row.generate_3d and not for_3d:
        neg_parts.extend(_I2M_EXTRA_NEGATIVES)
    if not for_3d:
        neg_parts.extend(_UI_TEXT_NEGATIVES)
    if neg_parts:
        neg_joined = ", ".join(neg_parts)
        main = f"{main} Avoid: {neg_joined}."

    if row.category:
        main = enhance_prompt_for_pipeline(
            main,
            category=row.category,
            generate_3d=row.generate_3d,
            generate_rig=row.generate_rig,
            for_3d=for_3d,
            pose_preset=getattr(getattr(row, "omni", None), "pose_preset", None),
        )

    return main


def build_audio_prompt(
    profile: GameProfile,
    preset: dict[str, Any],
    row: ManifestRow,
) -> str:
    """Prompt para Text2Sound (áudio a partir de texto; sem referências visuais de imagem 3D)."""
    prefix = str(preset.get("prompt_prefix") or "").strip()
    label_hint = str(preset.get("hint_audio") or preset.get("hint_2d") or "").strip()

    kind = (row.kind or "").strip().lower()
    kind_extra = _AUDIO_KIND_HINTS.get(kind, "game audio, stereo, high quality")

    mood = _mood_atmosphere(profile.genre, profile.tone)
    idea = row.idea.strip()

    chunks: list[str] = []
    if prefix:
        chunks.append(prefix)
    if mood:
        chunks.append(f"Mood: {mood}.")
    chunks.append(f"{idea}.")
    chunks.append(kind_extra + ".")
    if label_hint:
        chunks.append(label_hint + ".")

    dur = row.audio_duration
    if row.audio_profile == "effects" or (dur is not None and dur <= 5):
        chunks.append("brief single occurrence, no repetition, no looping")
    elif dur is not None and dur <= 10:
        chunks.append("short concise sound, minimal repetition")
    elif dur is not None and dur >= 20:
        chunks.append("loop-friendly, seamless loop, continuous atmosphere")

    if dur is not None and dur <= 5:
        chunks.append("immediate start, no silence at beginning")

    if row.audio_profile == "effects":
        chunks.append("crisp punchy sound effect, clean transients")
    elif row.audio_profile == "music":
        chunks.append("musical composition, melodic, harmonic")

    main = " ".join(chunks)
    main = re.sub(r"\s+", " ", main).strip()

    neg_parts: list[str] = []
    neg_suffix = str(preset.get("negative_suffix") or "").strip()
    if neg_suffix:
        neg_parts.append(neg_suffix)
    for kw in profile.negative_keywords:
        kw = str(kw).strip()
        if kw:
            neg_parts.append(kw)
    if dur is not None and dur <= 5:
        neg_parts.append("long fade-out")
        neg_parts.append("extended reverb tail")
        neg_parts.append("repetition")
    if neg_parts:
        neg_joined = ", ".join(neg_parts)
        main = f"{main} Avoid: {neg_joined}."

    return main
