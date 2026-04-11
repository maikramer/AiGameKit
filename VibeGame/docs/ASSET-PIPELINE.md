# 🔧 GameAssets Pipeline (Python → VibeGame)

Integração entre o pipeline Python (GameAssets) e o motor TypeScript (VibeGame).

**Localização:** `src/plugins/scene-manifest/`

## Fluxo

```
GameAssets (Python)                    VibeGame (TypeScript)
─────────────────────                  ─────────────────────
gameassets generate                    SceneManifestPlugin
        │                                     │
        ▼                                     ▼
gameassets_manifest.json  ──────────►  loadSceneManifest()
        │                                     │
        │   (GLB + PBR + animações)           │
        └─────────────────────────────────────┘
```

## `gameassets_manifest.json`

Gerado pelo pipeline Python (`Shared/src/gamedev_shared/pipeline/manifest.py`):

```json
{
  "version": 1,
  "generated": "2026-04-08T10:00:00Z",
  "assets": {
    "cristal": {
      "model": "assets/cristal.glb",
      "pbr_textures": [
        "assets/cristal_albedo.png",
        "assets/cristal_normal.png",
        "assets/cristal_roughness.png"
      ],
      "animations": ["idle", "float"],
      "bounds": { "min": [-1, -1, -1], "max": [1, 2, 1], "size": [2, 3, 2] },
      "source_pipeline": "tripo3d",
      "position": [0, 0, 0],
      "scale": [1, 1, 1]
    }
  }
}
```

## `SceneManifestEntry`

```ts
interface SceneManifestEntry {
  model?: string;              // caminho do GLB
  textures?: string[];         // caminhos legacy
  pbr_textures?: string[];     // PBR set (albedo, normal, roughness, metallic, ao)
  animations?: string[];       // clips de animação disponíveis
  audio?: string;              // áudio associado
  bounds?: {                   // bounding box calculada pelo pipeline
    min?: number[];
    max?: number[];
    size?: number[];
  };
  source_pipeline?: string;    // pipeline geradora ('tripo3d', 'meshy', etc.)
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: [number, number, number];
}
```

## Uso no VibeGame

```ts
import { loadSceneManifest, createSceneManifestConfig } from 'vibegame';

// Carrega o manifest e spawna todas as entidades
const manifest = await loadSceneManifest(state, '/gameassets_manifest.json');

// Ou com config customizado
const cfg = createSceneManifestConfig({
  manifestUrl: '/assets/custom_manifest.json',
  basePath: '/assets/',
});
const manifest = await loadSceneManifest(state, cfg.manifestUrl, cfg.basePath);
```

## `createSceneManifestConfig()`

Helper para criar config tipado:

```ts
function createSceneManifestConfig(config?: {
  manifestUrl?: string;  // default: '/gameassets_manifest.json'
  basePath?: string;     // default: '/'
}): Required<SceneManifestPluginConfig>
```

## Integração Texture2D → VibeGame

Texturas procedurais geradas pelo Texture2D podem ser aplicadas via `TextureRecipe` (plugin rendering):

```html
<GameObject transform renderer
  textureRecipe="url: textures/marble.png; repeatMode: 1; repeatX: 8; repeatY: 8; channel: 0">
</GameObject>
```

Veja [`docs/PLUGINS.md`](PLUGINS.md) para detalhes do TextureRecipe.

## Campos Python → TS

O contrato entre o pipeline Python e TypeScript é documentado em `shared/types.ts`. Os campos `pbr_textures`, `bounds`, `source_pipeline` e `generated` são gerados pelo pipeline Python e consumidos diretamente pelo `SceneManifestEntry`.

Veja [`docs/SHARED.md`](SHARED.md) para os tipos compartilhados.
