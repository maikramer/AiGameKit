# Composition

Plugin que permite compor **uma única entidade ECS** a partir de múltiplas
primitivas (Box/Sphere/Cylinder/Plane) aninhadas como filhos XML — produzindo
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

| Atributo   | Formato                | Notas                                           |
| ---------- | ---------------------- | ----------------------------------------------- |
| `pos`      | `x y z`                | Posição **local** relativa ao Composition.      |
| `rotation` | `rx ry rz`             | **Radianos** (convenção do motor).              |
| `size`     | ver abaixo             | Dimensões da primitiva.                         |
| `color`    | `#rrggbb` / `#rgb`     | Cor do `MeshStandardMaterial`.                  |

Semântica de `size` por tipo:

- **Box**: `largura altura profundidade`
- **Sphere**: `raio` (1 valor) ou `raio raio raio` (usa o primeiro como raio)
- **Cylinder**: `raioTopo raioBase altura`
- **Plane**: `largura altura` (double-sided; colisor = slab fino)

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
