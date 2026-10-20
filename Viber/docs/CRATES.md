# CRATES.md — adoção de crates e watch-list

Estudo do ecossistema crates.io/Bevy feito a 2026-09-07 (actualizado no upgrade
a 0.20 a 2026-09-25) e o que dele entrou na engine. Critérios de adoção:
(1) compatibilidade Bevy confirmada nas
dependências publicadas, (2) substituir uma roda própria SEM mudar
comportamento visível (contrato "mesma seed, mesmo mundo"), (3) risco de
driver NV (materiais/shaders custom) avaliado, (4) manutenção ativa do crate.

## Adotados nesta ronda

| Crate | Versão | Substitui | Notas |
|-------|--------|-----------|-------|
| `dirs` | 7.0 (2026-10-18; era 6.0) | `HOME`/`XDG_CACHE_HOME` manuais | `save.rs` usa `home_dir()` (semântica `$HOME` exata — saves históricos em `~/.local/share/viber`); `session.rs` usa `cache_dir()`. |
| `half` | 2 | `half_to_f32` manual (`heightmap.rs`) | Conversão f16→f32 bit a bit idêntica. |
| — (dedup) | — | SplitMix64 ×6 ficheiros | `src/rng.rs` único; golden test congela a sequência (`Rng::new(0)` → `0xE220A8397B1DCDAF…`). `spawner.rs` re-exporta para compat. |
| `notify` | 8.2 | — (feature nova) | Hot-reload de scripts Luau: `src/hot_reload.rs`, watcher recursivo sobre `<mundo>/scripts/`, debounce 250 ms, erro de compilação mantém o chunk antigo. `VIBER_HOT_RELOAD=0` desliga. |
| `bevy_kira_audio` | 0.27.1 (kira 0.12; era 0.26, depois rc.1) | `bevy_audio`/rodio como backend de playback | Buses tipados `MusicBus`/`SfxBus` (`crate::music`); `mixer_sync` empurra o `AudioMixerSettings` (save/menu/XML) para os canais — volumes respondem AO VIVO, mesmo em one-shots a meio. Crossfade `fade_step` preservado em linear (testes intactos); conversão `linear_to_db` só na fronteira. Jitter de pitch via `with_playback_rate`. Cachoeiras/água/chuva/BGM todos no bus SFX/Music. |
| `serde_yaml` | 0.9 | parsing do `config.yaml` do jogo (`src/config.rs`) | Contrato de paths lido em runtime (roots/bgm/sfx/texturas/scripts/ui/save). Arquivada (dtolnay) mas congelada-estável e é o parser de referência; ~1 dep transitiva (`unsafe-libyaml`). Chaves desconhecidas avisam e não partem o parse. |
| `dlss_wgpu` | 6.0.0 (via `bevy/dlss`, feature `dlss` do Viber) | — (feature nova) | DLSS Super Resolution nativo do Bevy 0.20 (Linux/Vulkan). **NÃO-default**: o build com `--features dlss` exige o SDK da NVIDIA (`DLSS_SDK` → clone de github.com/NVIDIA/DLSS tag v310.9.1; `VULKAN_SDK` com headers + libclang para o bindgen) e em runtime a `libnvidia-ngx-dlss.so.310.9.1` junto ao binário (rpath `$ORIGIN` no `.cargo/config.toml`). Gate em `src/dlss.rs` (`VIBER_DLSS` > config.yaml `dlss:` > auto). Pin `prettyplease` 0.2.37 RETIRADO (2026-10-18): o lock resolve `bindgen 0.73.2` + `prettyplease 0.3` sem âncora. |

O `bevy_audio`/rodio do Bevy fica compilado (tirar a feature do `bevy` exige
re-listar ~20 features default — frágil); NENHUM som nasce por ele —
`AudioPlayer`/`PlaybackSettings` deixaram de ser usados em runtime.

## Forks vendored para o Bevy 0.20 (2026-09-25; bevy oficial 2026-10-09)

O Bevy **0.20.0 final** está no crates.io desde 2026-10-08 — o `bevy = "0.20"`
resolve do registry e a tabela `[patch.crates-io]` de tags git foi removida.
Os crates do ecossistema abaixo continuam em `vendor/` até os upstreams
publicarem as versões bevy-0.20 (proveniência/revs e condições de des-vendor
em [`vendor/FORKS_020.md`](../vendor/FORKS_020.md)):

| Fork | A montar quando | Notas do port |
|------|-----------------|---------------|
| ~~`bevy_rapier3d` 0.36~~ | **DESVENDORED 2026-10-18** — o upstream `dimforge/rapier` publicou o **0.37.0** (bevy ^0.20, rapier3d 0.36.1, nalgebra 0.35 convert-glam033), que é o port oficial daquilo que o fork fazia à mão | `bevy_rapier3d = { version = "0.37", default-features = false }` do registry |
| `bevy_rerecast` + `bevy_rerecast_core` 0.5 | `janhohenheim/rerecast` publicar 0.20 (a 2026-10-18 o 0.5.0 continua a última versão) | reflect derives por feature do `rerecast` core. |
| `bevy_landmass` 0.13 + `landmass_rerecast` 0.3 | `andriyDev/landmass` branch bevy-0.20 virar release (a 2026-10-18 o 0.12.1/0.3.0 do crates.io continuam bevy ^0.19) | landmass_rerecast ainda misturava bevy 0.19 no branch — corrigido no vendor. |

Único `[patch.crates-io]` restante: `rerecast` (fixes locais de robustez —
`vendor/rerecast/VIBER_PATCH.md`). Dev-deps de shader alinhados com o bevy 0.20
(2026-10-18): `wesl` 0.6 + `naga` 30 — UMA só cópia de cada no lockfile
(antes: wesl 0.4.2 + naga 29.0.4 duplicados na árvore); com o parser do wesl 0.6
a aceitar `binding_array`, os 2 testes ignorados de `tests/chunk_shader.rs`
foram reativados (stub `bevy_render::bindless` com o `enable wgpu_binding_array;`
e `VertexOutput.instance_index` com `@interpolate(flat)`, como no
`bevy_pbr`/`bevy_render` reais).

## Ronda de atualização 2026-10-18

* `bevy_rapier3d` 0.36 (fork) → **0.37.0** (registry) — ver acima.
* `bevy_kira_audio` 0.27.0-rc.1 → **0.27.1** (stable, kira 0.12 sem mudança).
* `mlua` 0.10 → **0.12**: `StdLib::PACKAGE` deixou de existir para Luau
  (`ALL_SAFE` = todas as libs do Luau — sem package/os/io de qualquer forma) e
  `Table::set_metatable` devolve `Result` (propagado nos 3 sítios: host.rs,
  game.rs, bridge/lua.rs).
* `dirs` 6 → **7**, `meshopt` 0.6 → **0.7**, `base64` 0.22 → **0.23**,
  `roxmltree` 0.20 → **0.21** — drop-in (zero alterações de código).
* Lock: `prettyplease` 0.2.37 → **0.3.0** com `bindgen` 0.73.2 como único
  dependente — o pin do build DLSS deixou de ser necessário. O mecanismo: o
  bindgen 0.73.2 declara `prettyplease >=0.2.7, <0.4` e `syn >=2,<4` — NESTE
  lock liga `syn 3.0.9` + `prettyplease 0.3` (o par consistente). Se uma
  dependência futura re-ancorar o syn do bindgen em 2, repor o pin com
  `cargo update -p prettyplease --precise 0.2.37` (o falhanço sem pin é um
  `syn::file::File: ParseQuote` dentro do bindgen, só no build `--features dlss`).

## Follow-ups da ronda (veredictos ao vivo, 2026-10-18)

* **Luau JIT ADOTADO** — feature cargo `luau-jit` (mlua 0.12, codegen do Luau,
  vector_size 3 do `luau0-src`) no default; `VIBER_LUAU_JIT=0` volta ao
  interpretador no MESMO binário. Medido ao vivo (REPL, 10M iterações de loop
  numérico quente): ~1,17× (0,196 s vs 0,235 s). O orçamento de 500 ms por
  chamada interrompe TAMBÉM código JIT — `test_infinite_loop_is_interrupted_by_the_cpu_budget`
  passa com a feature ligada (67/67 testes luau verdes).
* **Re-upload de uniforms de materiais custom: CONTINUA PARTIDO** — o motivo
  de o vento da relva/céu/água viverem em consts no fonte do shader foi
  re-verificado ao vivo (sonda `#[uniform(100)]` da `GrassExtension` mutada em
  onda quadrada por `Assets::get_mut`, 28 tiles de relva vivos no campo da
  câmara): a relva NÃO respondeu ao longo de 2 períodos — só o upload inicial
  chega à GPU. Veredicto anotado em `src/grass.rs` (`configure_shader`).
* **SIGSEGV bindless: ENCERRADO** — o crash de `vkCreatePipelineLayout`
  (`libnvidia-gpucomp`) com materiais custom não-bindless + `#[texture]`
  JÁ NÃO REPRODUZ no bevy 0.20 + wgpu 30 + driver NV 615.71.09 (o gatilho era
  wgpu 29 + driver 595.84). Experimento: `#[bindless]` do
  `TerrainChunkMaterial` removido → boot completo de `worlds/lua-demo` com
  terreno desenhado (look flat). Bindless mantido pelo layout, não por
  necessidade — veredicto em `layer_material.rs` e `chunk.wesl`.
* **`ui_focus_system` portado: MIGRADO AO PICKING NATIVO (2026-10-18)** — o
  port do `ui_focus_system` 0.19 saiu; [`src/ui/interaction.rs`](../src/ui/interaction.rs)
  é agora um adaptador fino: o hit-testing é o `bevy_ui::picking_backend`
  (mesma travessia UiStack/clip/visibilidade, truncada no 1.º bloqueador) e o
  adaptador traduz a cadeia `PointerHits` para a `Interaction`/`Button`
  nuestras com a semântica de sempre (os ~12 ficheiros consumidores não
  mudaram). O blocking vive em `Pickable` (`runtime::ui_pickable` traduz a
  política; o backend ignora `FocusPolicy`). Bónus de perf: o mesh picking
  (sem consumidor na engine) ficou `require_markers: true` — deixou de
  raycastar todas as malhas visíveis por frame. QA ao vivo: tooltip de hover,
  hover de aba e TROCA DE ABA POR CLIQUE (Loja aberta no menu Diário) com o
  binário novo. Nota de QA: clique sintético no MESMO frame do `move` apanha
  a posição ANTIGA (o backend de picking lê o cursor no `First` do frame
  seguinte) — a receita continua sendo `viber debug move X Y` → esperar →
  `click X Y`.

## Watch-list (avaliar numa próxima ronda)

Confirmados compatíveis com Bevy 0.19 (dependências publicadas verificadas a
2026-09-07):

- **`bevy_rerecast` + `vleue_navigator`** — port do Recast (navmesh a partir
  da geometria real: trimesh das colunas + colliders) + pathfinding Polyanya.
  Usaria a tag `NavMesh` (hoje só afina o landmass) e substituiria o
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
