"""Testes para aigamekit_shared.hf."""

import os
from unittest.mock import patch

from aigamekit_shared.env import HF_HOME
from aigamekit_shared.hf import (
    HF_TOKEN,
    HUGGINGFACEHUB_API_TOKEN,
    get_hf_token,
    hf_home_display_rich,
)


class TestGetHfToken:
    def test_hf_token_first(self):
        with patch.dict(
            os.environ,
            {HF_TOKEN: "abc", HUGGINGFACEHUB_API_TOKEN: "def"},
            clear=True,
        ):
            assert get_hf_token() == "abc"

    def test_fallback_hub_token(self):
        with patch.dict(os.environ, {HUGGINGFACEHUB_API_TOKEN: "xyz"}, clear=True):
            assert get_hf_token() == "xyz"

    def test_empty_returns_none(self):
        with patch.dict(os.environ, {HF_TOKEN: "  ", HUGGINGFACEHUB_API_TOKEN: ""}, clear=True):
            assert get_hf_token() is None

    def test_none_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_hf_token() is None


class TestHfHomeDisplayRich:
    def test_uses_env(self):
        with patch.dict(os.environ, {HF_HOME: "/data/hf"}, clear=True):
            assert hf_home_display_rich() == "/data/hf"

    def test_default_label(self):
        with patch.dict(os.environ, {}, clear=True):
            assert "huggingface" in hf_home_display_rich()

    def test_custom_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert hf_home_display_rich(default_label="[dim]custom[/dim]") == "[dim]custom[/dim]"
