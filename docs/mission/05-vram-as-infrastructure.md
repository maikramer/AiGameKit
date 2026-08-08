# Premise 4 — VRAM is infrastructure, not a user problem

> Nobody should plan peak memory, juggle which models fit, or keep GPU occupancy in their head — regardless of how large the models are or how many backends a tool owns. The Unified Model Server (vramd) owns admit, queue, eviction, and peak accounting so that the GPU stays **busy** when there is work, VRAM stays **inside a safe margin** at all times, and model count/size change **latency and queue order**, not the mental model.

## Intent

VRAM anxiety is the tax that makes local generative pipelines feel like research lab work. This premise says: **pay that tax inside the supervisor**, once, for every tool.

Operators (human or AI) should think:

- “generate this asset / run this batch”
- “is my job queued or done?” (`vramd status`, `vramd queue`, `vramd wait`)

They should **not** think:

- “is Hunyuan + Paint + FLUX going to fit?”
- “which process do I kill?”
- “what quant do I need for a 6 GB card today?”

Model size and model count are **implementation details** of backends. They may change how long you wait. They must not change whether you are allowed to ask for work without a spreadsheet.

## Dual obligation: utilization + margin

Infrastructure success is two-sided:

| Goal | Meaning |
|------|---------|
| **GPU busy** | When the queue has work, the device should stay highly utilized — not idle because nothing dared to load |
| **VRAM in margin** | Peak residency (weights + activation + safety) stays under a safe envelope; OOM and thrash are supervisor failures |

Saturating the GPU by ignoring peaks is not success. Leaving the GPU idle “to be safe” while jobs wait is not success either. vramd exists to hit **both**.

## What vramd owns

Canonical system: `Vramd/` (CLI `vramd` ≡ `vramd`).

Responsibilities:

- **Admit** — decide if a job’s peak fits now (not “GPU has N GB so anything goes”).
- **Queue** — priority (interactive vs batch), affinity cuts, depth limits.
- **Load / evict** — weight lifecycle + LRU so multiple backends do not require multiple mental models.
- **Single flight** — `MAX_INFLIGHT` (and related env) so peaks do not stack blindly.
- **Truth for agents** — `status` / `queue` / `wait` / `cancel` / `flush` as the control plane.

Clients delegate **before** in-process GPU prep (`try_vramd_delegation` / `delegate_to_vramd`). Auto-start unless explicitly disabled.

## Peak accounting (the real unit)

Admit uses approximately:

```text
peak ≈ weights(quant) + inference activation + safety
```

Not bare card size. Not YAML “footprint” alone when it understates activation.

Consequences:

- Small cards may refuse full fp16 and require quant (`sdnq-int4`, etc.).
- Peak signals (`sdnq_preset`, `memory_efficient=true`, …) ride the **vramd payload**, filled by **hw-auto** / `with_vramd_peak_opts` — **not** public CLI flags (`--low-vram` / `--memory-efficient` removed). Omitting them makes vramd assume a larger peak and refuse — or worse, admit wrong.
- `prepare_gpu_exclusive` / aggressive ensure-vram only after vramd fail or `--no-vramd`. Legacy per-tool servers: `AIGAMEKIT_ALLOW_LEGACY_SERVER=1`.
- In-process `ensure_vram_available(N, backend=…)` should align with vramd peak logic (`max(N, peak)`), not a parallel folk formula.

## What may change when models grow

Allowed to change:

- queue wait time;
- which backend is resident;
- whether a job is deferred until eviction;
- which quant preset is the soft default for a quality tier.

Not allowed to change:

- the operator needing a new personal VRAM checklist;
- “kill the other job” as the happy path;
- each tool inventing its own unsupervised GPU ownership story.

## Agent protocol when GPU seems “stuck”

1. `vramd status` / `vramd queue` / `vramd doctor` — see **HOLDING** / who owns the device; free MiB via NVML (`aigamekit_shared.gpu`).
2. Wait (`vramd wait <job_id>`, `--vramd-stream`) or cancel deliberately (`vramd cancel`).
3. **Never** `kill` / GPU pkill / `--gpu-kill-others` while vramd has jobs.
4. Idle vramd holding CUDA context with 0 backends and `free < peak`: `vramd stop` + `vramd start` (only when queue empty).
5. `--no-vramd` only when intentionally bypassing the supervisor; kill still respects a busy queue.

This checklist is part of the premise, not optional etiquette.

## Anti-patterns

- Teaching MiB budgets in every tool README as the main UX.
- Racing the queue with process kills.
- Loading a second backend in-process “because vramd felt slow.”
- Omitting quant/memory flags so admit math is wrong.
- Assuming “one model at a time” in docs while code paths stack peaks.

## Acceptance questions (for PRs)

- Does new GPU work delegate to vramd by default?
- Is peak/quant declared so admit can be honest?
- Did we add operator-facing VRAM steps that belong in the supervisor?
- Under load, do we still prefer queue/wait over kill?

## Concrete examples in this repo

- Batch waves submit×N with sliding window; no sync preload of Omni/paint weights.
- `*/ums_payload.py` + `with_vramd_peak_opts` declare quant so admit is honest.
- After editing a tool: `vramd respawn <backend>` — not “kill GPU and restart everything.”

## Pointers in this repo

- vramd: [`Vramd/README.md`](../../Vramd/README.md)
- Client helpers: `Shared/src/aigamekit_shared/vramd_client.py`
- Batch waves: [`GAMEASSETS_UMS_BATCH.md`](../GAMEASSETS_UMS_BATCH.md)
- Findings: [`UMS_VRAM_FINDINGS.md`](../findings/UMS_VRAM_FINDINGS.md)
