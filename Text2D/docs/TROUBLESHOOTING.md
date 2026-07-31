# Resolução de problemas — Text2D

## nvtop mostra GPU a 0% e pouca VRAM enquanto “carrega”

Comportamento esperado **durante a maior parte do primeiro arranque**:

1. **`from_pretrained`** — rede + disco + desserialização em CPU; a GPU pode ficar quase idle.
2. Só **depois** os tensores passam para CUDA (`pipe.to("cuda")`) ou offload.
3. Com **hw-auto** (modo memory-efficient, on por defeito), o modelo **não** fica todo na VRAM; o uso pode parecer baixo.

O CLI mostra **1/2 — download + carregamento** e **2/2 — inferência**, e no stderr **Passo 1/3 … Passo 3/3**.

## Primeira execução muito lenta; a segunda mais rápida

- **1.ª vez:** descarga de vários GB → **vários minutos** são normais.
- **Com cache HF** (mesma máquina): o custo de rede desaparece; resta ler do disco + inferência (**segundos a ~1 min** típico, conforme GPU).

Cada comando `text2d` é um **processo novo** — o pipeline volta a carregar-se do disco (não fica residente entre comandos).

## Python 3.13 + PyTorch CUDA

O índice `cu121` do PyTorch pode não oferecer **torchvision** compatível com Python 3.13. `setup.sh` e `installer.py` usam **`pip install torch torchvision`** (PyPI) quando detectam **Python ≥ 3.13** e GPU NVIDIA.

## `ImportError: sdnq`

```bash
pip install sdnq
```

## `Flux2KleinPipeline` não encontrado

```bash
pip install -U diffusers
```

## VRAM insuficiente (ex.: 6 GB)

- O `hw-auto` (modo memory-efficient, ativo por defeito) seleciona o modelo 4B e CPU offload automaticamente em GPUs pequenas (<7.5 GB); se necessário, desativar com `TEXT2D_HW_AUTO=0` só agrava o problema.
- Reduzir `--width` e `--height` (ex. 512)
- Outro modelo via `TEXT2D_MODEL_ID` (se compatível)

## `text2d generate -v`

O subcomando aceita `-v` / `--verbose`. Também: `text2d -v generate "..."`.

## O modelo Disty0 não carrega ou licença

1. **Hub:** confirma no [model card Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) se há passos extra (aceitar termos, login `huggingface-cli`).
2. **Termos do checkpoint:** o metadata HF associa este repositório a **FLUX Non-Commercial** — não é o mesmo regime que o modelo oficial **Apache 2.0** ([black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)).
3. **Uso comercial:** para reduzir ambiguidade jurídica, usa o oficial com mais VRAM:
   ```bash
   export TEXT2D_MODEL_ID=black-forest-labs/FLUX.2-klein-4B
   text2d generate "prompt" ...
   ```
4. **Resumo do monorepo:** [AiGameKit/README.md — secção Licenças](../../README.md).
