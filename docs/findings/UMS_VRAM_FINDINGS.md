# UMS / VRAM — descobertas operacionais

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Canónico: [`ModelServer/README.md`](../../ModelServer/README.md),
`Shared/src/gamedev_shared/model_server.py`,
`GameAssets/src/gameassets/ums_batch.py` / `ums_coord.py`.

---

## Modelo mental

- **Um processo**, um socket (`~/.cache/gamedev/model-server.sock`), backends GPU
  com carga/evict, fila prioridade + afinidade VRAM, `MAX_INFLIGHT` (tip. 1).
- CLIs chamam `try_ums_delegation` **antes** de prep GPU in-process.
- Alias: `ums` ≡ `gamedev-model-server`.

---

## Pico de admit (o que falhou na prática)

```
peak ≈ weights(quant) + inference_activation + GAMEDEV_UMS_VRAM_SAFETY_MIB (384)
```

| Situação | Comportamento correcto |
|----------|------------------------|
| peak > VRAM total GPU | **Hard refuse** — tip `sdnq-int4` / quality fast / mem-eff |
| peak ≤ total mas free < peak | **Transitória**: evict + backoff + **requeue** até `GAMEDEV_UMS_MAX_VRAM_RETRIES` (8); poll admit `GAMEDEV_UMS_VRAM_ADMIT_WAIT_SEC` |
| Cliente omite quant | UMS assume **fp16** → text3d/paint “não cabem” em 6 GB mesmo com SDNQ real |

**Descoberta batch Omni:** paint/text3d **devem** mandar `sdnq_preset` e/ou
`memory_efficient=true` no payload. Sem isso, fila morre com “livre < peak”
enganoso.

---

## Shape/paint waves (GameAssets)

| Anti-padrão | Efeito visto | Fix |
|-------------|--------------|-----|
| Preload sync text3d >~600 s antes da wave | Client Broken pipe → UMS evict modelo → free stuck ~4 GB < peak → **todos** os jobs falham | **Remover** preload sync; 1º job da wave carrega o shape |
| Preload timeout curto | Mesmo padrão | Se preload existir: default timeout alto (ex. 1800 s) |
| Kill GPU com fila busy | Mata job errado / corrompe fila | `ums cancel` / wait; nunca `nvidia-smi` pkill |

Código: `run_shape_wave_or_fallback` / `run_paint_wave_or_fallback` em
`ums_batch.py` — sem preload síncrono para shape/paint.

---

## Env knobs úteis

| Var | Papel |
|-----|--------|
| `GAMEDEV_UMS_AUTO_START` | Auto-start supervisor (0 = off) |
| `GAMEDEV_UMS_PRIORITY` | `batch` nos subprocessos GameAssets |
| `GAMEDEV_UMS_MAX_VRAM_RETRIES` | Requeues VRAM transitória (default 8) |
| `GAMEDEV_UMS_VRAM_ADMIT_WAIT_SEC` | Poll free antes de refuse |
| `GAMEDEV_UMS_VRAM_SAFETY_MIB` | Folga no peak |
| `GAMEDEV_UMS_MAX_AFFINITY_CUTS` | Saltos de afinidade (≤3 tip.) |
| `GAMEDEV_UMS_MAX_QUEUE_DEPTH` | Cap fila |
| `GAMEDEV_UMS_MAX_INFLIGHT` | Paralelismo VRAM |
| `GAMEDEV_MODEL_SERVER_SOCKET` | Path socket |

Runtime pós-load: `gamedev_shared.vram_budget` — Text3D `num_chunks`, Paint
views/tiles. Desligar Paint auto: `PAINT3D_AUTO_VRAM_BUDGET=0`.

---

## Checklist agente (VRAM “ocupada”)

1. `ums status` / `ums queue` — ler `HOLDING` / `vram_retries`.
2. Esperar (`ums wait`, `--ums-stream`) ou `ums cancel` / `flush` se stale.
3. **Não** kill GPU enquanto houver jobs.
4. Confirmar payload tem quant correcto para o backend.
5. Só `--no-ums` se bypass intencional; kill continua recusado se UMS busy.

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-19 | VRAM retry/requeue; remoção preload sync shape wave; hard refuse peak>total |
