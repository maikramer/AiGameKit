# Premise 2 — Automate to the edge

> Stages chain without babysitting: shape → clean → paint → rig → animate → LOD → validate → handoff (Round 3). GPU work rides UMS waves; master finalize is deferred. Resume, profile autodetection, and orchestration exist so neither humans nor agents re-learn the DAG each run.

## Intent

A game asset is not “a mesh file.” It is the **terminal deliverable** of a directed pipeline: the thing you ship to the engine (typically LOD0, with rig/animation when the asset needs them). Automation means the operator starts the job and the DAG walks itself to that terminal — including retries/resume when something stops midway.

“To the edge” means we automate until the artifact is **engine-ready**, not until the first GPU call returns.

## Canonical stage story (master pipeline — Round 3)

Conceptual order (see `GameAssets` `run_master_pipeline` + UMS waves):

1. **Shape** — `text3d generate` (raw; often UMS shape wave; Omni soft-fill)
2. **Clean** — `text3d topology-fix` (origin, holes, repair)
3. **Paint** — `paint3d` (UMS paint wave; master deferred until wave drains)
4. **Rig** — `rigging3d pipeline` on **`_painted`** (not clean HI)
5. **Animate** — `animator3d game-pack` **×1** on the rigged GLB
6. **LOD / finish** — `text3d lod` on animated/rigged → lod0/1/2 (+ KTX2/meshopt); **no** `transfer-weights` in the DAG
7. **Collision / validate** — as profile requires
8. **Handoff** — into `public/` / VibeGame consumption

**Statics:** lod ladder from painted. **Abolished:** `_rigged_hi`, bake-master-before-rig as default LOD0, transfer-weights×LOD.

GPU orchestration: [`docs/GAMEASSETS_UMS_BATCH.md`](../GAMEASSETS_UMS_BATCH.md).
Mesh detail: [`docs/findings/MESH_PIPELINE_FINDINGS.md`](../findings/MESH_PIPELINE_FINDINGS.md).
GLB compression (KTX2 + meshopt, `text3d finish`, deps `ktx`): [`docs/GLB_FINISH_COMPRESSION.md`](../GLB_FINISH_COMPRESSION.md).

Opt-out flags (`--no-rig`, `--no-animate`, `--legacy-pipeline`, etc.) exist for control. The **default** is the full path implied by manifest + `game.yaml`.

## LOD0 as the edge

Autodetection of the terminal stage:

| Asset reality | LOD0 should be |
|---------------|----------------|
| Has animation | Animated GLB |
| Rigged, no animation | Rigged GLB |
| Paint only | Painted (lod finish) |

Shipping an unrigged painted mesh as LOD0 when the profile asked for rig/animation is a **pipeline bug**, not a style choice.

## Resume and intermediates

Long pipelines die. Automation without resume is cruelty.

- Intermediates (`shape`, `painted`, `clean`, …) belong under `_intermediate/` (or equivalent), not as the public runtime path.
- Resume must **find** archived intermediates — regenerating from zero because files moved is a failure of orchestration.
- Progress UIs / dashboards must show **all** stages (LOD, rig, animate, validate), not stop visually at paint.

## Autodetection over checklists

Stage enablement should come from:

- manifest columns / asset kind;
- `game.yaml` profile blocks;
- quality / category;

…not from the operator re-deriving “do I need bake-master?” every time. Explicit opt-outs are fine; mandatory opt-ins for the common case are not.

## Ownership boundaries (automation ≠ spaghetti)

Automation is orchestration, not “put mesh math everywhere”:

- **Text3D** owns mesh ops (LOD, collision, simplify, topology-fix, bake-master).
- **GameAssets** owns the DAG and subprocess wiring — not `bpy`/`trimesh` mesh surgery.
- **Rigging3D / Animator3D** own rig and clips.
- **UMS** owns GPU scheduling across tools.

Crossing these boundaries “to go faster” usually breaks resume, testing, and agent comprehension.

## Anti-patterns

- Stopping the mental model at “paint done.”
- Requiring a human to run each stage CLI in order for the default profile.
- Resume that ignores `_intermediate/` and regenerates expensive GPU work.
- Dashboards that imply the job is finished mid-DAG.
- Duplicating mesh repair in GameAssets “just this once.”

## Acceptance questions (for PRs)

- Does the default path reach the true terminal asset for this profile?
- Can resume continue after a kill mid-stage without folklore?
- Are new stages plugged into the DAG + progress reporting?
- Did we violate package ownership to “automate”?

## Pointers in this repo

- Master pipeline: `GameAssets/src/gameassets/pipeline.py`
- Paths / intermediates: `GameAssets/src/gameassets/paths.py`
- Layout: [`docs/MONOREPO_GAME_PIPELINE.md`](../MONOREPO_GAME_PIPELINE.md)
- Dream runner: `GameAssets/src/gameassets/dream/`
