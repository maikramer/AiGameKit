//! Reader for the `.ahgt` baked heightmap format.
//!
//! Mirrors the engine's authoritative decoder (`Viber/src/terrain/heightmap.rs::from_ahgt`) so
//! the editor accepts exactly what the engine writes:
//!
//! ```text
//! offset  0..4   magic "AHGT" (u32 LE)
//! offset  4..6   version (u16 LE, currently 1)
//! offset  6..8   grid width  in texels (u16 LE)
//! offset  8..10  grid depth  in texels (u16 LE)
//! offset 10..16  reserved
//! offset 16..20  metadata JSON length in bytes (u32 LE)
//! offset 20..    metadata JSON ({"worldSize":4000,"maxHeight":200,...})
//! rest           u16 LE height grid, row-major [z][x], deflate-compressed
//!                (raw deflate or the RFC1950 zlib wrapper; both accepted)
//! ```

use anyhow::{Context, bail};

/// The only `.ahgt` version this reader (and the engine) understands.
const AHGT_VERSION: u16 = 1;
/// `AHGT` as a little-endian u32.
const AHGT_MAGIC: u32 = 0x5447_4841;
/// Header size up to and including the metadata length field.
const HEADER_LEN: usize = 20;
/// Ceil on the `worldSize` a metadata block may declare before it is considered corrupt
/// (same cap as the engine, guarding against giant allocations off a damaged header).
const MAX_WORLD_SIZE: f32 = 65536.0;

/// Metadata the `.ahgt` file declares about the world it describes.
///
/// Unknown JSON fields (e.g. the engine's `biomes` block) are ignored — the editor only
/// needs the terrain extents for the minimap.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct AhgtMeta {
    pub world_size: Option<f32>,
    pub max_height: Option<f32>,
}

/// A 16-bit height grid, row-major `[z][x]`, matching the engine's `HeightMapU16`.
#[derive(Debug, Clone, PartialEq)]
pub struct Ahgt {
    pub width: usize,
    pub depth: usize,
    pub data: Vec<u16>,
    pub meta: AhgtMeta,
}

impl Ahgt {
    /// Decodes an `.ahgt` blob.
    ///
    /// Unlike the engine, a missing `worldSize`/`maxHeight` is tolerated (the bake document
    /// usually carries them); only structural corruption is an error.
    pub fn from_bytes(bytes: &[u8]) -> anyhow::Result<Self> {
        if bytes.len() < HEADER_LEN {
            bail!("AHGT: truncated header");
        }
        let magic = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        if magic != AHGT_MAGIC {
            bail!("AHGT: bad magic 0x{magic:08x}");
        }
        let version = u16::from_le_bytes([bytes[4], bytes[5]]);
        if version != AHGT_VERSION {
            bail!("AHGT: unsupported version {version}");
        }
        let width = u16::from_le_bytes([bytes[6], bytes[7]]) as usize;
        let depth = u16::from_le_bytes([bytes[8], bytes[9]]) as usize;
        let meta_len = u32::from_le_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]) as usize;
        if HEADER_LEN + meta_len > bytes.len() {
            bail!("AHGT: truncated metadata block");
        }
        let meta = parse_meta(&bytes[HEADER_LEN..HEADER_LEN + meta_len])?;

        // Validate the payload size BEFORE reserving it: a corrupt header can claim an
        // ~8.6 GiB grid and the naive allocation aborts the editor.
        let expected = width
            .checked_mul(depth)
            .and_then(|texels| texels.checked_mul(2))
            .ok_or_else(|| anyhow::anyhow!("AHGT: {width}x{depth} grid overflows usize"))?;
        if expected > 512 * 1024 * 1024 {
            bail!(
                "AHGT: header requests {expected} bytes of height data — refusing (corrupt header?)"
            );
        }

        // The payload accepts both raw deflate and the RFC1950 zlib wrapper, like the engine.
        let payload = &bytes[HEADER_LEN + meta_len..];
        let zlib_wrapped = payload.len() >= 2
            && (payload[0] & 0x0f) == 0x08
            && ((payload[0] as u16) << 8 | payload[1] as u16).is_multiple_of(31);
        let mut raw: Vec<u8> = Vec::new();
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

        let data = raw[..expected]
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| u16::from_le_bytes(*pair))
            .collect();
        Ok(Self {
            width,
            depth,
            data,
            meta,
        })
    }

    /// The normalized height `[0, 1]` at grid cell `(x, z)`, row-major `[z][x]`.
    pub fn normalized(&self, x: usize, z: usize) -> f32 {
        self.data[z * self.width + x] as f32 / u16::MAX as f32
    }
}

/// Parses the metadata JSON block; empty blocks are fine (defaults).
fn parse_meta(bytes: &[u8]) -> anyhow::Result<AhgtMeta> {
    if bytes.is_empty() {
        return Ok(AhgtMeta::default());
    }
    let meta: serde_json::Value =
        serde_json::from_slice(bytes).context("AHGT: invalid metadata JSON")?;
    let pick = |name: &str| {
        meta[name].as_f64().and_then(|value| {
            let value = value as f32;
            (value.is_finite() && value > 0.0 && value <= MAX_WORLD_SIZE).then_some(value)
        })
    };
    Ok(AhgtMeta {
        world_size: pick("worldSize"),
        max_height: pick("maxHeight"),
    })
}

/// Encodes a grid as a synthetic `.ahgt` blob (raw deflate payload).
///
/// Only used by tests here; the real files come from the engine's bake.
#[cfg(test)]
pub(crate) fn encode_ahgt(
    width: usize,
    depth: usize,
    data: &[u16],
    meta: &str,
    zlib_wrap: bool,
) -> Vec<u8> {
    use std::io::Write;

    assert_eq!(data.len(), width * depth);
    let mut raw = Vec::with_capacity(data.len() * 2);
    for value in data {
        raw.extend_from_slice(&value.to_le_bytes());
    }
    let payload = if zlib_wrap {
        let mut encoder =
            flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        encoder.write_all(&raw).unwrap();
        encoder.finish().unwrap()
    } else {
        let mut encoder =
            flate2::write::DeflateEncoder::new(Vec::new(), flate2::Compression::default());
        encoder.write_all(&raw).unwrap();
        encoder.finish().unwrap()
    };

    let mut bytes = Vec::with_capacity(HEADER_LEN + meta.len() + payload.len());
    bytes.extend_from_slice(&AHGT_MAGIC.to_le_bytes());
    bytes.extend_from_slice(&AHGT_VERSION.to_le_bytes());
    bytes.extend_from_slice(&(width as u16).to_le_bytes());
    bytes.extend_from_slice(&(depth as u16).to_le_bytes());
    bytes.extend_from_slice(&[0u8; 6]); // reserved
    bytes.extend_from_slice(&(meta.len() as u32).to_le_bytes());
    bytes.extend_from_slice(meta.as_bytes());
    bytes.extend_from_slice(&payload);
    bytes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrips_synthetic_ahgt_raw_deflate() {
        let width = 8;
        let depth = 4;
        let data: Vec<u16> = (0..width * depth).map(|i| (i * 1000) as u16).collect();
        let meta = r#"{"worldSize":4000,"maxHeight":200,"biomes":{"core":90}}"#;
        let bytes = encode_ahgt(width, depth, &data, meta, false);

        let map = Ahgt::from_bytes(&bytes).unwrap();
        assert_eq!(map.width, width);
        assert_eq!(map.depth, depth);
        assert_eq!(map.data, data);
        assert_eq!(map.meta.world_size, Some(4000.0));
        assert_eq!(map.meta.max_height, Some(200.0));
        // The engine-side `biomes` block is ignored but tolerated.
        assert_eq!(map.normalized(1, 0), 1000.0 / u16::MAX as f32);
    }

    #[test]
    fn roundtrips_synthetic_ahgt_zlib_wrapped() {
        let data = vec![u16::MAX; 16];
        let bytes = encode_ahgt(4, 4, &data, r#"{"worldSize":512,"maxHeight":64}"#, true);
        let map = Ahgt::from_bytes(&bytes).unwrap();
        assert_eq!(map.data, data);
        assert_eq!(map.normalized(0, 0), 1.0);
    }

    #[test]
    fn accepts_empty_metadata() {
        let bytes = encode_ahgt(2, 2, &[0, 1, 2, 3], "", false);
        let map = Ahgt::from_bytes(&bytes).unwrap();
        assert_eq!(map.meta, AhgtMeta::default());
    }

    #[test]
    fn rejects_bad_magic_version_and_truncation() {
        let good = encode_ahgt(2, 2, &[0; 4], "{}", false);

        let mut bad_magic = good.clone();
        bad_magic[0] = b'X';
        assert!(Ahgt::from_bytes(&bad_magic).is_err());

        let mut bad_version = good.clone();
        bad_version[4] = 2;
        assert!(
            Ahgt::from_bytes(&bad_version)
                .unwrap_err()
                .to_string()
                .contains("unsupported version 2")
        );

        // Truncated everywhere a reader could trip: header, metadata, payload.
        assert!(Ahgt::from_bytes(&good[..19]).is_err());
        let mut no_meta = good.clone();
        no_meta.truncate(HEADER_LEN + 2);
        no_meta[16..20].copy_from_slice(&50u32.to_le_bytes()); // claims 50 bytes of meta
        assert!(Ahgt::from_bytes(&no_meta).is_err());
        let mut short_payload = good;
        short_payload.pop();
        short_payload.pop();
        assert!(Ahgt::from_bytes(&short_payload).is_err());
    }

    #[test]
    fn rejects_giant_grid_claims_without_allocating() {
        // 65535 x 65535 texels would be an ~8.6 GiB payload; the header alone must be refused.
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&AHGT_MAGIC.to_le_bytes());
        bytes.extend_from_slice(&AHGT_VERSION.to_le_bytes());
        bytes.extend_from_slice(&65535u16.to_le_bytes());
        bytes.extend_from_slice(&65535u16.to_le_bytes());
        bytes.extend_from_slice(&[0u8; 6]);
        bytes.extend_from_slice(&0u32.to_le_bytes());
        let error = Ahgt::from_bytes(&bytes).unwrap_err().to_string();
        assert!(error.contains("refusing"), "unexpected error: {error}");
    }
}
