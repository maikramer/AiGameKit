"""Gramática de combinação de packs de animação (``--anim-pack``).

Um único parser canónico partilhado por Animator3D (CLI) e GameAssets
(validação do ``game.yaml``/manifest), para que as duas pontas aceitem
exactamente a mesma gramática:

- packs individuais: ``quaternius`` (UAL1), ``quaternius2`` (UAL2),
  ``villager`` (Kevin Iglesias, FBX por clip);
- ``both``  = UAL1 + UAL2 (mesmo rig; UAL2 substitui keys dedicadas);
- ``all``   = UAL1 + UAL2 + villager (villager acrescenta trabalhos e só
  substitui as keys em ``replace_keys`` do seu perfil);
- lista por vírgulas: ``"quaternius2,villager"``, ``"both,villager"`` —
  a ordem define quem substitui quem (pack posterior ganha as colisões
  permitidas).

A expansão devolve uma lista ordenada e sem duplicados de chaves de pack
(:func:`aigamekit_shared.quaternius_fetch.pack_names` + aliases).
"""

from __future__ import annotations

# Packs individuais (chaves válidas para fetch_itch_pack / perfis retarget).
ANIM_PACK_SINGLES: tuple[str, ...] = ("quaternius", "quaternius2", "villager")
# Aliases compostos aceites na gramática. ``all`` corre o villager PRIMEIRO:
# a UAL (corrida por cima) substitui as colisões (idle/gather — locomoção e
# gestos da UAL ganham) e os clips só-villager (mine, hammer, fish, plow...)
# sobrevivem à cadeia porque a UAL não os define.
ANIM_PACK_ALIASES: dict[str, tuple[str, ...]] = {
    "both": ("quaternius", "quaternius2"),
    "all": ("villager", "quaternius", "quaternius2"),
}
# Tudo o que se pode escrever num --anim-pack (para mensagens de erro).
ANIM_PACK_TOKENS: tuple[str, ...] = ANIM_PACK_SINGLES + tuple(ANIM_PACK_ALIASES)


class AnimPackError(ValueError):
    """Spec de anim_pack inválida (token desconhecido ou lista vazia)."""


def expand_anim_packs(value: str) -> list[str]:
    """Expande uma spec de ``anim_pack`` numa lista ordenada de packs.

    Args:
        value: token ou lista por vírgulas (ex.: ``"both"``, ``"all"``,
            ``"quaternius2,villager"``). Case-insensitive.

    Returns:
        Lista ordenada (ordem de execução do retarget), sem duplicados.

    Raises:
        AnimPackError: token desconhecido ou spec vazia.
    """
    tokens = [t.strip().lower() for t in str(value).split(",") if t.strip()]
    if not tokens:
        raise AnimPackError(f"anim_pack vazio — usa um de: {', '.join(ANIM_PACK_TOKENS)}")
    out: list[str] = []
    for token in tokens:
        if token in ANIM_PACK_ALIASES:
            out.extend(ANIM_PACK_ALIASES[token])
        elif token in ANIM_PACK_SINGLES:
            out.append(token)
        else:
            raise AnimPackError(
                f"anim_pack desconhecido: {token!r} — válidos: {', '.join(ANIM_PACK_TOKENS)} "
                "(ou lista por vírgulas, ex.: both,villager)"
            )
    # Dedup preservando ordem: o 1.º lugar de um pack define a sua ordem.
    seen: set[str] = set()
    result: list[str] = []
    for pack in out:
        if pack not in seen:
            seen.add(pack)
            result.append(pack)
    return result
