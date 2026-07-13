"""Prompt enhancer para texturas de chão de jogo (ground/floor top-down).

Resolve três problemas recorrentes em texturas de chão geradas por difusão:
  1. **Perspetiva isométrica/angulada** — sem direção de câmara, o modelo mostra
     a grama "a crescer" em diagonal em vez de vista de cima.
  2. **Zoom macro** — termos como "natural blades" induzem close-ups de detalhe.
  3. **Relevo 3D excessivo** — "lush lawn" gera lâminas com volume e sombra forte.

O backend Stable Diffusion (UNet convolucional + circular padding) tem uma janela
CLIP de 77 tokens: o material/descritor deve vir **antes** dos modificadores de
viewpoint para entrar na composição. Ao contrário do FLUX (T5 wide), vocabulário
como "aerial photograph / nadir view / spanning several meters" faz o SD1.5 gerar
**imagem de satélite** (árvores, lagos vistos de avião) — os sufixos abaixo usam
linguagem de superfície próxima (top-down close-up, flat ground).
"""

from __future__ import annotations

import re

# Sufixo positivo para ground textures (SD1.5). Linguagem de superfície próxima:
# "top-down close-up photograph of flat ground surface" descreve o que queremos
# sem acionar o prior de "vista aérea de paisagem" (árvores/lagos/horizonte).
GROUND_SUFFIX = (
    "top-down close-up photograph of flat ground surface, seamless tileable game texture, "
    "albedo material, even diffuse lighting, uniform fine detail"
)

# Negative prompt por defeito para ground textures — bloqueia os 3 problemas e os
# artefactos de "vista aérea" típicos do SD1.5 (satélite/drone/paisagem).
GROUND_DEFAULT_NEGATIVE = (
    "aerial view, satellite image, drone shot, landscape, horizon, sky, trees, bushes, "
    "buildings, roads, water, isometric view, oblique angle, perspective, tilted camera, "
    "side view, macro photography, extreme close-up, individual grass blades leaning sideways, "
    "3d render, depth of field, bokeh, strong directional shadows, deep relief, "
    "ambient occlusion, high contrast, dramatic lighting"
)

# Orçamento brando em palavras (~55 ≈ 70-75 tokens CLIP). O SD1.5 trunca a 77
# tokens; aparar o excesso evita que o sujeito do utilizador seja cortado.
_SOFT_WORD_BUDGET = 55

# Marcadores que indicam que o utilizador já especificou o viewpoint top-down.
_VIEWPOINT_MARKERS = re.compile(
    r"\b(top.?down|straight from above|perpendicular to ground|overhead|bird.?s? eye|"
    r"from above|orthographic|flat view|top view|nadir|close.?up)\b",
    flags=re.IGNORECASE,
)
_LIGHTING_MARKERS = re.compile(
    r"\b(flat (?:diffuse )?lighting|even lighting|diffuse overcast|albedo|no (?:strong )?shadows|low relief)\b",
    flags=re.IGNORECASE,
)
_SCALE_MARKERS = re.compile(
    r"\b(medium.?scale|not macro|not close.?up|large area|wide ground area|flat ground surface|"
    r"seamless tileable)\b",
    flags=re.IGNORECASE,
)

# Palavras que tipicamente indicam chão/terreno de jogo.
_GROUND_KEYWORDS = re.compile(
    r"\b(grass|lawn|meadow|dirt|soil|earth|sand|desert|mud|swamp|stone|rock|gravel|"
    r"ground|floor|terrain|path|road|pavement|asphalt|cobble|snow|ice|tarmac|"
    r"forest floor|savanna|tundra|clay|pebbles)\b",
    flags=re.IGNORECASE,
)


def _count_words(text: str) -> int:
    """Conta palavras de um prompt (aproximação barata de tokens)."""
    return len(text.split())


def _truncate_to_budget(prompt: str, budget_words: int = _SOFT_WORD_BUDGET) -> str:
    """Apara o prompt por palavras se exceder o orçamento brando.

    Não corta palavras a meio; trunca à última palavra completa dentro do budget.
    Preserva pontuação terminal quando possível.
    """
    words = prompt.split()
    if len(words) <= budget_words:
        return prompt
    truncated = " ".join(words[:budget_words])
    # Garante terminação limpa (ponto ou sem pontuação — sem vírgula pendente).
    truncated = re.sub(r"[,\s]+$", "", truncated)
    return truncated


def looks_like_ground(prompt: str) -> bool:
    """Heurística: o prompt descreve um chão/terreno de jogo?"""
    return bool(_GROUND_KEYWORDS.search(prompt or ""))


def enhance_ground_prompt(
    prompt: str,
    *,
    viewpoint: bool = True,
    lighting: bool = True,
    scale: bool = True,
) -> str:
    """Aplica o sufixo de chão top-down (SD1.5) a um prompt.

    O sujeito do utilizador fica **antes** do sufixo ``GROUND_SUFFIX`` para entrar
    primeiro na janela de 77 tokens do CLIP (é o que mais influencia o material).
    Se o utilizador já incluiu marcadores de viewpoint/iluminação/escala, o sufixo
    não é duplicado.

    Args:
        prompt: Prompt do utilizador (já pode incluir tileability).
        viewpoint: Reservado (mantido para compat de assinatura).
        lighting: Reservado (mantido para compat de assinatura).
        scale: Reservado (mantido para compat de assinatura).

    Returns:
        Prompt melhorado, aparado ao orçamento brando de palavras.
    """
    p = (prompt or "").strip()
    if not p:
        return p

    # Se o utilizador já cobriu os 3 vetores, não adiciona o sufixo.
    already_covered = _VIEWPOINT_MARKERS.search(p) and _LIGHTING_MARKERS.search(p) and _SCALE_MARKERS.search(p)
    if already_covered:
        return _truncate_to_budget(p)

    return _truncate_to_budget(f"{p}, {GROUND_SUFFIX}")


def enhance_ground_negative(negative_prompt: str) -> str:
    """Funde o negative do utilizador com o conjunto por defeito de ground textures.

    Não duplica termos já presentes.
    """
    user_neg = (negative_prompt or "").strip()
    existing = {w.strip().lower().rstrip(",.") for w in user_neg.split(",") if w.strip()}
    additions = [w for w in GROUND_DEFAULT_NEGATIVE.split(", ") if w.lower() not in existing]
    if not additions:
        return user_neg or GROUND_DEFAULT_NEGATIVE
    merged = ", ".join(additions)
    return f"{user_neg}, {merged}" if user_neg else merged
