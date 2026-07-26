# Instalação - Text3D

Guia completo de instalação do Text3D em diferentes configurações.

## Índice

- [Requisitos](#requisitos)
- [Dependências de finish (KTX2 + meshopt)](#dependências-de-finish-ktx2--meshopt)
- [Método 1: Virtual Environment (Recomendado)](#método-1-virtual-environment-recomendado)
- [Método 2: System-Wide](#método-2-system-wide)
- [Método 3: pip install](#método-3-pip-install)
- [Download de Modelos](#download-de-modelos)
- [Variáveis de Ambiente](#variáveis-de-ambiente)
- [Troubleshooting de Instalação](#troubleshooting-de-instalação)

## Requisitos

### Sistema

- **Python**: 3.8 ou superior (3.10+ recomendado)
- **Sistema Operacional**: Linux (Ubuntu 20.04+), macOS, Windows (WSL recomendado)
- **RAM**: 8GB mínimo, 16GB recomendado
- **Disco**: 10GB livre mínimo, 20GB recomendado

### GPU (Opcional mas Recomendada)

- **NVIDIA GPU** com suporte CUDA
- **VRAM**: 6GB+ para modo normal, 4GB+ para modo economia
- **Drivers**: NVIDIA 520+ ou CUDA 11.8/12.1

### Pacotes do Sistema

Ubuntu/Debian:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Para GPUs NVIDIA:
```bash
sudo apt install -y nvidia-driver-535 nvidia-cuda-toolkit
```

## Dependências de finish (KTX2 + meshopt)

Necessárias para `text3d finish`, `bake-master` e LOD com compressão (entregáveis
validados por `gamedev-lab check` com `texture_format: ktx2` / `compression: meshopt`).

| Dep | Papel | Instalação tip. |
|-----|-------|-----------------|
| Node.js + `npx` | `@gltf-transform/cli` (dedup/prune/uastc/meshopt) | [nodejs.org](https://nodejs.org/) / nvm |
| `ktx` (KTX-Software) | **Obrigatório** para UASTC → KTX2 | `./install.sh text3d` (extras) ou [releases Khronos](https://github.com/KhronosGroup/KTX-Software/releases) → `~/.local/opt/KTX-Software` |
| `libmeshoptimizer-dev` | meshopt nativo bpy 5.2+ | `sudo apt install libmeshoptimizer-dev` |

```bash
# Preferido no monorepo (venv + wrappers + ktx user-local)
./install.sh text3d

text3d doctor   # deve listar npx, @gltf-transform/cli, ktx, meshopt (bpy)
```

Guia operacional: [`../../docs/GLB_FINISH_COMPRESSION.md`](../../docs/GLB_FINISH_COMPRESSION.md).

## Método 1: Virtual Environment (Recomendado)

Melhor para desenvolvimento e isolamento de dependências.

### Passo 1: Clone o Repositório

```bash
git clone <repository-url> text3d
cd text3d
```

### Passo 2: Executar Setup

```bash
chmod +x setup.sh
./setup.sh
```

O script `setup.sh` irá:
1. Detectar Python 3.8+
2. Criar ambiente virtual (`.venv/`)
3. Detectar CUDA e instalar PyTorch compatível
4. Instalar todas as dependências Python
5. Configurar diretórios de saída (`outputs/`)
6. Verificar instalação

### Passo 3: Ativar e Usar

```bash
source .venv/bin/activate
text3d --help
```

### Desativar

```bash
deactivate
```

## Método 2: System-Wide

Para uso global no sistema sem virtualenv.

### Usando install.sh

```bash
chmod +x install.sh
sudo ./install.sh
```

### Opções do install.sh

```bash
--prefix DIR       # Diretório de instalação (padrão: /usr/local)
--skip-deps        # Pular instalação de pacotes do sistema
--skip-models      # Pular download dos modelos HF
--force            # Forçar reinstalação
--python CMD       # Comando Python a usar (padrão: python3)
```

### Exemplos

```bash
# Instalação padrão
sudo ./install.sh

# Instalação local (sem sudo)
./install.sh --prefix ~/.local

# Pular dependências do sistema (já instaladas)
sudo ./install.sh --skip-deps

# Pular download de modelos
sudo ./install.sh --skip-models

# Reinstalar
sudo ./install.sh --force
```

### Variáveis de Ambiente

```bash
INSTALL_PREFIX=/opt/text3d    # Diretório de instalação
PYTHON_CMD=python3.10         # Python específico
TEXT3D_OUTPUT_DIR=/path       # Diretório de saída padrão
TEXT3D_MODELS_DIR=/path       # Cache de modelos
```

## Método 3: pip install

Para usuários Python que preferem pip.

### Instalação Editável (Desenvolvimento)

```bash
cd text3d
pip install -e .
```

### Instalação Normal

```bash
cd text3d
pip install .
```

### Instalação de Dependências Manuais

```bash
# PyTorch com CUDA 11.8
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

# Ou PyTorch com CUDA 12.1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# Ou CPU-only
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Resto das dependências
pip install -r requirements.txt
```

## Download de Modelos

O Text3D usa modelos Shap-E do Hugging Face (~4.9 GB cada).

### Download Automático

Na primeira execução, os modelos são baixados automaticamente para `~/.cache/huggingface/`.

### Download Manual (Recomendado)

Para evitar downloads na primeira execução:

```bash
# Login Hugging Face (se necessário para modelos gated)
huggingface-cli login

# Baixar modelo text-to-3D
huggingface-cli download openai/shap-e \
    --local-dir ./models/shap-e \
    --local-dir-use-symlinks False

# Baixar modelo image-to-3D
huggingface-cli download openai/shap-e-img2img \
    --local-dir ./models/shap-e-img2img \
    --local-dir-use-symlinks False

# Continuar download interrompido
huggingface-cli download openai/shap-e \
    --local-dir ./models/shap-e \
    --local-dir-use-symlinks False \
    --resume-download
```

### Verificar Modelos

```bash
# Listar modelos cacheados
huggingface-cli scan-cache

# Limpar cache antigo
huggingface-cli delete-cache
```

## Variáveis de Ambiente

### Cache e Diretórios

```bash
# Diretório base Hugging Face (inclui cache de modelos)
export HF_HOME=/path/to/cache

# Diretório específico para datasets
export HF_DATASETS_CACHE=/path/to/datasets

# Diretório específico para transformers
export TRANSFORMERS_CACHE=/path/to/transformers

# Cache de modelos Text3D
export TEXT3D_MODELS_DIR=/path/to/models

# Diretório padrão de saída
export TEXT3D_OUTPUT_DIR=/path/to/outputs
```

### GPU/CUDA

```bash
# Selecionar GPU específica
export CUDA_VISIBLE_DEVICES=0

# Forçar modo CPU
export USE_CPU=1

# Limitar memória GPU (PyTorch)
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
```

### Hugging Face

```bash
# Token de acesso (para modelos gated)
export HF_TOKEN=seu_token_aqui

# Offline mode (não baixar nada)
export HF_HUB_OFFLINE=1
```

## Troubleshooting de Instalação

### PyTorch sem CUDA

```bash
# Verificar se CUDA está disponível
python -c "import torch; print(torch.cuda.is_available())"

# Se False, reinstalar PyTorch com CUDA
pip uninstall torch torchvision -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

# Ou para CUDA 12.1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

### Permissões no setup.sh

```bash
chmod +x setup.sh
./setup.sh
```

### Erro "command not found" após instalação

System-wide:
```bash
# Verificar se está no PATH
which text3d

# Se não, adicionar ao PATH
export PATH=$PATH:/usr/local/bin  # ou seu prefixo
```

Virtualenv:
```bash
# Verificar se venv está ativado
which python
# Deve mostrar: /path/to/.venv/bin/python

# Reativar se necessário
source .venv/bin/activate
```

### Conflitos de Dependências

```bash
# Limpar e reinstalar
pip uninstall text3d -y
pip cache purge
pip install -e . --force-reinstall --no-deps
pip install -r requirements.txt
```

### Download de Modelos Falha

```bash
# Verificar conexão
ping huggingface.co

# Usar mirror (China)
export HF_ENDPOINT=https://hf-mirror.com

# Download manual via browser e colocar em ./models/
```

### Erro de Memória na Instalação

```bash
# Usar pip com menos workers
pip install -r requirements.txt --no-deps
pip install torch torchvision --no-cache-dir
```

## Atualização

### Via setup.sh

```bash
git pull origin main
./setup.sh
```

### Via pip

```bash
pip install --upgrade -e .
```

### Via install.sh

```bash
sudo ./install.sh --force
```

## Desinstalação

### Virtualenv

```bash
rm -rf .venv/
```

### System-Wide

```bash
sudo ./install.sh --uninstall
# ou
sudo rm /usr/local/bin/text3d
sudo rm -rf /usr/local/lib/python*/site-packages/text3d*
```

### pip

```bash
pip uninstall text3d -y
```
