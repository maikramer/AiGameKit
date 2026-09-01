//! `<ParticleSystem>` runtime: CPU particle emitters drawn as camera-facing
//! quads in one dynamic mesh per emitter.
//!
//! Presets mirror the VibeGame particle library (fire, smoke, fireflies, …);
//! the `particle-emitter="…"` component-string in a world overrides the
//! preset fields. Particles live in emitter-local space (the emitters in this
//! world are static, so local and world space are equivalent).

use bevy::math::Vec3;
use bevy::prelude::*;

use crate::recipes::ParticleSpec;

/// Resolved emitter values (preset defaults + world overrides).
#[derive(Debug, Clone)]
pub struct ResolvedEmitter {
    pub emission_rate: f32,
    pub life: (f32, f32),
    pub speed: (f32, f32),
    pub size: (f32, f32),
    pub color_a: [f32; 3],
    pub color_b: [f32; 3],
    /// World acceleration applied to each particle (m/s²).
    pub gravity: Vec3,
    /// Spawn spread radius around the emitter origin.
    pub radius: f32,
    pub additive: bool,
    /// Particle grows (`>1`) or shrinks (`<1`) linearly to this factor over
    /// its lifetime.
    pub end_size_factor: f32,
}

/// Preset library — mirrors `VibeGame/src/plugins/particles/presets.ts` for
/// the presets this world uses (values from the TS factories where present).
pub fn preset(name: &str) -> ResolvedEmitter {
    // Defaults roughly matching `fire`; each arm overrides what differs.
    let (rate, life, speed, size, color_a, color_b, gravity, radius, additive, end_size) =
        match name {
            "fire" => (
                55.0,
                (0.5, 1.4),
                (1.5, 3.5),
                (0.35, 0.7),
                [1.0, 0.85, 0.25],
                [1.0, 0.35, 0.05],
                Vec3::new(0.0, 1.2, 0.0),
                0.12,
                true,
                0.05,
            ),
            "smoke" => (
                8.0,
                (2.0, 4.0),
                (0.4, 1.0),
                (0.4, 0.9),
                [0.53, 0.53, 0.53],
                [0.35, 0.35, 0.35],
                Vec3::new(0.0, 0.6, 0.0),
                0.25,
                false,
                2.4,
            ),
            "fireflies" => (
                14.0,
                (2.5, 5.0),
                (0.15, 0.55),
                (0.08, 0.18),
                [0.65, 1.0, 0.25],
                [0.95, 1.0, 0.45],
                Vec3::ZERO,
                3.5,
                true,
                1.0,
            ),
            "ground-dust" => (
                6.0,
                (1.5, 3.0),
                (0.2, 0.6),
                (0.2, 0.5),
                [0.62, 0.55, 0.44],
                [0.45, 0.40, 0.32],
                Vec3::new(0.0, -0.1, 0.0),
                0.6,
                false,
                1.6,
            ),
            "sparkle" => (
                10.0,
                (0.6, 1.2),
                (0.3, 1.0),
                (0.06, 0.14),
                [1.0, 1.0, 1.0],
                [0.6, 0.9, 1.0],
                Vec3::new(0.0, -0.4, 0.0),
                0.4,
                true,
                0.2,
            ),
            "leaves" => (
                5.0,
                (4.0, 7.0),
                (0.2, 0.6),
                (0.1, 0.22),
                [0.35, 0.62, 0.25],
                [0.55, 0.45, 0.2],
                Vec3::new(0.0, -0.5, 0.0),
                1.2,
                false,
                1.0,
            ),
            "snow" => (
                20.0,
                (4.0, 8.0),
                (0.4, 1.0),
                (0.06, 0.16),
                [1.0, 1.0, 1.0],
                [0.85, 0.92, 1.0],
                Vec3::new(0.0, -0.8, 0.0),
                6.0,
                false,
                1.0,
            ),
            "sand-dust" => (
                8.0,
                (2.0, 4.0),
                (0.6, 1.4),
                (0.4, 1.1),
                [0.85, 0.72, 0.5],
                [0.7, 0.58, 0.4],
                Vec3::new(0.4, 0.05, 0.0),
                0.8,
                false,
                1.8,
            ),
            "magic" => (
                18.0,
                (0.8, 1.6),
                (0.5, 1.5),
                (0.1, 0.25),
                [0.6, 0.35, 1.0],
                [0.3, 0.8, 1.0],
                Vec3::new(0.0, 0.8, 0.0),
                0.3,
                true,
                0.1,
            ),
            // "core": bright fast core of bigger effects (forge, portals)
            _ => (
                40.0,
                (0.3, 0.8),
                (1.0, 2.5),
                (0.15, 0.35),
                [1.0, 0.95, 0.7],
                [1.0, 0.5, 0.15],
                Vec3::new(0.0, 0.8, 0.0),
                0.1,
                true,
                0.1,
            ),
        };
    ResolvedEmitter {
        emission_rate: rate,
        life,
        speed,
        size,
        color_a,
        color_b,
        gravity,
        radius,
        additive,
        end_size_factor: end_size,
    }
}

/// Apply a world's `particle-emitter` overrides on top of the preset.
pub fn resolve(spec: &ParticleSpec) -> ResolvedEmitter {
    let mut resolved = preset(&spec.preset);
    if let Some(rate) = spec.emission_rate {
        resolved.emission_rate = rate;
    }
    if let Some(life) = spec.life {
        resolved.life = life;
    }
    if let Some(speed) = spec.speed {
        resolved.speed = speed;
    }
    if let Some(size) = spec.size {
        resolved.size = size;
    }
    if let Some(color) = spec.color {
        resolved.color_a = color;
        resolved.color_b = color;
    }
    if let Some(radius) = spec.shape_radius {
        resolved.radius = radius.max(0.0);
    }
    resolved
}

/// One live particle (emitter-local space).
#[derive(Debug, Clone, Copy)]
pub struct LiveParticle {
    pub pos: Vec3,
    pub vel: Vec3,
    pub life: f32,
    pub max_life: f32,
    pub size: f32,
    pub color: [f32; 3],
}

/// Particle spawn + integration, split from rendering so both are testable.
pub struct EmitterSim {
    pub resolved: ResolvedEmitter,
    pub accumulator: f32,
    pub particles: Vec<LiveParticle>,
    rng: crate::spawner::Rng,
}

impl EmitterSim {
    pub fn new(spec: &ParticleSpec) -> Self {
        let resolved = resolve(spec);
        let capacity = ((resolved.emission_rate * resolved.life.1).ceil() as usize + 16).min(1024);
        Self {
            resolved,
            accumulator: 0.0,
            particles: Vec::with_capacity(capacity),
            rng: crate::spawner::Rng::new(0x0DEFACED),
        }
    }

    /// Advance the emitter by `dt`: emit new particles, integrate, cull dead.
    pub fn step(&mut self, dt: f32) {
        // Integrate first, emit after — particles born this frame keep their
        // full lifetime instead of ageing a whole step at birth.
        let gravity = self.resolved.gravity;
        self.particles.retain_mut(|p| {
            p.life -= dt;
            if p.life <= 0.0 {
                return false;
            }
            p.vel += gravity * dt;
            p.pos += p.vel * dt;
            true
        });
        if self.resolved.emission_rate > 0.0 {
            self.accumulator += self.resolved.emission_rate * dt;
            while self.accumulator >= 1.0 {
                self.accumulator -= 1.0;
                self.spawn_one();
            }
        }
    }

    fn spawn_one(&mut self) {
        let r = &mut self.rng;
        let life = r.range(self.resolved.life.0, self.resolved.life.1);
        let speed = r.range(self.resolved.speed.0, self.resolved.speed.1);
        let size = r.range(self.resolved.size.0, self.resolved.size.1);
        let mix = r.next_f32();
        let color = [
            self.resolved.color_a[0] + (self.resolved.color_b[0] - self.resolved.color_a[0]) * mix,
            self.resolved.color_a[1] + (self.resolved.color_b[1] - self.resolved.color_a[1]) * mix,
            self.resolved.color_a[2] + (self.resolved.color_b[2] - self.resolved.color_a[2]) * mix,
        ];
        // Cone-ish upward spread (matches the upright-cone presets of the
        // original library; fireflies/snow read fine with the wide radius).
        let angle = r.range(0.0, std::f32::consts::TAU);
        let spread = r.next_f32().sqrt() * 0.5;
        let offset = bevy::math::Vec2::new(angle.cos() * spread, angle.sin() * spread);
        let disc = r.unit_disc() * self.resolved.radius;
        self.particles.push(LiveParticle {
            pos: Vec3::new(disc.x, 0.0, disc.y),
            vel: Vec3::new(offset.x * speed, speed, offset.y * speed),
            life,
            max_life: life,
            size,
            color,
        });
    }
}

/// Write one emitter's live particles into `mesh` as camera-facing quads.
///
/// The mesh has FIXED capacity (created by [`particle_mesh`]) — buffers are
/// never reallocated (per-frame reallocation trips the GPU slab allocator's
/// use-after-free check). Unused slots are degenerate zero-area quads. Vertex
/// colors carry the fade (alpha = remaining life); the end-size factor scales
/// each quad over its lifetime. Returns the live vertex count written.
pub fn write_billboards(
    mesh: &mut bevy::mesh::Mesh,
    particles: &[LiveParticle],
    emitter_pos: Vec3,
    camera_pos: Vec3,
    end_size_factor: f32,
    capacity: usize,
) -> usize {
    let capacity = capacity.max(1);
    let live = particles.len().min(capacity);
    let total = capacity * 4;
    let mut positions: Vec<[f32; 3]> = vec![[0.0; 3]; total];
    let mut colors: Vec<[f32; 4]> = vec![[0.0; 4]; total];
    let normals: Vec<[f32; 3]> = vec![[0.0, 1.0, 0.0]; total];
    let mut uvs: Vec<[f32; 2]> = vec![[0.0; 2]; total];

    let to_camera = (camera_pos - emitter_pos).normalize_or_zero();
    let right = Vec3::Y.cross(to_camera).normalize_or_zero();
    let up = if right.length_squared() > f32::EPSILON {
        to_camera.cross(right).normalize_or_zero()
    } else {
        Vec3::Y
    };

    for (index, p) in particles.iter().take(live).enumerate() {
        let t = (p.life / p.max_life).clamp(0.0, 1.0);
        let size = p.size * (1.0 + (end_size_factor - 1.0) * (1.0 - t));
        let half = size * 0.5;
        let alpha = t;
        let base = index * 4;
        for (corner, (dx, dy)) in [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
            .into_iter()
            .enumerate()
        {
            let world = emitter_pos + p.pos + right * (dx * half) + up * (dy * half);
            positions[base + corner] = world.to_array();
            colors[base + corner] = [p.color[0], p.color[1], p.color[2], alpha];
            uvs[base + corner] = [(dx + 1.0) * 0.5, (dy + 1.0) * 0.5];
        }
    }

    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_POSITION, positions);
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_COLOR, colors);
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_NORMAL, normals);
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_UV_0, uvs);
    mesh.insert_indices(bevy::mesh::Indices::U32(fixed_indices(capacity)));
    live
}

/// Fixed index buffer for `capacity` quads — created once per emitter mesh.
fn fixed_indices(capacity: usize) -> Vec<u32> {
    let mut indices = Vec::with_capacity(capacity * 6);
    for quad in 0..capacity as u32 {
        let base = quad * 4;
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3]);
    }
    indices
}

/// Fixed-capacity dynamic mesh for one emitter (never reallocated; unused
/// slots are degenerate zero-area quads at the origin).
pub fn particle_mesh(capacity: usize) -> bevy::mesh::Mesh {
    use bevy::asset::RenderAssetUsages;
    let capacity = capacity.max(1);
    let mut mesh = bevy::mesh::Mesh::new(
        bevy::render::mesh::PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(
        bevy::mesh::Mesh::ATTRIBUTE_POSITION,
        vec![[0.0f32; 3]; capacity * 4],
    );
    mesh.insert_attribute(
        bevy::mesh::Mesh::ATTRIBUTE_COLOR,
        vec![[0.0f32; 4]; capacity * 4],
    );
    mesh.insert_attribute(
        bevy::mesh::Mesh::ATTRIBUTE_NORMAL,
        vec![[0.0f32, 1.0, 0.0]; capacity * 4],
    );
    mesh.insert_attribute(
        bevy::mesh::Mesh::ATTRIBUTE_UV_0,
        vec![[0.0f32; 2]; capacity * 4],
    );
    mesh.insert_indices(bevy::mesh::Indices::U32(fixed_indices(capacity)));
    mesh
}

/// Fixed quad budget for one emitter mesh (bounds GPU buffer size).
pub fn emitter_capacity(resolved: &ResolvedEmitter) -> usize {
    ((resolved.emission_rate * resolved.life.1).ceil() as usize + 8).clamp(8, 512)
}

/// Per-emitter component: simulation state plus its mesh capacity.
#[derive(Component)]
pub struct ParticleEmitter {
    pub sim: EmitterSim,
    pub capacity: usize,
}

/// Advance every emitter and rewrite its billboard mesh.
pub fn particle_emitter_update(
    time: Res<Time>,
    cameras: Query<&GlobalTransform, With<Camera>>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut emitters: Query<(&GlobalTransform, &mut ParticleEmitter, &Mesh3d)>,
) {
    let Ok(camera) = cameras.single() else {
        return;
    };
    let camera_pos = camera.translation();
    let dt = time.delta_secs().clamp(0.0, 0.1);
    for (transform, mut emitter, mesh_handle) in &mut emitters {
        emitter.sim.step(dt);
        if let Some(mut mesh) = meshes.get_mut(&mesh_handle.0) {
            write_billboards(
                &mut mesh,
                &emitter.sim.particles,
                transform.translation(),
                camera_pos,
                emitter.sim.resolved.end_size_factor,
                emitter.capacity,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fire_spec() -> ParticleSpec {
        ParticleSpec {
            preset: "fire".into(),
            emission_rate: Some(20.0),
            life: Some((0.5, 1.0)),
            speed: Some((1.0, 2.0)),
            size: Some((0.2, 0.5)),
            color: None,
            shape_radius: None,
            looping: true,
            world_space: false,
        }
    }

    #[test]
    fn test_preset_library_covers_world_presets() {
        for name in [
            "fire",
            "smoke",
            "fireflies",
            "ground-dust",
            "sparkle",
            "leaves",
            "snow",
            "sand-dust",
            "magic",
            "core",
        ] {
            let p = preset(name);
            assert!(p.emission_rate > 0.0, "{name}");
            assert!(p.life.0 > 0.0 && p.life.1 >= p.life.0, "{name}");
        }
        // unknown preset falls back to the generic core
        assert_eq!(preset("nope").emission_rate, preset("core").emission_rate);
    }

    #[test]
    fn test_emitter_sim_emits_and_culls() {
        let mut sim = EmitterSim::new(&fire_spec());
        sim.step(1.0); // 20/s * 1s → ~20 particles
        assert!(!sim.particles.is_empty(), "emission produced particles");
        let live = sim.particles.len();
        for _ in 0..40 {
            sim.step(0.2);
        }
        assert!(
            sim.particles.len() <= live,
            "particles die after their lifetime"
        );
    }

    #[test]
    fn test_write_billboards_writes_four_verts_per_particle() {
        let mut sim = EmitterSim::new(&fire_spec());
        let capacity = emitter_capacity(&sim.resolved);
        sim.step(1.0);
        let count = sim.particles.len().min(capacity);
        let mut mesh = particle_mesh(capacity);
        let written = write_billboards(
            &mut mesh,
            &sim.particles,
            Vec3::ZERO,
            Vec3::new(0.0, 0.0, 10.0),
            0.1,
            capacity,
        );
        assert_eq!(written, count);
        assert_eq!(mesh.count_vertices(), capacity * 4);
    }

    #[test]
    fn test_write_billboards_empty_mesh_stays_degenerate() {
        let mut mesh = particle_mesh(8);
        let written = write_billboards(&mut mesh, &[], Vec3::ZERO, Vec3::Z, 1.0, 8);
        assert_eq!(written, 0);
        assert_eq!(mesh.count_vertices(), 32);
    }

    /// `shape-radius` is what spreads a campfire's flame across its pit
    /// instead of firing a single jet from the centre.
    #[test]
    fn test_shape_radius_overrides_the_preset_spread() {
        let base = resolve(&fire_spec());
        let mut wide_spec = fire_spec();
        wide_spec.shape_radius = Some(base.radius + 1.5);
        let wide = resolve(&wide_spec);
        assert!((wide.radius - (base.radius + 1.5)).abs() < 1e-5);

        // A negative radius is clamped rather than inverting the spread.
        let mut bad_spec = fire_spec();
        bad_spec.shape_radius = Some(-3.0);
        assert_eq!(resolve(&bad_spec).radius, 0.0);
    }
}
