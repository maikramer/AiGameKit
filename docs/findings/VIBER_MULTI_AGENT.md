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
| **Mundo/Personagem** | `src/terrain/`, `src/physics.rs`, `src/animation.rs`, `src/player.rs`, `src/particles.rs`, `src/worldsys.rs`, câmara em `src/recipes/spawn.rs` | terreno, colisão (Rapier), animação glTF, controlo do herói, assentamento no chão, iluminação do ciclo dia/noite |

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

**RESOLVIDO (2026-08-31, agente de assets/infra — coordenação: o dono do Sky
estava a iterar noutra frente e o xadrez bloqueava o exemplo inteiro):** o
xadrez azul/branco NÃO era o shader nem o binding — era **winding do mesh do
domo**. Em `sky_dome_mesh()` o split do quad era `[a,e,d, a,e,c]`: o cálculo
vetorial mostra `[a,e,d]` com normal **para fora** (culled visto de dentro →
aparecia o clear-color) e `[a,e,c]` para dentro — um triângulo sim, um não,
exatamente o xadrez. Fix (1 linha): `indices.extend([a, d, e, a, e, c]);`
Verificado: mundo `bare` (céu sem nuvens/sol) passou de checker completo para
gradiente suave ([sky-bare.png](file:///tmp/viber-shots/sky-bare.png) vs
[sky-bare-fixed.png](file:///tmp/viber-shots/sky-bare-fixed.png)). Bissecção
que isolou: full→checker, `cloud-density=0`+`sun-intensity=0.1`→checker
puro (matou teoria de nuvens/FBM), sem `<Sky>`→azul chapado, só-terreno→sem
checker.

Ao editar `src/sky.wgsl`, sincronizar sempre a cópia:

```bash
cp Viber/src/sky.wgsl Viber/examples/simple-rpg/shaders/sky.wgsl
```

## Assets em falta — `*_collision.glb` (65 ficheiros) — RESOLVIDO 2026-08-31

**Atualização (agente de assets/infra):** os 65 `_collision.glb` **existem no
pool** (`Viber/examples/shared-assets/public/assets/meshes/**` — o pool foi movido
para dentro de Viber em 2026-08-31) — o doc
original confundiu o pool com o espelho do exemplo. Nunca apareceram no
espelho porque os paths só ocorrem **dentro do valor** de attrs `collider="…;
mesh-url: …"` (o scanner só lia attrs cujo *nome* termina em `url`).
`scripts/sync_assets.py` foi corrigido: (1) attrs de asset sem sufixo url
(`texture=`, `terrain-texture=`, `icon=`) agora são recolhidos; (2) para cada
GLB visual espelhado, o irmão `<base>_collision.glb` do pool entra na fila.
Resultado: 108 ficheiros novos no espelho (vale_grass.png incluído via
`texture=`), **zero** `Path not found` em runtime (verificado). A tempestade
de 404 e o terreno invisível (material não-preparado — ver WIP de
`terrain/runtime.rs`) tinham esta mesma causa.

Pendência menor: `/assets/icons/hud_*.png` (5 urls) não existem em lado
nenhum — o pool não tem pasta `icons/`. Cosmético (HUD usa texto).

## Achados do agente de assets/infra (2026-08-31)

1. **`main.rs` — bridge perdida na delegação**: `viber run world.xml
   --bridge PORT` dentro do checkout delega em `cargo run -- run <world>`
   **sem propagar `--bridge`** → a bridge nunca ligava quando o agente usava
   o binário do checkout. Corrigido (`delegate_run_to_cargo` recebe e passa
   `bridge`).
2. **Cidade renderiza** depois do sync de assets + WIP dos agentes de
   terreno/mundo: muralha, portão, casas, mercado, bancos e NPCs visíveis e
   nas posições certas (y≈24.6 = heightfield real). O invisível anterior era
   ausência de assets + parse GLB lento em build debug — não é bug de
   `GltfScene`.
3. **Grid de terreno 2× (para o agente de Terrain):** a árvore live mostra
   **15 625 chunks = 125×125**, `chunk 0-0` em `(-3968, 0, -3968)`, espaçamento
   64 m → cobertura **±4000 m** para um `world-size="4000"` (deveria ser
   ±2000). Os índices parecem centrados em 0 (`-62..+62` = 125 valores) em vez
   de `0..count` com offset `−world/2`. Efeito: anel de chunks fora do
   heightfield (amostragem out-of-bounds) e o dobro da memória/draw. O resto
   (plaza branca = base-color sem textura de estrada) é provavelmente o mesmo
   caminho de material que o WIP do runtime já cobre.
4. **Xadrez azul/branco no céu — RESOLVIDO:** era **winding do mesh do domo**
   (ver secção *Sky shader — binding canónico* abaixo): o split `[a,e,d]`
   tinha normal para fora e era culled de dentro — um triângulo sim, um não.
   Fix em `sky_dome_mesh()`: `[a, d, e, a, e, c]`. Bissecção que isolou
   (mundos mínimos, cópias /tmp com assets symlinkado): full→checker,
   `cloud-density=0`+`sun-intensity=0.1`→checker puro (matou a teoria
   nuvens/FBM), sem `<Sky>`→azul chapado, só-terreno→sem checker.
   **Estado pós-fix (mundo completo):** vila inteira renderiza (muralha,
   portão, casas, mercado, fogueira, bancos), terreno verde, sombras, HUD,
   céu limpo — screenshot `final-shot-2/3.png`. Falta vs referência VibeGame:
   praça/estradas com textura cobblestone (hoje base-color branco — road
   texture não aplica, escopo Terrain) e minimapa sem blips.

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
