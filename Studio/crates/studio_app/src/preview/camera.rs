//! Câmara órbita do viewport (adaptada do `editor_camera` do Vael).

use bevy::{
    input::mouse::{AccumulatedMouseMotion, AccumulatedMouseScroll},
    prelude::*,
};
use bevy_ext_gltf::GlbBounds;

use super::{PreviewCommand, ViewportInfo};

#[derive(Component)]
pub struct PreviewCamera;

#[derive(Resource, Debug, Clone, Copy)]
pub struct OrbitCamera {
    pub target: Vec3,
    pub yaw_deg: f32,
    pub pitch_deg: f32,
    pub distance: f32,
    pub turntable: bool,
}

impl Default for OrbitCamera {
    fn default() -> Self {
        Self {
            target: Vec3::ZERO,
            yaw_deg: 35.0,
            pitch_deg: 18.0,
            distance: 6.0,
            turntable: false,
        }
    }
}

const MIN_DISTANCE: f32 = 0.15;
const MAX_DISTANCE: f32 = 500.0;
const MIN_PITCH: f32 = -88.0;
const MAX_PITCH: f32 = 88.0;

impl OrbitCamera {
    pub fn eye(&self) -> Vec3 {
        let yaw = self.yaw_deg.to_radians();
        let pitch = self.pitch_deg.to_radians();
        let dir = Vec3::new(
            yaw.sin() * pitch.cos(),
            pitch.sin(),
            yaw.cos() * pitch.cos(),
        );
        self.target + dir * self.distance
    }

    pub fn fit(&mut self, bounds: GlbBounds) {
        let center = bounds.center();
        let half = bounds.half_extents();
        let radius = half.length().max(0.2);
        self.target = center;
        self.distance = (radius * 2.6).clamp(MIN_DISTANCE, MAX_DISTANCE);
        self.yaw_deg = 35.0;
        self.pitch_deg = 18.0;
    }

    pub fn orbit(&mut self, dx: f32, dy: f32) {
        self.yaw_deg -= dx * 0.35;
        self.pitch_deg = (self.pitch_deg + dy * 0.35).clamp(MIN_PITCH, MAX_PITCH);
    }

    pub fn pan(&mut self, dx: f32, dy: f32) {
        let forward = (self.target - self.eye()).normalize_or_zero();
        let right = forward.cross(Vec3::Y).normalize_or_zero();
        let up = right.cross(forward).normalize_or_zero();
        let scale = self.distance * 0.0018;
        self.target += (-right * dx + up * dy) * scale;
    }

    pub fn zoom(&mut self, lines: f32) {
        let factor = (-lines * 0.12).exp();
        self.distance = (self.distance * factor).clamp(MIN_DISTANCE, MAX_DISTANCE);
    }
}

pub fn orbit_input(
    mut camera: ResMut<OrbitCamera>,
    mut command: ResMut<PreviewCommand>,
    info: Res<ViewportInfo>,
    buttons: Res<ButtonInput<MouseButton>>,
    keys: Res<ButtonInput<KeyCode>>,
    motion: Res<AccumulatedMouseMotion>,
    scroll: Res<AccumulatedMouseScroll>,
    time: Res<Time>,
) {
    if let Some(turntable) = command.turntable.take() {
        camera.turntable = turntable;
    }
    if camera.turntable {
        camera.yaw_deg += 18.0 * time.delta_secs();
    }

    if info.egui_wants_pointer || !info.pointer_in_viewport {
        return;
    }
    let pan = keys.pressed(KeyCode::ShiftLeft) || keys.pressed(KeyCode::ShiftRight);
    if buttons.pressed(MouseButton::Right) || buttons.pressed(MouseButton::Middle) {
        if pan {
            camera.pan(motion.delta.x, motion.delta.y);
        } else {
            camera.orbit(motion.delta.x, motion.delta.y);
        }
    }
    if scroll.delta.y.abs() > f32::EPSILON {
        camera.zoom(scroll.delta.y);
    }
}

pub fn apply_camera(
    camera: Res<OrbitCamera>,
    mut query: Query<&mut Transform, With<PreviewCamera>>,
) {
    let eye = camera.eye();
    for mut transform in &mut query {
        transform.translation = eye;
        transform.look_at(camera.target, Vec3::Y);
    }
}
