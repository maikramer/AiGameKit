# VibeGame — Plano de Adoção de Pacotes npm

Data: 2026-07-04. Resultado de auditoria das dependências e do código custom do
`VibeGame/` para identificar onde pacotes npm prontos (mesmo exóticos, desde que
mantidos) podem substituir código próprio ou preencher lacunas.

Contexto: a engine já migrou várias áreas para pacotes maduros nos últimos ciclos
(`camera-controls`, `maath`, `simplex-noise`, `stats-gl`, `postprocessing`,
`three.quarks`, `troika-three-text`, `@recast-navigation/three`, `three-mesh-bvh`,
`@dimforge/rapier3d-compat`). Este plano continua esse movimento.

## Status de execução (2026-07-04)

Executado numa sessão sem gate de teste manual entre fases (pedido explícito do
usuário); validação final: `tsc --noEmit` limpo, `eslint` sem novos erros,
`bun test` 2072-2075 pass / 0 fail (variação de contagem entre runs é flake
pré-existente, não relacionada às mudanças — suíte `tests/unit/terrain` isolada
roda 68/68 estável).

| Fase | Status |
|---|---|
| 1 — remover `three-pathfinding`, `colyseus.js` | ✅ feito |
| 2 — `@three.ez/instanced-mesh` em `auto-instance.ts` | ✅ feito (rewrite completo, LOD/culling nativos) |
| 3 — yuka → substituto | ✅ feito, **mas diferente do plano**: `ai-steering` não tinha navmesh (esse já usa DetourCrowd via `recast-navigation`, plugin `navmesh`, sem yuka). Escrito `vehicle.ts` — steering Reynolds (seek/flee/wander/obstacle) sem nenhuma lib nova, só `THREE.Vector3` |
| 4 — CSM (`three-custom-shader-material`) | ✅ feito, **incluindo terrain** (ver detalhe abaixo). `destructible/fx.ts` e `water/systems.ts` migrados primeiro (corrigem o bug histórico de `uTime` congelado). `terrain/systems.ts` migrado numa segunda passada com QA visual no browser — ver "Terrain shader — migração + QA visual" |
| 5.1 — `n8ao` (SSAO) | ✅ feito, substitui `SSAOPass` (que não tinha `.intensity`) |
| 5.2 — `detect-gpu` | ✅ feito, tier usado só para sample count do n8ao (`setQualityMode`); radius/intensity continuam 100% autor-controlados |
| 5.3 — `tweakpane` | ✅ feito, painel auto-bind no registry de debug vars existente, tecla `T`, import dinâmico (dev-only) |
| 6 — `three-mesh-ui` → `@pmndrs/uikit` | ✅ feito. Achado extra: `three-mesh-ui` já tinha sido removido do `package.json` (fase 1 cleanup) mas o pacote ainda existia fisicamente em `node_modules/` sem estar no lockfile — teria quebrado num install limpo |
| 7 — `@takram/three-clouds`/`three-atmosphere` | ❌ não implementado (recomendação mantida como protótipo futuro, GPU pesada, precisa QA visual) |
| 7 — `alea` | ❌ não implementado: inspeção mostrou que `weather/rng.ts` já tem `mulberry32` seedável funcionando — trocar seria só churn |
| 8 — manter custom | sem mudanças, conforme plano |

Commits gerados automaticamente por hooks do ambiente (não por `git commit`
explícito do agente) — revisar/squash conforme desejado:
`07109add6`, `072a3c1e0`, `da65461c8`, `92294d6c0`.

### Terrain shader — migração + QA visual (2026-07-04, sessão seguinte)

Migração completa do `onBeforeCompile` de ~230 linhas em
`terrain/systems.ts` para `three-custom-shader-material`, com QA visual real
no browser (Playwright, exemplo `simple-rpg`).

- **`_shaderRefs` eliminado por completo.** Como CSM mantém `uniforms` vivos
  diretamente no material (não recriados a cada recompile), `applyTerrainSplat`,
  `applyLakeSand` e o crossfade de textura em `TerrainLodSelectSystem` agora
  escrevem direto em `mat.uniforms.X.value` — sem laço `for (const sh of refs)`.
  Isso também elimina os 9 avisos `@typescript-eslint/no-explicit-any` que
  existiam nesse arquivo (todos vinham de `(mat as any)._shaderRefs`).
- **Armadilha real encontrada e corrigida:** o hook de corpo principal do CSM
  (`csm_DiffuseColor`/`csm_FragNormal`/`csm_Roughness`) roda **antes** dos
  chunks padrão do three (`normal_fragment_begin`, `normal_fragment_maps`) no
  mesmo escopo de função — não em um bloco isolado. Declarar variáveis locais
  com os mesmos nomes que esses chunks usam mais adiante (`tbn`, `mapN`) causa
  erro de compilação GLSL `'tbn' : redefinition`. Corrigido renomeando para
  `csmTbn`/`csmMapN`. A blendagem de normal tangent-space reconstrói o TBN
  manualmente via `getTangentFrame()` (função de arquivo do three, sempre
  declarada quando `USE_NORMALMAP_TANGENTSPACE` está ativo sem tangentes de
  vértice) em vez de depender do `tbn` chunk-local — usa `csm_FragNormal`
  (que já tem o valor default `normalize(vNormal)` nesse ponto) como
  `surf_norm`, equivalente exato ao `normal` pré-perturbação do código original.
- **Segunda armadilha (mecânica, não de shader):** um backtick literal dentro
  de um comentário GLSL escrito como texto (`` `tbn`/`mapN` ``) fechou
  prematuramente o template string JS que envolve o shader inteiro — quebrou
  o parse do arquivo TypeScript (erro Vite/oxc "Expected a semicolon"), não o
  GLSL. Aconteceu duas vezes (uma vez já na primeira sessão, em outro
  comentário). Lição: nunca usar `` ` `` dentro de comentários dentro de
  shaders escritos como template literals — usar aspas simples ("...") em vez
  de crase para destacar identificadores GLSL nos comentários.
- **QA visual (Playwright, `examples/simple-rpg`):** capturado o erro real via
  monkey-patch de `WebGL2RenderingContext.prototype.{compileShader,linkProgram}`
  (o browser não expõe o info-log de compilação via `console.error` nesse
  caso — só o warning genérico `WebGL: INVALID_OPERATION: useProgram: program
  not valid`, centenas de vezes). Antes da correção: terreno renderizava azul
  liso (shader não linkava, nenhum fragmento desenhado). Depois: confirmado
  visualmente — textura base com detalhe de normal mapping (grama com relevo
  direcional), blend de bioma (transição grama → deserto/wasteland visível),
  e sand-blend de rio (faixa de areia clara na margem, exatamente como o
  código original). Zero erros de console durante navegação/movimento.

Arquivo de referência: `VibeGame/src/plugins/terrain/systems.ts`
(`buildTerrainMaterial`, `terrainFragmentShader`, `TERRAIN_VERTEX_SHADER`).

---

## Fase 1 — Limpeza: dependências mortas (esforço: trivial)

### 1.1 Remover `three-pathfinding`

- **Achado:** 0 referências em `VibeGame/src` (`grep` não encontra nenhum import).
  Pathfinding real é feito por `recast-navigation` / `@recast-navigation/three`
  (plugin `navmesh`).
- **Como fazer:**
  1. Remover a entrada de `dependencies` em `VibeGame/package.json`.
  2. `bun install` para atualizar `bun.lock`.
  3. `make test-vibegame && make check-vibegame && make build-vibegame`.
- **Risco:** nenhum. Se algum exemplo externo importar, o build do exemplo acusa.

### 1.2 Remover `colyseus.js`

- **Achado:** 0 referências em `VibeGame/src`. Multiplayer nunca foi ligado.
- **Como fazer:** mesmo procedimento de 1.1. Se multiplayer for meta futura,
  registrar em issue/doc em vez de manter a dep instalada.
- **Risco:** nenhum. `msgpackr` e `fflate` têm 1 uso cada — **verificar** esses usos
  antes de mexer; se forem só resquício do colyseus, remover junto.

---

## Fase 2 — Maior ROI: instancing com `@three.ez/instanced-mesh`

- **Achado:** `plugins/gltf-xml/auto-instance.ts` (~540 linhas) implementa à mão:
  slots de instância, dirty-check de matrizes, culling manual (`culled` flag),
  repack de LOD gatado por câmera, bounding sphere estática. Memórias do projeto
  registram bugs históricos aqui (corrupção de source-matrix, LOD repack, trees
  deitadas). O spawner de vegetação/rochas é o hot path de performance do mapa.
- **Pacote:** [`@three.ez/instanced-mesh`](https://github.com/three-ez/instanced-mesh)
  (`InstancedMesh2`). Mantido ativamente. Oferece nativamente:
  - frustum culling **por instância** (BVH espacial interno);
  - sistema de LOD embutido (`addLOD(geometry, material, distance)`);
  - capacidade dinâmica (add/remove instância sem realocar na mão);
  - sorting e raycast acelerado por BVH.
- **Como fazer (incremental):**
  1. `bun add @three.ez/instanced-mesh` na engine.
  2. Criar adaptador em `gltf-xml/auto-instance.ts`: trocar `THREE.InstancedMesh`
     por `InstancedMesh2`, mapeando slot→`instances[i]` da lib.
  3. Substituir o repack de LOD custom por `addLOD()` com as distâncias que hoje
     estão no perfil do spawner (`profiles.ts`).
  4. Deletar o código de culling/dirty-check manual que a lib passa a cobrir.
  5. Validar numericamente (contagem de instâncias visíveis, posições) — headless
     WebGPU/swiftshader não desenha InstancedMesh confiável em screenshot
     (fato conhecido do projeto), então testes devem ser de dados, não de imagem.
  6. Benchmark antes/depois com stats-gl no `simple-rpg` (draw calls, frame time).
- **Risco:** médio. API diferente (instâncias são objetos-proxy, não índices).
  Integração com o sidecar ECS (eid→slot) precisa manter cleanup via
  `state.onDestroy`. Fazer atrás de flag ou em branch até paridade visual.

---

## Fase 3 — Menos uma dep stale: `yuka` → DetourCrowd (recast)

- **Achado:** `yuka` está sem release desde ~2023. Uso restrito a
  `plugins/ai-steering` (~300 linhas, 3 arquivos): steering behaviors para AI.
- **Pacote substituto:** nenhum novo — `recast-navigation` (já dependência) expõe
  **DetourCrowd**: agentes com path following, local avoidance (RVO), raio/altura,
  max speed/accel, tudo integrado ao navmesh que o plugin `navmesh` já baked.
- **Como fazer:**
  1. No plugin `ai-steering`, criar `Crowd` a partir do `NavMesh` existente
     (`new Crowd(navMesh, { maxAgents, maxAgentRadius })`).
  2. Cada entidade AI vira `crowd.addAgent(pos, params)`; sistema por frame chama
     `crowd.update(dt)` e copia `agent.position()`/`agent.velocity()` para
     `Transform`/`CharacterMovement`.
  3. Comportamentos tipo seek/flee que hoje usam yuka viram `agent.requestMoveTarget()`
     (seek) e lógica fina fica no FSM do `rpg-ai` (que já é custom e não usa yuka).
  4. Remover `yuka` e `types-yuka.d.ts`.
- **Risco:** médio-baixo. Ganho colateral: avoidance entre inimigos (hoje
  inexistente) sai de graça. Atenção ao failsafe de navmesh já documentado
  (gate com timeout) — agentes só entram no crowd após navmesh pronto.

---

## Fase 4 — Shaders declarativos: `three-custom-shader-material`

- **Achado:** `onBeforeCompile` manual em `plugins/terrain/systems.ts`,
  `plugins/water/systems.ts` e `plugins/destructible/fx.ts`. Padrão frágil:
  quebra silenciosamente em upgrade do Three (projeto já sofreu com símbolos
  removidos do Three em deps de pós-processamento).
- **Pacote:** [`three-custom-shader-material`](https://github.com/FarazzShaikh/THREE-CustomShaderMaterial)
  (pmndrs/Faraz Shaikh, mantido). Permite estender `MeshStandardMaterial` etc.
  com chunks GLSL declarativos (`vertexShader`/`fragmentShader` + uniforms)
  preservando luzes/sombras/fog do material base.
- **Como fazer:**
  1. `bun add three-custom-shader-material` (usar o entry `vanilla`).
  2. Migrar um material por vez, começando pelo mais simples (`destructible/fx.ts`),
     depois água (atenção: refs vivas de uniform `uTime` — bug já visto quando a
     ref do shader morre), terreno por último (mais chunks).
  3. Critério de aceitação: paridade visual via A/B no browser (MCP firefox-devtools,
     fluxo já usado no projeto) + testes existentes verdes.
- **Risco:** baixo, migração é local a cada material. Não migrar tudo de uma vez.

---

## Fase 5 — Pós-processamento e qualidade

### 5.1 `n8ao` — SSAO pronto

- **Achado:** pipeline usa a lib `postprocessing` 6.x; não há AO de qualidade.
- **Pacote:** [`n8ao`](https://github.com/N8python/n8ao) (N8python, mantido).
  Expõe `N8AOPostPass` compatível com `EffectComposer` da lib `postprocessing`.
- **Como fazer:** adicionar pass no plugin `postprocessing` atrás de atributo XML
  (ex.: `<PostProcessing ao="true" ao-radius="1.5">`), seguindo o padrão de
  atributos kebab-case dos recipes. Intensidade moderada por padrão (preferência
  registrada do projeto para exemplos jogáveis).
- **Risco:** baixo. Custo de GPU — ligar por tier (ver 5.2).

### 5.2 `detect-gpu` — auto-tier de qualidade

- **Achado:** engine não tem detecção de capacidade da GPU; efeitos pesados
  (nuvens, AO, resolução de terreno, contagem de partículas) são fixos.
- **Pacote:** [`detect-gpu`](https://github.com/pmndrs/detect-gpu) (pmndrs,
  mantido). Benchmark table → tier 0–3 + fps estimado.
- **Como fazer:**
  1. `getGPUTier()` async no bootstrap do renderer (plugin `rendering`).
  2. Publicar tier no registry global (`__VIBEGAME__.rendering()` já existe como
     ponto de acesso) e expor default para plugins: postfx liga AO/bloom por tier,
     weather reduz passos de raymarch das nuvens, terrain reduz resolução de chunk.
  3. Sempre sobrescritível por atributo XML explícito (mesma filosofia de
     resolução soft do QualityEngine Python do monorepo).
- **Risco:** baixo. Fallback: tier indefinido ⇒ comportamento atual.

### 5.3 `tweakpane` — painel de debug

- **Achado:** plugin `debug` tem stats-gl + registry custom + toggle de postfx,
  mas tuning ao vivo (fog, exposure, bloom, água) é feito via console
  (`__VIBEGAME__.rendering()` na mão).
- **Pacote:** [`tweakpane`](https://tweakpane.github.io/docs/) v4 (mantido).
- **Como fazer:** no plugin `debug` (dev-only, tree-shaken do build de release),
  gerar painel automaticamente a partir do `registry.ts` existente: cada entrada
  registrada vira binding. Import dinâmico (`await import('tweakpane')`) para não
  entrar no bundle de produção.
- **Risco:** nenhum em produção se o import for lazy e gated por debug.

---

## Fase 6 — UI 3D: `three-mesh-ui` → `@pmndrs/uikit`

- **Achado:** `three-mesh-ui` sem release desde ~2023 (efetivamente abandonado);
  o projeto até carrega `@fredli74/typr` e `types-three-mesh-ui.d.ts` como
  suporte. Uso: `plugins/hud/components.ts` + `systems.ts` (parte 3D do HUD;
  a maior parte do HUD é DOM e não é afetada).
- **Pacote:** [`@pmndrs/uikit`](https://github.com/pmndrs/uikit) — versão
  **vanilla** (sem React). Flexbox real (yoga), texto SDF, mantido ativamente.
- **Como fazer:**
  1. Inventariar quais widgets do HUD realmente usam three-mesh-ui (grep indica
     poucos pontos).
  2. Recriar com `Container`/`Text` do uikit vanilla; chamar `root.update(dt)`
     no system existente.
  3. Remover `three-mesh-ui`, `@fredli74/typr` e o `.d.ts` custom.
- **Risco:** médio (API bem diferente). Fazer quando houver trabalho planejado
  no HUD 3D, não como projeto isolado.

---

## Fase 7 — Exóticos de alto impacto visual (avaliar com protótipo)

### 7.1 `@takram/three-clouds`

- **Achado:** `plugins/weather/clouds.ts` implementa nuvens GPU custom.
- **Pacote:** [`@takram/three-clouds`](https://github.com/takram-design-engineering/three-geospatial)
  — nuvens volumétricas raymarched com iluminação física, parte do monorepo
  three-geospatial (Takram, mantido ativamente).
- **Como fazer:** protótipo isolado primeiro (custo de GPU é alto); se aprovado,
  integrar como modo `clouds="volumetric"` no recipe `<Weather>`, mantendo o
  modo atual como fallback para tiers baixos (integra com 5.2).
- **Risco:** alto em GPU fraca (projeto tem alvo de 6 GB VRAM em dev). Por isso
  gate por tier é pré-requisito.

### 7.2 `@takram/three-atmosphere`

- **Pacote:** mesma família; céu atmosférico físico (modelo Bruneton) com sol/lua
  corretos por posição.
- **Uso proposto:** alternativa procedural ao skymap equirect gerado por IA —
  quando o jogo não tem asset de sky, `<Skybox mode="atmosphere">` em vez do
  fallback atual. Também habilita ciclo dia/noite futuro.
- **Risco:** médio; conviver com o pipeline PMREM/equirect existente exige cuidado
  (histórico de gotchas equirect documentado no projeto).

### 7.3 `alea` — PRNG seedável

- **Achado:** `simplex-noise` (já usado no weather placement) aceita função
  random custom; spawner tem requisito de determinismo (weather placement já é
  determinístico via noise).
- **Como fazer:** `bun add alea`; nos pontos de spawn que usam `Math.random()`,
  aceitar `seed` no XML e criar `alea(seed)`. Mundo reproduzível por seed.
- **Risco:** trivial.

---

## Fase 8 — Baixa prioridade / decisão de manter

| Área | Decisão | Motivo |
|---|---|---|
| `plugins/tweening` (282 linhas) | **Manter custom** | Pequeno, integrado ao ECS; animejs/tween.js não compensam a ponte |
| `plugins/i18n` (248 linhas) | **Manter custom** | i18next é overkill para o escopo |
| `plugins/save-load` (localStorage) | **Opcional: `idb-keyval`** (~600 B) | Só se saves crescerem além de ~5 MB de quota ou o sync-write travar frame |
| `howler` (áudio) | **Manter** | Manutenção lenta mas estável; migração para WebAudio puro não paga |
| `bitecs` | **Manter** | Núcleo da engine; miniplex etc. seriam reescrita, não adoção |
| Parser XML custom (`core/xml`) | **Manter** | Fino (187 linhas) sobre DOMParser nativo |
| Mobile input | **Futuro: `nipplejs`** | Joystick touch mantido; só se mobile virar meta (hoje: teclado+gamepad) |
| `realism-effects` / `screen-space-reflections` (0beqz) | **Evitar** | Já quebrou com upgrade do Three no projeto; manutenção incerta |

---

## Ordem de execução recomendada

| # | Item | Esforço | Ganho |
|---|---|---|---|
| 1 | Fase 1 — remover deps mortas | ~5 min | Bundle/install menor, lockfile limpo |
| 2 | Fase 2 — `@three.ez/instanced-mesh` | dias | Perf no hot path + deletar ~400 linhas frágeis |
| 3 | Fase 3 — yuka → DetourCrowd | ~1 dia | -1 dep stale, avoidance de graça |
| 4 | Fase 5.2 — `detect-gpu` | horas | Pré-requisito para AO/nuvens gated |
| 5 | Fase 5.1 — `n8ao` | horas | Salto visual barato |
| 6 | Fase 4 — CSM (um material por vez) | incremental | Robustez a upgrades do Three |
| 7 | Fase 5.3 — `tweakpane` | horas | DX de tuning |
| 8 | Fase 7 — takram clouds/atmosphere, `alea` | protótipos | Visual AAA opcional |
| 9 | Fase 6 — uikit | quando mexer no HUD 3D | Sai de dep abandonada |

Validação padrão para toda fase: `make test-vibegame`, `make check-vibegame`,
`make lint-vibegame`, build do `simple-rpg` e verificação visual no browser
(MCP firefox-devtools/Playwright), conforme prática já estabelecida no projeto.
