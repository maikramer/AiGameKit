# World Border Plugin (context.md)

<!-- LLM:OVERVIEW -->

`<WorldBorder>` declares a soft circular border around the world origin. A player entity that crosses `radius` gets a countdown (`warn-seconds`, big screen-space numbers plus a world-space warning over their head); when it expires they are teleported to the nearest point `margin` meters inside the border, seated on the surface (BVH probe first, terrain sample fallback) with linear/angular velocity zeroed. Walking back inside during the countdown cancels it silently.

<!-- /LLM:OVERVIEW -->

## Layout

```
world-border/
├── context.md     # This file
├── index.ts       # Exports
├── plugin.ts      # WorldBorderPlugin (recipe + system + component + parser)
├── components.ts  # WorldBorder (radius, warnSeconds, margin, countdown state)
├── recipes.ts     # worldBorderRecipe (<WorldBorder>)
├── parser.ts      # worldBorderParser (XML attributes → component)
└── systems.ts     # WorldBorderSystem (countdown + teleport)
```

## Scope

- **In-scope**: circular border for `PlayerController` entities, countdown warning via FloatingText, surface-seated teleport with velocity reset.
- **Out-of-scope**: non-circular shapes, non-player entities, visual walls (use terrain/props for that).

## Dependencies

- **Internal**: transforms (`Transform`), player (`PlayerController`), physics (`Rigidbody`, `getBodyForEntity`, `getBodyYForFeetAt`, `GROUND_CONTACT_SKIN`), terrain (`getGroundHeight`), bvh (`getBvhSurfaceHeight`), floating-text (screen + world spawn).

## Teleport sequence (mirrors chrono/save-load)

Write `Transform` + `Rigidbody` SOA position, zero SOA velocity, then `body.setTranslation/setLinvel/setAngvel(0)/wakeUp()` — ECS is authoritative, Rapier follows the same frame.

## Example

```xml
<WorldBorder radius="600" warn-seconds="5" margin="24"></WorldBorder>
```

| Attribute      | Type   | Default | Notes                                             |
| -------------- | ------ | ------- | ------------------------------------------------- |
| `radius`       | number | `600`   | Circle around the world origin (m).               |
| `warn-seconds` | number | `5`     | Countdown before the teleport (s).                |
| `margin`       | number | `24`    | How far inside the border the player lands (m).   |
