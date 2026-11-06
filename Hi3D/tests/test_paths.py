"""Cache e validação dos artefactos do modelo."""

from __future__ import annotations

from pathlib import Path

import pytest
from hi3d import DEFAULT_MODEL_ID, paths
from hi3d.paths import (
    EXPECTED_WEIGHT_BYTES,
    REQUIRED_ARTIFACTS,
    cache_root,
    default_model_dir,
    is_complete,
    missing_artifacts,
    resolve_model_ref,
    weight_bytes,
)


def _make_model(root: Path, *, skip: tuple[str, ...] = ()) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel in REQUIRED_ARTIFACTS:
        if rel in skip:
            continue
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 16)
    return root


class TestCacheRoot:
    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AIGAMEKIT_MODELS_DIR", str(tmp_path / "models"))
        assert cache_root() == tmp_path / "models"

    def test_env_override_expands_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AIGAMEKIT_MODELS_DIR", "~/custom-models")
        assert str(cache_root()).endswith("custom-models")

    def test_xdg_cache_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("AIGAMEKIT_MODELS_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert cache_root() == tmp_path / "aigamekit" / "models"

    def test_default_home_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AIGAMEKIT_MODELS_DIR", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert str(cache_root()).endswith("aigamekit/models")


class TestDefaultModelDir:
    def test_default_id_uses_stable_slug(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AIGAMEKIT_MODELS_DIR", str(tmp_path))
        assert default_model_dir() == tmp_path / "hi3d-v1.0"

    def test_custom_repo_id_is_lowercased(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AIGAMEKIT_MODELS_DIR", str(tmp_path))
        assert default_model_dir("org/MyModel") == tmp_path / "mymodel"


class TestIsComplete:
    def test_complete_tree(self, tmp_path: Path) -> None:
        assert is_complete(_make_model(tmp_path / "m"))

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert not is_complete(tmp_path / "nope")

    @pytest.mark.parametrize("rel", REQUIRED_ARTIFACTS)
    def test_each_required_artifact_matters(self, tmp_path: Path, rel: str) -> None:
        assert not is_complete(_make_model(tmp_path / "m", skip=(rel,)))

    def test_empty_file_is_incomplete(self, tmp_path: Path) -> None:
        model = _make_model(tmp_path / "m")
        (model / "vae/model.safetensors").write_bytes(b"")
        assert not is_complete(model)


class TestMissingArtifacts:
    def test_empty_when_complete(self, tmp_path: Path) -> None:
        assert missing_artifacts(_make_model(tmp_path / "m")) == []

    def test_lists_all_when_absent(self, tmp_path: Path) -> None:
        assert len(missing_artifacts(tmp_path / "nope")) == len(REQUIRED_ARTIFACTS)

    def test_lists_specific_missing(self, tmp_path: Path) -> None:
        model = _make_model(tmp_path / "m", skip=("transformer_1024/model.safetensors",))
        assert missing_artifacts(model) == ["transformer_1024/model.safetensors"]


class TestWeightBytes:
    def test_sums_safetensors(self, tmp_path: Path) -> None:
        model = _make_model(tmp_path / "m")
        (model / "vae/model.safetensors").write_bytes(b"y" * 100)
        total = weight_bytes(model)
        assert total >= 100
        assert total == sum(f.stat().st_size for f in model.rglob("*.safetensors"))

    def test_missing_dir_is_zero(self, tmp_path: Path) -> None:
        assert weight_bytes(tmp_path / "nope") == 0

    def test_ignores_json(self, tmp_path: Path) -> None:
        model = _make_model(tmp_path / "m")
        (model / "model_index.json").write_bytes(b"z" * 500)
        safetensors = [f for f in REQUIRED_ARTIFACTS if f.endswith(".safetensors")]
        assert weight_bytes(model) == 16 * len(safetensors)


class TestResolveModelRef:
    def test_explicit_local_dir(self, tmp_path: Path) -> None:
        model = _make_model(tmp_path / "m")
        assert resolve_model_ref(str(model)) == str(model)

    def test_explicit_missing_dir_falls_back_to_id(self, tmp_path: Path) -> None:
        assert resolve_model_ref(str(tmp_path / "nope")) == str(tmp_path / "nope")

    def test_repo_id_passthrough(self) -> None:
        assert resolve_model_ref("someone/other-model") == "someone/other-model"

    def test_none_prefers_complete_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AIGAMEKIT_MODELS_DIR", str(tmp_path))
        _make_model(tmp_path / "hi3d-v1.0")
        assert resolve_model_ref(None) == str(tmp_path / "hi3d-v1.0")

    def test_none_without_cache_returns_hub_id(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AIGAMEKIT_MODELS_DIR", str(tmp_path))
        assert resolve_model_ref(None) == DEFAULT_MODEL_ID

    def test_expected_weight_bytes_is_plausible(self) -> None:
        assert 7_000_000_000 < EXPECTED_WEIGHT_BYTES < 9_000_000_000

    def test_required_artifacts_are_unique(self) -> None:
        assert len(set(REQUIRED_ARTIFACTS)) == len(REQUIRED_ARTIFACTS)

    def test_paths_module_exposes_module_constant(self) -> None:
        assert paths.REQUIRED_ARTIFACTS[0] == "model_index.json"
