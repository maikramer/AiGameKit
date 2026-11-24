"""Precisão do pedido: orçamento de tokens do CLIP, parâmetros explícitos e
pormenor isolado (o que o tiling repete em cada célula).

Cobre `texture2d.prompt_budget`, as métricas `score_isolated_detail`,
`score_structure` (bandas por eixo, cauda pesada) e `grading.match_mean_color`,
e a sua integração no gerador (re-roll com a seed seguinte, rotação do eixo,
graduação), no payload vramd, no adapter do worker e no CLI — tudo sem GPU
(pipeline falso).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import numpy
import pytest
import torch
from click.testing import CliRunner
from PIL import Image, ImageDraw, ImageFilter

from tests.test_generator import _make_gen
from texture2d.generator import DEFAULT_STEPS, SD_BASE_NEGATIVE, TextureGenerator
from texture2d.grading import format_hex_color, match_mean_color, parse_hex_color
from texture2d.prompt_budget import (
    CLIP_BUDGET,
    counter_for,
    estimate_tokens,
    fit_negative,
    fit_prompt,
    truncate_clauses,
)
from texture2d.tileability import (
    AxisBanding,
    IsolatedDetail,
    Structure,
    TailRatio,
    score_axis_banding,
    score_isolated_detail,
    score_tail_ratio,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _word_counter(text: str) -> int:
    """Contador determinístico: uma palavra = um token, mais BOS/EOS."""
    return len(text.split()) + 2 if text else 0


def _clauses(prefix: str, count: int) -> str:
    return ", ".join(f"{prefix}{index}" for index in range(count))


def _grain(size: int = 256) -> Image.Image:
    """Veio: ondas lentas + grão fino, tudo periódico (tileable por construção)."""
    axis = numpy.linspace(0.0, 2.0 * numpy.pi, size, dtype=numpy.float32)
    x = axis[None, :]
    y = axis[:, None]
    phase = 0.8 * numpy.sin(3.0 * x + 0.4) * numpy.cos(2.0 * y)
    grain = numpy.sin(46.0 * y + 1.7 * numpy.sin(2.0 * x) + phase)
    broad = numpy.sin(4.0 * x + 1.3) * numpy.cos(3.0 * y + 0.6)
    value = 128.0 + 26.0 * broad + 7.0 * grain
    channels = [value * 1.00 + 2.0, value * 0.86, value * 0.70 - 4.0]
    return Image.fromarray(numpy.stack(channels, axis=2).clip(0, 255).astype("uint8"), "RGB")


def _with_knot(base: Image.Image, at: tuple[int, int], radius: int = 7) -> Image.Image:
    """Um nó: disco corado com uma auréola clara à volta."""
    image = base.copy()
    size = image.size[0]
    x, y = at
    halo = Image.new("L", image.size, 0)
    ImageDraw.Draw(halo).ellipse((x - radius * 4, y - radius * 4, x + radius * 4, y + radius * 4), fill=255)
    halo = halo.filter(ImageFilter.GaussianBlur(radius * 1.5))
    alpha = (numpy.asarray(halo, dtype=numpy.float32) / 255.0 * 0.14)[..., None]
    lighter = numpy.asarray(image, dtype=numpy.float32)
    lighter = lighter + (255.0 - lighter) * alpha
    image = Image.fromarray(lighter.clip(0, 255).astype("uint8"), "RGB")
    for dx in (-size, 0, size):
        for dy in (-size, 0, size):
            ImageDraw.Draw(image).ellipse(
                (x + dx - radius, y + dy - radius, x + dx + radius, y + dy + radius), fill=(214, 92, 48)
            )
    return image


# ---------------------------------------------------------------------------
# Contagem de tokens
# ---------------------------------------------------------------------------


class TestTokenCounter:
    def test_clip_window_is_75_text_tokens(self):
        assert CLIP_BUDGET == 75

    def test_estimate_close_to_measured_clip_counts(self):
        """Medido com o tokenizador real do SD1.5: 78 e 83 tokens."""
        user = (
            "seamless tileable texture, rough hand-hewn oak wood surface, warm umber brown, "
            "open wood grain running top to bottom, shallow adze facets, subtle worn edges, "
            "wood grain texture, broad clean color zones, earthy mineral pigments, matte tempera finish, "
            "soft even lighting, flat top-down view, texture fills the whole frame, stylized game texture"
        )
        negative = (
            "planks, board seams, gaps between boards, nails, screws, knot, knots, isolated spot, "
            "blurry patch, out of focus, frame, border, text, watermark, low quality, plastic glossy sheen, "
            "photorealistic, neon oversaturated colors, busy micro-patterns, ornate filigree noise, "
            "melted organic edges, strong directional light, cast shadows, perspective view, 3d object"
        )
        # Sem tokenizador: a estimativa com a margem nunca fica abaixo do real
        # (uma cauda cortada) e passa pouco dele nas duas famílias de prompt.
        for text, real in ((user, 78), (negative, 83)):
            estimated = estimate_tokens(text) + 3
            assert real <= estimated <= 1.15 * real
        assert estimate_tokens("") == 0

    def test_counter_uses_the_pipeline_tokenizer(self):
        class _Tokenizer:
            def __call__(self, text: str) -> dict:
                return {"input_ids": [0, *text.split(), 2]}

        class _Pipe:
            tokenizer = _Tokenizer()

        assert counter_for(_Pipe())("a b c") == 5

    def test_counter_falls_back_without_a_tokenizer(self):
        count = counter_for(object())
        assert count("one two") == estimate_tokens("one two") + 3
        broken = type("P", (), {"tokenizer": lambda self, text: (_ for _ in ()).throw(RuntimeError("offline"))})()
        assert counter_for(broken)("one two") == estimate_tokens("one two") + 3


# ---------------------------------------------------------------------------
# fit_prompt
# ---------------------------------------------------------------------------


class TestFitPrompt:
    def test_legacy_text_when_everything_fits(self):
        fit = fit_prompt("red oak", "seamless wood texture", _word_counter)
        assert fit.text == "seamless wood texture, red oak"
        assert fit.tokens == 7
        assert not fit.user_truncated and not fit.dropped

    def test_user_keeps_its_tail_when_the_preset_would_push_it_out(self):
        user = _clauses("u", 10)  # 10 palavras + 2 = 12 tokens
        preset = _clauses("p", 3)  # ", p0, p1, p2" → 5 tokens com a vírgula
        fit = fit_prompt(user, preset, _word_counter, budget=13)
        # Cabe o utilizador inteiro e uma cláusula do preset; as outras saem.
        assert fit.text == f"{user}, p0"
        assert fit.tokens == 13
        assert fit.dropped == ("p1", "p2")
        assert not fit.user_truncated

    def test_user_alone_wins_when_the_preset_does_not_fit_at_all(self):
        user = _clauses("u", 10)
        fit = fit_prompt(user, _clauses("p", 3), _word_counter, budget=12)
        assert fit.text == user
        assert fit.dropped == ("p0", "p1", "p2")

    def test_long_user_is_cut_on_a_clause_boundary(self):
        fit = fit_prompt(_clauses("u", 10), None, _word_counter, budget=7)
        assert fit.text == _clauses("u", 5)
        assert fit.user_truncated

    def test_one_huge_clause_is_cut_by_words(self):
        fit = fit_prompt(" ".join(f"w{index}" for index in range(20)), None, _word_counter, budget=6)
        assert fit.text == "w0 w1 w2 w3"
        assert fit.user_truncated

    def test_truncate_clauses_reports_nothing_when_it_fits(self):
        text, truncated = truncate_clauses("a, b, c", _word_counter, budget=10)
        assert text == "a, b, c" and not truncated

    def test_empty_prompt_is_empty(self):
        assert fit_prompt("", "seamless wood texture", _word_counter).text == "seamless wood texture"
        assert fit_prompt("", None, _word_counter).text == ""


# ---------------------------------------------------------------------------
# fit_negative
# ---------------------------------------------------------------------------


class TestFitNegative:
    def test_groups_join_in_order_when_they_fit(self):
        fit = fit_negative([("base terms, x", 1), ("user terms", 2)], _word_counter, budget=50)
        assert fit.text == "base terms, x, user terms"
        assert not fit.dropped

    def test_duplicates_are_dropped_once_the_window_overflows(self):
        groups = [("blurry, low quality, seam", 1), ("blurry, low quality, distorted", 0), ("knots, planks", 2)]
        fit = fit_negative(groups, _word_counter, budget=50)
        assert fit.text == "blurry, low quality, seam, distorted, knots, planks"

    def test_base_stays_then_the_user_then_the_preset(self):
        """A base da ferramenta é o que torna a textura utilizável; o preset é o
        primeiro a ceder; o utilizador perde a cauda, nunca a cabeça."""
        base = "blurry, low quality, watermark, text, signature, frame, border, visible seam"
        user = _clauses("avoid", 4)
        fit = fit_negative([(base, 2), ("distorted", 0), (user, 1)], _word_counter, budget=15)
        assert fit.tokens <= 16
        assert fit.text.startswith(f"{base}, avoid0, avoid1, avoid2")
        assert "avoid3" in fit.dropped
        assert "distorted" in fit.dropped

    def test_a_flooded_negative_never_evicts_the_seam_terms(self):
        base = "blurry, low quality, visible seam"
        fit = fit_negative([(base, 2), ("distorted", 0), (_clauses("avoid", 40), 1)], _word_counter, budget=10)
        assert fit.text.startswith(f"{base}, avoid0")
        assert "distorted" in fit.dropped


# ---------------------------------------------------------------------------
# Pormenor isolado
# ---------------------------------------------------------------------------


class TestIsolatedDetail:
    def test_grain_has_no_isolated_detail(self):
        assert score_isolated_detail(_grain()).spot < 0.06

    def test_a_knot_is_flagged(self):
        report = score_isolated_detail(_with_knot(_grain(), (128, 128)))
        assert report.spot > 0.12
        assert report.band_max > report.band_p99

    @pytest.mark.parametrize("at", [(128, 128), (3, 250), (2, 2)])
    def test_the_seam_does_not_hide_a_knot(self, at: tuple[int, int]):
        """A textura é um toro: um nó na costura conta como um nó no meio."""
        centre = score_isolated_detail(_with_knot(_grain(), (128, 128))).spot
        edge = score_isolated_detail(_with_knot(_grain(), at)).spot
        assert edge > 0.12
        assert math.isclose(edge, centre, rel_tol=0.25)

    def test_a_flat_image_has_no_spot(self):
        assert score_isolated_detail(Image.new("RGB", (64, 64), (90, 70, 50))).spot == 0.0

    def test_too_small_raises(self):
        with pytest.raises(ValueError):
            score_isolated_detail(Image.new("RGB", (8, 8)))

    def test_summary_and_dict(self):
        report = IsolatedDetail(spot=0.2, band_max=0.3, band_p99=0.1)
        assert report.to_dict() == {"spot": 0.2, "band_max": 0.3, "band_p99": 0.1}
        assert "spot=0.2000" in report.summary()


# ---------------------------------------------------------------------------
# Gerador: preset x parâmetros explícitos, orçamento de tokens, re-roll
# ---------------------------------------------------------------------------


class TestExplicitParams:
    def test_preset_fills_everything_when_nothing_is_declared(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("test", seed=1, preset="Wood", ground="off")
        assert meta["guidance_scale"] == 7.5
        assert meta["num_inference_steps"] == 50
        assert meta["preset_params_applied"] == ["guidance_scale", "num_inference_steps"]

    def test_declared_step_wins_over_the_preset(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate(
                "test", seed=1, preset="Wood", ground="off", num_inference_steps=20, explicit={"num_inference_steps"}
            )
        assert meta["num_inference_steps"] == 20
        assert meta["guidance_scale"] == 7.5
        assert meta["preset_params_applied"] == ["guidance_scale"]

    def test_everything_declared_leaves_the_preset_quiet(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate(
                "test",
                seed=1,
                preset="Wood",
                ground="off",
                guidance_scale=4.0,
                explicit={"guidance_scale", "num_inference_steps"},
            )
        assert meta["guidance_scale"] == 4.0
        assert meta["num_inference_steps"] == DEFAULT_STEPS
        assert "preset_params_applied" not in meta


class TestPromptWindow:
    USER = (
        "rough hand-hewn oak wood surface, warm umber brown, open wood grain running top to bottom, "
        "shallow adze facets, subtle worn edges, broad clean color zones, earthy mineral pigments, "
        "matte tempera finish, soft even lighting, flat top-down view, texture fills the whole frame, "
        "stylized game texture"
    )

    def test_short_prompt_is_the_legacy_text(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("oak grain texture", seed=1, preset="Wood", ground="off")
        assert (
            meta["prompt_final"]
            == "seamless wood texture, natural grain, high detail, tiling pattern, oak grain texture"
        )
        assert meta["prompt_tokens"] <= CLIP_BUDGET
        assert "preset_dropped" not in meta and "prompt_user_truncated" not in meta

    def test_long_prompt_keeps_its_own_tail(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate(self.USER, seed=1, preset="Wood", ground="off")
        assert meta["prompt_tokens"] <= CLIP_BUDGET
        # O pedido do utilizador vem inteiro e à cabeça; o preset só empresta o que cabe.
        assert meta["prompt_final"].startswith(self.USER)
        preset = "seamless wood texture, natural grain, high detail, tiling pattern"
        assert meta["preset_dropped"]
        assert set(meta["preset_dropped"]) <= set(preset.split(", "))
        assert "prompt_user_truncated" not in meta

    def test_long_negative_keeps_the_user_and_the_seam_terms(self):
        gen, fake = _make_gen()
        negative = ", ".join(f"avoid{index}" for index in range(60))
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("oak texture", negative_prompt=negative, seed=1, preset="Wood", ground="off")
        assert meta["negative_prompt_tokens"] <= CLIP_BUDGET
        assert meta["negative_prompt"].startswith(f"{SD_BASE_NEGATIVE}, avoid0")
        assert "visible seam" in meta["negative_prompt"]
        assert "distorted" in meta["negative_dropped"]
        assert "avoid0" in meta["negative_prompt"]


class TestIsolatedDetailReroll:
    @staticmethod
    def _sequence(spots: list[float]):
        return [IsolatedDetail(spot=spot, band_max=spot + 0.1, band_p99=0.1) for spot in spots]

    def test_no_gate_generates_once(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch("texture2d.generator.score_isolated_detail", side_effect=self._sequence([0.9])),
        ):
            _, meta = gen.generate("wood texture", seed=10, ground="off")
        assert "isolated_detail_gate" not in meta
        assert "isolated_detail" in meta

    def test_failing_spot_moves_to_the_next_seed(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch("texture2d.generator.score_isolated_detail", side_effect=self._sequence([0.3, 0.05])),
        ):
            _, meta = gen.generate("wood texture", seed=10, ground="off", max_isolated_detail=0.12, attempts=4)
        assert meta["seed"] == 11
        assert meta["isolated_detail"]["spot"] == 0.05
        assert meta["isolated_detail_gate"] == {"max": 0.12, "passed": True}

    def test_none_passing_returns_the_best_and_says_so(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch("texture2d.generator.score_isolated_detail", side_effect=self._sequence([0.4, 0.2, 0.5])),
        ):
            _, meta = gen.generate("wood texture", seed=10, ground="off", max_isolated_detail=0.12, attempts=3)
        assert meta["seed"] == 11
        assert meta["seed_requested"] == 10
        assert meta["isolated_detail_gate"] == {"max": 0.12, "passed": False, "best_of": 3}

    def test_attempts_below_one_is_one(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch("texture2d.generator.score_isolated_detail", side_effect=self._sequence([0.9, 0.0])) as detail,
        ):
            gen.generate("wood texture", seed=10, ground="off", max_isolated_detail=0.12, attempts=0)
        assert detail.call_count == 1


# ---------------------------------------------------------------------------
# Política de decode: o tiling deixa manchas, só quando não há alternativa
# ---------------------------------------------------------------------------


class TestDecodePolicy:
    MIB = 1024**2

    @staticmethod
    def _cuda_gen(free_mib: int):
        """Um gerador "em CUDA" sem GPU, com a folga de VRAM simulada."""
        gen = TextureGenerator(device="cpu")
        gen.device = "cuda:0"
        gen.torch_dtype = torch.float16
        gen._vae_swapped = True
        cleared: list[int] = []
        gen._clear_cache = lambda: cleared.append(1)
        return (
            gen,
            cleared,
            patch(
                "torch.cuda.mem_get_info", return_value=(free_mib * TestDecodePolicy.MIB, 6141 * TestDecodePolicy.MIB)
            ),
        )

    def _decode(self, gen, latents, **kwargs):
        pipe = _make_gen()[1]
        with patch.object(gen, "_load_pipeline", return_value=pipe):
            return (*gen._decode_latents(pipe, latents, **kwargs), pipe)

    def test_a_gpu_with_room_decodes_integrally(self):
        gen, cleared, memory = self._cuda_gen(free_mib=4096)
        latents = torch.zeros(1, 4, 128, 128)
        with memory:
            _, tiled, pipe = self._decode(gen, latents, width=1024, height=1024, vae_tiling=None)
        assert tiled is False
        assert pipe.vae.decode_calls == [(128, 128)], "sem pad: decode integral"
        assert cleared == [1], "a folga mede-se depois de esvaziar o cache"

    def test_a_really_short_gpu_still_tiles(self):
        gen, cleared, memory = self._cuda_gen(free_mib=2048)
        latents = torch.zeros(1, 4, 128, 128)
        with memory:
            _, tiled, pipe = self._decode(gen, latents, width=1024, height=1024, vae_tiling=None)
        assert tiled is True
        assert pipe.vae.decode_calls == [(136, 136)], "pad circular de 4 latents de cada lado"
        assert cleared == [1]

    def test_an_explicit_choice_never_measures(self):
        gen, cleared, memory = self._cuda_gen(free_mib=2048)
        latents = torch.zeros(1, 4, 32, 32)
        with memory:
            _, tiled, integral = self._decode(gen, latents, width=256, height=256, vae_tiling=False)
            _, forced, tiles = self._decode(gen, latents, width=1024, height=1024, vae_tiling=True)
        assert tiled is False and forced is True
        assert cleared == [], "uma escolha explícita não mede a folga"
        assert integral.vae.decode_calls == [(32, 32)] and tiles.vae.decode_calls == [(40, 40)]

    def test_small_targets_never_measure(self):
        gen, cleared, memory = self._cuda_gen(free_mib=0)
        with memory:
            _, tiled, _ = self._decode(gen, torch.zeros(1, 4, 32, 32), width=256, height=256, vae_tiling=None)
        assert tiled is False and cleared == []

    def test_a_failing_probe_falls_back_to_tiling(self):
        gen, _, _ = self._cuda_gen(free_mib=8192)
        latents = torch.zeros(1, 4, 128, 128)
        with patch("torch.cuda.mem_get_info", side_effect=RuntimeError("sem driver")):
            _, tiled, _ = self._decode(gen, latents, width=1024, height=1024, vae_tiling=None)
        assert tiled is True


# ---------------------------------------------------------------------------
# Payload vramd, adapter do worker e CLI
# ---------------------------------------------------------------------------


class TestPayloadAndAdapter:
    def test_payload_carries_the_precision_keys(self):
        from texture2d.vramd_payload import build_generate_request

        payload = build_generate_request(
            prompt="wood",
            output="/tmp/w.png",
            preset="Wood",
            explicit=["num_inference_steps", "guidance_scale"],
            max_isolated_detail=0.12,
            max_banding=0.5,
            max_tail_ratio=5.0,
            axis="v",
            match_color="#232B24",
            attempts=4,
        )
        assert payload["explicit"] == ["guidance_scale", "num_inference_steps"]
        assert payload["max_isolated_detail"] == 0.12
        assert payload["max_banding"] == 0.5
        assert payload["max_tail_ratio"] == 5.0
        assert payload["axis"] == "v"
        assert payload["match_color"] == "#232B24"
        assert payload["attempts"] == 4

    def test_payload_omits_them_when_unset(self):
        from texture2d.vramd_payload import build_generate_request

        payload = build_generate_request(prompt="wood", output="/tmp/w.png", explicit=[])
        for key in (
            "explicit",
            "max_isolated_detail",
            "max_banding",
            "max_tail_ratio",
            "axis",
            "match_color",
            "attempts",
        ):
            assert key not in payload

    def test_worker_adapter_forwards_the_request(self):
        from texture2d.worker_serve_adapter import Adapter

        seen: dict = {}

        class _Model:
            def generate(self, **kwargs):
                seen.update(kwargs)
                return Image.new("RGB", (32, 32)), {"seed": 5}

        adapter = Adapter()
        with (
            patch.object(Adapter, "begin_generate", return_value=(None, 12, None, None)),
            patch.object(Adapter, "should_abort", return_value=False),
            patch.object(Adapter, "finish_response", return_value={"ok": True}),
            patch.object(Adapter, "report_progress"),
        ):
            adapter.generate(
                _Model(),
                {
                    "prompt": "wood",
                    "output": "/tmp/w.png",
                    "explicit": ["guidance_scale"],
                    "max_isolated_detail": 0.1,
                    "max_banding": 0.5,
                    "max_tail_ratio": 5,
                    "axis": "v",
                    "match_color": "#232B24",
                    "attempts": 3,
                },
            )
        assert seen["explicit"] == {"guidance_scale"}
        assert seen["max_isolated_detail"] == 0.1
        assert seen["max_banding"] == 0.5
        assert seen["max_tail_ratio"] == 5
        assert seen["axis"] == "v"
        assert seen["match_color"] == "#232B24"
        assert seen["attempts"] == 3

    def test_worker_adapter_without_the_keys_keeps_the_old_contract(self):
        from texture2d.worker_serve_adapter import Adapter

        seen: dict = {}

        class _Model:
            def generate(self, **kwargs):
                seen.update(kwargs)
                return Image.new("RGB", (32, 32)), {"seed": 5}

        with (
            patch.object(Adapter, "begin_generate", return_value=(None, 12, None, None)),
            patch.object(Adapter, "should_abort", return_value=False),
            patch.object(Adapter, "finish_response", return_value={"ok": True}),
            patch.object(Adapter, "report_progress"),
        ):
            Adapter().generate(_Model(), {"prompt": "wood", "output": "/tmp/w.png"})
        assert seen["explicit"] is None
        assert seen["max_isolated_detail"] is None
        assert seen["max_banding"] is None
        assert seen["max_tail_ratio"] is None
        assert seen["axis"] is None
        assert seen["match_color"] is None
        assert seen["attempts"] == 1


class TestCli:
    @staticmethod
    def _fake_generator(calls: dict):
        class _FakeGen:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def warmup(self):
                return None

            def generate(self, **kwargs):
                calls["generate"] = kwargs
                return Image.new("RGB", (64, 64)), {
                    "seed": kwargs.get("seed") or 1,
                    "prompt_final": "wood",
                    "tileability": {"score": 0.99},
                    "isolated_detail": {"spot": 0.02},
                    "prompt_tokens": 20,
                    "negative_prompt_tokens": 20,
                }

        return _FakeGen

    def test_help_lists_the_new_flags(self):
        result = CliRunner().invoke(_cli(), ["generate", "--help"])
        assert result.exit_code == 0
        for flag in (
            "--max-isolated-detail",
            "--max-banding",
            "--max-tail-ratio",
            "--axis",
            "--match-color",
            "--attempts",
        ):
            assert flag in result.output

    def test_declared_flags_and_a_chosen_tier_beat_the_preset(self, tmp_path: Path):
        calls: dict = {}
        with patch("texture2d.cli.TextureGenerator", self._fake_generator(calls)):
            result = CliRunner().invoke(
                _cli(),
                [
                    "generate",
                    "oak",
                    "-o",
                    str(tmp_path / "oak.png"),
                    "--cpu",
                    "--preset",
                    "wood",
                    "--quality",
                    "high",
                    "-s",
                    "20",
                    "--max-isolated-detail",
                    "0.12",
                    "--max-banding",
                    "0.5",
                    "--max-tail-ratio",
                    "5",
                    "--axis",
                    "v",
                    "--match-color",
                    "#232B24",
                    "--attempts",
                    "4",
                ],
            )
        assert result.exit_code == 0, result.output
        sent = calls["generate"]
        assert sent["explicit"] == {"num_inference_steps", "guidance_scale", "width", "height"}
        assert sent["num_inference_steps"] == 20
        assert sent["max_isolated_detail"] == 0.12
        assert sent["max_banding"] == 0.5
        assert sent["max_tail_ratio"] == 5.0
        assert sent["axis"] == "v"
        assert sent["match_color"] == "#232B24"
        assert sent["attempts"] == 4

    def test_only_a_preset_declares_nothing(self, tmp_path: Path):
        calls: dict = {}
        with patch("texture2d.cli.TextureGenerator", self._fake_generator(calls)):
            result = CliRunner().invoke(
                _cli(), ["generate", "oak", "-o", str(tmp_path / "oak.png"), "--cpu", "--preset", "wood"]
            )
        assert result.exit_code == 0, result.output
        assert calls["generate"]["explicit"] == set()
        # O passo seguinte é do gerador: sem nada declarado, o preset ganha (50 passos).
        assert calls["generate"]["preset"] == "Wood"
        assert calls["generate"]["max_isolated_detail"] is None
        assert calls["generate"]["max_banding"] is None
        assert calls["generate"]["max_tail_ratio"] is None
        assert calls["generate"]["axis"] is None
        assert calls["generate"]["match_color"] is None
        assert calls["generate"]["attempts"] == 1

    def test_vramd_payload_carries_the_declared_params(self):
        captured: dict = {}

        def _delegate(tool: str, **kwargs):
            captured.update(kwargs)
            return True

        with patch("texture2d.cli.delegate_or_prepare", _delegate):
            result = CliRunner().invoke(
                _cli(),
                [
                    "generate",
                    "oak",
                    "-o",
                    "/tmp/vael-oak.png",
                    "--preset",
                    "wood",
                    "-g",
                    "6.5",
                    "--max-isolated-detail",
                    "0.12",
                    "--max-banding",
                    "0.5",
                    "--max-tail-ratio",
                    "5",
                    "--axis",
                    "v",
                    "--match-color",
                    "#232B24",
                    "--attempts",
                    "3",
                ],
            )
        assert result.exit_code == 0, result.output
        assert captured["payload"]["explicit"] == ["guidance_scale"]
        assert captured["payload"]["max_isolated_detail"] == 0.12
        assert captured["payload"]["max_banding"] == 0.5
        assert captured["payload"]["max_tail_ratio"] == 5.0
        assert captured["payload"]["axis"] == "v"
        assert captured["payload"]["match_color"] == "#232B24"
        assert captured["payload"]["attempts"] == 3

    def test_validate_tileable_reports_the_spot(self, tmp_path: Path):
        from texture2d._validate_cli import validate_tileable_cmd

        buf: list[str] = []

        class _Sink:
            def print_json(self, text: str):
                buf.append(text)

        clean = tmp_path / "clean.png"
        _grain().save(clean)
        knotted = tmp_path / "knotted.png"
        _with_knot(_grain(), (128, 128)).save(knotted)
        with patch("texture2d._validate_cli.console", _Sink()):
            assert (
                CliRunner()
                .invoke(validate_tileable_cmd, [str(clean), "--json", "--max-isolated-detail", "0.12"])
                .exit_code
                == 0
            )
            assert (
                CliRunner()
                .invoke(validate_tileable_cmd, [str(knotted), "--json", "--max-isolated-detail", "0.12"])
                .exit_code
                == 1
            )
        clean_report = json.loads(buf[0])
        knotted_report = json.loads(buf[1])
        assert clean_report["isolated_detail"]["spot"] < 0.12 <= knotted_report["isolated_detail"]["spot"]
        assert clean_report["max_isolated_detail"] == 0.12

    def test_validate_tileable_without_the_threshold_keeps_the_exit_code(self, tmp_path: Path):
        from texture2d._validate_cli import validate_tileable_cmd

        knotted = tmp_path / "knotted.png"
        # Custo zero: o nó no meio de um veio periódico (tileable por construção).
        _with_knot(_grain(), (128, 128)).save(knotted)
        with patch("texture2d._validate_cli.console", type("_Sink", (), {"print_json": lambda self, text: None})()):
            assert CliRunner().invoke(validate_tileable_cmd, [str(knotted), "--json"]).exit_code == 0

    def test_validate_tileable_fails_on_gaps_and_flecks(self, tmp_path: Path):
        from texture2d._validate_cli import validate_tileable_cmd

        buf: list[str] = []

        class _Sink:
            def print_json(self, text: str):
                buf.append(text)

        planks = tmp_path / "planks.png"
        _lines(horizontal=True).save(planks)
        flecks = tmp_path / "flecks.png"
        _flecks().save(flecks)
        with patch("texture2d._validate_cli.console", _Sink()):
            assert (
                CliRunner().invoke(validate_tileable_cmd, [str(planks), "--json", "--max-banding", "0.5"]).exit_code
                == 1
            )
            assert (
                CliRunner().invoke(validate_tileable_cmd, [str(flecks), "--json", "--max-tail-ratio", "5"]).exit_code
                == 1
            )
            assert CliRunner().invoke(validate_tileable_cmd, [str(planks), "--json"]).exit_code == 0
        report = json.loads(buf[0])
        assert report["banding"]["rows"] > 0.5
        assert report["max_banding"] == 0.5
        assert json.loads(buf[1])["tail_ratio"]["high"] > 8
        assert json.loads(buf[1])["max_tail_ratio"] == 5


def _cli():
    from texture2d.cli import cli

    return cli


# ---------------------------------------------------------------------------
# Estrutura: bandas por eixo (tábuas, veio deitado) e cauda pesada (flocos)
# ---------------------------------------------------------------------------


def _lines(horizontal: bool, size: int = 256) -> Image.Image:
    """Veio com frestas: linhas escuras de 8 px a cada 96 px, num sentido ou noutro."""
    base = numpy.asarray(_grain(size), dtype=numpy.float32)
    for at in range(24, size, 96):
        rows, cols = (slice(at, at + 8), slice(None)) if horizontal else (slice(None), slice(at, at + 8))
        base[rows, cols] *= 0.55
    return Image.fromarray(base.clip(0, 255).astype("uint8"), "RGB")


def _flecks(count: int = 12, dark: bool = False, size: int = 256) -> Image.Image:
    """Um fundo liso com poucos flocos espalhados (claros, ou escuros com ``dark``).

    Como na cal com flocos: ~1% da área, para o percentil 95 ficar no fundo.
    """
    rng = numpy.random.default_rng(7)
    value = 150.0 + rng.normal(0.0, 1.5, (size, size))
    arr = numpy.stack([value, value * 0.97, value * 0.9], axis=2)
    image = Image.fromarray(arr.clip(0, 255).astype("uint8"), "RGB")
    for _ in range(count):
        x, y = (int(step) for step in rng.integers(12, size - 12, 2))
        ImageDraw.Draw(image).ellipse((x - 5, y - 5, x + 5, y + 5), fill=(40, 34, 28) if dark else (245, 238, 220))
    return image


def _mean(image: Image.Image) -> numpy.ndarray:
    return numpy.asarray(image, dtype=numpy.float64).reshape(-1, 3).mean(axis=0)


class TestAxisBanding:
    def test_horizontal_gaps_show_up_in_the_rows(self):
        banding = score_axis_banding(_lines(horizontal=True))
        assert banding.rows > 0.5
        assert banding.rows > 3 * banding.cols

    def test_vertical_gaps_show_up_in_the_columns(self):
        banding = score_axis_banding(_lines(horizontal=False))
        assert banding.cols > 0.5
        assert banding.cols > 3 * banding.rows

    def test_rotating_swaps_the_axes(self):
        turned = score_axis_banding(_lines(horizontal=True).transpose(Image.Transpose.ROTATE_90))
        assert turned.cols > 0.5
        assert turned.cols > 3 * turned.rows

    def test_speckles_do_not_count_as_bands(self):
        assert score_axis_banding(_flecks()).worst < 0.5

    def test_a_flat_image_has_no_banding(self):
        assert score_axis_banding(Image.new("RGB", (64, 64), (90, 70, 50))) == AxisBanding(rows=0.0, cols=0.0)

    def test_too_small_raises(self):
        with pytest.raises(ValueError):
            score_axis_banding(Image.new("RGB", (12, 12)))

    def test_summary_and_dict(self):
        banding = AxisBanding(rows=0.8, cols=0.2)
        assert banding.worst == 0.8
        assert "rows=0.800" in banding.summary()
        assert banding.to_dict() == {"rows": 0.8, "cols": 0.2}


class TestTailRatio:
    def test_flecks_make_a_heavy_tail(self):
        tail = score_tail_ratio(_flecks())
        assert tail.high > 8
        assert tail.low < 5

    def test_dark_specks_count_on_the_dark_side(self):
        tail = score_tail_ratio(_flecks(dark=True))
        assert tail.low > 8
        assert tail.high < 5

    def test_dense_grain_has_a_bell_shaped_tail(self):
        tail = score_tail_ratio(_grain())
        assert 1.0 < tail.worst < 5.0

    def test_a_flat_image_has_no_tail(self):
        assert score_tail_ratio(Image.new("RGB", (64, 64), (90, 70, 50))) == TailRatio(high=0.0, low=0.0)

    def test_too_small_raises(self):
        with pytest.raises(ValueError):
            score_tail_ratio(Image.new("RGB", (12, 12)))

    def test_summary_and_dict(self):
        structure = Structure(banding=AxisBanding(0.8, 0.2), tail=TailRatio(24.0, 4.0))
        assert structure.tail.worst == 24.0
        assert "high=24.00" in structure.tail.summary()
        assert structure.to_dict() == {"banding": {"rows": 0.8, "cols": 0.2}, "tail_ratio": {"high": 24.0, "low": 4.0}}


# ---------------------------------------------------------------------------
# Cor média pedida (o SD1.5 sai sempre castanho a meia luminância)
# ---------------------------------------------------------------------------

PINE = (35.0, 43.0, 36.0)  # #232B24


class TestColorMatch:
    def test_parse_hex_color(self):
        assert parse_hex_color("#7A5C3A") == (122.0, 92.0, 58.0)
        assert parse_hex_color("7a5c3a") == (122.0, 92.0, 58.0)
        assert parse_hex_color("#ABC") == (170.0, 187.0, 204.0)
        for bad in ("#7A5C", "#GGGGGG", "", "marrom"):
            with pytest.raises(ValueError):
                parse_hex_color(bad)

    def test_format_hex_color(self):
        assert format_hex_color((122, 92.2, 58)) == "#7A5C3A"

    def test_the_mean_lands_on_the_target(self):
        graded, info = match_mean_color(_grain(), "#232B24")
        assert numpy.allclose(_mean(graded), PINE, atol=0.5)
        assert info.target == "#232B24"
        assert info.after == "#232B24"
        assert info.before == format_hex_color(tuple(_mean(_grain())))
        assert graded.mode == "RGB" and graded.size == (256, 256)

    def test_structure_is_kept_when_it_fits(self):
        base = _grain()
        graded, info = match_mean_color(base, "#232B24")
        assert info.scale == 1.0
        before = numpy.asarray(base, dtype=numpy.float64) - _mean(base)
        after = numpy.asarray(graded, dtype=numpy.float64) - _mean(graded)
        assert numpy.allclose(before, after, atol=0.51), "só o arredondamento a uint8"

    def test_contrast_is_compressed_to_reach_an_extreme(self):
        _, very_dark = match_mean_color(_grain(), "#101210")
        assert very_dark.scale < 0.6
        ramp = numpy.tile(numpy.linspace(0.0, 255.0, 128), (128, 1))
        wide = Image.fromarray(numpy.stack([ramp, ramp * 0.8, ramp * 0.6 + 10.0], axis=2).astype("uint8"), "RGB")
        graded, bright = match_mean_color(wide, "#F0E8D0")
        assert bright.scale < 0.2
        assert numpy.allclose(_mean(graded), (240.0, 232.0, 208.0), atol=0.5)
        assert numpy.asarray(graded).min() >= 0.0 and numpy.asarray(graded).max() <= 255

    def test_a_flat_image_stays_flat(self):
        flat = Image.new("RGB", (32, 32), (90, 70, 50))
        graded, info = match_mean_color(flat, "#5A4632")
        assert info.scale == 1.0
        assert list(numpy.asarray(graded).reshape(-1, 3)[0]) == [90, 70, 50]

    def test_a_tuple_target_and_a_bad_one(self):
        _, info = match_mean_color(_grain(), PINE)
        assert info.target == "#232B24"
        with pytest.raises(ValueError):
            match_mean_color(_grain(), "#232B2")

    def test_summary_and_dict(self):
        _, info = match_mean_color(_grain(), "#232B24")
        assert "→ #232B24" in info.summary()
        assert info.to_dict() == {
            "target": "#232B24",
            "before": info.before,
            "after": "#232B24",
            "scale": 1.0,
        }


# ---------------------------------------------------------------------------
# Portões de qualidade no gerador: bandas, cauda, eixo e cor
# ---------------------------------------------------------------------------


def _structure(rows: float = 0.2, cols: float = 0.2, high: float = 2.0, low: float = 2.0) -> Structure:
    return Structure(banding=AxisBanding(rows=rows, cols=cols), tail=TailRatio(high=high, low=low))


class TestQualityGates:
    @staticmethod
    def _sequence(structures: list[Structure]):
        return [Structure(item.banding, item.tail) for item in structures]

    def test_banding_re_rolls_until_the_gaps_are_gone(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch(
                "texture2d.generator.score_structure", side_effect=self._sequence([_structure(rows=0.87), _structure()])
            ),
        ):
            _, meta = gen.generate("wood texture", seed=10, ground="off", max_banding=0.5, attempts=4)
        assert meta["seed"] == 11
        assert meta["quality_gates"] == {
            "passed": True,
            "banding": {"value": 0.2, "max": 0.5, "passed": True},
        }

    def test_flecks_re_roll_on_the_tail_gate(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch(
                "texture2d.generator.score_structure", side_effect=self._sequence([_structure(high=28.0), _structure()])
            ),
        ):
            _, meta = gen.generate("wood texture", seed=10, ground="off", max_tail_ratio=5.0, attempts=4)
        assert meta["seed"] == 11
        assert meta["tail_ratio"] == {"high": 2.0, "low": 2.0}

    def test_several_gates_pick_the_closest_overall(self):
        spots = [IsolatedDetail(spot=spot, band_max=spot, band_p99=0.0) for spot in (0.3, 0.13, 0.9)]
        structures = self._sequence([_structure(rows=0.2), _structure(rows=0.49), _structure(rows=0.1)])
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch("texture2d.generator.score_isolated_detail", side_effect=spots),
            patch("texture2d.generator.score_structure", side_effect=structures),
        ):
            _, meta = gen.generate(
                "wood texture", seed=10, ground="off", max_isolated_detail=0.12, max_banding=0.5, attempts=3
            )
        # 2,5 · 1,08 · 7,5: a segunda (seed 11) é a que passa mais perto, embora nenhuma passe.
        assert meta["seed"] == 11
        assert meta["seed_requested"] == 10
        assert meta["quality_gates"]["passed"] is False
        assert meta["quality_gates"]["best_of"] == 3
        assert meta["quality_gates"]["banding"] == {"value": 0.49, "max": 0.5, "passed": True}
        assert meta["isolated_detail_gate"] == {"max": 0.12, "passed": False, "best_of": 3}

    def test_without_gates_nothing_is_recorded(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("wood texture", seed=10, ground="off")
        assert "quality_gates" not in meta
        assert meta["banding"]["rows"] >= 0.0 and meta["tail_ratio"]["high"] >= 0.0

    def test_axis_v_rotates_a_horizontal_texture(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch.object(gen, "_generate_and_decode", return_value=(_lines(horizontal=True), False)),
        ):
            image, meta = gen.generate("wood texture", seed=10, ground="off", axis="v")
        assert meta["axis"] == {"wanted": "v", "rotated": True}
        assert score_axis_banding(image).cols > score_axis_banding(image).rows

    def test_axis_u_is_the_other_way_round(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch.object(gen, "_generate_and_decode", return_value=(_lines(horizontal=False), False)),
        ):
            image, meta = gen.generate("wood texture", seed=10, ground="off", axis="u")
        assert meta["axis"] == {"wanted": "u", "rotated": True}
        assert score_axis_banding(image).rows > score_axis_banding(image).cols

    def test_a_texture_without_a_dominant_axis_is_left_alone(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch("texture2d.generator.score_structure", return_value=_structure(rows=0.17, cols=0.08)),
        ):
            _, meta = gen.generate("wood texture", seed=10, ground="off", axis="v")
        assert meta["axis"] == {"wanted": "v", "rotated": False}

    def test_no_axis_never_rotates(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch("texture2d.generator.score_structure", return_value=_structure(rows=0.9, cols=0.1)),
        ):
            _, meta = gen.generate("wood texture", seed=10, ground="off")
        # Os metadados não trazem chaves a None.
        assert "axis" not in meta

    def test_an_invalid_axis_or_color_raises_before_generating(self):
        gen, _ = _make_gen()
        with patch.object(gen, "_generate_once") as once:
            with pytest.raises(ValueError, match="axis"):
                gen.generate("wood texture", seed=10, ground="off", axis="diagonal")
            with pytest.raises(ValueError, match="Cor inválida"):
                gen.generate("wood texture", seed=10, ground="off", match_color="dark pine")
        once.assert_not_called()

    def test_match_color_grades_the_output(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch.object(gen, "_generate_and_decode", return_value=(_grain(), False)),
        ):
            image, meta = gen.generate("wood texture", seed=10, ground="off", match_color="#232B24")
        assert meta["match_color"]["target"] == "#232B24"
        assert meta["match_color"]["after"] == "#232B24"
        assert numpy.allclose(_mean(image), PINE, atol=0.5)
