"""Prompt enhancer para texturas de chão de jogo (ground/floor top-down).

Resolve três problemas recorrentes em texturas de chão geradas por difusão:
  1. **Perspetiva isométrica/angulada** — sem direção de câmara, o modelo mostra
     a grama "a crescer" em diagonal em vez de vista de cima.
  2. **Zoom macro** — termos como "natural blades" induzem close-ups de detalhe.
  3. **Relevo 3D excessivo** — "lush lawn" gera lâminas com volume e sombra forte.

O enhancer aplica modificadores de viewpoint/iluminação/escala e é **token-aware**:
o FLUX usa T5 (janela larga) + CLIP (77 tokens para o pooled embedding). Manter o
sujeito + viewpoint **no início** do prompt garante que entram na janela do CLIP,
que é o que mais influencia a composição geral. O sufixo é aparado se exceder um
orçamento de palavras.
"""

from __future__ import annotations

import re

# Modificadores por vetor (ordem = prioridade; os primeiros entram no CLIP).
GROUND_VIEWPOINT = (
    "flat top-down orthographic view, straight from above, perpendicular to ground, no perspective, no angle"
)
GROUND_LIGHTING = "even flat diffuse lighting, low relief, minimal height variation, no strong shadows"
GROUND_SCALE = "medium-scale texture, large area coverage, not macro, not close-up"

# Negative prompt por defeito para ground textures — bloqueia os 3 problemas.
GROUND_DEFAULT_NEGATIVE = (
    "macro, close-up, individual blades, extreme detail, 3d relief, strong directional shadows, "
    "depth, isometric, perspective, angled, side view, oblique, baked shadows, ambient occlusion, "
    "high contrast, 3d, pbr"
)

# Orçamento brando em palavras (~55 ≈ 70-75 tokens CLIP). O T5 leva o prompt
# completo, mas aparar o excesso evita desperdiçar a janela do CLIP com sufixo.
_SOFT_WORD_BUDGET = 55

# Marcadores que indicam que o utilizador já especificou o viewpoint.
_VIEWPOINT_MARKERS = re.compile(
    r"\b(top.?down|straight from above|perpendicular to ground|overhead|bird.?s? eye|"
    r"from above|orthographic|flat view|top view)\b",
    flags=re.IGNORECASE,
)
_LIGHTING_MARKERS = re.compile(
    r"\b(flat (?:diffuse )?lighting|even lighting|no (?:strong )?shadows|low relief)\b",
    flags=re.IGNORECASE,
)
_SCALE_MARKERS = re.compile(
    r"\b(medium.?scale|not macro|not close.?up|large area|aerial scale|far view)\b",
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
    """Aplica modificadores de chão top-down a um prompt.

    Acrescenta apenas os vetores que ainda não estão presentes no prompt
    (deteção por regex, case-insensitive). O sujeito do utilizador fica
    **depois** do viewpoint/iluminação para que os modificadores de composição
    entrem primeiro na janela de 77 tokens do CLIP.

    Args:
        prompt: Prompt do utilizador (já pode incluir tileability).
        viewpoint: Aplicar modificador de câmara top-down.
        lighting: Aplicar modificador de iluminação plana / baixo relevo.
        scale: Aplicar modificador de escala média.

    Returns:
        Prompt melhorado, aparado ao orçamento brando de palavras.
    """
    p = (prompt or "").strip()
    if not p:
        return p

    prefix_parts: list[str] = []
    if viewpoint and not _VIEWPOINT_MARKERS.search(p):
        prefix_parts.append(GROUND_VIEWPOINT)
    if lighting and not _LIGHTING_MARKERS.search(p):
        prefix_parts.append(GROUND_LIGHTING)
    if scale and not _SCALE_MARKERS.search(p):
        prefix_parts.append(GROUND_SCALE)

    if not prefix_parts:
        return _truncate_to_budget(p)

    prefix = ". ".join(prefix_parts) + ". "
    return _truncate_to_budget(prefix + p)


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
