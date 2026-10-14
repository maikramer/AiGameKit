//! `.vinst` — instâncias pré-geradas de um `<Vegetation>` (ou de qualquer scatter) num
//! binário compacto: o mundo estático não gera folhagem em runtime, carrega-a.
//!
//! Formato (little-endian): `b"VINS"` · versão `u32` · nº de instâncias `u32` · payload zlib
//! com, por instância, posição `3×f32` · rotação `4×f32` (quat xyzw) · escala `3×f32` ·
//! índice do template `u16` (42 bytes). O XML fica pequeno e legível para agentes; as
//! dezenas de milhares de árvores/erva vivem aqui.

use std::io::{Read, Write};
use std::path::Path;

use anyhow::{Context, Result, bail};
use bevy::math::{Quat, Vec3};

use crate::spawner::PlacedInstance;

const MAGIC: &[u8; 4] = b"VINS";
const VERSION: u32 = 1;
const RECORD: usize = 3 * 4 + 4 * 4 + 3 * 4 + 2;
/// Teto de segurança (um ficheiro corrompido não pede gigabytes).
const MAX_INSTANCES: u32 = 5_000_000;

pub fn encode(instances: &[PlacedInstance]) -> Result<Vec<u8>> {
    let mut raw = Vec::with_capacity(instances.len() * RECORD);
    for inst in instances {
        for v in inst.position.to_array() {
            raw.extend_from_slice(&v.to_le_bytes());
        }
        for v in inst.rotation.to_array() {
            raw.extend_from_slice(&v.to_le_bytes());
        }
        for v in inst.scale.to_array() {
            raw.extend_from_slice(&v.to_le_bytes());
        }
        let template =
            u16::try_from(inst.template_index).context("vinst: índice de template > 65535")?;
        raw.extend_from_slice(&template.to_le_bytes());
    }
    let mut out = Vec::with_capacity(12 + raw.len() / 3);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(&VERSION.to_le_bytes());
    out.extend_from_slice(&(instances.len() as u32).to_le_bytes());
    let mut encoder = flate2::write::ZlibEncoder::new(out, flate2::Compression::default());
    encoder.write_all(&raw)?;
    Ok(encoder.finish()?)
}

pub fn decode(bytes: &[u8]) -> Result<Vec<PlacedInstance>> {
    if bytes.len() < 12 || &bytes[..4] != MAGIC {
        bail!("vinst: magia inválida (não é um ficheiro .vinst)");
    }
    let version = u32::from_le_bytes(bytes[4..8].try_into().expect("4 bytes"));
    if version != VERSION {
        bail!("vinst: versão {version} não suportada (esperada {VERSION})");
    }
    let count = u32::from_le_bytes(bytes[8..12].try_into().expect("4 bytes"));
    if count > MAX_INSTANCES {
        bail!("vinst: {count} instâncias excede o teto ({MAX_INSTANCES})");
    }
    let mut raw = Vec::with_capacity(count as usize * RECORD);
    flate2::read::ZlibDecoder::new(&bytes[12..])
        .take((count as usize * RECORD) as u64 + 1)
        .read_to_end(&mut raw)
        .context("vinst: payload zlib")?;
    if raw.len() != count as usize * RECORD {
        bail!(
            "vinst: payload com {} bytes, esperados {}",
            raw.len(),
            count as usize * RECORD
        );
    }
    let f = |chunk: &[u8], i: usize| {
        f32::from_le_bytes(chunk[i * 4..i * 4 + 4].try_into().expect("4 bytes"))
    };
    Ok(raw
        .as_chunks::<RECORD>()
        .0
        .iter()
        .map(|c| {
            let rotation = Quat::from_xyzw(f(c, 3), f(c, 4), f(c, 5), f(c, 6)).normalize();
            let yaw_deg = rotation.to_euler(bevy::math::EulerRot::YXZ).0.to_degrees();
            PlacedInstance {
                position: Vec3::new(f(c, 0), f(c, 1), f(c, 2)),
                yaw_deg,
                rotation,
                scale: Vec3::new(f(c, 7), f(c, 8), f(c, 9)),
                template_index: u16::from_le_bytes([c[40], c[41]]) as usize,
            }
        })
        .collect())
}

pub fn write(path: &Path, instances: &[PlacedInstance]) -> Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).with_context(|| format!("criar {}", dir.display()))?;
    }
    std::fs::write(path, encode(instances)?).with_context(|| format!("escrever {}", path.display()))
}

pub fn read(path: &Path) -> Result<Vec<PlacedInstance>> {
    decode(&std::fs::read(path).with_context(|| format!("ler {}", path.display()))?)
        .with_context(|| path.display().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Vec<PlacedInstance> {
        (0..300)
            .map(|i| PlacedInstance {
                position: Vec3::new(i as f32, 2.5, -(i as f32) * 0.5),
                yaw_deg: 0.0,
                rotation: Quat::from_rotation_y(i as f32 * 0.1),
                scale: Vec3::new(1.0, 1.0 + i as f32 * 0.001, 1.0),
                template_index: i % 3,
            })
            .collect()
    }

    #[test]
    fn roundtrip_is_exact() {
        let instances = sample();
        let back = decode(&encode(&instances).unwrap()).unwrap();
        assert_eq!(back.len(), instances.len());
        for (a, b) in instances.iter().zip(&back) {
            assert_eq!(a.position, b.position);
            assert_eq!(a.scale, b.scale);
            assert_eq!(a.template_index, b.template_index);
            assert!(a.rotation.dot(b.rotation).abs() > 1.0 - 1e-6);
        }
    }

    #[test]
    fn compresses() {
        let bytes = encode(&sample()).unwrap();
        assert!(bytes.len() < 300 * RECORD, "{} bytes", bytes.len());
    }

    #[test]
    fn rejects_garbage() {
        assert!(decode(b"nope").is_err());
        let mut bytes = encode(&sample()).unwrap();
        bytes[4] = 9;
        assert!(decode(&bytes).unwrap_err().to_string().contains("versão"));
        let mut bytes = encode(&sample()).unwrap();
        bytes[8..12].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(decode(&bytes).is_err());
        let bytes = encode(&sample()).unwrap();
        assert!(decode(&bytes[..bytes.len() - 10]).is_err(), "truncado");
    }

    #[test]
    fn empty_is_valid() {
        assert!(decode(&encode(&[]).unwrap()).unwrap().is_empty());
    }
}
