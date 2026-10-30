"""Model weights: SHA-256 verification, cache override and diagnostics."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from motion3d import weights


class TestConstants:
    def test_repo_and_file(self) -> None:
        assert weights.HF_REPO == "SegerEnd/SprongMotion-14M"
        assert weights.MODEL_FILE == "SprongMotion-14M-v1.0-Q8_0.gguf"

    def test_hash_is_a_sha256(self) -> None:
        assert len(weights.MODEL_SHA256) == 64
        int(weights.MODEL_SHA256, 16)

    def test_size_matches_the_model_card(self) -> None:
        assert weights.MODEL_BYTES == 33_337_824

    def test_license_is_apache(self) -> None:
        assert weights.LICENSE == "Apache-2.0"


class TestSha256:
    def test_known_value(self, tmp_path: Path) -> None:
        path = tmp_path / "x.bin"
        path.write_bytes(b"abc")
        assert weights.sha256_file(path) == hashlib.sha256(b"abc").hexdigest()

    def test_streams_large_files(self, tmp_path: Path) -> None:
        path = tmp_path / "big.bin"
        payload = b"a" * (3 << 20)
        path.write_bytes(payload)
        assert weights.sha256_file(path, chunk=1024) == hashlib.sha256(payload).hexdigest()

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")
        assert weights.sha256_file(path) == hashlib.sha256(b"").hexdigest()


class TestVerifyModel:
    def test_missing_file(self, tmp_path: Path) -> None:
        ok, message = weights.verify_model(tmp_path / "nope.gguf")
        assert not ok and "falta" in message

    def test_wrong_size(self, tmp_path: Path) -> None:
        path = tmp_path / "short.gguf"
        path.write_bytes(b"x" * 10)
        ok, message = weights.verify_model(path)
        assert not ok and "tamanho" in message

    def test_hash_mismatch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "same-size.gguf"
        path.write_bytes(b"")
        monkeypatch.setattr(weights, "MODEL_BYTES", 0)
        ok, message = weights.verify_model(path)
        assert not ok and "sha256" in message

    def test_good_hash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "good.gguf"
        path.write_bytes(b"abc")
        monkeypatch.setattr(weights, "MODEL_BYTES", 3)
        ok, message = weights.verify_model(path, expected_sha256=hashlib.sha256(b"abc").hexdigest())
        assert ok and message == "ok"


class TestCachePaths:
    def test_override_is_used_when_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "local.gguf"
        path.write_bytes(b"x")
        monkeypatch.setenv("SPRONG_MODEL", str(path))
        assert weights.cached_model_path() == path

    def test_override_missing_is_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPRONG_MODEL", str(tmp_path / "nope.gguf"))
        assert weights.cached_model_path() is None

    def test_ensure_uses_the_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "local.gguf"
        path.write_bytes(b"x")
        monkeypatch.setenv("SPRONG_MODEL", str(path))
        assert weights.ensure_model_path(verify=False) == path

    def test_ensure_missing_override_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPRONG_MODEL", str(tmp_path / "nope.gguf"))
        with pytest.raises(weights.ModelIntegrityError, match="SPRONG_MODEL"):
            weights.ensure_model_path()


class TestModelInfo:
    def test_reports_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("SPRONG_MODEL", str(tmp_path / "nope.gguf"))
        info = weights.model_info()
        assert info["state"] == "missing"
        assert info["repo"] == weights.HF_REPO

    def test_reports_corrupt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "bad.gguf"
        path.write_bytes(b"x")
        monkeypatch.setenv("SPRONG_MODEL", str(path))
        info = weights.model_info()
        assert info["state"] == "corrupt"
        assert "detail" in info

    def test_reports_ok_for_a_verified_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "good.gguf"
        path.write_bytes(b"abc")
        monkeypatch.setenv("SPRONG_MODEL", str(path))
        monkeypatch.setattr(weights, "MODEL_BYTES", 3)
        monkeypatch.setattr(weights, "MODEL_SHA256", hashlib.sha256(b"abc").hexdigest())
        info = weights.model_info()
        assert info["state"] == "ok"
