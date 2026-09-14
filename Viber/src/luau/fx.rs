//! Primitivas de FX e de combate expostas a scripts (Onda 3 do
//! desacoplamento): câmara (shake/kick/fov), pós-processo (punch), hit-stop,
//! bursts/anéis de partículas, números de dano, dano em área e clip de ação.
//!
//! Todas REUSAM a lógica nativa (constantes e funções de `skills`/`combat`/
//! `particles`/`impact`/`camera`/`physics_fx`) — a duplicação aqui é só a
//! costura. Os recursos são `Option` porque cada preset traz um subconjunto
//! diferente (o `run` registra os de FX puros mesmo em `gameplay: none`; num
//! teste minimal podem faltar todos e as primitivas degradam para no-op).

use bevy::prelude::*;

use crate::feedback::DamageNumberEvent;

/// Recursos de FX consumidos pelas primitivas de script.
#[derive(bevy::ecs::system::SystemParam)]
pub struct ScriptFx<'w> {
    pub shake: Option<ResMut<'w, crate::camera::CameraShake>>,
    pub kick: Option<ResMut<'w, crate::camera::CameraKick>>,
    pub fov: Option<ResMut<'w, crate::camera::CameraFx>>,
    pub postfx: Option<ResMut<'w, crate::postfx::PostFxState>>,
    pub hit_stop: Option<ResMut<'w, crate::combat::HitStop>>,
    pub numbers: Option<bevy::ecs::message::MessageWriter<'w, DamageNumberEvent>>,
    pub meshes: Option<ResMut<'w, Assets<Mesh>>>,
    pub materials: Option<ResMut<'w, Assets<StandardMaterial>>>,
}

impl ScriptFx<'_> {
    pub fn shake(&mut self, amount: f32) {
        if let Some(shake) = self.shake.as_deref_mut() {
            crate::camera::add_camera_shake(shake, amount);
        }
    }

    pub fn kick(&mut self, impulse: Vec3) {
        if let Some(kick) = self.kick.as_deref_mut() {
            crate::camera::add_camera_kick(kick, impulse);
        }
    }

    pub fn fov_kick(&mut self, deg: f32) {
        if let Some(fx) = self.fov.as_deref_mut() {
            crate::camera::fov_kick(fx, deg);
        }
    }

    pub fn punch(&mut self, stops: f32, bloom: f32) {
        if let Some(postfx) = self.postfx.as_deref_mut() {
            crate::postfx::punch_impact(postfx, stops, bloom);
        }
    }

    pub fn hit_stop(&mut self, secs: f32) {
        if let Some(stop) = self.hit_stop.as_deref_mut() {
            crate::combat::request_hit_stop(stop, secs);
        }
    }

    pub fn damage_number(&mut self, text: String, pos: Vec3, color: Option<[f32; 3]>) {
        if let Some(numbers) = self.numbers.as_mut() {
            numbers.write(DamageNumberEvent {
                position: pos,
                text,
                color: color
                    .map(|[r, g, b]| Color::srgb(r, g, b))
                    .unwrap_or(Color::srgb(1.0, 0.9, 0.4)),
            });
        }
    }

    /// Burst de partículas com o preset autoral (`particles::PRESET_NAMES`);
    /// unknown já foi barrado à fila (erro de script). `"sparks"` é o preset
    /// de IMPACTO do melee nativo (`combat::hit_sparks_spec`) — vive fora da
    /// biblioteca porque a spec traz tamanho/vida colonados para o golpe.
    pub fn burst(&mut self, commands: &mut Commands, preset: &str, pos: Vec3, count: usize) {
        if preset == "sparks" {
            let spec = crate::combat::hit_sparks_spec();
            crate::particles::spawn_burst(commands, &spec, pos, count);
            return;
        }
        let spec = crate::recipes::ParticleSpec {
            preset: preset.to_string(),
            emission_rate: None,
            life: None,
            speed: None,
            size: None,
            color: None,
            shape_radius: None,
            looping: false,
            world_space: false,
        };
        crate::particles::spawn_burst(commands, &spec, pos, count);
    }

    /// Anel de choque no chão (`impact::spawn_impact_ring`).
    pub fn ring(&mut self, commands: &mut Commands, x: f32, z: f32, radius: f32, color: Option<[f32; 3]>) {
        let (Some(meshes), Some(materials)) = (self.meshes.as_deref_mut(), self.materials.as_deref_mut())
        else {
            return;
        };
        crate::impact::spawn_impact_ring(
            commands,
            meshes,
            materials,
            Vec3::new(x, 0.0, z),
            radius,
            color
                .map(|[r, g, b]| Color::srgb(r, g, b))
                .unwrap_or(Color::srgba(0.85, 0.9, 1.0, 0.9)),
        );
    }
}
