//! Scene tabs (Godot-style): one tab per open scene of the project, the active one is the
//! scene loaded in the viewport. Switching tabs saves the current scene and swaps the world
//! IN-PROCESS through the engine's scene loader (`viber::scene_loader` — the same unload/load
//! the game uses for `change_scene`), so a scene is its own universe here too: the interior
//! of a house is edited alone, not as a remote corner of the overworld.
//!
//! Agent parity: `viber.scene.change` over the bridge swaps the editor's scene as well — the
//! editor listens to [`SceneLoaded`] and opens/activates the matching tab.

use std::path::{Path, PathBuf};

use bevy::picking::events::PointerClick;
use bevy::picking::pointer::PointerButton;
use bevy::prelude::*;
use viber::project::{Project, SceneTemplate};
use viber::scene_loader::{Arrival, ChangeScene, CurrentScene, SceneLoaded};

use super::icons::{self, fa};
use super::strings::{Lang, StrKey, t};
use super::theme;
use super::tooltip::Tooltip;
use crate::scene::doc::SceneDoc;
use crate::scene::{EditStatus, Selection, TreeExpansion};

/// One open scene.
#[derive(Debug, Clone, PartialEq)]
pub struct SceneTab {
    pub path: PathBuf,
    pub title: String,
    /// The editor camera when this tab was last left (restored on return).
    pub camera: Option<TabCamera>,
}

/// The orbit pose of the editor camera, per tab.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TabCamera {
    pub target: Vec3,
    pub yaw_deg: f32,
    pub pitch_deg: f32,
    pub distance: f32,
}

/// The open scenes and the active one (the scene in the viewport).
#[derive(Resource, Debug, Default)]
pub struct SceneTabs {
    pub tabs: Vec<SceneTab>,
    pub active: usize,
    /// Re-render request (titles, dirty marker, language).
    rev: u64,
    rendered_rev: Option<u64>,
    rendered_dirty: bool,
}

impl SceneTabs {
    fn touch(&mut self) {
        self.rev += 1;
    }

    /// Opens `path` (or activates its tab); returns the tab index.
    pub fn open(&mut self, path: &Path, title: String) -> usize {
        let path = canonical(path);
        let index = match self.tabs.iter().position(|tab| tab.path == path) {
            Some(index) => {
                if self.tabs[index].title != title {
                    self.tabs[index].title = title;
                }
                index
            }
            None => {
                self.tabs.push(SceneTab {
                    path,
                    title,
                    camera: None,
                });
                self.tabs.len() - 1
            }
        };
        self.active = index;
        self.touch();
        index
    }

    /// Closes a tab; the active tab cannot be closed while it is the only one.
    pub fn close(&mut self, index: usize) -> bool {
        if self.tabs.len() <= 1 || index >= self.tabs.len() {
            return false;
        }
        self.tabs.remove(index);
        if self.active > index || self.active >= self.tabs.len() {
            self.active = self.active.saturating_sub(1);
        }
        self.touch();
        true
    }
}

fn canonical(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}

/// The tab title of a scene: its `<world title>`, else the scene id, else the file stem.
fn scene_title(path: &Path) -> String {
    let project = Project::discover(path);
    project
        .scene_info(path)
        .and_then(|info| info.title.or(Some(info.id)))
        .or_else(|| {
            path.file_stem()
                .map(|stem| stem.to_string_lossy().into_owned())
        })
        .unwrap_or_default()
}

/// The tab bar container.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SceneTabBar;

/// A tab button (its index).
#[derive(Component, Debug, Clone, Copy)]
struct TabButton(usize);

/// A tab's close button (its index).
#[derive(Component, Debug, Clone, Copy)]
struct TabClose(usize);

/// The "+" (new scene) button.
#[derive(Component, Debug, Clone, Copy)]
struct NewSceneButton;

/// Asks to open a scene file in a tab (Files dock, context menu). Processed by
/// [`process_open_requests`] (needs the whole `World`).
#[derive(Resource, Debug, Default)]
pub struct OpenSceneRequest(pub Option<PathBuf>);

pub struct SceneTabsPlugin;

impl Plugin for SceneTabsPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<SceneTabs>()
            .init_resource::<OpenSceneRequest>()
            .add_systems(
                Update,
                (
                    seed_first_tab,
                    on_scene_loaded,
                    render_tabs,
                    process_open_requests,
                )
                    .chain(),
            );
    }
}

/// The bar itself (empty; [`render_tabs`] fills it). Height matches the dock tab bars.
pub fn scene_tab_bar() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            height: px(28),
            flex_shrink: 0.0,
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::End,
            column_gap: px(2),
            padding: UiRect::horizontal(px(4)),
            overflow: Overflow::clip(),
        }
        SceneTabBar
    }
}

/// First frame with a document: the world the editor opened becomes the first tab.
fn seed_first_tab(
    doc: Option<Res<SceneDoc>>,
    current: Option<Res<CurrentScene>>,
    mut tabs: ResMut<SceneTabs>,
) {
    if !tabs.tabs.is_empty() {
        return;
    }
    let Some(doc) = doc else {
        return;
    };
    let path = doc.world_path().to_path_buf();
    let title = current
        .and_then(|c| c.title.clone())
        .unwrap_or_else(|| scene_title(&path));
    tabs.open(&path, title);
}

/// A scene finished loading (tab switch, `viber.scene.change` from an agent, Ctrl+R):
/// activate its tab and drop every piece of editor state that pointed into the old scene.
#[allow(clippy::too_many_arguments)]
fn on_scene_loaded(
    mut loaded: MessageReader<SceneLoaded>,
    mut tabs: ResMut<SceneTabs>,
    mut commands: Commands,
    mut config: ResMut<crate::app::EditorConfig>,
    mut status: ResMut<EditStatus>,
    camera: Option<ResMut<viber::editor_camera::EditorCamState>>,
    terrain: Option<Res<viber::terrain::runtime::TerrainRuntime>>,
    lang: Res<Lang>,
) {
    let Some(event) = loaded.read().last().cloned() else {
        return;
    };
    // A câmara (que a troca não mexe) ainda é a da aba que se deixa: fica guardada nela,
    // venha a troca de uma aba, do dock Ficheiros ou de um agente (`viber.scene.change`).
    let leaving = tabs.active;
    if let Some(cam) = camera.as_deref()
        && let Some(tab) = tabs.tabs.get_mut(leaving)
        && tab.path != canonical(&event.path)
    {
        tab.camera = Some(TabCamera {
            target: cam.target,
            yaw_deg: cam.yaw_deg,
            pitch_deg: cam.pitch_deg,
            distance: cam.distance,
        });
    }
    let title = scene_title(&event.path);
    let index = tabs.open(&event.path, title.clone());
    // A câmara volta onde estava nesta cena; numa cena nunca vista, enquadra a origem
    // (uma sala de interior vive à volta de 0,0 — a pose da vila ficava a km dela).
    if let Some(mut camera) = camera {
        match tabs.tabs[index].camera {
            Some(pose) => {
                camera.target = pose.target;
                camera.yaw_deg = pose.yaw_deg;
                camera.pitch_deg = pose.pitch_deg;
                camera.distance = pose.distance;
            }
            None => {
                let ground = terrain
                    .as_deref()
                    .map(|t| t.sample(0.0, 0.0))
                    .unwrap_or(0.0);
                camera.target = Vec3::new(0.0, ground, 0.0);
                camera.distance = 32.0;
            }
        }
    }
    // Everything below addressed NODES or ENTITIES of the old scene.
    commands.insert_resource(Selection::default());
    commands.insert_resource(TreeExpansion::default());
    commands.insert_resource(crate::scene::PendingEdits::default());
    commands.insert_resource(crate::scene::tree_drag::TreeDrag::default());
    commands.insert_resource(crate::scene::tree::RenameState::default());
    commands.insert_resource(crate::scene::tree::TreeView::default());
    commands.insert_resource(crate::scene::inspector::InspectorView::default());
    commands.insert_resource(crate::scene::add_node::AddNodeOpen::default());
    commands.insert_resource(crate::viewport::gizmo::SelectedEntity::default());
    commands.insert_resource(crate::viewport::feature_handles::FeaturePoints::default());
    commands.insert_resource(crate::viewport::feature_handles::FeatureDrag::default());
    commands.insert_resource(crate::viewport::terrain_tool::Stroke::default());
    commands.insert_resource(crate::viewport::stamp_tool::StampStroke::default());
    commands.insert_resource(super::problems::ProblemsState::default());
    commands.insert_resource(super::history::HistoryState::default());
    commands.insert_resource(super::biomes::BiomesState::default());
    commands.insert_resource(super::canvas::CanvasDrag::default());
    // The World Doc panel reads the bake of THIS scene (interiors have none).
    config.bake_dir = super::worlddoc::default_bake_dir(&event.path);
    commands.insert_resource(super::worlddoc::WorldDocState::default());
    status.0 = format!("{}: {title}", t(*lang, StrKey::SceneOpened));
}

/// Rebuilds the tab buttons when the tabs, the language or the dirty state change.
#[allow(clippy::too_many_arguments)]
fn render_tabs(
    mut commands: Commands,
    mut tabs: ResMut<SceneTabs>,
    doc: Option<Res<SceneDoc>>,
    lang: Res<Lang>,
    bars: Query<Entity, With<SceneTabBar>>,
) {
    let Ok(bar) = bars.single() else {
        return;
    };
    let dirty = doc
        .as_ref()
        .is_some_and(|doc| !doc.dirty_paths().is_empty());
    if tabs.rendered_rev == Some(tabs.rev) && tabs.rendered_dirty == dirty && !lang.is_changed() {
        return;
    }
    tabs.rendered_rev = Some(tabs.rev);
    tabs.rendered_dirty = dirty;
    commands.entity(bar).despawn_related::<Children>();
    let closable = tabs.tabs.len() > 1;
    for (index, tab) in tabs.tabs.iter().enumerate() {
        let active = index == tabs.active;
        let label = if active && dirty {
            format!("{}  •", tab.title)
        } else {
            tab.title.clone()
        };
        let (bg, fg, border) = if active {
            (theme::BG_PANEL, theme::TEXT, theme::ACCENT)
        } else {
            (theme::BG_HEADER, theme::TEXT_DIM, theme::BG_HEADER)
        };
        commands
            .spawn((
                TabButton(index),
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                    height: px(24),
                    padding: UiRect::new(px(10), px(if closable { 4 } else { 10 }), px(0), px(0)),
                    border: UiRect::top(px(2)),
                    border_radius: BorderRadius::top(px(5)),
                    ..default()
                },
                BackgroundColor(bg),
                BorderColor::all(border),
                Tooltip(t(*lang, StrKey::SceneTabTip)),
                ChildOf(bar),
            ))
            .observe(tab_clicked)
            .with_children(|row| {
                row.spawn((
                    icons::icon_text(
                        fa::CUBES,
                        10.0,
                        if active {
                            theme::ACCENT
                        } else {
                            icons::ICON_DIM
                        },
                    ),
                    Pickable::IGNORE,
                ));
                row.spawn((
                    Text::new(label),
                    TextFont::from_font_size(theme::FONT_BODY),
                    TextColor(fg),
                    Pickable::IGNORE,
                ));
                if closable {
                    row.spawn((
                        TabClose(index),
                        icons::icon_text('\u{f00d}', 9.0, icons::ICON_DIM),
                        Node {
                            padding: UiRect::all(px(3)),
                            ..default()
                        },
                        Tooltip(t(*lang, StrKey::SceneCloseTip)),
                    ))
                    .observe(close_clicked);
                }
            });
    }
    commands
        .spawn((
            NewSceneButton,
            Node {
                align_items: AlignItems::Center,
                height: px(24),
                padding: UiRect::horizontal(px(8)),
                ..default()
            },
            Tooltip(t(*lang, StrKey::SceneNewTip)),
            ChildOf(bar),
        ))
        .observe(new_scene_clicked)
        .with_children(|row| {
            row.spawn((
                icons::icon_text(fa::PLUS, 11.0, icons::ICON_DIM),
                Pickable::IGNORE,
            ));
        });
}

fn tab_clicked(
    click: On<PointerClick>,
    buttons: Query<&TabButton>,
    tabs: Res<SceneTabs>,
    mut requests: ResMut<OpenSceneRequest>,
) {
    if click.button != PointerButton::Primary {
        return;
    }
    let Ok(TabButton(index)) = buttons.get(click.event_target()) else {
        return;
    };
    if *index == tabs.active {
        return;
    }
    if let Some(tab) = tabs.tabs.get(*index) {
        requests.0 = Some(tab.path.clone());
    }
}

fn close_clicked(
    mut click: On<PointerClick>,
    buttons: Query<&TabClose>,
    mut tabs: ResMut<SceneTabs>,
    mut requests: ResMut<OpenSceneRequest>,
) {
    click.propagate(false);
    let Ok(TabClose(index)) = buttons.get(click.event_target()) else {
        return;
    };
    let was_active = *index == tabs.active;
    if tabs.close(*index) && was_active {
        // The neighbour becomes the scene in the viewport.
        let next = tabs.tabs[tabs.active].path.clone();
        requests.0 = Some(next);
    }
}

fn new_scene_clicked(
    _click: On<PointerClick>,
    doc: Res<SceneDoc>,
    mut requests: ResMut<OpenSceneRequest>,
    mut status: ResMut<EditStatus>,
    lang: Res<Lang>,
) {
    let project = Project::discover(doc.world_path());
    let mut n = 1;
    let path = loop {
        let id = format!("cena-{n}");
        match project.new_scene(&id, SceneTemplate::Empty) {
            Ok(path) => break path,
            Err(_) if n < 999 => n += 1,
            Err(error) => {
                status.0 = format!("{}: {error:#}", t(*lang, StrKey::SceneOpenFailed));
                return;
            }
        }
    };
    requests.0 = Some(path);
}

/// Opens the requested scene: saves the current one, then swaps in-process (project
/// scenes) or opens a new editor window (a world outside the project).
fn process_open_requests(world: &mut World) {
    let Some(path) = world.resource_mut::<OpenSceneRequest>().0.take() else {
        return;
    };
    let lang = *world.resource::<Lang>();
    let current = world.get_resource::<CurrentScene>().cloned();
    let id = current
        .as_ref()
        .filter(|c| c.switchable())
        .and_then(|c| Project::at(&c.root).scene_id(&path));
    let Some(id) = id else {
        // Not a scene of this project: the old behaviour — its own editor window.
        let opened = std::env::current_exe()
            .ok()
            .and_then(|exe| std::process::Command::new(exe).arg(&path).spawn().ok())
            .is_some();
        world.resource_mut::<EditStatus>().0 = if opened {
            t(lang, StrKey::SceneNotProject).to_string()
        } else {
            t(lang, StrKey::SceneOpenFailed).to_string()
        };
        return;
    };
    crate::scene::save_world(world);
    let request = ChangeScene::new(id, Arrival::Keep);
    if let Err(error) = viber::scene_loader::request_change(world, request) {
        world.resource_mut::<EditStatus>().0 =
            format!("{}: {error:#}", t(lang, StrKey::SceneOpenFailed));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tabs_open_activate_and_close() {
        let mut tabs = SceneTabs::default();
        let a = tabs.open(Path::new("/x/world.xml"), "Vila".into());
        let b = tabs.open(Path::new("/x/scenes/house.xml"), "Casa".into());
        assert_eq!((a, b, tabs.active), (0, 1, 1));
        // Reopening activates, never duplicates.
        assert_eq!(tabs.open(Path::new("/x/world.xml"), "Vila".into()), 0);
        assert_eq!(tabs.tabs.len(), 2);
        assert_eq!(tabs.active, 0);
        // Closing a tab before the active one keeps the same scene active.
        tabs.open(Path::new("/x/scenes/house.xml"), "Casa".into());
        assert!(tabs.close(0));
        assert_eq!(tabs.tabs[tabs.active].title, "Casa");
        // The last tab cannot be closed.
        assert!(!tabs.close(0));
    }
}
