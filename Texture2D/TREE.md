# Texture2D — Estrutura do Projeto

```
Texture2D/
├── config/
│   └── requirements-dev.txt          # Dependências de desenvolvimento
├── scripts/
│   └── installer.py                  # Instalador system-wide
├── src/
│   └── texture2d/
│       ├── __init__.py               # Versão do pacote
│       ├── __main__.py               # python -m texture2d
│       ├── _validate_cli.py          # Comando validate-tileable
│       ├── cli.py                    # CLI principal (Click + Rich)
│       ├── cli_rich.py               # rich-click via aigamekit_shared
│       ├── client.py                 # Cliente do model server
│       ├── cursor_skill/
│       │   └── SKILL.md              # Agent Skill do Cursor
│       ├── generator.py              # TextureGenerator (SD1.5 + circular padding)
│       ├── hardware.py               # Auto-detecção de hardware
│       ├── image_processor.py        # save_image, ZIP, metadata JSON
│       ├── presets.py                # 13 presets de materiais
│       ├── prompt_enhancer.py        # Enhancers de prompt chão/top-down
│       ├── server.py                 # Model server (mantém pipeline carregado)
│       ├── tileability.py            # Helpers de tileability
│       └── utils.py                  # Validação, seeds, helpers
├── tests/
│   ├── __init__.py
│   ├── test_cli_smoke.py
│   ├── test_generator.py
│   ├── test_hardware.py
│   ├── test_image_processor.py
│   ├── test_presets.py
│   ├── test_prompt_enhancer.py
│   ├── test_texture2d_extended.py
│   ├── test_tileability.py
│   ├── test_utils.py
│   └── test_validate_cli.py
├── pyproject.toml                    # Metadata + dependências (setuptools)
├── pytest.ini                        # Configuração do pytest
├── activate.sh                       # Atalho para ativar o venv
├── README.md                         # Documentação (English)
├── README_PT.md                      # Documentação (Português)
├── LICENSE                           # MIT
└── TREE.md                           # Este ficheiro
```
