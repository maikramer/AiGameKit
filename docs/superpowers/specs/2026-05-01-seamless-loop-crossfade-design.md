# Seamless Loop via Equal-Power Crossfade — Text2Sound

**Data:** 2026-05-01
**Status:** Aprovado
**Pacote:** `text2sound`

## Problema

Quando geramos BGM com `audio_kind` = `music_loop` ou `ambient_loop`, o início e o fim do áudio não casam perfeitamente — há uma descontinuidade audível ao reproduzir em loop. O único tratamento atual é um prompt hint (`"seamless loop, consistent volume throughout"`) que não garante resultado no nível do sinal.

## Solução

Adicionar pós-processamento de **equal-power crossfade** entre o final e o início do clipe. O crossfade sobrepõe os últimos `N` ms do áudio com os primeiros `N` ms usando curvas `cos²` / `sin²`, preservando a energia RMS durante a transição (sem dip de volume).

## Design

### Algoritmo — `apply_seamless_loop_crossfade`

Nova função em `audio_processor.py`:

```python
def apply_seamless_loop_crossfade(
    audio: torch.Tensor,      # (channels, samples)
    sample_rate: int,
    crossfade_ms: float = 500.0,
) -> torch.Tensor:
```

1. `n = int(sample_rate * crossfade_ms / 1000)` — número de samples do crossfade
2. `tail = audio[:, -n:]` — últimos N samples
3. `head = audio[:, :n]` — primeiros N samples
4. Curvas equal-power sobre N samples:
   - `t = linspace(0, π/2, n)`
   - `fade_out = cos(t)²` — o final desvanece
   - `fade_in = sin(t)²` — o início emerge
5. `crossfaded = tail * fade_out + head * fade_in`
6. `result = concat(audio[:, :-n], crossfaded)` — mantém a mesma duração

Propriedade equal-power: `cos²(t) + sin²(t) = 1` — energia constante ao longo do crossfade.

### Pipeline de post-processamento

**Antes:**
```
Peak Normalize → Trim Silence (opcional) → Edge Fade (5ms/20ms) → Save
```

**Depois (quando seamless loop ativo):**
```
Peak Normalize → [Trim PULADO] → Seamless Loop Crossfade → Save (sem edge fade)
```

O `apply_edge_fade` é suprimido em modo loop porque o crossfade já trata as bordas.

### Trigger automático via QualityEngine

Activado automaticamente quando o `audio_kind` tem `loop_hint: true`:
- `music_loop` → crossfade ativo
- `ambient_loop` → crossfade ativo
- Todos os outros kinds → sem crossfade (comportamento atual)

O parâmetro `crossfade_ms` é adicionado aos `audio_kinds` com `loop_hint: true` no `asset-categories.yaml`:

| audio_kind | crossfade_ms |
|------------|-------------|
| `music_loop` | 500 |
| `ambient_loop` | 500 |

Valores por quality tier (em `quality-profiles.yaml`):

| Tier | crossfade_ms |
|------|-------------|
| fast | 300 |
| low | 400 |
| medium | 500 |
| high | 600 |
| highest | 800 |

### Integração no Generator e CLI

1. `AudioGenerator.generate()` recebe novo parâmetro `seamless_loop: bool = False` e `crossfade_ms: float | None = None`.
2. `save_audio()` recebe `seamless_loop: bool = False` e `crossfade_ms: float = 500.0`.
3. Quando `seamless_loop=True` em `save_audio()`: aplica `apply_seamless_loop_crossfade` em vez de `apply_edge_fade`.
4. O CLI passa os parâmetros automaticamente quando o QualityEngine resolve `loop_hint: true`.

### Fluxo de dados completo

```
text2sound generate "forest ambience" --preset forest_ambience
  → QualityEngine resolve: audio_kind=ambient_loop, loop_hint=True, crossfade_ms=500
  → AudioGenerator.generate(seamless_loop=True, crossfade_ms=500.0)
    → Diffusion gera áudio bruto (stereo, 44100 Hz)
    → Peak normalize
    → Trim PULADO (trim_default=False para loops)
    → apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500)
    → Save WAV (sem edge fade)
    → Metadata JSON com seamless_loop=True, crossfade_ms=500
```

### Ficheiros modificados

| Ficheiro | Alteração |
|----------|-----------|
| `Text2Sound/src/text2sound/audio_processor.py` | Adicionar `apply_seamless_loop_crossfade()`; estender `save_audio()` com parâmetros `seamless_loop` / `crossfade_ms` |
| `Text2Sound/src/text2sound/generator.py` | Adicionar `seamless_loop` e `crossfade_ms` a `generate()` e `GenerationResult` |
| `Text2Sound/src/text2sound/cli.py` | Passar `seamless_loop` e `crossfade_ms` do QualityEngine para o generator |
| `Text2Sound/tests/test_audio_processor.py` | Testes para `apply_seamless_loop_crossfade` |
| `Text2Sound/tests/test_generator.py` | Teste de integração (mock) para seamless loop |
| `Shared/src/gamedev_shared/data/asset-categories.yaml` | Adicionar `crossfade_ms` a `music_loop` e `ambient_loop` |
| `Shared/src/gamedev_shared/data/quality-profiles.yaml` | Adicionar `crossfade_ms` por tier na secção text2sound |

### Testes

1. **`TestApplySeamlessLoopCrossfade`** (unitário em `test_audio_processor.py`):
   - Output tem mesmo shape do input
   - Curvas equal-power: `cos²(t) + sin²(t) ≈ 1.0` para todo `t`
   - Crossfade não modifica o centro do áudio (fora da zona de sobreposição)
   - Funciona com stereo e mono
   - Edge case: áudio mais curto que crossfade_ms
   - Não modifica o tensor original

2. **`TestSaveAudio`** (extensão em `test_audio_processor.py`):
   - `seamless_loop=True` aplica crossfade em vez de edge fade
   - `seamless_loop=False` mantém edge fade (comportamento atual)
   - Metadata regista `seamless_loop` e `crossfade_ms`

3. **`TestGenerator`** (extensão em `test_generator.py`):
   - `seamless_loop=True` propagado correctamente ao `GenerationResult`

### Decisões de design

- **Equal-power (cos²/sin²)** em vez de linear: evita dip de volume no centro da transição.
- **Mesma duração do original**: o crossfade substitui os últimos N samples pela mistura — sem alongar o clipe.
- **Sem dependências novas**: usa só `torch`, já disponível.
- **Trigger automático por audio_kind**: sem flag nova no CLI, alinhado com a abordagem existente (trim automático por audio_kind).
- **Edge fade suprimido em modo loop**: o crossfade cobre as bordas; o micro fade-in/out de 5ms/20ms seria redundante e poderia interferir.
