# Composition & Prototypes — objetos inteiros em primitivas (XML)

Uma `<Composition>` define um objeto completo com primitivas: **1 corpo**
(Rapier, default `fixed`) + **1 colisor exato por primitiva** + luzes e
partículas como filhos. Um `<Prototype>` define o objeto **uma vez**; um
`<Use>` instancia-o **N vezes** com transform/overrides — a expansão é em
parse-time, custo zero em runtime.

Demo executável: `worlds/composition.xml` (`viber analyze` / `viber run`).

```html
<!-- Definir uma vez (top-level, pode viver num <Include>) -->
<Prototype id="lantern-post">
  <Composition>
    <Cylinder translation="0 1.4 0" radius="0.07" half-height="1.4" base-color="#4a4038" />
    <Sphere translation="0 2.6 0" radius="0.14" base-color="#ffd9a0" collider="none" />
    <PointLight translation="0 2.6 0" color="#ffd9a0" intensity="900" />
  </Composition>
</Prototype>

<!-- Instanciar N vezes -->
<Use prototype="lantern-post" pos="-1.4 0 -0.2" name="lanterna.portao" />
<Use prototype="lantern-post" pos="6 0 6" euler="0 90 0" scale="1.2 1.2 1.2" />

<!-- Ou em linha, sem protótipo -->
<Composition name="mesa" translation="0 0 4" seat="none">
  <Box translation="0 0.45 0" half-size="1.1 0.08 0.7" />
</Composition>
```

## `<Composition>` (root do objeto)

| Attr | Default | Notas |
|------|---------|-------|
| `translation`/`pos`, `euler`, `rotation`, `scale` | — | attrs universais (`euler` em **graus**; `rotation` com 3 valores = **radianos**, compat VibeGame; com 4 = quat) |
| `body` | `fixed` | `fixed`/`static`, `dynamic`, `kinematic`, `none` (sem corpo) — `none` é honrado (bug do VibeGame corrigido) |
| `collider` | `auto` | `auto` = colisor exato por parte; `none` = só visual. Component-strings não se aplicam ao objeto inteiro |
| `place` | — | `place="at: x z; align-to-terrain: 0\|1; base-y-offset: y"` — colocação explícita: reescreve o XZ do objeto para `at` e amostra o solo (sobe **e** desce); `align-to-terrain` orienta o +Y à normal preservando o yaw |
| `seat` | `auto` | `seat="none"` = objeto flutuante (mantém a cota autoral; sem `SeatOnTerrain`) |
| `name`, `tag`, `script`, `destructible` | — | attrs universais |

Regras de seating (na prática): sem `place` e sem `seat="none"`, a composition
assenta no terreno **como um bloco** (amostra no centro do conteúdo) — nunca
divide as partes, ao contrário de um `<Group>` espalhado.

## Partes (primitivas filhas)

Tags: `Box` (alias de `Cuboid`), `Sphere`, `Cylinder`, `Plane`, `Capsule`.
Grupos aninhados funcionam como sub-pivots; as primitivas dentro deles
continuam a ser partes.

| Attr | Notas |
|------|-------|
| `translation`/`pos` | local ao root da composition |
| `euler` (graus) / `rotation` (3 = radianos, 4 = quat) | rotação da parte |
| `scale` | multiplica a malha **e** o colisor |
| `half-size` (Box/Plane), `radius`, `half-height` | dimensões por forma |
| `base-color`, `metallic`, `roughness` | material PBR |
| `opacity` | 0..1; `<1` → translúcido (`AlphaMode::Blend`); `0` = invisível (parede de colisor puro) |
| `emissive`/`emissive-color` | brilho próprio (#hex ou cor nomeada) |
| `texture`/`texture-url`, `texture-tile-size` | base color map |
| `collider` | por parte: `none` desliga; `shape: box\|sphere\|cylinder\|capsule; …` substitui o colisor derivado |

Colisor derivado da forma (sem espera de malha, sem AABB aproximado):
`Box`→cuboid, `Sphere`→ball, `Cylinder`→cylinder, `Capsule`→capsule,
`Plane`→lâmina fina (0.02 m). Non-uniform scale: esfera usa o maior eixo
(elipsoide→ball); cilindro/cápsula usam o maior eixo horizontal para o raio.
As partes colam-se ao corpo do root — **colisor composto do Rapier**: vãos
(portas, arcadas) ficam atravessáveis onde o antigo `collider="auto"` de
grupo (1 AABB da subárvore) bloqueava.

Filhos não-primitivos (`PointLight`, `ParticleSystem`, `DialogueNPC`, …) são
entidades normais da hierarquia — seguem o objeto.

## `<Prototype>` / `<Use>`

- `<Prototype id="id">` aceita **qualquer árvore** como raiz (uma
  `<Composition>`, um `<Group>`, uma primitiva, uma luz). Definições são
  recolhidas de TODO o mundo expandido (incluindo `<Include>`) **antes** da
  expansão — a ordem no documento não importa; a última definição de um id
  ganha. Definições não spawnam entidade.
- `<Use prototype="id" …>` clona a árvore e aplica os attrs do `<Use>` sobre
  o **root final**: `name`, `tag`, `script`, `destructible`, `collider`,
  `rigidbody`/`body`, `translation`/`pos`, `euler`, `rotation`, `scale`,
  `transform` (component-string). Campos presentes **substituem** os do
  protótipo (transform: só os componentes indicados). Valores inválidos
  avisam e mantêm o do protótipo.
- `<Use>` aninhado (um protótipo que instancia outro) funciona, com corte de
  ciclo e teto de profundidade (16).
- `viber analyze` reporta `prototypes: N definido(s), M instância(s)` e
  falha em `--strict` com `<Use>` de id desconhecido.

## Dedup de assets

Primitivas idênticas (mesma forma+dimensões; mesmo material) partilham
`Mesh`/`StandardMaterial` automaticamente — 3 paredes iguais = 1 mesh +
1 material, não 3+3. Exceção: `texture-tile-size` em Plane/Cuboid assa a
translation do mundo nas UVs, por isso esse caminho mantém mesh dedicada
(o material continua partilhado, o que mantém o padrão alinhado com as
ribbons das estradas).

## Diferenças para o plugin `<Composition>` do VibeGame

| Tema | VibeGame | Viber |
|------|----------|-------|
| Protótipos/instanciação | não existe | `<Prototype>`/`<Use>` em parse-time |
| Colisores por parte | compound Rapier por primitiva ✓ | igual, mas derivado da forma **exata** (incl. cylinder/capsule) e com scale |
| `body="none"` | bug: cai em `fixed` silenciosamente | honrado |
| Sub-pivots (`<Group>` dentro) | não | sim |
| Rotação das partes | radianos (root em graus) | `euler` graus **ou** `rotation` radianos — explícito por attr |
| `Pad` (SDF/feather) | sim | não — usar `<GroundDecal>` (fora da composition) ou `Plane`+`opacity` |
| Colocação | `place` no spawner | `place` (explícito) ou `seat` (bloco, como grupos) |
| LOD/draw calls | 1 draw call por parte | dedup de assets; sem LOD por parte (roadmap) |

`overlap-max` (attr de análise do VibeGame) é aceite e ignorado, para os
mundos migrarem sem warnings.
