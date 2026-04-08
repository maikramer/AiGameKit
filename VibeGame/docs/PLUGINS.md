# 🔌 Lista de Plugins

Todos os plugins registrados em `DefaultPlugins` (`src/plugins/defaults.ts`).

## Core

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `transforms` | `transforms/` | Posição, rotação e escala 3D (componente WorldTransform) |
| `physics` | `physics/` | Física com Rapier (corpos, colliders, sensors) |
| `rendering` | `rendering/` | Three.js renderer, câmeras, materiais, texturas |
| `input` | `input/` | Teclado, mouse e gamepad |
| `startup` | `startup/` | Execução deferida pós-inicialização |

## Câmera

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `orbit-camera` | `orbit-camera/` | Câmera orbital com zoom e rotação |
| `follow-camera` | `follow-camera/` | Câmera em terceira pessoa (segue jogador) |

## Jogador

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `player` | `player/` | Movimento, pulo e controle de personagem |
| `gltf-anim` | `gltf-anim/` | Animação de modelos GLTF (clips de animação) |

## Modelo 3D

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `gltf-xml` | `gltf-xml/` | Carregamento declarativo de modelos GLB/GLTF via XML |
| `text-3d` | `text-3d/` | Integração de modelos GLB gerados pelo Text3D (Hunyuan) |

### text-3d

Diferente do plugin `text` (que usa troika-three-text para texto 2D em 3D), o `text-3d` carrega **modelos 3D geométricos** gerados pela pipeline Text3D (Hunyuan3D).

```html
<world canvas="#canvas">
  <text3d text3dModel="url: models/hello.glb" scale="2" tint="0xff0000" pos="0 1 0"></text3d>
</world>
```

**Componentes:** `Text3dModel` (url, pending, scale, tint)
**Systems:** `Text3dLoadSystem` (carrega GLB), `Text3dCleanupSystem` (limpa entidades removidas)
**Recipe:** `text3d` (transform + text3dModel)

## Ambiente

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `sky` | `sky/` | Skybox com texturas equirectangulares (PMREM/IBL) |
| `fog` | `fog/` | Neblina volumétrica (exponential, linear, height-falloff) |
| `water` | `water/` | Água com física, nado e reflexos |

### sky

Carrega texturas equirectangulares (2:1 PNG/JPG/HDR) como background e/ou iluminação IBL (Image-Based Lighting) via PMREM.

```html
<world canvas="#canvas">
  <sky url="textures/sky.hdr" rotationDeg="90" setBackground="1"></sky>
</world>
```

**Componentes:** `Sky` (urlIndex, rotationDeg, setBackground, loaded)
**Systems:** `SkySystem` (carrega textura, aplica PMREM, seta environment/background)

## Visual

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `postprocessing` | `postprocessing/` | Efeitos pós-processamento (bloom, SMAA, dithering, tonemapping) |
| `sprite` | `sprite/` | Sprites 2D |
| `line` | `line/` | Linhas 3D |
| `terrain` | `terrain/` | Terreno procedural com heightmaps |

Veja [`docs/EFFECT-REGISTRY.md`](EFFECT-REGISTRY.md) para detalhes do sistema de efeitos.

## Lógica

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `animation` | `animation/` | Sistema genérico de animação |
| `tweening` | `tweening/` | Interpolações suaves com easing |
| `spawner` | `spawner/` | Spawn de entidades |
| `respawn` | `respawn/` | Respawn de entidades |
| `lod` | `lod/` | Level of Detail (near/far) |
| `audio` | `audio/` | Áudio espacial |
| `debug` | `debug/` | Debug overlays (wireframes, etc.) |

## Pipeline

| Plugin | Pasta | Descrição |
|--------|-------|-----------|
| `scene-manifest` | `scene-manifest/` | Integração com o pipeline GameAssets (manifest JSON) |

Veja [`docs/ASSET-PIPELINE.md`](ASSET-PIPELINE.md) para detalhes.

## Texture Recipe

O plugin `rendering` inclui o sistema `TextureRecipe` que integra texturas procedurais do Texture2D:

```html
<entity transform renderer
  textureRecipe="url: textures/wood.png; repeatMode: 1; repeatX: 4; repeatY: 4; channel: 0">
</entity>
```

**Canais:** 0=map (diffuse), 1=normalMap, 2=roughnessMap, 3=metalnessMap

Veja a [arquitetura de plugins](../src/plugins/README.md) para detalhes de como criar novos plugins.
