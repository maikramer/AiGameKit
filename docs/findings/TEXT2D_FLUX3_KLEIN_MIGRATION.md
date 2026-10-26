# Text2D → FLUX.3 Image Klein — migração e o que mudou (2026-10)

Migração dura do Text2D de `black-forest-labs/FLUX.2-klein-4B|9B` (pipeline
clássico `Flux2KleinPipeline`) para **`black-forest-labs/flux-3-image-klein`**
(ModularPipeline). Sem fallback: um só happy path.

## O modelo

- Transformer **5.6B guidance-distilled** (flow map) + text encoder
  **Qwen3-VL-4B** (Apache-2.0) + VAE `AutoencoderKLFlux2`. Latents packed
  `(B, 128, H/16, W/16)`.
- **16 steps** por defeito; faixa treinada **4-16**. **Sem guidance** (o
  checkpoint distilled não tem guider nem `negative_prompt`) — `--guidance`/
  `guidance_scale` continuam aceites mas são **inertes** (aviso único no log).
- Dois workflows: `text2image` e `image_conditioned` (imagem → i2i; até **10
  referências**; tamanho de saída default = 1.ª referência).
- **Layout por boxes** `{id, desc, bbox:[y_min,x_min,y_max,x_max]}` em 0-1 —
  guia colocação/escala, não é máscara dura. Em edits: `from: "target"` +
  `tgt_bbox` para adicionar; `from: "ref_image_N"` + `src_bbox`/`tgt_bbox` para
  mover.
- **Não tem silhouette conditioning nativo** — a silhueta entra como imagem de
  referência normal. O Text2D ganhou `text2d silhouette` (rembg + threshold de
  alpha → sujeito branco em fundo transparente) e `--ref-silhouette` (extrai e
  usa como `--ref`) para consistência de forma entre poses (animação 2D).
- **Prompt upsampler** (`flux-3-image-klein-pup`, Qwen3.5-9B): a BFL recomenda
  usar sempre; no Text2D é **opt-in** (`--upsample`). Default OFF = o bloco
  `prompt_upsampler` é **popado do ModularPipeline antes do init/load**, logo os
  ~19 GB de pesos do pup nunca são carregados.
- **Licença:** FLUX Kommunity License v1.0; repo **gated** (aceitar no Hub +
  `HF_TOKEN`). O text encoder Qwen3-VL é Apache-2.0.

## diffusers 0.42 — ModularPipeline (armadilhas)

- Suporte chegou no **diffusers 0.42.0** (`Flux3ModularPipeline`,
  `Flux3DistilledModularPipeline`, `Flux3AutoBlocks`, `Flux3DistilledAutoBlocks`,
  `Flux3Transformer2DModel`). Venvs das tools que importam text2d in-process
  (Text3D) têm de ter >=0.42.
- **Lazy load**: `ModularPipeline.from_pretrained(repo)` só resolve o índice
  modular; os pesos entram em `pipe.load_components(dtype={...})`. Para poupar o
  pup:
  ```python
  blocks = ModularPipeline.from_pretrained(repo).blocks
  blocks.sub_blocks.pop("prompt_upsampler", None)   # antes do init_pipeline
  pipe = blocks.init_pipeline(repo)
  pipe.load_components(dtype={"vae": torch.float32, "default": torch.bfloat16})
  ```
- `__call__(prompt=..., output="images")` devolve a lista de imagens diretamente
  (sem `output` devolve o `PipelineState`). `callback_on_step_end` é aceite
  (o nosso `attach_step_hooks` funciona); **não há `_interrupt`** — abortar =
  levantar exceção no callback.
- Componentes são acessíveis como atributos (`pipe.transformer`,
  `pipe.text_encoder`, `pipe.vae`, `pipe.guider`) e `setattr` é a via documentada
  — por isso o **SDNQ runtime** (`quantize_model` + `setattr`) e o nosso planner
  `getattr`-based (GO+streams, channels_last, compile) continuam a funcionar.
- **Rungs clássicos de offload não existem** no ModularPipeline
  (`enable_model_cpu_offload`/`enable_sequential_cpu_offload`) — o
  `apply_offload_plan` faz `hasattr` e avisa "placement pode ser no-op" se o
  plano pedir `model_cpu`/`sequential_cpu`. Na prática o caminho de produção é
  **group offload + CUDA streams** (o default em GPUs pequenas) ou full-GPU;
  se um dia for preciso model_cpu num ModularPipeline, a alternativa da BFL é
  `ComponentsManager.enable_auto_cpu_offload()`.
- `load_components(**kwargs)` encaminha kwargs a cada componente (incl.
  `cache_dir`), e salta componentes já presentes (`getattr(pipe, name) is not
  None`) — o que torna seguro quantizar com `setattr` depois do load.

## SDNQ

- Quantiza-se em runtime `transformer`, `text_encoder` e (só com `--upsample`)
  `prompt_upsampler`; o **VAE fica fp32** (qualidade do decode). Não há
  `text_encoder_2`.
- **`dequantize_fp32=False` é obrigatório neste pipeline.** O ModularPipeline
  deriva o dtype de compute de `transformer.dtype` (dtype do 1.º parâmetro
  flutuante). Com o default do preset (`dequantize_fp32=True`) as scales ficam
  **fp32** → o pipeline cria latents fp32 e os lineares não-quantizados (bf16)
  rebentam com `expected mat1 and mat2 to have the same dtype, but got: float
  != c10::BFloat16`. Com scales bf16 o compute fica bf16 e o dequant é
  consistente (validado E2E na 4050).
- **Matmul quantizado desligado**: o FLUX.3 calcula o embedding de timestep em
  float32 e o kernel int8 do SDNQ não faz autocast — a aceleração Triton fica
  off (`apply_quantized_matmul(enabled=False)`); a via dequant+linear (default)
  funciona e a 512²/4 steps gera em ~6 s na 4050.
- Presets ladder mantém-se: int4 base com GO; int3/int2 só quando o headroom
  `usable - activation` aperta (<2.5 / <1.5 GiB).

## E2E medido (RTX 4050 Laptop 6 GB, int4 + GO+streams)

- Load ~32 s (cache quente), generate 512²/4 steps ~5-8 s; 1024²/16 steps
  ~45 s. Pico ~5.4 GiB (admit 5824 MiB) — em linha com o FLUX.2.
- `vramd calibrate text2d`: weights 0.37 GiB + activation 4.73 GiB + context
  0.23 GiB → `vram_mib 5440`, `admit_peak_mib 5824` (entrada em
  `calibrated/backends-6g.yaml`).
- Referências (1 ref): saída no tamanho da 1.ª ref; edição consistente.
  Boxes: colocação respeitada (modelo trata como guia, não máscara; cores podem
  desviar). Silhueta: forma copiada com fidelidade — caso de uso de animação 2D
  validado.
- Aviso benigno do GO: "some layers were not executed during the forward pass"
  (prefetch tracing em blocos condicionais do FLUX.3) — a geração corre e o
  pico medido mantém-se dentro do admit.
- **Preflight restrito**: o repo tem 43 GB (variants fp8r/nvfp4 + o
  `model.safetensors` single-file original) mas o pipeline só usa ~20 GB. O
  `_preflight_download` ignora `variants/*` e `model.safetensors`.
- `--upsample` (pup 9B) não foi corrido E2E nesta migração (download ~19 GB);
  o caminho de load (pop vs manter bloco) está coberto por testes.

## Tiers de qualidade retunados

Distilled 4-16 steps (o antigo icon a 2 steps deixou de existir):

| tier | resolução | steps |
|---|---|---|
| fast | 512² | 4 |
| low | 768² | 8 |
| medium | 1024² | 12 |
| high | 1024² | 16 |
| highest | 1536² | 16 |
| icon (categoria) | 512² | 4 |

`guidance` foi removida das secções `text2d` (o YAML do Shared e o
`generation_profiles.py` do GameAssets espelham a tabela).

## Footprints e vramd

- Footprint novo: `flux3-klein` = `ModelFootprint(18.5, 2.0, 11.2)` GiB
  (9.7B params fp16 ≈ 18.5; maior módulo transformer ≈ 11.2). **Estimativa** do
  model card — recalibrar com `vramd calibrate text2d`.
- `activation_gib=2.0` é reserva do planner GO (pico real medido no FLUX.2 a
  1024² era ~5 GiB com GO+streams); valores altos (4.5) mandavam 6 GB para int2
  sem necessidade.
- A entrada medida em `calibrated/backends-6g.yaml` era do FLUX.2 e foi
  **removida** — até recalibrar, o admit cai nas estimativas + hw-auto.
- **`upsample` é decisão de LOAD e de SHAPE** (o bloco é popado no load; mudar
  exige reload). O backend `text2d` no `backends.yaml` passou a declarar
  `load_keys:`/`shape_keys:` (mecanismo por-backend do vramd) porque a allowlist
  global do supervisor não conhece a chave.

## CLI nova

```bash
text2d generate "prompt" -o out.png                       # t2i
text2d generate "the same character running" --ref hero.png -o run.png
text2d generate "two balloons" --boxes '[{"id":"b1","desc":"a red balloon","bbox":[0.1,0.1,0.5,0.4]}]'
text2d generate "hero pose" --ref-silhouette hero.png -o pose.png
text2d generate "hero" -n 3 -o hero.png                   # hero.png, hero_1.png, hero_2.png
text2d generate "epic scene" --upsample                   # pup 9B (lento)
text2d silhouette hero.png -o hero_sil.png                # extração standalone
```

Com `--ref` e sem `-W/-H` explícitos, a saída fica no tamanho da 1.ª referência
(comportamento do modelo); `-W/-H` ganham sempre.
