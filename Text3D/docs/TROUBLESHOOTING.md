# Troubleshooting - Text3D

Guia completo de resolução de problemas comuns.

## Índice

- [Problemas de Memória](#problemas-de-memória)
- [Problemas de GPU/CUDA](#problemas-de-gpucuda)
- [Problemas de Instalação](#problemas-de-instalação)
- [Problemas de Modelos](#problemas-de-modelos)
- [Problemas de Qualidade](#problemas-de-qualidade)
- [Erros Comuns](#erros-comuns)

## Problemas de Memória

### Out of Memory (OOM)

**Sintoma:** Erro `RuntimeError: CUDA out of memory` ou travamento.

**Soluções:**

1. **Use preset fast ou balanced (hw-auto aplica SDNQ INT4 em GPUs pequenas):**
```bash
text3d generate "prompt" --preset balanced --steps 24
```

2. **Reduza frame-size:**
```bash
text3d generate "prompt" --frame-size 128 --steps 32
```

3. **Use CPU:**
```bash
text3d generate "prompt" --cpu --steps 16
```

4. **Limite memória PyTorch:**
```bash
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
```

### VRAM Não Liberada

**Sintoma:** VRAM continua cheia após geração.

**Solução:**
```python
import torch
# Limpar cache CUDA
torch.cuda.empty_cache()

# Ou reiniciar Python
```

### Memory Leak em Batch

**Sintoma:** Memória aumenta a cada geração em loop.

**Solução:**
```python
import torch
from text3d import ShapEGenerator

with ShapEGenerator() as gen:
    for prompt in prompts:
        mesh = gen.generate(prompt)
        # Processar mesh...
        
        # Limpar entre iterações
        torch.cuda.empty_cache()
```

## Problemas de GPU/CUDA

### CUDA Not Available

**Sintoma:** `torch.cuda.is_available()` retorna `False`.

**Diagnóstico:**
```bash
# Verificar instalação PyTorch
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
python -c "import torch; print(f'CUDA version: {torch.version.cuda}')"

# Verificar drivers NVIDIA
nvidia-smi

# Verificar CUDA toolkit
nvcc --version
```

**Soluções:**

1. **Reinstalar PyTorch com CUDA:**
```bash
pip uninstall torch torchvision -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

2. **Verificar compatibilidade:**
- PyTorch CUDA 11.8 → NVIDIA driver 520+
- PyTorch CUDA 12.1 → NVIDIA driver 525+

3. **Instalar drivers atualizados:**
```bash
# Ubuntu
sudo apt update
sudo apt install nvidia-driver-535
sudo reboot
```

### Múltiplas GPUs

**Selecionar GPU específica:**
```bash
export CUDA_VISIBLE_DEVICES=0  # Usar primeira GPU
export CUDA_VISIBLE_DEVICES=1  # Usar segunda GPU
export CUDA_VISIBLE_DEVICES=0,1  # Usar múltiplas
```

### GPU Não Detectada no WSL

**Solução:**
```bash
# Verificar WSL está atualizado
wsl --update

# Instalar CUDA no WSL
sudo apt install nvidia-cuda-toolkit
```

## Problemas de Instalação

### ImportError: No module named 'text3d'

**Causa:** Ambiente não ativado ou instalação incompleta.

**Soluções:**

1. **Ativar venv:**
```bash
source .venv/bin/activate
```

2. **Verificar instalação:**
```bash
pip list | grep text3d
```

3. **Reinstalar:**
```bash
pip install -e . --force-reinstall
```

### Conflitos de Dependências

**Sintoma:** Erros de versão ao importar.

**Solução:**
```bash
# Limpar tudo
pip uninstall text3d torch torchvision diffusers -y
pip cache purge

# Reinstalar limpo
pip install -e .
```

### Erro de Permissão

**Sintoma:** `Permission denied` ao executar scripts.

**Solução:**
```bash
chmod +x setup.sh
chmod +x install.sh
./setup.sh
```

## Problemas de Modelos

### Modelo Não Encontrado

**Sintoma:** Erro ao carregar modelo ou download falha.

**Soluções:**

1. **Download manual:**
```bash
huggingface-cli download openai/shap-e \
    --local-dir ./models/shap-e \
    --local-dir-use-symlinks False
```

2. **Verificar conexão:**
```bash
ping huggingface.co
```

3. **Usar mirror (China):**
```bash
export HF_ENDPOINT=https://hf-mirror.com
```

### Modelo Corrompido

**Sintoma:** Erros estranhos ou resultados ruins após download.

**Solução:**
```bash
# Limpar cache
huggingface-cli delete-cache

# Ou manualmente
rm -rf ~/.cache/huggingface/hub/models--openai--shap-e

# Baixar novamente
huggingface-cli download openai/shap-e --local-dir ./models/shap-e
```

### Token Hugging Face

**Sintoma:** Erro de autenticação para modelos gated.

**Solução:**
```bash
# Login
huggingface-cli login

# Ou via token
export HF_TOKEN=seu_token_aqui
```

## Problemas de Qualidade

### Modelo Estranho/Distorcido

**Possíveis causas e soluções:**

1. **Guidance muito baixo:**
```bash
# Aumentar guidance
text3d generate "prompt" --guidance 20.0
```

2. **Passos insuficientes:**
```bash
# Aumentar steps
text3d generate "prompt" --steps 64
```

3. **Prompt muito vago:**
```bash
# Evitar: "car"
# Usar: "a red sports car with black wheels"
```

### Textura Ruim ou Ausente

**Causa:** Shap-E gera meshes com cores por vértice, não texturas UV.

**Solução:**
- Use formato GLB (preserva cores por vértice)
- Para texturas UV, use software externo (Blender, Substance)

### Mesh com Buracos

**Causa:** Geração com poucos passos ou parâmetros agressivos.

**Solução:**
```bash
# Aumentar qualidade
text3d generate "prompt" --steps 64 --guidance 15.0
```

## Erros Comuns

### FileNotFoundError: Arquivo de Saída

**Causa:** Diretório de saída não existe.

**Solução:**
```bash
# Criar diretório
mkdir -p outputs/meshes

# Ou especificar caminho completo
text3d generate "prompt" --output ./meus_modelos/modelo.glb
```

### KeyError: 'vertices'

**Causa:** Problema na geração ou formato inesperado.

**Solução:**
```python
# Verificar mesh foi gerado
if mesh is None:
    print("Erro na geração")
    return

# Verificar propriedades
print(dir(mesh))
print(mesh.vertices.shape)
```

### RuntimeError: Expected all tensors on same device

**Causa:** Tensores em dispositivos diferentes (CPU vs CUDA).

**Solução:**
```python
# Forçar device consistente
mesh = mesh.apply_translation([0, 0, 0])  # Operação no device correto
```

### ValueError: Unsupported format

**Causa:** Formato de arquivo não suportado.

**Formatos suportados:**
- `.glb` - Binary glTF
- `.ply` - Stanford Polygon
- `.obj` - Wavefront OBJ

**Solução:**
```bash
# Verificar extensão
# Use .glb, .ply, ou .obj
```

## Performance

### Geração Muito Lenta

**Possíveis causas:**

1. **Usando CPU em vez de GPU:**
```bash
# Verificar
python -c "import torch; print(torch.cuda.is_available())"

# Se False, verificar instalação CUDA
```

2. **Passos muito altos:**
```bash
# Reduzir para testes
text3d generate "prompt" --steps 16
```

3. **Frame size muito grande:**
```bash
# Usar 128 para rápido
text3d generate "prompt" --frame-size 128
```

### Otimizações

```bash
# Modo mais rápido (qualidade reduzida)
text3d generate "prompt" --steps 16 --frame-size 128 --guidance 10.0

# Modo qualidade (mais lento)
text3d generate "prompt" --steps 64 --frame-size 512 --guidance 20.0
```

## Debug Avançado

### Verbose Mode

```bash
# Logs detalhados
text3d generate "prompt" --verbose
```

### Python Debug

```python
import logging
logging.basicConfig(level=logging.DEBUG)

from text3d import ShapEGenerator

with ShapEGenerator() as gen:
    gen.logger.setLevel(logging.DEBUG)
    mesh = gen.generate("prompt")
```

### Verificar Ambiente

```bash
# Criar relatório de diagnóstico
python -c "
import torch
import text3d
from text3d.utils import get_gpu_info

print(f'PyTorch: {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
print(f'CUDA version: {torch.version.cuda}')

if torch.cuda.is_available():
    gpus = get_gpu_info()
    for gpu in gpus:
        print(f'GPU: {gpu}')
"
```

## Suporte

Se o problema persistir:

1. Verifique [GitHub Issues](https://github.com/user/text3d/issues)
2. Crie uma issue com:
   - Comando executado
   - Erro completo (stack trace)
   - `text3d info` output
   - Sistema operacional e hardware
