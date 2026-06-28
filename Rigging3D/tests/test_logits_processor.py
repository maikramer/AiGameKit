"""Paridade do VocabSwitchingLogitsProcessor vectorizado vs FSM legado.

Gera sequências válidas-sob-máscara com o FSM original e verifica que a
versão vectorizada produz exactamente as mesmas máscaras em cada passo.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torch_cluster")

_UNIRIG = Path(__file__).resolve().parents[1] / "src" / "rigging3d" / "unirig"
sys.path.insert(0, str(_UNIRIG))

from src.tokenizer.spec import TokenizerConfig  # noqa: E402


@pytest.fixture(scope="module")
def tokenizer():
    import src.tokenizer.tokenizer_part as tp

    original = tp.get_order
    tp.get_order = lambda cfg: None
    try:
        config = TokenizerConfig(
            method="tokenizer_part",
            num_discrete=256,
            continuous_range=(-1.0, 1.0),
            cls_token_id={"vroid": 0, "mixamo": 1, "articulationxl": 2},
            parts_token_id={"body": 0, "hand": 1},
            order_config=None,
        )
        yield tp.TokenizerPart(config=config)
    finally:
        tp.get_order = original


def _processors(tokenizer, start_tokens):
    from src.model.unirig_ar import (
        LegacyVocabSwitchingLogitsProcessor,
        VocabSwitchingLogitsProcessor,
    )

    st = torch.tensor(start_tokens, dtype=torch.long)
    return (
        LegacyVocabSwitchingLogitsProcessor(tokenizer=tokenizer, start_tokens=st),
        VocabSwitchingLogitsProcessor(tokenizer=tokenizer, start_tokens=st),
    )


def _random_valid_walk(tokenizer, start_tokens: list[int], steps: int, rng: random.Random) -> list[list[int]]:
    """Sequências geradas (sem start) em cada passo, sempre válidas sob máscara."""
    import numpy as np

    seq: list[int] = []
    prefixes = []
    for _ in range(steps):
        prefixes.append(list(seq))
        allowed = tokenizer.next_posible_token(ids=np.array(start_tokens + seq, dtype=np.int64))
        tok = rng.choice(allowed)
        if tok == tokenizer.token_id_eos:
            break
        seq.append(tok)
    return prefixes


@pytest.mark.parametrize("cls", [None, "articulationxl", "vroid"])
def test_vectorized_matches_legacy(tokenizer, cls) -> None:
    rng = random.Random(42)
    start = [tokenizer.token_id_bos]
    if cls is not None:
        start.append(tokenizer.cls_name_to_token(cls))
    legacy, fast = _processors(tokenizer, start)

    vocab = tokenizer.vocab_size
    for trial in range(20):
        prefixes = _random_valid_walk(tokenizer, start, steps=40, rng=rng)
        # batch: compara várias prefixes do mesmo comprimento (simula beams)
        for prefix in prefixes:
            if len(prefix) == 0:
                continue
            ids = torch.tensor([prefix], dtype=torch.long)
            scores_a = torch.zeros(1, vocab)
            scores_b = torch.zeros(1, vocab)
            out_a = legacy(ids, scores_a)
            out_b = fast(ids, scores_b)
            same = torch.isneginf(out_a) == torch.isneginf(out_b)
            assert bool(same.all()), (
                f"trial={trial} prefix_len={len(prefix)} cls={cls}\n"
                f"legacy allowed={torch.where(~torch.isneginf(out_a))[1].tolist()}\n"
                f"fast   allowed={torch.where(~torch.isneginf(out_b))[1].tolist()}\n"
                f"prefix={prefix}"
            )


def test_vectorized_batch_of_beams(tokenizer) -> None:
    """Beams com estados diferentes no mesmo batch — máscaras independentes."""
    rng = random.Random(7)
    start = [tokenizer.token_id_bos, tokenizer.cls_name_to_token("articulationxl")]
    legacy, fast = _processors(tokenizer, start)
    vocab = tokenizer.vocab_size

    walks = [_random_valid_walk(tokenizer, start, steps=30, rng=rng)[-1] for _ in range(8)]
    L = min(len(w) for w in walks)
    if L == 0:
        pytest.skip("walk vazio")
    batch = torch.tensor([w[:L] for w in walks], dtype=torch.long)
    out_a = legacy(batch, torch.zeros(len(walks), vocab))
    out_b = fast(batch, torch.zeros(len(walks), vocab))
    assert bool((torch.isneginf(out_a) == torch.isneginf(out_b)).all())


def test_vectorized_is_faster_on_long_sequences(tokenizer) -> None:
    import time

    rng = random.Random(3)
    start = [tokenizer.token_id_bos, tokenizer.cls_name_to_token("articulationxl")]
    legacy, fast = _processors(tokenizer, start)
    vocab = tokenizer.vocab_size

    walk = _random_valid_walk(tokenizer, start, steps=900, rng=rng)[-1]
    batch = torch.tensor([walk] * 15, dtype=torch.long)  # 15 beams

    t0 = time.perf_counter()
    legacy(batch, torch.zeros(15, vocab))
    t_legacy = time.perf_counter() - t0

    fast(batch, torch.zeros(15, vocab))  # warmup (build tables)
    t0 = time.perf_counter()
    fast(batch, torch.zeros(15, vocab))
    t_fast = time.perf_counter() - t0

    assert t_fast < t_legacy / 5, f"esperado >=5x mais rápido: legacy={t_legacy:.4f}s fast={t_fast:.4f}s"
