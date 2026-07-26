# Premise 3 — Agent-first reproducibility

> Docs, CLIs, and env contracts must be sufficient for a fresh agent to succeed. Same inputs + same quality tier ⇒ same deliverable class. Ambiguity in the happy path is a bug in the product, not a training gap for the user.

## Intent

This monorepo is operated heavily by **coding agents**. Humans matter; agents are first-class operators. If a cold agent cannot succeed from `AGENTS.md` + CLIs + env vars, the product is incomplete — even if a veteran human can muscle through.

Reproducibility here means **class of deliverable and procedure**, not necessarily bitwise-identical stochastic samples:

- same commands;
- same quality tier / category;
- same profile and manifest intent;
- same stage graph;
- same artifact naming and handoff layout;
- same failure protocol when the GPU is busy.

## Why “agent-first”

Agents:

- do not retain tribal Slack lore across sessions unless it is written down;
- over-fit to the last chat’s improvisation if docs are weak;
- will invent kill/VRAM rituals unless the contract forbids them;
- succeed when interfaces are **explicit, stable, and boring**.

Designing for agents makes the human path better too: clearer errors, fewer secret flags, fewer “ask Maikeu” steps.

## Contracts that must stay stable

| Contract | Role |
|----------|------|
| `AGENTS.md` | Operating manual + mission + anti-patterns |
| Package CLIs / `--help` | Executable truth of flags |
| Env vars (`GAMEDEV_*`, tool bins, UMS socket) | Configuration without code edits |
| `game.yaml` + manifest | Intent for batch/dream |
| Quality presets YAML | Soft default matrix |
| UMS protocol / queue commands | GPU coordination language |
| Artifact paths (LOD0, `_intermediate`, handoff) | Where “done” lives |

Changing a contract without updating docs and tests is a reproducibility bug.

## Same inputs ⇒ same deliverable class

Given:

- fixed prompt / manifest row;
- fixed `--quality` (and category if any);
- fixed pipeline profile (master vs legacy, rig/animate opts);
- healthy UMS / install;

…two runs should produce assets that pass the **same validation rules** and fit the **same engine slot**. Sampling noise may change vertices/pixels; structure (origin, stages present, texture expectations, LOD0 role) must not randomly drop.

Seeds and deterministic flags, where tools support them, are welcome extras — not a substitute for stable orchestration.

## Ambiguity is a product bug

Examples of happy-path ambiguity (fix these, do not document around them):

- Two “official” ways that disagree on defaults.
- Docs say master pipeline; CLI still defaults to legacy.
- Handoff examples use paths that batch never writes.
- “Works if you remember SDNQ” but UMS admit assumes fp16.
- Agent told to `--gpu-kill-others` while UMS holds a job.

If an agent needs a coin-flip, we failed this premise.

## Non-interactive by default

Installers, retries, and long jobs must not block on license prompts or Y/N confirms. Automation that cannot finish unattended is not agent-first. Prefer fail-fast with a clear message over hanging for input.

## Anti-patterns

- “The agent should just know.”
- Undocumented env vars required for success.
- Chat-only runbooks that never land in the repo.
- Flaky defaults that change by host without detection (`doctor` / checks).
- Tests that mock away the only path agents actually use, with no contract test left.

## Acceptance questions (for PRs)

- Would a new agent session reproduce this workflow from repo files alone?
- Did we add a second competing happy path?
- Are errors actionable (`ums queue`, resume, missing dep via `doctor`)?
- Is install/batch still non-interactive?

## Pointers in this repo

- Agent guide: [`AGENTS.md`](../../AGENTS.md)
- Zero-to-game: [`docs/ZERO_TO_GAME_AI.md`](../ZERO_TO_GAME_AI.md)
- Testing / coverage floor + CI pitfalls: [`docs/TESTING.md`](../TESTING.md) · [`docs/TESTING_PT.md`](../TESTING_PT.md)
- UMS agent checklist: [`ModelServer/README.md`](../../ModelServer/README.md) (Agents / anti-patterns)
- CLI-for-agents design notes: [`Materialize/docs/plans/2026-03-15-cli-for-ai-agents-design.md`](../../Materialize/docs/plans/2026-03-15-cli-for-ai-agents-design.md)
