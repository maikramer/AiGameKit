//! Recipe layer: expanded XML nodes → entity IR with Bevy naming.
//!
//! Tags and attributes follow Bevy component/field names (`translation`,
//! `euler`, `half-size`, `base-color`, `metallic`, …). Tag matching is
//! case-insensitive; unknown attributes become warnings, unknown elements are
//! hard errors with the known-tag list.

pub mod spawn;
pub mod transform;

use anyhow::{Result, bail};

use crate::xml::{XmlNode, values};

/// Tags accepted inside a `<world>` root (lowercase canonical spellings).
pub const KNOWN_TAGS: &[&str] = &[
    "entity",
    "group",
    "cuboid",
    "sphere",
    "cylinder",
    "plane",
    "capsule",
    "pointlight",
    "ambientlight",
    "orbitcamera",
];

/// A parsed world: clear color, entity tree and non-fatal warnings.
#[derive(Debug, Clone, Default)]
pub struct ParsedWorld {
    pub clear_color: Option<[f32; 3]>,
    pub entities: Vec<EntitySpec>,
    pub warnings: Vec<String>,
}

/// Local transform of an entity (world transform comes from the hierarchy).
#[derive(Debug, Clone)]
pub struct TransformSpec {
    pub translation: [f32; 3],
    /// Degrees, XYZ order (`euler="x y z"`).
    pub euler_deg: Option<[f32; 3]>,
    /// Raw quaternion `x y z w` — wins over `euler` when both are present.
    pub rotation_quat: Option<[f32; 4]>,
    pub scale: [f32; 3],
}

impl Default for TransformSpec {
    fn default() -> Self {
        Self {
            translation: [0.0; 3],
            euler_deg: None,
            rotation_quat: None,
            scale: [1.0; 3],
        }
    }
}

/// Mesh primitive shapes, mirroring the `bevy::math::primitives` constructors.
#[derive(Debug, Clone)]
pub enum Shape {
    /// `Cuboid` — half extents per axis.
    Cuboid { half_size: [f32; 3] },
    /// `Sphere` — radius.
    Sphere { radius: f32 },
    /// `Cylinder` — half height + radius.
    Cylinder { half_height: f32, radius: f32 },
    /// `Plane3d` — half size on the XZ plane.
    Plane { half_size: [f32; 2] },
    /// `Capsule` — radius + half height (vertical).
    Capsule { radius: f32, half_height: f32 },
}

/// `StandardMaterial` overrides; unset fields use Bevy defaults.
#[derive(Debug, Clone, Default)]
pub struct MaterialSpec {
    pub base_color: Option<[f32; 3]>,
    pub metallic: Option<f32>,
    pub roughness: Option<f32>,
}

#[derive(Debug, Clone)]
pub enum EntityKind {
    /// Transform-only container (the `entity` and `group` tags).
    Group,
    Primitive {
        shape: Shape,
        material: MaterialSpec,
    },
    PointLight {
        color: Option<[f32; 3]>,
        intensity: Option<f32>,
        radius: Option<f32>,
        shadows: Option<bool>,
    },
    /// Applied as a world resource, not an entity.
    AmbientLight {
        color: Option<[f32; 3]>,
        brightness: Option<f32>,
    },
    OrbitCamera {
        /// Target entity resolved by `name` at runtime; `None` looks at the origin.
        target: Option<String>,
        distance: f32,
        height: f32,
        /// `Some` only when `pitch` was set explicitly (overrides `height`).
        pitch_deg: Option<f32>,
    },
}

/// A resolved recipe: everything needed to spawn one Bevy entity.
#[derive(Debug, Clone)]
pub struct EntitySpec {
    pub name: Option<String>,
    /// Runtime element tag (parsed; consumed by later phases).
    #[allow(dead_code)]
    pub tag: Option<String>,
    /// Script file reserved for the Luau runtime (parsed, not yet executed).
    #[allow(dead_code)]
    pub script: Option<String>,
    pub transform: TransformSpec,
    pub kind: EntityKind,
    pub children: Vec<EntitySpec>,
}

/// Parse the root of an expanded world.
pub fn parse_world(root_attrs: &[(String, String)], nodes: &[XmlNode]) -> Result<ParsedWorld> {
    let mut warnings = Vec::new();
    let mut clear_color = None;
    for (key, value) in root_attrs {
        match key.as_str() {
            "clear-color" => {
                clear_color = Some(values::parse_color(value, "<world clear-color>")?);
            }
            other => warnings.push(format!("<world>: ignored attribute `{other}`")),
        }
    }
    let entities = parse_entities(nodes, &mut warnings)?;
    let ambient_count = count_ambient_lights(&entities);
    if ambient_count > 1 {
        warnings.push(format!(
            "multiple <AmbientLight> elements ({ambient_count}) — the last one wins"
        ));
    }
    Ok(ParsedWorld {
        clear_color,
        entities,
        warnings,
    })
}

/// Ambient lights are applied as a world resource, so more than one is
/// almost always an authoring mistake — counted for the parse warning.
fn count_ambient_lights(entities: &[EntitySpec]) -> usize {
    entities
        .iter()
        .map(|spec| {
            let own = usize::from(matches!(spec.kind, EntityKind::AmbientLight { .. }));
            own + count_ambient_lights(&spec.children)
        })
        .sum()
}

fn parse_entities(nodes: &[XmlNode], warnings: &mut Vec<String>) -> Result<Vec<EntitySpec>> {
    nodes.iter().map(|n| parse_entity(n, warnings)).collect()
}

fn parse_entity(node: &XmlNode, warnings: &mut Vec<String>) -> Result<EntitySpec> {
    let lower = node.tag.to_ascii_lowercase();
    match lower.as_str() {
        "entity" | "group" => finish_group(node, warnings),
        "cuboid" | "sphere" | "cylinder" | "plane" | "capsule" => finish_primitive(node, warnings),
        "pointlight" => finish_point_light(node, warnings),
        "ambientlight" => finish_ambient_light(node, warnings),
        "orbitcamera" => finish_orbit_camera(node, warnings),
        "include" => bail!(
            "<{}>: <include> must be expanded before parsing (use xml::include::load_world)",
            node.tag
        ),
        "world" | "scene" => bail!("<{}>: world roots cannot be nested", node.tag),
        _ => bail!(
            "<{}>: unknown element — known tags: {}",
            node.tag,
            KNOWN_TAGS.join(", ")
        ),
    }
}

/// Attributes shared by every entity tag.
struct Common {
    name: Option<String>,
    tag: Option<String>,
    script: Option<String>,
    transform: TransformSpec,
}

/// Parse the universal attributes, returning the ones left for the kind parser.
fn parse_common(node: &XmlNode) -> Result<(Common, Vec<(String, String)>)> {
    let ctx = format!("<{}>", node.tag);
    let mut common = Common {
        name: None,
        tag: None,
        script: None,
        transform: TransformSpec::default(),
    };
    let mut rest = Vec::new();
    for (key, value) in &node.attrs {
        match key.as_str() {
            "name" => common.name = Some(value.clone()),
            "tag" => common.tag = Some(value.clone()),
            "script" => common.script = Some(value.clone()),
            "translation" => {
                common.transform.translation =
                    values::parse_vec3(value, &format!("{ctx} translation"))?;
            }
            "euler" => {
                common.transform.euler_deg =
                    Some(values::parse_vec3(value, &format!("{ctx} euler"))?);
            }
            "rotation" => {
                common.transform.rotation_quat =
                    Some(values::parse_vec4(value, &format!("{ctx} rotation"))?);
            }
            "scale" => {
                common.transform.scale = values::parse_vec3(value, &format!("{ctx} scale"))?;
            }
            other => rest.push((other.to_string(), value.clone())),
        }
    }
    Ok((common, rest))
}

fn warn_ignored(node: &XmlNode, rest: Vec<(String, String)>, warnings: &mut Vec<String>) {
    for (key, _) in rest {
        warnings.push(format!("<{}>: ignored attribute `{key}`", node.tag));
    }
}

fn finish_group(node: &XmlNode, warnings: &mut Vec<String>) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node)?;
    warn_ignored(node, rest, warnings);
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        kind: EntityKind::Group,
        children: parse_entities(&node.children, warnings)?,
    })
}

fn finish_primitive(node: &XmlNode, warnings: &mut Vec<String>) -> Result<EntitySpec> {
    let lower = node.tag.to_ascii_lowercase();
    let (common, rest) = parse_common(node)?;
    let ctx = format!("<{}>", node.tag);
    let mut shape = match lower.as_str() {
        "cuboid" => Shape::Cuboid {
            half_size: [0.5; 3],
        },
        "sphere" => Shape::Sphere { radius: 0.5 },
        "cylinder" => Shape::Cylinder {
            half_height: 0.5,
            radius: 0.5,
        },
        "plane" => Shape::Plane {
            half_size: [1.0; 2],
        },
        "capsule" => Shape::Capsule {
            radius: 0.5,
            half_height: 0.5,
        },
        _ => unreachable!("finish_primitive called for non-primitive"),
    };
    let mut material = MaterialSpec::default();
    for (key, value) in rest {
        let kctx = format!("{ctx} {key}");
        match key.as_str() {
            "half-size" => match &mut shape {
                Shape::Cuboid { half_size } => {
                    *half_size = values::parse_vec3(&value, &kctx)?;
                }
                Shape::Plane { half_size } => {
                    *half_size = values::parse_vec2(&value, &kctx)?;
                }
                _ => warnings.push(format!("{ctx}: `{key}` does not apply to this shape")),
            },
            "radius" => match &mut shape {
                Shape::Sphere { radius }
                | Shape::Cylinder { radius, .. }
                | Shape::Capsule { radius, .. } => *radius = values::parse_f32(&value, &kctx)?,
                _ => warnings.push(format!("{ctx}: `{key}` does not apply to this shape")),
            },
            "half-height" => match &mut shape {
                Shape::Cylinder { half_height, .. } | Shape::Capsule { half_height, .. } => {
                    *half_height = values::parse_f32(&value, &kctx)?;
                }
                _ => warnings.push(format!("{ctx}: `{key}` does not apply to this shape")),
            },
            "base-color" => material.base_color = Some(values::parse_color(&value, &kctx)?),
            "metallic" => material.metallic = Some(values::parse_f32(&value, &kctx)?),
            "roughness" => material.roughness = Some(values::parse_f32(&value, &kctx)?),
            other => warnings.push(format!("{ctx}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        kind: EntityKind::Primitive { shape, material },
        children: parse_entities(&node.children, warnings)?,
    })
}

fn finish_point_light(node: &XmlNode, warnings: &mut Vec<String>) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node)?;
    let ctx = format!("<{}>", node.tag);
    let mut color = None;
    let mut intensity = None;
    let mut radius = None;
    let mut shadows = None;
    for (key, value) in rest {
        let kctx = format!("{ctx} {key}");
        match key.as_str() {
            "color" => color = Some(values::parse_color(&value, &kctx)?),
            "intensity" => intensity = Some(values::parse_f32(&value, &kctx)?),
            "radius" => radius = Some(values::parse_f32(&value, &kctx)?),
            "shadows" => shadows = Some(values::parse_bool(&value, &kctx)?),
            other => warnings.push(format!("{ctx}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        kind: EntityKind::PointLight {
            color,
            intensity,
            radius,
            shadows,
        },
        children: parse_entities(&node.children, warnings)?,
    })
}

fn finish_ambient_light(node: &XmlNode, warnings: &mut Vec<String>) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node)?;
    let ctx = format!("<{}>", node.tag);
    let mut color = None;
    let mut brightness = None;
    for (key, value) in rest {
        let kctx = format!("{ctx} {key}");
        match key.as_str() {
            "color" => color = Some(values::parse_color(&value, &kctx)?),
            "brightness" => brightness = Some(values::parse_f32(&value, &kctx)?),
            other => warnings.push(format!("{ctx}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        kind: EntityKind::AmbientLight { color, brightness },
        children: parse_entities(&node.children, warnings)?,
    })
}

fn finish_orbit_camera(node: &XmlNode, warnings: &mut Vec<String>) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node)?;
    let ctx = format!("<{}>", node.tag);
    let mut kind = EntityKind::OrbitCamera {
        target: None,
        distance: 12.0,
        height: 4.0,
        pitch_deg: None,
    };
    for (key, value) in rest {
        let kctx = format!("{ctx} {key}");
        let EntityKind::OrbitCamera {
            target,
            distance,
            height,
            pitch_deg,
        } = &mut kind
        else {
            unreachable!("kind is OrbitCamera here");
        };
        match key.as_str() {
            "target" => *target = Some(value.trim().to_string()),
            "distance" => *distance = values::parse_f32(&value, &kctx)?,
            "height" => *height = values::parse_f32(&value, &kctx)?,
            "pitch" => *pitch_deg = Some(values::parse_f32(&value, &kctx)?),
            other => warnings.push(format!("{ctx}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        kind,
        children: parse_entities(&node.children, warnings)?,
    })
}

/// Headless summary used by `viber analyze` and tests.
#[derive(Debug, Default, PartialEq)]
pub struct WorldSummary {
    pub groups: usize,
    pub primitives: usize,
    pub point_lights: usize,
    pub cameras: usize,
    pub has_ambient: bool,
}

impl WorldSummary {
    /// Total spawned entities (ambient lights are resources, not entities).
    pub fn entities(&self) -> usize {
        self.groups + self.primitives + self.point_lights + self.cameras
    }
}

/// Walk the entity tree and count each kind.
pub fn summarize(world: &ParsedWorld) -> WorldSummary {
    fn walk(specs: &[EntitySpec], out: &mut WorldSummary) {
        for spec in specs {
            match &spec.kind {
                EntityKind::Group => out.groups += 1,
                EntityKind::Primitive { .. } => out.primitives += 1,
                EntityKind::PointLight { .. } => out.point_lights += 1,
                EntityKind::AmbientLight { .. } => out.has_ambient = true,
                EntityKind::OrbitCamera { .. } => out.cameras += 1,
            }
            walk(&spec.children, out);
        }
    }
    let mut out = WorldSummary::default();
    walk(&world.entities, &mut out);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(tag: &str, attrs: &[(&str, &str)]) -> XmlNode {
        XmlNode {
            tag: tag.to_string(),
            attrs: attrs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            children: vec![],
        }
    }

    fn parse_one(n: &XmlNode) -> Result<(EntitySpec, Vec<String>)> {
        let mut warnings = Vec::new();
        let spec = parse_entity(n, &mut warnings)?;
        Ok((spec, warnings))
    }

    #[test]
    fn test_entity_defaults() {
        let (spec, w) = parse_one(&node("Entity", &[])).unwrap();
        assert!(w.is_empty());
        assert!(matches!(spec.kind, EntityKind::Group));
        assert_eq!(spec.transform.scale, [1.0; 3]);
        assert_eq!(spec.transform.translation, [0.0; 3]);
    }

    #[test]
    fn test_group_tag_is_alias_of_entity() {
        let (a, _) = parse_one(&node("group", &[])).unwrap();
        let (b, _) = parse_one(&node("Entity", &[])).unwrap();
        assert!(matches!(a.kind, EntityKind::Group));
        assert!(matches!(b.kind, EntityKind::Group));
    }

    #[test]
    fn test_tag_matching_is_case_insensitive() {
        let (spec, _) = parse_one(&node("POINTLIGHT", &[])).unwrap();
        assert!(matches!(spec.kind, EntityKind::PointLight { .. }));
    }

    #[test]
    fn test_universal_attributes() {
        let (spec, _) = parse_one(&node(
            "Entity",
            &[("name", "hero"), ("tag", "player"), ("script", "hero.lua")],
        ))
        .unwrap();
        assert_eq!(spec.name.as_deref(), Some("hero"));
        assert_eq!(spec.tag.as_deref(), Some("player"));
        assert_eq!(spec.script.as_deref(), Some("hero.lua"));
    }

    #[test]
    fn test_translation_and_scale() {
        let (spec, _) =
            parse_one(&node("Entity", &[("translation", "1 2 3"), ("scale", "2")])).unwrap();
        assert_eq!(spec.transform.translation, [1.0, 2.0, 3.0]);
        assert_eq!(spec.transform.scale, [2.0; 3]);
    }

    #[test]
    fn test_bad_translation_is_error() {
        assert!(parse_one(&node("Entity", &[("translation", "1 2")])).is_err());
    }

    #[test]
    fn test_unknown_attribute_is_warning() {
        let (spec, w) = parse_one(&node("Entity", &[("pos", "0 0 0")])).unwrap();
        assert!(matches!(spec.kind, EntityKind::Group));
        assert_eq!(w, vec!["<Entity>: ignored attribute `pos`".to_string()]);
    }

    #[test]
    fn test_unknown_element_lists_known_tags() {
        let err = parse_one(&node("GameObject", &[])).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("unknown element"), "{msg}");
        assert!(msg.contains("entity"), "{msg}");
        assert!(msg.contains("orbitcamera"), "{msg}");
    }

    #[test]
    fn test_nested_world_and_include_are_errors() {
        assert!(parse_one(&node("world", &[])).is_err());
        assert!(parse_one(&node("Include", &[("src", "x.xml")])).is_err());
    }

    #[test]
    fn test_cuboid_half_size() {
        let (spec, _) = parse_one(&node("Cuboid", &[("half-size", "1 0.5 2")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Cuboid { half_size } if half_size == [1.0, 0.5, 2.0]));
    }

    #[test]
    fn test_cuboid_default_is_unit_cube() {
        let (spec, _) = parse_one(&node("Cuboid", &[])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Cuboid { half_size } if half_size == [0.5; 3]));
    }

    #[test]
    fn test_sphere_radius_broadcasts_single() {
        let (spec, _) = parse_one(&node("Sphere", &[("radius", "0.75")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Sphere { radius } if radius == 0.75));
    }

    #[test]
    fn test_cylinder_attributes() {
        let (spec, _) =
            parse_one(&node("Cylinder", &[("radius", "2"), ("half-height", "1")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        let Shape::Cylinder {
            half_height,
            radius,
        } = shape
        else {
            panic!("expected cylinder");
        };
        assert_eq!(half_height, 1.0);
        assert_eq!(radius, 2.0);
    }

    #[test]
    fn test_plane_half_size_two_components() {
        let (spec, _) = parse_one(&node("Plane", &[("half-size", "10 5")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Plane { half_size } if half_size == [10.0, 5.0]));
    }

    #[test]
    fn test_material_attributes() {
        let (spec, _) = parse_one(&node(
            "Cuboid",
            &[
                ("base-color", "#c0764a"),
                ("metallic", "0.8"),
                ("roughness", "0.2"),
            ],
        ))
        .unwrap();
        let EntityKind::Primitive { material, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(material.base_color.is_some());
        assert_eq!(material.metallic, Some(0.8));
        assert_eq!(material.roughness, Some(0.2));
    }

    #[test]
    fn test_shape_specific_attr_mismatch_warns() {
        let (_, w) = parse_one(&node("Sphere", &[("half-height", "1")])).unwrap();
        assert!(w.iter().any(|m| m.contains("does not apply")), "{w:?}");
    }

    #[test]
    fn test_point_light_overrides() {
        let (spec, _) = parse_one(&node(
            "PointLight",
            &[
                ("color", "#ffddaa"),
                ("intensity", "1200"),
                ("radius", "0.2"),
                ("shadows", "true"),
            ],
        ))
        .unwrap();
        let EntityKind::PointLight {
            color,
            intensity,
            radius,
            shadows,
        } = spec.kind
        else {
            panic!("expected point light");
        };
        assert!(color.is_some() && intensity == Some(1200.0));
        assert_eq!(radius, Some(0.2));
        assert_eq!(shadows, Some(true));
    }

    #[test]
    fn test_ambient_light_is_resource_kind() {
        let (spec, _) = parse_one(&node("AmbientLight", &[("brightness", "300")])).unwrap();
        assert!(matches!(spec.kind, EntityKind::AmbientLight { .. }));
    }

    #[test]
    fn test_multiple_ambient_lights_warn() {
        let world =
            parse_world(&[], &[node("AmbientLight", &[]), node("AmbientLight", &[])]).unwrap();
        assert!(
            world
                .warnings
                .iter()
                .any(|w| w.contains("multiple <AmbientLight>")),
            "{:?}",
            world.warnings
        );
    }

    #[test]
    fn test_single_ambient_light_does_not_warn() {
        let world = parse_world(&[], &[node("AmbientLight", &[])]).unwrap();
        assert!(world.warnings.is_empty(), "{:?}", world.warnings);
    }

    #[test]
    fn test_orbit_camera_defaults_and_overrides() {
        let (spec, _) = parse_one(&node(
            "OrbitCamera",
            &[("target", "props"), ("pitch", "30")],
        ))
        .unwrap();
        let EntityKind::OrbitCamera {
            target,
            distance,
            height,
            pitch_deg,
        } = spec.kind
        else {
            panic!("expected camera");
        };
        assert_eq!(target.as_deref(), Some("props"));
        assert_eq!(distance, 12.0);
        assert_eq!(height, 4.0);
        assert_eq!(pitch_deg, Some(30.0));
    }

    #[test]
    fn test_orbit_camera_pitch_defaults_to_none() {
        let (spec, _) = parse_one(&node("OrbitCamera", &[])).unwrap();
        let EntityKind::OrbitCamera { pitch_deg, .. } = spec.kind else {
            panic!("expected camera");
        };
        assert_eq!(pitch_deg, None);
    }

    #[test]
    fn test_group_nesting_children() {
        let mut parent = node("Group", &[("name", "props")]);
        parent.children = vec![node("Cuboid", &[]), node("Entity", &[("name", "kid")])];
        let (spec, _) = parse_one(&parent).unwrap();
        assert_eq!(spec.children.len(), 2);
        assert!(matches!(
            spec.children[0].kind,
            EntityKind::Primitive { .. }
        ));
    }

    #[test]
    fn test_parse_world_root_attrs_and_warnings() {
        let world = parse_world(
            &[
                ("clear-color".into(), "#87ceeb".into()),
                ("sky".into(), "#fff".into()),
            ],
            &[],
        )
        .unwrap();
        assert_eq!(
            world.clear_color,
            Some([135.0 / 255.0, 206.0 / 255.0, 235.0 / 255.0])
        );
        assert!(world.warnings.iter().any(|w| w.contains("`sky`")));
    }

    #[test]
    fn test_summarize_counts_kinds() {
        let mut group = node("Group", &[]);
        group.children = vec![
            node("Cuboid", &[]),
            node("PointLight", &[]),
            node("AmbientLight", &[]),
        ];
        let world = parse_world(&[], &[group, node("OrbitCamera", &[])]).unwrap();
        let summary = summarize(&world);
        assert_eq!(
            summary,
            WorldSummary {
                groups: 1,
                primitives: 1,
                point_lights: 1,
                cameras: 1,
                has_ambient: true,
            }
        );
        assert_eq!(summary.entities(), 4);
    }
}
