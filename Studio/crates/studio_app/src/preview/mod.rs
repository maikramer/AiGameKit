//! Viewport 3D do Studio: cache de decode, câmara órbita, cena e animação.

pub mod camera;
pub mod cache;
pub mod scene;

use std::path::PathBuf;

use bevy::prelude::*;
use bevy_egui::egui;
use crossbeam_channel::Receiver;

pub struct PreviewPlugin {
    pub cache_root: PathBuf,
}

impl Plugin for PreviewPlugin {
    fn build(&self, app: &mut App) {
        app.insert_resource(PreviewCache {
            root: self.cache_root.clone(),
        })
        .init_resource::<PreviewCommand>()
        .init_resource::<PreviewState>()
        .init_resource::<camera::OrbitCamera>()
        .init_resource::<ViewportInfo>()
        .add_systems(Startup, scene::setup_environment)
        .add_systems(
            Update,
            (
                cache::handle_load_request,
                cache::poll_decode,
                scene::poll_scene_load,
                scene::apply_preview_requests,
                camera::orbit_input,
                camera::apply_camera,
                scene::draw_grid,
                scene::move_ground,
            )
                .chain(),
        );
    }
}

#[derive(Resource, Clone)]
pub struct PreviewCache {
    pub root: PathBuf,
}

/// Pedido de preview escrito pela UI/app e consumido pelo viewport.
#[derive(Resource, Default)]
pub struct PreviewCommand {
    pub load: Option<LoadRequest>,
    pub clear: bool,
    pub frame: bool,
    pub set_clip: Option<String>,
    pub toggle_play: bool,
    pub speed: Option<f32>,
    pub turntable: Option<bool>,
    pub wireframe: Option<bool>,
}

#[derive(Debug, Clone)]
pub struct LoadRequest {
    pub source: PathBuf,
    pub artifact: String,
    pub clip: Option<String>,
}

pub struct PendingDecode {
    pub artifact: String,
    pub source: PathBuf,
    pub rx: Receiver<Result<DecodedGlb, String>>,
}

#[derive(Debug, Clone)]
pub struct DecodedGlb {
    pub relative: String,
}

pub struct PendingScene {
    pub artifact: String,
    pub source: PathBuf,
    pub handle: Handle<Gltf>,
}

pub struct ReadyScene {
    pub artifact: String,
    pub source: PathBuf,
    pub clips: Vec<String>,
    pub indices: Vec<AnimationNodeIndex>,
    pub graph: Option<Handle<AnimationGraph>>,
}

pub struct Loaded {
    pub artifact: String,
    pub source: PathBuf,
    pub root: Entity,
    pub player: Option<Entity>,
    pub clips: Vec<String>,
    pub indices: Vec<AnimationNodeIndex>,
    pub active_clip: Option<usize>,
    pub speed: f32,
}

#[derive(Resource, Default)]
pub struct PreviewState {
    pub loaded: Option<Loaded>,
    pub pending_decode: Option<PendingDecode>,
    pub pending_scene: Option<PendingScene>,
    pub ready_scene: Option<ReadyScene>,
    pub error: Option<String>,
    pub bounds: Option<bevy_ext_gltf::GlbBounds>,
    pub playing: bool,
    pub frame_pending: bool,
    pub source_artifact: Option<String>,
}

/// Área livre do ecrã (entre painéis egui) e estado do input.
#[derive(Resource, Default, Clone, Copy)]
pub struct ViewportInfo {
    pub rect: Option<egui::Rect>,
    pub pointer_in_viewport: bool,
    pub egui_wants_pointer: bool,
    pub egui_wants_keyboard: bool,
    pub mouse_pos: Option<egui::Pos2>,
}
