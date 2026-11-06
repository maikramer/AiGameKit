# Hi3D — refinamento de geometria para a pipeline 3D

Refinamento de malha **image-conditioned** construído sobre o
[Hi3D v1.0 / SPARC3D](https://huggingface.co/hi3d-official/Hi3Dv1.0). Recebe uma
malha coarse + a imagem concept que a gerou e devolve uma superfície mais densa
e fiel. No AiGameKit corre como **pós-processamento do stage de shape**
(`topology-fix` → **`refine`** → `paint`), ativado por defeito.

```bash
hi3d refine clean.glb -i referencia.png -o refined.glb          # 512³, por fase (60 s a frio)
hi3d refine clean.glb -i referencia.png -o refined.glb --target-faces 60000
hi3d refine clean.glb -i referencia.png -o refined.glb --no-staging   # offload upstream (137 s, 0,9 GiB)
hi3d doctor                                                     # stack + GPU + pesos + plano
hi3d download-model                                             # 8 artefactos, ~7.7 GB
```

Dentro do toolkit não o chamas à mão — o `gameassets batch` faz isto via
`text3d refine`.

## Instalação

```bash
./install.sh hi3d        # venv 3.10 + torch cu124 + spconv + flash-attn + sparc3d
hi3d download-model      # ~7.7 GB de pesos para ~/.cache/aigamekit/models/hi3d-v1.0
hi3d doctor
```

O instalador é não-interativo e imprime a nota de licença. Sem os pesos tudo
continua a funcionar: o `refine` responde "indisponível" (exit 3) e o batch segue
com a malha `_clean`.

## Porque tem um venv Python 3.10 próprio

O upstream só mediu uma stack: Python 3.10.12, torch 2.5.0+cu124, CUDA 12.4,
`flash-attn==2.7.4.post1`, `spconv-cu120==2.3.6`, `transformers==4.46.3`. O
`pyproject.toml` fixa `>=3.10,<3.11` e **não declara dependências de runtime** de
propósito — o hook de instalação
(`aigamekit_shared.installer.hi3d_extras`) instala a stack medida, para o `uv`
nunca re-resolver o `torch` para um build CPU/outro CUDA. É o único venv 3.10 do
monorepo; todas as outras tools continuam em 3.13.

## Medido numa GPU de portátil com 6 GB (RTX 4050, CC 8.9)

| Configuração | Tempo | Pico VRAM | Saída |
|---|---|---|---|
| **Residência por fase** (default), worker vramd quente | **32 s** | 3,74 GiB | 32k faces (decimado) |
| Residência por fase, processo novo (com load) | **60 s** | 3,74 GiB | 543k faces → decimado |
| Offload sequencial do upstream (`--no-staging`) | 137 s | 0,86 GiB | idem |
| Residência por fase, 5 passos | 32 s | 3,74 GiB | — |

O upstream só oferece "tudo na GPU" (7,7 GB — não cabe em 6 GB) ou
`enable_sequential_cpu_offload`, que move **cada sub-módulo** a cada forward: 25
passos custam ~92 s de PCIe com a GPU quase parada (pico 238 MiB). A residência
por fase move um componente inteiro (conditioner, transformer, VAE) para a GPU
no início da sua fase e devolve no fim — 92 s → 27 s.

Medido e **rejeitado** (não repetir sem bench novo): autocast fp16 (sem ganho —
o modelo já é misto fp16/fp32), pesos fp16 (rebenta no RoPE `polar_cuda`),
`torch.compile` (cold ~50 s, sem ganho hot; `reduce-overhead` falha com CUDA
graphs), `assign=True`/meta-device (os 19,7 s são **construção** do modelo e o
meta deixa 24 buffers sem dados). `--fast-postprocess` (salta o filtro de
componentes do upstream) poupa ~1 s e é opt-in. Números completos:
[`docs/findings/HI3D_REFINE_FINDINGS.md`](../docs/findings/HI3D_REFINE_FINDINGS.md).

O card do modelo não publica mínimo de VRAM (1536³ só foi validado num H20 de
96 GB). Os limites em `capability.py` são heurística conservadora do monorepo:

* compute capability ≥ 8.0 (Ampere) — requisito duro (flash-attn/spconv, sem CPU);
* 512³ (1 passe) exige ≥ 4,8 GiB livres;
* 1024³ (2 passes) a partir de 16 GiB livres; 1536³ (3 passes) a partir de 40 GiB;
* residência por fase (um componente na GPU de cada vez) a partir de 4,6 GiB
  livres — é isso que faz caber o 512³ em 6 GB; abaixo disso cai para o offload
  sequencial do upstream (pico 0,86 GiB, 2,3x mais lento).

`hi3d doctor --json` imprime o plano da máquina; `vramd calibrate hi3d` substitui
as estimativas por medições reais.

## O que faz à malha

O upstream devolve geometria **centrada na média dos vértices e normalizada para
raio máximo 1** — não preserva coordenadas de mundo. Por isso o `refine`:

1. regista o enquadramento do coarse (`center = vertices.mean()`, `radius = max‖v−center‖`);
2. corre a escada de refinamento;
3. inverte a normalização tal e qual (`v * radius + center`), para escala/origem
   baterem com a `_clean` que alimenta o paint (pés em y=0, metros de `size_m`);
4. decima com quadric decimation para `--target-faces` (0 = manter ~1M);
5. remove ilhas abaixo de 0,5 % do maior componente (o mesmo rácio que o upstream
   usa antes de simplificar — a decimação cria lascas novas, ex.: 214 ilhas de
   1–7 faces num hero de 32k);
6. exporta GLB só de geometria (sem UVs/materiais — o paint vem depois).

O resultado é uma superfície fechada: no asset de teste de 6 GB, 127 986 faces,
**1 componente, 0 arestas de fronteira, 0 arestas não-manifold**. O `text3d refine`
**não** corre `topology_clean` por defeito (`--clean` ativa-o): medido, o clean
abre a malha (2 804 arestas de fronteira) e fragmenta 298 micro-ilhas perdendo
15 % das faces — uma regressão sobre a superfície de marching cubes. O único
ganho (weld) já vem do sampler do upstream.

## Integração vramd

O `hi3d` é um backend registado
(`Shared/src/aigamekit_shared/data/backends.yaml`, `vram_mib: 4300` — o pico da
residência por fase). O worker
fala o protocolo JSONL canónico (`serve --ums-worker`) implementado localmente
em `hi3d/ums.py`: o venv do Hi3D é 3.10 e o `aigamekit_shared` exige 3.13, logo o
package não o pode importar. O `tests/test_ums.py` lê
`Shared/.../worker_protocol.py` e compara todas as constantes de
comando/evento/erro — qualquer deriva falha o teste.

## Configuração na pipeline (`game.yaml`)

```yaml
refine:
  enabled: true          # ON por defeito
  resolution: auto       # 512 | 1024 | 1536 | auto (política por VRAM)
  steps: 25
  max_faces_scale: 4.0   # teto = 4 × faces alvo do LOD0 (0 = sem teto)
  seed: 0
  remove_background: false
```

O stage é saltado (com razão visível) quando falta o binário, os pesos, uma GPU
adequada ou a imagem de referência. Nunca falha um asset. Desligar globalmente
com `refine: false` ou `refine: {enabled: false}`.

## CLI

```
hi3d refine COARSE -i IMAGE -o OUT [--resolution auto|512|1024|1536] [--steps N]
                   [--seed N] [--target-faces N] [--align/--no-align]
                   [--model DIR|REPO] [--offload/--no-offload]
                   [--staging/--no-staging] [--fast-postprocess]
                   [--remove-background] [--local-files-only]
                   [--dino-model DIR] [--clip-model DIR] [--json] [-v]
hi3d doctor [--json]
hi3d download-model [--model REPO]
hi3d serve --ums-worker
```

Códigos de saída: `0` ok, `1` erro, `3` indisponível neste hardware/instalação —
o orquestrador usa o 3 para skip gracioso.

## Licença

A implementação de 2.º estágio e os oito artefactos do Hi3D estão sob a **Hi3D
Open License Agreement 1.0**. Uso comercial é permitido com condições (resumo,
não é aconselhamento jurídico — lê `LICENSE`/`NOTICE` no repo do modelo):

* integrações comerciais têm de exibir **"Powered by Hi3D 1.0"**, com link para o
  site oficial quando o meio o permitir;
* acima de **200 000 utilizadores ativos mensais ou 1 000 000 USD/mês de receita**
  é obrigatório notificar o licenciador e pedir licença escrita em 60 dias;
* **outputs standalone** sem materiais ou derivados do modelo (o GLB refinado
  sozinho) não ficam sujeitos a esse branding por si só — outra lei e direitos de
  terceiros continuam a aplicar-se;
* os encoders de condicionamento DINOv2 (`facebook/dinov2-large`) e CLIP
  (`openai/clip-vit-large-patch14`) mantêm as suas licenças;
* o exemplo opcional de coarse Hunyuan3D do upstream é uma dependência separada.

## Limitações

* Só geometria — sem texturas, materiais ou cores de vértice.
* Batch size um; sem eventos de progresso durante decode/extract.
* Erros estruturais graves no coarse podem persistir; geometria escondida é uma
  conclusão plausível, não uma reconstrução.
* Seeds não são hashes da malha (sparse conv/atenção na GPU não são bitwise
  determinísticos).
* O upstream só traz inferência; não há código de treino/avaliação.
