# Premise 1 — Ease over knobs

> Prefer one command that finishes (`gameassets batch`, `gameassets dream`, tool `generate`) over exposing every model flag. Quality presets and soft defaults beat expert tuning as the primary path.

## Intent

Power users will always want knobs. That is fine. The **primary path** must not require them.

Ease means:

- a short command produces a usable artifact;
- defaults match the common game-dev outcome (feet origin, medium quality, master pipeline);
- optional flags refine; they do not unlock basic success;
- naming and help text describe *outcomes* (“quality”, “category”) more than *implementation* (“this UNet attention slice”).

## Why this premise exists

ML tooling culture often ships “complete control” first. Game teams (and AI agents) need **finished assets**, not a research console. Every required flag is a chance to:

- pick the wrong quant and OOM;
- skip topology-fix and ship broken meshes;
- forget paint / bake / LOD and call an intermediate “done”;
- diverge from the next agent’s recipe.

Knobs are a privilege earned after the happy path works.

## Design rules

1. **Outcome-first CLI.** `generate`, `batch`, `dream`, `handoff` are the nouns users remember. Internal stage names may appear in logs; they must not be mandatory CLI literacy.
2. **Soft defaults.** Fill only what the user/agent did not set (`ParameterSource` / QualityEngine pattern). Never overwrite an explicit choice.
3. **Quality as the main dial.** `--quality fast|low|medium|high|highest` (and category where relevant) is the preferred lever. Per-model microflags are escape hatches.
4. **Profiles over flag soups.** `game.yaml` / `GameProfile` should express intent once; subprocesses inherit coherent args.
5. **Help that teaches the happy path.** `--help` and README examples lead with the one-liner that finishes; advanced sections come later.

## What “one command that finishes” means

| Intent | Preferred surface | Not the primary surface |
|--------|-------------------|-------------------------|
| Many assets for a game | `gameassets batch` | Manual per-tool loop |
| Idea → scaffold | `gameassets dream` | Hand-writing every manifest column first |
| Single mesh | `text3d generate …` (with topology-fix on by default) | Raw stage-1 only unless opted out |
| Textured mesh | paint in pipeline / paint CLI with sane defaults | DIY UV + external painter as default |
| Into the browser | `gameassets handoff` + VibeGame loaders | Copying `_intermediate` into `public/` |

## Anti-patterns

- New required flag for a case that defaults could cover.
- “Expert mode” that is actually the only mode that works.
- Documenting twenty env vars before the first working example.
- Exposing vendor model IDs as the main UX when a preset name would do.
- Breaking soft-fill so batch and interactive CLI disagree on defaults.

## Acceptance questions (for PRs)

- Can a cold agent succeed with the README one-liner?
- Did we add a knob that should have been a default or a quality tier mapping?
- Does `--help` still lead with the finishing command?
- Do GameAssets and the tool CLI agree on soft defaults?

## Concrete examples in this repo

- **QualityEngine** — one `--quality` dial instead of per-model soup.
- **Omni soft-fill** — `softfill_omni_from_category` fills pose/bbox when the manifest omits geom controls ([`OMNI_SHAPE_FINDINGS.md`](../OMNI_SHAPE_FINDINGS.md)).
- **UMS peak** — quant/mem-eff ride in the payload (`resolve_*_vram_opts`); not a public `--low-vram` checklist.

## Pointers in this repo

- QualityEngine: `Shared/src/gamedev_shared/quality/`, [`docs/superpowers/specs/2026-04-30-quality-presets-design.md`](../superpowers/specs/2026-04-30-quality-presets-design.md)
- Batch / dream: `GameAssets/src/gameassets/` · [`GAMEASSETS_UMS_BATCH.md`](../GAMEASSETS_UMS_BATCH.md)
- Tool CLIs: each package’s `cli.py`
