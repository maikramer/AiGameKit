"""Helpers partilhados pelos testes do UMS (adapters mock, registry de teste)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from modelserver.adapters.base import BackendAdapter
from modelserver.registry import BackendDescriptor, Registry


class MockModel:
    """Model object fake para testar load/generate/unload lifecycle."""

    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.kwargs = kwargs
        self.unloaded = False
        self.generate_calls = 0

    def generate(self, **kwargs: Any) -> tuple[Any, dict]:
        self.generate_calls += 1
        return ("image-bytes", {"prompt_final": kwargs.get("prompt", ""), "seed": 42})


class MockAdapter(BackendAdapter):
    """Adapter mock que regista load/generate/unload para asserções."""

    def __init__(self, name: str = "mock", *, fail_generate: bool = False) -> None:
        self.name = name
        self.fail_generate = fail_generate
        self.load_calls = 0
        self.unload_calls = 0

    def load(self, **kwargs: Any) -> MockModel:
        self.load_calls += 1
        return MockModel(self.name, **kwargs)

    def generate(self, model: MockModel, request: dict[str, Any]) -> dict[str, Any]:
        if self.fail_generate:
            raise RuntimeError("generate failed (mock)")
        prompt = request.get("prompt", "")
        return {"status": "ok", "output": f"/tmp/mock-{model.name}.png", "prompt": prompt, "seed": 42}

    def unload(self, model: MockModel) -> None:
        self.unload_calls += 1
        model.unloaded = True


def make_mock_registry(
    specs: dict[str, tuple[int, int]] | None = None,
    *,
    yaml_path: str | None = None,
) -> Registry:
    """Cria um Registry com adapters mock para os backends dados.

    Args:
        specs: ``{name: (vram_mib, priority)}``. Default: 3 backends de teste.
        yaml_path: Path do YAML a usar (gera ficheiro temporário).

    Returns:
        Registry cujos adapters são ``MockAdapter`` instâncias controladas.
    """
    import yaml as _yaml

    if specs is None:
        specs = {"alpha": (1000, 10), "beta": (3000, 30), "gamma": (5000, 50)}

    if yaml_path is None:
        raise ValueError("yaml_path obrigatório para make_mock_registry (usa tmp_path dos testes)")

    entries = [{"name": n, "adapter": f"_mock_{n}", "vram_mib": v, "priority": p} for n, (v, p) in specs.items()]
    Path(yaml_path).write_text(_yaml.safe_dump({"backends": entries}))

    descriptors = {
        n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
    }
    registry = Registry(descriptors=descriptors)
    # Injetar adapters mock no cache do registry (chave = backend name, não adapter path).
    for n in specs:
        registry._adapter_instances[n] = MockAdapter(name=n)
    return registry
