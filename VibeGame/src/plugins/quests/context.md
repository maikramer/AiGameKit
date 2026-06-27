# quests plugin

Single-quest-per-NPC dialogue + quest progress system (spec §4/§5, plan Track B).
Phase 1: linear dialogue (no branching/boss gating).

## Components

- `QuestGiver` (per NPC entity): `questId` (registry index 0..63), `state`
  (`0=available, 1=taken, 2=completed, 3=failed`).
- `DialogueData` (per NPC entity): interned string indices for portrait/voice.
- `QuestState` (global singleton, `MAX_QUESTS=64`): `active`, `progress`,
  `completed` arrays indexed by quest index. **Not** entity-indexed — accessed
  directly via the exported const.

## Quest registry

Quests are declarative JSON (spec §5). At boot, the game fetches each
`<biome>_quests.json` and calls `registerQuest(state, def)` once per entry. This
stores the def in the engine `DataRegistry` under kind `'quest'` and allocates a
**stable index** (insertion order, capped at 64). The same registration order
must be reproduced on load so indices round-trip across save/load.

## Dialogue flow

- `QuestTriggerSystem` (group `late`): when the player presses **F** within 4 m
  of the nearest `QuestGiver`, it opens a dialogue whose phase is derived from
  the giver state (intro / progress / complete) via `showDialogue`.
- `DialogueBalloon` HUD widget (mirror of `InteractionPrompt`) renders the
  portrait + title + lines + buttons. Buttons:
  - **Aceitar** → `acceptQuest` (giver `state=taken`, `QuestState.active=1`)
  - **Recusar** / **Fechar** → `endDialogue`
- `showDialogue` pushes a `'dialogue'` modal (via `rpg-pause` `pushModal`), which
  pauses the simulation; `endDialogue` pops it.

## Progress

- `QuestProgressSystem` (group `simulation`) drains a kill/collect queue.
- **Kill reporting**: game scripts call `notifyEnemyKilled(state, 'wolf')` on
  enemy death. There is no engine enemy-registry event API, so this engine-side
  notifier is the integration point (Track C wires it into enemy death
  handlers). `notifyResourceHarvested(state, kind)` covers `collect` objectives.
- On match with an active quest objective, `progress` increments; at the goal
  the quest completes, the giver flips to `completed`, `quest:completed` is
  emitted on the EventBus, and rewards are applied (gold via vault, xp via
  progression, items via inventory — each guarded by component presence).

## HUD

- `<DialogueBalloon>` — single overlay instance in `<Scene>`.
- `<QuestsTab>` — child of `<TabbedModal id="pause">`; built by the `queststab`
  branch in `tabbed-modal.ts` `buildTabsFromChildren`. Shows Ativas / Completas
  / Falhadas sections.

## Save / load

- `serializeQuestState(state)` / `applyQuestStateSnapshot(state, data)` expose
  the snapshot (spec §10): `{ active: number[], progress: Record<idx,count>,
  completed: number[] }`.
- The plugin registers a `'quests'` **global save serializer** (see
  `save-load/serializer-registry.ts`) so any game using `serializeAll` /
  `deserializeAll` round-trips quests automatically with back-compat defaults.
- Games using the msgpackr `saveToLocalStorage` path (current simple-rpg) should
  call `serializeQuestState` / `applyQuestStateSnapshot` in their own save
  adapter (Track E integration).

## Labels

UI strings (Ativas/Completas/Falhadas/Aceitar/Recusar/Fechar) are hardcoded PT.
i18n keys are a follow-up.

## Open questions for Track E

- How/when do NPC scripts attach the `QuestGiver` component? Phase 1 uses the
  `<DialogueNPC dialogue-id="...">` recipe (parser sets `questId` from the
  registered index). If the quest JSON is registered **after** scene parse, the
  NPC's `questId` will be 0 — register quests **before** `runtime.start()`.
- Should multiple NPCs share one quest? Currently yes (any giver with matching
  `questId` flips to `completed` together).
