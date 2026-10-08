# rerecast 0.4.0 — patch do Viber

Cópia de `rerecast` 0.4.0 (crates.io, MIT OR Apache-2.0) ligada por
`[patch.crates-io]` no `Viber/Cargo.toml`. Só `src/poly_mesh.rs` difere do
original; cada alteração está marcada com `Viber patch:`.

1. **Triangulação falhada já não aborta a navmesh.** O Recast C++ devolve os
   triângulos feitos até ali (`return -ntris`) e o `rcBuildPolyMesh` só loga um
   warning — um contorno dobrado sobre si próprio deixa um buraco pequeno. O
   port Rust transformava isso em `PolygonNavmeshError::InvalidContour`, que
   deitava fora a navmesh inteira. No `simple-rpg` o tile de 256 m do spawn
   falhava de forma determinística (qualquer deslocamento de grelha e qualquer
   `max_simplification_error`): nenhuma criatura tinha navmesh em toda a
   sessão.
2. **`len` das diagonais em `u32`.** O quadrado do comprimento era truncado com
   `as u16`: qualquer diagonal com mais de 255 células dava a volta e o ear
   clipping escolhia orelhas arbitrárias (triângulos compridos e finos).

Remover quando o upstream aceitar o equivalente (e apagar a entrada
`[patch.crates-io]`).

Port Bevy 0.20 (2026-09-25): `bevy_reflect` → `0.20.0-rc.2` e glam `0.32` →
`0.33.2` (a versão do `bevy_math` 0.20); dev-dependencies e `tests/` removidos
como nos outros forks (`vendor/FORKS_020.md`).
