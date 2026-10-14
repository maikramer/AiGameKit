//! Pose (translação/rotação/escala) de um nó XML: leitura com as MESMAS regras do parser de
//! recipes e escrita na forma canónica.
//!
//! O formato aceita aliases históricos — `pos`, `rotation` (3 valores = radianos, 4 = quat),
//! `transform="pos: …; rotation: …"` (aqui `rotation` são GRAUS). Para agentes e diffs isto
//! é ruído: [`pose_op`] escreve sempre `translation` + `euler` (graus, XYZ) + `scale` e remove
//! os aliases, de modo que o mesmo gesto produz sempre o mesmo XML.

use std::collections::BTreeMap;

use anyhow::Result;
use bevy::math::{EulerRot, Quat, Vec3};

use super::ops::EditOp;
use crate::recipes::{parse_component_string, parse_rotation_attr};
use crate::xml::XmlNode;
use crate::xml::values::parse_vec3;

/// Pose local de um nó (relativa ao pai, como no XML).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pose {
    pub translation: Vec3,
    pub rotation: Quat,
    pub scale: Vec3,
}

impl Default for Pose {
    fn default() -> Self {
        Self {
            translation: Vec3::ZERO,
            rotation: Quat::IDENTITY,
            scale: Vec3::ONE,
        }
    }
}

/// Que partes da pose escrever.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PoseParts {
    pub translation: bool,
    pub rotation: bool,
    pub scale: bool,
}

impl PoseParts {
    pub const ALL: Self = Self {
        translation: true,
        rotation: true,
        scale: true,
    };
    pub const TRANSLATION: Self = Self {
        translation: true,
        rotation: false,
        scale: false,
    };
}

/// Lê a pose de um nó. Atributos inválidos dão erro (como no parse do mundo).
pub fn read_pose(node: &XmlNode) -> Result<Pose> {
    let ctx = format!("<{}>", node.tag);
    let mut translation = Vec3::ZERO;
    let mut euler_deg: Option<Vec3> = None;
    let mut quat: Option<Quat> = None;
    let mut scale = Vec3::ONE;
    for (key, value) in &node.attrs {
        match key.as_str() {
            "translation" | "pos" => translation = Vec3::from(parse_vec3(value, &ctx)?),
            "euler" => euler_deg = Some(Vec3::from(parse_vec3(value, &ctx)?)),
            "rotation" => {
                let [x, y, z, w] = parse_rotation_attr(value, &ctx)?;
                quat = Some(Quat::from_xyzw(x, y, z, w));
            }
            "scale" => scale = Vec3::from(parse_vec3(value, &ctx)?),
            "transform" => {
                for (part, value) in parse_component_string(value) {
                    match part.as_str() {
                        "pos" | "position" => translation = Vec3::from(parse_vec3(&value, &ctx)?),
                        "euler" | "rotation" => {
                            euler_deg = Some(Vec3::from(parse_vec3(&value, &ctx)?))
                        }
                        "scale" => scale = Vec3::from(parse_vec3(&value, &ctx)?),
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }
    // Como no `build_transform` do spawn: o quaternião ganha ao euler.
    let rotation = quat.unwrap_or_else(|| match euler_deg {
        Some(e) => Quat::from_euler(
            EulerRot::XYZ,
            e.x.to_radians(),
            e.y.to_radians(),
            e.z.to_radians(),
        ),
        None => Quat::IDENTITY,
    });
    Ok(Pose {
        translation,
        rotation,
        scale,
    })
}

/// A operação `set` que escreve `pose` (só as `parts` pedidas) na forma canónica, removendo
/// os aliases que a contradiriam. As partes não pedidas mantêm o valor ATUAL, reescrito
/// também na forma canónica quando o alias que o guardava desaparece.
pub fn pose_op(selector: &str, node: &XmlNode, pose: Pose, parts: PoseParts) -> Result<EditOp> {
    let current = read_pose(node)?;
    let has = |key: &str| node.attr(key).is_some();
    let had_translation = has("translation") || has("pos") || has("transform");
    let had_rotation = has("euler") || has("rotation") || has("transform");
    let had_scale = has("scale") || has("transform");

    // Rotação que não muda e já está em forma canónica (inclui um `rotation` quaternião
    // canónico): fica exatamente como está.
    let keep_rotation = !parts.rotation && rotation_is_canonical(node);
    let mut attrs: BTreeMap<String, Option<String>> = BTreeMap::new();
    for alias in ["pos", "rotation", "transform"] {
        if has(alias) && !(alias == "rotation" && keep_rotation) {
            attrs.insert(alias.into(), None);
        }
    }
    let translation = if parts.translation {
        pose.translation
    } else {
        current.translation
    };
    if parts.translation || had_translation {
        attrs.insert("translation".into(), Some(format_vec3(translation)));
    }
    let rotation = if parts.rotation {
        pose.rotation
    } else {
        current.rotation
    };
    if (parts.rotation || had_rotation) && !keep_rotation {
        if rotation.dot(Quat::IDENTITY).abs() > 1.0 - 1e-7 && !has("euler") {
            attrs.insert("euler".into(), None);
        } else {
            // `euler` canónico; `rotation` (quat) só onde o euler não é estável e fiel.
            let (key, value) = rotation_attr(rotation);
            let other = if key == "euler" { "rotation" } else { "euler" };
            attrs.insert(key.into(), Some(value));
            if has(other) {
                attrs.insert(other.into(), None);
            } else {
                attrs.remove(other);
            }
        }
    }
    let scale = if parts.scale {
        pose.scale
    } else {
        current.scale
    };
    if parts.scale || had_scale {
        attrs.insert("scale".into(), Some(format_scale(scale)));
    }
    Ok(EditOp::Set {
        node: selector.to_owned(),
        attrs,
    })
}

/// `"x y z"` com até 4 casas decimais, sem zeros à direita (`"4.5 0 -2.25"`).
pub fn format_vec3(v: Vec3) -> String {
    format!(
        "{} {} {}",
        format_num(v.x),
        format_num(v.y),
        format_num(v.z)
    )
}

/// Ângulos euler XYZ em graus de um quaternião.
pub fn format_euler(q: Quat) -> String {
    stable_euler(q).unwrap_or_else(|| euler_text(q, 4))
}

/// O atributo canónico para ESCREVER uma rotação nova: `euler` (graus XYZ, legível) quando há
/// uma string euler estável (relê-se igual) e fiel a esta rotação, senão `rotation` em
/// quaternião. Na ordem XYZ o ângulo do meio é o Y (o yaw): com |Y| ≈ 90° e inclinação, X e Z
/// acoplam-se e em f32 a string euler deriva — aí o quaternião. Rotações limpas (`0 90 0`)
/// são euler. Uma rotação que NÃO muda e já está em forma canónica nunca é re-decomposta
/// ([`pose_op`]) — é isso que torna a forma estável entre edições.
pub fn rotation_attr(q: Quat) -> (&'static str, String) {
    if let Some(text) = stable_euler(q)
        && parse_euler_text(&text).dot(q).abs() > 1.0 - EULER_FIDELITY
    {
        return ("euler", text);
    }
    ("rotation", quat_text(q))
}

/// Desvio máximo (1 − |q·q'|) entre a rotação e a sua string euler canónica.
const EULER_FIDELITY: f32 = 2e-7;

/// O texto já está na forma que [`format_num_with`] escreveria (sem casas a mais, sem `-0`).
fn tokens_canonical(text: &str, count: usize, decimals: i32) -> Option<Vec<f32>> {
    let tokens: Vec<&str> = text.split_whitespace().collect();
    if tokens.len() != count {
        return None;
    }
    let values: Vec<f32> = tokens
        .iter()
        .map(|t| t.parse().ok())
        .collect::<Option<_>>()?;
    let canonical = tokens
        .iter()
        .zip(&values)
        .all(|(token, value)| *token == format_num_with(*value, decimals));
    canonical.then_some(values)
}

/// A rotação do nó já está escrita na forma canónica (euler em (-180, 180] com ≤4 casas, ou
/// quaternião normalizado com 6 casas e w ≥ 0), sem aliases a contradizê-la.
fn rotation_is_canonical(node: &XmlNode) -> bool {
    if node.attr("transform").is_some() {
        return false;
    }
    match (node.attr("euler"), node.attr("rotation")) {
        (Some(euler), None) => tokens_canonical(euler, 3, 4)
            .is_some_and(|v| v.iter().all(|d| *d > -180.0 && *d <= 180.0)),
        (None, Some(quat)) => tokens_canonical(quat, 4, 6).is_some_and(|v| {
            let norm = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2] + v[3] * v[3]).sqrt();
            v[3] >= 0.0 && (norm - 1.0).abs() < 1e-5
        }),
        _ => false,
    }
}

/// O ponto fixo de arredondar→reler a 4 casas, se existir (8 iterações).
fn stable_euler(q: Quat) -> Option<String> {
    let mut text = euler_text(q, 4);
    for _ in 0..8 {
        let again = euler_text(parse_euler_text(&text), 4);
        if again == text {
            return Some(text);
        }
        text = again;
    }
    None
}

/// Quaternião `x y z w` com 6 casas e `w ≥ 0` (q e -q são a mesma rotação), normalizado.
fn quat_text(q: Quat) -> String {
    let q = q.normalize();
    let q = if q.w < 0.0 { -q } else { q };
    [q.x, q.y, q.z, q.w]
        .map(|v| format_num_with(v, 6))
        .join(" ")
}

fn euler_text(q: Quat, decimals: i32) -> String {
    let (x, y, z) = q.to_euler(EulerRot::XYZ);
    [x, y, z]
        .map(|angle| format_num_with(canonical_degrees(angle.to_degrees()), decimals))
        .join(" ")
}

fn parse_euler_text(text: &str) -> Quat {
    let v: Vec<f32> = text
        .split_whitespace()
        .filter_map(|n| n.parse().ok())
        .collect();
    Quat::from_euler(
        EulerRot::XYZ,
        v[0].to_radians(),
        v[1].to_radians(),
        v[2].to_radians(),
    )
}

/// Ângulo em (-180, 180], com o -180 (e o que arredonda para ele) escrito como 180 — sem isto
/// a mesma rotação saía `-180 … -180` numa leitura e `180 … 180` na seguinte.
fn canonical_degrees(degrees: f32) -> f32 {
    let wrapped = (degrees + 180.0).rem_euclid(360.0) - 180.0;
    if wrapped <= -180.0 + 5e-5 {
        180.0
    } else {
        wrapped
    }
}

/// Escala uniforme sai como um número só (`"2"`).
pub fn format_scale(s: Vec3) -> String {
    if (s.x - s.y).abs() < 1e-5 && (s.x - s.z).abs() < 1e-5 {
        format_num(s.x)
    } else {
        format_vec3(s)
    }
}

pub fn format_num(value: f32) -> String {
    format_num_with(value, 4)
}

/// Como [`format_num`], com `decimals` casas.
pub fn format_num_with(value: f32, decimals: i32) -> String {
    let scale = 10f64.powi(decimals);
    let rounded = (value as f64 * scale).round() / scale;
    // `-0` não tem lugar num ficheiro de mundo.
    let rounded = if rounded == 0.0 { 0.0 } else { rounded };
    let text = format!("{rounded:.*}", decimals as usize);
    text.trim_end_matches('0').trim_end_matches('.').to_owned()
}

/// Referencial MUNDO dos filhos de `node`, só a partir do documento: compõe as poses dos
/// ancestrais (e de quem inclui o ficheiro). Num mundo ESTÁTICO as poses do XML são finais,
/// por isso isto é exato sem engine — a CLI offline, o bridge e o editor convertem igual.
/// (Num mundo procedural o assentamento em runtime não entra na conta.)
pub fn world_frame(doc: &super::doc::SceneDoc, node: &super::doc::NodeRef) -> bevy::math::Affine3A {
    use bevy::math::Affine3A;
    let mut chain: Vec<Affine3A> = Vec::new();
    let mut current = node.clone();
    for _ in 0..256 {
        if !current.is_root() {
            if let Some(xml) = doc.node(&current)
                && let Ok(pose) = read_pose(xml)
            {
                chain.push(Affine3A::from_scale_rotation_translation(
                    pose.scale,
                    pose.rotation,
                    pose.translation,
                ));
            }
            current = current.parent().expect("não-raiz");
            continue;
        }
        // Raiz de um ficheiro: continua pelo `<Include>` que o carrega. Um include com
        // transform vira um Group na expansão — o loop compõe essa pose como a de
        // qualquer nó não-raiz (a pose do próprio include entra no referencial).
        let includer = (0..doc.files.len()).find_map(|file| {
            doc.files[file]
                .includes
                .iter()
                .find(|(_, target)| *target == current.file)
                .map(|(path, _)| super::doc::NodeRef::new(file, path.clone()))
        });
        match includer {
            Some(include) => current = include,
            None => break,
        }
    }
    chain
        .iter()
        .rev()
        .fold(Affine3A::IDENTITY, |frame, local| frame * *local)
}

/// Pose mundo → pose LOCAL a `parent` (o inverso de [`world_frame`]).
pub fn world_to_local(
    doc: &super::doc::SceneDoc,
    parent: &super::doc::NodeRef,
    world: Pose,
) -> Pose {
    let frame = world_frame(doc, parent);
    let (scale, rotation, _) = frame.to_scale_rotation_translation();
    Pose {
        translation: frame.inverse().transform_point3(world.translation),
        rotation: (rotation.inverse() * world.rotation).normalize(),
        scale: world.scale / scale,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(attrs: &[(&str, &str)]) -> XmlNode {
        let mut node = XmlNode::new("Entity");
        for (k, v) in attrs {
            node.set_attr(k, *v);
        }
        node
    }

    fn approx_quat(a: Quat, b: Quat) -> bool {
        // `angle_between` perde precisão em f32 perto de 0 (acos de ~1); o produto escalar não.
        a.dot(b).abs() > 1.0 - 1e-6
    }

    #[test]
    fn reads_every_alias_like_the_world_parser() {
        let pose = read_pose(&node(&[("pos", "1 2 3"), ("scale", "2")])).unwrap();
        assert_eq!(pose.translation, Vec3::new(1.0, 2.0, 3.0));
        assert_eq!(pose.scale, Vec3::splat(2.0));
        // `rotation` com 3 valores = radianos.
        let pose = read_pose(&node(&[("rotation", "0 1.5707964 0")])).unwrap();
        assert!(approx_quat(
            pose.rotation,
            Quat::from_rotation_y(90f32.to_radians())
        ));
        // Dentro de `transform`, `rotation` são graus.
        let pose = read_pose(&node(&[("transform", "pos: 1 0 0; rotation: 0 90 0")])).unwrap();
        assert_eq!(pose.translation, Vec3::X);
        assert!(approx_quat(
            pose.rotation,
            Quat::from_rotation_y(90f32.to_radians())
        ));
        // O quaternião ganha ao euler, qualquer que seja a ordem.
        let pose = read_pose(&node(&[("rotation", "0 0 0 1"), ("euler", "0 90 0")])).unwrap();
        assert!(approx_quat(pose.rotation, Quat::IDENTITY));
        assert!(read_pose(&node(&[("translation", "1 2")])).is_err());
    }

    #[test]
    fn writes_canonical_attributes_and_drops_aliases() {
        let n = node(&[("pos", "1 2 3"), ("rotation", "0 0 0 1"), ("name", "x")]);
        let pose = Pose {
            translation: Vec3::new(4.5, 0.0, -2.25),
            rotation: Quat::from_rotation_y(90f32.to_radians()),
            scale: Vec3::splat(1.5),
        };
        let EditOp::Set { attrs, .. } = pose_op("#0", &n, pose, PoseParts::ALL).unwrap() else {
            panic!("set");
        };
        assert_eq!(attrs["pos"], None);
        assert_eq!(attrs["rotation"], None);
        assert_eq!(attrs["translation"].as_deref(), Some("4.5 0 -2.25"));
        assert_eq!(attrs["euler"].as_deref(), Some("0 90 0"));
        assert_eq!(attrs["scale"].as_deref(), Some("1.5"));
        assert!(!attrs.contains_key("name"));
    }

    #[test]
    fn translation_only_keeps_rotation_from_an_alias() {
        // A rotação vivia num alias que vai desaparecer: tem de ser reescrita, não perdida.
        let n = node(&[("transform", "pos: 1 0 0; rotation: 0 45 0")]);
        let pose = Pose {
            translation: Vec3::new(9.0, 0.0, 0.0),
            ..Pose::default()
        };
        let EditOp::Set { attrs, .. } = pose_op("#0", &n, pose, PoseParts::TRANSLATION).unwrap()
        else {
            panic!("set");
        };
        assert_eq!(attrs["transform"], None);
        assert_eq!(attrs["translation"].as_deref(), Some("9 0 0"));
        assert_eq!(attrs["euler"].as_deref(), Some("0 45 0"));
    }

    #[test]
    fn untouched_parts_stay_absent() {
        let n = node(&[("translation", "1 0 0")]);
        let pose = Pose {
            translation: Vec3::new(2.0, 0.0, 0.0),
            ..Pose::default()
        };
        let EditOp::Set { attrs, .. } = pose_op("#0", &n, pose, PoseParts::TRANSLATION).unwrap()
        else {
            panic!("set");
        };
        assert_eq!(attrs.len(), 1, "{attrs:?}");
    }

    #[test]
    fn rotation_form_is_stable_near_gimbal_lock() {
        // Árvores do simple-rpg com yaw ≈ ±90° e inclinação (align-to-terrain).
        for euler in [
            "92.1805 -86.1509 94.2007",
            "143.5261 -87.0869 135.7578",
            "151.7537 87.679 -151.9419",
            "16.181 -85.8349 30.2164",
            "107.0164 87.7158 -109.4939",
            "-68.5064 86.3708 62.886",
            "-138.6825 88.6644 142.6393",
            "150.6213 89.9147 -136.8194",
        ] {
            let messy = node(&[("rotation", "0 0 0 1")]);
            let q = read_pose(&node(&[("euler", euler)])).unwrap().rotation;
            let pose = Pose {
                rotation: q,
                ..Pose::default()
            };
            // Escrever a rotação nova…
            let EditOp::Set { attrs, .. } = pose_op("#0", &messy, pose, PoseParts::ALL).unwrap()
            else {
                panic!()
            };
            let mut written = node(&[]);
            for (k, v) in &attrs {
                if let Some(v) = v {
                    written.set_attr(k, v.clone());
                }
            }
            let back = read_pose(&written).unwrap().rotation;
            assert!(
                back.dot(q).abs() > 1.0 - 1e-6,
                "{euler}: a rotação mantém-se"
            );
            // …e uma edição seguinte que não mexe na rotação deixa-a intacta.
            let none = PoseParts {
                translation: true,
                rotation: false,
                scale: false,
            };
            let EditOp::Set { attrs, .. } =
                pose_op("#0", &written, read_pose(&written).unwrap(), none).unwrap()
            else {
                panic!()
            };
            assert!(
                !attrs.contains_key("euler") && !attrs.contains_key("rotation"),
                "{euler}: {attrs:?}"
            );
        }
    }

    #[test]
    fn clean_yaw_stays_euler() {
        for yaw in [90.0f32, -90.0, 45.0, 180.0] {
            let (key, text) = rotation_attr(Quat::from_rotation_y(yaw.to_radians()));
            assert_eq!(key, "euler", "{yaw}: {text}");
        }
    }

    #[test]
    fn euler_is_stable_around_180() {
        for deg in [180.0f32, -180.0, 179.99998, -179.99998, 540.0] {
            let q = Quat::from_euler(EulerRot::XYZ, deg.to_radians(), 0.3, deg.to_radians());
            let once = format_euler(q);
            let again = format_euler(read_pose(&node(&[("euler", &once)])).unwrap().rotation);
            assert_eq!(once, again, "{deg}");
            assert!(!once.contains("-180"), "{once}");
        }
    }

    #[test]
    fn numbers_are_compact() {
        assert_eq!(format_num(1.0), "1");
        assert_eq!(format_num(-0.00001), "0");
        assert_eq!(format_num(2.123456), "2.1235");
        assert_eq!(format_num(-3.5), "-3.5");
        assert_eq!(format_scale(Vec3::new(1.0, 2.0, 1.0)), "1 2 1");
    }
}
