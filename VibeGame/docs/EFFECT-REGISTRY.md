# 🎨 Effect Registry

Sistema de registro de efeitos de pós-processamento, baseado no padrão registry.

**Localização:** `src/plugins/postprocessing/`

## Arquivos

| Arquivo | Descrição |
|---------|-----------|
| `effect-registry.ts` | API de registro (`registerEffect`, `getEffectDefinitions`, `unregisterEffect`) |
| `builtin-effects.ts` | Efeitos builtin: bloom, SMAA, dithering, tonemapping |
| `systems.ts` | System que itera sobre o registry para aplicar efeitos |

## Como Funciona

O `PostprocessingSystem` (em `systems.ts`) usa `getEffectDefinitions()` para descobrir dinamicamente quais efeitos existem, em vez de ter lógica hardcoded para cada efeito. Isso significa que adicionar um novo efeito requer apenas:

1. Criar um component `bitecs`
2. Registrar um `EffectDefinition` via `registerEffect()`

## API

### `EffectDefinition`

```ts
interface EffectDefinition {
  readonly key: string;          // identificador único ('bloom', 'smaa', etc.)
  readonly component: Component; // componente bitecs que ativa o efeito
  create(state, entity): Effect; // cria instância do efeito (chamado uma vez)
  update?(state, entity, effect): boolean | void; // atualiza props por frame
  readonly position?: 'first' | 'last'; // ordem no pipeline
}
```

### Funções

```ts
registerEffect(definition: EffectDefinition): void
getEffectDefinitions(): readonly EffectDefinition[]
unregisterEffect(key: string): boolean
```

## Efeitos Builtin

Registrados automaticamente por `registerBuiltinEffects()`:

| Efeito | Component | Posição | Configurações |
|--------|-----------|---------|---------------|
| **SMAA** | `SMAA` | `first` | `preset` |
| **Bloom** | `Bloom` | — | `intensity`, `luminanceThreshold`, `mipmapBlur`, `radius`, `levels` |
| **Dithering** | `Dithering` | — | `colorBits`, `intensity`, `grayscale`, `scale`, `noise` |
| **Tonemapping** | `Tonemapping` | `last` | `mode`, `middleGrey`, `whitePoint`, `averageLuminance`, `adaptationRate` |

## Criando um Efeito Customizado

```ts
import { registerEffect, type EffectDefinition } from '../postprocessing/effect-registry';
import { MyEffect } from './my-effect-component';

registerEffect({
  key: 'my-effect',
  component: MyEffect,
  create(state, entity) {
    return new MyPostprocessingEffect({ intensity: MyEffect.intensity[entity] });
  },
  update(state, entity, effect) {
    effect.intensity = MyEffect.intensity[entity];
  },
});
```

O `PostprocessingSystem` automaticamente detectará e aplicará o novo efeito.
