"""Registry de backends — carrega ``data/backends.yaml`` e resolve adapters por lazy import.

O registry é declarativo (YAML) e resolution de adapter é lazy: só importamos
o módulo da tool quando o backend é efetivamente pedido. Isto mantém o UMS
importável sem todas as deps GPU (torch/diffusers/stable-audio) instaladas.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, resources
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class BackendDescriptor:
    """Descriptor declarativo de um backend (uma ferramenta GPU).

    Attributes:
        name: Identificador canónico (ex: ``text2icon``).
        adapter: Dotted path do módulo que exporta a classe ``BackendAdapter``.
        vram_mib: Estimativa do footprint em VRAM (MiB) quando carregado.
        priority: Prioridade de evicção — valores MAIORES = menos provável de ser
            evicted (backends "pesados" que compensa manter quentes). Tie-break: LRU.
        footprint_key: Chave do registry ``aigamekit_shared.lowvram.FOOTPRINTS`` (ex:
            ``"flux-klein-9b"``). Se definida, o ``vram_mib`` é derivado do footprint
            (mais preciso que o valor estático). Opcional.
        tool: Nome da tool monorepo (ex: ``text3d``, ``paint3d``) para o modo
            subprocess-per-backend. Se definido, o BackendManager despacha jobs
            para um worker persistente no venv da tool em vez de importar o
            adapter in-process. ``None`` = backend in-process (durante migração).
    """

    name: str
    adapter: str
    vram_mib: int
    priority: int
    footprint_key: str | None = None
    tool: str | None = None


def _default_yaml_path() -> str:
    """Path canónico do ``backends.yaml`` empacotado (package-data)."""
    return str(resources.files("modelserver").joinpath("data", "backends.yaml"))


def load_descriptors(yaml_path: str | None = None) -> dict[str, BackendDescriptor]:
    """Carrega descriptors de backends a partir do YAML.

    Args:
        yaml_path: Path alternativo. Se ``None``, usa o ``data/backends.yaml``
            empacotado com o package (permite override para testes).

    Returns:
        Dict ``{name: BackendDescriptor}``.

    Raises:
        FileNotFoundError: YAML não existe.
        ValueError: YAML malformado ou entrada sem ``backends``.
    """
    path = yaml_path or _default_yaml_path()
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "backends" not in data:
        raise ValueError(f"backends.yaml malformado: falta a chave 'backends' ({path})")

    entries = data["backends"]
    if not isinstance(entries, list):
        raise ValueError(f"backends.yaml: 'backends' deve ser uma lista ({path})")

    descriptors: dict[str, BackendDescriptor] = {}
    for entry in entries:
        descriptors[entry["name"]] = BackendDescriptor(
            name=entry["name"],
            adapter=entry["adapter"],
            vram_mib=int(entry["vram_mib"]),
            priority=int(entry.get("priority", 0)),
            footprint_key=entry.get("footprint_key"),
            tool=entry.get("tool"),
        )
    return descriptors


class Registry:
    """Registry de backends com resolução lazy de adapters.

    Mantém os ``BackendDescriptor`` (data estática do YAML) e instancia a classe
    ``BackendAdapter`` de cada backend sob procura (lazy import do módulo da tool).
    """

    def __init__(
        self, descriptors: dict[str, BackendDescriptor] | None = None, *, yaml_path: str | None = None
    ) -> None:
        self._descriptors = descriptors if descriptors is not None else load_descriptors(yaml_path)
        self._adapter_instances: dict[str, object] = {}

    @property
    def names(self) -> list[str]:
        """Nomes de todos os backends registados."""
        return list(self._descriptors)

    def descriptor(self, name: str) -> BackendDescriptor:
        """Retorna o descriptor de um backend.

        Raises:
            KeyError: Backend não registado.
        """
        if name not in self._descriptors:
            raise KeyError(f"Backend desconhecido: {name!r}. Disponíveis: {sorted(self._descriptors)}")
        return self._descriptors[name]

    def has(self, name: str) -> bool:
        """True se o backend ``name`` está registado."""
        return name in self._descriptors

    def adapter(self, name: str) -> object:
        """Retorna a instância do ``BackendAdapter`` para ``name`` (lazy import + cache).

        O módulo do adapter só é importado na primeira invocação. A instância é
        cacheada — adapters são stateless quanto ao modelo (o modelo vive no
        BackendManager, não no adapter).

        Raises:
            KeyError: Backend não registado.
            ImportError: Módulo do adapter não encontrável (deps da tool em falta).
        """
        if name in self._adapter_instances:
            return self._adapter_instances[name]

        desc = self.descriptor(name)
        module = import_module(desc.adapter)
        # Convenção: cada módulo adapter exporta uma classe sem argumentos que
        # implementa o contrato (load/generate/unload). Instanciamos sem estado.
        cls = getattr(module, "Adapter", None)
        if cls is None:
            raise ImportError(f"Adapter {desc.adapter} não exporta a classe 'Adapter'")
        instance = cls()
        self._adapter_instances[name] = instance
        return instance

    def __iter__(self) -> Iterator[BackendDescriptor]:
        return iter(self._descriptors.values())

    def __len__(self) -> int:
        return len(self._descriptors)
