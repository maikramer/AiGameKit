# Intrinsic — decomposição intrínseca como backend vramd

Wrapper do modelo [compphoto/Intrinsic](https://github.com/compphoto/Intrinsic)
(Careaga & Aksoy, *Intrinsic Image Decomposition via Ordinal Shading*, ACM TOG /
SIGGRAPH Asia 2023; pipeline v2 = *Colorful Diffuse Intrinsic Image
Decomposition in the Wild*, TOG 2024) exposto como backend GPU `intrinsic` do
[vramd](../Vramd/). Consumidor principal: `materialize --intrinsic`
(Materialize 3.0, Fase 6 do upgrade baseado em papers).

## ⚠️ LICENÇA — leia antes de usar

O modelo upstream **não é open-source**: a licença do repo é
"**academic use only**" (uso académico exclusivo, redistribuição permitida
apenas para uso académico, com crédito) e o método está **"patent pending"**
(SFU Technology Licensing Office para inquiries comerciais). Este pacote NÃO
vendoriza nenhum código nem pesos do upstream — o instalador faz `pip install`
do repositório público na máquina do utilizador, e os pesos (~1,8 GB) são
descarregados do GitHub Releases no primeiro `load` (cache
`~/.cache/torch/hub/checkpoints`). **Usa este backend apenas se tiver direito
de uso académico/permitido; para uso comercial contacte os autores.** Cite os
papers se usar os resultados.

## O que faz

Input: uma imagem RGB (photo/texture). Output: três PNGs 8-bit display-referred:

| Saída | Conteúdo |
|-------|----------|
| `<stem>_albedo.png` | albedo difuso sem sombras nem especulares (`hr_alb`) |
| `<stem>_shading.png` | shading colorido tonemapped (`1 − invert(dif_shd)`) |
| `<stem>_specular.png` | residual especular (highlights/fontes de luz, `pos_res`) |

## Instalação

```bash
./install.sh intrinsic          # no raiz do monorepo (venv próprio + pip)
```

O vramd arranca o worker em `Intrinsic/.venv` via `runtime.command`
(`python -m intrinsic_worker serve --ums-worker`) — o módulo chama-se
`intrinsic_worker` para não colidir com o módulo `intrinsic` do upstream.

## Uso

Pelo vramd (canónico):

```bash
vramd status                     # backend "intrinsic" listado
materialize photo.png --intrinsic -o out/    # decomposição + PBR maps completos
materialize decompose photo.png -o out/      # só a decomposição
```

Standalone (sem vramd, para debug):

```bash
cd Intrinsic && .venv/bin/intrinsic-decompose decompose photo.png -o out/
```

## Bugs upstream conhecidos (contornados no wrapper)

- **v2.1: assets com nomes errados.** O `load_models('v2.1')` do upstream pede
  `v2.1/stage_N.pt` mas a release publica `stage_N_v21.pt` (HTTP 404). O nosso
  `weights.load_models_compat` tenta o caminho canónico e, em falha,
  descarrega os `_v21` (cache torch.hub) e passa a lista de ficheiros com
  `alb_residual=True` (que o ramo v2.1 ativa internamente). Release `v2` não
  tem o problema.
- **chrislib: optimal-resize rebenta em imagens pequenas** (UnboundLocalError
  em `calculateprocessingres`). Contornado com `resize_conf=None` — mantém a
  resolução original (o que queremos para texturas).
- **torch.hub pede confiança interativa** para o repo
  `rwightman/gen-efficientnet-pytorch` (backbone do MiDaS alterado) no
  primeiro load. Pré-confiar de forma não-interativa:
  `echo rwightman_gen-efficientnet-pytorch >> ~/.cache/torch/hub/trusted_list`

## Notas operacionais

- Depois de editar código do worker: `vramd respawn intrinsic`.
- VRAM estimada: ~3,2 GiB (pesos ~1,8 GB + ativações); `vram_mib: 3200` no
  `backends.yaml` até haver calibração real (`vramd calibrate intrinsic`).
- A decomposição corre à resolução "optimal resize" do modelo; o worker
  faz upscale dos componentes para o tamanho original (bilinear) antes de
  salvar.
- Sem GPU/vramd: o `materialize --intrinsic` degrada graciosamente para o
  pipeline heurístico com um warning.

## Estrutura

```
src/intrinsic_worker/
  cli.py                    # serve --ums-worker + decompose standalone
  worker_serve_adapter.py   # Adapter (WorkerAdapter) — load/generate/unload
  decompose.py              # core: run_pipeline + pós-processamento + PNGs
tests/                      # CPU-first (modelo mockado; sem torch no CI)
```
