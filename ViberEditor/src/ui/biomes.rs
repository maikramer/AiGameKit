//! The Biomes editor (Fase G2): a colored preview of the biome field
//! (rasterized from the engine's pure `BiomeField::sample`), plus editable
//! wedge rows (angle label + biome dropdown + remove) and an add button.
//! Every change is ONE canonical `terrain_biomes` op — the same one agents
//! use — and the scene sync re-bakes the chunk splats live.

use bevy::feathers::controls::{ButtonVariant, FeathersSelect, FeathersToolButton};
use bevy::feathers::controls::{OptionIndex, list_rows_from_strings};
use bevy::image::Image;
use bevy::prelude::*;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat};
use bevy::ui_widgets::{Activate, ValueChange};
use viber::scene::doc::SceneDoc;
use viber::scene::ops::EditOp;
use viber::terrain::splat::{Biome, BiomeField};

use super::icons::{self, fa, icon};
use crate::scene::{PendingEdits, Selection};

/// Cores de aproximação por bioma (o motor usa paletas de splat — aqui é só
/// para o preview do editor). A ordem é a dos pesos `BiomeBlend::ground`.
const BIOME_COLORS: [(&str, [u8; 3]); 5] = [
    ("vale", [106, 170, 84]),
    ("forest", [52, 110, 62]),
    ("desert", [214, 190, 122]),
    ("swamp", [92, 110, 74]),
    ("peaks", [230, 234, 240]),
];

fn biome_color(name: &str) -> [u8; 3] {
    BIOME_COLORS
        .iter()
        .find(|(key, _)| key.eq_ignore_ascii_case(name))
        .map(|(_, color)| *color)
        .unwrap_or([120, 120, 120])
}

/// The panel body (rebuilt when the field changes).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct BiomesBody;

#[derive(Component, Debug, Clone, Copy, PartialEq, Eq)]
pub struct WedgeBiome(pub usize);

#[derive(Component, Debug, Clone, Copy, PartialEq, Eq)]
pub struct WedgeRemove(pub usize);

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct WedgeAdd;

#[derive(Resource, Debug, Default)]
pub struct BiomesState {
    built_for: Option<u64>,
}

pub struct BiomesPlugin;

impl Plugin for BiomesPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<BiomesState>()
            .add_observer(on_wedge_biome)
            .add_observer(on_wedge_remove)
            .add_observer(on_wedge_add)
            .add_systems(Update, render_biomes);
    }
}

/// The current field (None sem terreno).
fn current_field(world: &World) -> Option<BiomeField> {
    world
        .resource::<SceneDoc>()
        .terrain
        .as_ref()
        .map(|terrain| terrain.meta.biomes.clone())
}

/// Aplica o campo como UMA op canónica.
fn apply_field(world: &mut World, field: &BiomeField) {
    let op = EditOp::TerrainBiomes {
        core: field.core,
        blend: field.blend,
        wedges: field
            .wedges
            .iter()
            .map(|w| (w.dir.x, w.dir.y, w.biome.name().to_owned()))
            .collect(),
    };
    world
        .resource_mut::<PendingEdits>()
        .push("biomas".to_owned(), vec![op]);
}

fn set_biome(world: &mut World, index: usize, option: usize) {
    let Some(mut field) = current_field(world) else {
        return;
    };
    if index >= field.wedges.len() || option >= BIOME_COLORS.len() {
        return;
    }
    let Some(biome) = Biome::from_name(BIOME_COLORS[option].0) else {
        return;
    };
    field.wedges[index].biome = biome;
    apply_field(world, &field);
}

fn remove_wedge(world: &mut World, index: usize) {
    let Some(mut field) = current_field(world) else {
        return;
    };
    if index >= field.wedges.len() || field.wedges.len() <= 1 {
        return;
    }
    field.wedges.remove(index);
    apply_field(world, &field);
}

fn add_wedge(world: &mut World) {
    let Some(mut field) = current_field(world) else {
        return;
    };
    let count = field.wedges.len();
    let dir = Vec2::from_angle((count as f32) * std::f32::consts::TAU / 5.0);
    field.wedges.push(viber::terrain::splat::BiomeWedge {
        dir,
        biome: Biome::Forest,
    });
    apply_field(world, &field);
}

// ── Observers ───────────────────────────────────────────────────────────────

fn on_wedge_biome(
    event: On<ValueChange<Entity>>,
    rows: Query<&WedgeBiome>,
    options: Query<&OptionIndex>,
    mut commands: Commands,
) {
    let Ok(row) = rows.get(event.source) else {
        return;
    };
    let Ok(option) = options.get(event.value) else {
        return;
    };
    let (index, option) = (row.0, option.0);
    commands.queue(move |world: &mut World| set_biome(world, index, option));
}

fn on_wedge_remove(event: On<Activate>, rows: Query<&WedgeRemove>, mut commands: Commands) {
    let Ok(row) = rows.get(event.event_target()) else {
        return;
    };
    let index = row.0;
    commands.queue(move |world: &mut World| remove_wedge(world, index));
}

fn on_wedge_add(event: On<Activate>, rows: Query<(), With<WedgeAdd>>, mut commands: Commands) {
    if !rows.contains(event.event_target()) {
        return;
    }
    commands.queue(|world: &mut World| add_wedge(world));
}

// ── Preview rasterization ───────────────────────────────────────────────────

const PREVIEW_PX: u32 = 96;

fn rasterize(field: &BiomeField) -> Image {
    let size = PREVIEW_PX;
    let mut data = Vec::with_capacity((size * size * 4) as usize);
    let half = 200.0;
    let names = ["vale", "forest", "desert", "swamp", "peaks"];
    for row in 0..size {
        let z = (row as f32 / size as f32) * 400.0 - half;
        for col in 0..size {
            let x = (col as f32 / size as f32) * 400.0 - half;
            let blend = field.sample(x, z, 0);
            let mut rgb = [0f32; 3];
            let total: f32 = blend.ground.iter().sum();
            if total > 1e-5 {
                for (index, name) in names.iter().enumerate() {
                    let weight = blend.ground[index] / total;
                    let color = biome_color(name);
                    for (channel, value) in color.iter().enumerate() {
                        rgb[channel] += *value as f32 * weight;
                    }
                }
            }
            data.extend_from_slice(&[rgb[0] as u8, rgb[1] as u8, rgb[2] as u8, 255]);
        }
    }
    Image::new(
        Extent3d {
            width: size,
            height: size,
            depth_or_array_layers: 1,
        },
        TextureDimension::D2,
        data,
        TextureFormat::Rgba8UnormSrgb,
        bevy::asset::RenderAssetUsages::default(),
    )
}

/// Assinatura estável do campo (só reconstruir o painel quando muda).
fn field_signature(field: &BiomeField) -> u64 {
    let mut hash = (field.core.to_bits() as u64) ^ ((field.blend.to_bits() as u64) << 1);
    for wedge in &field.wedges {
        hash = hash.rotate_left(3)
            ^ (wedge.dir.x.to_bits() as u64)
            ^ ((wedge.dir.y.to_bits() as u64) << 16);
        hash ^= wedge.biome as usize as u64;
    }
    hash
}

// ── Rendering ───────────────────────────────────────────────────────────────

/// Renders the panel for the selected `<Terrain>`.
fn render_biomes(
    mut commands: Commands,
    mut images: ResMut<Assets<Image>>,
    mut state: ResMut<BiomesState>,
    doc: Res<SceneDoc>,
    selection: Res<Selection>,
    bodies: Query<Entity, With<BiomesBody>>,
) {
    let Some(node) = selection.primary().cloned() else {
        return;
    };
    let Some(xml) = doc.node(&node) else {
        return;
    };
    if !xml.tag.eq_ignore_ascii_case("terrain") {
        if state.built_for.is_some() {
            state.built_for = None;
        }
        return;
    }
    let Some(field) = doc
        .terrain
        .as_ref()
        .map(|terrain| terrain.meta.biomes.clone())
    else {
        return;
    };
    let Ok(body) = bodies.single() else {
        return;
    };
    let signature = field_signature(&field);
    if state.built_for == Some(signature) {
        return;
    }
    state.built_for = Some(signature);
    commands.entity(body).despawn_related::<Children>();

    // preview colorido (esquerda)
    let handle = images.add(rasterize(&field));
    commands.spawn((
        ImageNode::new(handle),
        Node {
            width: px(PREVIEW_PX as f32),
            height: px(PREVIEW_PX as f32),
            ..default()
        },
        ChildOf(body),
    ));

    // coluna de cunhas (direita do preview): linhas ângulo | bioma | remover
    for (index, wedge) in field.wedges.iter().enumerate() {
        let degrees = wedge.dir.y.atan2(wedge.dir.x).to_degrees();
        let option = BIOME_COLORS
            .iter()
            .position(|(name, _)| name.eq_ignore_ascii_case(wedge.biome.name()))
            .unwrap_or(0);
        let row = commands
            .spawn((
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(4),
                    margin: UiRect::top(px(4)),
                    ..default()
                },
                ChildOf(body),
            ))
            .id();
        let label = format!("{:.0}°", degrees);
        commands.spawn((
            Text::new(label),
            TextFont::from_font_size(11.0),
            TextColor(Color::srgb(0.62, 0.66, 0.72)),
            Node {
                width: px(38),
                ..default()
            },
            Pickable::IGNORE,
            ChildOf(row),
        ));
        let list = list_rows_from_strings(BIOME_COLORS.iter().map(|(name, _)| *name), Some(option));
        commands
            .spawn_scene(bsn! {
                @FeathersSelect {
                    @options: {list},
                    @max_visible: 5usize,
                }
            })
            .insert((
                WedgeBiome(index),
                Node {
                    width: px(96),
                    ..default()
                },
                ChildOf(row),
            ))
            .observe(on_wedge_biome);
        commands
            .spawn_scene(bsn! {
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! { @icon(fa::CIRCLE_XMARK, 10.0, icons::ICON_DIM) },
                }
            })
            .insert((WedgeRemove(index), ChildOf(row)))
            .observe(on_wedge_remove);
    }
}
