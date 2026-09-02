//! Chunk mesh generation from a heightfield — pure geometry, no Bevy ECS.
//!
//! Ported from the VibeGame terrain plugin (`chunk-geometry.ts`) and the
//! `bevy_mesh_terrain` crate (MIT, ethereumdegen), with the known upstream
//! bugs fixed: borders are sealed with vertical **skirts** instead of the
//! broken neighbor-stitching ("THIS IS BUSTED" in `compute_stitch_data`), and
//! **frontier normals** sample the shared heightfield with a constant
//! terrain-wide epsilon so lighting is seamless across chunks and LODs.
//!
//! # Geometry contract
//!
//! * Top-grid vertex XZ are **relative to the chunk center**
//!   (`origin + size/2`); the chunk entity keeps its `Transform` translation
//!   at `(origin.x + size/2, 0, origin.z + size/2)`. Y stays **absolute**
//!   (meters) so vertical placement is baked into the vertices.
//! * Every top-grid normal comes from [`HeightField::sample_normal`] with
//!   [`ChunkMeshParams::normal_epsilon`] — the same terrain-wide constant on
//!   both sides of a chunk border yields identical shared-vertex normals, so
//!   there is no lighting seam between chunks or LOD levels.
//! * UVs are world-space (`world / tile`), keeping texel density constant
//!   across chunks and LODs.
//! * Skirt walls duplicate the 4 border rows/columns and drop them to the
//!   NEIGHBORING surface (`min` of two outside samples − 6 cm, capped), so on
//!   flat borders the skirt is a 6 cm sliver and over road trenches it floors
//!   out naturally — no fixed-depth hanging curtain. Normals point outward.
//! * Collider meshes ([`build_chunk_collider`]) use **absolute world**
//!   positions (unlike render meshes) and are consumed by the Phase 3 physics
//!   integration (avian).

use bevy::math::Vec3;

/// Read-only height queries used by mesh/collider building.
///
/// Implemented by [`crate::terrain::sampler::HeightSampler`]. All coordinates
/// are world-space meters; heights are meters (`normalized * max_height`).
pub trait HeightField {
    /// Terrain height at a world XZ position (smoothed per the sampler mode).
    fn sample(&self, world_x: f32, world_z: f32) -> f32;
    /// Surface normal via central differences with the given world epsilon.
    /// Callers pass a terrain-wide constant epsilon (not per-chunk/per-LOD)
    /// so normals agree on shared chunk borders.
    fn sample_normal(&self, world_x: f32, world_z: f32, epsilon: f32) -> Vec3;
    /// Peak height of the heightfield (meters).
    fn max_height(&self) -> f32;
}

/// Vertex/index buffers for one terrain chunk mesh.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ChunkMeshData {
    /// Vertex positions, world-origin relative to the chunk entity
    /// (x/z relative to the chunk center, y absolute meters).
    pub positions: Vec<[f32; 3]>,
    /// Smooth normals.
    pub normals: Vec<[f32; 3]>,
    /// UVs (world-space tiled, see [`build_chunk_mesh`]).
    pub uvs: Vec<[f32; 2]>,
    /// RGBA vertex colors from the height/slope tint.
    pub colors: Vec<[f32; 4]>,
    /// Triangle indices.
    pub indices: Vec<u32>,
}

/// Low-poly heightfield collision mesh for one chunk (Phase 3 physics ready).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TerrainColliderData {
    /// Vertex positions (world XZ, absolute Y meters).
    pub positions: Vec<[f32; 3]>,
    /// Triangle indices.
    pub indices: Vec<u32>,
}

/// Height/slope banding parameters for [`build_chunk_mesh`] (the render-side
/// subset of [`crate::terrain::spec::TerrainTint`]).
#[derive(Debug, Clone, PartialEq)]
pub struct TintParams {
    pub base_color: [f32; 4],
    pub color_low: [f32; 4],
    pub color_mid: [f32; 4],
    pub color_high: [f32; 4],
    pub color_rock: [f32; 4],
    pub snow_height: f32,
    pub slope_threshold: f32,
    pub slope_softness: f32,
    /// `0` disables the banding entirely: every vertex gets `base_color`, so
    /// the surface is the material texture times the authored base colour and
    /// nothing else.
    pub height_blend_strength: f32,
}

impl Default for TintParams {
    fn default() -> Self {
        Self {
            base_color: [1.0; 4],
            color_low: [1.0; 4],
            color_mid: [1.0; 4],
            color_high: [1.0; 4],
            color_rock: [1.0; 4],
            snow_height: 1.0,
            slope_threshold: 1.0,
            slope_softness: 0.05,
            height_blend_strength: 0.0,
        }
    }
}

impl From<&crate::terrain::spec::TerrainTint> for TintParams {
    fn from(t: &crate::terrain::spec::TerrainTint) -> Self {
        // Linear, not sRGB. `Mesh::ATTRIBUTE_COLOR` is consumed by the PBR
        // shader as linear RGBA, so handing it sRGB components renders every
        // band far too bright (sRGB 0.29 read as linear is ~4x the intended
        // 0.068) — which is why the tint used to wash out to near-white.
        let conv = |c: bevy::color::Color| {
            let l = c.to_linear();
            [l.red, l.green, l.blue, l.alpha]
        };
        Self {
            base_color: conv(t.base_color),
            color_low: conv(t.color_low),
            color_mid: conv(t.color_mid),
            color_high: conv(t.color_high),
            color_rock: conv(t.color_rock),
            snow_height: t.snow_height,
            slope_threshold: t.slope_threshold,
            slope_softness: t.slope_softness,
            height_blend_strength: t.height_blend_strength,
        }
    }
}

/// Options for [`build_chunk_mesh`].
#[derive(Debug, Clone)]
pub struct ChunkMeshParams {
    /// World XZ of the chunk's minimum corner (meters).
    pub origin: Vec3,
    /// Chunk edge length on X and Z (meters). Must be an exact multiple of
    /// [`ChunkMeshParams::lod_step`] or the build returns `Ok(None)`.
    pub size: f32,
    /// Grid step over the heightfield in meters: `1 << lod`. `1` = full
    /// resolution. Must be constant per LOD level across the whole terrain so
    /// chunk borders line up.
    pub lod_step: usize,
    /// Vertical skirt depth in meters (0 disables skirts).
    pub skirt_depth: f32,
    /// World-space epsilon (meters) for frontier normals. Must be **identical
    /// for every chunk and LOD of a terrain**: shared border vertices then
    /// receive exactly the same normal on both sides and lighting has no seam.
    /// A typical value is the heightmap grid spacing (`world_size / width`).
    pub normal_epsilon: f32,
    /// Texture tile size in meters.
    ///
    /// * `> 0` — used as-is: `uv = world / tile`.
    /// * `<= 0` — auto: resolved through
    ///   [`auto_texture_tile_size`](`world_size`, `levels`). When the auto
    ///   rule cannot resolve (`levels == 0` or non-positive `world_size`) the
    ///   UVs fall back to plain world meters (scale 1).
    pub texture_tile_size: f32,
    /// Number of LOD levels of the parent terrain (e.g.
    /// [`crate::terrain::spec::TerrainSpec::levels`]). Only consumed when
    /// `texture_tile_size <= 0` to resolve the auto tile size; keep it
    /// constant across the terrain for deterministic UVs. `0` disables the
    /// auto resolution.
    pub levels: u8,
    /// Terrain-wide world size, used by the auto tile-size rule.
    pub world_size: f32,
    /// Height/slope banding folded into the vertex colours. Keep it identical
    /// for every chunk and LOD of a terrain or neighbouring chunks disagree at
    /// their shared border.
    pub tint: TintParams,
}

/// Auto texture tile size: keeps texel density constant between LODs and
/// continuous across chunks (`world_size / 2^(levels-1) / 32`), ported from
/// VibeGame's `textureTileSize = 0` auto rule.
pub fn auto_texture_tile_size(world_size: f32, levels: u8) -> f32 {
    let levels = levels.max(1);
    world_size / (1u32 << (levels - 1)) as f32 / 32.0
}

/// Skirt edges in buffer order: `(outward normal, winding is the direct
/// pattern)`. Edge `k` runs over the grid border in ascending row/column
/// order; the direct wall pattern `(g0, s0, g1)` faces `down × edge_dir`, so
/// edges whose outward side disagrees (min-Z traversed +X, max-X traversed +Z)
/// flip the triangle order. This fixes the half-inverted skirt winding of the
/// TypeScript original.
const SKIRT_EDGES: [([f32; 3], bool); 4] = [
    ([0.0, 0.0, -1.0], false), // min-Z border (row 0)
    ([0.0, 0.0, 1.0], true),   // max-Z border (row `segments`)
    ([-1.0, 0.0, 0.0], true),  // min-X border (column 0)
    ([1.0, 0.0, 0.0], false),  // max-X border (column `segments`)
];

/// Snow target color blended in above `snow_height`.
/// Altitude band centers (normalized 0..1) of the low→mid and mid→high blends.
const MID_BAND_CENTER: f32 = 0.35;
const HIGH_BAND_CENTER: f32 = 0.7;
/// Half-width of the smoothstep between two altitude bands (fraction of 0..1).
const BAND_HALF_WIDTH: f32 = 0.25;
/// Snow target color blended in above `snow_height`.
const SNOW_COLOR: [f32; 4] = [1.0, 1.0, 1.0, 1.0];
/// Neutral the band colour is mixed against: white leaves the diffuse texture
/// untouched at `height-blend-strength: 0`.
const NEUTRAL_COLOR: [f32; 4] = [1.0, 1.0, 1.0, 1.0];
/// Relative tolerance when checking `size` is an exact multiple of `lod_step`.
const SEGMENT_FIT_TOLERANCE: f32 = 1e-4;

/// Builds one terrain chunk mesh from the heightfield.
///
/// The grid has `size / lod_step` segments per edge (`(segments + 1)²` top
/// vertices), then up to `4 * (segments + 1)` skirt vertices when
/// `skirt_depth > 0`. Winding is CCW seen from +Y (Bevy front face); normals
/// are frontier normals sampled with `normal_epsilon`; UVs are world-space;
/// colors are the height/slope tint of [`TintParams`].
///
/// Returns `Ok(None)` when the chunk contains no vertices at the requested
/// step (degenerate request, e.g. `lod_step` larger than the chunk grid, or
/// `size` not an exact multiple of `lod_step`).
///
/// # Errors
///
/// Fails when `size` is not finite and positive or `lod_step` is `0`, or when
/// the requested grid exceeds the `u32` index limit.
pub fn build_chunk_mesh(
    field: &impl HeightField,
    params: &ChunkMeshParams,
) -> anyhow::Result<Option<ChunkMeshData>> {
    if params.size <= 0.0 || !params.size.is_finite() {
        anyhow::bail!(
            "chunk size must be finite and positive, got {}",
            params.size
        );
    }
    if params.lod_step == 0 {
        anyhow::bail!("lod_step must be at least 1 grid meter, got 0");
    }

    let step = params.lod_step as f32;
    let segments_f = params.size / step;
    if !segments_f.is_finite() || segments_f > u32::MAX as f32 {
        anyhow::bail!(
            "chunk size {} at lod_step {} yields a degenerate grid",
            params.size,
            params.lod_step
        );
    }
    let segments = segments_f.round() as usize;
    if segments == 0 {
        return Ok(None); // lod_step larger than the chunk
    }
    if (segments as f32 * step - params.size).abs() > SEGMENT_FIT_TOLERANCE * params.size.max(step)
    {
        return Ok(None); // size is not an exact multiple of lod_step
    }

    let verts64 = segments as u64 + 1;
    let has_skirt = params.skirt_depth > 0.0;
    let skirt_verts: u64 = if has_skirt { verts64 * 4 } else { 0 };
    if verts64
        .checked_mul(verts64)
        .is_none_or(|sq| sq + skirt_verts > u64::from(u32::MAX))
    {
        anyhow::bail!(
            "chunk {}m at lod_step {} exceeds the u32 index limit; raise lod_step or shrink the chunk",
            params.size,
            params.lod_step
        );
    }
    let verts = verts64 as usize;

    let half = params.size * 0.5;
    let max_height = field.max_height();
    let tile = resolved_tile_size(params);
    let skirt_depth = params.skirt_depth;
    let index_count = segments * segments * 6 + if has_skirt { 4 * segments * 6 } else { 0 };

    let mut mesh = ChunkMeshData {
        positions: Vec::with_capacity(verts * verts),
        normals: Vec::with_capacity(verts * verts),
        uvs: Vec::with_capacity(verts * verts),
        // Height/slope tint, folded into the vertex colours so the PBR
        // material needs no custom WGSL: `color-low/mid/high/rock`,
        // `snow-height` and `slope-threshold` all land here. With
        // `height-blend-strength: 0` every vertex is just `base-color`, so a
        // world that does not want banding pays nothing for it.
        colors: Vec::with_capacity(verts * verts),
        indices: Vec::with_capacity(index_count),
    };

    // Top grid: row-major, z outer / x inner. Positions are chunk-center
    // relative on XZ and absolute on Y; normals and UVs are world-driven so
    // they agree with every neighbor chunk and LOD.
    for z in 0..verts {
        for x in 0..verts {
            let world_x = params.origin.x + x as f32 * step;
            let world_z = params.origin.z + z as f32 * step;
            let y = field.sample(world_x, world_z);
            let normal = field.sample_normal(world_x, world_z, params.normal_epsilon);
            mesh.positions
                .push([x as f32 * step - half, y, z as f32 * step - half]);
            mesh.normals.push(normal.to_array());
            mesh.uvs.push(if tile > 0.0 {
                [world_x / tile, world_z / tile]
            } else {
                [world_x, world_z]
            });
            mesh.colors
                .push(tint_vertex_color(y, normal.y, max_height, &params.tint));
        }
    }

    // Surface: 2 CCW-from-+Y triangles per cell.
    for z in 0..segments {
        for x in 0..segments {
            let a = (z * verts + x) as u32;
            let b = a + 1;
            let c = a + verts as u32;
            let d = c + 1;
            mesh.indices.extend_from_slice(&[a, c, b, b, c, d]);
        }
    }

    // Skirts: duplicate each border row/column and drop a wall whose bottom
    // ADAPTS to the neighboring surface: `min(própria, 2 amostras fora da
    // borda) − 6 cm` (cap 6 m). Em bordas planas a amostra externa ≈ a
    // própria altura → o skirt vira uma fresta de 6 cm (invisível); sobre
    // valas de estrada o desce até o piso e vira continuação do chão — a
    // cortina de profundidade fixa (`skirt_depth`) lia como parede artificial
    // pendurada. UVs match the border vertex so the wall reads as a
    // continuation of the surface; normals are horizontal and point outward.
    if has_skirt {
        let grid_count = verts * verts;
        for (edge, (outward, direct)) in SKIRT_EDGES.into_iter().enumerate() {
            let skirt_base = (grid_count + edge * verts) as u32;
            for k in 0..verts {
                let g = grid_index(edge, k, verts, segments);
                let border_pos = mesh.positions[g];
                let world_x = params.origin.x + border_pos[0] + half;
                let world_z = params.origin.z + border_pos[2] + half;
                let probe = field
                    .sample(world_x + outward[0] * step, world_z + outward[2] * step)
                    .min(field.sample(
                        world_x + outward[0] * step * 2.0,
                        world_z + outward[2] * step * 2.0,
                    ));
                let drop = (border_pos[1] - probe.min(border_pos[1]) + 0.06).clamp(0.0, 6.0);
                mesh.positions
                    .push([border_pos[0], border_pos[1] - drop, border_pos[2]]);
                mesh.normals.push(outward);
                let border_uv = mesh.uvs[g];
                mesh.uvs.push(border_uv);
                let border_color = mesh.colors[g];
                mesh.colors.push(border_color);
            }
            for k in 0..segments {
                let g0 = grid_index(edge, k, verts, segments) as u32;
                let g1 = grid_index(edge, k + 1, verts, segments) as u32;
                let s0 = skirt_base + k as u32;
                let s1 = s0 + 1;
                if direct {
                    mesh.indices.extend_from_slice(&[g0, s0, g1, g1, s0, s1]);
                } else {
                    mesh.indices.extend_from_slice(&[g0, g1, s0, g1, s1, s0]);
                }
            }
        }
    }

    Ok(Some(mesh))
}

/// Builds a regular-grid collision heightfield for one chunk at the given
/// resolution (samples per edge). Decoupled from the render LOD so collision
/// fidelity is tunable independently (VibeGame `collision-resolution`).
///
/// The grid has `(resolution + 1)²` vertices spanning `origin..origin+size` on
/// world XZ, with absolute Y sampled from the field; 2 CCW triangles per cell
/// (same winding as [`build_chunk_mesh`]). Positions are **absolute world**
/// coordinates so the Phase 3 physics integration (avian trimesh colliders)
/// can consume the buffer without a transform indirection.
///
/// `origin.y` is ignored (heights always come from the field).
///
/// # Errors
///
/// Fails when `size` is not finite and positive, `resolution` is `0`, or the
/// grid exceeds the `u32` index limit.
pub fn build_chunk_collider(
    field: &impl HeightField,
    origin: Vec3,
    size: f32,
    resolution: u32,
) -> anyhow::Result<TerrainColliderData> {
    if size <= 0.0 || !size.is_finite() {
        anyhow::bail!("collider chunk size must be finite and positive, got {size}");
    }
    if resolution == 0 {
        anyhow::bail!("collider resolution must be at least 1 sample per edge, got 0");
    }
    let verts64 = u64::from(resolution) + 1;
    if verts64
        .checked_mul(verts64)
        .is_none_or(|sq| sq > u64::from(u32::MAX))
    {
        anyhow::bail!("collider resolution {resolution} exceeds the u32 index limit");
    }

    let verts = verts64 as usize;
    let step = size / resolution as f32;
    let mut data = TerrainColliderData {
        positions: Vec::with_capacity(verts * verts),
        indices: Vec::with_capacity(resolution as usize * resolution as usize * 6),
    };

    for z in 0..verts {
        for x in 0..verts {
            let world_x = origin.x + x as f32 * step;
            let world_z = origin.z + z as f32 * step;
            data.positions
                .push([world_x, field.sample(world_x, world_z), world_z]);
        }
    }

    for z in 0..resolution as usize {
        for x in 0..resolution as usize {
            let a = (z * verts + x) as u32;
            let b = a + 1;
            let c = a + verts as u32;
            let d = c + 1;
            data.indices.extend_from_slice(&[a, c, b, b, c, d]);
        }
    }

    Ok(data)
}

/// Grid vertex index of border position `k` along skirt edge `edge`
/// (0 = min-Z row, 1 = max-Z row, 2 = min-X column, 3 = max-X column).
fn grid_index(edge: usize, k: usize, verts: usize, segments: usize) -> usize {
    match edge {
        0 => k,
        1 => segments * verts + k,
        2 => k * verts,
        _ => k * verts + segments,
    }
}

/// Texture tile size actually used for UVs: the explicit param when positive,
/// otherwise the auto rule from `world_size`/`levels`, otherwise `0.0`
/// (UVs in plain world meters, scale 1).
fn resolved_tile_size(params: &ChunkMeshParams) -> f32 {
    if params.texture_tile_size > 0.0 {
        params.texture_tile_size
    } else if params.levels > 0 && params.world_size.is_finite() && params.world_size > 0.0 {
        auto_texture_tile_size(params.world_size, params.levels)
    } else {
        0.0
    }
}

/// Evaluates the height/slope tint for one vertex — CPU port of the VibeGame
/// terrain fragment shader block (`colorLow/Mid/High/Rock`, `snowHeight`,
/// `slopeThreshold`), folded into the vertex color so no custom WGSL is
/// needed.
///
/// `y` is the absolute vertex height, `normal_y` the up component of its
/// normal, `max_height` the field peak (normalized altitude bands run 0..1).
///
/// The band colour **modulates** rather than replaces: the result is
/// `mix(white, band, height_blend_strength) * base_color`. That matters
/// because the terrain also carries a diffuse texture, and the shader
/// multiplies the two. An earlier version handed the raw band colour to the
/// shader, so `color-low: #35502a` (linear ~0.04) multiplied the grass
/// texture down to near black and the whole tint had to be ripped out. With
/// the mix, `height-blend-strength` is exactly what its name says — how far
/// the surface is pushed towards the authored band colour — and `0` leaves
/// the texture times `base-color`, i.e. no tint at all.
pub fn tint_vertex_color(y: f32, normal_y: f32, max_height: f32, tint: &TintParams) -> [f32; 4] {
    let strength = tint.height_blend_strength.clamp(0.0, 1.0);
    if strength <= 0.0 {
        return tint.base_color;
    }
    let h = if max_height > 0.0 {
        (y / max_height).clamp(0.0, 1.0)
    } else {
        0.0
    };
    // Slope: 0 flat, 1 vertical.
    let slope = 1.0 - normal_y.clamp(0.0, 1.0);
    let width = BAND_HALF_WIDTH;

    // Three-band altitude blend: low → mid → high, softstepped at the knots.
    let mid_band = smoothstep(MID_BAND_CENTER - width, MID_BAND_CENTER + width, h);
    let high_band = smoothstep(HIGH_BAND_CENTER - width, HIGH_BAND_CENTER + width, h);
    let mut color = mix4(tint.color_low, tint.color_mid, mid_band);
    color = mix4(color, tint.color_high, high_band);

    // Snow: above `snow_height` the high color fades to white.
    let snow_band = smoothstep(tint.snow_height - width, tint.snow_height + width, h);
    color = mix4(color, SNOW_COLOR, snow_band);

    // Rock on steep faces wins over altitude and snow (a cliff at the snow
    // line should not read as pure white).
    let rock_band = smoothstep(
        tint.slope_threshold - tint.slope_softness,
        tint.slope_threshold + tint.slope_softness,
        slope,
    );
    color = mix4(color, tint.color_rock, rock_band);

    // Modulate, then apply the authored base colour.
    let color = mix4(NEUTRAL_COLOR, color, strength);
    [
        color[0] * tint.base_color[0],
        color[1] * tint.base_color[1],
        color[2] * tint.base_color[2],
        color[3] * tint.base_color[3],
    ]
}

/// GLSL-style `smoothstep(edge0, edge1, x)` with a guard for degenerate edges
/// (`edge1 <= edge0` collapses to a hard step instead of dividing by zero).
fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    if (edge1 - edge0).abs() < f32::EPSILON {
        return if x >= edge1 { 1.0 } else { 0.0 };
    }
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Linear blend of two RGBA colors: `a` at `t = 0`, `b` at `t = 1`.
fn mix4(a: [f32; 4], b: [f32; 4], t: f32) -> [f32; 4] {
    [
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
        a[3] + (b[3] - a[3]) * t,
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    const EPS: f32 = 1e-4;

    /// Heightfield over an arbitrary function, independent of
    /// [`crate::terrain::sampler::HeightSampler`] (implemented in parallel).
    struct TestField {
        f: Box<dyn Fn(f32, f32) -> f32>,
        peak: f32,
    }

    impl TestField {
        fn new(f: Box<dyn Fn(f32, f32) -> f32>, peak: f32) -> Self {
            Self { f, peak }
        }

        fn flat() -> Self {
            Self::new(Box::new(|_, _| 0.0), 50.0)
        }

        /// Smooth gaussian hill, 10 m at the origin, flat far away.
        fn hill() -> Self {
            Self::new(
                Box::new(|x: f32, z: f32| 10.0 * (-(x * x + z * z) / 200.0).exp()),
                10.0,
            )
        }
    }

    impl HeightField for TestField {
        fn sample(&self, world_x: f32, world_z: f32) -> f32 {
            (self.f)(world_x, world_z)
        }

        fn sample_normal(&self, world_x: f32, world_z: f32, epsilon: f32) -> Vec3 {
            let hl = self.sample(world_x - epsilon, world_z);
            let hr = self.sample(world_x + epsilon, world_z);
            let hd = self.sample(world_x, world_z - epsilon);
            let hu = self.sample(world_x, world_z + epsilon);
            Vec3::new(
                (hl - hr) / (2.0 * epsilon),
                1.0,
                (hd - hu) / (2.0 * epsilon),
            )
            .normalize()
        }

        fn max_height(&self) -> f32 {
            self.peak
        }
    }

    fn base_params(origin: Vec3, size: f32, step: usize) -> ChunkMeshParams {
        ChunkMeshParams {
            origin,
            size,
            lod_step: step,
            skirt_depth: 0.0,
            normal_epsilon: 0.5,
            texture_tile_size: 0.0,
            levels: 0,
            world_size: 256.0,
            tint: TintParams::default(),
        }
    }

    #[test]
    fn test_tint_modulates_instead_of_replacing() {
        // The whole point of the mix: at full strength the band colour lands
        // as authored, but at the strengths worlds actually use it only
        // *pushes* the texture, instead of multiplying it down to black.
        let mut tint = TintParams {
            base_color: [1.0; 4],
            color_low: [0.04, 0.09, 0.02, 1.0],
            color_mid: [0.5, 0.5, 0.5, 1.0],
            color_high: [0.9, 0.9, 1.0, 1.0],
            color_rock: [0.2, 0.18, 0.15, 1.0],
            snow_height: 0.92,
            slope_threshold: 0.93,
            slope_softness: 0.05,
            height_blend_strength: 0.22,
        };
        // Valley floor, flat: the low band at 22% barely darkens the texture.
        let low = tint_vertex_color(10.0, 1.0, 200.0, &tint);
        assert!(
            low[1] > 0.7,
            "22% strength must not crush the texture: {low:?}"
        );
        assert!(
            low[0] < low[1],
            "and it still pushes towards green: {low:?}"
        );

        // Same vertex at full strength: the authored band colour, as-is.
        tint.height_blend_strength = 1.0;
        let full = tint_vertex_color(10.0, 1.0, 200.0, &tint);
        assert!((full[1] - tint.color_low[1]).abs() < 1e-5, "{full:?}");

        // Zero strength is an exact no-op: just the base colour.
        tint.height_blend_strength = 0.0;
        tint.base_color = [0.58, 0.55, 0.49, 1.0];
        assert_eq!(tint_vertex_color(10.0, 1.0, 200.0, &tint), tint.base_color);
    }

    #[test]
    fn test_tint_snow_on_peaks_and_rock_on_cliffs() {
        let tint = TintParams {
            base_color: [1.0; 4],
            color_low: [0.04, 0.09, 0.02, 1.0],
            color_mid: [0.2, 0.3, 0.1, 1.0],
            color_high: [0.3, 0.3, 0.35, 1.0],
            color_rock: [0.6, 0.1, 0.1, 1.0], // vivid, so the band is obvious
            snow_height: 0.8,
            slope_threshold: 0.5,
            slope_softness: 0.05,
            height_blend_strength: 1.0,
        };
        // High and flat → snow white.
        let peak = tint_vertex_color(199.0, 1.0, 200.0, &tint);
        assert!(peak.iter().take(3).all(|c| *c > 0.95), "snow: {peak:?}");
        // Just as high but vertical → rock wins over snow.
        let cliff = tint_vertex_color(199.0, 0.0, 200.0, &tint);
        assert!(cliff[0] > cliff[1], "rock beats snow on a cliff: {cliff:?}");
    }

    #[test]
    fn test_chunk_carries_one_colour_per_vertex_including_skirts() {
        let field = TestField::hill();
        let mut params = base_params(Vec3::ZERO, 16.0, 1);
        params.skirt_depth = 1.0;
        let mesh = build(&field, &params);
        assert_eq!(
            mesh.colors.len(),
            mesh.positions.len(),
            "every vertex, skirt rows included, needs a colour or the mesh \
             attribute lengths disagree"
        );
    }

    fn build(field: &TestField, params: &ChunkMeshParams) -> ChunkMeshData {
        build_chunk_mesh(field, params)
            .expect("valid params")
            .expect("non-degenerate grid")
    }

    fn assert_close<const N: usize>(actual: [f32; N], expected: [f32; N], what: &str) {
        for (a, e) in actual.iter().zip(expected.iter()) {
            assert!(
                (a - e).abs() < EPS,
                "{what}: got {actual:?}, expected ~{expected:?}"
            );
        }
    }

    /// Geometric normal of a triangle (right-hand rule).
    fn tri_normal(m: &ChunkMeshData, t: usize) -> [f32; 3] {
        let i0 = m.indices[t * 3] as usize;
        let i1 = m.indices[t * 3 + 1] as usize;
        let i2 = m.indices[t * 3 + 2] as usize;
        let (p0, p1, p2) = (m.positions[i0], m.positions[i1], m.positions[i2]);
        let e1 = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]];
        let e2 = [p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]];
        [
            e1[1] * e2[2] - e1[2] * e2[1],
            e1[2] * e2[0] - e1[0] * e2[2],
            e1[0] * e2[1] - e1[1] * e2[0],
        ]
    }

    // ----- helpers -----

    #[test]
    fn test_auto_texture_tile_size_known_values() {
        assert!((auto_texture_tile_size(256.0, 3) - 2.0).abs() < EPS); // 256/4/32
        assert!((auto_texture_tile_size(256.0, 1) - 8.0).abs() < EPS); // 256/1/32
        assert!((auto_texture_tile_size(512.0, 4) - 2.0).abs() < EPS); // 512/8/32
        assert!((auto_texture_tile_size(64.0, 0) - 2.0).abs() < EPS); // levels clamped to 1
    }

    // ----- counts and layout -----

    #[test]
    fn test_vertex_counts_no_skirt() {
        let field = TestField::flat();
        let mesh = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        assert_eq!(mesh.positions.len(), 17 * 17);
        assert_eq!(mesh.normals.len(), 17 * 17);
        assert_eq!(mesh.uvs.len(), 17 * 17);
        assert_eq!(mesh.indices.len(), 16 * 16 * 6);
    }

    #[test]
    fn test_vertex_counts_with_skirt() {
        let field = TestField::flat();
        let mut params = base_params(Vec3::ZERO, 16.0, 1);
        params.skirt_depth = 2.0;
        let mesh = build(&field, &params);
        // 4 border rows of 17 verts + 4 * 16 skirt quads.
        assert_eq!(mesh.positions.len(), 17 * 17 + 4 * 17);
        assert_eq!(mesh.indices.len(), 16 * 16 * 6 + 4 * 16 * 6);
    }

    #[test]
    fn test_skirt_disabled_when_depth_zero() {
        let field = TestField::flat();
        let mut params = base_params(Vec3::ZERO, 8.0, 1);
        params.skirt_depth = 0.0;
        let mesh = build(&field, &params);
        assert_eq!(mesh.positions.len(), 9 * 9);
        assert_eq!(mesh.indices.len(), 8 * 8 * 6);
    }

    #[test]
    fn test_lod_step_reduces_vertex_count() {
        let field = TestField::hill();
        let fine = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        let coarse = build(&field, &base_params(Vec3::ZERO, 16.0, 2));
        assert_eq!(fine.positions.len(), 17 * 17);
        assert_eq!(coarse.positions.len(), 9 * 9);
    }

    #[test]
    fn test_positions_relative_to_center_y_absolute() {
        let field = TestField::flat();
        let mesh = build(&field, &base_params(Vec3::new(64.0, 0.0, 64.0), 16.0, 8));
        // 3x3 grid; corner verts sit at ±8 around the chunk center.
        assert_close(mesh.positions[0], [-8.0, 0.0, -8.0], "min corner");
        assert_close(mesh.positions[4], [0.0, 0.0, 0.0], "center");
        assert_close(mesh.positions[8], [8.0, 0.0, 8.0], "max corner");
    }

    #[test]
    fn test_heights_sampled_from_field() {
        let field = TestField::hill();
        let mesh = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        // Vertex (x=4, z=2) -> world (4, 2), row-major index.
        let i = 2 * 17 + 4;
        let expected: f32 = 10.0 * (-(16.0f32 + 4.0) / 200.0).exp();
        assert!((mesh.positions[i][1] - expected).abs() < EPS);
        // Y is sampled at the chunk's world position, even with an offset origin
        // (vertex (4, 2) of a chunk at (100, -50) lands on world (104, -48)).
        let shifted = build(&field, &base_params(Vec3::new(100.0, 0.0, -50.0), 16.0, 1));
        let expected_shifted: f32 = 10.0 * (-(104.0f32 * 104.0 + 48.0 * 48.0) / 200.0).exp();
        assert!((shifted.positions[i][1] - expected_shifted).abs() < EPS);
    }

    // ----- skirts -----

    #[test]
    fn test_skirt_flat_border_is_a_sliver() {
        // Campo plano: a amostra externa = a própria altura → o skirt vira
        // uma fresta de 6 cm (antes: cortina fixa de `skirt_depth`).
        let field = TestField::flat();
        let mut params = base_params(Vec3::ZERO, 16.0, 1);
        params.skirt_depth = 3.5;
        let mesh = build(&field, &params);
        let verts = 17;
        let grid = verts * verts;
        for (edge, (_, _)) in SKIRT_EDGES.into_iter().enumerate() {
            for k in 0..verts {
                let g = grid_index(edge, k, verts, 16);
                let s = grid + edge * verts + k;
                let (gp, sp) = (mesh.positions[g], mesh.positions[s]);
                assert_close(sp, [gp[0], gp[1] - 0.06, gp[2]], "flat skirt sliver");
            }
        }
    }

    #[test]
    fn test_skirt_follows_lower_neighbor_surface() {
        // Degrau de 4 m no plano x=16.5: a borda max-X está no platô alto com
        // o lado de fora já no piso baixo → o skirt desce até o piso
        // (drop ≈ 4.06); a borda min-X tem os dois lados altos → fresta.
        let field = TestField::new(Box::new(|x: f32, _| if x >= 16.5 { 0.0 } else { 4.0 }), 4.0);
        let mut params = base_params(Vec3::ZERO, 16.0, 1);
        params.skirt_depth = 3.5;
        let mesh = build(&field, &params);
        let verts = 17;
        let grid = verts * verts;
        for k in 0..verts {
            let g = grid_index(3, k, verts, 16); // max-X, outward +X
            let s = grid + 3 * verts + k;
            let (gp, sp) = (mesh.positions[g], mesh.positions[s]);
            let drop = gp[1] - sp[1];
            assert!(
                (drop - 4.06).abs() < 0.15,
                "max-X follows the floor: {drop}"
            );
        }
        for k in 0..verts {
            let g = grid_index(2, k, verts, 16); // min-X, outward -X
            let s = grid + 2 * verts + k;
            let (gp, sp) = (mesh.positions[g], mesh.positions[s]);
            let drop = gp[1] - sp[1];
            assert!((drop - 0.06).abs() < 0.15, "min-X sliver: {drop}");
        }
    }
    #[test]
    fn test_all_triangles_wind_against_stored_normals() {
        // On a flat field every geometric normal must agree with the stored
        // normal: +Y on the surface, horizontal outward on the skirt walls.
        let field = TestField::flat();
        let mut params = base_params(Vec3::ZERO, 16.0, 1);
        params.skirt_depth = 2.0;
        let mesh = build(&field, &params);
        let tris = mesh.indices.len() / 3;
        for t in 0..tris {
            let n = tri_normal(&mesh, t);
            let mut avg = [0.0f32; 3];
            for k in 0..3 {
                let sn = mesh.normals[mesh.indices[t * 3 + k] as usize];
                for c in 0..3 {
                    avg[c] += sn[c];
                }
            }
            let dot = n[0] * avg[0] + n[1] * avg[1] + n[2] * avg[2];
            assert!(dot > 0.0, "triangle {t} winds against its normals");
        }
    }

    // ----- normals -----

    #[test]
    fn test_flat_field_normals_point_up() {
        let field = TestField::flat();
        let mesh = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        for n in &mesh.normals {
            assert_close(*n, [0.0, 1.0, 0.0], "flat normal");
        }
    }

    #[test]
    fn test_top_winding_ccw_from_above() {
        let field = TestField::flat();
        let mesh = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        let surface_tris = 16 * 16 * 2;
        for t in 0..surface_tris {
            assert!(
                tri_normal(&mesh, t)[1] > 0.0,
                "surface triangle {t} not CCW from +Y"
            );
        }
    }

    #[test]
    fn test_normal_continuity_between_adjacent_chunks() {
        let field = TestField::hill();
        let a = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        let b = build(&field, &base_params(Vec3::new(16.0, 0.0, 0.0), 16.0, 1));
        for k in 0..17 {
            let na = a.normals[k * 17 + 16]; // chunk A max-X border
            let nb = b.normals[k * 17]; // chunk B min-X border
            for c in 0..3 {
                assert!(
                    (na[c] - nb[c]).abs() < 1e-5,
                    "seam normal mismatch at row {k}: {na:?} vs {nb:?}"
                );
            }
        }
    }

    #[test]
    fn test_normal_continuity_across_lods() {
        let field = TestField::hill();
        let fine = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        let coarse = build(&field, &base_params(Vec3::new(16.0, 0.0, 0.0), 16.0, 2));
        // Shared border vertices exist at even z rows on both grids.
        for k in (0..17).step_by(2) {
            let nf = fine.normals[k * 17 + 16];
            let nc = coarse.normals[(k / 2) * 9];
            for c in 0..3 {
                assert!(
                    (nf[c] - nc[c]).abs() < 1e-5,
                    "cross-LOD seam at row {k}: {nf:?} vs {nc:?}"
                );
            }
        }
    }

    // ----- uvs -----

    #[test]
    fn test_uv_continuity_between_adjacent_chunks() {
        let field = TestField::hill();
        let mut params = base_params(Vec3::ZERO, 16.0, 1);
        params.texture_tile_size = 8.0;
        let mut params_b = params.clone();
        params_b.origin = Vec3::new(16.0, 0.0, 0.0);
        let a = build(&field, &params);
        let b = build(&field, &params_b);
        for k in 0..17 {
            assert_eq!(a.uvs[k * 17 + 16], b.uvs[k * 17], "uv seam at row {k}");
            assert_close(
                [a.uvs[k * 17 + 16][0], a.uvs[k * 17 + 16][1], 0.0],
                [2.0, k as f32 / 8.0, 0.0],
                "world/tile uv",
            );
        }
    }

    #[test]
    fn test_uv_world_scale_when_unresolved() {
        let field = TestField::flat();
        // tile <= 0 with levels = 0 cannot resolve the auto rule -> scale 1.
        let mesh = build(&field, &base_params(Vec3::ZERO, 16.0, 1));
        let i = 2 * 17 + 4; // world (4, 2)
        assert_close(
            [mesh.uvs[i][0], mesh.uvs[i][1], 0.0],
            [4.0, 2.0, 0.0],
            "scale-1 uv",
        );
    }

    #[test]
    fn test_uv_auto_tile_from_levels() {
        let field = TestField::flat();
        let mut params = base_params(Vec3::ZERO, 16.0, 1);
        params.texture_tile_size = 0.0;
        params.levels = 3;
        params.world_size = 256.0;
        let mesh = build(&field, &params);
        // Auto tile = 256 / 4 / 32 = 2 m; vertex world (16, 0) -> uv (8, 0).
        let i = 16; // x = 16, z = 0
        assert_close(
            [mesh.uvs[i][0], mesh.uvs[i][1], 0.0],
            [8.0, 0.0, 0.0],
            "auto-tile uv",
        );
    }

    // ----- tint -----

    #[test]
    fn test_ok_none_when_step_exceeds_size() {
        let field = TestField::flat();
        let result = build_chunk_mesh(&field, &base_params(Vec3::ZERO, 16.0, 32));
        assert!(matches!(result, Ok(None)));
    }

    #[test]
    fn test_ok_none_when_size_not_multiple_of_step() {
        let field = TestField::flat();
        // 16 / 3 rounds to 5 segments = 15 m: not an exact fit.
        let bad = build_chunk_mesh(&field, &base_params(Vec3::ZERO, 16.0, 3));
        assert!(matches!(bad, Ok(None)));
        // 15 / 3 = 5 exact segments builds fine.
        let good = build(&field, &base_params(Vec3::ZERO, 15.0, 3));
        assert_eq!(good.positions.len(), 6 * 6);
    }

    #[test]
    fn test_err_on_invalid_mesh_params() {
        let field = TestField::flat();
        assert!(build_chunk_mesh(&field, &base_params(Vec3::ZERO, 0.0, 1)).is_err());
        assert!(build_chunk_mesh(&field, &base_params(Vec3::ZERO, -4.0, 1)).is_err());
        assert!(build_chunk_mesh(&field, &base_params(Vec3::ZERO, 16.0, 0)).is_err());
    }

    // ----- collider -----

    #[test]
    fn test_collider_counts_heights_and_world_positions() {
        let field = TestField::hill();
        let data = build_chunk_collider(&field, Vec3::new(10.0, 999.0, -4.0), 16.0, 8)
            .expect("valid collider");
        assert_eq!(data.positions.len(), 9 * 9);
        assert_eq!(data.indices.len(), 8 * 8 * 6);
        // Absolute world XZ (origin.y ignored) and field-sampled Y.
        // Grid step = size / resolution = 2 m; vertex (x=4, z=2) -> world (18, 0).
        let i = 2 * 9 + 4;
        let p = data.positions[i];
        assert!((p[0] - 18.0).abs() < EPS && (p[2] - 0.0).abs() < EPS);
        let expected: f32 = 10.0 * (-(18.0f32 * 18.0) / 200.0).exp();
        assert!((p[1] - expected).abs() < EPS);
    }

    #[test]
    fn test_collider_winding_ccw_from_above() {
        let field = TestField::flat();
        let data = build_chunk_collider(&field, Vec3::ZERO, 16.0, 4).expect("valid collider");
        let flat_mesh = ChunkMeshData {
            positions: data.positions.clone(),
            ..ChunkMeshData::default()
        };
        let mut indices_mesh = flat_mesh.clone();
        indices_mesh.indices = data.indices.clone();
        for t in 0..data.indices.len() / 3 {
            assert!(
                tri_normal(&indices_mesh, t)[1] > 0.0,
                "collider triangle {t} not CCW"
            );
        }
    }

    #[test]
    fn test_collider_err_cases() {
        let field = TestField::flat();
        assert!(build_chunk_collider(&field, Vec3::ZERO, 16.0, 0).is_err());
        assert!(build_chunk_collider(&field, Vec3::ZERO, 0.0, 4).is_err());
        assert!(build_chunk_collider(&field, Vec3::ZERO, -1.0, 4).is_err());
    }
}
