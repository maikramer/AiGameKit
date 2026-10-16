//! Inspector v2 — the typed property registry (Godot-style sections): transform
//! with SCRUBBING number inputs (translation/euler/scale through the canonical
//! `pose_op`, the same op the gizmo writes), physics (`collider`/`rigidbody`
//! component-strings), lights, material/shape and GLTF fields — every change is
//! ONE canonical op; a scrub previews live on the entities (the gizmo pattern).
//!
//! The widgets are feathers': `FeathersNumberInput` (native drag-to-scrub, X/Y/Z
//! sigils), `FeathersCheckbox`, `FeathersColorInput`, `FeathersSelect`. They
//! emit `ValueChange<T>` entity events; the observers here only BUFFER them
//! ([`PropertyEvents`]) and [`apply_property_changes`] (an exclusive system)
//! does the reading/writing — so live previews can also teleport Rapier bodies.

use std::collections::HashSet;

use bevy::feathers::controls::{
    ColorInputValue, FeathersCheckbox, FeathersColorInput, FeathersNumberInput, FeathersSelect,
    NumberInputPrecision, NumberInputStep, NumberInputValue, OptionIndex, list_rows_from_strings,
};
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::ui_widgets::ValueChange;
use viber::scene::doc::{NodeRef, SceneDoc};
use viber::scene::ops::EditOp;
use viber::scene::pose::{self, Pose, PoseParts};
use viber::xml::XmlNode;

use super::{PendingEdits, Selection};
use crate::ui::strings::{Bi, Lang, StrKey, attr_display, component_display, pick, t};
use crate::ui::tooltip::Tooltip;

// ── Registry ────────────────────────────────────────────────────────────────

/// What one control edits.
#[derive(Debug, Clone, PartialEq)]
pub enum FieldTarget {
    /// A pose axis: `part` + `axis` (0..2), committed via `pose_op`.
    Pose { part: PosePart, axis: usize },
    /// A plain XML attribute.
    Attr { key: &'static str },
    /// A member of a component-string attribute (`collider`, `rigidbody`).
    Component {
        attr: &'static str,
        key: &'static str,
    },
    /// The node's name (rename).
    Name,
    /// The node's direct text (`<UiText>100/100</UiText>`), via `set_text`.
    NodeText,
    /// O raio do núcleo do campo de biomas (via `terrain_biomes`, preserva as
    /// cunhas).
    TerrainBiomesCore,
    /// A largura de transição do campo de biomas (idem).
    TerrainBiomesBlend,
    /// A script-exported prop (`prop-<name>`, `--@export`): `default` shows when the
    /// instance has no value; `int` rounds the committed value; `range` clamps it
    /// (a runtime REJEITA fora do range — o editor não pode escrever o que o
    /// `parse_value`/`check_range` recusam).
    Prop {
        name: String,
        default: Option<String>,
        int: bool,
        range: Option<(f32, f32)>,
    },
    /// A free-text attribute whose key is only known at runtime (`groups`,
    /// `connect-<signal>` of a script's `--@signal`).
    Link { key: String },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PosePart {
    Translation,
    Euler,
    Scale,
}

/// The typed shape of one field.
#[derive(Debug, Clone)]
pub enum FieldKind {
    /// One scrubbing number input (the `tip` carries the unit).
    Number { step: f32 },
    /// A bool checkbox.
    Bool,
    /// A color picker (`#rrggbb` attrs).
    Color,
    /// A dropdown over the given options (value = the string).
    Enum { options: &'static [&'static str] },
    /// Free text (committed on Enter).
    Text,
    /// A dropdown over runtime options (script `enum(…)` props).
    Choice(Vec<String>),
}

/// One typed field of a section.
#[derive(Debug, Clone)]
pub struct FieldDef {
    pub target: FieldTarget,
    pub kind: FieldKind,
    /// The field's tooltip, bilingual (PT, EN).
    pub tip: Bi,
}

/// The inspector's typed sections (order matters).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SectionId {
    Transform,
    Physics,
    Light,
    Material,
    Shape,
    Gltf,
    Sky,
    DayCycle,
    Script,
    Biomes,
}

impl SectionId {
    fn title(self, lang: Lang) -> &'static str {
        match self {
            SectionId::Transform => t(lang, StrKey::SecTransform),
            SectionId::Physics => t(lang, StrKey::SecPhysics),
            SectionId::Light => t(lang, StrKey::SecLight),
            SectionId::Material => t(lang, StrKey::SecMaterial),
            SectionId::Shape => t(lang, StrKey::SecShape),
            SectionId::Gltf => t(lang, StrKey::SecGltf),
            SectionId::Sky => t(lang, StrKey::SecSky),
            SectionId::DayCycle => t(lang, StrKey::SecDayCycle),
            SectionId::Script => t(lang, StrKey::SecScript),
            SectionId::Biomes => t(lang, StrKey::SecBiomes),
        }
    }
}

const fn num(target: FieldTarget, step: f32, tip: Bi) -> FieldDef {
    FieldDef {
        target,
        kind: FieldKind::Number { step },
        tip,
    }
}

const fn bool_field(target: FieldTarget, tip: Bi) -> FieldDef {
    FieldDef {
        target,
        kind: FieldKind::Bool,
        tip,
    }
}

const fn text(target: FieldTarget, tip: Bi) -> FieldDef {
    FieldDef {
        target,
        kind: FieldKind::Text,
        tip,
    }
}

const fn color(target: FieldTarget, tip: Bi) -> FieldDef {
    FieldDef {
        target,
        kind: FieldKind::Color,
        tip,
    }
}

const fn enumeration(target: FieldTarget, options: &'static [&'static str], tip: Bi) -> FieldDef {
    FieldDef {
        target,
        kind: FieldKind::Enum { options },
        tip,
    }
}

/// Tags that spawn positioned 3D entities (the Transform section applies).
pub const TRANSFORMABLE_TAGS: &[&str] = &[
    "entity",
    "group",
    "cuboid",
    "box",
    "sphere",
    "cylinder",
    "plane",
    "capsule",
    "composition",
    "gltfscene",
    "playergltf",
    "pointlight",
    "directionallight",
    "staticspawner",
    "prototype",
];

const SKY_MODELS: &[&str] = &["analytic", "nishita"];

const COLLIDER_SHAPES: &[&str] = &[
    "auto",
    "none",
    "box",
    "sphere",
    "cylinder",
    "capsule",
    "trimesh",
    "precompute",
];
const BODY_KINDS: &[&str] = &["none", "fixed", "dynamic", "kinematic"];

/// The typed sections a tag shows. Keys covered here are HIDDEN from the
/// fallback attr list (see [`covered_keys`]).
pub fn sections_for(tag: &str) -> Vec<(SectionId, Vec<FieldDef>)> {
    let tag = tag.to_ascii_lowercase();
    let mut sections = Vec::new();
    let transformable = TRANSFORMABLE_TAGS.contains(&tag.as_str());
    if transformable {
        sections.push((
            SectionId::Transform,
            vec![
                num(
                    FieldTarget::Pose {
                        part: PosePart::Translation,
                        axis: 0,
                    },
                    0.1,
                    Bi("m", "m"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Translation,
                        axis: 1,
                    },
                    0.1,
                    Bi("m", "m"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Translation,
                        axis: 2,
                    },
                    0.1,
                    Bi("m", "m"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Euler,
                        axis: 0,
                    },
                    5.0,
                    Bi("°", "°"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Euler,
                        axis: 1,
                    },
                    5.0,
                    Bi("°", "°"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Euler,
                        axis: 2,
                    },
                    5.0,
                    Bi("°", "°"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Scale,
                        axis: 0,
                    },
                    0.1,
                    Bi("×", "×"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Scale,
                        axis: 1,
                    },
                    0.1,
                    Bi("×", "×"),
                ),
                num(
                    FieldTarget::Pose {
                        part: PosePart::Scale,
                        axis: 2,
                    },
                    0.1,
                    Bi("×", "×"),
                ),
            ],
        ));
    }
    let has_physics = transformable
        && tag != "playergltf"
        && !matches!(tag.as_str(), "pointlight" | "directionallight");
    if has_physics {
        sections.push((
            SectionId::Physics,
            vec![
                enumeration(
                    FieldTarget::Component {
                        attr: "collider",
                        key: "shape",
                    },
                    COLLIDER_SHAPES,
                    Bi("forma do collider", "collider shape"),
                ),
                text(
                    FieldTarget::Component {
                        attr: "collider",
                        key: "size",
                    },
                    Bi(
                        "tamanho total (m): 1 ou larg prof alt",
                        "total size (m): 1 value or w d h",
                    ),
                ),
                num(
                    FieldTarget::Component {
                        attr: "collider",
                        key: "radius",
                    },
                    0.1,
                    Bi("m", "m"),
                ),
                num(
                    FieldTarget::Component {
                        attr: "collider",
                        key: "half-height",
                    },
                    0.1,
                    Bi("m", "m"),
                ),
                text(
                    FieldTarget::Component {
                        attr: "collider",
                        key: "mesh-url",
                    },
                    Bi("GLB do trimesh/convex", "trimesh/convex GLB"),
                ),
                enumeration(
                    FieldTarget::Component {
                        attr: "rigidbody",
                        key: "type",
                    },
                    BODY_KINDS,
                    Bi("tipo de corpo", "body type"),
                ),
                num(
                    FieldTarget::Component {
                        attr: "rigidbody",
                        key: "gravity-scale",
                    },
                    0.1,
                    Bi("× gravidade", "× gravity"),
                ),
            ],
        ));
    }
    match tag.as_str() {
        "sky" => sections.push((
            SectionId::Sky,
            vec![
                enumeration(
                    FieldTarget::Attr { key: "model" },
                    SKY_MODELS,
                    Bi("modelo atmosférico", "atmospheric model"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "sun-elevation",
                    },
                    1.0,
                    Bi("° acima do horizonte", "° above the horizon"),
                ),
                num(
                    FieldTarget::Attr { key: "sun-azimuth" },
                    5.0,
                    Bi("° (bússola)", "° (compass)"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "sun-intensity",
                    },
                    0.1,
                    Bi("intensidade do sol", "sun intensity"),
                ),
                num(
                    FieldTarget::Attr { key: "rayleigh" },
                    0.1,
                    Bi("dispersão Rayleigh", "Rayleigh scattering"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "mie-coefficient",
                    },
                    0.001,
                    Bi("dispersão Mie", "Mie scattering"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "mie-directional-g",
                    },
                    0.05,
                    Bi("-0.99..0.99", "-0.99..0.99"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "cloud-coverage",
                    },
                    0.05,
                    Bi("0..1", "0..1"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "cloud-density",
                    },
                    0.05,
                    Bi("0..1", "0..1"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "cloud-elevation",
                    },
                    0.05,
                    Bi("0..1", "0..1"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "star-density",
                    },
                    0.1,
                    Bi("estrelas", "stars"),
                ),
                num(FieldTarget::Attr { key: "aurora" }, 0.1, Bi("0..2", "0..2")),
                num(
                    FieldTarget::Attr { key: "nebula" },
                    0.1,
                    Bi("0..1.5", "0..1.5"),
                ),
                num(
                    FieldTarget::Attr { key: "nishita-mie" },
                    0.1,
                    Bi("mie do Nishita", "Nishita mie"),
                ),
            ],
        )),
        "daycycle" => sections.push((
            SectionId::DayCycle,
            vec![
                num(
                    FieldTarget::Attr {
                        key: "minute-of-day",
                    },
                    15.0,
                    Bi("0..1440", "0..1440"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "minutes-per-real-second",
                    },
                    0.5,
                    Bi("min de jogo / s real", "game minutes per real second"),
                ),
                num(
                    FieldTarget::Attr { key: "dawn-minute" },
                    15.0,
                    Bi("0..1440", "0..1440"),
                ),
                num(
                    FieldTarget::Attr { key: "dusk-minute" },
                    15.0,
                    Bi("0..1440", "0..1440"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "ambient-day-intensity",
                    },
                    0.05,
                    Bi("luz ambiente de dia", "day ambient light"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "ambient-night-intensity",
                    },
                    0.05,
                    Bi("luz ambiente de noite", "night ambient light"),
                ),
                bool_field(
                    FieldTarget::Attr {
                        key: "drive-ambient",
                    },
                    Bi("ambient pela hora", "ambient driven by time of day"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "max-sun-elevation",
                    },
                    2.0,
                    Bi("° ao meio-dia", "° at noon"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "min-sun-elevation",
                    },
                    2.0,
                    Bi("° à meia-noite", "° at midnight"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "sun-azimuth-base",
                    },
                    5.0,
                    Bi("° (bússola base)", "° (base compass)"),
                ),
            ],
        )),
        "terrain" => sections.push((
            SectionId::Biomes,
            vec![
                num(
                    FieldTarget::TerrainBiomesCore,
                    5.0,
                    Bi("raio do núcleo neutro (m)", "neutral core radius (m)"),
                ),
                num(
                    FieldTarget::TerrainBiomesBlend,
                    10.0,
                    Bi("transição núcleo→cunhas (m)", "core→wedges blend (m)"),
                ),
            ],
        )),
        "pointlight" => sections.push((
            SectionId::Light,
            vec![
                color(FieldTarget::Attr { key: "color" }, Bi("cor", "color")),
                num(
                    FieldTarget::Attr { key: "intensity" },
                    50.0,
                    Bi("lúmens", "lumens"),
                ),
                num(
                    FieldTarget::Attr { key: "radius" },
                    0.5,
                    Bi("m de alcance", "range (m)"),
                ),
                bool_field(
                    FieldTarget::Attr { key: "shadows" },
                    Bi("sombras", "shadows"),
                ),
            ],
        )),
        "directionallight" => sections.push((
            SectionId::Light,
            vec![
                color(FieldTarget::Attr { key: "color" }, Bi("cor", "color")),
                num(
                    FieldTarget::Attr { key: "illuminance" },
                    500.0,
                    Bi("lux", "lux"),
                ),
                bool_field(
                    FieldTarget::Attr { key: "shadows" },
                    Bi("sombras", "shadows"),
                ),
            ],
        )),
        "ambientlight" => sections.push((
            SectionId::Light,
            vec![
                color(FieldTarget::Attr { key: "color" }, Bi("cor", "color")),
                num(
                    FieldTarget::Attr { key: "brightness" },
                    10.0,
                    Bi("brilho", "brightness"),
                ),
            ],
        )),
        _ => {}
    }
    if matches!(
        tag.as_str(),
        "cuboid" | "box" | "sphere" | "cylinder" | "plane" | "capsule"
    ) {
        sections.push((
            SectionId::Shape,
            vec![
                num(
                    FieldTarget::Attr { key: "half-size" },
                    0.1,
                    Bi("half-extents (m)", "half-extents (m)"),
                ),
                num(FieldTarget::Attr { key: "radius" }, 0.1, Bi("m", "m")),
                num(FieldTarget::Attr { key: "half-height" }, 0.1, Bi("m", "m")),
            ],
        ));
        sections.push((
            SectionId::Material,
            vec![
                color(
                    FieldTarget::Attr { key: "base-color" },
                    Bi("cor base", "base color"),
                ),
                num(
                    FieldTarget::Attr { key: "metallic" },
                    0.05,
                    Bi("0..1", "0..1"),
                ),
                num(
                    FieldTarget::Attr { key: "roughness" },
                    0.05,
                    Bi("0..1", "0..1"),
                ),
                num(
                    FieldTarget::Attr { key: "opacity" },
                    0.05,
                    Bi("0..1", "0..1"),
                ),
                color(
                    FieldTarget::Attr { key: "emissive" },
                    Bi("cor emissiva", "emissive color"),
                ),
                text(
                    FieldTarget::Attr { key: "texture" },
                    Bi("textura (path)", "texture (path)"),
                ),
                text(
                    FieldTarget::Attr { key: "normal-map" },
                    Bi("normal map (path)", "normal map (path)"),
                ),
            ],
        ));
    }
    if tag == "gltfscene" {
        sections.push((
            SectionId::Gltf,
            vec![
                text(
                    FieldTarget::Attr { key: "url" },
                    Bi("GLB principal", "main GLB"),
                ),
                text(
                    FieldTarget::Attr { key: "lod1-url" },
                    Bi("GLB do LOD1", "LOD1 GLB"),
                ),
                text(
                    FieldTarget::Attr { key: "lod2-url" },
                    Bi("GLB do LOD2", "LOD2 GLB"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "lod-threshold-near",
                    },
                    5.0,
                    Bi("m", "m"),
                ),
                num(
                    FieldTarget::Attr {
                        key: "lod-threshold-mid",
                    },
                    10.0,
                    Bi("m", "m"),
                ),
            ],
        ));
    }
    sections
}

/// The keys the typed sections own (hidden from the fallback list).
pub fn covered_keys(tag: &str) -> Vec<&'static str> {
    let mut keys: Vec<&'static str> = vec!["name"];
    // collider/rigidbody só são "cobertos" quando a secção Física existe —
    // noutros nós (PlayerGLTF, Use, luzes) ficavam INVISÍVEIS: nem campo
    // tipado nem linha no fallback
    let has_physics = sections_for(tag)
        .iter()
        .any(|(section, _)| *section == SectionId::Physics);
    if has_physics {
        keys.push("collider");
        keys.push("rigidbody");
    }
    for (_section, fields) in sections_for(tag) {
        for field in fields {
            match &field.target {
                FieldTarget::Attr { key } => keys.push(key),
                FieldTarget::Component { attr, .. } => keys.push(attr),
                FieldTarget::NodeText | FieldTarget::Prop { .. } | FieldTarget::Link { .. } => {}
                FieldTarget::TerrainBiomesCore | FieldTarget::TerrainBiomesBlend => {}
                FieldTarget::Pose { .. } => {
                    keys.extend([
                        "translation",
                        "euler",
                        "rotation",
                        "pos",
                        "transform",
                        "scale",
                    ]);
                }
                FieldTarget::Name => keys.push("name"),
            }
        }
    }
    keys.sort_unstable();
    keys.dedup();
    keys
}

// ── Component-string helpers (`collider`/`rigidbody`) ───────────────────────

/// Reads `key` from a `"k: v; k2: v2"` value (None when absent).
pub fn component_get(value: &str, key: &str) -> Option<String> {
    for pair in value.split(';') {
        let pair = pair.trim();
        if pair.is_empty() {
            continue;
        }
        let Some((k, v)) = pair.split_once(':') else {
            continue;
        };
        if k.trim().eq_ignore_ascii_case(key) {
            return Some(v.trim().to_owned());
        }
    }
    None
}

/// Rewrites `key` in a component-string, preserving every other key in order.
pub fn component_set(value: &str, key: &str, new: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut replaced = false;
    for pair in value.split(';') {
        let pair = pair.trim();
        if pair.is_empty() {
            continue;
        }
        let Some((k, v)) = pair.split_once(':') else {
            out.push(pair.to_owned());
            continue;
        };
        if k.trim().eq_ignore_ascii_case(key) {
            replaced = true;
            out.push(format!("{key}: {new}"));
        } else {
            out.push(format!("{}: {}", k.trim(), v.trim()));
        }
    }
    if !replaced {
        out.push(format!("{key}: {new}"));
    }
    out.join("; ")
}

// ── Panel state + rendering ─────────────────────────────────────────────────

/// Which sections are expanded (default: Transform only).
#[derive(Resource, Debug, Default)]
pub struct SectionOpen(pub HashSet<SectionId>);

impl SectionOpen {
    pub fn with_defaults() -> Self {
        Self([SectionId::Transform, SectionId::Script].into())
    }
}

/// A section header owns its body's visibility (the disclosure toggles it).
#[derive(Component, Debug)]
pub struct SectionBody {
    pub body: Entity,
    pub section: SectionId,
}

/// The buffered widget events (observers write; the exclusive system drains).
#[derive(Debug, Clone)]
pub enum PropertyEvent {
    Number {
        source: Entity,
        value: f32,
        is_final: bool,
    },
    Bool {
        source: Entity,
        value: bool,
    },
    Color {
        source: Entity,
        value: Color,
    },
    Option {
        source: Entity,
        row: Entity,
    },
}

#[derive(Resource, Debug, Default)]
pub struct PropertyEvents(pub Vec<PropertyEvent>);

/// The options of a [`FieldKind::Choice`] dropdown (dynamic, unlike the static enums).
#[derive(Component, Debug, Clone)]
pub struct FieldChoices(pub Vec<String>);

/// Marker on a typed control: maps `ValueChange.source` back to the field.
#[derive(Component, Debug, Clone)]
pub struct FieldControl(pub FieldTarget);

/// Marker on a text row (committed on Enter). `initial` is injected on the
/// frame after spawn — feathers' TextInput keeps its own buffer, so writing
/// the `Text` component alone does not fill it.
#[derive(Component, Debug, Clone)]
pub struct FieldText {
    pub target: FieldTarget,
    pub initial: String,
    pub prefilled: bool,
}

/// The property panel plugin: registers the widget observers.
pub struct PropertyPlugin;

impl Plugin for PropertyPlugin {
    fn build(&self, app: &mut App) {
        app.insert_resource(PropertyEvents::default())
            .insert_resource(SectionOpen::with_defaults())
            .add_observer(
                |event: On<ValueChange<f32>>, mut events: ResMut<PropertyEvents>| {
                    events.0.push(PropertyEvent::Number {
                        source: event.source,
                        value: event.value,
                        is_final: event.is_final,
                    });
                },
            )
            .add_observer(
                |event: On<ValueChange<bool>>, mut events: ResMut<PropertyEvents>| {
                    events.0.push(PropertyEvent::Bool {
                        source: event.source,
                        value: event.value,
                    });
                },
            )
            // Os widgets do feathers NÃO se atualizam sozinhos: emitem
            // `ValueChange` e esperam que alguém escreva o `Checked`. Os campos do
            // documento (FieldControl) refletem-no ao reconstruir o painel; todos os
            // outros toggles (disclosure das secções, checkboxes da Vista…) mudam
            // aqui — sem isto as secções nunca colapsavam.
            .add_observer(self_update_toggle)
            .add_observer(
                |event: On<ValueChange<Color>>, mut events: ResMut<PropertyEvents>| {
                    events.0.push(PropertyEvent::Color {
                        source: event.source,
                        value: event.value,
                    });
                },
            )
            .add_observer(
                |event: On<ValueChange<Entity>>, mut events: ResMut<PropertyEvents>| {
                    events.0.push(PropertyEvent::Option {
                        source: event.source,
                        row: event.value,
                    });
                },
            );
    }
}

/// Os widgets do feathers NÃO se atualizam sozinhos: emitem `ValueChange` e esperam que
/// alguém escreva o `Checked`. Os campos do documento (FieldControl) refletem-no ao
/// reconstruir o painel; todos os outros toggles (disclosure das secções, checkboxes da
/// Vista…) mudam aqui. Uma secção abre/fecha JÁ neste observer: retirar o `Checked` não
/// é um `Changed`, e o [`sync_section_toggles`] nunca via o fecho.
fn self_update_toggle(
    event: On<ValueChange<bool>>,
    fields: Query<(), With<FieldControl>>,
    parents: Query<&ChildOf>,
    sections: Query<&SectionBody>,
    mut nodes: Query<&mut Node>,
    mut open: ResMut<SectionOpen>,
    mut commands: Commands,
) {
    if fields.contains(event.source) {
        return;
    }
    let Ok(mut entity) = commands.get_entity(event.source) else {
        return;
    };
    if event.value {
        entity.insert(bevy::ui::Checked);
    } else {
        entity.remove::<bevy::ui::Checked>();
    }
    let header = std::iter::once(event.source)
        .chain(parents.iter_ancestors(event.source))
        .find(|candidate| sections.contains(*candidate));
    let Some(info) = header.and_then(|header| sections.get(header).ok()) else {
        return;
    };
    if let Ok(mut body) = nodes.get_mut(info.body) {
        body.display = if event.value {
            Display::Flex
        } else {
            Display::None
        };
    }
    if event.value {
        open.0.insert(info.section);
    } else {
        open.0.remove(&info.section);
    }
}

/// Renders the typed sections of the selected node under `parent`.
pub fn render_sections(
    commands: &mut Commands,
    parent: Entity,
    doc: &SceneDoc,
    node: &NodeRef,
    open: &SectionOpen,
    lang: Lang,
) {
    let Some(xml) = doc.node(node).cloned() else {
        return;
    };
    // o TEXTO direto do nó (`<UiText>100/100</UiText>`), editável
    if !xml.text.trim().is_empty() {
        let text_field = FieldDef {
            target: FieldTarget::NodeText,
            kind: FieldKind::Text,
            tip: Bi("texto do nó", "node text"),
        };
        spawn_field(commands, parent, doc, node, &xml, &text_field, lang);
    }
    // renomear: o `name` em cima de tudo (UI usa `id`, fica no fallback)
    if xml.attr("name").is_some() || !viber_is_ui_tag(&xml.tag) {
        let rename = FieldDef {
            target: FieldTarget::Name,
            kind: FieldKind::Text,
            tip: Bi("nome do nó", "node name"),
        };
        spawn_field(commands, parent, doc, node, &xml, &rename, lang);
    }
    // Script primeiro (como as "Script Variables" do Godot): é o que dá identidade à
    // instância — falas, loot, velocidades.
    super::script_props::render(commands, parent, doc, node, &xml, open, lang);
    for (section, fields) in sections_for(&xml.tag) {
        let body = spawn_section(
            commands,
            parent,
            section,
            open,
            section.title(lang).to_owned(),
        );
        for field in fields {
            if !field_applies(&xml, &field.target) {
                continue;
            }
            spawn_field(commands, body, doc, node, &xml, &field, lang);
        }
    }
}

/// A collapsible section: the header (disclosure + title) and its body, which is returned
/// for the caller to fill. `title` is usually [`SectionId::title`].
pub fn spawn_section(
    commands: &mut Commands,
    parent: Entity,
    section: SectionId,
    open: &SectionOpen,
    title: String,
) -> Entity {
    {
        let expanded = open.0.contains(&section);
        // HEADER primeiro, corpo depois (a ordem de spawn é a ordem visual).
        let header = commands
            .spawn((
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                    margin: UiRect::top(px(6)),
                    padding: UiRect::axes(px(6), px(4)),
                    border_radius: BorderRadius::all(px(4)),
                    ..default()
                },
                BackgroundColor(crate::ui::theme::BG_HEADER),
                ChildOf(parent),
            ))
            .id();
        let body = commands
            .spawn((
                Node {
                    flex_direction: FlexDirection::Column,
                    row_gap: px(2),
                    display: if expanded {
                        Display::Flex
                    } else {
                        Display::None
                    },
                    padding: UiRect::new(px(8), px(2), px(4), px(2)),
                    ..default()
                },
                ChildOf(parent),
            ))
            .id();
        // Disclosure (chevron) — o estado inicial segue `SectionOpen`.
        let mut toggle = commands.spawn_scene(bsn! {
            @bevy::feathers::controls::FeathersDisclosureToggle
        });
        toggle.insert(ChildOf(header));
        if expanded {
            toggle.insert(bevy::ui::Checked);
        }
        commands
            .spawn_scene(bsn! {
                @caption(title)
            })
            .insert((
                TextColor(crate::ui::theme::TEXT),
                Pickable::IGNORE,
                ChildOf(header),
            ));
        commands
            .entity(header)
            .insert(SectionBody { body, section });
        body
    }
}

/// Spawns one field row with the right control for its kind; returns the row (callers
/// may append to it, e.g. a reset button).
pub fn spawn_field(
    commands: &mut Commands,
    parent: Entity,
    doc: &SceneDoc,
    _node: &NodeRef,
    xml: &XmlNode,
    field: &FieldDef,
    lang: Lang,
) -> Entity {
    let row = commands
        .spawn((
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
                padding: UiRect::vertical(px(1)),
                ..default()
            },
            Tooltip(field.tip.get(lang)),
            ChildOf(parent),
        ))
        .id();
    let label = field_label(lang, &field.target);
    commands
        .spawn_scene(bsn! {
            @caption(label)
        })
        .insert((
            TextColor(crate::ui::theme::TEXT_DIM),
            Node {
                width: px(132),
                flex_shrink: 0.0,
                ..default()
            },
            Pickable::IGNORE,
            ChildOf(row),
        ));
    match &field.kind {
        FieldKind::Number { step } => {
            let mut current = field_number(xml, &field.target).unwrap_or(0.0);
            if matches!(
                field.target,
                FieldTarget::TerrainBiomesCore | FieldTarget::TerrainBiomesBlend
            ) && let Some(terrain) = doc.terrain.as_ref()
            {
                current = match field.target {
                    FieldTarget::TerrainBiomesCore => terrain.meta.biomes.core,
                    FieldTarget::TerrainBiomesBlend => terrain.meta.biomes.blend,
                    _ => current,
                };
            }
            commands
                .spawn_scene(bsn! {
                    @FeathersNumberInput {
                        @label_text: {axis_label(&field.target)},
                        @sigil_color: {axis_token(&field.target)},
                    }
                    Node { flex_grow: 1.0 }
                })
                .insert((
                    NumberInputValue::F32(current),
                    NumberInputPrecision(precision_for(&field.target)),
                    NumberInputStep(*step as f64),
                    FieldControl(field.target.clone()),
                    ChildOf(row),
                ));
        }
        FieldKind::Bool => {
            let current = field_bool(xml, &field.target);
            let mut toggle = commands.spawn_scene(bsn! { @FeathersCheckbox });
            toggle.insert((FieldControl(field.target.clone()), ChildOf(row)));
            if current {
                toggle.insert(bevy::ui::Checked);
            }
        }
        FieldKind::Color => {
            let current = field_color(xml, &field.target);
            commands
                .spawn_scene(bsn! { @FeathersColorInput Node { width: px(120) } })
                .insert((
                    ColorInputValue(current),
                    FieldControl(field.target.clone()),
                    ChildOf(row),
                ));
        }
        FieldKind::Enum { options } => {
            let current = field_enum(xml, &field.target, options);
            let list = list_rows_from_strings(options.iter().copied(), current);
            commands
                .spawn_scene(bsn! {
                    @FeathersSelect {
                        @options: {list},
                        @max_visible: 8usize,
                    }
                    Node { flex_grow: 1.0 }
                })
                .insert((FieldControl(field.target.clone()), ChildOf(row)));
        }
        FieldKind::Text => {
            let current = field_text(xml, &field.target);
            let container = commands
                .spawn_scene(bsn! {
                    @bevy::feathers::controls::FeathersTextInputContainer
                    Node { flex_grow: 1.0 }
                })
                .insert((
                    FieldText {
                        target: field.target.clone(),
                        initial: current.clone(),
                        prefilled: false,
                    },
                    ChildOf(row),
                ))
                .id();
            commands
                .spawn_scene(bsn! {
                    @bevy::feathers::controls::FeathersTextInput
                })
                .insert((Text::new(current), ChildOf(container)));
        }
        FieldKind::Choice(options) => {
            let current = field_enum(
                xml,
                &field.target,
                &options.iter().map(String::as_str).collect::<Vec<_>>(),
            );
            let list = list_rows_from_strings(options.iter().map(String::as_str), current);
            commands
                .spawn_scene(bsn! {
                    @FeathersSelect {
                        @options: {list},
                        @max_visible: 8usize,
                    }
                    Node { flex_grow: 1.0 }
                })
                .insert((
                    FieldControl(field.target.clone()),
                    FieldChoices(options.clone()),
                    ChildOf(row),
                ));
        }
    }
    row
}

fn axis_label(target: &FieldTarget) -> Option<&'static str> {
    match target {
        FieldTarget::Pose { axis, .. } => Some(["X", "Y", "Z"][(*axis).min(2)]),
        _ => None,
    }
}

fn axis_token(target: &FieldTarget) -> bevy::feathers::theme::ThemeToken {
    use bevy::feathers::tokens as theme;
    match target {
        FieldTarget::Pose { axis: 0, .. } => theme::TEXT_INPUT_X_AXIS,
        FieldTarget::Pose { axis: 1, .. } => theme::TEXT_INPUT_Y_AXIS,
        FieldTarget::Pose { axis: 2, .. } => theme::TEXT_INPUT_Z_AXIS,
        _ => theme::TEXT_INPUT_LABEL_BG,
    }
}

fn precision_for(target: &FieldTarget) -> i32 {
    match target {
        FieldTarget::Pose {
            part: PosePart::Euler,
            ..
        } => 1,
        FieldTarget::Pose { .. } => 2,
        FieldTarget::Prop { int: true, .. } => 0,
        _ => 3,
    }
}

/// The field's label, friendly and bilingual: pose parts and XML keys go through
/// [`attr_display`], component members through [`component_display`]. The label
/// width in [`spawn_field`] is tuned for ~20 characters.
fn field_label(lang: Lang, target: &FieldTarget) -> String {
    let axis = match target {
        FieldTarget::Pose { axis, .. } => ["X", "Y", "Z"][(*axis).min(2)].to_owned(),
        _ => String::new(),
    };
    let name = match target {
        FieldTarget::Pose {
            part: PosePart::Translation,
            ..
        } => attr_display(lang, "translation"),
        FieldTarget::Pose {
            part: PosePart::Euler,
            ..
        } => attr_display(lang, "euler"),
        FieldTarget::Pose {
            part: PosePart::Scale,
            ..
        } => attr_display(lang, "scale"),
        FieldTarget::Attr { key } => return attr_display(lang, key).to_owned(),
        FieldTarget::Component { attr, key } => return component_display(lang, attr, key),
        FieldTarget::Name => return attr_display(lang, "name").to_owned(),
        FieldTarget::NodeText => return attr_display(lang, "text").to_owned(),
        FieldTarget::TerrainBiomesCore => {
            return pick(lang, "Biomas: núcleo", "Biomes: core").to_owned();
        }
        FieldTarget::TerrainBiomesBlend => {
            return pick(lang, "Biomas: transição", "Biomes: blend").to_owned();
        }
        FieldTarget::Prop { name, .. } => return name.clone(),
        FieldTarget::Link { key } => {
            return match viber::luau::exports::connect_signal(key) {
                Some(signal) => format!("{signal} →"),
                None => key.clone(),
            };
        }
    };
    if axis.is_empty() {
        name.to_owned()
    } else {
        format!("{name} {axis}")
    }
}

// ── Reading current values ──────────────────────────────────────────────────

fn attr_value(xml: &XmlNode, target: &FieldTarget) -> Option<String> {
    match target {
        FieldTarget::Attr { key } => xml.attr(key).map(str::to_owned),
        FieldTarget::Name => xml.attr("name").map(str::to_owned),
        FieldTarget::Component { attr, key } => {
            xml.attr(attr).and_then(|value| component_get(value, key))
        }
        FieldTarget::NodeText => Some(xml.text.clone()),
        FieldTarget::TerrainBiomesCore | FieldTarget::TerrainBiomesBlend => None,
        FieldTarget::Pose { .. } => None,
        FieldTarget::Link { key } => xml.attr(key).map(str::to_owned),
        FieldTarget::Prop { name, default, .. } => xml
            .attr(&viber::luau::exports::prop_attr(name))
            .map(str::to_owned)
            .or_else(|| default.clone()),
    }
}

pub fn field_number(xml: &XmlNode, target: &FieldTarget) -> Option<f32> {
    // -0.0 lê-se "-0" no NumberInput; +0.0 é o canónico
    fn clean(value: f32) -> f32 {
        value + 0.0
    }
    match target {
        FieldTarget::Pose { part, axis } => {
            let axis = (*axis).min(2);
            let pose = pose::read_pose(xml).ok()?;
            match part {
                PosePart::Translation => Some(clean(pose.translation[axis])),
                PosePart::Euler => {
                    let (x, y, z) = pose.rotation.to_euler(bevy::math::EulerRot::XYZ);
                    Some(clean([x, y, z][axis].to_degrees()))
                }
                PosePart::Scale => Some(clean(pose.scale[axis])),
            }
        }
        _ => attr_value(xml, target)
            .and_then(|value| value.trim().parse::<f32>().ok())
            .map(clean),
    }
}

pub fn field_bool(xml: &XmlNode, target: &FieldTarget) -> bool {
    attr_value(xml, target)
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "true" | "1" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

pub fn field_color(xml: &XmlNode, target: &FieldTarget) -> Color {
    attr_value(xml, target)
        .and_then(|value| viber::xml::values::parse_color(&value, "inspector").ok())
        .map(|[r, g, b]| Color::srgb(r, g, b))
        .unwrap_or(Color::WHITE)
}

pub fn field_text(xml: &XmlNode, target: &FieldTarget) -> String {
    attr_value(xml, target).unwrap_or_default()
}

pub fn field_enum(xml: &XmlNode, target: &FieldTarget, options: &[&str]) -> Option<usize> {
    let value = attr_value(xml, target)?.to_ascii_lowercase();
    options
        .iter()
        .position(|option| option.eq_ignore_ascii_case(&value))
}

// ── Applying changes (the exclusive-system side) ────────────────────────────

/// Drains the buffered widget events: live-preview while scrubbing, ONE op on
/// the final value. Text fields commit on Enter ([`commit_text_fields`]).
pub fn apply_property_changes(world: &mut World) {
    let events = std::mem::take(&mut world.resource_mut::<PropertyEvents>().0);
    if events.is_empty() {
        return;
    }
    for event in events {
        let target = match &event {
            PropertyEvent::Number { source, .. }
            | PropertyEvent::Bool { source, .. }
            | PropertyEvent::Color { source, .. }
            | PropertyEvent::Option { source, .. } => {
                let Some(control) = world.get::<FieldControl>(*source) else {
                    continue;
                };
                control.0.clone()
            }
        };
        match event {
            PropertyEvent::Number {
                value, is_final, ..
            } => {
                apply_number(world, &target, value, is_final);
            }
            PropertyEvent::Bool { value, .. } => {
                apply_value(world, &target, if value { "true" } else { "false" });
            }
            PropertyEvent::Color { value, .. } => {
                apply_value(world, &target, &color_to_xml(&value));
            }
            PropertyEvent::Option { source, row } => {
                let index = world.get::<OptionIndex>(row).map(|index| index.0);
                if let Some(choices) = world.get::<FieldChoices>(source) {
                    if let Some(option) = index.and_then(|index| choices.0.get(index)).cloned() {
                        apply_value(world, &target, &option);
                    }
                    continue;
                }
                let options = enum_options_of(&target);
                if let (Some(index), Some(options)) = (index, options)
                    && let Some(option) = options.get(index)
                {
                    apply_value(world, &target, option);
                }
            }
        }
    }
}

fn enum_options_of(target: &FieldTarget) -> Option<&'static [&'static str]> {
    match target {
        FieldTarget::Component {
            attr: "collider",
            key: "shape",
        } => Some(COLLIDER_SHAPES),
        FieldTarget::Component {
            attr: "rigidbody",
            key: "type",
        } => Some(BODY_KINDS),
        _ => None,
    }
}

/// `Color` → `#rrggbb` (the XML canonical form).
pub fn color_to_xml(color: &Color) -> String {
    let Srgba {
        red, green, blue, ..
    } = color.to_srgba();
    format!(
        "#{:02x}{:02x}{:02x}",
        (red * 255.0).round() as u8,
        (green * 255.0).round() as u8,
        (blue * 255.0).round() as u8
    )
}

/// A number changed: scrub previews on the live entities, the final value
/// commits one op (pose via `pose_op`, everything else `Set`).
fn apply_number(world: &mut World, target: &FieldTarget, value: f32, is_final: bool) {
    let Some(node) = world.resource::<Selection>().primary().cloned() else {
        return;
    };
    let Some(xml) = world.resource::<SceneDoc>().node(&node).cloned() else {
        return;
    };
    let selector = world.resource::<SceneDoc>().selector(&node);
    let lang = *world.resource::<Lang>();
    let FieldTarget::Pose { part, axis } = target else {
        // Non-pose attrs have no live preview: committing on every scrub tick
        // would rebuild the panel mid-drag (killing the scrub) and would spam
        // one undo step per tick. Commit only on the final event.
        if !is_final {
            return;
        }
        // Props `int`/range: a runtime REJEITA `7.5` num `int` e valores fora
        // do range (`check_range`) — o editor arredonda e clampeia antes.
        let value = if let FieldTarget::Prop { int, range, .. } = target {
            let mut value = value;
            if *int {
                value = value.round();
            }
            if let Some((min, max)) = range {
                value = value.clamp(*min, *max);
            }
            value
        } else {
            value
        };
        apply_value(world, target, &pose::format_num(value));
        return;
    };
    let axis = (*axis).min(2);
    let Ok(mut current) = pose::read_pose(&xml) else {
        return;
    };
    match part {
        PosePart::Translation => current.translation[axis] = value,
        PosePart::Euler => {
            let (x, y, z) = current.rotation.to_euler(bevy::math::EulerRot::XYZ);
            let mut degrees = [x.to_degrees(), y.to_degrees(), z.to_degrees()];
            degrees[axis] = value;
            current.rotation = Quat::from_euler(
                bevy::math::EulerRot::XYZ,
                degrees[0].to_radians(),
                degrees[1].to_radians(),
                degrees[2].to_radians(),
            );
        }
        PosePart::Scale => current.scale[axis] = value,
    }
    if !is_final {
        preview_pose(world, &node, &current);
        return;
    }
    let parts = match part {
        PosePart::Translation => PoseParts {
            translation: true,
            rotation: false,
            scale: false,
        },
        PosePart::Euler => PoseParts {
            translation: false,
            rotation: true,
            scale: false,
        },
        PosePart::Scale => PoseParts {
            translation: false,
            rotation: false,
            scale: true,
        },
    };
    match pose::pose_op(&selector, &xml, current, parts) {
        Ok(op) => world
            .resource_mut::<PendingEdits>()
            .push(t(lang, StrKey::EditTransform).to_owned(), vec![op]),
        Err(error) => warn!("inspector: pose op falhou: {error:#}"),
    }
}

/// Writes a plain attr / component member / name as ONE op.
fn apply_value(world: &mut World, target: &FieldTarget, value: &str) {
    let lang = *world.resource::<Lang>();
    // BIOMAS: core/blend via `terrain_biomes` preservando as cunhas
    if matches!(
        target,
        FieldTarget::TerrainBiomesCore | FieldTarget::TerrainBiomesBlend
    ) {
        let Some(parsed) = value.parse::<f32>().ok() else {
            return;
        };
        let terrain = world
            .resource::<SceneDoc>()
            .terrain
            .as_ref()
            .map(|terrain| terrain.meta.biomes.clone());
        let Some(terrain) = terrain else {
            return;
        };
        let op = EditOp::TerrainBiomes {
            core: if matches!(target, FieldTarget::TerrainBiomesCore) {
                parsed
            } else {
                terrain.core
            },
            blend: if matches!(target, FieldTarget::TerrainBiomesBlend) {
                parsed
            } else {
                terrain.blend
            },
            wedges: terrain
                .wedges
                .iter()
                .map(|w| (w.dir.x, w.dir.y, w.biome.name().to_owned()))
                .collect(),
        };
        world
            .resource_mut::<PendingEdits>()
            .push(pick(lang, "biomas", "biomes"), vec![op]);
        return;
    }
    let Some(node) = world.resource::<Selection>().primary().cloned() else {
        return;
    };
    let Some(xml) = world.resource::<SceneDoc>().node(&node).cloned() else {
        return;
    };
    let selector = world.resource::<SceneDoc>().selector(&node);
    let (key, new_value): (String, Option<String>) = match target {
        FieldTarget::Name => ("name".into(), (!value.is_empty()).then(|| value.to_owned())),
        FieldTarget::Attr { key } => (
            (*key).to_owned(),
            (!value.is_empty()).then(|| value.to_owned()),
        ),
        FieldTarget::Component { attr, key } => {
            let merged = component_set(xml.attr(attr).unwrap_or(""), key, value);
            ((*attr).to_owned(), Some(merged))
        }
        FieldTarget::Prop { name, .. } => (
            viber::luau::exports::prop_attr(name),
            (!value.is_empty()).then(|| value.to_owned()),
        ),
        FieldTarget::Link { key } => (key.clone(), (!value.is_empty()).then(|| value.to_owned())),
        FieldTarget::Pose { .. }
        | FieldTarget::TerrainBiomesCore
        | FieldTarget::TerrainBiomesBlend => return,
        FieldTarget::NodeText => {
            world.resource_mut::<PendingEdits>().push(
                pick(lang, "texto", "text"),
                vec![EditOp::SetText {
                    node: selector,
                    text: value.to_owned(),
                }],
            );
            return;
        }
    };
    world.resource_mut::<PendingEdits>().push(
        format!("{} = {value}", attr_display(lang, &key)),
        vec![EditOp::Set {
            node: selector,
            attrs: [(key, new_value)].into_iter().collect(),
        }],
    );
}

/// Scrub preview: patch the live entities' local transform (and teleport their
/// Rapier bodies — the writeback would otherwise drag them back).
fn preview_pose(world: &mut World, node: &NodeRef, pose: &Pose) {
    let origin = world.resource::<SceneDoc>().origin_of(node);
    let local = Transform {
        translation: pose.translation,
        rotation: pose.rotation,
        scale: pose.scale,
    };
    let mut query = world.query_filtered::<(Entity, &viber::scene::SceneSource), With<Transform>>();
    let entities: Vec<Entity> = query
        .iter(world)
        .filter(|(_, source)| *source.0 == origin)
        .map(|(entity, _)| entity)
        .collect();
    for entity in entities {
        if let Some(mut transform) = world.get_mut::<Transform>(entity) {
            *transform = local;
        }
        viber::scene::sync::teleport_body(world, entity, local);
    }
}

/// Injects the initial value into freshly spawned text fields (the same
/// `TextEdit::Insert` flow the fallback inspector uses).
pub fn prefill_text_fields(
    mut fields: Query<(&mut FieldText, &Children), Changed<FieldText>>,
    mut inputs: Query<&mut Text>,
    mut editable: Query<&mut bevy::text::EditableText>,
) {
    for (mut field, children) in &mut fields {
        if field.prefilled {
            continue;
        }
        field.prefilled = true;
        for child in children.iter() {
            let Ok(mut text) = inputs.get_mut(child) else {
                continue;
            };
            if text.0.is_empty() && !field.initial.is_empty() {
                text.0 = field.initial.clone();
            }
            if let Ok(mut editable) = editable.get_mut(child) {
                editable.queue_edit(bevy::text::TextEdit::Insert(field.initial.as_str().into()));
            }
        }
    }
}

/// Commits a typed text field on Enter.
pub fn commit_text_fields(world: &mut World) {
    let Some(focused) = world
        .get_resource::<bevy::input_focus::InputFocus>()
        .and_then(|focus| focus.get())
    else {
        return;
    };
    if !world
        .resource::<ButtonInput<KeyCode>>()
        .just_pressed(KeyCode::Enter)
    {
        return;
    }
    let Some(text) = world.get::<Text>(focused) else {
        return;
    };
    let value = text.0.clone();
    let mut current = focused;
    let target = loop {
        if let Some(field) = world.get::<FieldText>(current) {
            // Enter sem alteração: NÃO empurra um `Set` idêntico (era um
            // passo de undo fantasma por Enter)
            if value.trim() == field.initial.trim() {
                if let Some(mut focus) = world.get_resource_mut::<bevy::input_focus::InputFocus>() {
                    focus.clear();
                }
                return;
            }
            break field.target.clone();
        }
        match world.get::<ChildOf>(current).map(|child| child.parent()) {
            Some(parent) => current = parent,
            None => return,
        }
    };
    apply_value(world, &target, value.trim());
    if let Some(mut focus) = world.get_resource_mut::<bevy::input_focus::InputFocus>() {
        focus.clear();
    }
}

/// Whether a field's control makes sense for the node's current state (the
/// collider params are per-shape: `size` só faz sentido num `box`, etc).
fn field_applies(xml: &XmlNode, target: &FieldTarget) -> bool {
    let shape = xml
        .attr("collider")
        .and_then(|value| component_get(value, "shape"))
        .or_else(|| {
            // forma nua: `collider="auto"` / `collider="box"`
            xml.attr("collider")
                .filter(|value| !value.contains(':'))
                .map(|value| value.trim().to_owned())
        })
        .unwrap_or_default()
        .to_ascii_lowercase();
    match target {
        FieldTarget::Component {
            attr: "collider",
            key,
        } => match *key {
            "size" => shape == "box" || shape == "cuboid",
            "radius" => matches!(shape.as_str(), "sphere" | "ball" | "cylinder" | "capsule"),
            "half-height" => matches!(shape.as_str(), "cylinder" | "capsule"),
            "mesh-url" => matches!(shape.as_str(), "trimesh" | "mesh" | "precompute" | "convex"),
            _ => true,
        },
        _ => true,
    }
}

/// True para tags de UI (o campo de renomeação fica no fallback `id`).
fn viber_is_ui_tag(tag: &str) -> bool {
    viber::ui::is_ui_tag(tag)
}

/// Um disclosure toggle mudou: mostra/esconde o corpo da secção e recorda o
/// estado em [`SectionOpen`] (sobrevive aos rebuilds do painel).
pub fn sync_section_toggles(world: &mut World) {
    let mut toggles = world.query_filtered::<Entity, Changed<bevy::ui::Checked>>();
    let changed: Vec<Entity> = toggles.iter(world).collect();
    for checkbox in changed {
        // sobe do checkbox até ao header com SectionBody
        let mut current = checkbox;
        let header = loop {
            if world.get::<SectionBody>(current).is_some() {
                break current;
            }
            match world.get::<ChildOf>(current).map(|child| child.parent()) {
                Some(parent) => current = parent,
                None => break Entity::PLACEHOLDER,
            }
        };
        if header == Entity::PLACEHOLDER {
            continue;
        }
        let (body, section) = {
            let info = world.get::<SectionBody>(header).unwrap();
            (info.body, info.section)
        };
        let open = world.get::<bevy::ui::Checked>(checkbox).is_some();
        if let Some(mut node) = world.get_mut::<Node>(body)
            && node.display != if open { Display::Flex } else { Display::None }
        {
            node.display = if open { Display::Flex } else { Display::None };
        }
        let mut state = world.resource_mut::<SectionOpen>();
        if open {
            state.0.insert(section);
        } else {
            state.0.remove(&section);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn xml(src: &str) -> XmlNode {
        viber::xml::parse_fragment(src).unwrap().remove(0)
    }

    #[test]
    fn component_strings_roundtrip_and_preserve_unknown_keys() {
        let value = "shape: box; size: 2 1 2; flavor: spicy";
        assert_eq!(component_get(value, "shape").as_deref(), Some("box"));
        assert_eq!(component_get(value, "SIZE").as_deref(), Some("2 1 2"));
        assert_eq!(component_get(value, "missing"), None);
        let rewritten = component_set(value, "size", "4 2 4");
        assert_eq!(
            rewritten, "shape: box; size: 4 2 4; flavor: spicy",
            "a ordem e as chaves alheias preservam-se"
        );
        assert_eq!(component_set("", "type", "dynamic"), "type: dynamic");
        assert_eq!(
            component_set("type: dynamic", "gravity-scale", "2"),
            "type: dynamic; gravity-scale: 2"
        );
    }

    #[test]
    fn transform_fields_read_pose_and_cover_the_aliases() {
        let node = xml(r#"<Entity name="crate" translation="1 2 3" euler="0 90 0" scale="2" />"#);
        assert_eq!(
            field_number(
                &node,
                &FieldTarget::Pose {
                    part: PosePart::Translation,
                    axis: 1
                }
            ),
            Some(2.0)
        );
        assert_eq!(
            field_number(
                &node,
                &FieldTarget::Pose {
                    part: PosePart::Euler,
                    axis: 1
                }
            ),
            Some(90.0)
        );
        assert_eq!(
            field_number(
                &node,
                &FieldTarget::Pose {
                    part: PosePart::Scale,
                    axis: 0
                }
            ),
            Some(2.0)
        );
        // aliases também ficam cobertos (escondidos do fallback)
        let keys = covered_keys("entity");
        for alias in [
            "translation",
            "euler",
            "rotation",
            "pos",
            "transform",
            "scale",
            "collider",
            "rigidbody",
        ] {
            assert!(keys.contains(&alias), "{alias} devia estar coberto");
        }
    }

    #[test]
    fn every_known_tag_has_sections_and_covered_keys() {
        for tag in viber::recipes::KNOWN_TAGS {
            let sections = sections_for(tag);
            let keys = covered_keys(tag);
            assert!(!keys.is_empty(), "{tag}");
            // uma tag transformável tem SEMPRE a secção Transform
            if TRANSFORMABLE_TAGS.contains(tag) {
                assert!(
                    sections.iter().any(|(id, _)| *id == SectionId::Transform),
                    "{tag} devia ter Transform"
                );
            }
        }
    }

    #[test]
    fn lights_material_and_gltf_get_typed_fields() {
        assert!(
            sections_for("PointLight")
                .iter()
                .any(|(id, _)| *id == SectionId::Light)
        );
        let light = sections_for("pointlight");
        let light_fields = light
            .iter()
            .find(|(id, _)| *id == SectionId::Light)
            .map(|(_, fields)| fields.len())
            .unwrap();
        assert_eq!(light_fields, 4);
        assert!(
            sections_for("Cuboid")
                .iter()
                .any(|(id, _)| *id == SectionId::Material)
        );
        assert!(
            sections_for("GltfScene")
                .iter()
                .any(|(id, _)| *id == SectionId::Gltf)
        );
        // UI tags não ganham Transform (não são entidades posicionadas)
        assert!(sections_for("uitext").is_empty());
    }

    #[test]
    fn colors_convert_to_xml_canonical() {
        assert_eq!(color_to_xml(&Color::srgb(1.0, 0.5, 0.0)), "#ff8000");
        assert_eq!(color_to_xml(&Color::WHITE), "#ffffff");
        let node = xml(r##"<PointLight color="#ff8000" />"##);
        assert_eq!(
            color_to_xml(&field_color(&node, &FieldTarget::Attr { key: "color" })),
            "#ff8000"
        );
    }
}
