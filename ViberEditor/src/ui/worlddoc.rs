//! The World Doc panel: a read-only view of the baked world document (`bake.json` + `.ahgt`).
//!
//! Shows the terrain summary, every carved feature kind with counts and key fields, the spawn
//! groups, and a hillshade minimap with feature outlines drawn on top. The bake is loaded once
//! at startup from `--bake <dir>` (default: `<world_dir>/bake` when it exists); without a bake
//! the panel stays in an explicit empty state.

use bevy::prelude::*;
use std::path::Path;

use super::strings::{
    Lang, StrKey, t, wd_feature_kind, wd_heightmap_label, wd_instances, wd_segments, wd_source,
    wd_spawn_groups, wd_spec_label, wd_warning, wd_world_line,
};
use crate::app::EditorConfig;
use crate::doc::bake::{BakeDir, Features};
use crate::doc::minimap::{Minimap, minimap_to_bevy_image, render_minimap};
use crate::ui::icons::{self, image_icon};
use bevy::tasks::Task;

/// Rendered minimap side, in pixels.
const MINIMAP_SIDE: u32 = 512;
/// Outline thickness in minimap pixels.
const OUTLINE_PX: f32 = 2.0;
/// Selection-marker color: white core over a dark ring for contrast.
const SELECTION_COLOR: [u8; 4] = [255, 255, 255, 255];

/// Point-marker radius in minimap pixels.
const MARKER_RADIUS: f32 = 3.0;

/// Overlay colors per feature kind (RGBA8).
const PAD_COLOR: [u8; 4] = [255, 170, 60, 255];
const LAKE_COLOR: [u8; 4] = [80, 150, 255, 255];
const RIVER_COLOR: [u8; 4] = [90, 200, 255, 255];
const ROAD_COLOR: [u8; 4] = [205, 165, 100, 255];
const CLIFF_COLOR: [u8; 4] = [255, 100, 90, 255];
const ARCH_COLOR: [u8; 4] = [255, 130, 220, 255];
const CAVE_COLOR: [u8; 4] = [170, 110, 70, 255];
const EXCLUSION_COLOR: [u8; 4] = [190, 130, 255, 255];

/// Marks the outer World Doc body (attached below the tab header).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct WorldDocBody;

/// Marks the node the minimap texture is attached to.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct WorldDocMinimap;

/// Marks one rendered document line, so the whole set can be replaced in one command.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct WorldDocLine;

#[derive(Resource, Default)]
pub struct WorldDocState {
    bake: Option<BakeDir>,
    dirty: bool,
    /// Whether the bake directory was already read (startup runs once, but keep it idempotent).
    loaded: bool,
    minimap_spawned: bool,
    minimap: Option<Handle<Image>>,
    /// The instance name currently marked on the minimap (selection sync).
    marker_name: Option<String>,
    /// A `viber bake` subprocess in flight (re-bake or verify).
    bake_task: Option<Task<(String, bool)>>,
    /// Last bake/verify outcome shown as the panel's first line.
    pub status: Option<String>,
    /// Re-read the bake directory (set after a finished re-bake).
    pub reload_requested: bool,
}

/// Loads the bake directory once at startup; missing bakes are an empty state, not an error.
pub fn load_world_doc(config: Res<EditorConfig>, mut state: ResMut<WorldDocState>) {
    if state.loaded {
        return;
    }
    state.loaded = true;
    let Some(dir) = config.bake_dir.clone() else {
        state.dirty = true;
        return;
    };
    match BakeDir::load(&dir) {
        Ok(bake) => {
            info!("world doc: loaded bake from {}", dir.display());
            state.bake = Some(bake);
        }
        Err(error) => {
            warn!("world doc: failed to load {}: {error}", dir.display());
        }
    }
    state.dirty = true;
}

/// Rebuilds the document lines and attaches the minimap texture when the panel is up.
#[allow(clippy::too_many_arguments)] // Bevy system: one parameter per resource/query.
pub fn render_world_doc(
    mut commands: Commands,
    mut state: ResMut<WorldDocState>,
    selection: Res<crate::scene::Selection>,
    doc: Res<crate::scene::doc::SceneDoc>,
    lang: Res<Lang>,
    lines_containers: Query<Entity, With<WorldDocLines>>,
    minimap_anchors: Query<Entity, With<WorldDocMinimap>>,
    mut images: ResMut<Assets<Image>>,
) {
    // The minimap texture is attached once the panel node exists.
    if !state.minimap_spawned
        && let Ok(anchor) = minimap_anchors.single()
    {
        state.minimap_spawned = true;
        if let Some(bake) = &state.bake {
            let image = minimap_to_bevy_image(&build_minimap(bake, MINIMAP_SIDE));
            let handle = images.add(image);
            state.minimap = Some(handle.clone());
            // Extra 2: a imagem é CLICÁVEL — envia a câmara de editor para o XZ.
            commands
                .spawn((
                    ImageNode::new(handle),
                    Node {
                        width: px(MINIMAP_SIDE as f32),
                        aspect_ratio: Some(1.0),
                        ..default()
                    },
                    MinimapImage,
                    ChildOf(anchor),
                ))
                .observe(minimap_click);
        }
    }

    // Selection sync: the picked instance's name gets a white marker on the
    // minimap (redrawn only when it changes).
    let selected_name = selection
        .primary()
        .and_then(|node| doc.node(node))
        .and_then(|node| node.attr("name"))
        .map(str::to_owned);
    if selected_name != state.marker_name
        && let Some(handle) = state.minimap.clone()
    {
        state.marker_name = selected_name.clone();
        if let Some(bake) = &state.bake {
            let marker = selected_name
                .as_deref()
                .and_then(|name| instance_xz(bake, name));
            let image = minimap_to_bevy_image(&build_minimap_with_marker(
                bake,
                MINIMAP_SIDE,
                marker.as_ref(),
            ));
            if let Err(error) = images.insert(handle.id(), image) {
                warn!("world doc: minimap re-render failed: {error:?}");
            }
        }
    }

    if lang.is_changed() {
        state.dirty = true;
    }
    if !state.dirty {
        return;
    }
    let Ok(container) = lines_containers.single() else {
        return; // panel not spawned yet; retry next frame with dirty still set
    };
    state.dirty = false;

    commands.despawn_all::<With<WorldDocLine>>();
    if state.bake.is_none() {
        // The text2d-generated map stands in for the minimap until a bake exists.
        commands
            .spawn_scene(bsn! {
                Node {
                    width: percent(100),
                    align_items: AlignItems::Center,
                    padding: {UiRect::vertical(px(12))},
                }
                WorldDocLine
                Pickable::IGNORE
                Children [
                    @image_icon(icons::WORLD_ICON, 112.0)
                ]
            })
            .insert(ChildOf(container));
    }
    for line in world_doc_lines(state.bake.as_ref(), *lang) {
        commands.spawn((
            WorldDocLine,
            Text::new(line.text),
            TextColor(line.kind.color()),
            Pickable::IGNORE,
            ChildOf(container),
        ));
    }
}

/// The XZ position of an instance by name, from the bake document.
fn instance_xz(bake: &BakeDir, name: &str) -> Option<[f32; 2]> {
    bake.doc
        .spawn_groups
        .iter()
        .flat_map(|group| group.instances.iter())
        .find(|instance| instance.name.as_deref() == Some(name))
        .and_then(|instance| instance.translation)
        .map(|translation| [translation[0], translation[2]])
}

/// Marks the scrolling container that holds the document lines.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct WorldDocLines;

/// One rendered document line with its visual role.
#[derive(Debug, Clone, PartialEq)]
struct DocLine {
    text: String,
    kind: LineKind,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LineKind {
    Section,
    Item,
    Dim,
    Warn,
}

impl LineKind {
    fn color(self) -> Color {
        match self {
            LineKind::Section => Color::srgb(0.66, 0.82, 0.95),
            LineKind::Item => Color::srgb(0.85, 0.87, 0.9),
            LineKind::Dim => Color::srgb(0.62, 0.65, 0.68),
            LineKind::Warn => Color::srgb(0.94, 0.78, 0.44),
        }
    }
}

/// Builds every document line for the panel; `None` renders the empty state.
fn world_doc_lines(bake: Option<&BakeDir>, lang: Lang) -> Vec<DocLine> {
    let mut lines = Vec::new();
    let Some(bake) = bake else {
        lines.push(DocLine {
            text: t(lang, StrKey::WdNoBake).into(),
            kind: LineKind::Dim,
        });
        return lines;
    };

    if let Some(world) = bake.doc.source.world.as_deref() {
        lines.push(DocLine {
            text: wd_source(
                lang,
                world,
                bake.doc.source.viber_version.as_deref().unwrap_or("?"),
            ),
            kind: LineKind::Dim,
        });
    }
    for warning in &bake.warnings {
        lines.push(DocLine {
            text: wd_warning(lang, warning),
            kind: LineKind::Warn,
        });
    }

    // Terrain.
    lines.push(DocLine {
        text: t(lang, StrKey::WdTerrain).into(),
        kind: LineKind::Section,
    });
    let terrain = &bake.doc.terrain;
    lines.push(DocLine {
        text: wd_world_line(
            lang,
            &fmt_opt_f(terrain.world_size),
            &fmt_opt_f(terrain.max_height),
            &if terrain.layers.is_empty() {
                "—".to_string()
            } else {
                terrain.layers.join(", ")
            },
        ),
        kind: LineKind::Item,
    });
    if let Some(name) = terrain.heightmap.as_deref() {
        let grid = bake
            .heightmap
            .as_ref()
            .map(|map| format!(" ({}x{})", map.width, map.depth))
            .unwrap_or_default();
        lines.push(DocLine {
            text: format!("{}{name}{grid}", wd_heightmap_label(lang)),
            kind: LineKind::Item,
        });
    }
    let spec = &terrain.spec;
    let spec_bits = [
        opt_line("levels", spec.levels.map(|v| v.to_string())),
        opt_line("res", spec.resolution.map(|v| v.to_string())),
        opt_line(
            "collision",
            spec.collision_resolution.map(|v| v.to_string()),
        ),
        opt_line("render", spec.render_distance.map(|v| format!("{v:.0} m"))),
        opt_line("snow", spec.snow_height.map(|v| v.to_string())),
        opt_line("cliff", spec.cliff_angle.map(|v| format!("{v:.0}°"))),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join(" · ");
    if !spec_bits.is_empty() {
        lines.push(DocLine {
            text: format!("{}{spec_bits}", wd_spec_label(lang)),
            kind: LineKind::Item,
        });
    }

    // Features.
    let features = &bake.doc.features;
    let feature_kinds: [(&str, usize); 9] = [
        ("Pads", features.pads.len()),
        ("Lakes", features.lakes.len()),
        ("Rivers", features.rivers.len()),
        ("RoadNetworks", features.road_networks.len()),
        ("Roads", features.roads.len()),
        ("Cliffs", features.cliffs.len()),
        ("Arches", features.arches.len()),
        ("Caves", features.caves.len()),
        ("Exclusions", features.exclusions.len()),
    ];
    if feature_kinds.iter().any(|(_, count)| *count > 0) {
        lines.push(DocLine {
            text: t(lang, StrKey::WdFeatures).into(),
            kind: LineKind::Section,
        });
        for pad in &features.pads {
            lines.push(DocLine {
                text: format!(
                    "{} {} · at {} · {:.1}x{:.1} · h {}",
                    wd_feature_kind(lang, "pad"),
                    pad.id,
                    fmt_at(&pad.at),
                    pad.size[0],
                    pad.size[1],
                    fmt_opt_f(pad.resolved_height),
                ),
                kind: LineKind::Item,
            });
        }
        for lake in &features.lakes {
            lines.push(DocLine {
                text: format!(
                    "{} {} · at {} · r {} · depth {} · water {}",
                    wd_feature_kind(lang, "lake"),
                    lake.id,
                    lake.at.map(|at| fmt_at(&at)).unwrap_or_else(|| "—".into()),
                    fmt_opt_f(lake.radius),
                    fmt_opt_f(lake.depth),
                    fmt_opt_f(lake.water_level),
                ),
                kind: LineKind::Item,
            });
        }
        for river in &features.rivers {
            lines.push(DocLine {
                text: format!(
                    "{} {} · {} pts · w {} · d {}",
                    wd_feature_kind(lang, "river"),
                    river.id,
                    river.points.len(),
                    fmt_opt_f(river.width),
                    fmt_opt_f(river.depth),
                ),
                kind: LineKind::Item,
            });
        }
        for net in &features.road_networks {
            lines.push(DocLine {
                text: format!(
                    "{} {} · {} · {}",
                    wd_feature_kind(lang, "network"),
                    net.id,
                    net.profile.as_deref().unwrap_or("?"),
                    wd_segments(lang, net.segments.len()),
                ),
                kind: LineKind::Item,
            });
            for segment in &net.segments {
                let ends = match (segment.a.as_deref(), segment.b.as_deref()) {
                    (Some(a), Some(b)) => format!(" ({a} → {b})"),
                    _ => String::new(),
                };
                lines.push(DocLine {
                    text: format!(
                        "  segment{ends} · {} pts · w {}",
                        segment.polyline.len(),
                        fmt_opt_f(segment.width),
                    ),
                    kind: LineKind::Dim,
                });
            }
        }
        for road in &features.roads {
            lines.push(DocLine {
                text: format!(
                    "{} {} · {} pts · w {}",
                    wd_feature_kind(lang, "road"),
                    road.id,
                    road.points.len(),
                    fmt_opt_f(road.width),
                ),
                kind: LineKind::Item,
            });
        }
        for cliff in &features.cliffs {
            lines.push(DocLine {
                text: format!(
                    "{} {} · at {} · {}",
                    wd_feature_kind(lang, "cliff"),
                    cliff.id,
                    fmt_at(&cliff.at),
                    cliff.profile.as_deref().unwrap_or("?"),
                ),
                kind: LineKind::Item,
            });
        }
        for arch in &features.arches {
            lines.push(DocLine {
                text: format!(
                    "{} {} · at {} · {}x{} · yaw {}",
                    wd_feature_kind(lang, "arch"),
                    arch.id,
                    fmt_at(&arch.at),
                    fmt_opt_f(arch.width),
                    fmt_opt_f(arch.height),
                    fmt_opt_deg(arch.yaw),
                ),
                kind: LineKind::Item,
            });
        }
        for cave in &features.caves {
            lines.push(DocLine {
                text: format!(
                    "{} {} · at {} · r [{}] · depth {}",
                    wd_feature_kind(lang, "cave"),
                    cave.id,
                    fmt_at(&cave.at),
                    cave.radius
                        .iter()
                        .map(|r| format!("{r:.1}"))
                        .collect::<Vec<_>>()
                        .join(", "),
                    fmt_opt_f(cave.depth),
                ),
                kind: LineKind::Item,
            });
        }
        for exclusion in &features.exclusions {
            lines.push(DocLine {
                text: format!(
                    "{} {} · at {} · r {}",
                    wd_feature_kind(lang, "exclusion"),
                    exclusion.id,
                    fmt_at(&exclusion.at),
                    fmt_opt_f(exclusion.radius),
                ),
                kind: LineKind::Item,
            });
        }
    } else {
        lines.push(DocLine {
            text: t(lang, StrKey::WdFeaturesNone).into(),
            kind: LineKind::Dim,
        });
    }

    // Spawn groups.
    lines.push(DocLine {
        text: wd_spawn_groups(lang, bake.doc.spawn_groups.len()),
        kind: LineKind::Section,
    });
    for group in &bake.doc.spawn_groups {
        lines.push(DocLine {
            text: format!(
                "{} · {} · seed {} · {}",
                group.name.as_deref().unwrap_or(&group.id),
                group.kind.as_deref().unwrap_or("?"),
                group
                    .seed
                    .map(|seed| seed.to_string())
                    .unwrap_or_else(|| "—".into()),
                wd_instances(lang, group.instances.len()),
            ),
            kind: LineKind::Item,
        });
        if let (Some(min), Some(max)) = (group.rules.region_min, group.rules.region_max) {
            lines.push(DocLine {
                text: format!(
                    "  region [{:.0}, {:.0}, {:.0}] .. [{:.0}, {:.0}, {:.0}]",
                    min[0], min[1], min[2], max[0], max[1], max[2],
                ),
                kind: LineKind::Dim,
            });
        }
    }

    lines
}

/// Renders the feature-overlay minimap for a loaded bake.
///
/// World coordinates map onto the square `world_size x world_size` extent centered at the
/// origin (x east → right, z south → down). Without a heightmap the map is a dark canvas
/// with just the overlays.
pub fn build_minimap(bake: &BakeDir, side_px: u32) -> Minimap {
    build_minimap_with_marker(bake, side_px, None)
}

/// Same as [`build_minimap`], with an optional SELECTION marker disc drawn on
/// top (XZ world position of the picked instance).
pub fn build_minimap_with_marker(
    bake: &BakeDir,
    side_px: u32,
    marker: Option<&[f32; 2]>,
) -> Minimap {
    let mut minimap = match &bake.heightmap {
        Some(map) => render_minimap(map, side_px),
        None => Minimap::filled(side_px, side_px, [24, 26, 30, 255]),
    };
    let world = bake.world_size();
    let half = world / 2.0;
    let scale = side_px as f32 / world;
    let to_px = |p: &[f32; 2]| [(p[0] + half) * scale, (p[1] + half) * scale];

    let features: &Features = &bake.doc.features;
    // Bottom layer: exclusion zones and lake outlines.
    for exclusion in &features.exclusions {
        if let Some(radius) = exclusion.radius {
            minimap.draw_circle_outline(
                to_px(&exclusion.at),
                radius * scale,
                OUTLINE_PX,
                EXCLUSION_COLOR,
            );
        }
    }
    for lake in &features.lakes {
        if lake.outline.len() >= 2 {
            let outline: Vec<[f32; 2]> = lake.outline.iter().map(to_px).collect();
            minimap.draw_polyline(&outline, OUTLINE_PX, LAKE_COLOR);
        } else if let (Some(at), Some(radius)) = (lake.at, lake.radius) {
            minimap.draw_circle_outline(to_px(&at), radius * scale, OUTLINE_PX, LAKE_COLOR);
        }
    }
    // Water and roads.
    for river in &features.rivers {
        if river.points.len() >= 2 {
            let points: Vec<[f32; 2]> = river.points.iter().map(to_px).collect();
            minimap.draw_polyline(&points, OUTLINE_PX, RIVER_COLOR);
        }
    }
    for road in &features.roads {
        if road.points.len() >= 2 {
            let points: Vec<[f32; 2]> = road.points.iter().map(to_px).collect();
            minimap.draw_polyline(&points, OUTLINE_PX, ROAD_COLOR);
        }
    }
    for net in &features.road_networks {
        for segment in &net.segments {
            if segment.polyline.len() >= 2 {
                let points: Vec<[f32; 2]> = segment.polyline.iter().map(to_px).collect();
                minimap.draw_polyline(&points, OUTLINE_PX, ROAD_COLOR);
            }
        }
    }
    // Pads on top of the ground features.
    for pad in &features.pads {
        minimap.draw_rect_outline(
            to_px(&pad.at),
            [pad.size[0] * scale * 0.5, pad.size[1] * scale * 0.5],
            OUTLINE_PX,
            PAD_COLOR,
        );
    }
    // Point markers last so they stay visible over everything.
    for cliff in &features.cliffs {
        minimap.draw_disc(to_px(&cliff.at), MARKER_RADIUS, CLIFF_COLOR);
    }
    for arch in &features.arches {
        minimap.draw_disc(to_px(&arch.at), MARKER_RADIUS, ARCH_COLOR);
    }
    for cave in &features.caves {
        minimap.draw_disc(to_px(&cave.at), MARKER_RADIUS, CAVE_COLOR);
    }
    // The SELECTION marker: bigger, white with a dark ring for contrast.
    if let Some(marker) = marker {
        minimap.draw_disc(to_px(marker), MARKER_RADIUS + 2.0, SELECTION_COLOR);
    }
    minimap
}

/// `"label: value"` for optional scalars; `None` fields are skipped entirely.
fn opt_line(label: &str, value: Option<String>) -> Option<String> {
    value.map(|value| format!("{label}: {value}"))
}

/// `(x, z)` with one decimal, for XZ ground positions.
fn fmt_at(at: &[f32; 2]) -> String {
    format!("({:.1}, {:.1})", at[0], at[1])
}

/// Optional scalar with one decimal; absent fields render as `—`.
fn fmt_opt_f(value: Option<f32>) -> String {
    value
        .map(|v| format!("{v:.1}"))
        .unwrap_or_else(|| "—".into())
}

/// Optional angle in degrees.
fn fmt_opt_deg(value: Option<f32>) -> String {
    value
        .map(|v| format!("{v:.0}°"))
        .unwrap_or_else(|| "—".into())
}

/// Whether a world directory has a sibling `bake/` directory.
/// A imagem do minimapa (alvo do clique).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct MinimapImage;

/// Clique no minimapa → a câmara de editor salta para o XZ clicado (Extra 2).
/// O mapeamento é a inversa do `to_px` do desenho: px = (world + half) * scale,
/// com a linha do canvas a crescer para +Z.
pub fn minimap_click(
    click: On<PointerClick>,
    maps: Query<(&ComputedNode, &UiGlobalTransform), With<MinimapImage>>,
    windows: Query<&Window, With<bevy::window::PrimaryWindow>>,
    state: Res<WorldDocState>,
    runtime: Option<Res<viber::terrain::runtime::TerrainRuntime>>,
    mut camera: ResMut<viber::editor_camera::EditorCamState>,
) {
    let Ok((node, ui_transform)) = maps.get(click.event_target()) else {
        return;
    };
    let Some(bake) = state.bake.as_ref() else {
        return;
    };
    let Ok(window) = windows.single() else {
        return;
    };
    let Some(position) = window.cursor_position() else {
        return;
    };
    let physical = position * window.scale_factor();
    let size = node.size();
    if size.x <= 1.0 || size.y <= 1.0 {
        return;
    }
    let top_left = ui_transform.translation - size * 0.5;
    let local = physical - top_left;
    if local.x < 0.0 || local.y < 0.0 || local.x > size.x || local.y > size.y {
        return;
    }
    let uv = local / size;
    let world = bake.world_size();
    let x = uv.x * world - world * 0.5;
    let z = uv.y * world - world * 0.5;
    let y = runtime
        .as_deref()
        .map(|runtime| runtime.sample(x, z))
        .unwrap_or(camera.target.y);
    camera.target = Vec3::new(x, y, z);
}

pub fn default_bake_dir(world: &Path) -> Option<std::path::PathBuf> {
    let dir = world.parent()?.join("bake");
    dir.is_dir().then_some(dir)
}

/// Settles a finished bake/verify: status line and bake reload
/// of the fresh document.
pub fn poll_bake(lang: Res<Lang>, mut state: ResMut<WorldDocState>) {
    let Some(task) = &mut state.bake_task else {
        return;
    };
    let Some((summary, ok)) = bevy::tasks::block_on(bevy::tasks::poll_once(task)) else {
        return;
    };
    state.bake_task = None;
    let verdict = if ok { StrKey::WdOk } else { StrKey::WdFailed };
    state.status = Some(format!("{}: {summary}", t(*lang, verdict)));
    info!(
        "world doc: bake settle — {}",
        state.status.as_deref().unwrap_or("")
    );
    // A finished re-bake (not a verify) reloads the bake document.
    if ok && !summary.contains("verify OK") && !summary.contains("instances match") {
        state.reload_requested = true;
    }
}

/// Re-reads the bake directory after a finished re-bake.
pub fn reload_world_doc(config: Res<EditorConfig>, mut state: ResMut<WorldDocState>) {
    if !state.reload_requested {
        return;
    }
    state.reload_requested = false;
    let Some(dir) = config.bake_dir.clone() else {
        return;
    };
    match BakeDir::load(&dir) {
        Ok(bake) => {
            state.bake = Some(bake);
            state.dirty = true;
            state.minimap_spawned = false;
            state.marker_name = None;
            state.minimap = None;
            info!("world doc: bake reloaded after re-bake");
        }
        Err(error) => warn!("world doc: reload failed: {error}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::doc::ahgt::{self, Ahgt};
    use crate::doc::bake::BakeDoc;
    use serde_json::{Value, json};
    use std::path::PathBuf;

    fn fixture_bake(dir: &Path, with_heightmap: bool) -> BakeDir {
        std::fs::write(
            dir.join("bake.json"),
            serde_json::to_string(&json!({
                "schema": 1,
                "source": { "world": "examples/simple-rpg/world.xml", "viber_version": "0.1.0" },
                "terrain": {
                    "world_size": 4000.0, "max_height": 200.0,
                    "heightmap": if with_heightmap { json!("terrain.baked.ahgt") } else { Value::Null },
                    "layers": ["grass"],
                    "spec": { "levels": 5, "resolution": 128, "render_distance": 950.0 }
                },
                "features": {
                    "pads": [ { "id": "pad-0", "at": [0.0, 0.0], "size": [400.0, 400.0],
                                "resolved_height": 46.3 } ],
                    "lakes": [ { "id": "lake-0", "at": [-800.0, 1040.0], "radius": 160.0 } ],
                    "rivers": [ { "id": "river-0", "points": [[0.0, 0.0], [3140.0, 1250.0]] } ],
                    "exclusions": [ { "id": "excl-0", "at": [1000.0, -1000.0], "radius": 520.0 } ],
                    "arches": [ { "id": "arch-0", "at": [352.0, 96.0] } ]
                },
                "spawn_groups": [
                    { "id": "grp-0", "name": "forest-pines", "kind": "static", "seed": 12,
                      "rules": { "count": 620, "region_min": [-450.0, 0.0, -450.0],
                                 "region_max": [450.0, 200.0, 450.0] },
                      "instances": [ { "name": "i0" }, { "name": "i1" } ] }
                ]
            }))
            .unwrap(),
        )
        .unwrap();
        if with_heightmap {
            let heights = vec![u16::MAX / 2; 32 * 32];
            let bytes = ahgt::encode_ahgt(
                32,
                32,
                &heights,
                r#"{"worldSize":4000,"maxHeight":200}"#,
                false,
            );
            std::fs::write(dir.join("terrain.baked.ahgt"), bytes).unwrap();
        }
        BakeDir::load(dir).unwrap()
    }

    #[test]
    fn empty_state_without_bake() {
        let lines = world_doc_lines(None, Lang::Pt);
        assert_eq!(lines.len(), 1);
        assert!(lines[0].text.contains("sem bake"));
        assert!(world_doc_lines(None, Lang::En)[0].text.contains("no bake"));
    }

    #[test]
    fn document_lines_cover_every_section() {
        let dir = tempfile::tempdir().unwrap();
        let bake = fixture_bake(dir.path(), true);
        let lines = world_doc_lines(Some(&bake), Lang::Pt);
        let text: Vec<&str> = lines.iter().map(|line| line.text.as_str()).collect();

        assert!(text.contains(&"Terreno"));
        assert!(
            text.iter()
                .any(|t| t.starts_with("origem: examples/simple-rpg"))
        );
        assert!(
            text.iter()
                .any(|t| t.contains("mundo 4000") && t.contains("camadas grass"))
        );
        assert!(text.contains(&"mapa de alturas: terrain.baked.ahgt (32x32)"));
        assert!(
            text.iter()
                .any(|t| t.contains("especificação:") && t.contains("levels: 5"))
        );

        assert!(text.contains(&"Elementos"));
        assert!(
            text.iter()
                .any(|t| t.starts_with("plataforma pad-0") && t.contains("h 46.3"))
        );
        assert!(
            text.iter()
                .any(|t| t.starts_with("lago lake-0") && t.contains("r 160.0"))
        );
        assert!(
            text.iter()
                .any(|t| t.starts_with("rio river-0") && t.contains("2 pts"))
        );
        assert!(
            text.iter()
                .any(|t| t.starts_with("exclusão excl-0") && t.contains("r 520.0"))
        );
        assert!(text.iter().any(|t| t.starts_with("arco arch-0")));

        assert!(text.contains(&"Grupos de objetos (1)"));
        assert!(text.contains(&"forest-pines · static · seed 12 · 2 instâncias"));
        assert!(
            text.iter()
                .any(|t| t.starts_with("  region [-450, 0, -450] .. [450, 200, 450]"))
        );
        // The heightmap parsed, so no heightmap warning is shown.
        assert!(!text.iter().any(|t| t.contains("aviso:")));
    }

    #[test]
    fn minimap_overlays_draw_feature_pixels() {
        let dir = tempfile::tempdir().unwrap();
        let bake = fixture_bake(dir.path(), false); // no heightmap: flat dark canvas
        let map = build_minimap(&bake, 256);

        // The pad (400x400 m at origin on a 4000 m world) is a 25.6 px rect centered at 128.
        assert_eq!(map.pixel(128, 115), PAD_COLOR, "pad top edge");
        assert_eq!(map.pixel(115, 128), PAD_COLOR, "pad left edge");
        assert_ne!(map.pixel(100, 100), PAD_COLOR, "inside-but-outside the pad");

        // The exclusion circle (r 520 m → 33 px around (192, 64)).
        let probe = map.pixel(192 + 33, 64);
        assert_eq!(probe, EXCLUSION_COLOR, "exclusion outline east point");

        // The river crosses the whole map diagonally.
        assert_eq!(map.pixel(128, 128), RIVER_COLOR, "river midpoint");

        // The arch marker sits at its world position.
        let px = ((352.0f32 + 2000.0) / 4000.0 * 256.0).round() as i32;
        let py = ((96.0f32 + 2000.0) / 4000.0 * 256.0).round() as i32;
        assert_eq!(map.pixel(px, py), ARCH_COLOR, "arch marker");
    }

    #[test]
    fn default_bake_dir_requires_sibling_directory() {
        let dir = tempfile::tempdir().unwrap();
        let world = dir.path().join("world.xml");
        assert_eq!(default_bake_dir(&world), None);

        std::fs::create_dir(dir.path().join("bake")).unwrap();
        assert_eq!(default_bake_dir(&world), Some(dir.path().join("bake")));
    }

    #[test]
    fn bake_without_heightmap_reports_warning() {
        let dir = tempfile::tempdir().unwrap();
        let bake = fixture_bake(dir.path(), false);
        assert!(bake.heightmap.is_none());
        assert!(
            bake.warnings
                .iter()
                .any(|warning| warning.contains("no baked heightmap"))
        );
    }

    #[test]
    fn state_defaults_are_empty_and_not_dirty() {
        let state = WorldDocState::default();
        assert!(state.bake.is_none());
        assert!(!state.dirty);
        assert!(!state.loaded);
    }

    #[test]
    fn bake_doc_deserializes_minimal_spawn_group() {
        // Spawn groups may omit everything but the id; the panel must not care.
        let doc: BakeDoc = serde_json::from_value(json!({
            "schema": 1,
            "spawn_groups": [ { "id": "grp-x" } ]
        }))
        .unwrap();
        assert_eq!(doc.spawn_groups.len(), 1);
        assert_eq!(doc.spawn_groups[0].id, "grp-x");
        assert_eq!(doc.spawn_groups[0].rules.count, None);
    }

    #[test]
    fn ahgt_meta_feeds_world_size_fallback() {
        let bake = BakeDir {
            dir: PathBuf::new(),
            doc: BakeDoc::default(),
            heightmap: Some(Ahgt {
                width: 2,
                depth: 2,
                data: vec![0; 4],
                meta: ahgt::AhgtMeta {
                    world_size: Some(2000.0),
                    max_height: None,
                },
            }),
            warnings: Vec::new(),
        };
        assert_eq!(bake.world_size(), 2000.0);
    }
}
