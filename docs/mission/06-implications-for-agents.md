# Implications for agents changing this repo

> Concrete rules that follow from the mission. If a change fights these, it fights the product — not just a style preference.

## Rule summary

1. Optimize for **“first command works”** and **“batch finishes alone.”**
2. Route GPU work through **UMS**; never teach kill/race/pkill as the normal path.
3. Hide VRAM math behind **admit / peak / quant / soft-fill**; surface progress and artifacts, not memory spreadsheets.
4. When adding models or stages, **extend the coordinator and defaults** — do not add operator burden.
5. **Document the happy path** so the next AI gets identical results without this conversation.

Deep dives: [north star](01-north-star.md) · [ease](02-ease-over-knobs.md) · [automate](03-automate-to-the-edge.md) · [reproducibility](04-agent-first-reproducibility.md) · [VRAM](05-vram-as-infrastructure.md).

---

## 1. First command works; batch finishes alone

**Do**

- Lead examples with finishing commands (`generate`, `batch`, `dream`, `handoff`).
- Keep soft defaults aligned across tool CLI and GameAssets.
- Map new quality needs into QualityEngine / profiles before inventing flags.
- Make failure messages name the next command (`ums queue`, `resume`, `doctor`).

**Don’t**

- Ship a feature that only works after a manual multi-step ritual undocumented in-repo.
- Leave batch mid-DAG with “success” semantics.
- Require interactive prompts in install or generate paths.

**PR self-check:** Would a cold agent reach a valid LOD0 / handoff with the documented one-liner?

---

## 2. UMS is the GPU control plane

**Do**

- Call `try_ums_delegation` / `delegate_to_ums` before in-process GPU prep (`prepare_gpu_exclusive` only after UMS fail / `--no-ums`).
- Fill peak fields via hw-auto / `with_ums_peak_opts` (`sdnq_preset` / `memory_efficient` on the payload — not public CLI knobs).
- Use `ums status|queue|wait|cancel` in runbooks and agent tips (`UMS_DO_NOT_KILL_TIP`).
- Keep interactive priority above batch unless the product explicitly wants otherwise.

**Don’t**

- Recommend `--gpu-kill-others`, pkill, or `kill` while UMS holds work.
- Document `--low-vram` / `--memory-efficient` as operator flags (removed).
- Add a parallel “shadow server” per tool as the new default (legacy: `GAMEDEV_ALLOW_LEGACY_SERVER=1` only).
- Bypass UMS in docs “for speed” without labeling it an advanced escape hatch.

**PR self-check:** Under contention, does the happy path wait/cancel — or murder a sibling job?

---

## 3. Hide VRAM math; show progress and artifacts

**Do**

- Put peak formulas, safety margins, and eviction in ModelServer / shared helpers.
- Show stage progress, job ids, output paths, validation results.
- Soft-fill quant/memory from quality tier when the user did not choose.

**Don’t**

- Make “calculate your MiB” a README section for normal users/agents.
- Print scary VRAM dumps as the primary UX without an action.
- Assume fp16 admit when the runtime path is int4.

**PR self-check:** Did we teach a spreadsheet, or a command that finishes?

---

## 4. Extend the coordinator — don’t tax the operator

When you add a model, backend, or pipeline stage:

| Extend | Avoid |
|--------|--------|
| UMS backend + peak metadata | “User must unload X before Y” |
| GameAssets DAG + resume paths | Extra manual CLI between stages by default |
| QualityEngine mappings | Raw vendor flags as required UX |
| `doctor` / validation rules | Silent deliverable-class changes |
| Progress/dashboard stages | UI that stops at an early stage |

**PR self-check:** Did operator cognitive load go up with model count?

---

## 5. Leave the happy path written down

**Do**

- Update `AGENTS.md`, package README, and/or `docs/mission/*` when the contract changes.
- Prefer in-repo runbooks over chat-only fixes.
- Add/adjust tests that lock the contract (CLI smoke, batch wiring, UMS client flags).
- Keep installers non-interactive; report and continue on automation failure.

**Don’t**

- Fix production pain only in a transcript.
- Change default paths/flags without a doc + test touch.
- Assume the next agent will read this conversation.

**PR self-check:** Can the next session reproduce the workflow from git alone?

---

## Decision cheat-sheet

| Situation | Prefer |
|-----------|--------|
| New generation feature | Default path via existing CLI + quality tier |
| Multi-stage asset | GameAssets master DAG + resume |
| GPU contention | `ums queue` / wait / cancel |
| Mesh operation | Text3D ownership, GameAssets orchestrates |
| Engine delivery | LOD0 terminal + handoff; no `_intermediate` in runtime |
| Unclear UX | Write the happy path; delete the ambiguous second path |

## Git / process notes (repo convention)

- Work on **`main`** unless the user explicitly asks for a branch.
- Commit only when asked; conventional commits.
- After code edits, keep graphify current (`graphify update .`) when that workflow applies.

## Pointers

- Summary: [`AGENTS.md` Mission](../../AGENTS.md)
- UMS: [`ModelServer/README.md`](../../ModelServer/README.md)
- Pipeline: [`docs/MONOREPO_GAME_PIPELINE.md`](../MONOREPO_GAME_PIPELINE.md)
