"""Prompt enhancement para gerar imagens 2D limpas de sombras/iluminação.

O Hunyuan3D interpreta silhuetas e contrastes da imagem 2D como geometria.
Qualquer sombra, gradiente de luz ou plano de chão na imagem vira
disco/placa no mesh 3D.

Estratégia v2: **framing positivo** em vez de negações.
Modelos FLUX ignoram frequentemente "no X" porque ainda processam o token "X".
Em vez disso, descrevemos positivamente o que queremos:
  - Tipo de render (albedo/flat/unlit)
  - Iluminação uniforme (flat ambient, softbox omnidirecional)
  - Fundo (branco puro infinito, sem horizonte)
  - Composição (objeto isolado, centrado, flutuante)
"""

from __future__ import annotations

import random
import re

# ---------------------------------------------------------------------------
# Termos tóxicos — causam sombras/chão na imagem 2D
# ---------------------------------------------------------------------------
TOXIC_TERMS: tuple[str, ...] = (
    "on the ground",
    "on the floor",
    "on a pedestal",
    "on a platform",
    "standing on",
    "sitting on",
    "on a surface",
    "on a table",
    "on a desk",
    "studio floor",
    "contact shadow",
    "drop shadow",
    "ground shadow",
    "ambient occlusion on ground",
    "harsh lighting",
    "dramatic lighting",
    "rim light",
    "strong directional light",
    "spotlight",
    "volumetric light",
    "god rays",
    "lens flare",
    "backlit",
    "side lit",
    "chiaroscuro",
    # VFX baked into mesh (smoke/fire/particles) — engine ParticleSystem owns FX.
    "billowing smoke",
    "volumetric smoke",
    "smoke plume",
    "smoke rising",
    "smoke coming out",
    "chimney smoke",
    "emitting smoke",
    "puffing smoke",
    "smoke trail",
    "smoke cloud",
    "wisps of smoke",
    "with smoke",
    "and smoke",
    "particle effects",
    "particle system",
    "fire particles",
    "ember particles",
    "sparks flying",
    "fire plume",
    "open flames",
    "with flames",
    "flames coming",
    "active fire",
    "burning fire",
)

# Frases arquitectónicas que o modelo lê como fumo → reescrever antes do strip.
_VFX_REWRITES: tuple[tuple[str, str], ...] = (
    ("smoke cap", "chimney rain hood"),
    ("smoke hood", "chimney rain hood"),
    ("smoking chimney", "empty chimney"),
    ("chimney smoking", "empty chimney"),
)

# ---------------------------------------------------------------------------
# Bloco de render — pré-fixado ao prompt do utilizador
# Descreve o "enquadramento técnico" da imagem: tipo de render + iluminação
# ---------------------------------------------------------------------------
_RENDER_PREFIX = (
    "3D game asset reference render, three-quarter view showing depth and volume, "
    "flat ambient lighting from all directions equally, "
    "uniform soft diffuse illumination, "
    "pure white seamless infinite void background on all sides, "
    "single isolated object centered in frame"
)

_RENDER_PREFIX_LIGHT = (
    "clean product render, soft even ambient light, white seamless background, isolated centered object"
)

# ---------------------------------------------------------------------------
# Sufixo de reforço — após a descrição do utilizador
# ---------------------------------------------------------------------------
_RENDER_SUFFIX = (
    "vibrant flat colors, "
    "completely shadowless, "
    "matte surface finish, "
    "full 3D volume visible from all angles, "
    "white background visible beneath and around the object, "
    "clean silhouette, game asset quality, "
    "static solid geometry only, "
    "empty chimneys and cold hearths without smoke or fire plumes, "
    "no particle blobs or volumetric effects"
)

_RENDER_SUFFIX_LIGHT = "flat lit, clean render, game asset, static mesh only, no smoke no flames no particles"

# Bipedais / humanoides: gap leve entre pernas na imagem 2D (Hunyuan lê
# silhueta — coxas coladas → webbing no mesh). Só quando o prompt sugere personagem.
_BIPED_STANCE = (
    "feet slightly apart with a clear open gap between the legs, "
    "thighs separated, relaxed upright stance, no fused or joined legs"
)

_BIPED_MARKERS: tuple[str, ...] = (
    "standing",
    "humanoid",
    "character",
    "person",
    "warrior",
    "bandit",
    "hero",
    "goblin",
    "merchant",
    "witch",
    "creature",
    "enemy",
    "npc",
    "legs",
    "feet",
    "a-pose",
    "apose",
    "t-pose",
    "tpose",
)

# ---------------------------------------------------------------------------
# Detector de termos já presentes (evitar duplicação)
# ---------------------------------------------------------------------------
_ALREADY_CLEAN_MARKERS: tuple[str, ...] = (
    "flat ambient",
    "albedo",
    "unlit render",
    "flat lighting",
    "uniform lighting",
    "diffuse only",
    "shadowless",
    "white seamless",
    "infinite background",
    "3d asset reference",
    "product render",
    "flat shad",
)


def _has_clean_markers(prompt_lower: str) -> bool:
    return any(m in prompt_lower for m in _ALREADY_CLEAN_MARKERS)


def sanitize_prompt(prompt: str) -> str:
    """Remove termos que causam sombras/chão/VFX na imagem 2D."""
    result = prompt

    # Reescrever frases ambíguas (ex. "smoke cap" = capelo, não fumo) antes do strip.
    for src, dst in sorted(_VFX_REWRITES, key=lambda p: len(p[0]), reverse=True):
        result = re.compile(re.escape(src), re.IGNORECASE).sub(dst, result)

    # Processar termos mais longos primeiro para evitar remoções parciais
    for term in sorted(TOXIC_TERMS, key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        result = pattern.sub("", result)

    # Conjunções/preposições/artigos órfãos após remoção de termos
    result = re.sub(r"\bwith\s+(and|,)", r"\1", result, flags=re.IGNORECASE)
    result = re.sub(r"\bwith\s*$", "", result)
    result = re.sub(r"\bwith\s*,", ",", result)
    result = re.sub(r"\band\s*,", ",", result)
    result = re.sub(r",\s*and\s*$", "", result)
    result = re.sub(r",\s*and\s*,", ",", result)
    # Trailing dangling words: "standing", "sitting", etc. sem complemento
    result = re.sub(r"\b(standing|sitting)\s+(and\s*)?$", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\b(standing|sitting)\s+(and\s*)?,", ",", result, flags=re.IGNORECASE)
    # Trailing "and" solto no final
    result = re.sub(r"\band\s*$", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"[,;]+\s*[,;]*", ", ", result)
    result = result.strip(",. ")
    return result


def _looks_bipedal(prompt_lower: str) -> bool:
    return any(m in prompt_lower for m in _BIPED_MARKERS)


def _with_biped_stance(prompt: str) -> str:
    """Acrescenta stance de pernas abertas se ainda não estiver no prompt."""
    pl = prompt.lower()
    if "gap between" in pl or "feet slightly apart" in pl or "thighs separated" in pl:
        return prompt
    if not _looks_bipedal(pl):
        return prompt
    return f"{prompt.rstrip(',. ')}, {_BIPED_STANCE}"


def enhance_prompt_for_clean_base(prompt: str, aggressive: bool = True) -> str:
    """Envolve o prompt do utilizador num enquadramento de render limpo.

    Modo aggressive (defeito): prefixo completo + sufixo albedo.
    Modo light: prefixo curto + sufixo curto (menos tokens, prompts já bons).
    """
    prompt_lower = prompt.lower()

    if _has_clean_markers(prompt_lower):
        return _with_biped_stance(prompt)

    if aggressive:
        base = f"{_RENDER_PREFIX}, {prompt.strip()}, {_RENDER_SUFFIX}"
    else:
        base = f"{_RENDER_PREFIX_LIGHT}, {prompt.strip()}, {_RENDER_SUFFIX_LIGHT}"
    return _with_biped_stance(base)


def create_optimized_prompt(prompt: str, aggressive: bool = True) -> str:
    """Pipeline completo: sanitizar + enquadrar com render limpo."""
    clean = sanitize_prompt(prompt)
    enhanced = enhance_prompt_for_clean_base(clean, aggressive=aggressive)
    return enhanced


# ---------------------------------------------------------------------------
# Modificadores de prompt para retry — palavras neutras que geram imagens
# levemente diferentes sem alterar o estilo ou o objeto descrito.
# ---------------------------------------------------------------------------
RETRY_PROMPT_MODIFIERS: tuple[str, ...] = (
    "high quality",
    "beautiful",
    "nice",
    "detailed",
    "clean",
    "polished",
    "crisp",
    "sharp",
    "well crafted",
    "professional",
    "elegant",
    "refined",
    "pristine",
    "fine",
    "premium",
)


def modify_prompt_for_retry(prompt: str, attempt: int, *, rng: random.Random | None = None) -> str:
    """Adiciona um modificador neutro ao prompt para gerar uma imagem levemente diferente.

    Usa ``attempt`` como índice (com shuffle determinístico por seed) para
    garantir que cada retry usa um modificador diferente.
    """
    if rng is None:
        rng = random.Random(42)

    mods = list(RETRY_PROMPT_MODIFIERS)
    rng.shuffle(mods)

    idx = (attempt - 1) % len(mods)
    modifier = mods[idx]

    prompt_lower = prompt.lower()
    if modifier.lower() in prompt_lower:
        idx = (idx + 1) % len(mods)
        modifier = mods[idx]

    return f"{modifier} {prompt}"
