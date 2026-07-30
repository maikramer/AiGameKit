# quests plugin

Single-quest-per-NPC dialogue + quest progress system (spec §4/§5, plan Track B).
Phase 1: linear dialogue (no branching/boss gating).

## Components

- `QuestGiver` (per NPC entity): `questId` (registry index 0..63), `state`
  (`0=available, 1=taken, 2=completed, 3=failed`), `acknowledged` (o jogador já
  ouviu as falas de conclusão — apaga o badge/marcador), `markerHeight` (altura
  do badge acima da origem; 0 = default).
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

### Objetivo `visit` (chegar a um marco)

`kill`/`collect` são **push** (o jogo reporta o evento); "vai ver este sítio"
não tem nada que o reporte, por isso `QuestVisitSystem` (grupo `simulation`)
**puxa**: a cada tick mede a distância do jogador aos alvos das quests `visit`
activas.

```json
"objective": {
  "type": "visit",
  "target": "peaks-cairn-1 peaks-cairn-2 peaks-cairn-3",
  "count": 3,
  "radius": 9
}
```

- `target` = **`name=` das entidades** na XML da cena, separados por espaços;
  resolvidos com `state.getEntityByName`. Um alvo que ainda não existe é
  ignorado (sem erro) — o mundo carrega por partes.
- Cada nome conta **uma vez**, por muito que o jogador volte a passar lá.
- `radius` em metros, default 8.

Sem isto, uma quest apontada a um ponto de interesse nunca podia completar e os
marcos do mapa só podiam ser cenário.

## Sinalização — "onde é que eu vou?"

Uma quest que só existe no ECS é invisível para quem joga: o NPC parece cenário
e o objetivo não tem direção. Quatro peças resolvem isso.

### Balão na cabeça (`markers.ts`)

`QuestMarkerSystem` (grupo `draw`) põe um sprite sobre cada `QuestGiver`:

| estado | badge | quando |
|---|---|---|
| `available` | `!` dourado | quest por aceitar |
| `progress` | `?` apagado | aceite, objetivo por cumprir |
| `turnin` | `✓` verde | objetivo cumprido, falta ouvir o fim |
| — (sem badge) | | `QuestGiver.acknowledged = 1` |

`acknowledged` é ligado por `showDialogue` na fase `complete` — a quest termina
**no campo**, longe do NPC, por isso a conclusão sozinha não pode apagar o
badge. Sprites com `depthTest:false` (atravessam paredes) e escala que cresce
com a distância (`min(3, dist/14)`) para continuarem legíveis ao longe.
Altura: `<DialogueNPC marker-height="2.5">` (default 2.6).

### Marcadores no mapa/bússola (`beacon.ts`)

`QuestBeaconSystem` (grupo `late`, antes do `HudScreenUpdateSystem`) traduz o
estado das quests em **waypoints** (`hud/waypoints.ts` — registo neutro, sem
ciclo `hud`↔`quests`). Ids com prefixo `quest:`, reconstruídos a cada tick;
só esses são podados, marcadores do jogo ficam intactos.

- giver `available` → `quest-available`; giver `turnin` → `quest-turnin`.
- giver `progress` é **mudo** no mapa: enquanto o objetivo está no mundo,
  mandar o jogador de volta ao NPC é errado.
- objetivos `visit` activos → um marcador por marco ainda não visitado
  (`getVisitedTargets`); `kill`/`collect` não têm posição fixa, logo não geram
  marcador.

O mesmo sistema regista cada giver como `InteractionTarget` — sem isso não
aparecia prompt `[F]` nenhum e o NPC era indistinguível de uma estátua. A label
muda com o estado (`quests.prompt.talk` / `.progress` / `.turnin`).

**Rastrear:** `setTrackedQuest(state, id)` (botão na `QuestsTab`) fixa a seta e
o tracker. A resolução quest→waypoint corre a cada tick, com o objetivo a ganhar
ao giver, por isso o alvo acompanha a quest de aceitar → cumprir → entregar.

### Virar-se para o jogador (`facing.ts`)

`QuestGiverFacingSystem` roda o NPC para o jogador dentro de 7 m e volta à pose
original ao sair. **Vive no motor de propósito:** o auto-idle do `gltf-xml` salta
entidades com `MonoBehaviour`, por isso pôr um script de facing no NPC custava-lhe
a animação idle — trocava-se um tipo de estátua por outro. Nada de `script=` nos
`<GameObject>` dos NPCs.

## HUD

- `<DialogueBalloon>` — single overlay instance in `<Scene>`.
- `<QuestsTab>` — child of `<TabbedModal id="pause">`; built by the `queststab`
  branch in `tabbed-modal.ts` `buildTabsFromChildren`. Shows Ativas / Completas
  / Falhadas sections, cada linha activa com botão **Rastrear**.
- `<QuestTracker anchor="top-right" max-rows="4">` — canto sempre visível com as
  quests activas, progresso `n/m` e distância ao marcador mais próximo *daquela*
  quest. A tab de pausa já lista tudo, mas uma lista que obriga a pausar não
  responde a "estou a chegar mais perto?" enquanto se anda.
- `<WaypointArrow margin="64">` (widget do `hud`) — aponta ao waypoint
  rastreado; dentro do ecrã pousa no alvo, fora encosta à borda e roda.

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
