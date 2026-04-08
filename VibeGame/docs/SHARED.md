# 📦 Módulo `shared/`

Módulo de tipos, helpers matemáticos e schemas de validação compartilhados entre plugins do VibeGame.

**Localização:** `src/shared/`

## Arquivos

| Arquivo | Descrição |
|---------|-----------|
| `types.ts` | Interfaces e type aliases compartilhados |
| `math.ts` | Helpers matemáticos (vec3, vec2, aabb, clamp, etc.) |
| `validation.ts` | Schemas Zod reutilizáveis para parsing de XML/JSON |
| `index.ts` | Re-export público |

## Tipos Compartilhados (`types.ts`)

```ts
interface Vector3Like { x: number; y: number; z: number }
interface Vector2Like { x: number; y: number }
interface ColorLike   { r: number; g: number; b: number; a?: number }
interface AABB        { min: Vector3Like; max: Vector3Like }
interface QuaternionLike { x: number; y: number; z: number; w: number }
type Matrix4 = number[]
type EntityId = number
type Noop = () => void
interface Disposable { dispose(): void }
interface EntityRef { readonly name: string }
interface TimingInfo { delta: number; elapsed: number; fixedDelta: number }
```

**Uso principal:** comunicação entre Python e TypeScript (pipeline GameAssets → VibeGame). Por exemplo, `AABB` representa bounding boxes calculadas pelo pipeline Python e consumidas pelo `scene-manifest`.

## Helpers Matemáticos (`math.ts`)

### `vec3`
Operações com vetores 3D (objetos `{x, y, z}`, não instâncias THREE.Vector3):

```ts
vec3.create(0, 1, 0)      // {x:0, y:1, z:0}
vec3.add(a, b)             // soma
vec3.sub(a, b)             // subtração
vec3.scale(v, s)           // escala escalar
vec3.multiply(a, b)        // multiplicação componente-a-componente
vec3.dot(a, b)             // produto escalar
vec3.cross(a, b)           // produto vetorial
vec3.length(v)             // magnitude
vec3.normalize(v)          // normalização
vec3.distance(a, b)        // distância euclidiana
vec3.lerp(a, b, t)         // interpolação linear
vec3.equals(a, b, eps)     // comparação com epsilon (default 1e-6)
```

### `vec2`
Operações com vetores 2D (`create`, `clone`, `add`, `sub`, `scale`, `dot`, `length`, `lerp`).

### `aabb`
AABB (Axis-Aligned Bounding Box):

```ts
aabb.create(minX, minY, minZ, maxX, maxY, maxZ)
aabb.contains(box, point)   // teste de ponto
aabb.intersects(a, b)       // teste de interseção
```

### Utilitários gerais

```ts
clamp(value, min, max)
clamp01(value)                    // clamp para [0, 1]
degToRad(deg) / radToDeg(rad)
mapRange(value, inMin, inMax, outMin, outMax)
smoothstep(edge0, edge1, x)      // interpolação suave Hermite
```

## Schemas de Validação (`validation.ts`)

Schemas Zod para parsing flexível de atributos XML/JSON:

| Schema | Aceita | Saída |
|--------|--------|-------|
| `vector3Schema` | `"1 2 3"`, `{x:1,y:2,z:3}`, `1`, `"1.5"` | `{x, y, z}` |
| `vector2Schema` | `"1 2"`, `{x:1,y:2}`, `1`, `"1.5"` | `{x, y}` |
| `colorSchema` | `"#ff0000"`, `"0xff0000"`, `0xff0000`, `255` | `number` |
| `shapeSchema` | `"box"`, `"sphere"` | string enum |
| `bodyTypeSchema` | `"static"`, `"dynamic"`, `"kinematic"` | string enum |

## Integração Python ↔ TypeScript

O módulo `shared/` serve como contrato entre o pipeline Python (GameAssets) e o motor TypeScript. Os tipos em `types.ts` espelham as estruturas que o pipeline gera no `gameassets_manifest.json`:

- **AABB** → `bounds` no manifest
- **Vector3Like** → `position`, `rotation`, `scale`
- **SceneManifestEntry** → campos `pbr_textures`, `source_pipeline`, `generated`

Veja também [`docs/ASSET-PIPELINE.md`](ASSET-PIPELINE.md).
