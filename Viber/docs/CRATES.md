# CRATES.md — adoção de crates e watch-list

Estudo do ecossistema crates.io/Bevy feito a 2026-09-07 e o que dele entrou
na engine. Critérios de adoção: (1) compatibilidade Bevy 0.19 confirmada nas
dependências publicadas, (2) substituir uma roda própria SEM mudar
comportamento visível (contrato "mesma seed, mesmo mundo"), (3) risco de
driver NV (materiais/shaders custom) avaliado, (4) manutenção ativa do crate.

## Adotados nesta ronda

| Crate | Versão | Substitui | Notas |
|-------|--------|-----------|-------|
| `dirs` | 6.0 | `HOME`/`XDG_CACHE_HOME` manuais | `save.rs` usa `home_dir()` (semântica `$HOME` exata — saves históricos em `~/.local/share/viber`); `session.rs` usa `cache_dir()`. |
| `half` | 2 | `half_to_f32` manual (`heightmap.rs`) | Conversão f16→f32 bit a bit idêntica. |
| — (dedup) | — | SplitMix64 ×6 ficheiros | `src/rng.rs` único; golden test congela a sequência (`Rng::new(0)` → `0xE220A8397B1DCDAF…`). `spawner.rs` re-exporta para compat. |
| `notify` | 8.2 | — (feature nova) | Hot-reload de scripts Luau: `src/hot_reload.rs`, watcher recursivo sobre `<mundo>/scripts/`, debounce 250 ms, erro de compilação mantém o chunk antigo. `VIBER_HOT_RELOAD=0` desliga. |
| `bevy_kira_audio` | 0.26 (kira 0.12) | `bevy_audio`/rodio como backend de playback | Buses tipados `MusicBus`/`SfxBus` (`crate::music`); `mixer_sync` empurra o `AudioMixerSettings` (save/menu/XML) para os canais — volumes respondem AO VIVO, mesmo em one-shots a meio. Crossfade `fade_step` preservado em linear (testes intactos); conversão `linear_to_db` só na fronteira. Jitter de pitch via `with_playback_rate`. Cachoeiras/água/chuva/BGM todos no bus SFX/Music. |
| `serde_yaml` | 0.9 | parsing do `config.yaml` do jogo (`src/config.rs`) | Contrato de paths lido em runtime (roots/bgm/sfx/texturas/scripts/ui/save). Arquivada (dtolnay) mas congelada-estável e é o parser de referência; ~1 dep transitiva (`unsafe-libyaml`). Chaves desconhecidas avisam e não partem o parse. |

O `bevy_audio`/rodio do Bevy fica compilado (tirar a feature do `bevy` exige
re-listar ~20 features default — frágil); NENHUM som nasce por ele —
`AudioPlayer`/`PlaybackSettings` deixaram de ser usados em runtime.

## Watch-list (avaliar numa próxima ronda)

Confirmados compatíveis com Bevy 0.19 (dependências publicadas verificadas a
2026-09-07):

- **`bevy_rerecast` + `vleue_navigator`** — port do Recast (navmesh a partir
  da geometria real: trimesh das colunas + colliders) + pathfinding Polyanya.
  Daria consumidor à tag `NavMesh` (hoje data-only) e substituiria o
  wander/chase determinístico da `src/ai.rs` quando o mundo pede navegação.
  PRÓXIMO CANDIDATO.
- **`bevy_replicon`** 0.44 (set 2026) — replicação server-authoritative;
  **`lightyear`** 0.29 — alternativa com prediction/interpolation. Multiplayer
  = decisão arquitetural grande: spike documentado antes de integrar.
- **`leafwing-input-manager`** 0.21 — ações + rebinding de teclas (o usuário
  remapear WASD; hoje o mapa é fixo em `player.rs`).
- **`bevy-inspector-egui`** 0.37 (+`bevy_egui` 0.42) — inspeção live de
  componentes para debug; sobrepõe-se ao `viber.debug.*`/profiler? Decidir
  antes de trazer egui para a stack (peso de build).
- **`bevy_sonus`** — áudio espacial COM oclusão/difração — encaixe perfeito
  para as caves voxel; crate novo, vigiar maturidade. O bus kira atual já
  dá o caminho (spatial é por-instância).
- **`bevy_hanabi`** 0.19 — partículas GPU (fogo/chuva/magia em milhares).
  ⚠ Histórico de crash do driver NV 595.84 com shaders custom
  (`docs/PERFORMANCE.md`) — só com env-gate tipo `VIBER_CHUNK_LAYERS` e QA
  pesada de render.

## Rejeitados / não adotar sem reavaliar

- **`bevy_save`** 2.0.1 — STALE (última publicação ago 2025, pré-0.19). O
  `save.rs` custom (JSON + tmp+fsync+rename atómico, campos `#[serde(default)]`)
  é superior para o caso: manter.
- **`rand`/`noise` para os algoritmos de mundo** — trocar SplitMix64/value-noise
  FBM por crates QUEBRA o contrato "mesma seed, mesmo mundo" (mundos
  procedurais mudariam de forma). Os algoritmos estão congelados em
  `src/rng.rs` + `heightmap.rs` com golden tests.
- **`Dexterous Developer`** — hot-reload pesado de código; para scripts Luau
  o `notify` é suficiente (adotado).
- **`quick-xml`/`roxmltree` swap, tokio no bridge, `ureq` no cliente HTTP** —
  `roxmltree` já usado; o cliente std-only do bridge (`src/bridge/client.rs`)
  é decisão consciente (CI-ready, zero deps) — só rever se precisar de TLS.
