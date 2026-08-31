# landmarks/&lt;biome&gt;.xml — POIs por bioma

`<biome>` ∈ `forest` (N) · `desert` (E) · `swamp` (S) · `peaks` (O). Cada ficheiro
é um `<Group name="biome.<biome>.landmarks">` incluído a partir de `index.html`,
depois da vegetação do mesmo bioma. Ver visão geral em `../context.md`.

## forest.xml — Floresta Sombria (Norte)

Dono: `landmarks/forest.xml`. 5 destinos ao longo do trilho norte
(`paths/trails.xml`), cada um com `SpawnExclusion` e ligado a `ASSETS_REGISTRY.md`:

| §   | Landmark                           | GLB(s)                                                            | Notas                                                                                                                       |
| --- | ---------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 1   | Posto avançado arruinado (-46, 88) | `watchtower`                                                      | Tomo `poi/watch-tome.ts` (XP)                                                                                               |
| 2   | Círculo de menires (52, 118)       | `stone_pillar` ×8 + `druid_stone_altar`                           | Altar central era `<Composition>` (cilindros) — migrado para GLB 2026-07-29; luz + `ParticleSystem preset="magic"` mantidos |
| 3   | Acampamento de lenhador (-30, 64)  | `village_longhouse` (escala 0.62) + `log_pile` + `chopping_block` | Toros/cepo eram `<Composition>` — migrados para GLB 2026-07-29                                                              |
| 4   | Poço da encruzilhada (14, 96)      | `medieval_well`                                                   | `Composition` restante é só luz (`body="none"`) — sem GLB catálogo, não migrar                                              |
| 5   | Clareira da bruxa (-10, 135)       | `witch_hut`                                                       | Luz verde só (`Composition` PointLight) — caldeirão Cylinder removido                                                       |

`bosque encantado` (18, 72): luz + 3× `mushroom_glow` (GLB existia no disco sem
referência XML nenhuma antes de 2026-07-29) + 2 `forage-mushroom` (`mushroom.ts`).

**Não migrar** (efeito, não prop de catálogo): luzes puras
(`body="none"; collider="none"` + `PointLight`/`ParticleSystem`).

Depois de editar: `vibegame analyze examples/simple-rpg/index.html` → `errors=0`.
