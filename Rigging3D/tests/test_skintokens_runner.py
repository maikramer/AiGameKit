"""Testes para ``rigging3d.skintokens_runner`` (sem carregar o modelo real)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from rigging3d import skintokens_runner as runner


class TestCheckpointsHome:
    def test_default_under_home_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RIGGING3D_SKINTOKENS_HOME", raising=False)
        home = runner._checkpoints_home()
        assert home == Path.home() / ".cache" / "rigging3d" / "skintokens"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("RIGGING3D_SKINTOKENS_HOME", str(tmp_path))
        assert runner._checkpoints_home() == tmp_path.resolve()


class TestEnsureCheckpoints:
    def test_symlinks_vendored_configs(self, tmp_path: Path) -> None:
        """Não baixa nada (mockado); só garante o symlink configs/."""
        fake_root = tmp_path / "home"
        # Simula ficheiros já presentes para pular o download real.
        for filename in (runner.DEFAULT_TOKENRIG_CKPT, runner.DEFAULT_SKIN_VAE_CKPT):
            p = fake_root / filename
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")

        with patch("huggingface_hub.hf_hub_download") as mock_dl:
            result = runner.ensure_checkpoints(home=fake_root)

        mock_dl.assert_not_called()
        assert result == fake_root
        configs_link = fake_root / "configs"
        assert configs_link.is_symlink()
        assert configs_link.resolve() == (runner._PACKAGE_ROOT / "configs").resolve()

    def test_downloads_missing_checkpoints(self, tmp_path: Path) -> None:
        fake_root = tmp_path / "home"

        with patch("huggingface_hub.hf_hub_download") as mock_dl:
            runner.ensure_checkpoints(home=fake_root)

        assert mock_dl.call_count == 2
        called_filenames = {c.kwargs["filename"] for c in mock_dl.call_args_list}
        assert called_filenames == {runner.DEFAULT_TOKENRIG_CKPT, runner.DEFAULT_SKIN_VAE_CKPT}
