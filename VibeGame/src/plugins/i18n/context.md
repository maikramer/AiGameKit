# I18n Plugin (context.md)

<!-- LLM:OVERVIEW -->

Opt-in internationalization plugin. Maps translation keys to localized strings and writes the resolved text into HUD panels. **Requires `HudPlugin` to be registered first** (the plugin warns at `initialize` if `hud-panel` is absent and becomes a no-op, because resolved strings are written through `internString` / `getStringAt` from `hud/context` into `HudPanel.textIndex`). Two recipes: `<I18nText key="...">` (merge recipe, attaches the `i18n-text` component to an existing HUD entity) and `<I18n auto-engine-defaults="true">` (loads the built-in EN dictionary). Locale is **not** auto-detected: it defaults to `'en'` and must be set explicitly with `setLocale(state, lang)`. Dictionaries are registered per locale with `loadDictionary(state, lang, dict)`; missing keys fall back to the key text itself and log a warning in non-production builds.

<!-- /LLM:OVERVIEW -->

## Layout

```
i18n/
├── context.md         # This file
├── index.ts           # Public re-exports
├── plugin.ts          # I18nPlugin (systems + recipes + components + adapters)
├── components.ts      # I18nText, I18nConfig
├── systems.ts         # I18nAutoDefaultsSystem (setup), I18nResolveSystem (simulation)
├── recipes.ts         # i18nTextRecipe (merge), i18nConfigRecipe
├── utils.ts           # setLocale / getLocale / loadDictionary / t / resolveI18nKey
└── engine-defaults.ts # ENGINE_DEFAULT_EN_DICTIONARY + loadEngineDefaultDictionary
```

## Scope

- **In-scope**: Per-state locale storage, dictionary registration, key resolution with `{param}` interpolation, HUD text binding, built-in EN engine strings.
- **Out-of-scope**: Browser locale auto-detection (call `setLocale(state, navigator.language)` yourself if you want it), RTL layout, pluralization rules, loading dictionaries from JSON files (call `loadDictionary` with the parsed object).

## Entry Points

- **plugin.ts**: `I18nPlugin` (systems `I18nAutoDefaultsSystem`, `I18nResolveSystem`; recipes `I18nText`, `I18n`; components `i18n-text`, `i18n-config`).
- **utils.ts**: `setLocale`, `getLocale`, `loadDictionary`, `t`, `resolveI18nKey`.
- **engine-defaults.ts**: `ENGINE_DEFAULT_LOCALE` (`'en'`), `ENGINE_DEFAULT_EN_DICTIONARY`, `loadEngineDefaultDictionary`.
- **index.ts**: Re-exports all of the above plus recipes and systems.

## Dependencies

- **Internal**: Core ECS, `hud/context` (`internString`, `getStringAt`) and `hud/components` (`HudPanel`). The HudPlugin must be registered before I18nPlugin.
- **External**: None.
<!-- LLM:REFERENCE -->

### Components

#### I18nText

- `keyIndex`: ui32 (index into the HUD interned-string table for the translation key, set via the `key` adapter).
- `resolved`: ui8 (0 = pending, 1 = already resolved into `HudPanel.textIndex` this run).

#### I18nConfig

- `autoEngineDefaults`: ui8 (1 = load `ENGINE_DEFAULT_EN_DICTIONARY` during setup).
- `applied`: ui8 (1 = the auto-defaults load already ran for this entity).

### Systems (order in the plugin)

1. **I18nAutoDefaultsSystem** (`setup`) - for each `I18nConfig` entity with `applied === 0`, if `autoEngineDefaults === 1` it calls `loadEngineDefaultDictionary(state)`, then marks `applied = 1`.
2. **I18nResolveSystem** (`simulation`) - for each entity that has both `I18nText` and `HudPanel` and `resolved === 0`, reads the key string via `getStringAt(state, keyIndex)`, translates it with `t(state, key)`, writes the result back into `HudPanel.textIndex` via `internString`, and sets `resolved = 1`. Resolution happens once per entity; toggle `resolved` back to 0 to re-resolve after a locale change.

### Recipes

- **`<I18nText key="...">`** - `merge: true`, adds the `i18n-text` component onto an existing entity (typically a `<HudPanel>`). The `key` adapter interns the string and stores its index in `I18nText.keyIndex`.
- **`<I18n auto-engine-defaults="true">`** - creates an `i18n-config` entity. The `auto-engine-defaults` adapter maps the string `"true"` to `1`.

### Locale handling

Locale lives in a `WeakMap<State, string>` (see `utils.ts`). `getLocale(state)` returns the stored value or `'en'` when nothing has been set. `setLocale(state, lang)` overwrites it. There is **no automatic detection** from `navigator.language` or any other source; wire that up in app code if you need it. After changing locale you must reset `I18nText.resolved` back to `0` on every HUD entity you want re-translated, because `I18nResolveSystem` skips already-resolved entities.

### Dictionary loading and lookup

`loadDictionary(state, lang, dict)` merges entries into a per-state `Map<string, string>` keyed by `${lang}:${k}`. Multiple calls for the same lang add to (and overwrite) the same map. `t(state, key, params?)`:

1. Reads `lang = getLocale(state)`.
2. Looks up `${lang}:${key}`. If absent, returns `key` unchanged.
3. In non-production (`process.env.NODE_ENV !== 'production'`) logs `[i18n] missing key "..." for locale "..."` as a warning.
4. Replaces every `{param}` token with the matching value from `params`.

The built-in `ENGINE_DEFAULT_EN_DICTIONARY` covers HUD labels (`hud.health`, `hud.xp`, `hud.gold`, ...), control hints, banners, menu tabs, skill names, and modal strings, all in EN under locale `'en'`.

### Fallback behavior

- Missing locale for a key: try `${lang}:${key}`, then return the raw key.
- Missing param in `params`: the `{param}` token is left in the output verbatim.
- Missing HudPlugin: the plugin logs a warning and `I18nResolveSystem` matches no entities (it requires both `I18nText` and `HudPanel`).

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

```html
<!-- Load the engine EN strings once at scene start. -->
<I18n auto-engine-defaults="true"></I18n>

<!-- A HUD panel whose text is driven by a translation key. -->
<HudPanel id="health-label">
  <I18nText key="hud.health"></I18nText>
</HudPanel>
```

```ts
import { setLocale, loadDictionary, t, I18nText } from 'vibegame';

// Register a Portuguese dictionary.
loadDictionary(state, 'pt', {
  'hud.health': 'Vida',
  'hud.xp': 'XP',
});

// Switch locale (then flip resolved flags so the HUD re-resolves).
setLocale(state, 'pt');
for (const eid of i18nQuery(state.world)) I18nText.resolved[eid] = 0;

// Translate ad-hoc, with interpolation.
t(state, 'modal.skillPoints', { n: '3' }); // -> "3 skill points" (or PT equivalent if loaded)
```

<!-- /LLM:EXAMPLES -->

## Known Limitations

- No locale auto-detection. Default is always `'en'`; call `setLocale` yourself (for example with `navigator.language.split('-')[0]`).
- Resolution runs once. After a locale switch you must reset `I18nText.resolved` to `0` on the entities you want refreshed.
- Keys are case-sensitive and stored verbatim. There is no pluralization, gender, or ICU MessageFormat support.
- The plugin depends on HudPlugin for rendering. Without it the resolve system is inert and translated strings are never shown.
