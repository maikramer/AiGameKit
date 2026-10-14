//! The editor's own UI: toolbar, docks (Objects, Inspector, Console/Logs/World Overview)
//! around the viewport. Drawn by a dedicated camera, apart from the game's UI. All
//! user-visible text is bilingual (PT default, EN) via [`strings`].

mod canvas;
mod console;
mod files;
pub mod icons;
mod layout;
mod logs;
mod mode_strip;
mod profiler;
pub mod strings;
mod toolbar;
mod tools;
pub mod tooltip;
pub mod worlddoc;

use bevy::prelude::*;
use strings::Lang;

pub struct EditorUiPlugin;

impl Plugin for EditorUiPlugin {
    fn build(&self, app: &mut App) {
        icons::load_font(app.world().resource::<AssetServer>());
        app.init_resource::<Lang>()
            .init_resource::<logs::BridgeLogs>()
            .init_resource::<console::ConsoleState>()
            .init_resource::<worlddoc::WorldDocState>()
            .init_resource::<toolbar::GamePause>()
            .init_resource::<toolbar::PendingPlayFrom>()
            .init_resource::<files::FilesState>()
            .add_plugins((
                tooltip::TooltipPlugin,
                tools::ToolStripPlugin,
                mode_strip::ModeStripPlugin,
                canvas::CanvasPlugin,
                profiler::ProfilerPlugin,
            ))
            .add_systems(
                Startup,
                (layout::camera.spawn(), layout::editor_root.spawn()),
            )
            .add_systems(
                Update,
                (layout::apply_startup_tab, mode_strip::apply_startup_mode),
            )
            .add_systems(
                Update,
                (
                    strings::sync_lang_labels,
                    layout::target_editor_camera,
                    toolbar::update_status,
                    toolbar::play_shortcuts,
                    toolbar::poll_pause,
                    toolbar::poll_play_from_here,
                    logs::poll_logs,
                    logs::render_logs,
                    (
                        console::submit_console,
                        console::clear_submitted_input,
                        console::poll_console,
                        console::render_console,
                    )
                        .chain(),
                    files::scan_files,
                    files::render_files,
                    files::render_file_header,
                    (
                        worlddoc::load_world_doc,
                        worlddoc::reload_world_doc,
                        worlddoc::poll_bake,
                        worlddoc::render_world_doc,
                    )
                        .chain(),
                ),
            );
    }
}

/// Marks the editor's root UI node.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct EditorRoot;
