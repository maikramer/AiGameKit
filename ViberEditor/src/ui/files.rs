//! The Files dock: the world's folder as a navigable tree (Fase 3) — the same
//! [`viber::scene::files`] scan the CLI serves (`viber scene files`). Selecting a
//! file offers the per-kind action: GLB → insert into the scene (dropped on the
//! terrain under the cursor), scenes → open in a new editor window, any file →
//! open externally.

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::feathers::display::caption;
use bevy::feathers::theme::ThemeBackgroundColor;
use bevy::feathers::tokens;
use bevy::prelude::*;
use bevy::ui_widgets::Activate;
use viber::scene::doc::SceneDoc;
use viber::scene::files::{self, FileEntry, FileKind};

use super::icons::{self, fa, icon_caption, icon_text};
use super::strings::{Lang, StrKey, t};
use super::tooltip::Tooltip;
use crate::scene::PendingEdits;

/// The dock's scrollable tree body.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct FilesBody;

/// The header row: filter chips + refresh + the selected file's actions.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct FilesHeader;

/// A file row (`path` is world-relative, `/`-separated).
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct FileRow {
    pub path: String,
    pub kind: FileKind,
}

/// The "insert this GLB into the scene" button.
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct InsertGlb(pub String);

/// "Carimbar" — ativa o stamp tool com este GLB.
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct StampGlb(pub String);

/// Open a world/scene XML in a NEW editor window.
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct OpenWorld(pub String);

/// Open the file with the desktop's default app.
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct OpenExternal(pub String);

/// Which files the tree shows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum FilesFilter {
    #[default]
    All,
    Scenes,
    Glb,
    Scripts,
}

impl FilesFilter {
    const ALL: [FilesFilter; 4] = [
        FilesFilter::All,
        FilesFilter::Scenes,
        FilesFilter::Glb,
        FilesFilter::Scripts,
    ];

    fn matches(self, kind: FileKind) -> bool {
        match self {
            FilesFilter::All => true,
            FilesFilter::Scenes => kind.is_scene(),
            FilesFilter::Glb => kind == FileKind::Gltf,
            FilesFilter::Scripts => kind == FileKind::Script,
        }
    }

    fn label(self, lang: Lang) -> &'static str {
        match (self, lang) {
            (FilesFilter::All, Lang::En) => "All",
            (FilesFilter::All, Lang::Pt) => "Tudo",
            (FilesFilter::Scenes, Lang::En) => "Scenes",
            (FilesFilter::Scenes, Lang::Pt) => "Cenas",
            (FilesFilter::Glb, _) => "GLB",
            (FilesFilter::Scripts, _) => "Scripts",
        }
    }
}

#[derive(Resource, Debug, Default)]
pub struct FilesState {
    pub entries: Vec<FileEntry>,
    pub selected: Option<(String, FileKind)>,
    pub filter: FilesFilter,
    built: bool,
}

/// Rescans the world folder (first frame + whenever the world document is
/// re-created, e.g. after switching worlds).
pub fn scan_files(doc: Res<SceneDoc>, mut state: ResMut<FilesState>) {
    if state.built && !doc.is_added() {
        return;
    }
    state.built = true;
    let Some(dir) = doc.world_path().parent() else {
        warn!("files dock: world sem pasta");
        return;
    };
    state.entries = files::scan(dir, 6);
    if let Some((path, _)) = &state.selected
        && !state
            .entries
            .iter()
            .any(|entry| entry.name == *path || entry.children.iter().any(|c| c.name == *path))
    {
        state.selected = None;
    }
}

/// Rebuilds the tree rows whenever the state, filter or language change.
pub fn render_files(
    mut commands: Commands,
    state: Res<FilesState>,
    lang: Res<Lang>,
    bodies: Query<Entity, With<FilesBody>>,
) {
    let Ok(body) = bodies.single() else {
        return;
    };
    if !state.is_changed() && !lang.is_changed() {
        return;
    }
    commands.entity(body).despawn_related::<Children>();
    let mut rows = Vec::new();
    flatten(&state.entries, state.filter, "", 0, &mut rows);
    for row in rows {
        let selected = state
            .selected
            .as_ref()
            .is_some_and(|(path, _)| *path == row.path);
        let icon_color = if selected {
            Color::WHITE
        } else {
            kind_color(row.kind)
        };
        let mut entity = commands.spawn((
            FileRow {
                path: row.path.clone(),
                kind: row.kind,
            },
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
                padding: UiRect::new(px(4.0 + row.depth as f32 * 12.0), px(4), px(1), px(1)),
                ..default()
            },
            ChildOf(body),
        ));
        if selected {
            entity.insert(ThemeBackgroundColor(tokens::BUTTON_PRIMARY_BG));
        }
        entity.observe(select_file).with_children(|children| {
            children.spawn((
                icon_text(kind_glyph(row.kind), 11.0, icon_color),
                Node {
                    width: px(14),
                    ..default()
                },
                Pickable::IGNORE,
            ));
            children.spawn((
                Text::new(row.name.clone()),
                TextFont::from_font_size(12.0),
                TextColor(row_color(row.kind)),
                Pickable::IGNORE,
            ));
        });
    }
}

/// Rebuilds the header: filter chips, and the selected file's actions.
pub fn render_file_header(
    mut commands: Commands,
    state: Res<FilesState>,
    lang: Res<Lang>,
    headers: Query<Entity, With<FilesHeader>>,
) {
    let Ok(header) = headers.single() else {
        return;
    };
    if !state.is_changed() && !lang.is_changed() {
        return;
    }
    commands.entity(header).despawn_related::<Children>();
    for filter in FilesFilter::ALL {
        let active = state.filter == filter;
        commands
            .spawn_scene(bsn! {
                @FeathersToolButton {
                    @variant: {if active { ButtonVariant::Primary } else { ButtonVariant::Plain }},
                    @caption: bsn! { @caption(filter.label(*lang)) },
                }
            })
            .insert((FilterChip(filter), ChildOf(header)))
            .observe(set_filter);
    }
    if let Some((path, kind)) = state.selected.clone() {
        if kind == FileKind::Gltf {
            commands
                .spawn_scene(bsn! {
                    @FeathersToolButton {
                        @variant: ButtonVariant::Plain,
                        @caption: bsn! {
                            @icon_caption(fa::CUBE, t(*lang, StrKey::FilesInsert), icons::ICON)
                        },
                    }
                    Tooltip({t(*lang, StrKey::FilesInsertTip)})
                })
                .insert((InsertGlb(path.clone()), ChildOf(header)))
                .observe(insert_glb);
            commands
                .spawn_scene(bsn! {
                    @FeathersToolButton {
                        @variant: ButtonVariant::Plain,
                        @caption: bsn! {
                            @icon_caption(fa::STAMP, t(*lang, StrKey::FilesStamp), icons::ICON)
                        },
                    }
                    Tooltip({t(*lang, StrKey::FilesStampTip)})
                })
                .insert((StampGlb(path.clone()), ChildOf(header)))
                .observe(stamp_glb);
        }
        if kind.is_scene() {
            commands
                .spawn_scene(bsn! {
                    @FeathersToolButton {
                        @variant: ButtonVariant::Plain,
                        @caption: bsn! {
                            @icon_caption(fa::CUBE, t(*lang, StrKey::FilesInstance), icons::ICON)
                        },
                    }
                    Tooltip({t(*lang, StrKey::FilesInstanceTip)})
                })
                .insert((InstanceScene(path.clone()), ChildOf(header)))
                .observe(instance_scene);
            commands
                .spawn_scene(bsn! {
                    @FeathersToolButton {
                        @variant: ButtonVariant::Plain,
                        @caption: bsn! {
                            @icon_caption(fa::GLOBE, t(*lang, StrKey::FilesOpenWorld), icons::ICON)
                        },
                    }
                    Tooltip({t(*lang, StrKey::FilesOpenWorldTip)})
                })
                .insert((OpenWorld(path.clone()), ChildOf(header)))
                .observe(open_world);
        }
        commands
            .spawn_scene(bsn! {
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icon_caption(
                            fa::FILE_LINES,
                            t(*lang, StrKey::FilesOpenExternal),
                            icons::ICON_DIM
                        )
                    },
                }
            })
            .insert((OpenExternal(path.clone()), ChildOf(header)))
            .observe(open_external);
    }
}

/// A filter chip button.
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq)]
pub struct FilterChip(pub FilesFilter);

fn set_filter(activate: On<Activate>, chips: Query<&FilterChip>, mut state: ResMut<FilesState>) {
    if let Ok(chip) = chips.get(activate.event_target())
        && state.filter != chip.0
    {
        state.filter = chip.0;
    }
}

fn select_file(click: On<PointerClick>, rows: Query<&FileRow>, mut state: ResMut<FilesState>) {
    if let Ok(row) = rows.get(click.event_target()) {
        state.selected = Some((row.path.clone(), row.kind));
    }
}

/// GLB → insert into the scene, dropped on the terrain under the cursor (or the
/// world origin when the cursor never crossed the viewport).
fn insert_glb(activate: On<Activate>, buttons: Query<&InsertGlb>, mut commands: Commands) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let glb = button.0.clone();
    commands.queue(move |world: &mut World| {
        let at = drop_point(world).unwrap_or(Vec3::ZERO);
        let parent = world
            .resource::<SceneDoc>()
            .selector(&viber::scene::doc::NodeRef::root(0));
        let op = match viber::scene::catalog::glb_drop(
            world.resource::<SceneDoc>(),
            &parent,
            &glb,
            [at.x, at.y, at.z],
        ) {
            Ok(op) => op,
            Err(error) => {
                warn!("insert {glb}: {error:#}");
                return;
            }
        };
        let label = t(*world.resource::<Lang>(), StrKey::FilesInsert).to_string();
        world
            .resource_mut::<PendingEdits>()
            .push_selecting_inserted(label, vec![op]);
    });
}

/// The "instance this scene" button (an `.xml` of the world folder).
#[derive(Component, Debug, Clone)]
pub struct InstanceScene(pub String);

/// Scene → `<Include src>` at the root, on the terrain under the cursor (the same op as
/// `viber scene instance`).
fn instance_scene(activate: On<Activate>, buttons: Query<&InstanceScene>, mut commands: Commands) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let scene = button.0.clone();
    commands.queue(move |world: &mut World| {
        let at = drop_point(world).map(|at| [at.x, at.y, at.z]);
        let doc = world.resource::<SceneDoc>();
        let parent = doc.selector(&viber::scene::doc::NodeRef::root(0));
        let op = match viber::scene::catalog::scene_instance(doc, &parent, &scene, at) {
            Ok(op) => op,
            Err(error) => {
                world.resource_mut::<crate::scene::EditStatus>().0 = format!("{error:#}");
                return;
            }
        };
        let label = t(*world.resource::<Lang>(), StrKey::FilesInstance).to_string();
        world
            .resource_mut::<PendingEdits>()
            .push_selecting_inserted(label, vec![op]);
    });
}

/// Where a dropped/inserted GLB lands: the terrain under the last viewport ray.
pub fn drop_point(world: &mut World) -> Option<Vec3> {
    let cursor = *world.resource::<crate::viewport::gizmo::ViewportCursor>();
    let ray = cursor.ray?;
    let runtime = world.get_resource::<viber::terrain::runtime::TerrainRuntime>()?;
    viber::scene::terrain::ray_ground(ray.origin, *ray.direction, 4000.0, |x, z| {
        runtime.sample(x, z)
    })
}

/// "Carimbar": ativa o stamp tool com este GLB.
fn stamp_glb(activate: On<Activate>, buttons: Query<&StampGlb>, mut commands: Commands) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let glb = button.0.clone();
    commands.queue(move |world: &mut World| {
        world
            .resource_mut::<crate::viewport::stamp_tool::ActiveStamp>()
            .0 = Some(glb);
        world
            .resource_mut::<crate::viewport::gizmo::GizmoTool>()
            .mode = crate::viewport::gizmo::Mode::Stamp;
    });
}

fn open_external(
    activate: On<Activate>,
    buttons: Query<&OpenExternal>,
    doc: Res<SceneDoc>,
    mut status: ResMut<crate::scene::EditStatus>,
    lang: Res<Lang>,
) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let Some(dir) = doc.world_path().parent() else {
        return;
    };
    let path = dir.join(&button.0);
    let opened = std::process::Command::new("xdg-open")
        .arg(&path)
        .spawn()
        .is_ok();
    status.0 = if opened {
        t(*lang, StrKey::FilesOpened).to_string()
    } else {
        t(*lang, StrKey::FilesOpenFailed).to_string()
    };
}

fn open_world(
    activate: On<Activate>,
    buttons: Query<&OpenWorld>,
    doc: Res<SceneDoc>,
    mut status: ResMut<crate::scene::EditStatus>,
    lang: Res<Lang>,
) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let Some(dir) = doc.world_path().parent() else {
        return;
    };
    let path = dir.join(&button.0);
    let opened = std::env::current_exe()
        .ok()
        .and_then(|exe| {
            std::process::Command::new(exe)
                .arg(&path)
                .spawn()
                .ok()
                .map(|_| ())
        })
        .is_some();
    status.0 = if opened {
        t(*lang, StrKey::FilesOpened).to_string()
    } else {
        t(*lang, StrKey::FilesOpenFailed).to_string()
    };
}

fn flatten(
    entries: &[FileEntry],
    filter: FilesFilter,
    prefix: &str,
    depth: usize,
    out: &mut Vec<FlatRow>,
) {
    for entry in entries {
        let path = if prefix.is_empty() {
            entry.name.clone()
        } else {
            format!("{prefix}/{}", entry.name)
        };
        if entry.kind == FileKind::Dir {
            if filter == FilesFilter::All || has_matching(&entry.children, filter) {
                out.push(FlatRow {
                    path: path.clone(),
                    name: entry.name.clone(),
                    kind: FileKind::Dir,
                    depth,
                });
            }
            flatten(&entry.children, filter, &path, depth + 1, out);
        } else if filter.matches(entry.kind) {
            out.push(FlatRow {
                path,
                name: entry.name.clone(),
                kind: entry.kind,
                depth,
            });
        }
    }
}

fn has_matching(entries: &[FileEntry], filter: FilesFilter) -> bool {
    entries.iter().any(|entry| {
        entry.kind == FileKind::Dir && has_matching(&entry.children, filter)
            || filter.matches(entry.kind)
    })
}

struct FlatRow {
    path: String,
    name: String,
    kind: FileKind,
    depth: usize,
}

pub fn kind_glyph(kind: FileKind) -> char {
    match kind {
        FileKind::Dir => fa::FOLDER,
        FileKind::World => fa::GLOBE,
        FileKind::Scene => fa::FILE_LINES,
        FileKind::Script => fa::TERMINAL,
        FileKind::Gltf => fa::CUBE,
        FileKind::Texture => fa::SQUARE,
        FileKind::Heightmap => fa::MOUNTAIN,
        FileKind::Instances => fa::SEEDLING,
        FileKind::Audio => fa::MUSIC,
        FileKind::Other => fa::FILE_LINES,
    }
}

fn kind_color(kind: FileKind) -> Color {
    match kind {
        FileKind::Dir => icons::ICON_DIM,
        FileKind::World => Color::srgb(0.55, 0.8, 0.55),
        FileKind::Scene => Color::srgb(0.62, 0.78, 0.95),
        FileKind::Script => Color::srgb(0.95, 0.78, 0.42),
        FileKind::Gltf => Color::srgb(0.75, 0.6, 0.95),
        _ => icons::ICON_DIM,
    }
}

fn row_color(kind: FileKind) -> Color {
    match kind {
        FileKind::Dir => Color::srgb(0.7, 0.74, 0.8),
        _ => Color::srgb(0.88, 0.9, 0.94),
    }
}
