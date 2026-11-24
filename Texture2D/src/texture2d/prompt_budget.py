"""Orçamento de tokens do CLIP para os prompts do SD1.5.

O codificador de texto do SD1.5 lê 77 tokens (BOS + 75 + EOS) por prompt — no
positivo e no negativo. O que passa disso não dá erro nem aviso: o modelo
simplesmente não o vê. Com ``--preset wood`` o prompt do preset (~15 tokens)
ia à cabeça, e um prompt de utilizador corrente (78 tokens) perdia a cauda —
exactamente a parte mais específica (viewpoint, iluminação, estilo).

Política (:func:`fit_prompt`):

1. se preset + utilizador couber, o texto é o de sempre (preset à cabeça);
2. se não couber, o prompt do utilizador vem inteiro e o preset só empresta as
   cláusulas que couberem — as restantes ficam em ``dropped``;
3. se o próprio utilizador não couber, corta na última fronteira de vírgula
   que cabe (``user_truncated``), nunca a meio de uma cláusula.

O negativo (:func:`fit_negative`) só entra em acção quando o texto antigo não
cabe: aí junta os grupos sem repetir cláusulas e mantém-nos por prioridade — a
base da ferramenta (costura, marca de água) primeiro, o utilizador depois e o
preset por último. Tudo o que já cabia fica byte a byte igual, para que um
asset gerado antes continue a reproduzir-se com a mesma seed.

A contagem usa o tokenizador do pipeline quando existe; sem ele (testes com
pipeline falso, CPU) usa uma estimativa por palavras e vírgulas com margem
para o lado seguro.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Janela do CLIP: BOS + 75 tokens de texto + EOS.
CLIP_MAX_TOKENS = 77
CLIP_BUDGET = CLIP_MAX_TOKENS - 2

TokenCounter = Callable[[str], int]

# Margem da estimativa sem tokenizador: ela fica 2-4% abaixo do real nos
# prompts medidos — 3 tokens cobrem a diferença para o lado seguro.
_ESTIMATE_MARGIN = 3


def estimate_tokens(text: str) -> int:
    """Conta tokens sem tokenizador: 10% das palavras + vírgulas + hífens + BOS/EOS.

    Calibrada nos prompts do pool do Vael, medidos com o tokenizador real
    (78 e 83 tokens): fica 2-4% abaixo nos dois casos, e a margem de
    :data:`_ESTIMATE_MARGIN` (3) cobre-a — sem tokenizador é mais seguro caber
    o prompt de novo do que deixar passar uma cauda truncada.
    """
    words = text.split()
    if not words:
        return 0
    commas = text.count(",")
    hyphenated = sum(2 if "-" in word else 0 for word in words)
    return math.ceil(len(words) * 1.1) + commas + hyphenated + 2


def counter_for(pipeline: object) -> TokenCounter:
    """Contador de tokens do ``pipeline.tokenizer`` (CLIP), ou a estimativa.

    O pipeline pode não ter tokenizador (testes com pipeline falso) ou o
    tokenizador falhar fora de linha — em ambos os casos cai-se na estimativa,
    já com a margem de 3 tokens.
    """
    tokenizer = getattr(pipeline, "tokenizer", None)
    if not callable(tokenizer):
        return _estimate_with_margin
    try:
        probe = tokenizer("texture")
        if not probe["input_ids"]:
            return _estimate_with_margin
    except Exception:
        return _estimate_with_margin

    def count(text: str) -> int:
        try:
            return len(tokenizer(text)["input_ids"])
        except Exception:
            return _estimate_with_margin(text)

    return count


def _estimate_with_margin(text: str) -> int:
    return estimate_tokens(text) + _ESTIMATE_MARGIN if text else 0


def split_clauses(text: str) -> list[str]:
    """Parte um prompt nas vírgulas, sem cláusulas vazias."""
    return [clause.strip() for clause in (text or "").split(",") if clause.strip()]


def _join(clauses: Sequence[str]) -> str:
    return ", ".join(clauses)


def _truncate_words(text: str, count: TokenCounter, budget: int) -> str:
    """Último recurso: uma cláusula só, mais longa que o orçamento inteiro."""
    words = text.split()
    while len(words) > 1 and count(" ".join(words)) > budget:
        words.pop()
    return " ".join(words)


def truncate_clauses(text: str, count: TokenCounter, budget: int) -> tuple[str, bool]:
    """Corta da cauda em fronteiras de vírgula até caber.

    Returns:
        Tuple ``(texto, truncado)`` — ``truncado`` é falso quando já cabia.
    """
    clauses = split_clauses(text)
    if not clauses:
        return "", False
    total = len(clauses)
    while len(clauses) > 1 and count(_join(clauses)) > budget:
        clauses.pop()
    text_out = _join(clauses)
    if count(text_out) > budget:
        text_out = _truncate_words(text_out, count, budget)
    return text_out, len(clauses) < total or text_out != (text or "").strip()


@dataclass(frozen=True)
class PromptFit:
    """Prompt positivo dentro da janela do CLIP.

    Attributes:
        text: Prompt final (o que o modelo vê inteiro).
        tokens: Tokens do texto final, com BOS/EOS.
        user_truncated: O prompt do utilizador não cabia inteiro e foi cortado.
        dropped: Cláusulas do preset que não couberam.
    """

    text: str
    tokens: int
    user_truncated: bool = False
    dropped: tuple[str, ...] = ()


def fit_prompt(user: str, preset: str | None, count: TokenCounter, budget: int = CLIP_BUDGET) -> PromptFit:
    """Cabe o prompt na janela do CLIP sem perder o que o utilizador pediu.

    ``user`` já inclui tudo o que a tool acrescentou ao pedido do utilizador
    (sufixo de textura, melhorias de chão); ``preset`` é o prompt genérico do
    preset de material.

    Args:
        user: Prompt do utilizador, já melhorado.
        preset: Prompt do preset (vazio/``None`` = sem preset).
        count: Contador de tokens (ver :func:`counter_for`).
        budget: Tokens de texto disponíveis (default: 75).

    Returns:
        :class:`PromptFit` com o texto final e o que ficou de fora.
    """
    user = (user or "").strip()
    preset = (preset or "").strip()
    if not preset:
        text, truncated = truncate_clauses(user, count, budget)
        return PromptFit(text, count(text), truncated)

    legacy = f"{preset}, {user}" if user else preset
    if count(legacy) <= budget:
        return PromptFit(legacy, count(legacy))

    # Não cabe: o utilizador primeiro, inteiro se for possível.
    user_fit, user_truncated = truncate_clauses(user, count, budget)
    kept: list[str] = []
    dropped: list[str] = []
    for clause in split_clauses(preset):
        candidate = f"{user_fit}, {_join([*kept, clause])}"
        if user_fit and count(candidate) <= budget:
            kept.append(clause)
        else:
            dropped.append(clause)
    text = f"{user_fit}, {_join(kept)}" if kept else user_fit
    return PromptFit(text, count(text), user_truncated, tuple(dropped))


@dataclass(frozen=True)
class NegativeFit:
    """Prompt negativo dentro da janela do CLIP.

    Attributes:
        text: Prompt final.
        tokens: Tokens do texto final, com BOS/EOS.
        dropped: Cláusulas que não couberam (as de prioridade mais baixa).
    """

    text: str
    tokens: int
    dropped: tuple[str, ...] = ()


def fit_negative(groups: Sequence[tuple[str, int]], count: TokenCounter, budget: int = CLIP_BUDGET) -> NegativeFit:
    """Junta os grupos do negativo sem passar a janela do CLIP.

    Args:
        groups: Pares ``(texto, prioridade)`` por ordem de saída; prioridade
            maior mantém-se quando não cabe (empate = a ordem dada).
        count: Contador de tokens (ver :func:`counter_for`).
        budget: Tokens de texto disponíveis (default: 75).

    Returns:
        :class:`NegativeFit` com o texto final e as cláusulas de fora.
    """
    seen: set[str] = set()
    clauses: list[tuple[str, int, int]] = []
    for text, priority in groups:
        for clause in split_clauses(text):
            key = clause.lower()
            if key in seen:
                continue
            seen.add(key)
            clauses.append((clause, priority, len(clauses)))

    def render(items: Sequence[tuple[str, int, int]]) -> str:
        return _join([clause for clause, _, _ in sorted(items, key=lambda item: item[2])])

    text = render(clauses)
    if count(text) <= budget:
        return NegativeFit(text, count(text))

    kept: list[tuple[str, int, int]] = []
    dropped: list[str] = []
    for item in sorted(clauses, key=lambda item: (-item[1], item[2])):
        if count(render([*kept, item])) <= budget:
            kept.append(item)
        else:
            dropped.append(item[0])
    text = render(kept)
    return NegativeFit(text, count(text), tuple(dropped))
