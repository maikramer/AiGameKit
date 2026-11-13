# text2svg no Text2D — pesquisa de modelos e desenho

Data: 2026-11-12. Âmbito: gerar SVG a partir do pipeline do `text2d`
(FLUX.3 Klein → raster → vectorizer), com prompt enhancement e um backend
`text2svg` no vramd. O PNG continua a ser o entregável do jogo; o SVG é um
sidecar (ícones de UI, emblemas, marcadores, web).

## 1. Modelos avaliados

| Modelo | Tipo | Licença | Pesos / VRAM | Nota |
|---|---|---|---|---|
| `starvector/starvector-1b-im2svg` | image→SVG | Apache-2.0 | ~5,1 GB / ~4–5 GB fp16 | VLM StarCoder-1B + CLIP ViT-L/14. Treinado em SVG-Stack (ícones, logos, diagramas, fontes, emoji). **Não** foi treinado em imagens naturais nem ilustrações. |
| `starvector/starvector-8b-im2svg` | image→SVG | Apache-2.0 | ~16 GB+ | Melhor DINO em SVG-Bench; não cabe na GPU local de 6 GB. |
| `OmniSVG/OmniSVG1.1_4B` | text+image→SVG | Apache-2.0 (dataset MMSVG CC BY-NC-SA) | 7,6 GB / ~17 GB | Qwen2.5-VL + SketchDecoder + tokenizer SVG. Ilustrações/anime; 4–83 s por imagem. Integração pesada; servidor remoto com split/offload. |
| `OmniSVG/OmniSVG1.1_8B` | text+image→SVG | idem | 17,2 GB / ~26 GB | Fora do alcance de uma 3060 12 GB sem offload agressivo. |
| `vtracer` (PyPI) | tracer clássico | MIT | CPU | Rust; color/binary, stacked/cutout, spline/polygon. Sem semântica, mas robusto em arte flat e determinístico. |

Referências: model cards no Hugging Face, `github.com/joanrod/star-vector`
(Apache-2.0), `github.com/OmniSVG/OmniSVG`, paper StarVector arXiv:2312.11556,
paper OmniSVG arXiv:2504.06263.

### 1.1 Domínio (o ponto que decide o default)

O próprio card do StarVector avisa: **não funciona em imagens naturais ou
ilustrações**; o ponto forte são ícones, logótipos, diagramas, gráficos e
charts. A arte do Vael é painterly (personagens, criaturas, props), portanto:

- **ícones 512² flat com transparência** (`assets/public/assets/icons/*.png`) →
  StarVector é o motor certo;
- **concept art painterly** → VTracer (ou nenhum SVG); o StarVector falharia.
- O prompt enhancement “achata” o raster (cores sólidas, paleta limitada,
  silhueta limpa, sem gradientes/texturas) para o tracer ter hipótese.

## 2. Restrição de integração (StarVector)

O pacote `starvector` **não está no PyPI** e o `pyproject.toml` upstream fixa
`transformers==4.49`, `torch==2.5.1`, `fairscale`, `omegaconf`, `taming`,
`open_clip` e uma data stack de treino. O custom code do checkpoint HF
(`starvector_arch.py`) faz `from starvector.model.models.starvector_v1 import
StarVectorStarCoder`, ou seja, sem o pacote não carrega.

O venv do Text2D tem `transformers 5.14` e Python 3.13; instalar o pacote
upstream degradaria o stack. Solução: **vendorizar só o inference path**
(`Text2D/src/text2d/svg/vendor/starvector/`, Apache-2.0 + NOTICE):

- `starvector_arch.py`, `model/models/{starvector_base,starvector_v1}.py`,
  `model/llm/starcoder.py`, `model/adapters/adapter.py`,
  `model/image_encoder/{clip_model,image_encoder}.py`;
- imports relativos; `fairscale` substituído por um no-op (`compat.py`);
- ramos VQGAN/ConvNeXt/SigLIP/OpenCLIP removidos (só o ramo CLIP é usado);
- `GPTBigCode` vem do próprio `transformers` (existe na 5.14); não se
  vendoriza o fork de 70 KB;
- o LLM é construído a partir dos campos do config StarVector
  (`GPTBigCodeConfig`) e os pesos vêm do checkpoint StarVector. O repo base
  `bigcode/starcoderbase-1b` é **gated** (403 sem pedido de acesso) e **não é
  descarregado**; o tokenizer vem do repo StarVector;
- `AutoTokenizer(..., trust_remote_code=False)` é obrigatório: o repo tem
  `auto_map` e o hub pergunta se pode correr custom code — no worker o stdin
  é o canal JSONL, um prompt corrompia o protocolo.

Validação: o import e o `from_pretrained` foram testados com o checkpoint
real; ver §5.

## 3. Desenho escolhido

```
prompt ──[svg.prompt.enhance]──> FLUX.3 ──> PNG (sidecar; fonte 3D/QA)
                                          └─> vectorizer ──> SVG + .svg.json
                                              ├─ vtracer (CPU, default)
                                              └─ starvector-1b (GPU, ícones)
```

- `--svg` no `generate` produz o SVG ao lado do PNG; `-o foo.svg` implica SVG
  e usa um raster temporário (apagado salvo `--svg-keep-raster`).
- `text2d vectorize INPUT` vectoriza um raster existente.
- `generate-batch --svg` faz uma segunda passagem (`phase=svg` no JSONL);
  PNG existente + SVG em falta → só vectoriza.
- `auto`: StarVector quando `--category icon` e o checkpoint está cacheado;
  senão VTracer. `--svg-engine starvector` explícito permite o download e
  falha com hint se não carregar.
- vramd: backend **`text2svg`** com `tool: text2d` (mesmo venv), `task:
  vectorize` no load, `footprint_key: starvector-1b` (~2,9 GiB pesos + 1,2 GiB
  ativação). O FLUX do `text2d` é descarregado antes; os dois não cabem em
  6 GB. `models:` prefetcha só o checkpoint StarVector.
- Segurança: todo o SVG (incluindo o do modelo) passa por
  `extract → repair → sanitize → write`; `script`, `foreignObject`, `style`,
  `on*`, `href` externo e `DOCTYPE/ENTITY` são removidos.
- Transparência: o VTracer achata o alpha; compõe-se sobre uma cor sentinela
  ausente da paleta e removem-se os paths dessa cor no SVG final. Em `line`
  usa-se branco e só se remove o path que cobre a tela inteira.

## 4. Operação

```bash
# Ícone SVG com StarVector (GPU, ~4–5 GB)
text2d generate "blue dash wind speed swirl, fantasy RPG UI" \
  --category icon --svg --svg-engine starvector -o ability_dash.png

# Ícone com VTracer (CPU, sem GPU)
text2d generate "flat red apple icon" --category icon --svg \
  --svg-engine vtracer -o apple.png

# Vectorizar um raster existente
text2d vectorize ability_dash.png -o ability_dash.svg --engine vtracer --colors 8

# Lote: PNGs existentes → só SVGs
text2d generate-batch icons.json --svg --svg-engine vtracer --no-hw-auto
```

Flags principais: `--svg-style {flat,line,detailed}`, `--svg-colors 2..64`,
`--svg-detail {low,medium,high}`, `--svg-enhance/--no-svg-enhance`,
`--svg-max-tokens`, `--svg-seed`, `--svg-device {auto,cuda,cpu}`,
`--svg-preview` (cairosvg opcional), `--svg-transparent`,
`--svg-meta/--no-svg-meta`, `--svg-strict`, `--svg-max-kb`.

## 5. Resultado do spike

- Ícone de QA: `Vael/assets/public/assets/icons/ability_dash.png` (512²,
  RGBA, arte flat).
- **Carregamento no `transformers 5.14`: OK**, depois de duas correções de
  compatibilidade no vendor:
  1. `StarVectorForCausalLM.__init__` tem de chamar `post_init()` e declarar
     `_tied_weights_keys` (`lm_head.weight` ← `transformer.wte.weight`); sem
     isso o `from_pretrained` rebentava em `all_tied_weights_keys` e o
     `lm_head` ficava aleatório (o checkpoint só traz `wte`).
  2. Com `inputs_embeds`, o `generate` da 5.x quer `max_new_tokens`; o
     `max_length` antigo era subtraído do comprimento dos embeds
     (`12 - 259 = -247`) e rebentava. `top_p`/`temperature`/`early_stopping`
     só entram quando o modo os usa.
- O repo base `bigcode/starcoderbase-1b` é gated; removido do caminho. O
  tokenizer vem do repo StarVector (`GPT2Tokenizer`, 49156 tokens) e o LLM é
  construído a partir do config (`GPTBigCodeConfig`), com os pesos do
  checkpoint.
- **CPU (gate de carregamento/API)**: 512 tokens totais → 254 novos, 75,2 s,
  saída de 565 chars truncada antes do primeiro path (SVG vazio após o
  repair). Não serve para avaliar qualidade; só prova load + geração.
- **VTracer no mesmo ícone**: 159 paths, 157,4 KB, 0,75 s (CPU).
- **GPU (qualidade/VRAM)**: pendente de GPU livre — durante a validação a
  GPU local estava ocupada pelo próprio jogo (`target/release/vael`, ~4 GB) e
  o servidor remoto estava inacessível (`No route to host`). O gate é SVG
  válido, paths < ~200 e VRAM < ~5 GB. O spike
  `scripts/spike_svg_starvector.py` corre com `--device cuda --max-tokens 4000`
  quando a GPU estiver livre.
- **vramd local**: o supervisor foi reiniciado e o log de auto-start lista
  `text2svg` nos backends registados. A 0.8.0 instalada não conhece a chave
  `starvector-1b`; por isso o descriptor usa um bloco `vram:` estimado
  (2,9 GiB pesos + 1,2 GiB ativação, peak 4300 MiB, admit 4600 MiB). O
  `footprint_key` fica no `Shared/lowvram` para o planner in-process e no
  `vramd/footprints.py` do irmão para a próxima release; `vramd calibrate
  text2svg` deve substituir a estimativa por valores medidos.
- **Worker**: o protocolo `text2d serve --ums-worker` foi validado com
  `task=vectorize`/`engine=vtracer` (ready → progress → done → unloaded).
- **Remoto**: o cliente está implementado (inputs `image`, outputs `output`,
  sidecar `<name>.json`, sessão `text2d`, modelo StarVector); o servidor
  `192.168.0.201:8790` não respondeu no momento do teste.
- Se o GPU falhar, o `text2svg` fica só com VTracer e o bloqueio fica
  documentado aqui.

## 6. Limitações conhecidas

- O runtime do Vael ainda **não carrega SVG**; o SVG é um entregável para
  UI/editor/web. O PNG/KTX2 continua a ser o caminho do jogo.
- StarVector não serve para painterly; o `auto` só o escolhe em `icon`.
- Arte detalhada no VTracer pode gerar centenas de paths e ficheiros de
  centenas de KB; `--svg-detail low`, `--svg-colors` e `--svg-max-kb` limitam.
- O `--svg-preview` precisa de `cairosvg` + libcairo e fica fora do core
  (`pip install 'text2d[svg-preview]'`); o servidor remoto não precisa dele.
- O cancelamento do StarVector não é cooperativo a meio da geração (a API
  upstream não expõe `should_abort`); verifica-se antes/depois.
- O prompt enhancement é determinístico; não reescreve o sujeito nem o bloco
  `Avoid:`. Um rewriter LLM fica como extensão futura.
- `--upsample` (PUP 9B) expande a caption dentro do pipeline; o enhancer SVG
  corre antes e não controla a caption expandida. Evitar a combinação.
