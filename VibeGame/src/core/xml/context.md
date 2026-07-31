# XML Module

<!-- LLM:OVERVIEW -->

Declarative entity creation through XML parsing. Converts A-frame style XML into ECS entities with type-safe attribute parsing for vectors, colors, and other values. Before parse, `<Include src>` fragments expand recursively (browser fetch or headless disk load).
<!-- /LLM:OVERVIEW -->

## Purpose

- Parse XML strings/elements to ECS entities
- Support A-frame style declarative syntax
- Expand modular world fragments via `<Include src="…">`
- Handle attributes and nested structures
- Type-safe value parsing

## Layout

```
xml/
├── context.md   # This file
├── include.ts   # <Include src> expand (depth/cycle guards)
├── parser.ts    # Main XML parser
├── traverser.ts # DOM tree traversal
├── types.ts     # XML parsing types
├── values.ts    # Attribute value parsing
└── index.ts     # Module exports
```

## Scope

- **In-scope**: Include expand, XML to entity conversion, attribute parsing
- **Out-of-scope**: Component logic, rendering, CityGrid expand (see `plugins/city-layout`)

## Entry Points

- **include.ts**: `expandIncludes`, `unwrapIncludeFragment`, `createFetchIncludeLoader`
- **parser.ts**: XMLParser class for entity creation
- **traverser.ts**: Tree traversal utilities
- **values.ts**: Parse vectors, colors, numbers

## Dependencies

- **Internal**: ECS types; runtime calls `expandIncludes` before `parseWorldXml`
- **External**: DOM API / `fetch` (browser); headless injects a disk `load`

<!-- LLM:REFERENCE -->

## API Reference

### Include expand (`include.ts`)

Runs **before** Scene XML parsing (`GameRuntime` / headless). Replaces
`<Include src="…">` / `<include src="…">` (self-closing or paired) with fragment
text from `options.load(src)`.

| Contract  | Value                                                                                                                    |
| --------- | ------------------------------------------------------------------------------------------------------------------------ |
| Max depth | `MAX_INCLUDE_DEPTH` = **8**                                                                                              |
| Cycles    | Fail-fast if `src` already on stack                                                                                      |
| Wrappers  | `unwrapIncludeFragment` strips BOM, `<?xml?>`, outer `<Scene>` / `<World>` so files can be full scenes or bare fragments |
| Comments  | Tags inside `<!-- … -->` are ignored                                                                                     |

```typescript
import {
  expandIncludes,
  createFetchIncludeLoader,
  MAX_INCLUDE_DEPTH,
} from 'vibegame';

const xml = await expandIncludes(rawHtml, {
  load: createFetchIncludeLoader(), // browser: fetch(src)
  maxDepth: MAX_INCLUDE_DEPTH,
});
```

Headless / `vibegame analyze`: inject `load` that reads from `--public-dir`
(or entry dirname). Example layout: `examples/simple-rpg/public/world/`.

```html
<Scene canvas="#game-canvas">
  <Include src="/world/environment.xml"></Include>
  <Include src="/world/cities/discordia.xml"></Include>
  <Include src="/world/spawn/ring.xml"></Include>
</Scene>
```

### XMLParser

- `XMLParser.parse(xmlString: string): XMLParseResult` - Parse XML string into element tree

### Traversal Functions

- `traverseElements(element: ParsedElement, callback: (el: ParsedElement) => void): void` - Traverse element tree
- `findElements(element: ParsedElement, predicate: (el: ParsedElement) => boolean): ParsedElement[]` - Find matching elements

### XMLValueParser

- `XMLValueParser.parse(value: string): XMLValue` - Parse attribute values into appropriate types
  - Numbers: `"42"` → `42`
  - Booleans: `"true"` → `true`
  - Vectors: `"1 2 3"` → `[1, 2, 3]`
  - Hex colors: `"0xff0000"` → `16711680`
  - Strings: `"text"` → `"text"`

### Types

```typescript
interface ParsedElement {
  tagName: string;                       // Lowercase tag name
  attributes: Record<string, XMLValue>;  // Parsed attributes
  children: ParsedElement[];             // Child elements
}

type XMLValue = string | number | boolean | number[];

interface XMLParseResult {
  root: ParsedElement;                   // Root element
}
```

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Examples

### Basic XML Parsing

```typescript
import * as GAME from 'vibegame';

const xml = `
  <Scene>
    <GameObject pos="0 1 0" euler="0 45 0">
      <box size="1 1 1" color="#ff0000"></box>
      <rigidbody type="dynamic"></rigidbody>
    </GameObject>
  </Scene>
`;

const result = GAME.XMLParser.parse(xml);
// result.root.tagName === 'world'
// result.root.children[0].tagName === 'entity'
// result.root.children[0].attributes.pos === [0, 1, 0]
```

### Traversing Elements

```typescript
import * as GAME from 'vibegame';

GAME.traverseElements(result.root, (element) => {
  if (element.tagName === 'entity') {
    console.log('Found entity:', element.attributes);
  }
});
```

### Value Parsing

```typescript
import * as GAME from 'vibegame';

GAME.XMLValueParser.parse("42");           // 42
GAME.XMLValueParser.parse("true");         // true
GAME.XMLValueParser.parse("1 2 3");        // [1, 2, 3]
GAME.XMLValueParser.parse("0xff0000");     // 16711680
GAME.XMLValueParser.parse("hello world");  // "hello world"
```

### Canonical attribute parsers (plugins must reuse, not fork)

`XMLValueParser` pre-converts values (vectors → `{x,y,z}`, `#hex` colors →
numbers). When a plugin/parser needs a **typed read with a fallback**, use the
canonical helpers in this module instead of a local copy:

```typescript
import {
  parseNumberAttr, // (value, fallback) — number/string/boolean, NaN → fallback
  parseBoolAttr,   // (value, fallback) — true/false, 1/0, yes/no, on/off
  parseVec3Attr,   // (value, [fx,fy,fz]) — string/number/array/{x,y,z} → tuple
  parseColorValue, // (string) — handles the #hex → number → string round-trip
} from 'vibegame';

parseNumberAttr("0", 1);          // 0  (never falls back on explicit zero)
parseBoolAttr("false", true);     // false (honors explicit false)
parseVec3Attr("5", [0, 0, 0]);    // [5, 5, 5] (scalar broadcast)
parseColorValue("16737792");      // 0xff6600 (round-trip of #ff6600)
```

Color adapters receive `String(attrValue)`, so `#hex` arrives as a pure
decimal digit string — `parseColorValue` recovers the original number.

<!-- /LLM:EXAMPLES -->
