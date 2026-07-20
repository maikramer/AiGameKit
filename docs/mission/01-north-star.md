# North star

> Anyone who opens this repository — human or cold AI agent — can get the **same class of results on the first serious attempt**. Following `AGENTS.md` plus the CLIs is enough. Tribal knowledge, secret flags, and “works on my machine” GPU rituals are regressions.

## Intent

The product is not “a collection of ML CLIs.” The product is **reliable game-asset generation** that a stranger (or a fresh agent session) can operate without inheriting folklore from the last person who touched the GPU.

“Same class of results” does not mean bit-identical weights every run (diffusion and sampling are stochastic). It means:

- the **happy path** is discoverable from docs and CLIs alone;
- the **deliverable shape** is the same (LOD0 as terminal asset, handoff into the engine, validated GLBs, etc.);
- quality sits in the same **tier** when the same `--quality` / profile is used;
- failures are **actionable** (clear error, resume, queue status) — not silent wrong meshes or OOM folklore.

## Why this is the north star

Game pipelines historically fail the “first attempt” test:

| Failure mode | What it feels like | Mission response |
|--------------|--------------------|------------------|
| Tribal knowledge | “You have to pass flag X after kill Y” | Documented defaults; UMS owns GPU |
| Expert knobs first | Twenty flags before one GLB | One command finishes; knobs optional |
| Partial pipelines | Shape exists, paint forgotten, LOD0 wrong | Master DAG + autodetection |
| Non-reproducible agent runs | Next AI invents a different recipe | Contracts in `AGENTS.md` + env + CLIs |
| VRAM theatre | User manages MiB by hand | Premise 4 — infrastructure |

If a change makes the first serious attempt *harder* for a cold agent, it moves us away from the north star — even if it makes experts faster.

## Success criteria (operational)

A cold agent (or new human) should be able to:

1. Install via the documented path (`install.sh` / package installers) without interactive traps.
2. Run a documented generate or `gameassets` flow and obtain artifacts in the expected layout.
3. Use `ums status` / `ums queue` when the GPU is busy — never invent kill rituals.
4. Point a VibeGame example (or handoff) at the produced assets without referencing `_intermediate` as runtime.

Passing `make check` is necessary but not sufficient. The north star is about **operability of the product**, not only green CI.

## Anti-patterns (regressions)

- Features that only work if you read a Slack thread or a private chat.
- “Just use `--no-ums` and kill the other process” as the recommended path.
- Docs that list every flag but never the one command that finishes.
- Examples that point at intermediate paths or machine-specific absolute caches.
- Silent fallbacks that change deliverable class without logging why.

## Relationship to other blocks

- **Ease over knobs** — how the surface stays simple enough for a first attempt.
- **Automate to the edge** — how the DAG finishes without babysitting.
- **Agent-first reproducibility** — how the next session matches this one.
- **VRAM as infrastructure** — how hardware stops being a prerequisite skill.
- **Implications** — concrete rules when editing code.

## Pointers in this repo

- Entry for agents: [`AGENTS.md`](../../AGENTS.md)
- Idea → playable scaffold: [`docs/ZERO_TO_GAME_AI.md`](../ZERO_TO_GAME_AI.md)
- Pipeline layout: [`docs/MONOREPO_GAME_PIPELINE.md`](../MONOREPO_GAME_PIPELINE.md)
- UMS / anti-patterns: [`ModelServer/README.md`](../../ModelServer/README.md)
