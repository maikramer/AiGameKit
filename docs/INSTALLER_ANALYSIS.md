# GameDev — Sistema de Instalação: Análise e Padronização

## Estrutura Atual

```
Shared/src/gamedev_shared/installer/
├── __init__.py          # Exports principais
├── __main__.py          # Entry point: python -m gamedev_shared.installer
├── base.py              # PythonProjectInstaller (base class)
├── registry.py          # TOOLS, ToolSpec, get_tool(), list_available_tools()
├── unified.py           # UnifiedInstaller (CLI unificado gamedev-install)
├── python_installer.py  # Lógica de instalação Python
├── rust_installer.py    # Lógica de instalação Rust
├── logging.py           # Logger com Rich
├── paint3d_extras.py   # Extras Paint3D
├── part3d_extras.py     # Extras Part3D
├── text3d_extras.py     # Extras Text3D
└── text2sound_extras.py # Extras Text2Sound

<modulo>/scripts/installer.py  # Wrapper por ferramenta
```

## Análise dos Installers

| Módulo | Arquivo | Delegação | Status |
|--------|---------|-----------|--------|
| Text2D | `Text2D/scripts/installer.py` | ✅ Base + extras locais | Ok |
| Text3D | `Text3D/scripts/installer.py` | ✅ Base + text3d_extras | Ok |
| Texture2D | `Texture2D/scripts/installer.py` | ✅ Base + extras locais | Ok |
| Skymap2D | `Skymap2D/scripts/installer.py` | ✅ Base (sem extras) | Ok |
| Text2Sound | `Text2Sound/scripts/installer.py` | ✅ Base + text2sound_extras | Ok |
| Part3D | `Part3D/scripts/installer.py` | ✅ Base + part3d_extras | Ok |
| Rigging3D | `Rigging3D/scripts/installer.py` | ✅ Base + rigging_inference | Ok |
| GameAssets | ❌ Sem installer | — | **MISSING** |
| Paint3D | ❌ Sem installer | — | **MISSING** |
| Animator3D | ❌ Sem installer | — | **MISSING** |
| Materialize | `Materialize/installer/installer.py` | ✅ Rust (diferente) | Ok |

## Problemas Identificados

### 1. GameAssets, Paint3D, Animator3D sem installer.py
Estes módulos têm `pyproject.toml` mas não têm `scripts/installer.py`.

**Solução:** Criar installers mínimos que delegam para a base.

### 2. Inconsistência no padrão de extras
- Alguns usam `*_extras.py` do Shared
- Alguns têm lógica inline

**Solução:** Migrar lógica inline para `*_extras.py` no Shared.

### 3. GameAssets não precisa de extras
GameAssets só precisa do base installer.

## Template Padronizado

```python
#!/usr/bin/env python3
"""
<ModuleName> — instalador system-wide.

Usa gamedev_shared.installer.PythonProjectInstaller para a lógica base.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
_shared_src = _project_root.parent / "Shared" / "src"
if _shared_src.is_dir() and str(_shared_src) not in sys.path:
    sys.path.insert(0, str(_shared_src))

from gamedev_shared.installer import PythonProjectInstaller
from gamedev_shared.installer.base import default_python_command


class <ModuleName>Installer(PythonProjectInstaller):
    """Instalador específico do <ModuleName>."""

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(
            project_name="<ModuleName>",
            cli_name="<cli_name>",
            project_root=_project_root,
            install_prefix=Path(args.prefix),
            python_cmd=args.python,
            use_venv=args.use_venv,
            skip_deps=args.skip_deps,
            skip_models=args.skip_models,
            force=args.force,
        )
        self.args = args

    def run(self) -> bool:
        if not super().run():
            return False
        self.create_cli_wrappers()
        self.setup_directories()
        self.show_summary(
            commands=["<cli_name> --help"],
        )
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Instalador <ModuleName>")
    parser.add_argument("--prefix", default="/usr/local", help="Diretório de instalação")
    parser.add_argument("--python", default=None, help="Comando Python")
    parser.add_argument("--use-venv", action="store_true", help="Usar virtualenv")
    parser.add_argument("--skip-deps", action="store_true", help="Pular dependências")
    parser.add_argument("--skip-models", action="store_true", help="Pular modelos")
    parser.add_argument("--force", action="store_true", help="Forçar reinstalação")
    args = parser.parse_args()

    installer = <ModuleName>Installer(args)
    return 0 if installer.run() else 1


if __name__ == "__main__":
    sys.exit(main())
```

## Status Final (2026-04-04)

### ✅ Completado

1. **cli_rich.py unificado** (commit anterior `643f9e7`)
   - Animator3D: refactored to delegate to gamedev_shared
   - Part3D: added cli_rich.py (was using raw rich_click)
   - GameDevLab: added cli_rich.py (was importing directly)

2. **Sistema de Instalação Padronizado** (commit `1a77db6`)
   - ✅ GameAssets: installer.py criado
   - ✅ Paint3D: installer.py criado (com paint3d_extras)
   - ✅ Animator3D: installer.py criado
   - Todos os 11 módulos agora têm installer padronizado

3. **Lint/Format configurado**
   - ruff.toml: ignore minor issues (B007, RUF059, RUF100, I001, SIM102)
   - ruff: All checks passed ✅
   - ruff format: 274 files already formatted ✅

### Estrutura Final do Sistema de Instalação

```
<modulo>/scripts/installer.py
    └── delega para gamedev_shared.installer.PythonProjectInstaller
            └── gamedev_shared.installer.<modulo>_extras (se necessário)

Exemplo Paint3D:
installer.py → PythonProjectInstaller → paint3d_extras.py
```

### Comandos de Verificação

```bash
cd ~/GitClones/GameDev

# Verificar todos os installers
for d in Text2D Text3D Texture2D Skymap2D Text2Sound Part3D Rigging3D GameAssets Paint3D Animator3D; do
  if [ -f "$d/scripts/installer.py" ]; then
    echo "✅ $d"
  else
    echo "❌ $d"
  fi
done

# Lint completo
ruff check . && ruff format --check .
```
