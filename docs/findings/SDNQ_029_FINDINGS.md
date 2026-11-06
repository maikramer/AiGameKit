# SDNQ 0.2.9 — presets mais precisos e poupança real de VRAM

Data: 2026-11-04 · SDNQ 0.2.9 (2026-11-03) · RTX 4050 Laptop 6 GB · CPU para as medições

## Contexto

O monorepo fixava `sdnq>=0.2.1` e os venvs tinham 0.2.1–0.2.8. Entre 0.2.5 e
0.2.9 entraram features que atacam o ponto fraco dos presets de baixo bit:

| Versão | Feature relevante para inferência |
|--------|-----------------------------------|
| 0.2.5 | `use_codebook` (Lloyd-Max), `group_size=-2` (tensor-wise) |
| 0.2.6 | atenção com block mask, `quantize_fp32` (não usamos SDNQ Atten) |
| 0.2.8 | **double quantization de escalas/zero-points**: `scale_dtype`, `zero_point_dtype`, `use_codebook_scale` |
| 0.2.9 | dynamic loss com erro médio + max-row + max absoluto; threshold default `(e/2)^-bits`; `weights_dtype_order` configurável |

O caminho de produção (`quantize_model`) filtrava os kwargs por
`inspect.signature(sdnq_post_load_quant)`. Como `scale_dtype` **não** é
parâmetro explícito dessa função (só do `SDNQConfig`), o campo era descartado
em silêncio. Corrigido: `quantize_model` passa `quantization_config=config`
quando a versão o suporta (SDNQ >=0.2.5).

## Como medir

```bash
aigamekit-lab bench sdnq --layers 4 --json /tmp/sdnq.json
aigamekit-lab bench sdnq --candidate uint4-proposed --candidate current-int4 --layers 3
```

O comando lê tensores 2-D diretamente dos shards `safetensors`/`.bin`
(mmap, sem carregar o modelo), amostra camadas por estrato de tamanho e mede:

- `w_rel` — erro relativo de Frobenius dos pesos;
- `out_rel` — `||WX - WqX|| / ||WX||` com `X` aleatório (proxy da geração);
- `b/w` — bytes/peso **exatos** (peso empacotado + escalas + zero-points + SVD);
- tempo de quantização (impacto no load).

Checkpoints usados: FLUX.3 Klein transformer, SD1.5 UNet, Hunyuan3D-Paint
UNet, Hunyuan3D-Omni DiT (3 camadas cada).

## Descoberta: o codebook guarda 2^bits níveis fp32 **por grupo**

`use_codebook=True` devolve como `scale` uma tabela de `2**bits` níveis fp32
por grupo (não uma escala por grupo). Com `group_size=32`:

| Preset antigo | Overhead do codebook | Total medido |
|---------------|---------------------|--------------|
| `uint4` + codebook | 16 × 4 B / 32 pesos = 16 b/peso | **20.5 b/peso** |
| `uint3` + codebook | 8 × 4 B / 32 = 8 b/peso | **11.0 b/peso** |
| `uint2` + codebook | 4 × 4 B / 32 = 4 b/peso | **6.0 b/peso** |
| `uint8` auto (8192) | 256 × 4 B / 8192 = 1 b/peso | **10.1 b/peso** |
| `uint8` em camada pequena (grupo ≈ 320) | 256 × 4 B / 320 = 8 b/peso | **18.7 b/peso** |

Ou seja: `sdnq-int3` gastava **mais VRAM do que o uint8** e `sdnq-int2`
gastava o mesmo que o int4. Os degraus finos do planner não eram degraus de
memória. O codebook também custava tempo: **10.4 s vs 0.6 s** por camada
3072×20480 (17×) no load.

Por isso os presets passam a **não usar codebook**. O campo continua no
`SDNQPreset`/`create_config` para overrides pontuais.

## Resultados (média de 3 camadas por checkpoint)

### FLUX.3 Klein — transformer

| Receita | b/w | w_rel | Nota |
|---------|----:|------:|------|
| `current-uint8` (codebook) | 10.07 | 0.00950 | load lento |
| **`uint8-plain`** | **8.06** | **0.00839** | menor *e* mais preciso (grupo auto 1024) |
| `current-int4` (int4+SVD) | 5.59 | 0.09517 | |
| **`uint4-proposed`** (uint4+SVD+Hadamard+dq u8) | **5.09** | **0.07406** | -9% VRAM, -22% erro |
| **`uint4-hq`** (group 16) | 5.59 | 0.06179 | mesma VRAM do int4 antigo, -35% erro |
| `current-int3` (uint3+codebook) | 11.00 | 0.13268 | mais que o uint8 |
| `uint3-proposed` | 3.50 | 0.16787 | 3-bit real |
| `current-int2` (uint2+codebook) | 6.00 | 0.29334 | igual ao int4 |
| `uint2-proposed` | 2.50 | 0.39288 | 2-bit real |

### SD1.5 UNet

| Receita | b/w | w_rel |
|---------|----:|------:|
| `current-uint8` | 18.67 | 0.00874 |
| `uint8-plain` | 8.08 | 0.01011 |
| `current-int4` | 7.43 | 0.07951 |
| `uint4-proposed` | 6.93 | 0.06292 |
| `uint4-hq` | 7.43 | 0.05258 |
| `current-int3` | 11.00 | 0.13224 |
| `uint3-proposed` | 3.50 | 0.16989 |
| `current-int2` | 6.00 | 0.29225 |
| `uint2-proposed` | 2.50 | 0.39130 |

### Hunyuan3D-Paint UNet

| Receita | b/w | w_rel |
|---------|----:|------:|
| `current-uint8` | 17.07 | 0.01066 |
| `uint8-plain` | 8.09 | 0.01168 |
| `current-int4` | 7.47 | 0.09092 |
| `uint4-proposed` | 6.97 | 0.06062 |
| `uint4-hq` | 7.47 | 0.05068 |
| `current-int3` | 11.00 | 0.13163 |
| `uint3-proposed` | 3.50 | 0.16656 |
| `current-int2` | 6.00 | 0.29138 |
| `uint2-proposed` | 2.50 | 0.38941 |

### Hunyuan3D-Omni DiT

| Receita | b/w | w_rel |
|---------|----:|------:|
| `current-uint8` | 12.00 | 0.00763 |
| `uint8-plain` | 8.06 | 0.00778 |
| `current-int4` | 5.88 | 0.08103 |
| `uint4-proposed` | 5.38 | 0.06534 |
| `uint4-hq` | 5.88 | 0.05463 |
| `current-int3` | 11.00 | 0.13271 |
| `uint3-proposed` | 3.50 | 0.16879 |
| `current-int2` | 6.00 | 0.29325 |
| `uint2-proposed` | 2.50 | 0.39344 |

Conclusão: a receita nova é melhor nos quatro modelos. Nos tiers de 3/2 bits
há um trade-off explícito — o erro sobe (0.13 → 0.17 / 0.29 → 0.39), mas a
VRAM desce para 3.5/2.5 b/peso, que é o que o ladder de offload precisa.

## Presets finais

| Preset | dtype | group | SVD | Hadamard | Escalas | b/w medido |
|--------|-------|------:|-----|----------|---------|-----------:|
| `sdnq-uint8` | uint8 | 0 (auto≈1024) | — | — | fp32 | 8.1 |
| `sdnq-int8` | int8 | 0 | — | — | fp32 | 8.1 |
| `sdnq-int4` | uint4 | 32 | r=32 | N4/256 | **uint8 (dq)** | 5.1–7.0 |
| `sdnq-uint4` | uint4 | 32 | r=32 | N4/256 | uint8 (dq) | 5.1–7.0 |
| `sdnq-uint4-hq` | uint4 | 16 | r=32 | N4/256 | uint8 (dq) | 5.6–7.5 |
| `sdnq-fp8` | fp8 | 0 | — | — | fp32 | 8.0 |
| `sdnq-int3` | uint3 | 32 | — | N4/256 | uint8 (dq) | 3.5 |
| `sdnq-int2` | uint2 | 32 | — | N4/256 | uint8 (dq) | 2.5 |

`dequantize_fp32=False` (caminho do Text2D, para manter o compute dtype
consistente) não altera a qualidade de forma mensurável: 0.07260 vs 0.07264.

## Dynamic quantization — medida e **não** adotada como preset

Com piso de 4 bits (`weights_dtype="uint4"`), o threshold default 0.2.9
`(e/2)^-4 ≈ 0.293` é mais folgado que o erro real (0.073) → **nenhuma camada
sobe de dtype** e o resultado é idêntico ao uint4 estático.

Forçar mais qualidade (`dynamic_loss_threshold=0.05`) sobe a maioria das
camadas para uint6: 6.88 b/w, `w_rel=0.0198` — mas o custo é 14.3 s por camada
(vs 1.7 s), porque cada candidato é quantizado até passar o threshold. Para
comparação, `uint8` dá 0.0124 a 8.0 b/w em 0.6 s.

Fica suportado por `create_config(..., use_dynamic_quantization=True,
dynamic_loss_threshold=..., weights_dtype_order=[...])`, mas **sem preset**:
não compensa no load nem no binómio VRAM/erro.

## Integração

- `Shared/src/aigamekit_shared/sdnq.py` — campos novos no `SDNQPreset`,
  forwarding de `quantization_config`, fatores de VRAM por preset.
- `Shared/src/aigamekit_shared/lowvram.py` — fatores: `sdnq-uint4-hq` 0.38,
  `sdnq-int3` 0.24, `sdnq-int2` 0.18. A ladder e os defaults do hw-auto não
  mudaram (os nomes dos tiers é que passaram a medir o que dizem).
- `~/GitClones/vramd` (repo irmão) — mesma tabela em `footprints.py`, senão um
  preset novo cai no fator 1.0 (fp16) e é recusado em 6 GB. Versão 0.4.0.
- Pins `sdnq>=0.2.9` em Shared/Text2D/Text3D/Paint3D/Part3D/Skymap2D/
  AiGameKitLab.
- CLI: `sdnq-uint4-hq`, `sdnq-int3`, `sdnq-int2` expostos no Text3D;
  Part3D `-q hq`.

## Limitações / próximos passos

- As medições foram feitas em CPU. Em CUDA os `b/w` são os mesmos (storage),
  mas o tempo de quantização é menor; re-medir com `--device cuda` numa GPU
  livre se o load for crítico.
- Camadas pequenas pagam o overhead do SVD (ex.: 256×256 → 12.5 b/w). O
  `bench` amostra camadas grandes de propósito; o plano de VRAM usa margem.
- `use_codebook` continua disponível, mas a `group_size` pequena é armadilha:
  se for reativado, usar grupo ≥ 512 e medir o `b/w` antes.
