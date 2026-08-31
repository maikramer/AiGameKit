# Viber multi-agente — coordenação de escopos, instâncias e assets

Data: 2026-08-31 · Status: live (coordenação de agentes paralelos no crate `Viber/`)

## Contexto

Vários agentes editam o crate `Viber/` (Bevy 0.19) em paralelo e rodavam
instâncias de teste simultâneas sem coordenação — com `pgrep`/`kill` a
matarem instâncias uns dos outros. Este doc fixa o mapa de escopos
excluívos, as regras de concorrência e o inventário de assets em falta.

## Mapa de escopos EXCLUÍVOS por agente

Cada agente só edita os ficheiros da sua linha. Qualquer alteração fora do
escopo exige coordenação com o orquestrador.

| Domínio | Ficheiros (excluívos) | Notas |
|---------|----------------------|-------|
| **Terrain** | `src/terrain/` (todo o diretório) | heightmap/pads/águas do mundo |
| **Sky** | `src/sky.rs` + `src/sky.wgsl` | dono único; ver binding canónico abaixo |
| **Luau** | `src/luau.rs` | scripting/sandbox |
| **IA** | `src/ai.rs` | comportamento de NPCs/inimigos |
| **HUD/Vitals** | `src/hud.rs` + `src/vitals.rs` | overlay e saúde/fome/etc. |

Infra partilhada (só o agente de coordenação toca, ou com aviso no canal):
`scripts/instance-lock.sh` (lock de instância — uso livre, edição coordenada),
`docs/findings/VIBER_MULTI_AGENT.md` (este doc),
`examples/simple-rpg/shaders/sky.wgsl` (cópia sincronizada de `src/sky.wgsl` —
só o agente Sky edita o conteúdo; ver abaixo).

## Regras de concorrência

1. **NUNCA `pkill`/`kill` processos viber.** Se uma instância de teste está
   a correr, é de outro agente. O `kill` é recusado se vramd estiver busy e,
   aqui, é proibido por norma: mata o trabalho alheio e corre a fila.
2. **Lock de instância para testes — obrigatório.** Antes de rodar
   `viber run` (ou qualquer instância do jogo) num teste/manual, adquira o
   lock `/tmp/viber-instance.lock` via `scripts/instance-lock.sh`:

   ```bash
   # Padrão — script de teste (o trap EXIT liberta sozinho):
   source scripts/instance-lock.sh
   viber_lock_acquire "meu-teste" || exit 1
   cargo run -p viber -- run examples/simple-rpg/world.xml

   # Alternativa — wrapper:
   scripts/instance-lock.sh exec -- cargo run -p viber -- run world.xml

   # Consulta (exit 0 = instância a correr de outro agente):
   scripts/instance-lock.sh is-locked
   ```

   O lock falha (exit 1) se outro processo vivo o detém; lock órfão (PID
   morto) é detectado como stale e assumido. Override de caminho:
   `VIBER_INSTANCE_LOCK=...`.
3. **Builds partilham `target/`** — o cargo serializa com file lock. Se um
   build falhar com `Blocking waiting for file lock` ou erro transitório de
   lock, **reintente** (não limpar `target/`, não mudar de dir de build).
4. **Commits só pelo orquestrador.** Agentes trabalham no `main` sem
   commitar; o orquestrador integra e comita os resultados.
5. **Nada de formatters/linters globais** (`cargo fmt` no crate inteiro,
   etc.) — tocam ficheiros de outros escopos.

## Sky shader — binding canónico

`Viber/src/sky.wgsl` é a FONTE (embutida via `include_str!` em `sky.rs` e
escrita no world dir a cada arranque como `shaders/sky.wgsl`).

O binding (2,0) **tem de ser** `var<storage, read> sky: SkyUniform;` — o
derive `AsBindGroup` da Bevy 0.19 gera um layout Storage-LOAD para o bloco;
com `var<uniform>` a validação falha em runtime ("doesn't match the shader
Uniform"). O ficheiro já foi regenerado com a variante uniform por outro
agente; a variante storage/read está agora fixada no ficheiro fonte (com
comentário explicativo) e na cópia do exemplo.

Ao editar `src/sky.wgsl`, sincronizar sempre a cópia:

```bash
cp Viber/src/sky.wgsl Viber/examples/simple-rpg/shaders/sky.wgsl
```

## Assets em falta — `*_collision.glb` (65 ficheiros)

Os colisores `collider="shape: trimesh; mesh-url: /assets/meshes/..._
collision.glb"` referenciados em `examples/simple-rpg/world/**` não existem
no pool (`examples/simple-rpg/assets/meshes/`) nem no espelho do mundo.
Levantam 67 ocorrências de Path-not-found em runtime (65 referências vivas
únicas + 2 menções em comentários: `goblin_collision.glb` em
`world/creatures/enemies.xml` — marcado como unused, os inimigos usam cápsula
CCT — e `sandstone_arch_collision.glb` em `world/landmarks/desert.xml` —
comentado explicitamente como não-collider).

De 66 referências com caminho `/assets/...`, só
`/assets/meshes/infra/river_bridge_wood_collision.glb` existe.

Lista das 65 em falta, por diretória de `assets/meshes/`:

- **desert (3):** `desert_obelisk`, `ruin_pillar`, `scorpion_nest`
- **forest (1):** `witch_hut`
- **infra (5):** `city_gate_arch`, `city_wall_corner`, `city_wall_seg_a`,
  `city_wall_seg_b`, `city_wall_seg_c` — (`river_bridge_wood` existe)
- **interiors (20):** `bed_simple`, `bookshelf`, `candelabra_tall`,
  `cauldron_iron`, `chapel_altar`, `chapel_pew`, `chapel_pulpit`,
  `chapel_statue`, `church_organ`, `confessional`, `cupboard`,
  `dining_table`, `fireplace_hearth`, `forge_furnace`, `rug_woven`,
  `sledge_hammer`, `spinning_wheel`, `stool_wood`, `tavern_bar`,
  `wooden_chair`
- **props (2):** `crystal_blue`, `stone_pillar`
- **swamp (3):** `bone_altar`, `swamp_shack`, `wrecked_boat`
- **terrain (7):** `crystal_mine_entrance`, `druid_stone_altar`,
  `form_cliff_1`, `form_cliff_20`, `form_stack_11`, `form_stack_6`,
  `stone_cairn`
- **village (24):** `anvil`, `campfire_pit`, `chapel`, `chopping_block`,
  `forge_bellows`, `horseshoe_pile`, `iron_brazier`, `log_pile`,
  `market_stall`, `medieval_well`, `notice_board`, `quench_trough`,
  `shepherd_cottage`, `torch_post`, `treasure_chest`, `village_barn`,
  `village_forge`, `village_house`, `village_longhouse`, `watchtower`,
  `weapon_rack`, `wooden_barrel`, `wooden_bench`, `wooden_crate`

*(todos com sufixo `_collision.glb`; 3+1+5+20+2+3+7+24 = 65)*

### Opções para um passo futuro (NÃO executado agora)

1. **Gerar colisores low-poly** a partir dos GLBs visuais do pool
   (`village_forge_lod0.glb` etc.) — ex. `text3d collision` / decimate +
   export do LOD base, mantendo o sufixo `_collision.glb`. Custo: passo de
   pipeline + espaço no pool; benefício: colisão trimesh fiel onde ela é
   realmente usada.
2. **Ignorar** — estes colisores eram do esquema precompute; a física do
   jogo usa primitives (cápsula/cilindro/AABB via `fitColliderFromAabb` e
   `PrecomputePlugin`), e o mesh-collider só faz fetch para
   TriMesh/ConvexHull. Migrar as entidades para `shape: precompute` ou
   primitives equivaleria, na prática, a não precisar dos GLB.

Referência do desenho precompute:
`docs/findings/PRECOMPUTE_COLLIDERS_FINDINGS.md`.
