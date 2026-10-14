//! Heightmap loading, encoding and procedural generation.
//!
//! Fixes two upstream `bevy_mesh_terrain` bugs: the asymmetric PNG decoding
//! (read little-endian, write big-endian) and the main-thread PNG decode.
//! Decoding happens from a Bevy [`bevy::image::Image`] (already loaded by the
//! asset system); encoding is only used by tools/tests via the `image` dev
//! dependency.
//!
//! On-disk byte order: 16-bit PNGs are big-endian on the wire, but both the
//! `image` crate (used by the Bevy asset loader) and this decoder operate on
//! native little-endian `u16` values, so [`u16::from_le_bytes`] on the buffer
//! pairs is the correct read. When a heightmap attribute is absent the terrain
//! falls back to [`HeightMapU16::procedural`] — a seeded value-noise FBM with
//! no RNG dependency, so the same seed always rebuilds the same world.

use bevy::image::Image;
use bevy::render::render_resource::{TextureDimension, TextureFormat};

use super::spec::TerrainSpec;
use super::splat::{Biome, BiomeField, BiomeWedge};
use crate::rng::splitmix64;

/// Metadados que o próprio ficheiro `.ahgt` declara sobre o mundo que
/// descreve.
///
/// `worldSize`/`maxHeight` são antigos; o bloco `biomes` é o contrato novo:
/// o campo de climas do chão vive AO LADO do campo de alturas, não no XML,
/// porque os dois são autorados pelo mesmo gerador e uma cunha de deserto
/// sobre uma bacia húmida não é um mundo. Exemplo do meta:
///
/// ```json
/// {"worldSize":4000,"maxHeight":200,
///  "biomes":{"core":90,"blend":150,
///            "wedges":[{"dir":[0,1],"biome":"forest"},
///                      {"dir":[1,0],"biome":"desert"}]}}
/// ```
#[derive(Debug, Clone, Default, PartialEq)]
pub struct AhgtMeta {
    /// Cobertura em metros declarada pelo ficheiro (`None` = não declara).
    pub world_size: Option<f32>,
    /// Cota máxima em metros declarada pelo ficheiro.
    pub max_height: Option<f32>,
    /// Campo de biomas; vazio quando o ficheiro não declara nenhum.
    pub biomes: BiomeField,
}

/// Lê o bloco `biomes` do meta JSON. Nomes desconhecidos são saltados — um
/// heightmap novo continua a carregar numa engine antiga e vice-versa.
fn parse_biomes(meta: &serde_json::Value) -> BiomeField {
    let Some(node) = meta.get("biomes") else {
        return BiomeField::default();
    };
    let mut field = BiomeField {
        wedges: Vec::new(),
        core: node["core"].as_f64().unwrap_or(80.0) as f32,
        blend: node["blend"].as_f64().unwrap_or(140.0) as f32,
    };
    let Some(list) = node.get("wedges").and_then(|w| w.as_array()) else {
        return field;
    };
    for entry in list {
        let Some(biome) = entry["biome"].as_str().and_then(Biome::from_name) else {
            continue;
        };
        let dir = entry.get("dir").and_then(|d| d.as_array());
        let (dx, dz) = match dir {
            Some(pair) if pair.len() == 2 => (
                pair[0].as_f64().unwrap_or(0.0) as f32,
                pair[1].as_f64().unwrap_or(0.0) as f32,
            ),
            _ => continue,
        };
        let v = bevy::math::Vec2::new(dx, dz);
        if v.length_squared() < 1e-6 {
            continue;
        }
        field.wedges.push(BiomeWedge {
            dir: v.normalize(),
            biome,
        });
    }
    field
}

/// Base wavelength (meters) of the procedural FBM: ~1 noise cycle per 128 m.
const FBM_BASE_WAVELENGTH: f32 = 128.0;
/// Teto do `worldSize` que um `.ahgt` pode declarar: o meta só é
/// autoridade dentro de mundos declaráveis (WorldBorder 3800 m,
/// simple-rpg 4000 m) — um header corrupto com ~1e9 sobrevivia ao
/// `spec.validate()` do parse (corre ANTES do override) e multiplicava a
/// grelha de chunks no bootstrap.
const MAX_AHGT_WORLD_SIZE: f32 = 65536.0;
/// FBM octave count.
const FBM_OCTAVES: u32 = 5;
/// Frequency multiplier between FBM octaves.
const FBM_LACUNARITY: f32 = 2.0;
/// Amplitude multiplier between FBM octaves.
const FBM_GAIN: f32 = 0.5;

/// Per-format texel decoder: byte count plus a reader over one texel's bytes.
type TexelDecoder<'a> = &'a dyn Fn(&[u8]) -> u16;

/// 16-bit height grid, row-major `[z][x]` (compatible with the upstream
/// `HeightMapU16` on-disk layout: one PNG per chunk).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HeightMapU16 {
    pub width: usize,
    pub depth: usize,
    pub data: Vec<u16>,
}

impl HeightMapU16 {
    /// Allocates a flat map filled with `value`.
    pub fn filled(width: usize, depth: usize, value: u16) -> Self {
        Self {
            width,
            depth,
            data: vec![value; width * depth],
        }
    }

    /// Decodes a Bevy `Image` into a height grid.
    ///
    /// Accepts 16-bit grayscale (`R16Uint` / `R16Unorm` / `R16Float`),
    /// 8-bit grayscale (`R8Unorm`) and RGBA8 (red channel, VibeGame
    /// "R=high/G=low" packing also works because red == high byte).
    /// 8-bit values are expanded to the full 16-bit range (`v * 257`).
    /// `R16Float` samples are IEEE 754 half-floats converted manually;
    /// negative samples (holes in some exporters) normalize to 0.
    ///
    /// # Errors
    /// Fails on unsupported texture formats or non-2D images.
    /// Decode an `.ahgt` blob (AHGT magic + JSON meta + deflate u16 grid).
    /// Returns the map and the [`AhgtMeta`] the file carries: the
    /// authoritative `worldSize`/`maxHeight` (they override the XML spec —
    /// the heightmap defines the terrain) plus the optional biome field.
    pub fn from_ahgt(bytes: &[u8]) -> anyhow::Result<(Self, AhgtMeta)> {
        use anyhow::{Context, bail};

        if bytes.len() < 20 {
            bail!("AHGT: truncated header");
        }
        let magic = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        const AHGT_MAGIC: u32 = 0x5447_4841; // "AHGT" little-endian
        if magic != AHGT_MAGIC {
            bail!("AHGT: bad magic 0x{magic:08x}");
        }
        let version = u16::from_le_bytes([bytes[4], bytes[5]]);
        if version != 1 {
            bail!("AHGT: unsupported version {version}");
        }
        let width = u16::from_le_bytes([bytes[6], bytes[7]]) as usize;
        let depth = u16::from_le_bytes([bytes[8], bytes[9]]) as usize;
        let meta_len = u32::from_le_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]) as usize;
        let meta_start = 20;
        if meta_start + meta_len > bytes.len() {
            bail!("AHGT: truncated metadata block");
        }
        let meta: serde_json::Value =
            serde_json::from_slice(&bytes[meta_start..meta_start + meta_len])
                .context("AHGT: invalid metadata JSON")?;
        let world_size = meta["worldSize"].as_f64().unwrap_or(0.0) as f32;
        let max_height = meta["maxHeight"].as_f64().unwrap_or(0.0) as f32;
        if world_size <= 0.0 || max_height <= 0.0 {
            bail!("AHGT: metadata missing worldSize/maxHeight");
        }
        if !world_size.is_finite() || world_size > MAX_AHGT_WORLD_SIZE {
            bail!(
                "AHGT: worldSize {world_size} above the {MAX_AHGT_WORLD_SIZE} m cap (corrupt meta?)"
            );
        }

        // The payload accepts both raw deflate and the RFC1950 zlib wrapper.
        let payload = &bytes[meta_start + meta_len..];
        let zlib_wrapped = payload.len() >= 2
            && (payload[0] & 0x0f) == 0x08
            && ((payload[0] as u16) << 8 | payload[1] as u16).is_multiple_of(31);
        // Expected payload size, validated BEFORE reserving: a corrupted
        // header can claim a ~8.6 GB grid (65535² texels × 2) and the old
        // `with_capacity` aborted before the payload-size check below.
        let expected = width
            .checked_mul(depth)
            .and_then(|texels| texels.checked_mul(2))
            .ok_or_else(|| anyhow::anyhow!("AHGT: {width}x{depth} grid overflows usize"))?;
        let mut raw: Vec<u8> = Vec::new();
        raw.try_reserve(expected).map_err(|_| {
            anyhow::anyhow!(
                "AHGT: header requests {expected} bytes of height data ({:.1} GiB) — \
                 refusing the allocation (corrupt header?)",
                expected as f64 / (1024.0 * 1024.0 * 1024.0)
            )
        })?;
        {
            use std::io::Read;
            let mut zlib_failed = false;
            if zlib_wrapped {
                let mut decoder = flate2::read::ZlibDecoder::new(payload);
                if decoder.read_to_end(&mut raw).is_err() {
                    zlib_failed = true;
                    raw.clear();
                }
            }
            if !zlib_wrapped || zlib_failed {
                raw.clear();
                let mut decoder = flate2::read::DeflateDecoder::new(payload);
                decoder
                    .read_to_end(&mut raw)
                    .context("AHGT: deflate decompress failed")?;
            }
        }
        if raw.len() < expected {
            bail!(
                "AHGT: height payload too small ({} bytes for {width}x{depth})",
                raw.len()
            );
        }
        let mut data = Vec::with_capacity(width * depth);
        for i in 0..width * depth {
            data.push(u16::from_le_bytes([raw[i * 2], raw[i * 2 + 1]]));
        }
        Ok((
            Self { width, depth, data },
            AhgtMeta {
                world_size: Some(world_size),
                max_height: Some(max_height),
                biomes: parse_biomes(&meta),
            },
        ))
    }

    /// Encodes this grid as an `.ahgt` blob — the symmetric of
    /// [`HeightMapU16::from_ahgt`]: magic + v1 header + JSON meta +
    /// zlib-deflated little-endian u16 payload. Used by `viber bake` to
    /// freeze a carved grid; the biome field round-trips through the same
    /// `biomes` meta contract the reader parses.
    ///
    /// # Errors
    /// Fails when the meta lacks `worldSize`/`maxHeight`, when they are
    /// degenerate, or when the grid is inconsistent with its own extents.
    pub fn to_ahgt(&self, meta: &AhgtMeta) -> anyhow::Result<Vec<u8>> {
        use std::io::Write as _;

        let (Some(world_size), Some(max_height)) = (meta.world_size, meta.max_height) else {
            anyhow::bail!("AHGT: meta needs worldSize/maxHeight to encode");
        };
        if world_size <= 0.0 || !world_size.is_finite() || max_height <= 0.0 {
            anyhow::bail!("AHGT: degenerate worldSize/maxHeight ({world_size}/{max_height})");
        }
        if self.width == 0 || self.depth == 0 || self.data.len() != self.width * self.depth {
            anyhow::bail!(
                "AHGT: inconsistent grid ({}x{} with {} samples)",
                self.width,
                self.depth,
                self.data.len()
            );
        }
        if self.width > u16::MAX as usize || self.depth > u16::MAX as usize {
            anyhow::bail!(
                "AHGT: {}x{} grid exceeds the u16 header extents",
                self.width,
                self.depth
            );
        }

        let mut meta_json = serde_json::Map::new();
        meta_json.insert("worldSize".into(), serde_json::json!(world_size));
        meta_json.insert("maxHeight".into(), serde_json::json!(max_height));
        if !meta.biomes.wedges.is_empty() {
            let wedges: Vec<_> = meta
                .biomes
                .wedges
                .iter()
                .map(|w| {
                    serde_json::json!({
                        "dir": [w.dir.x, w.dir.y],
                        "biome": w.biome.name(),
                    })
                })
                .collect();
            meta_json.insert(
                "biomes".into(),
                serde_json::json!({
                    "core": meta.biomes.core,
                    "blend": meta.biomes.blend,
                    "wedges": wedges,
                }),
            );
        }
        let meta_bytes = serde_json::to_vec(&serde_json::Value::Object(meta_json))?;

        let mut raw = Vec::with_capacity(self.data.len() * 2);
        for v in &self.data {
            raw.extend_from_slice(&v.to_le_bytes());
        }
        let mut zlib = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        zlib.write_all(&raw)?;
        let payload = zlib.finish()?;

        let mut out = Vec::with_capacity(20 + meta_bytes.len() + payload.len());
        out.extend_from_slice(&0x5447_4841u32.to_le_bytes()); // "AHGT"
        out.extend_from_slice(&1u16.to_le_bytes()); // versão
        out.extend_from_slice(&(self.width as u16).to_le_bytes());
        out.extend_from_slice(&(self.depth as u16).to_le_bytes());
        out.extend_from_slice(&[0u8; 6]); // padding reservado do header
        out.extend_from_slice(&(meta_bytes.len() as u32).to_le_bytes());
        out.extend_from_slice(&meta_bytes);
        out.extend_from_slice(&payload);
        Ok(out)
    }

    pub fn from_image(image: &Image) -> anyhow::Result<Self> {
        if image.texture_descriptor.dimension != TextureDimension::D2 {
            anyhow::bail!(
                "terrain heightmap must be a 2D texture, got {:?}",
                image.texture_descriptor.dimension
            );
        }
        let data = image
            .data
            .as_deref()
            .ok_or_else(|| anyhow::anyhow!("terrain heightmap image has no CPU-side pixel data"))?;
        let width = image.width() as usize;
        let depth = image.height() as usize;
        let texels = width.saturating_mul(depth);
        if texels == 0 {
            anyhow::bail!("terrain heightmap image is empty ({}x{})", width, depth);
        }

        let format = image.texture_descriptor.format;
        let (bytes_per_texel, decode_texel): (usize, TexelDecoder<'_>) = match format {
            // PNG 16-bit grayscale decodes to native little-endian u16 values.
            TextureFormat::R16Uint | TextureFormat::R16Unorm => {
                (2, &|b: &[u8]| u16::from_le_bytes([b[0], b[1]]))
            }
            // Half-float samples in [0,1] (negative holes normalize to 0).
            TextureFormat::R16Float => (2, &|b: &[u8]| {
                let v = half_to_f32(u16::from_le_bytes([b[0], b[1]]));
                normalized_to_u16(v)
            }),
            // 8-bit gray expands to the full 16-bit range (255 -> 65535).
            TextureFormat::R8Unorm => (1, &|b: &[u8]| b[0] as u16 * 257),
            // RGBA8: red channel only (VibeGame "R=high/G=low" packing reads
            // back correctly because red == the high byte there).
            TextureFormat::Rgba8Unorm | TextureFormat::Rgba8UnormSrgb => {
                (4, &|b: &[u8]| b[0] as u16 * 257)
            }
            other => anyhow::bail!(
                "unsupported terrain heightmap format {other:?}: use 16-bit grayscale PNG, R8Unorm or RGBA8"
            ),
        };
        let required = texels
            .checked_mul(bytes_per_texel)
            .ok_or_else(|| anyhow::anyhow!("terrain heightmap image is too large to decode"))?;
        if data.len() < required {
            anyhow::bail!(
                "terrain heightmap data too short: {} bytes for {width}x{depth} {:?} (need {required})",
                data.len(),
                format
            );
        }

        let mut out = Vec::with_capacity(texels);
        for i in 0..texels {
            let texel = &data[i * bytes_per_texel..(i + 1) * bytes_per_texel];
            out.push(decode_texel(texel));
        }
        Ok(Self {
            width,
            depth,
            data: out,
        })
    }

    /// Generates a deterministic procedural heightfield (seeded value-noise
    /// FBM, no external RNG dependency) in `[0, 65535]`. Same seed + dims
    /// always produce the same grid (first-command-works / reproducibility).
    ///
    /// The noise is integer-hashed (splitmix64 over `(x, z, seed)`) value
    /// noise, bilinear-interpolated with a smoothstep fade, stacked in
    /// [`FBM_OCTAVES`] octaves with lacunarity [`FBM_LACUNARITY`] and gain
    /// [`FBM_GAIN`], at a base frequency of ~1 cycle per
    /// [`FBM_BASE_WAVELENGTH`] meters in world space. The raw FBM range is
    /// measured and rescaled to the full `u16` span, so gentle seeds do not
    /// waste dynamic range.
    ///
    /// Dimensions are `spec.chunk_rows() * samples_per_chunk_edge` samples per
    /// axis (square grid covering the full world span), capped by
    /// [`MAX_GRID_EDGE_VERTS`](super::spec::MAX_GRID_EDGE_VERTS) so a
    /// pathological `chunk-size` degrades the resolution instead of aborting.
    pub fn procedural(spec: &TerrainSpec, samples_per_chunk_edge: usize) -> Self {
        let samples = samples_per_chunk_edge.max(1);
        let side = spec.heightfield_edge(samples);
        let width = side.max(1);
        let depth = width;
        let denom = (width - 1) as f32;

        let mut fbm = vec![0.0f32; width * depth];
        let mut min = f32::MAX;
        let mut max = f32::MIN;
        for z in 0..depth {
            let world_z = grid_to_world(z, denom, spec.world_size);
            for x in 0..width {
                let world_x = grid_to_world(x, denom, spec.world_size);
                let v = fbm_at(spec.seed, world_x, world_z);
                fbm[z * width + x] = v;
                min = min.min(v);
                max = max.max(v);
            }
        }

        let span = max - min;
        let data = fbm
            .iter()
            .map(|&v| {
                if span <= 0.0 {
                    0
                } else {
                    normalized_to_u16((v - min) / span)
                }
            })
            .collect();
        Self { width, depth, data }
    }

    /// Raw height at grid coordinates (no bounds check beyond a panic-safe clamp).
    pub fn get(&self, x: usize, z: usize) -> u16 {
        if self.data.is_empty() {
            return 0;
        }
        let x = x.min(self.width.saturating_sub(1));
        let z = z.min(self.depth.saturating_sub(1));
        self.data[z * self.width + x]
    }
}

/// Maps a grid index along one axis to world meters (centered on the origin).
fn grid_to_world(index: usize, denom: f32, world_size: f32) -> f32 {
    if denom > 0.0 && world_size.is_finite() {
        (index as f32 / denom - 0.5) * world_size
    } else {
        0.0
    }
}

/// Normalized `[0,1]` sample to raw `u16` (NaN and negatives become 0).
fn normalized_to_u16(v: f32) -> u16 {
    let v = if v.is_nan() { 0.0 } else { v };
    (v.clamp(0.0, 1.0) * u16::MAX as f32).round() as u16
}

/// Converts an IEEE 754 binary16 value to `f32` — via a crate (`half`) em
/// vez do reshuffle manual de bits que vivia aqui.
fn half_to_f32(half: u16) -> f32 {
    half::f16::from_bits(half).to_f32()
}

/// Lattice value in `[0, 1)` for one integer noise cell — hash of (seed, x, z).
fn lattice_value(seed: u64, x: i64, z: i64) -> f32 {
    let mut state = seed
        ^ (x as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ (z as u64).wrapping_mul(0xC2B2_AE3D_27D4_EB4F);
    // splitmix64(&mut state) is the value itself; warm it once so correlated
    // inputs (x, x+1 sharing high bits) decorrelate.
    let v = splitmix64(&mut state);
    ((v >> 40) as f32) / (1u32 << 24) as f32
}

/// Smoothstep fade for bilinear value-noise interpolation.
fn noise_fade(t: f32) -> f32 {
    t * t * (3.0 - 2.0 * t)
}

/// 2D value noise in `[0, 1)` at an arbitrary world-space point.
fn value_noise(seed: u64, x: f32, z: f32) -> f32 {
    let xi = x.floor() as i64;
    let zi = z.floor() as i64;
    let tx = noise_fade(x - xi as f32);
    let tz = noise_fade(z - zi as f32);
    let v00 = lattice_value(seed, xi, zi);
    let v10 = lattice_value(seed, xi + 1, zi);
    let v01 = lattice_value(seed, xi, zi + 1);
    let v11 = lattice_value(seed, xi + 1, zi + 1);
    let a = v00 + (v10 - v00) * tx;
    let b = v01 + (v11 - v01) * tx;
    a + (b - a) * tz
}

/// FBM stack: [`FBM_OCTAVES`] value-noise octaves, normalized to `[0, 1]`.
fn fbm_at(seed: u64, world_x: f32, world_z: f32) -> f32 {
    let mut amplitude = 1.0f32;
    let mut frequency = 1.0 / FBM_BASE_WAVELENGTH;
    let mut sum = 0.0f32;
    let mut norm = 0.0f32;
    for _ in 0..FBM_OCTAVES {
        sum += amplitude * value_noise(seed, world_x * frequency, world_z * frequency);
        norm += amplitude;
        amplitude *= FBM_GAIN;
        frequency *= FBM_LACUNARITY;
    }
    sum / norm
}

#[cfg(test)]
mod tests {
    use bevy::asset::RenderAssetUsages;
    use bevy::render::render_resource::{Extent3d, TextureDimension};

    use super::*;

    /// Builds a 2D image with raw bytes, mirroring what the asset loader
    /// hands to `from_image` in production.
    fn test_image(format: TextureFormat, width: u32, height: u32, data: Vec<u8>) -> Image {
        Image::new(
            Extent3d {
                width,
                height,
                depth_or_array_layers: 1,
            },
            TextureDimension::D2,
            data,
            format,
            RenderAssetUsages::default(),
        )
    }

    #[test]
    fn test_filled_allocates_flat_map() {
        let map = HeightMapU16::filled(3, 2, 777);
        assert_eq!(map.width, 3);
        assert_eq!(map.depth, 2);
        assert_eq!(map.data, vec![777; 6]);
    }

    #[test]
    fn test_from_image_r16_uint_reads_little_endian_texels() {
        // 3x2 grid of LE u16 values.
        let raw: [u16; 6] = [0, 0x1234, 0xABCD, 0xFFFF, 1, 0x8000];
        let mut data = Vec::new();
        for v in raw {
            data.extend_from_slice(&v.to_le_bytes());
        }
        let map = HeightMapU16::from_image(&test_image(TextureFormat::R16Uint, 3, 2, data))
            .expect("R16Uint decodes");
        assert_eq!((map.width, map.depth), (3, 2));
        for (i, &expected) in raw.iter().enumerate() {
            assert_eq!(map.data[i], expected, "texel {i}");
        }
    }

    #[test]
    fn test_from_image_r16_unorm_decodes_same_bits_as_uint() {
        let mut data = Vec::new();
        for v in [1234u16, 54321, 0xFFFF] {
            data.extend_from_slice(&v.to_le_bytes());
        }
        let map = HeightMapU16::from_image(&test_image(TextureFormat::R16Unorm, 3, 1, data))
            .expect("R16Unorm decodes");
        assert_eq!(map.data, vec![1234, 54321, 0xFFFF]);
    }

    #[test]
    fn test_from_image_r16_float_maps_unit_range() {
        // 0.0=0x0000, 0.5=0x3800, 1.0=0x3C00, -1.0=0xBC00, NaN=0x7E00.
        let halves: [u16; 5] = [0x0000, 0x3800, 0x3C00, 0xBC00, 0x7E00];
        let mut data = Vec::new();
        for v in halves {
            data.extend_from_slice(&v.to_le_bytes());
        }
        let map = HeightMapU16::from_image(&test_image(TextureFormat::R16Float, 5, 1, data))
            .expect("R16Float decodes");
        assert_eq!(map.data[0], 0, "0.0 -> 0");
        assert!(
            (f32::from(map.data[1]) / 65535.0 - 0.5).abs() < 1e-4,
            "0.5 -> mid range, got {}",
            map.data[1]
        );
        assert_eq!(map.data[2], 65535, "1.0 -> full range");
        assert_eq!(map.data[3], 0, "negative sample normalizes to 0");
        assert_eq!(map.data[4], 0, "NaN sample normalizes to 0");
    }

    #[test]
    fn test_from_image_r16_float_subnormal_becomes_small_value() {
        // Smallest positive half (subnormal 2^-24) should decode > 0 but tiny.
        let data = 1u16.to_le_bytes().to_vec();
        let map = HeightMapU16::from_image(&test_image(TextureFormat::R16Float, 1, 1, data))
            .expect("decodes");
        assert_eq!(map.data[0], 0, "2^-24 rounds to 0 in u16 quantization");
    }

    #[test]
    fn test_from_image_r8_unorm_expands_by_257() {
        let map = HeightMapU16::from_image(&test_image(
            TextureFormat::R8Unorm,
            3,
            1,
            vec![0x00, 0xFF, 0x80],
        ))
        .expect("R8Unorm decodes");
        assert_eq!(map.data, vec![0, 65535, 0x80 * 257]);
    }

    #[test]
    fn test_from_image_rgba8_unorm_uses_red_channel() {
        // Pixel layout RGBA: red=0x12, green=0xFF, blue=0x34, alpha=0xFF.
        let data = vec![0x12, 0xFF, 0x34, 0xFF, 0x00, 0xAA, 0xBB, 0xCC];
        let map = HeightMapU16::from_image(&test_image(TextureFormat::Rgba8Unorm, 2, 1, data))
            .expect("Rgba8Unorm decodes");
        assert_eq!(map.data, vec![0x12 * 257, 0]);
    }

    #[test]
    fn test_from_image_rgba8_unorm_srgb_uses_red_channel() {
        let data = vec![0x42, 0x00, 0x00, 0xFF];
        let map = HeightMapU16::from_image(&test_image(TextureFormat::Rgba8UnormSrgb, 1, 1, data))
            .expect("Rgba8UnormSrgb decodes");
        assert_eq!(map.data[0], 0x42 * 257);
    }

    #[test]
    fn test_from_image_rejects_unsupported_format() {
        let data = vec![0u8; 16];
        let result = HeightMapU16::from_image(&test_image(TextureFormat::Rgba32Float, 1, 1, data));
        assert!(result.is_err(), "Rgba32Float must be rejected");
        let message = result.expect_err("checked").to_string();
        assert!(
            message.contains("unsupported"),
            "error should name the problem: {message}"
        );
    }

    #[test]
    fn test_from_image_rejects_non_2d_dimension() {
        let raw: [u16; 4] = [1, 2, 3, 4];
        let mut data = Vec::new();
        for v in raw {
            data.extend_from_slice(&v.to_le_bytes());
        }
        let image = Image::new(
            Extent3d {
                width: 2,
                height: 1,
                depth_or_array_layers: 2,
            },
            TextureDimension::D3,
            data,
            TextureFormat::R16Uint,
            RenderAssetUsages::default(),
        );
        assert!(
            HeightMapU16::from_image(&image).is_err(),
            "D3 must be rejected"
        );
    }

    #[test]
    fn test_from_image_rejects_missing_pixel_data() {
        let mut image = test_image(TextureFormat::R16Uint, 2, 2, vec![0; 8]);
        image.data = None;
        assert!(
            HeightMapU16::from_image(&image).is_err(),
            "image without CPU data must be rejected"
        );
    }

    #[test]
    fn test_from_image_rejects_truncated_data() {
        // Build a valid image, then shrink the buffer behind `Image::new`'s
        // debug assert to simulate a truncated decode.
        let mut image = test_image(TextureFormat::R16Uint, 4, 4, vec![0; 32]);
        image.data = Some(vec![0; 10]);
        let result = HeightMapU16::from_image(&image);
        assert!(result.is_err(), "truncated buffer must be rejected");
    }

    #[test]
    fn test_from_image_dimensions_come_from_image() {
        let data = vec![0u8; 5 * 3];
        let map = HeightMapU16::from_image(&test_image(TextureFormat::R8Unorm, 5, 3, data))
            .expect("decodes");
        assert_eq!((map.width, map.depth), (5, 3));
    }

    #[test]
    fn test_procedural_same_seed_reproduces_bit_identical_grid() {
        let spec = TerrainSpec {
            seed: 42,
            ..Default::default()
        };
        let a = HeightMapU16::procedural(&spec, 16);
        let b = HeightMapU16::procedural(&spec, 16);
        assert_eq!(a, b, "same seed + dims must give identical grids");
    }

    #[test]
    fn test_procedural_different_seeds_differ() {
        let spec_a = TerrainSpec {
            seed: 1,
            ..Default::default()
        };
        let spec_b = TerrainSpec {
            seed: 2,
            ..Default::default()
        };
        let a = HeightMapU16::procedural(&spec_a, 16);
        let b = HeightMapU16::procedural(&spec_b, 16);
        assert_ne!(
            a.data, b.data,
            "different seeds must produce different noise"
        );
    }

    #[test]
    fn test_procedural_dimensions_follow_chunk_rows() {
        let mut spec = TerrainSpec {
            seed: 7,
            ..Default::default()
        }; // 256/64 -> 4 chunk rows
        let map = HeightMapU16::procedural(&spec, 16);
        assert_eq!((map.width, map.depth), (4 * 16, 4 * 16));
        spec.world_size = 250.0;
        spec.chunk_size = 50.0; // ceil(5) rows
        let map = HeightMapU16::procedural(&spec, 8);
        assert_eq!((map.width, map.depth), (5 * 8, 5 * 8));
    }

    #[test]
    fn test_procedural_stays_in_u16_range_and_uses_it() {
        let spec = TerrainSpec {
            seed: 0,
            ..Default::default()
        };
        let map = HeightMapU16::procedural(&spec, 16);
        assert_eq!(map.width * map.depth, map.data.len());
        let max = *map.data.iter().max().expect("non-empty");
        let min = *map.data.iter().min().expect("non-empty");
        assert_eq!(
            max, 65535,
            "normalized FBM should reach the top of the range"
        );
        assert_eq!(
            min, 0,
            "normalized FBM should reach the bottom of the range"
        );
    }

    #[test]
    fn test_procedural_seed_zero_works_and_is_not_flat() {
        let spec = TerrainSpec::default(); // seed 0
        let map = HeightMapU16::procedural(&spec, 8);
        assert!(
            map.data.iter().any(|&v| v != 0),
            "seed 0 must still build relief"
        );
    }

    #[test]
    fn test_procedural_single_sample_edge() {
        let mut spec = TerrainSpec {
            seed: 9,
            ..Default::default()
        };
        spec.world_size = 10.0;
        spec.chunk_size = 64.0; // 1 chunk row x 1 sample -> 1x1 grid
        let map = HeightMapU16::procedural(&spec, 1);
        assert_eq!((map.width, map.depth), (1, 1));
        assert_eq!(map.data.len(), 1);
    }

    #[test]
    fn test_from_ahgt_rejects_corrupt_header_without_giant_alloc() {
        use std::io::Write;
        // Header claims a 65535×65535 grid (~8.6 GiB of payload) but ships a
        // tiny one — the decoder must refuse cleanly, never abort on the
        // reservation.
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&0x5447_4841_u32.to_le_bytes()); // "AHGT"
        bytes.extend_from_slice(&1_u16.to_le_bytes()); // version
        bytes.extend_from_slice(&u16::MAX.to_le_bytes()); // width
        bytes.extend_from_slice(&u16::MAX.to_le_bytes()); // depth
        bytes.extend_from_slice(&[0_u8; 6]); // reserved
        let meta = br#"{"worldSize":256,"maxHeight":50}"#;
        bytes.extend_from_slice(&(meta.len() as u32).to_le_bytes());
        bytes.extend_from_slice(meta);
        let mut enc = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(&[0, 0, 0, 0]).expect("zlib write");
        bytes.extend_from_slice(&enc.finish().expect("zlib finish"));

        let err = HeightMapU16::from_ahgt(&bytes).expect_err("corrupt grid must be refused");
        let msg = err.to_string();
        assert!(
            msg.contains("too small") || msg.contains("refusing"),
            "clean error (no abort): {msg}"
        );
    }

    #[test]
    fn test_from_ahgt_rejects_corrupt_world_size_cleanly() {
        use std::io::Write;
        // Grid 4×4 válida, mas o meta declara um worldSize ~1e9: sem
        // `world-size` autoral o override multiplicaria a grelha de chunks —
        // tem de falhar limpo para o fallback procedural.
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&0x5447_4841_u32.to_le_bytes()); // "AHGT"
        bytes.extend_from_slice(&1_u16.to_le_bytes()); // version
        bytes.extend_from_slice(&4_u16.to_le_bytes()); // width
        bytes.extend_from_slice(&4_u16.to_le_bytes()); // depth
        bytes.extend_from_slice(&[0_u8; 6]); // reserved
        let meta = br#"{"worldSize":1e9,"maxHeight":50}"#;
        bytes.extend_from_slice(&(meta.len() as u32).to_le_bytes());
        bytes.extend_from_slice(meta);
        let mut enc = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(&[0_u8; 32]).expect("zlib write");
        bytes.extend_from_slice(&enc.finish().expect("zlib finish"));

        let err = HeightMapU16::from_ahgt(&bytes).expect_err("corrupt worldSize must be refused");
        assert!(
            err.to_string().contains("worldSize"),
            "clean error naming the field: {err}"
        );
    }

    #[test]
    fn test_get_clamps_out_of_bounds_reads() {
        let mut map = HeightMapU16::filled(3, 2, 0);
        map.data[2] = 42; // last texel of row 0
        map.data[5] = 7; // last texel overall
        assert_eq!(map.get(10, 0), 42, "x clamps to width - 1");
        assert_eq!(map.get(0, 10), 0, "z clamps to depth - 1");
        assert_eq!(map.get(10, 10), 7, "both axes clamp to the last texel");
        assert_eq!(
            HeightMapU16::filled(0, 0, 5).get(0, 0),
            0,
            "empty map reads 0"
        );
    }

    #[test]
    fn test_to_ahgt_roundtrips_grid_and_meta() {
        use bevy::math::Vec2;

        // Grid irregular (não quadrada) com padrão determinístico.
        let mut map = HeightMapU16::filled(37, 23, 0);
        for z in 0..map.depth {
            for x in 0..map.width {
                map.data[z * map.width + x] = ((x * 977 + z * 131) % 65_536) as u16;
            }
        }
        let meta = AhgtMeta {
            world_size: Some(4000.0),
            max_height: Some(200.0),
            biomes: BiomeField {
                wedges: vec![BiomeWedge {
                    dir: Vec2::new(1.0, 0.0),
                    biome: Biome::Desert,
                }],
                core: 90.0,
                blend: 150.0,
            },
        };
        let bytes = map.to_ahgt(&meta).expect("encode");
        let (back, back_meta) = HeightMapU16::from_ahgt(&bytes).expect("re-decode");
        assert_eq!(back, map, "grid idêntica");
        assert_eq!(back_meta.world_size, Some(4000.0));
        assert_eq!(back_meta.max_height, Some(200.0));
        assert_eq!(back_meta.biomes.wedges.len(), 1, "wedge sobrevive");
        assert!(matches!(back_meta.biomes.wedges[0].biome, Biome::Desert));
        let wedge = &back_meta.biomes.wedges[0];
        assert!(
            (wedge.dir.x - 1.0).abs() < 1e-6 && wedge.dir.y.abs() < 1e-6,
            "direção normalizada preservada: {:?}",
            wedge.dir
        );
    }

    #[test]
    fn test_to_ahgt_refuses_degenerate_inputs() {
        let map = HeightMapU16::filled(4, 4, 10);
        let no_meta = AhgtMeta::default();
        assert!(map.to_ahgt(&no_meta).is_err(), "sem worldSize/maxHeight");
        let bad = AhgtMeta {
            world_size: Some(0.0),
            max_height: Some(200.0),
            biomes: Default::default(),
        };
        assert!(map.to_ahgt(&bad).is_err(), "worldSize zero");
        // Meta sem wedges não escreve o bloco biomas — o re-decode volta
        // com o campo vazio (contrato: ausência ≠ default autorado).
        let plain = AhgtMeta {
            world_size: Some(100.0),
            max_height: Some(50.0),
            biomes: Default::default(),
        };
        let bytes = map.to_ahgt(&plain).expect("encode plain");
        let (_, meta) = HeightMapU16::from_ahgt(&bytes).expect("decode plain");
        assert!(meta.biomes.wedges.is_empty());
    }
}
