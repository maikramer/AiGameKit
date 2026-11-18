//! AABB de cena de um GLB lido do DISCO, sem abrir a engine.
//!
//! É o que o `viber analyze` e as ferramentas de cena (`viber tool scatter`)
//! precisam para saber a pegada real de um prop. O `audit` já lia o chunk
//! JSON do GLB para validar compressões; aqui lemos só os `min`/`max` dos
//! accessors `POSITION` e compomos a hierarquia de nós — sem descomprimir
//! meshopt nem carregar texturas.
//!
//! Posições quantizadas são o caso normal do pool (`KHR_mesh_quantization`):
//! i16 `normalized` vale `valor / 32767`, e o `scale` do nó de malha entra
//! como qualquer outro. Ignorar isso dava bounds de 32 km para uma pedra de
//! 1 m — o audit não pode confiar no número cru do accessor.

use std::path::Path;

use bevy::math::{Affine3A, Mat4, Quat, Vec3};
use serde_json::Value;

/// AABB da cena glTF no espaço do modelo (inclui os transforms dos nós).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GlbBounds {
    pub min: Vec3,
    pub max: Vec3,
}

impl GlbBounds {
    pub fn half_extents(self) -> Vec3 {
        (self.max - self.min) * 0.5
    }

    pub fn center(self) -> Vec3 {
        (self.max + self.min) * 0.5
    }

    /// Pegada XZ no mesmo contrato de `spawner::template_footprint_radius`:
    /// a maior MEIA-LARGURA em X/Z (não a diagonal do AABB). É o número que
    /// o `footprint-radius` autoral tenta aproximar.
    pub fn footprint_radius(self) -> f32 {
        let half = self.half_extents();
        half.x.max(half.z).max(0.05)
    }
}

/// Lê o AABB de cena de um `.glb`. `None` quando o ficheiro não é GLB, não
/// tem geometria com bounds, ou o JSON está malformado — o chamador trata a
/// ausência como "sem pegada conhecida" (fallback), nunca como zero.
pub fn read(path: &Path) -> Option<GlbBounds> {
    let bytes = std::fs::read(path).ok()?;
    read_bytes(&bytes)
}

/// Lê só o cabeçalho e o chunk JSON do `.glb` (sem o BIN). É o caminho
/// barato para previews de cena: o AABB vem dos accessors, por isso o chunk
/// de geometria e as texturas nunca precisam de ser lidos do disco.
pub fn read_json_chunk(path: &Path) -> Option<GlbBounds> {
    use std::io::Read as _;

    const HEADER_LEN: usize = 20;
    /// Um chunk JSON maior que isto é quase de certeza um ficheiro corrompido;
    /// recusar a alocação evita um OOM num preview.
    const MAX_JSON_LEN: usize = 256 * 1024 * 1024;

    let mut file = std::fs::File::open(path).ok()?;
    let mut header = [0u8; HEADER_LEN];
    file.read_exact(&mut header).ok()?;
    if &header[0..4] != b"glTF" {
        return None;
    }
    let json_len = u32::from_le_bytes([header[12], header[13], header[14], header[15]]) as usize;
    if json_len == 0 || json_len > MAX_JSON_LEN {
        return None;
    }
    let mut bytes = vec![0u8; HEADER_LEN + json_len];
    bytes[..HEADER_LEN].copy_from_slice(&header);
    file.read_exact(&mut bytes[HEADER_LEN..]).ok()?;
    read_bytes(&bytes)
}

/// Variante para bytes já em memória (testes e o audit, que lê o ficheiro
/// uma vez por referência).
pub fn read_bytes(bytes: &[u8]) -> Option<GlbBounds> {
    const GLB_MAGIC: &[u8; 4] = b"glTF";
    if bytes.len() < 20 || &bytes[0..4] != GLB_MAGIC {
        return None;
    }
    let json_len = u32::from_le_bytes([bytes[12], bytes[13], bytes[14], bytes[15]]) as usize;
    // O chunk JSON é o primeiro; o tipo (`0x4E4F534A`) fica depois do len.
    let json_end = 20usize.checked_add(json_len)?;
    let json = std::str::from_utf8(bytes.get(20..json_end)?).ok()?;
    let doc: Value = serde_json::from_str(json).ok()?;
    scene_bounds(&doc)
}

fn scene_bounds(doc: &Value) -> Option<GlbBounds> {
    let nodes = doc.get("nodes").and_then(Value::as_array)?;
    let meshes = doc.get("meshes").and_then(Value::as_array)?;
    let accessors = doc.get("accessors").and_then(Value::as_array)?;

    // Pai de cada nó: a cena glTF é uma árvore; guardamos o pai para compor
    // o transform mundo de cada malha.
    let mut parent = vec![None; nodes.len()];
    for (index, node) in nodes.iter().enumerate() {
        if let Some(children) = node.get("children").and_then(Value::as_array) {
            for child in children.iter().filter_map(Value::as_u64) {
                if let Some(slot) = parent.get_mut(child as usize) {
                    *slot = Some(index);
                }
            }
        }
    }

    let mut world: Vec<Option<Affine3A>> = vec![None; nodes.len()];

    let mut min = Vec3::splat(f32::INFINITY);
    let mut max = Vec3::splat(f32::NEG_INFINITY);
    let mut found = false;
    for (index, node) in nodes.iter().enumerate() {
        let Some(mesh_index) = node.get("mesh").and_then(Value::as_u64) else {
            continue;
        };
        let Some(mesh) = meshes.get(mesh_index as usize) else {
            continue;
        };
        let Some(affine) = node_world(index, nodes, &parent, &mut world, 0) else {
            continue;
        };
        let Some(primitives) = mesh.get("primitives").and_then(Value::as_array) else {
            continue;
        };
        for primitive in primitives {
            let Some(position) = primitive
                .get("attributes")
                .and_then(|attrs| attrs.get("POSITION"))
                .and_then(Value::as_u64)
            else {
                continue;
            };
            let Some((local_min, local_max)) = accessor_bounds(accessors, position) else {
                continue;
            };
            // Os 8 cantos no mundo: rotação/escala de qualquer nó da cadeia
            // fica no affine (o AABB local deixa de estar alinhado aos eixos).
            for corner in 0..8 {
                let local = Vec3::new(
                    if corner & 1 == 0 {
                        local_min.x
                    } else {
                        local_max.x
                    },
                    if corner & 2 == 0 {
                        local_min.y
                    } else {
                        local_max.y
                    },
                    if corner & 4 == 0 {
                        local_min.z
                    } else {
                        local_max.z
                    },
                );
                let point = affine.transform_point3(local);
                min = min.min(point);
                max = max.max(point);
                found = true;
            }
        }
    }
    found.then_some(GlbBounds { min, max })
}

/// Transform mundo do nó, compondo a cadeia de pais. Guarda memo em `world`
/// (um nó partilhado por dois pais não é legal em glTF, mas um ciclo
/// acidental não pode pendurar o audit — o memo só é escrito DEPOIS de a
/// recursão ao pai devolver, por isso não interrompe ciclos: quem corta é o
/// limite `depth`, que excede o número de nós exactamente quando há ciclo).
fn node_world(
    index: usize,
    nodes: &[Value],
    parent: &[Option<usize>],
    world: &mut Vec<Option<Affine3A>>,
    depth: usize,
) -> Option<Affine3A> {
    if depth > nodes.len() {
        return None;
    }
    if let Some(cached) = world.get(index).copied().flatten() {
        return Some(cached);
    }
    let node = nodes.get(index)?;
    let local = node_affine(node);
    let mut result = local;
    if let Some(parent_index) = parent.get(index).copied().flatten() {
        if parent_index == index {
            return None;
        }
        let parent_world = node_world(parent_index, nodes, parent, world, depth + 1)?;
        result = parent_world * local;
    }
    if let Some(slot) = world.get_mut(index) {
        *slot = Some(result);
    }
    Some(result)
}

fn node_affine(node: &Value) -> Affine3A {
    // `matrix` (column-major no glTF) ganha à decomposição TRS.
    if let Some(matrix) = node
        .get("matrix")
        .and_then(Value::as_array)
        .filter(|matrix| matrix.len() == 16)
    {
        let mut columns = [0.0f32; 16];
        for (slot, value) in columns.iter_mut().zip(matrix) {
            *slot = value.as_f64().unwrap_or(0.0) as f32;
        }
        return Affine3A::from_mat4(Mat4::from_cols_array(&columns));
    }
    let translation = vec3(node.get("translation")).unwrap_or(Vec3::ZERO);
    let rotation = node
        .get("rotation")
        .and_then(Value::as_array)
        .filter(|values| values.len() == 4)
        .map(|values| {
            Quat::from_xyzw(
                values[0].as_f64().unwrap_or(0.0) as f32,
                values[1].as_f64().unwrap_or(0.0) as f32,
                values[2].as_f64().unwrap_or(0.0) as f32,
                values[3].as_f64().unwrap_or(1.0) as f32,
            )
        })
        .unwrap_or(Quat::IDENTITY);
    let scale = vec3(node.get("scale")).unwrap_or(Vec3::ONE);
    Affine3A::from_scale_rotation_translation(scale, rotation, translation)
}

fn vec3(value: Option<&Value>) -> Option<Vec3> {
    let values = value?.as_array()?;
    if values.len() < 3 {
        return None;
    }
    Some(Vec3::new(
        values[0].as_f64()? as f32,
        values[1].as_f64()? as f32,
        values[2].as_f64()? as f32,
    ))
}

/// Bounds locais de um accessor `POSITION`. Accessors `normalized` guardam
/// inteiros — o valor real é `inteiro / tipo_max` (o pool usa i16).
fn accessor_bounds(accessors: &[Value], index: u64) -> Option<(Vec3, Vec3)> {
    let accessor = accessors.get(index as usize)?;
    let mut min = vec3(Some(accessor.get("min")?))?;
    let mut max = vec3(Some(accessor.get("max")?))?;
    let normalized = accessor
        .get("normalized")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if normalized {
        let component_type = accessor
            .get("componentType")
            .and_then(Value::as_u64)
            .unwrap_or(5126);
        let divisor = match component_type {
            5120 => 127.0,        // i8
            5121 => 255.0,        // u8
            5122 => 32767.0,      // i16
            5123 => 65535.0,      // u16
            5125 => 4294967295.0, // u32
            _ => 1.0,             // f32 e desconhecidos
        };
        min /= divisor;
        max /= divisor;
    }
    Some((min, max))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// GLB mínimo: cabeçalho + chunk JSON (sem BIN — os bounds vêm dos
    /// accessors, exatamente o caminho do audit).
    fn glb(json: &str) -> Vec<u8> {
        let mut payload = json.as_bytes().to_vec();
        while !payload.len().is_multiple_of(4) {
            payload.push(b' ');
        }
        let mut out = Vec::with_capacity(20 + payload.len());
        out.extend_from_slice(b"glTF");
        out.extend_from_slice(&2u32.to_le_bytes());
        out.extend_from_slice(&((20 + payload.len()) as u32).to_le_bytes());
        out.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        out.extend_from_slice(&0x4E4F_534Au32.to_le_bytes());
        out.extend_from_slice(&payload);
        out
    }

    fn cube_doc(min: &str, max: &str, normalized: bool) -> String {
        format!(
            r#"{{"nodes":[{{"mesh":0,"translation":[1,2,3],"scale":[2,2,2]}}],
                "meshes":[{{"primitives":[{{"attributes":{{"POSITION":0}}}}]}}],
                "accessors":[{{"componentType":5122,"type":"VEC3","normalized":{normalized},
                    "count":8,"min":{min},"max":{max}}}]}}"#
        )
    }

    #[test]
    fn reads_node_transform_and_scale() {
        let doc = cube_doc("[-1,-1,-1]", "[1,1,1]", false);
        let bounds = read_bytes(&glb(&doc)).expect("bounds");
        assert!(
            (bounds.min - Vec3::new(-1.0, 0.0, 1.0)).length() < 1e-5,
            "{bounds:?}"
        );
        assert!(
            (bounds.max - Vec3::new(3.0, 4.0, 5.0)).length() < 1e-5,
            "{bounds:?}"
        );
        assert!((bounds.footprint_radius() - 2.0).abs() < 1e-5);
    }

    /// Dois nós que são pais um do outro (`children` cruzados) são inválidos
    /// per spec, mas o parser constrói o `parent[]` na mesma: sem o limite de
    /// profundidade em `node_world` a recursão não parava (o memo só escreve
    /// DEPOIS de o pai devolver) e o audit morria com stack overflow em vez
    /// de devolver `None`.
    #[test]
    fn cyclic_children_return_none_instead_of_overflowing() {
        let doc = r#"{"nodes":[{"mesh":0,"children":[1]},{"children":[0]}],
            "meshes":[{"primitives":[{"attributes":{"POSITION":0}}]}],
            "accessors":[{"componentType":5126,"type":"VEC3","count":8,
                "min":[-1,-1,-1],"max":[1,1,1]}]}"#;
        assert!(read_bytes(&glb(doc)).is_none());
    }

    /// i16 `normalized` é o caso do pool: o valor cru (32767) NÃO é metros.
    #[test]
    fn normalizes_quantized_positions() {
        let doc = cube_doc("[-32767,-32767,-32767]", "[32767,32767,32767]", true);
        let bounds = read_bytes(&glb(&doc)).expect("bounds");
        // Normalizado ⇒ [-1,1]; o nó escala ×2 e translate (1,2,3).
        assert!(
            (bounds.min - Vec3::new(-1.0, 0.0, 1.0)).length() < 1e-3,
            "{bounds:?}"
        );
        assert!(
            (bounds.max - Vec3::new(3.0, 4.0, 5.0)).length() < 1e-3,
            "{bounds:?}"
        );
    }

    #[test]
    fn rejects_garbage() {
        assert!(read_bytes(b"nope").is_none());
        assert!(read_bytes(&glb("{}")).is_none());
        assert!(read_bytes(&glb(r#"{"nodes":[],"meshes":[],"accessors":[]}"#)).is_none());
    }

    #[test]
    fn reads_only_the_json_chunk_from_disk() {
        use std::io::Write as _;

        let mut json = cube_doc("[-1,-1,-1]", "[1,1,1]", false).into_bytes();
        while !json.len().is_multiple_of(4) {
            json.push(b' ');
        }
        let bin = vec![0xABu8; 64];
        let total = 20 + json.len() + 8 + bin.len();
        let mut out = Vec::new();
        out.extend_from_slice(b"glTF");
        out.extend_from_slice(&2u32.to_le_bytes());
        out.extend_from_slice(&(total as u32).to_le_bytes());
        out.extend_from_slice(&(json.len() as u32).to_le_bytes());
        out.extend_from_slice(&0x4E4F_534Au32.to_le_bytes());
        out.extend_from_slice(&json);
        out.extend_from_slice(&(bin.len() as u32).to_le_bytes());
        out.extend_from_slice(&0x004E_4942u32.to_le_bytes()); // "BIN\0"
        out.extend_from_slice(&bin);

        let mut file = tempfile::NamedTempFile::new().expect("temp");
        file.write_all(&out).expect("write");
        let bounds = read_json_chunk(file.path()).expect("bounds");
        assert!((bounds.footprint_radius() - 2.0).abs() < 1e-5);
    }

    #[test]
    fn json_chunk_read_rejects_truncated_files() {
        use std::io::Write as _;

        let mut file = tempfile::NamedTempFile::new().expect("temp");
        file.write_all(b"glTF\x02\x00").expect("write");
        assert!(read_json_chunk(file.path()).is_none());
    }
}
