# Composition

Plugin que permite compor **uma única entidade ECS** a partir de múltiplas
primitivas (Box/Sphere/Cylinder/Plane/Pad) aninhadas como filhos XML — produzindo
1 entidade + 1 `THREE.Group` + N meshes + 1 `RigidBody` Rapier com **N colisores
compostos** (um por primitiva). Substitui o padrão de N `<GameObject>` separados
para estruturas semânticas (cabanas, muros, plataformas).

## Sintaxe

```html
<Composition pos="16 0 8" place="at: 16 8" body="fixed" collider="auto">
  <Box pos="0 0.1 0" size="6.4 0.2 6.4" color="#6b4a2b"></Box>
  <Sphere pos="0 3 0" size="0.5" color="#ffcc77"></Sphere>
  <Cylinder pos="2 1 2" size="0.3 0.3 2" color="#8a5a30"></Cylinder>
  <Plane pos="0 0.01 0" size="6.4 6.4" color="#333"></Plane>
  <PointLight pos="0 2.6 0" color="#ffcc77" intensity="10" distance="8"></PointLight>
</Composition>
```

### Atributos do `<Composition>`

| Atributo  | Default   | Descrição                                                       |
| --------- | --------- | --------------------------------------------------------------- |
| `pos`     | `0 0 0`   | Posição world (X Y Z), mapeado para `Transform` + `Rigidbody`.  |
| `place`   | (nenhum)  | `at: x z` — posiciona no terreno (requer SpawnerPlugin/Terrain).|
| `body`    | `fixed`   | `fixed` \| `dynamic` \| `kinematic` \| `kinematic-position`.    |
| `collider`| `auto`    | `auto` (composto por primitiva) \| `none` (só visual).          |

### Atributos das primitivas (`<Box>` etc.)

| Atributo              | Formato                | Notas                                                        |
| --------------------- | ---------------------- | ------------------------------------------------------------ |
| `pos`                 | `x y z`                | Posição **local** relativa ao Composition.                   |
| `rotation`            | `rx ry rz`             | **Radianos** (convenção do motor).                           |
| `size`                | ver abaixo             | Dimensões da primitiva.                                      |
| `color`               | `#rrggbb` / `#rgb`     | Cor do `MeshStandardMaterial` (usada se não houver textura). |
| `texture-url`         | caminho URL            | Textura albedo/seamless (sRGB). Alias: `map-url`, `texture`. |
| `texture-repeat`      | `rx ry` ou `r`         | Repetição UV (default `1 1`). `2 1` repete 2x em X.          |
| `texture-rotation`    | radianos               | Rotação UV (default `0`).                                    |
| `normal-map-url`      | caminho URL            | Normal map PBR (espaço linear). Alias: `normal-url`.         |
| `roughness-map-url`   | caminho URL            | Roughness map PBR (espaço linear). Alias: `roughness-url`.   |
| `roughness`           | `0..1`                 | Roughness fixa (default `1`). Ignorado se houver roughness-map. |
| `metalness`           | `0..1`                 | Metalness fixa (default `0`).                                |

As texturas são carregadas via `TextureLoader` com cache partilhado por URL;
cada primitiva pode ter `texture-repeat`/`texture-rotation` independente (a
imagem GPU é partilhada via `texture.clone()`). Se a URL falhar, cai para cor
flat. Exemplo de parede texturizada com relevo:

```html
<Box
  pos="0 1.4 -2.4"
  size="5 2.6 0.3"
  texture-url="/assets/textures/wall_plaster.png"
  texture-repeat="2 1"
  normal-map-url="/assets/textures/wall_plaster_normal.png"
></Box>
```

Semântica de `size` por tipo:

- **Box**: `largura altura profundidade`
- **Sphere**: `raio` (1 valor) ou `raio raio raio` (usa o primeiro como raio)
- **Cylinder**: `raioTopo raioBase altura`
- **Plane**: `largura altura` (double-sided; colisor = slab fino)
- **Pad**: `largura profundidade` (2 valores, plano X×Z) ou `largura _ profundidade`

## Pad — decal de chão com bordas seamless

`<Pad>` é um plano deitado no XZ (normal +Y) pensado para calçadas, praças e
estradas sobre terreno: em vez de empilhar Boxes com `opacity` crescente, o
material recebe um **alphaMap procedural** (SDF de retângulo arredondado) que
desvanece a borda para o terreno sem degraus retangulares.

| Atributo        | Default | Descrição                                                        |
| --------------- | ------- | ---------------------------------------------------------------- |
| `edge-feather`  | `0.8`   | Metros de fade de alpha da borda para dentro (`0` = borda dura). Aceita 1 valor (uniforme), 2 (`"fx fz"`) ou 4 (`"w e n s"` = −x +x −z +z) — lados com `0` ficam sólidos até à orla (junções). Per-side ignora `corner-radius`. |
| `corner-radius` | `0`     | Raio em metros dos cantos arredondados.                          |
| `edge-noise`    | `0`     | Amplitude em metros de ruído que corrói a borda para DENTRO (calçada gasta). Determinístico (seed = posição). |
| `texture-scale` | —       | Metros de mundo por tile de textura; deriva `texture-repeat` do tamanho do pad (precedência sobre `texture-repeat`). Também suportado em `Plane`. |

```html
<!-- Praça com cantos redondos + estrada, seamless com a relva -->
<Composition place="at: 0 0" body="fixed" collider="none">
  <Pad pos="0 0.03 0" size="16 16" corner-radius="5" edge-feather="2.2" edge-noise="0.55"
       texture-url="/assets/textures/cobblestone.png" texture-repeat="12 12"></Pad>
  <Pad pos="0 0.025 15" size="5.4 18" edge-feather="1.1" edge-noise="0.45"
       texture-url="/assets/textures/cobblestone.png" texture-repeat="4 13.5"></Pad>
</Composition>
```

Notas:

- **Coerência de escala**: usar `texture-scale` (m/tile) em pads vizinhos em
  vez de `texture-repeat` manual — a densidade fica idêntica automaticamente e
  a textura nunca é espremida por pads estreitos.
- **Junções sólidas**: para ligar uma estrada a uma praça sem costura
  translúcida, estende-se o pad da estrada até DENTRO do núcleo opaco da praça
  e zera-se o feather desse lado (`edge-feather="1.1 1.1 0 1.1"`).
- O feather consome largura útil dos dois lados: núcleo opaco =
  `largura − 2×(edge-feather + ~edge-noise/2)`. Estradas estreitas precisam de
  feather menor.
- **Trap XML**: o XMLValueParser converte `"a b"`/`"a b c d"` em objetos
  `{x,y}`/`{x,y,z,w}` ANTES do parser da primitiva — qualquer atributo
  multi-valor novo tem de aceitar essa forma (foi o bug que deixava
  `texture-repeat` sempre em `[1,1]` via XML).
- O alphaMap é gerado por `computePadAlphaData` (puro, testável sem GPU) e não
  usa `onBeforeCompile` — sobrevive ao patch de CSM (`setupCsmMaterial`).
- Pad não projeta sombra (`castShadow=false`), recebe sombra, usa
  `polygonOffset` e `depthWrite=false`; com `edge-feather="0"` e sem
  noise/radius fica opaco (sem alphaMap).

## Filhos não-primitivos

Tags com recipe registado (ex.: `PointLight`, `AudioSource`) tornam-se
**entidades irmãs** com `Parent` = Composition, pelo que o seu `Transform` local
é relativo à composição (a luz em `pos="0 2.6 0"` fica dentro do telhado). Não
são merged no pai (mesmo recipes `merge: true`), para preservar o offset local.

## Gotchas

- **Colisores compostos**: o componente `Collider` é SOA (1 linha por entidade),
  pelo que N colisores são criados diretamente via `world.createCollider(desc,
  body)` no `CompositionColliderSystem` (grupo `fixed`, depois de
  `PhysicsInitializationSystem`). A entidade tem `Rigidbody` mas **não** tem
  `Collider`.
- **place**: o parser de Composition replica a lógica de placement do spawner
  (`PlacePending` + `PlacementSpec`) — o `entityParser` do spawner só atua em
  `<GameObject>`, por isso o Composition trata `place` internamente.
- **Escala da entidade** é aplicada ao tamanho/posição dos colisores (meshes
  herdam via `Group.scale`); Composition típico usa escala 1.
- **Two-phase build**: meshes no grupo `setup`, colisores no `fixed` (após o
  body existir). Sistodos retentam no tick seguinte se o body/scene ainda não
  estiver pronto (placement pending).
