"""Testes do registry: carregar YAML, resolver descriptors, lazy import de adapters."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from modelserver.registry import BackendDescriptor, Registry, load_descriptors


class TestLoadDescriptors:
    """Carregar descriptors do backends.yaml."""

    def test_loads_default_yaml_has_9_backends(self) -> None:
        """O YAML empacotado tem exatamente os 9 backends GPU esperados."""
        descs = load_descriptors()
        expected = {"text2icon", "texture2d", "text2d", "skymap2d", "text3d", "paint3d", "part3d", "text2sound", "terrain3d"}
        assert set(descs) == expected

    def test_descriptors_have_required_fields(self) -> None:
        descs = load_descriptors()
        for name, d in descs.items():
            assert d.name == name
            assert isinstance(d.vram_mib, int) and d.vram_mib > 0
            assert isinstance(d.priority, int)
            assert d.adapter.startswith("modelserver.adapters.")

    def test_load_custom_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "custom.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "backends": [
                        {"name": "foo", "adapter": "pkg.mod.foo", "vram_mib": 1234, "priority": 5},
                    ]
                }
            )
        )
        descs = load_descriptors(str(yaml_path))
        assert "foo" in descs
        assert descs["foo"].vram_mib == 1234
        assert descs["foo"].priority == 5

    def test_malformed_yaml_no_backends_key(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text("not_backends: []")
        with pytest.raises(ValueError, match="backends"):
            load_descriptors(str(yaml_path))

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_descriptors(str(tmp_path / "nonexistent.yaml"))


class TestRegistry:
    """Registry: lookup de descriptors, lazy resolution de adapters."""

    def test_names_and_len(self) -> None:
        registry = Registry()
        assert len(registry) == 9
        assert "text2icon" in registry.names

    def test_descriptor_existing(self) -> None:
        registry = Registry()
        d = registry.descriptor("text3d")
        assert d.name == "text3d"
        assert d.vram_mib == 8000

    def test_descriptor_unknown_raises(self) -> None:
        registry = Registry()
        with pytest.raises(KeyError, match="Backend desconhecido"):
            registry.descriptor("nope")

    def test_has(self) -> None:
        registry = Registry()
        assert registry.has("text2icon")
        assert not registry.has("nope")

    def test_adapter_lazy_import_unknown_module(self) -> None:
        """Adapter resolution deve falhar graciosamente se o módulo não existe."""
        registry = Registry(descriptors={"x": BackendDescriptor(name="x", adapter="nonexistent.pkg.mod", vram_mib=100, priority=1)})
        with pytest.raises(ImportError):
            registry.adapter("x")

    def test_iter_descriptors(self) -> None:
        registry = Registry()
        names = [d.name for d in registry]
        assert len(names) == 9
        assert "text2icon" in names
