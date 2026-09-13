"""Testes para texture2d.generator (SD1.5 + seamless 2.0)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import numpy
import pytest
import torch
from PIL import Image

from texture2d.generator import (
    DEFAULT_GUIDANCE,
    DEFAULT_MODEL_ID,
    DEFAULT_STEPS,
    SD_BASE_NEGATIVE,
    SEAMLESS_LATE_FRACTION,
    SEAMLESS_MODES,
    TextureGenerator,
    default_model_id,
    heal_seam,
    merge_negative_prompt,
    patch_conv2d_circular,
    set_conv2d_padding,
)

# ---------------------------------------------------------------------------
# patch_conv2d_circular
# ---------------------------------------------------------------------------


class TestPatchConv2dCircular:
    def test_patches_all_nested_convs(self):
        model = torch.nn.Sequential(
            torch.nn.Conv2d(3, 8, 3, padding=1),
            torch.nn.Sequential(torch.nn.Conv2d(8, 8, 3, padding=1), torch.nn.ReLU()),
        )
        count = patch_conv2d_circular(model)
        assert count == 2
        for m in model.modules():
            if isinstance(m, torch.nn.Conv2d):
                assert m.padding_mode == "circular"

    def test_output_wraps_at_borders(self):
        """Com padding circular, a conv de uma imagem constante por colunas
        produz bordas esquerda/direita contínuas (sem efeito de borda zero)."""
        conv = torch.nn.Conv2d(1, 1, 3, padding=1, bias=False)
        torch.nn.init.constant_(conv.weight, 1.0)
        x = torch.ones(1, 1, 8, 8)
        y_zero = conv(x)
        patch_conv2d_circular(conv)
        y_circ = conv(x)
        # Zero padding atenua as bordas; circular mantém o valor uniforme.
        assert y_zero[0, 0, 0, 0] < y_circ[0, 0, 0, 0]
        assert torch.allclose(y_circ, torch.full_like(y_circ, 9.0))

    def test_ignores_non_conv_layers(self):
        model = torch.nn.Sequential(torch.nn.Linear(4, 4), torch.nn.ReLU())
        assert patch_conv2d_circular(model) == 0


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


class TestMergeNegativePrompt:
    def test_only_preset(self):
        assert merge_negative_prompt("blurry", "") == "blurry"

    def test_only_user(self):
        assert merge_negative_prompt("", "low quality") == "low quality"

    def test_both_different(self):
        m = merge_negative_prompt("a", "b")
        assert "a" in m and "b" in m

    def test_subset_dedup(self):
        assert merge_negative_prompt("blur", "no blur please") == "no blur please"


class TestDefaultModelId:
    def test_default(self):
        assert default_model_id() == DEFAULT_MODEL_ID

    @patch.dict("os.environ", {"TEXTURE2D_MODEL_ID": "custom/model"})
    def test_env_override(self):
        assert default_model_id() == "custom/model"


# ---------------------------------------------------------------------------
# TextureGenerator.generate (com pipeline mocked)
# ---------------------------------------------------------------------------


class _FakeVAE:
    """VAE mínimo: decode (1,4,h,w)→(1,3,H,W) em [-1,1] + flags de tiling."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(scaling_factor=1.0)
        self.tiled = False
        self.decode_calls: list[tuple[int, int]] = []
        self.decode_dtypes: list[torch.dtype] = []
        self.to_calls: list = []

    def enable_tiling(self) -> None:
        self.tiled = True

    def disable_tiling(self) -> None:
        self.tiled = False

    def to(self, *args, **kwargs):
        self.to_calls.append(args[0] if args else kwargs.get("dtype"))
        return self

    def decode(self, z, return_dict: bool = False):
        import torch.nn.functional as F

        self.decode_calls.append((int(z.shape[-2]), int(z.shape[-1])))
        self.decode_dtypes.append(z.dtype)
        up = F.interpolate(z, scale_factor=8, mode="nearest")
        return (up,)


class _FakeImageProcessor:
    @staticmethod
    def postprocess(sample, output_type: str = "pil"):
        arr = ((sample[0].permute(1, 2, 0).float().numpy() + 1.0) * 127.5).clip(0, 255).astype("uint8")
        return [Image.fromarray(arr)]


class _FakePipe:
    """Pipeline que devolve latents (output_type=latent) com decode controlado."""

    def __init__(self) -> None:
        self.last_kwargs: dict | None = None
        self.vae = _FakeVAE()
        self.image_processor = _FakeImageProcessor()

    def __call__(self, **kwargs):
        self.last_kwargs = kwargs
        w = int(kwargs.get("width", 512))
        h = int(kwargs.get("height", 512))
        return SimpleNamespace(images=[torch.zeros(1, 4, h // 8, w // 8)])


def _make_gen() -> tuple[TextureGenerator, _FakePipe]:
    gen = TextureGenerator(device="cpu")
    fake = _FakePipe()
    return gen, fake


class TestGenerate:
    def test_generate_returns_image_and_metadata(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            image, meta = gen.generate("red woven fabric", seed=42, width=256, height=256, ground="off")
        assert image.size == (256, 256)
        assert meta["backend"] == "sd-circular"
        assert meta["seed"] == 42
        assert "seamless texture" in meta["prompt_final"]
        assert meta["seamless_mode"] == "late"
        assert isinstance(meta["tileability"], dict)

    def test_base_negative_always_applied(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("brick wall texture", seed=1, ground="off")
        assert SD_BASE_NEGATIVE in meta["negative_prompt"]

    def test_ground_mode_enhances_prompt_and_negative(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("green grass", seed=1, ground="on")
        assert "top-down" in meta["prompt_final"]
        assert "isometric" in meta["negative_prompt"]

    def test_legacy_kwargs_ignored(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate(
                "wood planks texture",
                seed=7,
                lora_strength=1.0,
                true_cfg_scale=2.0,
                ground="off",
            )
        assert meta["seed"] == 7
        assert "lora_strength" not in fake.last_kwargs

    def test_random_seed_when_none(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("sand texture", seed=None, ground="off")
        assert isinstance(meta["seed"], int)
        assert meta["seed"] >= 0

    def test_preset_applied(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("test", seed=1, preset="Wood", ground="off")
        # O preset Wood prefixa o prompt base.
        assert "seamless wood texture" in meta["prompt_final"]
        assert meta["guidance_scale"] == 7.5

    def test_default_guidance_is_sd(self):
        """Default guidance deve ser 7.0 (CFG real do SD1.5), não 3.5 (FLUX)."""
        assert DEFAULT_GUIDANCE == 7.0
        assert DEFAULT_STEPS == 30

    @patch.object(TextureGenerator, "_load_pipeline")
    def test_generate_returns_image_via_mocked_pipe(self, mock_load):
        fake_pipe = _FakePipe()
        mock_load.return_value = fake_pipe

        gen = TextureGenerator(device="cpu")
        image, metadata = gen.generate(
            prompt="test stone",
            width=256,
            height=256,
            num_inference_steps=10,
            seed=1,
            ground="off",
        )
        assert isinstance(image, Image.Image)
        assert image.size == (256, 256)
        assert metadata["seed"] == 1
        # O pipeline corre com output_type=latent (decode controlado pela tool).
        assert fake_pipe.last_kwargs["output_type"] == "latent"
        assert fake_pipe.last_kwargs["callback_on_step_end_tensor_inputs"] == ["latents"]


# ---------------------------------------------------------------------------
# generate_batch (herdado da base — error continuation + seed increment)
# ---------------------------------------------------------------------------


class TestGenerateBatch:
    def test_generate_batch_continues_on_error(self):
        class FlakyPipe(_FakePipe):
            calls = 0

            def __call__(self, **kwargs):
                FlakyPipe.calls += 1
                if FlakyPipe.calls == 1:
                    raise RuntimeError("boom")
                return super().__call__(**kwargs)

        gen = TextureGenerator(device="cpu")
        with patch.object(gen, "_load_pipeline", return_value=FlakyPipe()):
            results = list(gen.generate_batch(["a texture", "b texture"], ground="off"))
        assert len(results) == 2
        assert results[0][0] is None
        assert "boom" in results[0][1]["error"]
        assert results[1][0] is not None


# ---------------------------------------------------------------------------
# Init / construction
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_defaults(self):
        gen = TextureGenerator(device="cpu")
        assert gen.model_id == DEFAULT_MODEL_ID
        assert gen.device == "cpu"
        # CPU usa float32 (base default), CUDA usa float16 (override do SD).
        assert gen.torch_dtype == torch.float32

    def test_init_dtype_fp16_on_cuda(self):
        """Mesmo que a base devolva bfloat16 em CUDA, o SD força float16.

        Sem CUDA real a base faz fallback ``cuda``→``cpu`` (float32) — skip.
        """
        if not torch.cuda.is_available():
            import pytest

            pytest.skip("CUDA required for fp16 dtype assertion")
        gen = TextureGenerator(device="cuda")
        assert gen.device.startswith("cuda")
        assert gen.torch_dtype == torch.float16


# ---------------------------------------------------------------------------
# Seamless 2.0: modos, callback late (roll + switch), decode, hires, heal
# ---------------------------------------------------------------------------


class TestSeamlessModes:
    def test_modes_contract(self):
        assert SEAMLESS_MODES == ("late", "full", "off")
        assert 0 < SEAMLESS_LATE_FRACTION < 1

    def test_invalid_mode_raises(self):
        gen, _ = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=_FakePipe()),
            pytest.raises(ValueError, match="seamless_mode"),
        ):
            gen.generate("stone", seamless_mode="banana", ground="off")

    def test_negative_has_seam_terms(self):
        assert "visible seam" in SD_BASE_NEGATIVE
        assert "tiling artifacts" in SD_BASE_NEGATIVE


class TestLateCallback:
    def _gen_with_conv(self) -> tuple[TextureGenerator, torch.nn.Conv2d]:
        gen = TextureGenerator(device="cpu")
        conv = torch.nn.Conv2d(3, 3, 3, padding=1)
        gen._unet_convs = [conv]
        return gen, conv

    def test_rolls_then_switches_even_parity(self):
        gen, conv = self._gen_with_conv()
        cb = gen._make_step_callback(
            total_steps=10,
            seamless_mode="late",
            progress_total=10,
            progress_offset=0,
            should_abort=None,
            on_step=None,
        )
        lat0 = torch.randn(1, 4, 32, 32)
        kw = {"latents": lat0.clone()}
        kw = cb(None, 0, None, kw)  # 1 roll: orientação invertida
        assert not torch.equal(kw["latents"], lat0)
        assert conv.padding_mode == "zeros"
        for i in range(1, 8):  # steps 1..7 (total 8 rolls, par)
            kw = cb(None, i, None, kw)
            assert conv.padding_mode == "zeros"
        kw = cb(None, 8, None, kw)  # switch: 8 rolls (par) → sem roll extra
        assert conv.padding_mode == "circular"
        assert torch.equal(kw["latents"], lat0)  # orientação original reposta

    def test_rolls_then_switches_odd_parity(self):
        gen, conv = self._gen_with_conv()
        cb = gen._make_step_callback(
            total_steps=9,
            seamless_mode="late",
            progress_total=9,
            progress_offset=0,
            should_abort=None,
            on_step=None,
        )
        lat0 = torch.randn(1, 4, 32, 32)
        kw = {"latents": lat0.clone()}
        for i in range(7):  # steps 0..6: 7 rolls (ímpar)
            kw = cb(None, i, None, kw)
        kw = cb(None, 7, None, kw)  # switch_idx = int(9*0.8) = 7
        assert conv.padding_mode == "circular"
        assert torch.equal(kw["latents"], lat0)

    def test_full_mode_never_rolls(self):
        gen, _conv = self._gen_with_conv()
        cb = gen._make_step_callback(
            total_steps=10,
            seamless_mode="full",
            progress_total=10,
            progress_offset=0,
            should_abort=None,
            on_step=None,
        )
        lat0 = torch.randn(1, 4, 32, 32)
        kw = cb(None, 0, None, {"latents": lat0.clone()})
        assert torch.equal(kw["latents"], lat0)

    def test_progress_and_abort(self):
        import pytest as _pytest

        from aigamekit_shared.diffusion_control import GenerationAborted

        gen, _ = self._gen_with_conv()
        seen: list[tuple[int, int]] = []
        cb = gen._make_step_callback(
            total_steps=10,
            seamless_mode="off",
            progress_total=10,
            progress_offset=0,
            should_abort=lambda: True,
            on_step=lambda cur, total: seen.append((cur, total)),
        )
        kw = {"latents": torch.zeros(1, 4, 8, 8)}
        with _pytest.raises(GenerationAborted):
            cb(None, 3, None, kw)
        assert seen == [(4, 10)]


class TestSetConv2dPadding:
    def test_toggles_both_ways(self):
        model = torch.nn.Sequential(torch.nn.Conv2d(3, 8, 3, padding=1), torch.nn.Conv2d(8, 8, 3, padding=1))
        assert set_conv2d_padding(model, "circular") == 2
        assert all(m.padding_mode == "circular" for m in model.modules() if isinstance(m, torch.nn.Conv2d))
        assert set_conv2d_padding(model, "zeros") == 2
        assert all(m.padding_mode == "zeros" for m in model.modules() if isinstance(m, torch.nn.Conv2d))


class TestHealSeam:
    def test_improves_score_on_hard_seam(self):
        rng = numpy.random.default_rng(7)
        width = height = 128
        xs = numpy.arange(width, dtype=numpy.float32)
        base = numpy.sin(2 * numpy.pi * xs / width * 3.0)
        arr = numpy.stack([base] * 3, axis=-1)[None, :, :] * numpy.ones((height, 1, 1))
        arr = ((arr + 1.0) * 127.0).astype(numpy.uint8)
        arr[:, -6:, :] = rng.integers(0, 255, (height, 6, 3), dtype=numpy.uint8)
        img = Image.fromarray(arr.astype("uint8"))

        from texture2d.tileability import score_tileability

        assert score_tileability(heal_seam(img)).score > score_tileability(img).score

    def test_returns_rgb_and_size(self):
        img = Image.new("RGB", (64, 64), (10, 20, 30))
        out = heal_seam(img, band=8)
        assert out.mode == "RGB" and out.size == (64, 64)


class TestVaeIdEnv:
    def test_default_ft_mse(self):
        import os

        from texture2d.generator import DEFAULT_VAE_ID, _default_vae_id

        assert DEFAULT_VAE_ID == "stabilityai/sd-vae-ft-mse"
        with patch.dict(os.environ, {"TEXTURE2D_VAE_ID": "none"}):
            assert _default_vae_id() == ""
        with patch.dict(os.environ, {"TEXTURE2D_VAE_ID": "org/other-vae"}):
            assert _default_vae_id() == "org/other-vae"


class TestDecodePolicy:
    def _gen_and_pipe(self):
        gen = TextureGenerator(device="cpu")
        pipe = _FakePipe()
        return gen, pipe

    def test_explicit_integral(self):
        gen, pipe = self._gen_and_pipe()
        lat = torch.zeros(1, 4, 32, 32)
        image, tiled = gen._decode_latents(pipe, lat, width=256, height=256, vae_tiling=False)
        assert tiled is False
        assert pipe.vae.tiled is False
        assert image.size == (256, 256)
        assert pipe.vae.decode_calls == [(32, 32)]  # sem pad

    def test_explicit_tiling_pads_and_crops(self):
        gen, pipe = self._gen_and_pipe()
        lat = torch.zeros(1, 4, 32, 32)
        image, tiled = gen._decode_latents(pipe, lat, width=256, height=256, vae_tiling=True)
        assert tiled is True
        assert pipe.vae.tiled is True
        assert pipe.vae.decode_calls == [(40, 40)]  # pad circular de 8 latents
        assert image.size == (256, 256)  # crop do pad

    def test_auto_cpu_is_integral(self):
        gen, pipe = self._gen_and_pipe()
        lat = torch.zeros(1, 4, 32, 32)
        _, tiled = gen._decode_latents(pipe, lat, width=256, height=256, vae_tiling=None)
        assert tiled is False


class TestDecodeUpcast:
    """Sem o swap ft-mse, o VAE do checkpoint decodifica em fp32 (overflow fp16)."""

    def _cuda_fp16_gen(self, swapped: bool) -> TextureGenerator:
        gen = TextureGenerator(device="cpu")
        gen.device = "cuda:0"
        gen.torch_dtype = torch.float16
        gen._vae_swapped = swapped
        return gen

    def test_checkpoint_vae_upcasts_to_fp32_and_restores(self):
        gen = self._cuda_fp16_gen(swapped=False)
        pipe = _FakePipe()
        lat = torch.zeros(1, 4, 32, 32, dtype=torch.float16)
        gen._decode_latents(pipe, lat, width=256, height=256, vae_tiling=False)
        # Decode em fp32 e dtype reposto no fim (fp16).
        assert pipe.vae.decode_dtypes == [torch.float32]
        assert pipe.vae.to_calls == [torch.float32, torch.float16]

    def test_swapped_ft_mse_stays_fp16(self):
        gen = self._cuda_fp16_gen(swapped=True)
        pipe = _FakePipe()
        lat = torch.zeros(1, 4, 32, 32, dtype=torch.float16)
        gen._decode_latents(pipe, lat, width=256, height=256, vae_tiling=False)
        assert pipe.vae.decode_dtypes == [torch.float16]
        assert pipe.vae.to_calls == []

    def test_cpu_never_upcasts(self):
        gen = TextureGenerator(device="cpu")
        gen._vae_swapped = False
        pipe = _FakePipe()
        lat = torch.zeros(1, 4, 32, 32)
        gen._decode_latents(pipe, lat, width=256, height=256, vae_tiling=False)
        assert pipe.vae.to_calls == []


class TestSelectScheduler:
    """Modo late → DDIM stateless (roll desalinha a história do DPM multistep)."""

    BASE_CFG: ClassVar[dict] = {
        "num_train_timesteps": 1000,
        "beta_start": 0.0001,
        "beta_end": 0.02,
        "beta_schedule": "scaled_linear",
        "steps_offset": 1,
        "clip_sample": False,
        "set_alpha_to_one": False,
    }

    def test_late_installs_ddim(self):
        from diffusers import DDIMScheduler

        gen = TextureGenerator(device="cpu")
        gen._base_scheduler_config = dict(self.BASE_CFG)
        pipe = _FakePipe()
        pipe.scheduler = object()
        gen._select_scheduler(pipe, "late")
        assert isinstance(pipe.scheduler, DDIMScheduler)
        # Instância cacheada — segunda chamada não reconstrói.
        cached = pipe.scheduler
        gen._select_scheduler(pipe, "late")
        assert pipe.scheduler is cached

    def test_full_restores_dpm(self):
        gen = TextureGenerator(device="cpu")
        gen._base_scheduler_config = dict(self.BASE_CFG)
        dpm = object()
        gen._dpm_scheduler = dpm
        pipe = _FakePipe()
        pipe.scheduler = object()  # DDIM deixado pelo run anterior
        gen._select_scheduler(pipe, "full")
        assert pipe.scheduler is dpm
        gen._select_scheduler(pipe, "off")
        assert pipe.scheduler is dpm

    def test_noop_without_loaded_config(self):
        """Load mockado (sem _base_scheduler_config) — scheduler intocado."""
        gen = TextureGenerator(device="cpu")
        pipe = _FakePipe()
        sentinel = object()
        pipe.scheduler = sentinel
        gen._select_scheduler(pipe, "late")
        assert pipe.scheduler is sentinel

    def test_generate_passes_late_scheduler_to_pipeline(self):
        from diffusers import DDIMScheduler

        gen = TextureGenerator(device="cpu")
        gen._base_scheduler_config = dict(self.BASE_CFG)
        fake = _FakePipe()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("stone texture", seed=1, ground="off")
        assert isinstance(fake.scheduler, DDIMScheduler)
        assert meta["scheduler"] == "DDIMScheduler"


class TestHiresPath:
    def test_hires_generates_native_then_refine(self):
        gen, fake = _make_gen()
        refine_calls: list[tuple[int, int]] = []

        def fake_refine(
            pipe,
            latents,
            *,
            prompt,
            negative_prompt,
            guidance_scale,
            refine_steps,
            generator,
            should_abort,
            on_step,
            progress_total,
            progress_offset,
        ):
            refine_calls.append((progress_offset, progress_total))
            return latents

        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch.object(gen, "_refine_latents", side_effect=fake_refine),
        ):
            image, meta = gen.generate("stone floor", seed=3, width=1024, height=1024, ground="off")
        assert fake.last_kwargs["width"] == 512 and fake.last_kwargs["height"] == 512
        assert refine_calls == [(30, 42)]  # 30 steps default + 12 refine
        assert meta["hires"] is True and meta["gen_width"] == 512
        assert meta["refine_steps"] == 12
        assert image.size == (1024, 1024)

    def test_no_hires_generates_direct(self):
        gen, fake = _make_gen()
        with (
            patch.object(gen, "_load_pipeline", return_value=fake),
            patch.object(gen, "_refine_latents") as mock_refine,
        ):
            _, meta = gen.generate("stone floor", seed=3, width=1024, height=1024, hires=False, ground="off")
        assert fake.last_kwargs["width"] == 1024
        mock_refine.assert_not_called()
        assert meta["hires"] is False

    def test_512_target_never_hires(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("sand", seed=1, ground="off")
        assert meta["hires"] is False
        assert "refine_steps" not in meta


class TestCompileFallback:
    def test_compile_forces_full_mode(self):
        gen = TextureGenerator(device="cpu", torch_compile=True)
        fake = _FakePipe()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("stone", seed=1, seamless_mode="late", ground="off")
        assert meta["seamless_mode"] == "full"


# ---------------------------------------------------------------------------
# Payload vramd + CLI: chaves seamless 2.0
# ---------------------------------------------------------------------------


class TestVramdPayloadSeamless:
    def test_new_keys_included_when_set(self):
        from texture2d.vramd_payload import build_generate_request

        p = build_generate_request(
            prompt="brick",
            output="/tmp/b.png",
            seamless_mode="full",
            refine_steps=16,
            vae_tiling=True,
            seam_heal=False,
            hires=False,
        )
        assert p["seamless_mode"] == "full"
        assert p["refine_steps"] == 16
        assert p["vae_tiling"] is True
        assert p["seam_heal"] is False
        assert p["hires"] is False

    def test_new_keys_omitted_when_none(self):
        from texture2d.vramd_payload import build_generate_request

        p = build_generate_request(prompt="brick", output="/tmp/b.png")
        for key in ("seamless_mode", "refine_steps", "vae_tiling", "seam_heal", "hires"):
            assert key not in p

    def test_defaults_aligned_medium_tier(self):
        from texture2d.vramd_payload import build_generate_request

        p = build_generate_request(prompt="x", output="/tmp/x.png")
        assert p["steps"] == 28 and p["guidance"] == 7.0


class TestCliSeamlessFlags:
    def test_help_lists_new_flags(self):
        from click.testing import CliRunner

        from texture2d.cli import cli

        r = CliRunner().invoke(cli, ["generate", "--help"])
        assert r.exit_code == 0
        for flag in ("--seamless-mode", "--refine-steps", "--vae-tiling", "--seam-heal", "--no-hires"):
            assert flag in r.output

    def test_invalid_seamless_mode_rejected(self):
        from click.testing import CliRunner

        from texture2d.cli import cli

        r = CliRunner().invoke(cli, ["generate", "stone", "--seamless-mode", "banana"])
        assert r.exit_code != 0
