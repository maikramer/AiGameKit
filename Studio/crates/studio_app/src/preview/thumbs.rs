//! Thumbnails offscreen: a cena atual renderizada para uma `Image` e gravada
//! em `.studio/thumbs/cover.png` (grelha da biblioteca).

use std::{path::PathBuf, time::Instant};

use bevy::{
    camera::{Camera, ClearColorConfig, RenderTarget},
    prelude::*,
    render::{
        render_resource::TextureFormat,
        view::screenshot::{Screenshot, ScreenshotCaptured},
    },
};

use super::PreviewState;

pub const THUMB_SIZE: u32 = 512;

#[derive(Resource, Default)]
pub struct ThumbnailRequest {
    pub source: Option<PathBuf>,
    pub out: Option<PathBuf>,
}

#[derive(Resource, Default)]
pub struct ThumbnailState {
    pub active: Option<ActiveThumb>,
    pub last_error: Option<String>,
    pub last_attempt: Option<Instant>,
}

pub struct ActiveThumb {
    pub camera: Entity,
    pub image: Handle<Image>,
    pub out: PathBuf,
    pub frames: u32,
    pub capture: bool,
}

#[derive(Component)]
struct ThumbCamera;

#[derive(Component)]
struct ThumbOut(PathBuf);

pub fn handle_request(
    mut commands: Commands,
    mut request: ResMut<ThumbnailRequest>,
    mut state: ResMut<ThumbnailState>,
    preview: Res<PreviewState>,
    mut images: ResMut<Assets<Image>>,
) {
    if state.active.is_some() {
        return;
    }
    let (Some(source), Some(out)) = (request.source.clone(), request.out.clone()) else {
        return;
    };
    if preview.loaded.as_ref().map(|loaded| &loaded.source) != Some(&source) {
        return;
    }
    let Some(bounds) = preview.bounds else {
        return;
    };

    let image = images.add(Image::new_target_texture(
        THUMB_SIZE,
        THUMB_SIZE,
        TextureFormat::Rgba8UnormSrgb,
        None,
    ));
    let center = bounds.center();
    let radius = bounds.half_extents().length().max(0.2);
    let yaw = 38f32.to_radians();
    let pitch = 14f32.to_radians();
    let dir = Vec3::new(
        yaw.sin() * pitch.cos(),
        pitch.sin(),
        yaw.cos() * pitch.cos(),
    );
    let eye = center + dir.normalize() * (radius * 3.1);
    let camera = commands
        .spawn((
            ThumbCamera,
            Camera3d::default(),
            Camera {
                order: -1,
                clear_color: ClearColorConfig::Custom(Color::srgb(0.055, 0.06, 0.07)),
                ..default()
            },
            RenderTarget::Image(image.clone().into()),
            Transform::from_translation(eye).looking_at(center, Vec3::Y),
            Msaa::Off,
        ))
        .id();

    tracing::debug!(
        "thumb eye={:?} center={:?} radius={} out={}",
        eye,
        center,
        radius,
        out.display()
    );
    state.active = Some(ActiveThumb {
        camera,
        image,
        out,
        frames: 0,
        capture: false,
    });
    state.last_attempt = Some(Instant::now());
    state.last_error = None;
    request.source = None;
    request.out = None;
}

pub fn tick(
    mut commands: Commands,
    mut state: ResMut<ThumbnailState>,
    mut images: ResMut<Assets<Image>>,
) {
    let Some(active) = state.active.as_mut() else {
        return;
    };
    active.frames += 1;
    if !active.capture && active.frames >= 20 {
        commands.spawn((
            Screenshot::image(active.image.clone()),
            ThumbOut(active.out.clone()),
        ));
        active.capture = true;
        tracing::debug!("thumbnail capture: {}", active.out.display());
    }
    if active.frames >= 28 {
        let Some(active) = state.active.take() else {
            return;
        };
        commands.entity(active.camera).despawn();
        images.remove(&active.image);
    }
}

fn on_captured(
    captured: On<ScreenshotCaptured>,
    outs: Query<&ThumbOut>,
    mut state: ResMut<ThumbnailState>,
) {
    let Ok(ThumbOut(out)) = outs.get(captured.entity) else {
        return;
    };
    let Some(data) = captured.image.data.as_deref() else {
        state.last_error = Some("screenshot sem dados".into());
        return;
    };
    let width = captured.image.width();
    let height = captured.image.height();
    let Some(buffer) = image::RgbaImage::from_raw(width, height, data.to_vec()) else {
        state.last_error = Some("screenshot com tamanho inesperado".into());
        return;
    };
    if let Some(parent) = out.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    match buffer.save(out) {
        Ok(()) => state.last_error = None,
        Err(err) => state.last_error = Some(format!("{}: {err}", out.display())),
    }
}

pub fn plugin(app: &mut App) {
    app.init_resource::<ThumbnailRequest>()
        .init_resource::<ThumbnailState>()
        .add_systems(Update, (handle_request, tick).chain())
        .add_observer(on_captured);
}
