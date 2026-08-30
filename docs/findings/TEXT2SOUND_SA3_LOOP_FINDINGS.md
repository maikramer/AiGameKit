# Text2Sound — BGM Seamless com Stable Audio 3 Small (2026-08)

Estudo e correções para geração de BGM loopable com os checkpoints
`stable-audio-3-small-music`/`-sfx`. Medições em RTX 4050 Laptop 6 GB,
44.1 kHz, prompts orquestrais a 120 BPM (16 s = 8 compassos).

## Resultado final (pipeline canónico `--category humanoid`)

| métrica | antes | depois |
|---|---|---|
| duração do loop | 15,5 s (`-d` - crossfade; off-grid: 31 beats) | **16,0000 s exactos** (8 compassos) |
| dip de energia na costura | -12,9% (curvas cos²/sin²) / -9,6% | **+3,6%** (equal-power cos/sin) |
| RMS da cauda vs corpo | 52–80% (outro do modelo) | **97–99%** (tail trim adaptativo) |
| flux espectral no wrap | ~p90 | **p29–p54** (abaixo da mediana do corpo) |
| jump no wrap | 0,295 (click: mastering stateful) | **0,004–0,022** (≈ passo normal) |

## O que compõe o caminho "best possible"

1. **Fold crossfade equal-power** (`apply_seamless_loop_crossfade`) — curvas
   `cos`/`sin` em amplitude. Para material **não-correlacionado** (head e tail
   de um loop são trechos musicais distintos, corr ≈ 0,05 medido) a potência
   soma `cos²+sin² = 1` constante. As curvas `cos²/sin²` anteriores só
   preservam RMS para material coerente; com não-correlacionado somam
   `cos⁴+sin⁴` → dip de -3 dB no meio da costura.
2. **Comprimento exacto** (`seamless_generation_duration`) — o CLI gera
   `D + crossfade + 2×edge` e o `save_audio` (edge trim + fold) aterra o loop
   final em `-d` exacto. Essencial para loops alinhados a compassos: com o
   fold simples, `-d 16` produzia 15,5 s = 31 beats a 120 BPM — o grid de
   batidas desloca meio compasso por ciclo.
3. **Tail trim adaptativo** (`_adaptive_tail_trim_samples`) — o SA3,
   condicionado por `seconds_total`, compõe um outro (fade musical) no fim da
   geração. O corte de cauda avança em janelas de 250 ms enquanto o RMS está
   abaixo de **75%** da mediana e só para após **3 janelas consecutivas**
   acima do piso (piso 85% / parar à primeira janela deixava passar outros
   graduais: 96→88→66→31%). Reserve por borda: `music_loop` 2,0 s,
   `ambient_loop` 1,0 s (`loop_edge_trim_s` no `asset-categories.yaml`).
   Cabeça: corte fixo (intros curtas). O fold absorve a diferença
   (`fold = len - target`, mínimo 150 ms) → comprimento final sempre exacto.
4. **Mastering em buffer dobrado** — compressor/limiter são **stateful**: no
   início do ficheiro o envelope parte de zero e no fim está activo; a
   diferença de ganho quebra a costura (jump 0,085 vs p99 0,043 medido em
   material real). Para loops com mastering, a cadeia corre sobre
   `cat([loop, loop])` e extrai-se a 2ª cópia — o estado no arranque herda o
   estado do fim (jump volta a 0,010). LUFS integrado é idêntico sobre
   conteúdo duplicado.
5. **Sem `trim_silence` no caminho de loop** — com buffer 0 rapava uma fatia
   variável das bordas (87 ms medidos) e partia a matemática de comprimento.

## Resultado negativo: refino da costura por inpainting

O SA3 é um modelo de inpainting (`generate_diffusion_cond_inpaint`), por isso
prototipámos **compor** a transição fim→início: buffer `C + head`,
regenerar a janela `[D-T, D+ε]` (mask 0), manter o resto. O mask funciona
(região KEEP preservada a corr 0,986; transição regenerada), mas a transição
não converge no head a cfg 1,0 — flux espectral no wrap **piorou** (p72–p98
vs p18 do fold equal-power; janelas T=2,5–4,5 s, steps 24–32 testados). O fold
crossfade de 500 ms já produz wrap mais suave por construção (a cauda converge
no head). Ideia arquivada; reacreditável se a Stability publicar guidance
próprio para seam inpainting.

## Recoções de uso (BGM)

- Pedir durações **múltiplo do compasso** (120 BPM 4/4 → múltiplos de 2 s) e
  incluir o BPM no prompt.
- `--category humanoid` (ou qualquer categoria com kind `music_loop`) liga
  tudo: seamless + crossfade 500 ms + edge 2,0 s + master_glue.
- Overrides: `--seamless-loop --crossfade-ms --loop-edge-trim` (flag explícita
  ganha aos defaults do kind).
- Verificação rápida de costura: RMS das últimas janelas vs mediana, flux
  espectral no wrap vs distribuição do corpo, `|m[0]-m[-1]|` vs p99 do passo
  (scripts deste estudo em `docs/findings/` como referência).

Ficheiros-chave: `Text2Sound/src/text2sound/audio_processor.py`
(`apply_seamless_loop_crossfade`, `seamless_generation_duration`,
`_shape_seamless_loop_exact`, mastering dobrado em `save_audio`),
`cli.py` (`gen_duration` + resolução `loop_edge_trim_s` por kind),
`Shared/.../asset-categories.yaml` (`loop_edge_trim_s`).

## Apêndice — regeneração dos exemplos (2026-08-25, simple-rpg + simple-racer)

35 ficheiros (4 BGM + 31 SFX) regenerados via scripts `regen_sounds.py`
(filtro `bgm`/`sfx`). Lições adicionais:

- **`trim_threshold_db` dos kinds: −40 → −55 dB.** O −40 (16 kinds) foi
  calibrado para os decaimentos lamacentos do Open; com o SA3 limpo cortava o
  corpo audível dos impactos (mine_hit 0.09 s!, hit 0.34 s de 2.0 pedidos;
  após −55: 1.50 s / 1.34 s).
- **Reserve de outro do `music_loop`: 3.5 s por borda.** Outros de faixas
  rápidas (synth rock) descem 3+ s; com 2.75 s o budget de tail trim
  (= xf + edge − 0.15) ficava curto (bgm_race: 3 seeds seguidas com cauda
  20-25%). Com 3.5 s: caudas 80-118%.
- **Gate de qualidade com retry por seed**: mudar o reserve muda
  `seconds_total` ⇒ música diferente pela mesma seed — tuning de reserve não
  converge. Os scripts medem cauda/início (RMS 500 ms vs mediana) e re-rolam
  com seed+1000 (≤3) até cauda ≥70% e início ≥60%.
- **Bug de placement corrigido** (Text2Sound/generator): reload sob VRAM
  pressionada escolhia plano `group_stream` cujo apply é no-op em
  stable-audio → modelo na CPU + generate em cuda = "Expected all tensors to
  be on the same device". Agora o `load()` verifica onde os pesos ficaram e
  degrada para geração em CPU (SA3 small 0.6B é viável) em vez de crashar.
- **Metadata do worker vramd** agora inclui `model_id` (paridade com o
  caminho in-process; sidecars `.ogg.json` completos).
- Nota: `dist/` dos exemplos fica com áudio antigo até um rebuild
  (`vibegame build`); a fonte de verdade é `public/assets/audio/`.

## Apêndice 2 — SFX "metálicos" (2026-08-25, pós-pool)

Sintoma: one-shots curtos (swing 0.5 s, enemy_hurt 0.8 s) saíam metálicos/
agudos, sem relação com o prompt. Causas ambas no conditioning SA3-sfx:

1. **`seconds_total` < 1 s → NaN.** O NumberConditioner (fourier features)
   degenera abaixo de ~1 s e o buffer vem não-finito; o peak-normalize
   transformava o NaN em ruído áspero. Varredura: 0.5/0.8 s → NaN; ≥1 s são.
2. **Crop cego ao `-d` cortava o transiente.** Com conditioning são, o
   one-shot vive ~0.4 s dentro do buffer (ex.: cond 2 s → pico @0.4 s);
   cortar o buffer exactamente em `-d` apanhava só o ataque.

Fix (Text2Sound/generator + models): `ModelSpec.min_condition_seconds`
(sfx: **2.0**, música: 0) — o conditioning passa a `max(-d, floor)`, o crop
SA3 usa `cond + 1.5 s` de folga para SFX (música mantém crop exacto — a
matemática do seamless depende do comprimento) e o **trim por kind decide o
comprimento final natural** (o `-d` de SFX é alvo, não contrato). Pós-fix
(medido): enemy_hurt 1.26 s centróide 1666 Hz / 2% HF (vocalização), swing
0.35 s de swish, horn 1.33 s, engine_rev 4.0 s. Nota: `hit` continua
brilhante (7.4 kHz) — embate de metal é metálico por natureza.
