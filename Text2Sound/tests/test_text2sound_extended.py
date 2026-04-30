"""Testes extra Text2Sound: utils, presets, áudio leve."""

from __future__ import annotations

from pathlib import Path

import pytest

from text2sound.presets import AUDIO_PRESETS, get_preset, list_presets
from text2sound.utils import (
    format_bytes,
    format_duration,
    generate_output_path,
    resolve_effective_seed,
    safe_filename,
)


def test_resolve_effective_seed_explicit() -> None:
    assert resolve_effective_seed(42) == 42


def test_resolve_effective_seed_random(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gamedev_shared.seed_utils.secrets.randbelow", lambda n: 7)
    assert resolve_effective_seed(None) == 7


def test_format_bytes_zero() -> None:
    assert format_bytes(0) == "0.0 B"


def test_format_bytes_kb() -> None:
    assert "KB" in format_bytes(2048)


def test_format_bytes_mb() -> None:
    assert "MB" in format_bytes(3 * 1024 * 1024)


def test_format_bytes_tb_path() -> None:
    s = format_bytes(1024**5)
    assert "PB" in s or "TB" in s


def test_safe_filename_alnum() -> None:
    assert safe_filename("HelloWorld") == "helloworld"


def test_safe_filename_specials() -> None:
    assert safe_filename("a/b@c#d") == "abcd"


def test_safe_filename_max_len() -> None:
    assert len(safe_filename("abcdefghij", max_len=4)) == 4


def test_format_duration_minutes() -> None:
    assert format_duration(125.9) == "2:05"


def test_format_duration_zero() -> None:
    assert format_duration(0.0) == "0:00"


def test_generate_output_path_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("text2sound.utils.time.time", lambda: 1_700_000_000)
    p = generate_output_path("hello world", tmp_path, fmt="flac")
    assert p.suffix == ".flac"
    assert "hello" in p.name.lower() or "_" in p.name


def test_list_presets_non_empty() -> None:
    names = list_presets()
    assert len(names) >= 1
    assert names == sorted(names)


def test_get_preset_normalized_name() -> None:
    if not AUDIO_PRESETS:
        pytest.skip("no presets")
    first = next(iter(AUDIO_PRESETS))
    assert get_preset(first.lower()) == AUDIO_PRESETS[first]


def test_get_preset_unknown() -> None:
    with pytest.raises(KeyError):
        get_preset("__no_such_preset__xyz__")


def test_get_spec_custom_id() -> None:
    from text2sound.models import get_spec

    s = get_spec("myorg/custom-audio-model")
    assert s.max_seconds == 47.0
    assert "Custom" in s.label


def test_get_spec_open_small_heuristic() -> None:
    from text2sound.models import SPEC_EFFECTS, get_spec

    s = get_spec("stabilityai/stable-audio-open-small")
    assert s.hf_id == SPEC_EFFECTS.hf_id


def test_resolve_model_id_strips_whitespace() -> None:
    from text2sound.models import MODEL_MUSIC_ID, resolve_model_id

    assert resolve_model_id("  music  ") == MODEL_MUSIC_ID


def test_resolve_model_from_profile_effects_with_override() -> None:
    from text2sound.models import MODEL_MUSIC_ID, resolve_model_from_profile

    assert resolve_model_from_profile("effects", "music") == MODEL_MUSIC_ID


def test_format_duration_single_digit_seconds() -> None:
    assert format_duration(65.0) == "1:05"


def test_safe_filename_unicode_replaced() -> None:
    s = safe_filename("café-日本")
    assert "_" in s or "caf" in s


def test_format_bytes_fractional() -> None:
    assert "B" in format_bytes(100.5)


def test_model_music_and_effects_ids_differ() -> None:
    from text2sound.models import MODEL_EFFECTS_ID, MODEL_MUSIC_ID

    assert MODEL_MUSIC_ID != MODEL_EFFECTS_ID
    assert "stabilityai" in MODEL_MUSIC_ID


def test_get_preset_name_with_spaces_normalized() -> None:
    assert get_preset("footsteps stone") == get_preset("footsteps-stone")


def test_audio_presets_keys_match_dict() -> None:
    assert set(list_presets()) == set(AUDIO_PRESETS.keys())
