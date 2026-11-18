# bevy_ext_gltf

GLB decoding for Bevy 0.20 with the compression/quantization extensions the
stock loader does not support:

- `EXT_meshopt_compression` — `ATTRIBUTES`, `TRIANGLES` and `INDICES` modes,
  with `NONE`/`OCTAHEDRAL`/`QUATERNION`/`EXPONENTIAL` filters.
- `KHR_mesh_quantization` — dequantizes `POSITION`/`NORMAL`/`TANGENT` from
  i8/u8/i16/u16 (normalized or not) into compact `f32` buffer views.
- `KHR_texture_basisu` — lifts the texture source out of the extension so the
  Bevy `gltf` loader finds it; KTX2/UASTC transcoding stays with Bevy's
  `basis-universal` feature.
- UASTC `RRR` — rewrites the channel layout to `RGB` to avoid Bevy's BC4 path.

```toml
[dependencies]
bevy_ext_gltf = "0.1"
```

```rust
use bevy::prelude::*;

let mut app = App::new();
bevy_ext_gltf::register_asset_source(&mut app, vec!["assets".into()]);
app.add_plugins(DefaultPlugins);
```

The reader must be registered **before** `AssetPlugin` is added. The crate is
runtime-agnostic: it wraps the default asset source and does not depend on a
game or an editor.

## Optional helpers

- `bounds` (default): `GlbBounds::read`/`read_bytes` — scene AABB from the GLB
  JSON, without a renderer.
- `textures` (default): CPU mipmaps and the world-tiled sampler for PNG/RGBA
  images.

## Limits

Only self-contained GLB files are decoded; `.gltf` with external buffers,
sparse accessors, Draco and multi-buffer GLBs are out of scope. ETC1S/BasisLZ
is not decoded by Bevy; the audit tools warn.

The tests use two small real GLBs in `tests/fixtures/` (meshopt +
quantization + KTX2/basisu) when `VAEL_ROOT` is unset, so the decoder path runs
in CI without the game checkout.

## Bevy version

Built against Bevy 0.20.1. The crate enables `bevy_asset`, `bevy_gltf`,
`bevy_image`, `bevy_log` and `bevy_render`; the host app owns the
renderer/window features.

## License

MIT OR Apache-2.0.
