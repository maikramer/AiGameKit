"""Registry de ferramentas do monorepo GameDev.

Define metadata (tipo, caminhos, nomes) para cada ferramenta instalável
(Python, Rust ou Bun/TypeScript), permitindo que um CLI unificado descubra
e instale qualquer uma delas.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ToolKind(Enum):
    PYTHON = "python"
    RUST = "rust"
    BUN = "bun"


@dataclass(frozen=True)
class ToolSpec:
    """Especificação de uma ferramenta instalável."""

    name: str
    kind: ToolKind
    folder: str
    cli_name: str
    description: str

    cargo_bin_name: str = ""
    python_module: str = ""
    min_python: tuple[int, int] = (3, 10)
    extra_aliases: tuple[str, ...] = ()
    needs_pytorch: bool = False
    needs_cuda: bool = False

    def project_root(self, monorepo: Path) -> Path:
        return monorepo / self.folder

    def exists(self, monorepo: Path) -> bool:
        root = self.project_root(monorepo)
        if self.kind == ToolKind.PYTHON:
            return (root / "setup.py").is_file() or (root / "pyproject.toml").is_file()
        if self.kind == ToolKind.RUST:
            return (root / "Cargo.toml").is_file()
        if self.kind == ToolKind.BUN:
            return (root / "package.json").is_file()
        return root.is_dir()


TOOLS: dict[str, ToolSpec] = {
    "text2d": ToolSpec(
        name="Text2D",
        kind=ToolKind.PYTHON,
        folder="Text2D",
        cli_name="text2d",
        python_module="text2d",
        description="CLI text-to-image com FLUX.2 Klein (SDNQ)",
        min_python=(3, 10),
        extra_aliases=("text2d-generate",),
        needs_pytorch=True,
        needs_cuda=True,
    ),
    "text3d": ToolSpec(
        name="Text3D",
        kind=ToolKind.PYTHON,
        folder="Text3D",
        cli_name="text3d",
        python_module="text3d",
        description="Pipeline text-to-3D (Text2D + Hunyuan3D)",
        min_python=(3, 10),
        extra_aliases=("text3d-generate",),
        needs_pytorch=True,
        needs_cuda=True,
    ),
    "gameassets": ToolSpec(
        name="GameAssets",
        kind=ToolKind.PYTHON,
        folder="GameAssets",
        cli_name="gameassets",
        python_module="gameassets",
        description="Batch de prompts/assets 2D/3D alinhados ao jogo",
        min_python=(3, 10),
        needs_pytorch=False,
    ),
    "gamedevlab": ToolSpec(
        name="GameDevLab",
        kind=ToolKind.PYTHON,
        folder="GameDevLab",
        cli_name="gamedev-lab",
        python_module="gamedev_lab",
        description="Laboratório: debug 3D, bancadas de quantização, profiling",
        min_python=(3, 10),
        needs_pytorch=False,
    ),
    "text2sound": ToolSpec(
        name="Text2Sound",
        kind=ToolKind.PYTHON,
        folder="Text2Sound",
        cli_name="text2sound",
        python_module="text2sound",
        description="CLI text-to-audio com Stable Audio Open 1.0",
        min_python=(3, 10),
        extra_aliases=("text2sound-generate",),
        needs_pytorch=True,
        needs_cuda=True,
    ),
    "texture2d": ToolSpec(
        name="Texture2D",
        kind=ToolKind.PYTHON,
        folder="Texture2D",
        cli_name="texture2d",
        python_module="texture2d",
        description="Texturas 2D seamless via HF Inference API (sem GPU local)",
        min_python=(3, 10),
        extra_aliases=("texture2d-generate",),
        needs_pytorch=False,
    ),
    "skymap2d": ToolSpec(
        name="Skymap2D",
        kind=ToolKind.PYTHON,
        folder="Skymap2D",
        cli_name="skymap2d",
        python_module="skymap2d",
        description="Skymaps equirectangular 360° via HF Inference API (sem GPU local)",
        min_python=(3, 10),
        extra_aliases=("skymap2d-generate",),
        needs_pytorch=False,
    ),
    "terrain3d": ToolSpec(
        name="Terrain3D",
        kind=ToolKind.PYTHON,
        folder="Terrain3D",
        cli_name="terrain3d",
        python_module="terrain3d",
        description="AI terrain generation via diffusion models (terrain-diffusion; vendored; CUDA GPU)",
        min_python=(3, 10),
        needs_pytorch=True,
        needs_cuda=True,
    ),
    "rigging3d": ToolSpec(
        name="Rigging3D",
        kind=ToolKind.PYTHON,
        folder="Rigging3D",
        cli_name="rigging3d",
        python_module="rigging3d",
        description="UniRig empacotado + CLI (auto-rigging 3D; PyTorch/CUDA; bpy 5.0.x / Python 3.11-3.12)",
        min_python=(3, 11),
        needs_pytorch=True,
        needs_cuda=True,
    ),
    "animator3d": ToolSpec(
        name="Animator3D",
        kind=ToolKind.PYTHON,
        folder="Animator3D",
        cli_name="animator3d",
        python_module="animator3d",
        description="Animação 3D com bpy (Blender Python) — complementa Rigging3D",
        min_python=(3, 13),
        needs_pytorch=False,
        needs_cuda=False,
    ),
    "part3d": ToolSpec(
        name="Part3D",
        kind=ToolKind.PYTHON,
        folder="Part3D",
        cli_name="part3d",
        python_module="part3d",
        description="Decomposição semântica de meshes 3D via Hunyuan3D-Part (P3-SAM + X-Part)",
        min_python=(3, 10),
        extra_aliases=("part3d-decompose",),
        needs_pytorch=True,
        needs_cuda=True,
    ),
    "paint3d": ToolSpec(
        name="Paint3D",
        kind=ToolKind.PYTHON,
        folder="Paint3D",
        cli_name="paint3d",
        python_module="paint3d",
        description="Texturização 3D: Hunyuan3D-Paint 2.1 (PBR nativo) + Upscale IA",
        min_python=(3, 13),
        needs_pytorch=True,
        needs_cuda=True,
    ),
    "materialize": ToolSpec(
        name="Materialize CLI",
        kind=ToolKind.RUST,
        folder="Materialize",
        cli_name="materialize",
        cargo_bin_name="materialize-cli",
        description="PBR maps (normal, AO, metallic, smoothness) via GPU/wgpu",
    ),
    "vibegame": ToolSpec(
        name="VibeGame",
        kind=ToolKind.BUN,
        folder="VibeGame",
        cli_name="vibegame",
        description="Motor 3D (TypeScript/Bun); CLI `vibegame create` + build da biblioteca",
    ),
}


def _walk_monorepo_root(start: Path) -> Path | None:
    """Percorre ascendentes; devolve raiz se ``.git`` e ``Shared/`` existirem."""
    current = start
    for _ in range(10):
        if (current / ".git").exists() and (current / "Shared").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def try_find_monorepo_root(start: Path | None = None) -> Path | None:
    """Como :func:`find_monorepo_root`, mas devolve ``None`` se não encontrar."""
    if start is None:
        start = Path(__file__).resolve()
    return _walk_monorepo_root(start)


def find_monorepo_root(start: Path | None = None) -> Path:
    """Encontra a raiz do monorepo GameDev navegando para cima.

    Procura pasta com ``.git`` + ``Shared/``.
    """
    if start is None:
        start = Path(__file__).resolve()
    found = _walk_monorepo_root(start)
    if found is not None:
        return found
    raise FileNotFoundError("Raiz do monorepo GameDev não encontrada. Execute a partir de dentro do repositório.")


def list_available_tools(monorepo: Path | None = None) -> list[ToolSpec]:
    """Lista ferramentas cujo directório existe no monorepo."""
    if monorepo is None:
        monorepo = find_monorepo_root()
    return [spec for spec in TOOLS.values() if spec.exists(monorepo)]


def get_tool(name: str) -> ToolSpec:
    """Retorna spec de uma ferramenta pelo nome (case-insensitive).

    Raises:
        KeyError: Ferramenta não conhecida.
    """
    key = name.lower().replace("-", "").replace("_", "")
    for k, spec in TOOLS.items():
        if k == key or spec.cli_name == key or spec.name.lower().replace(" ", "") == key:
            return spec
        for alias in spec.extra_aliases:
            if alias.lower().replace("-", "").replace("_", "") == key:
                return spec
    raise KeyError(f"Ferramenta desconhecida: {name!r}. Disponíveis: {', '.join(TOOLS.keys())}")
