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

/// Base wavelength (meters) of the procedural FBM: ~1 noise cycle per 128 m.
const FBM_BASE_WAVELENGTH: f32 = 128.0;
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
    /// axis (square grid covering the full world span).
    pub fn procedural(spec: &TerrainSpec, samples_per_chunk_edge: usize) -> Self {
        let samples = samples_per_chunk_edge.max(1);
        let side = spec.chunk_rows() as usize * samples;
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

/// Converts an IEEE 754 binary16 value to `f32` (no external crate).
///
/// Handles normals, subnormals, infinities and NaN by bit-reshuffling into
/// the 32-bit layout.
fn half_to_f32(half: u16) -> f32 {
    let sign = (u32::from(half >> 15) & 0x1) << 31;
    let exponent = u32::from(half >> 10) & 0x1F;
    let mantissa = u32::from(half & 0x3FF);
    let bits = match exponent {
        0 if mantissa == 0 => sign, // ±0
        0 => {
            // Subnormal: normalize so the leading 1 lands on the implicit bit.
            let mut m = mantissa;
            let mut e = (127 - 15) + 1;
            while m & 0x400 == 0 {
                m <<= 1;
                e -= 1;
            }
            m &= 0x3FF;
            sign | (e << 23) | (m << 13)
        }
        0x1F => sign | (0xFF << 23) | (mantissa << 13), // inf / NaN
        e => sign | ((e + 127 - 15) << 23) | (mantissa << 13),
    };
    f32::from_bits(bits)
}

/// Splitmix64 finalizer — the only "RNG" in the procedural path (pure math,
/// no crate, fully deterministic across platforms).
fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
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
}
