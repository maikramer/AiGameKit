"""Cobertura elaborada Text2Sound — funções puras, presets, UMS payload, DSP (sem GPU)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from text2sound import presets as presets_mod
from text2sound.hardware import profile_from_specs
from text2sound.models import (
    MODEL_EFFECTS_ID,
    MODEL_MUSIC_ID,
    get_spec,
    resolve_model_from_profile,
    resolve_model_id,
)
from text2sound.presets import AUDIO_PRESETS, get_preset, list_presets
from text2sound.prompt_enhancer import (
    detect_sound_type,
    enhance_negative,
    enhance_prompt,
    validate_and_correct,
)
from text2sound.ums_payload import build_generate_request
from text2sound.utils import format_duration, generate_output_path

# --- format_duration (35 casos) ---

_DURATION_CASES = [
    (0.0, "0:00"),
    (0.9, "0:00"),
    (59.0, "0:59"),
    (60.0, "1:00"),
    (61.0, "1:01"),
    (125.0, "2:05"),
    (3599.0, "59:59"),
    (3600.0, "60:00"),
    (3661.0, "61:01"),
    (7.5, "0:07"),
    (8.0, "0:08"),
    (599.9, "9:59"),
    (600.0, "10:00"),
    (601.0, "10:01"),
    (1799.0, "29:59"),
    (1800.0, "30:00"),
    (2700.0, "45:00"),
    (47.0, "0:47"),
    (11.0, "0:11"),
    (1.0, "0:01"),
    (0.01, "0:00"),
    (120.5, "2:00"),
    (121.0, "2:01"),
    (240.0, "4:00"),
    (241.0, "4:01"),
    (3000.0, "50:00"),
    (3059.0, "50:59"),
    (3060.0, "51:00"),
    (7200.0, "120:00"),
    (15.0, "0:15"),
    (16.0, "0:16"),
    (32.0, "0:32"),
    (33.0, "0:33"),
    (99.0, "1:39"),
    (100.0, "1:40"),
]


@pytest.mark.parametrize("seconds,expected", _DURATION_CASES)
def test_format_duration_mm_ss(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


# --- generate_output_path (8 casos) ---


@pytest.mark.parametrize(
    "prompt,fmt",
    [
        ("footsteps on stone", "wav"),
        ("battle music epic", "ogg"),
        ("ui click", "flac"),
        ("rain ambient loop", "ogg"),
        ("  spaced  prompt  ", "wav"),
        ("unicode café sfx", "ogg"),
        ("a" * 80, "wav"),
        ("slash/name test", "flac"),
    ],
)
def test_generate_output_path_has_extension(tmp_path: Path, prompt: str, fmt: str) -> None:
    out = generate_output_path(prompt, tmp_path, fmt=fmt)
    assert out.parent == tmp_path
    assert out.suffix == f".{fmt}"
    assert "_" in out.stem


# --- presets: um teste estrutural por preset (60) ---

_PRESET_NAMES = list_presets()


@pytest.mark.parametrize("name", _PRESET_NAMES)
def test_each_preset_has_required_keys(name: str) -> None:
    p = get_preset(name)
    assert isinstance(p["prompt"], str) and len(p["prompt"]) > 10
    assert p["kind"] in {
        "ambient_loop",
        "music_loop",
        "sfx_impact",
        "sfx_magic",
        "sfx_movement",
        "sfx_ui",
        "sfx_creature",
        "sfx_destruction",
        "sfx_weapon",
        "sfx_mechanical",
        "sfx_elemental",
        "sfx_vocal",
        "sfx_collectible",
        "sfx_alarm",
        "sfx_ambient_sfx",
    }
    assert 0.5 <= float(p["duration"]) <= 45.0
    assert 60 <= int(p["steps"]) <= 120
    assert 1.0 <= float(p["cfg_scale"]) <= 10.0


@pytest.mark.parametrize(
    "lookup",
    ["AMBIENT", "Battle", "menu", "sword_clash", "UI-CLICK"],
)
def test_get_preset_case_and_separator_aliases(lookup: str) -> None:
    canonical = lookup.lower().replace(" ", "-").replace("_", "-")
    assert get_preset(lookup) == AUDIO_PRESETS[canonical]


def test_list_presets_count_and_sorted() -> None:
    names = list_presets()
    assert len(names) == len(AUDIO_PRESETS) == 60
    assert names == sorted(names)


# --- models (18 casos) ---


@pytest.mark.parametrize(
    "user,expected",
    [
        (None, MODEL_MUSIC_ID),
        ("", MODEL_MUSIC_ID),
        ("music", MODEL_MUSIC_ID),
        ("full", MODEL_MUSIC_ID),
        ("1.0", MODEL_MUSIC_ID),
        ("effects", MODEL_EFFECTS_ID),
        ("small", MODEL_EFFECTS_ID),
        ("sfx", MODEL_EFFECTS_ID),
        ("stabilityai/stable-audio-open-1.0", MODEL_MUSIC_ID),
        ("stabilityai/stable-audio-open-small", MODEL_EFFECTS_ID),
    ],
)
def test_resolve_model_id(user: str | None, expected: str) -> None:
    assert resolve_model_id(user) == expected


@pytest.mark.parametrize(
    "profile,override,expected",
    [
        ("music", None, MODEL_MUSIC_ID),
        ("effects", None, MODEL_EFFECTS_ID),
        ("music", "small", MODEL_EFFECTS_ID),
        ("effects", "music", MODEL_MUSIC_ID),
    ],
)
def test_resolve_model_from_profile(profile: str, override: str | None, expected: str) -> None:
    assert resolve_model_from_profile(profile, override) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "hf_id,expected_max",
    [
        (MODEL_MUSIC_ID, 47.0),
        (MODEL_EFFECTS_ID, 11.0),
        ("stabilityai/stable-audio-open-small", 11.0),
        ("custom/org-model", 47.0),
    ],
)
def test_get_spec_max_seconds(hf_id: str, expected_max: float) -> None:
    spec = get_spec(hf_id)
    assert spec.max_seconds == expected_max
    assert spec.default_steps > 0


def test_resolve_model_id_unknown_raises() -> None:
    with pytest.raises(ValueError, match="desconhecido"):
        resolve_model_id("not-a-real-alias")


# --- hardware profile_from_specs (12 casos) ---


@pytest.mark.parametrize(
    "gpus,expect_device,expect_half,expect_chunked",
    [
        ([], "cpu", False, True),
        ([(0, 6 * 1024**3)], "cuda", True, True),
        ([(0, 8 * 1024**3)], "cuda", True, True),
        ([(0, 12 * 1024**3)], "cuda", False, False),
        ([(0, 16 * 1024**3)], "cuda", False, False),
        ([(0, 6 * 1024**3), (1, 6 * 1024**3)], "cuda", False, False),
        ([(0, 10 * 1024**3), (1, 10 * 1024**3)], "cuda", False, False),
        ([(0, 9 * 1024**3)], "cuda", True, False),
        ([(0, int(8.5 * 1024**3))], "cuda", True, False),
        ([(0, int(8.49 * 1024**3))], "cuda", True, True),
        ([(0, 12 * 1024**3 - 1)], "cuda", True, False),
        ([(0, 12 * 1024**3)], "cuda", False, False),
    ],
)
def test_profile_from_specs_tiers(
    gpus: list[tuple[int, int]],
    expect_device: str,
    expect_half: bool,
    expect_chunked: bool,
) -> None:
    prof = profile_from_specs(gpus)
    assert prof.device == expect_device
    assert prof.half is expect_half
    assert prof.chunked_vae is expect_chunked
    assert "half=" in prof.summary()


# --- UMS payload (12 casos) ---


@pytest.mark.parametrize(
    "kwargs,required_keys",
    [
        ({}, {"prompt", "output", "duration", "steps", "cfg_scale"}),
        ({"sigma_min": 0.1, "sigma_max": 500.0}, {"sigma_min", "sigma_max"}),
        ({"sampler_type": "euler"}, {"sampler_type"}),
        ({"negative_prompt": "noise"}, {"negative_prompt"}),
        ({"half_precision": True}, {"half_precision"}),
        ({"quality": "high", "category": "sfx_ui"}, {"quality", "category"}),
        ({"gpu_ids": [0, 1]}, {"gpu_ids"}),
        ({"gpu_ids": "0,1"}, {"gpu_ids"}),
        ({"seed": 42}, {"seed"}),
        ({"extra": {"batch_id": "x"}}, {"batch_id"}),
        ({"duration": 3.5, "steps": 80, "cfg_scale": 9.0}, {"duration", "steps", "cfg_scale"}),
        ({"half_precision": False}, {"half_precision"}),
    ],
)
def test_build_generate_request_keys(kwargs: dict, required_keys: set[str]) -> None:
    base = {"prompt": "test sfx", "output": "/tmp/out.wav"}
    if "duration" not in kwargs:
        base["duration"] = 2.0
    payload = build_generate_request(**base, **kwargs)
    for key in required_keys:
        assert key in payload
    assert payload["prompt"] == "test sfx"


# --- prompt enhancer com dados mínimos (15 casos) ---

_MOCK_DESCRIPTOR_DATA = {
    "keywords": {
        "impact": ["explosion", "blast"],
        "ui": ["click", "button"],
        "music_loop": ["orchestral", "theme"],
    },
    "descriptors": {
        "generic": {"texture": ["high quality", "detailed"], "dynamics": ["punchy"], "context": []},
        "impact": {"texture": ["sharp transient"], "dynamics": ["loud"], "context": ["game sfx"]},
        "ui": {"texture": ["clean digital"], "dynamics": [], "context": []},
        "music_loop": {"texture": ["warm pads"], "dynamics": [], "context": ["loopable"]},
    },
    "negative_descriptors": ["low quality", "distortion", "clipping"],
    "weak_terms": ["test", "asdf"],
}


@pytest.mark.parametrize(
    "raw,expect_substring",
    [
        ("", "ambient"),
        ("  hello  ", "Hello"),
        ("explosion boom", "explosion"),
        ("ui click sound", "click"),
        ("orchestral battle theme", "orchestral"),
    ],
)
def test_validate_and_correct(raw: str, expect_substring: str) -> None:
    clean, _ = validate_and_correct(raw, _MOCK_DESCRIPTOR_DATA)
    assert expect_substring.lower() in clean.lower()


@pytest.mark.parametrize(
    "prompt,audio_kind,expected_type",
    [
        ("random noise", "sfx_weapon", "weapon"),
        ("explosion in cave", None, "impact"),
        ("button click", None, "ui"),
        ("loopable theme", "music_loop", "music_loop"),
        ("unknown phrase", None, "generic"),
    ],
)
def test_detect_sound_type(prompt: str, audio_kind: str | None, expected_type: str) -> None:
    assert detect_sound_type(prompt, audio_kind=audio_kind, data=_MOCK_DESCRIPTOR_DATA) == expected_type


@pytest.mark.parametrize(
    "prompt",
    ["explosion", "click", "ambient wind", "sword clash metal", "menu theme piano"],
)
def test_enhance_prompt_adds_descriptors_or_keeps(prompt: str) -> None:
    enhanced, meta = enhance_prompt(prompt, data=_MOCK_DESCRIPTOR_DATA)
    assert len(enhanced) >= len(prompt.strip())
    assert "sound_type" in meta
    assert "original_prompt" in meta


@pytest.mark.parametrize(
    "neg,fragment",
    [
        ("", "low quality"),
        ("already has low quality", "low quality"),
        ("muffled", "distortion"),
    ],
)
def test_enhance_negative_appends(neg: str, fragment: str) -> None:
    out = enhance_negative(neg, _MOCK_DESCRIPTOR_DATA)
    assert fragment in out.lower()


# --- audio_processor DSP (25 casos, torch local) ---


@pytest.mark.parametrize("peak_val", [0.5, 1.0, 2.0, 10.0, 0.001])
def test_peak_normalize_clamps_to_unit(peak_val: float) -> None:
    import torch

    from text2sound.audio_processor import peak_normalize

    audio = torch.ones(2, 100) * peak_val
    out = peak_normalize(audio)
    assert float(out.abs().max()) <= 1.0 + 1e-6
    if peak_val > 0:
        assert abs(float(out.abs().max()) - 1.0) < 1e-5


@pytest.mark.parametrize("channels", [1, 2])
def test_to_int16_range(channels: int) -> None:
    import torch

    from text2sound.audio_processor import to_int16

    audio = torch.linspace(-1, 1, 50).unsqueeze(0).repeat(channels, 1)
    out = to_int16(audio)
    assert out.dtype == torch.int16
    assert int(out.min()) >= -32767
    assert int(out.max()) <= 32767


@pytest.mark.parametrize(
    "lead_silence,trail_silence",
    [(8000, 8000), (4000, 0), (0, 12000), (16000, 16000)],
)
def test_trim_silence_shortens(lead_silence: int, trail_silence: int) -> None:
    import torch

    from text2sound.audio_processor import trim_silence

    sr = 44100
    tone = torch.sin(torch.linspace(0, 50, 2000)) * 0.5
    audio = torch.zeros(1, lead_silence + 2000 + trail_silence)
    audio[0, lead_silence : lead_silence + 2000] = tone
    out = trim_silence(audio, sr, threshold_db=-40.0, buffer_ms=50)
    assert out.shape[-1] < audio.shape[-1]


def test_trim_silence_all_silent_unchanged() -> None:
    import torch

    from text2sound.audio_processor import trim_silence

    audio = torch.zeros(2, 5000)
    out = trim_silence(audio, 44100)
    assert out.shape == audio.shape


@pytest.mark.parametrize("crop_s", [0.5, 1.0, 2.0])
def test_crop_to_duration(crop_s: float) -> None:
    import torch

    from text2sound.audio_processor import crop_to_duration

    sr = 44100
    audio = torch.randn(2, sr * 5)
    out = crop_to_duration(audio, sr, crop_s, fade_out_seconds=0.05)
    assert out.shape[-1] <= int(crop_s * sr) + 1


@pytest.mark.parametrize("fade_in_ms,fade_out_ms", [(5, 20), (1, 1), (10, 30)])
def test_apply_edge_fade_reduces_edges(fade_in_ms: float, fade_out_ms: float) -> None:
    import torch

    from text2sound.audio_processor import apply_edge_fade

    sr = 48000
    audio = torch.ones(1, sr)
    out = apply_edge_fade(audio, sr, fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms)
    assert float(out[0, 0]) < 1.0
    assert float(out[0, -1]) < 1.0


@pytest.mark.parametrize("crossfade_ms", [100.0, 250.0, 500.0])
def test_seamless_loop_shorter_than_input(crossfade_ms: float) -> None:
    import torch

    from text2sound.audio_processor import apply_seamless_loop_crossfade

    sr = 44100
    n = sr * 2
    t = torch.linspace(0, 4 * math.pi, n)
    audio = torch.stack([torch.sin(t), torch.cos(t)])
    out = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=crossfade_ms)
    assert out.shape[-1] < n
    assert out.shape[0] == 2


def test_supported_formats_constant() -> None:
    from text2sound.audio_processor import DEFAULT_FORMAT, SUPPORTED_FORMATS

    assert DEFAULT_FORMAT == "ogg"
    assert "wav" in SUPPORTED_FORMATS
    assert "flac" in SUPPORTED_FORMATS


@pytest.mark.parametrize("fmt", ["wav", "ogg", "flac"])
def test_save_audio_writes_file(tmp_path: Path, fmt: str) -> None:
    import torch

    from text2sound.audio_processor import save_audio

    sr = 22050
    audio = torch.randn(2, sr) * 0.1
    path = tmp_path / "clip"
    out = save_audio(
        audio,
        sr,
        path,
        fmt=fmt,
        normalize=True,
        trim=False,
        apply_fade=True,
        as_int16=True,
    )
    assert out.exists()
    assert out.suffix == f".{fmt}"


def test_save_audio_invalid_format_raises(tmp_path: Path) -> None:
    import torch

    from text2sound.audio_processor import save_audio

    with pytest.raises(ValueError, match="não suportado"):
        save_audio(torch.zeros(1, 100), 44100, tmp_path / "x", fmt="mp3")


def test_presets_module_reexport_count() -> None:
    assert len(presets_mod.list_presets()) == 60
