# Instalação — Text2D

## Resumo dos ficheiros

| Ficheiro | Conteúdo |
|----------|----------|
| [`config/requirements.txt`](../config/requirements.txt) | Runtime: diffusers, sdnq, click, rich, … |
| [`config/requirements-dev.txt`](../config/requirements-dev.txt) | Inclui `requirements.txt` + pytest |
| [`scripts/setup.sh`](../scripts/setup.sh) | Cria `.venv`, PyTorch (CUDA/CPU), `pip install -e .` |
| [`scripts/installer.py`](../scripts/installer.py) | Instalação estilo Text3D + wrappers em `--prefix/bin` |
| [`scripts/run_installer.sh`](../scripts/run_installer.sh) | Executa `installer.py` (implementação) |
| [`scripts/install.sh`](../scripts/install.sh) | Delega para `run_installer.sh` (não confundir com `AiGameKit/install.sh` na raiz) |

## `scripts/setup.sh` (recomendado para desenvolvimento)

1. Cria `.venv` na raiz (remove um `.venv` existente).
2. Instala **PyTorch**:
   - Sem `nvidia-smi`: CPU (`download.pytorch.org/whl/cpu`).
   - Com NVIDIA e **Python 3.13+**: `pip install torch torchvision` (PyPI, wheels CUDA alinhados).
   - Com NVIDIA e **Python 3.10–3.12**: índice `cu121` ou `cu118` conforme driver.
3. Instala `config/requirements.txt` e `pip install -e .`.
4. Cria `outputs/images/`.

```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
text2d info
```

## `scripts/installer.py`

Instalação para `~/.local` ou outro `--prefix`:

```bash
chmod +x scripts/run_installer.sh scripts/install.sh
./scripts/run_installer.sh --prefix ~/.local
```

Com **venv** existente (só reinstala o pacote no `.venv` e gera wrappers):

```bash
./scripts/run_installer.sh --use-venv --prefix ~/.local
```

(`./scripts/install.sh` é equivalente a `run_installer.sh`.)

Sem `.venv` e com `--use-venv` → **erro** (mensagem a indicar `./scripts/setup.sh`).

Flags: `--skip-deps`, `--skip-models`, `--force`, `--python`, `INSTALL_PREFIX`.

## Manual

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel
# GPU: ver https://pytorch.org/get-started/locally/
pip install torch torchvision
pip install -r config/requirements.txt
pip install -e .
# opcional: pip install -e ".[dev]"
```

## Testes

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Primeira geração

Na primeira `text2d generate`, o Hub descarrega o modelo (vários GB). Espaço em disco e rede estáveis ajudam. Login opcional:

```bash
huggingface-cli login
```

## Resolução de problemas

Ver [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
