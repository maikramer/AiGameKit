//! `<ParticleSystem>` runtime: CPU particle emitters drawn as camera-facing
//! quads in one dynamic mesh per emitter.
//!
//! Presets mirror the VibeGame particle library (fire, smoke, fireflies, …);
//! the `particle-emitter="…"` component-string in a world overrides the
//! preset fields. Particles live in emitter-local space (the emitters in this
//! world are static, so local and world space are equivalent).

use bevy::asset::RenderAssetUsages;
use bevy::light::NotShadowCaster;
use bevy::math::Vec3;
use bevy::prelude::*;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat};

use crate::profiler::{Group, timed};
use crate::recipes::ParticleSpec;

/// Resolved emitter values (preset defaults + world overrides).
#[derive(Debug, Clone)]
pub struct ResolvedEmitter {
    pub emission_rate: f32,
    pub life: (f32, f32),
    pub speed: (f32, f32),
    pub size: (f32, f32),
    /// Extensão VERTICAL do quad (m) — gotas de chuva são esticadas,
    /// fumo/faíscas são quadradas (`size_y == size`). Overridable via
    /// `size-y` no component-string.
    pub size_y: (f32, f32),
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
            // Combate (presets do VibeGame `presets.ts`): slash do golpe,
            // sparks do impacto, shockwave do finisher — todos em burst.
            "slash" => (
                0.0,
                (0.12, 0.22),
                (0.4, 1.2),
                (0.28, 0.5),
                [1.0, 1.0, 1.0],
                [0.75, 0.88, 1.0],
                Vec3::ZERO,
                0.25,
                true,
                0.2,
            ),
            "sparks" => (
                0.0,
                (0.18, 0.4),
                (2.5, 6.0),
                (0.05, 0.12),
                [1.0, 0.92, 0.55],
                [1.0, 0.5, 0.12],
                Vec3::new(0.0, -7.0, 0.0),
                0.15,
                true,
                0.3,
            ),
            "explosion" => (
                0.0,
                (0.3, 0.6),
                (3.0, 7.0),
                (0.4, 0.9),
                [1.0, 0.8, 0.3],
                [1.0, 0.3, 0.05],
                Vec3::new(0.0, 1.0, 0.0),
                0.5,
                true,
                2.0,
            ),
            // Água (`terrain::water_fx`): `splash` é a coroa de gotas do
            // impacto (entrada/salto), `wade` são os salpicos contínuos de
            // quem caminha dentro de água — mais lentos e mais pequenos.
            "splash" => (
                0.0,
                (0.35, 0.75),
                (2.2, 5.5),
                (0.10, 0.26),
                [0.92, 0.97, 1.0],
                [0.62, 0.80, 0.86],
                Vec3::new(0.0, -12.0, 0.0),
                0.35,
                false,
                0.45,
            ),
            "wade" => (
                0.0,
                (0.25, 0.5),
                (0.9, 2.4),
                (0.07, 0.16),
                [0.95, 0.99, 1.0],
                [0.70, 0.86, 0.90],
                Vec3::new(0.0, -10.0, 0.0),
                0.22,
                false,
                0.5,
            ),
            // Espuma ambiente da linha de água (`spawn_water`): manchas
            // brancas que nascem na margem, crescem e dissolvem — não é
            // spray (isso é splash/wade), é a lámina viva da borda.
            "foam" => (
                6.0,
                (1.2, 2.6),
                (0.04, 0.2),
                (0.25, 0.6),
                [0.94, 0.98, 1.0],
                [0.72, 0.85, 0.9],
                Vec3::new(0.0, 0.12, 0.0),
                0.8,
                false,
                1.9,
            ),
            // Névoa de cascata (`spawn_water`): nuvem baixa na base da
            // queda — mais lenta, maior e mais densa que o foam.
            "mist" => (
                9.0,
                (1.6, 3.2),
                (0.1, 0.45),
                (0.4, 1.0),
                [0.96, 0.99, 1.0],
                [0.66, 0.8, 0.87],
                Vec3::new(0.0, 0.35, 0.0),
                0.5,
                false,
                2.2,
            ),
            // Chuva (WS-A): o emissor âncora do player usa este preset — gotas
            // finas ESTICADAS (`size_y`), Blend translúcido, rate alto e queda
            // rápida (speed negativo = vel.y para baixo). O driver escalá o
            // rate pela intensidade contínua do `<Weather>` (0 = escondido).
            "rain" => (
                500.0,
                (0.55, 0.85),
                (-24.0, -18.0),
                (0.02, 0.035),
                [0.62, 0.70, 0.80],
                [0.72, 0.80, 0.88],
                Vec3::new(0.0, -12.0, 0.0),
                9.0,
                false,
                1.0,
            ),
            // Spray de cachoeira (`spawn_water`): streaks brancos a
            // despenca do lip — chuva esticada mais curta e densa,
            // ancorada ao topo da cortina. O mesh da cortina é a água;
            // o spray é o que se perde na descida.
            "spray" => (
                90.0,
                (0.5, 0.9),
                (-16.0, -10.0),
                (0.025, 0.05),
                [0.88, 0.95, 1.0],
                [0.72, 0.85, 0.94],
                Vec3::new(0.0, -9.0, 0.0),
                1.0,
                false,
                1.2,
            ),
            // Ondinha no chão onde a gota aterra (WS-A): mini-burst estilo
            // ground-dust — anel BAIXO e largo que cresce e morre em ~0.3 s.
            "rain_ripple" => (
                0.0,
                (0.18, 0.3),
                (0.4, 1.0),
                (0.10, 0.22),
                [0.72, 0.80, 0.88],
                [0.55, 0.64, 0.75],
                Vec3::new(0.0, -6.0, 0.0),
                0.15,
                false,
                1.8,
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
    // Proporção vertical: só os presets elongados (chuva, spray de
    // cachoeira) a sobrepõem; todo o resto mantém quads quadrados como antes.
    let size_y = if matches!(name, "rain" | "spray") {
        (0.38, 0.55)
    } else {
        size
    };
    ResolvedEmitter {
        emission_rate: rate,
        life,
        speed,
        size,
        size_y,
        color_a,
        color_b,
        gravity,
        radius,
        additive,
        end_size_factor: end_size,
    }
}

/// Warn 1×/processo: o pedido (`rate × vida-máx`) estourou o teto declarativo
/// do mesh — partículas extra nascem mas nunca são desenhadas. Dedupe global
/// (padrão "warn 1x" do repo) porque `resolve` corre por emissor.
static CAPACITY_WARNED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

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
    // Teto mais apertado primeiro: emissores ambiente (teto 1024) só
    // truncam acima disso, mas o autor precisa de saber do limite do
    // `<ParticleSystem>` na mesma — a mensagem nomeia os dois.
    let required = (resolved.emission_rate * resolved.life.1).ceil() as usize + CAPACITY_HEADROOM;
    if required > EMITTER_MESH_CAP
        && !CAPACITY_WARNED.swap(true, std::sync::atomic::Ordering::Relaxed)
    {
        warn!(
            "particle '{}': rate×vida-máx pede ~{required} quads e o mesh do emissor tem \
             teto {EMITTER_MESH_CAP} (<ParticleSystem>) / {AMBIENT_MESH_CAP} (ambiente) — \
             baixe emission-rate ou start-life-max, senão o render trunca",
            spec.preset
        );
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
    /// Extensão horizontal do quad (m).
    pub size: f32,
    /// Extensão vertical do quad (m) — `== size` nos presets quadrados.
    pub size_y: f32,
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
        Self::seeded(spec, Vec3::ZERO)
    }

    /// Seed por emissor — os 105+ emissores do simple-rpg partilhavam a
    /// mesma sequência RNG e todas as chamas "respiravam" em lockstep.
    /// Derivar da posição (como o spawner faz) mantém o determinismo.
    pub fn seeded(spec: &ParticleSpec, position: Vec3) -> Self {
        let resolved = resolve(spec);
        let capacity = capacity_quads(resolved.emission_rate, resolved.life.1, AMBIENT_MESH_CAP);
        let position_seed = (position.x.to_bits() as u64)
            ^ (position.y.to_bits() as u64) << 21
            ^ (position.z.to_bits() as u64) << 42;
        Self {
            resolved,
            accumulator: 0.0,
            particles: Vec::with_capacity(capacity),
            rng: crate::spawner::Rng::new(0x0DEFACED ^ position_seed | 1),
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
        let size_y = r.range(self.resolved.size_y.0, self.resolved.size_y.1);
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
            size_y,
            color,
        });
    }
}

/// Fator de escala do billboard ao longo da vida (`t` = vida restante
/// normalizada 0..1): linear de 1 (nascimento) até `end_size_factor` (morte).
pub fn end_size_scale(t: f32, end_size_factor: f32) -> f32 {
    1.0 + (end_size_factor - 1.0) * (1.0 - t)
}

/// Offsets locais dos 4 cantos do billboard a partir de `p.pos` — ordem
/// idêntica aos UVs constantes do mesh (BL, BR, TR, TL). Escala X e Y
/// SEPARADAS (gotas de chuva são esticadas); função pura para teste.
pub fn billboard_corner_offsets(size_x: f32, size_y: f32, right: Vec3, up: Vec3) -> [Vec3; 4] {
    let hx = size_x * 0.5;
    let hy = size_y * 0.5;
    [
        right * (-hx) - up * hy,
        right * hx - up * hy,
        right * hx + up * hy,
        right * (-hx) + up * hy,
    ]
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

    let to_camera = (camera_pos - emitter_pos).normalize_or_zero();
    let right = Vec3::Y.cross(to_camera).normalize_or_zero();
    let up = if right.length_squared() > f32::EPSILON {
        to_camera.cross(right).normalize_or_zero()
    } else {
        Vec3::Y
    };

    // Only POSITION and COLOR change per frame, and both are rewritten
    // IN PLACE — allocating two fresh vectors per emitter per frame cost
    // ~12k allocations/s in the simple-rpg world (105 emitters) and the
    // insert_attribute dance re-uploaded three static buffers to the GPU.
    // Normals, UVs and the index buffer are constants of the slot layout and
    // are written once by [`particle_mesh`]. `attributes_mut` yields both
    // mutable slots from a single borrow of the mesh.
    let mut position_slot: Option<&mut bevy::mesh::VertexAttributeValues> = None;
    let mut color_slot: Option<&mut bevy::mesh::VertexAttributeValues> = None;
    for (attribute, values) in mesh.attributes_mut() {
        if *attribute == bevy::mesh::Mesh::ATTRIBUTE_POSITION {
            position_slot = Some(values);
        } else if *attribute == bevy::mesh::Mesh::ATTRIBUTE_COLOR {
            color_slot = Some(values);
        }
    }
    let bevy::mesh::VertexAttributeValues::Float32x3(positions) =
        position_slot.expect("particle mesh carries a fixed POSITION buffer")
    else {
        panic!("particle mesh POSITION attribute is Float32x3");
    };
    let bevy::mesh::VertexAttributeValues::Float32x4(colors) =
        color_slot.expect("particle mesh carries a fixed COLOR buffer")
    else {
        panic!("particle mesh COLOR attribute is Float32x4");
    };
    debug_assert_eq!(positions.len(), capacity * 4);
    debug_assert_eq!(colors.len(), capacity * 4);
    // Dead slots must fall back to degenerate zero-area quads — in-place
    // writing keeps the previous frame's vertices otherwise.
    positions.fill([0.0; 3]);
    colors.fill([0.0; 4]);

    for (index, p) in particles.iter().take(live).enumerate() {
        // max_life a 0 (start-life-min: 0 no XML) dava 0/0 = NaN no t e a
        // cor do vértice nascia NaN — epsilon no divisor.
        let t = (p.life / p.max_life.max(f32::EPSILON)).clamp(0.0, 1.0);
        let scale = end_size_scale(t, end_size_factor);
        let alpha = t;
        let base = index * 4;
        let corners = billboard_corner_offsets(p.size * scale, p.size_y * scale, right, up);
        for (corner, offset) in corners.into_iter().enumerate() {
            positions[base + corner] = (emitter_pos + p.pos + offset).to_array();
            colors[base + corner] = [p.color[0], p.color[1], p.color[2], alpha];
        }
    }

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
    // Corner UVs are a constant of the quad layout (the same four corners the
    // billboard writer emits, in the same order).
    let mut uvs: Vec<[f32; 2]> = Vec::with_capacity(capacity * 4);
    for _ in 0..capacity {
        for (dx, dy) in [(-1.0f32, -1.0f32), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)] {
            uvs.push([(dx + 1.0) * 0.5, (dy + 1.0) * 0.5]);
        }
    }
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_UV_0, uvs);
    mesh.insert_indices(bevy::mesh::Indices::U32(fixed_indices(capacity)));
    mesh
}

// ── capacidade do emissor (UMA fórmula) ─────────────────────────────────
// O mesh do emissor é de capacidade FIXA (nunca realocado — ver
// [`write_billboards`]): um sim maior que o mesh desenha só uma fatia das
// partículas. A fórmula vivia DUPLICADA com folgas/tetos diferentes
// (`<ParticleSystem>` +8/512 vs emissores ambiente +16/1024) e um XML com
// emission-rate alto truncava o render de forma diferente consoante o site
// que spawna o emissor — agora todos passam por aqui, com o teto por site
// como argumento (os tetos mantêm-se: 512 declarativo, 1024 ambiente).

/// Folga sobre o pico teórico `rate × vida-máx` (partículas nascidas no
/// mesmo frame em que as mais velhas ainda vivem).
const CAPACITY_HEADROOM: usize = 16;
/// Piso do mesh — rate 0 (sim de burst) nunca dá mesh vazio.
const CAPACITY_FLOOR: usize = 8;
/// Teto do mesh de um `<ParticleSystem>` declarativo (bounds GPU buffer).
pub const EMITTER_MESH_CAP: usize = 512;
/// Teto dos emissores ambiente da engine (foam/mist/chuva — rate alto).
pub const AMBIENT_MESH_CAP: usize = 1024;

/// Quad budget de um emissor contínuo: `rate × vida-máx` arredondado para
/// cima + folga, dentro do teto do site. Função pura para teste.
pub fn capacity_quads(rate: f32, life_max: f32, cap: usize) -> usize {
    ((rate * life_max).ceil() as usize + CAPACITY_HEADROOM)
        .max(CAPACITY_FLOOR)
        .min(cap)
}

/// `<ParticleSystem>` (teto 512) — assinatura mantida para `recipes::spawn`.
pub fn emitter_capacity(resolved: &ResolvedEmitter) -> usize {
    capacity_quads(resolved.emission_rate, resolved.life.1, EMITTER_MESH_CAP)
}

// ── sprite radial suave (WS-A) ──────────────────────────────────────────
// Os billboards eram QUADRADOS duros (UVs cobrindo o quad inteiro, sem
// textura). O sprite abaixo é gerado em RUNTIME — sem ficheiro em disco —
// e ligado ao `base_color_texture` dos materiais dos emissores: o RGB é
// branco (a cor real vive nas VERTEX COLORS) e a forma vive no alpha, que
// multiplica o fade de vida. Funciona com `AlphaMode::Add` (fogo/faísca)
// e `Blend` (fumo/chuva).

/// Lado do sprite radial (px).
pub const SPRITE_SIZE: u32 = 64;

/// Falloff radial do alpha: patamar sólido até 25 % do raio e smoothstep até
/// 0 na borda do QUAD (r = 1 nos pontos médios das arestas; os cantos ficam a
/// r = √2 → 0). Função pura para teste.
pub fn soft_sprite_alpha(r: f32) -> f32 {
    let t = ((r - 0.25) / 0.75).clamp(0.0, 1.0);
    1.0 - t * t * (3.0 - 2.0 * t)
}

/// Buffer RGBA8 do sprite: [`soft_sprite_alpha`] varrido em [`SPRITE_SIZE`]².
pub fn soft_sprite_pixels(size: u32) -> Vec<u8> {
    let mut data = Vec::with_capacity((size * size * 4) as usize);
    for y in 0..size {
        for x in 0..size {
            let dx = (x as f32 + 0.5) / size as f32 * 2.0 - 1.0;
            let dy = (y as f32 + 0.5) / size as f32 * 2.0 - 1.0;
            let r = (dx * dx + dy * dy).sqrt();
            let alpha = (soft_sprite_alpha(r) * 255.0).round() as u8;
            data.extend_from_slice(&[255, 255, 255, alpha]);
        }
    }
    data
}

/// A `Image` pronta para `Assets<Image>` (mesma receita da arte gerada do HUD).
pub fn soft_sprite_image() -> Image {
    Image::new(
        Extent3d {
            width: SPRITE_SIZE,
            height: SPRITE_SIZE,
            depth_or_array_layers: 1,
        },
        TextureDimension::D2,
        soft_sprite_pixels(SPRITE_SIZE),
        TextureFormat::Rgba8UnormSrgb,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

/// Handle partilhado do sprite (criado uma vez por processo).
#[derive(Debug, Resource)]
pub struct ParticleSprite {
    pub texture: Handle<Image>,
}

/// Marca emissores cujo material já recebeu o sprite (o `spawn_*` não tem
/// `Assets<Image>` — assinaturas estáveis, o WS-D chama-as — por isso o bind
/// é post-hoc, um frame depois do spawn no pior caso: impercetível).
#[derive(Debug, Component)]
pub(crate) struct SpriteBound;

/// Liga o sprite radial ao material de cada emissor ainda não marcado.
#[allow(clippy::type_complexity)]
pub(crate) fn emitter_sprite_bind(
    mut images: Option<ResMut<Assets<Image>>>,
    mut materials: Option<ResMut<Assets<StandardMaterial>>>,
    sprite: Option<Res<ParticleSprite>>,
    mut commands: Commands,
    unbound: Query<
        (Entity, &MeshMaterial3d<StandardMaterial>),
        (With<ParticleEmitter>, Without<SpriteBound>),
    >,
) {
    let (Some(images), Some(materials)) = (images.as_mut(), materials.as_mut()) else {
        return;
    };
    let Some(sprite) = sprite.as_ref() else {
        // 1.º emissor do processo: gera a imagem; o bind acontece no tick
        // seguinte (o recurso entra via Commands).
        let texture = images.add(soft_sprite_image());
        commands.insert_resource(ParticleSprite { texture });
        return;
    };
    for (entity, material_handle) in &unbound {
        commands.entity(entity).insert(SpriteBound);
        if let Some(mut material) = materials.get_mut(&material_handle.0) {
            // Os materiais do `BurstPool` são PARTILHADOS e já vêm ligados:
            // só escreve — e só marca `Changed`, com o bind group novo que
            // isso custa — na primeira vez que um material vé esta textura.
            if material.base_color_texture.is_none() {
                material.base_color_texture = Some(sprite.texture.clone());
            }
        }
    }
}

/// Material comum dos emissores: unlit, cor das VERTEX COLORS; a textura é
/// ligada depois por [`emitter_sprite_bind`].
fn emitter_material(resolved: &ResolvedEmitter) -> StandardMaterial {
    StandardMaterial {
        base_color: Color::WHITE,
        unlit: true,
        alpha_mode: if resolved.additive {
            AlphaMode::Add
        } else {
            AlphaMode::Blend
        },
        ..Default::default()
    }
}

/// Emitters farther than this from the camera stop simulating and clear their
/// mesh. A campfire on the far side of a 4 km world is sub-pixel, but every
/// one of the 105 emitters in `simple-rpg` was still paying a full CPU
/// simulation step plus a vertex-buffer upload on every frame.
pub const EMITTER_CULL_DISTANCE: f32 = 110.0;
/// Re-activation distance, so an emitter sitting on the boundary does not flip
/// between simulated and cleared every frame.
pub const EMITTER_CULL_HYSTERESIS: f32 = 12.0;

/// Distance state for one emitter: `true` once it is past the cull distance
/// and has had its mesh cleared.
pub fn cull_state(distance: f32, previously_culled: bool) -> bool {
    if previously_culled {
        distance > EMITTER_CULL_DISTANCE - EMITTER_CULL_HYSTERESIS
    } else {
        distance > EMITTER_CULL_DISTANCE
    }
}

/// Per-emitter component: simulation state plus its mesh capacity.
#[derive(Component)]
pub struct ParticleEmitter {
    pub sim: EmitterSim,
    pub capacity: usize,
    /// Set while the emitter is beyond [`EMITTER_CULL_DISTANCE`]; its mesh has
    /// been cleared once and neither the sim nor the writer run.
    pub culled: bool,
    /// Set depois da primeira escrita com zero partículas vivas: o mesh já está
    /// degenerado, e reescrever `capacity × 4` vértices + re-uploadar o mesh
    /// por frame não muda um pixel. Apanha os emissores que emitem e param — a
    /// chuva com intensidade a 0 fica `Visibility::Hidden` mas o emissor
    /// continua vivo e a pagar a escrita.
    pub idle: bool,
}

/// Gate de emissor vazio: sem partículas vivas, o mesh já está degenerado e a
/// reescrita + upload por frame não muda um pixel. `VIBER_PARTICLE_IDLE_GATE=0`
/// desliga (comportamento anterior — o braço de controlo do A/B no profiler).
fn idle_gate_enabled() -> bool {
    std::env::var("VIBER_PARTICLE_IDLE_GATE").as_deref() != Ok("0")
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
        let position = transform.translation();
        let culled = cull_state(position.distance(camera_pos), emitter.culled);
        if culled {
            // Clear once on the way out, then leave the emitter alone.
            if !emitter.culled {
                emitter.culled = true;
                emitter.sim.particles.clear();
                if let Some(mut mesh) = meshes.get_mut(&mesh_handle.0) {
                    let capacity = emitter.capacity;
                    write_billboards(&mut mesh, &[], position, camera_pos, 1.0, capacity);
                }
            }
            emitter.idle = true;
            continue;
        }
        emitter.culled = false;
        emitter.sim.step(dt);
        // Vazio: escrever os zeros UMA vez (senão a última partícula morta
        // ficava congelada no mesh) e depois não voltar a tocar-lhe.
        // `VIBER_PARTICLE_IDLE_GATE=0` desliga o gate — é o braço de controlo
        // do A/B (com ele, o emissor de chuva a 0 reescreve `capacity × 4`
        // vértices e re-uploada o mesh em todos os frames, para nada).
        if emitter.sim.particles.is_empty() && idle_gate_enabled() {
            if emitter.idle {
                continue;
            }
            emitter.idle = true;
        } else {
            emitter.idle = false;
        }
        if let Some(mut mesh) = meshes.get_mut(&mesh_handle.0) {
            write_billboards(
                &mut mesh,
                &emitter.sim.particles,
                position,
                camera_pos,
                emitter.sim.resolved.end_size_factor,
                emitter.capacity,
            );
        }
    }
}

// ── bursts one-shot (feedback de combate) ───────────────────────────────
// Um burst é um emissor comum cujas partículas nascem TODAS no frame do
// impacto (emission_rate = 0 depois da semente) e cuja entidade despawna
// quando a mais longa morre — sem loops nem fugas ao cull normal.

impl EmitterSim {
    /// Nasce `count` partículas já com vida/speed/tamanho resolvidos.
    ///
    /// `capacity` é o orçamento do mesh do burst — emitir para lá dele
    /// criaria partículas que nunca são desenhadas (slots inexistentes).
    pub fn burst(&mut self, count: usize, capacity: usize) {
        for _ in 0..count.min(capacity) {
            self.spawn_one();
        }
    }
}

/// Burst em curso: despawna a entidade quando o timer esgota.
#[derive(Debug, Clone, Component)]
pub struct ParticleBurst {
    pub timer: f32,
}

/// Vida total do burst = partícula mais longa + margem de um frame.
pub fn burst_lifetime(resolved: &ResolvedEmitter) -> f32 {
    resolved.life.1 + 0.25
}

/// Escadote de capacidades do pool de malhas de burst.
///
/// A capacidade pedida (`count + 8`) varia continuamente (com a velocidade do
/// golpe, o combo, a intensidade da chuva); sem escadote, o pool desbaratava
/// num balde por valor. Arredondar para CIMA é seguro — a capacidade é um
/// TETO do burst, não um alvo — e o pool converge para ≤ 7 malhas.
const MESH_BUCKETS: [usize; 7] = [16, 32, 64, 128, 256, 512, 1024];

fn mesh_bucket(requested: usize) -> usize {
    MESH_BUCKETS
        .iter()
        .copied()
        .find(|bucket| *bucket >= requested)
        .unwrap_or(MESH_BUCKETS[MESH_BUCKETS.len() - 1])
}

/// Assets de burst PARTILHADOS e RECICLADOS.
///
/// Antes disto cada burst criava `Assets::add(particle_mesh)` +
/// `Assets::add(emitter_material)` e a entidade despawnava sem os libertar —
/// o Bevy não faz GC de assets, portanto em combate (dezenas de bursts/s) e
/// com a chuva (ripples a ~14/s) o asset store crescia **sem limite**. Agora:
/// as malhas voltam ao pool no despawn (só podem ser REUTILIZADAS, nunca
/// partilhadas — o `particle_emitter_update` reescreve a malha de cada
/// emissor por frame) e há exactamente DOIS materiais (`Add` e `Blend` —
/// `emitter_material` só depende de `resolved.additive`).
#[derive(Resource, Default)]
pub struct BurstPool {
    meshes: std::collections::HashMap<usize, Vec<bevy::asset::Handle<bevy::mesh::Mesh>>>,
    material_add: Option<Handle<StandardMaterial>>,
    material_blend: Option<Handle<StandardMaterial>>,
}

impl BurstPool {
    fn take_mesh(&mut self, requested: usize) -> Option<bevy::asset::Handle<bevy::mesh::Mesh>> {
        self.meshes
            .get_mut(&mesh_bucket(requested))
            .and_then(|pool| pool.pop())
    }

    fn park_mesh(
        &mut self,
        capacity: usize,
        handle: bevy::asset::Handle<bevy::mesh::Mesh>,
    ) {
        self.meshes.entry(mesh_bucket(capacity)).or_default().push(handle);
    }

    fn material(&self, additive: bool) -> Option<Handle<StandardMaterial>> {
        if additive {
            self.material_add.clone()
        } else {
            self.material_blend.clone()
        }
    }

    fn park_material(&mut self, additive: bool, handle: Handle<StandardMaterial>) {
        if additive {
            self.material_add = Some(handle);
        } else {
            self.material_blend = Some(handle);
        }
    }
}

/// Spawna um burst `preset` em `position` — usado pelo melee (slash/sparks),
/// finisher (explosion), mortes e impactos de projétil. Os assets vêm do
/// [`BurstPool`] (ver a doc do recurso); por isso a assinatura já não pede
/// `Assets<Mesh>`/`Assets<StandardMaterial>` e o spawn corre por
/// `Commands::queue` — precisa do `World` para chegar ao pool.
pub fn spawn_burst(commands: &mut Commands, spec: &ParticleSpec, position: Vec3, count: usize) {
    let spec = spec.clone();
    commands.queue(move |world: &mut World| {
        // Apps mínimas de teste (e qualquer mundo montado sem o `BurstPlugin`)
        // também disparam bursts: o pool nasce à primeira utilização em vez de
        // obrigar cada chamador a conhecer o recurso.
        if !world.contains_resource::<BurstPool>() {
            world.init_resource::<BurstPool>();
        }
        let capacity = mesh_bucket(count + 8);
        let mut sim = EmitterSim::seeded(&spec, position);
        sim.burst(count, capacity);
        // Só burst: o update normal integra as partículas mas nunca emite mais.
        sim.resolved.emission_rate = 0.0;
        let lifetime = burst_lifetime(&sim.resolved);
        let mesh = match world.resource_mut::<BurstPool>().take_mesh(count + 8) {
            Some(handle) => handle,
            None => world.resource_mut::<Assets<Mesh>>().add(particle_mesh(capacity)),
        };
        let additive = sim.resolved.additive;
        let material = match world.resource::<BurstPool>().material(additive) {
            Some(handle) => handle,
            None => {
                let handle = world
                    .resource_mut::<Assets<StandardMaterial>>()
                    .add(emitter_material(&sim.resolved));
                world.resource_mut::<BurstPool>().park_material(additive, handle.clone());
                handle
            }
        };
        world.spawn((
            Transform::from_translation(position),
            Visibility::Inherited,
            Mesh3d(mesh),
            MeshMaterial3d(material),
            NotShadowCaster,
            ParticleEmitter {
                sim,
                capacity,
                culled: false,
                idle: false,
            },
            ParticleBurst { timer: lifetime },
            Name::new("fx:burst"),
        ));
    });
}

/// Spawna um emissor CONTÍNUO `preset` em `position` — espuma da linha de
/// água, braseiros ambientais: emite para sempre ao contrário do burst.
pub fn spawn_looping(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    spec: &ParticleSpec,
    position: Vec3,
) {
    let resolved = resolve(spec);
    let capacity = ((resolved.emission_rate * resolved.life.1).ceil() as usize + 16).min(1024);
    let sim = EmitterSim::seeded(spec, position);
    let mesh = meshes.add(particle_mesh(capacity));
    let material = materials.add(emitter_material(&sim.resolved));
    world.spawn((
        Transform::from_translation(position),
        Visibility::Inherited,
        Mesh3d(mesh),
        MeshMaterial3d(material),
        NotShadowCaster,
        ParticleEmitter {
            sim,
            capacity,
              idle: false,
          culled: false,
        },
        Name::new("fx:ambient"),
    ));
}

/// Variante de [`spawn_looping`] para SISTEMAS: só `&mut World` (o
/// `Commands::queue` não consegue emprestar os `Assets` em separado).
/// Devolve a entidade para o chamador anexar os seus próprios markers.
pub fn spawn_looping_in_world(world: &mut World, spec: &ParticleSpec, position: Vec3) -> Entity {
    let resolved = resolve(spec);
    let capacity = ((resolved.emission_rate * resolved.life.1).ceil() as usize + 16).min(1024);
    let mesh = {
        let mut meshes = world.resource_mut::<Assets<Mesh>>();
        meshes.add(particle_mesh(capacity))
    };
    let material = {
        let mut materials = world.resource_mut::<Assets<StandardMaterial>>();
        materials.add(emitter_material(&resolved))
    };
    let sim = EmitterSim::seeded(spec, position);
    world
        .spawn((
            Transform::from_translation(position),
            Visibility::Inherited,
            Mesh3d(mesh),
            MeshMaterial3d(material),
            NotShadowCaster,
            ParticleEmitter {
                sim,
                       idle: false,
         capacity,
                culled: false,
            },
            Name::new("fx:ambient"),
        ))
        .id()
}

/// Despawna o emissor quando o burst termina (as partículas morrem com ele) e
/// DEVOLVE a malha ao [`BurstPool`] — zerada, porque a malha reciclada não
/// pode estrear com as partículas do burst anterior (um frame de fantasma).
fn burst_despawn_system(
    time: Res<Time>,
    mut bursts: Query<(Entity, &mut ParticleBurst, &ParticleEmitter, &Mesh3d)>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut pool: ResMut<BurstPool>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut burst, emitter, mesh) in &mut bursts {
        burst.timer -= dt;
        if burst.timer <= 0.0 {
            if let Some(mut mesh_asset) = meshes.get_mut(&mesh.0) {
                if let Some(bevy::mesh::VertexAttributeValues::Float32x3(positions)) =
                    mesh_asset.attribute_mut(bevy::mesh::Mesh::ATTRIBUTE_POSITION)
                {
                    positions.fill([0.0; 3]);
                }
            }
            pool.park_mesh(emitter.capacity, mesh.0.clone());
            commands.entity(entity).despawn();
        }
    }
}

/// Registo do ciclo de vida dos bursts (chamado pelo arranque da engine).
pub struct BurstPlugin;
impl bevy::app::Plugin for BurstPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<BurstPool>()
            .add_systems(bevy::app::Update, timed(Group::Fx, burst_despawn_system));
        // Sprite radial suave nos materiais dos emissores (WS-A) — `Option`
        // em tudo o que depende do AssetPlugin, para sobreviver em Apps de
        // teste headless sem assets.
        app.add_systems(bevy::app::Update, timed(Group::Fx, emitter_sprite_bind));
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
            assert_eq!(p.size_y, p.size, "{name}: quadrado por omissão");
        }
        // unknown preset falls back to the generic core
        assert_eq!(preset("nope").emission_rate, preset("core").emission_rate);
    }

    /// Chuva (WS-A): gotas finas esticadas, Blend, rate alto, queda rápida.
    #[test]
    fn test_rain_preset_is_thin_and_stretched() {
        let rain = preset("rain");
        assert!(
            rain.emission_rate >= 400.0,
            "rate alto: {}",
            rain.emission_rate
        );
        assert!(!rain.additive, "chuva é Blend, não Add");
        assert!(rain.speed.1 < 0.0, "speed negativo = a cair");
        assert!(rain.gravity.y < -8.0, "gravidade forte");
        // Esticada: 10× mais alta que larga, mas sempre fina.
        assert!(rain.size.1 <= 0.05, "gota fina em X: {:?}", rain.size);
        assert!(
            rain.size_y.0 / rain.size.0 > 8.0,
            "gota esticada em Y: {:?} vs {:?}",
            rain.size_y,
            rain.size
        );
        // O ripple é um burst curto e baixo.
        let ripple = preset("rain_ripple");
        assert_eq!(ripple.emission_rate, 0.0, "burst, não loop");
        assert!(ripple.life.1 <= 0.35, "vida curta: {:?}", ripple.life);
        assert!(ripple.additive == rain.additive, "também Blend");
    }

    /// `size_y` separa a escala X e Y dos cantos do billboard (função pura).
    #[test]
    fn test_billboard_corner_offsets_scale_x_and_y() {
        let corners = billboard_corner_offsets(0.02, 0.5, Vec3::X, Vec3::Y);
        let xs: Vec<f32> = corners.iter().map(|c| c.x).collect();
        let ys: Vec<f32> = corners.iter().map(|c| c.y).collect();
        // X: ±size/2; Y: ±size_y/2 — ordem BL, BR, TR, TL.
        assert_eq!(xs, vec![-0.01, 0.01, 0.01, -0.01]);
        assert_eq!(ys, vec![-0.25, -0.25, 0.25, 0.25]);
        // Z fica a zero quando right/up são eixos canónicos.
        assert!(corners.iter().all(|c| c.z == 0.0));
    }

    /// Escala de fim de vida linear de 1 até `end_size_factor`.
    #[test]
    fn test_end_size_scale_is_linear_over_lifetime() {
        assert_eq!(end_size_scale(1.0, 0.3), 1.0, "nascimento: intacto");
        assert_eq!(end_size_scale(0.0, 0.3), 0.3, "morte: fator cheio");
        let mid = end_size_scale(0.5, 2.0);
        assert!((mid - 1.5).abs() < 1e-6);
    }

    /// `write_billboards` aplica size/size_y (com o fator de fim de vida) aos
    /// vértices reais do mesh.
    #[test]
    fn test_write_billboards_stretches_y_independently() {
        let particle = LiveParticle {
            pos: Vec3::ZERO,
            vel: Vec3::ZERO,
            life: 1.0,
            max_life: 1.0,
            size: 0.02,
            size_y: 0.5,
            color: [1.0, 1.0, 1.0],
        };
        let mut mesh = particle_mesh(1);
        // Câmara em +Z: right = X, up = Y (eixos canónicos).
        write_billboards(
            &mut mesh,
            &[particle],
            Vec3::ZERO,
            Vec3::new(0.0, 0.0, 10.0),
            1.0,
            1,
        );
        let positions = mesh
            .attribute(bevy::mesh::Mesh::ATTRIBUTE_POSITION)
            .unwrap();
        let bevy::mesh::VertexAttributeValues::Float32x3(positions) = positions else {
            panic!("position attribute type");
        };
        let xs: Vec<f32> = positions.iter().map(|p| p[0]).collect();
        let ys: Vec<f32> = positions.iter().map(|p| p[1]).collect();
        assert_eq!(xs, vec![-0.01, 0.01, 0.01, -0.01], "metade de size em X");
        assert_eq!(ys, vec![-0.25, -0.25, 0.25, 0.25], "metade de size_y em Y");
    }

    /// Falloff do sprite radial: centro cheio, borda e cantos a zero.
    #[test]
    fn test_soft_sprite_falloff_center_one_corners_zero() {
        assert_eq!(soft_sprite_alpha(0.0), 1.0, "centro sólido");
        assert_eq!(soft_sprite_alpha(0.25), 1.0, "patamar até 25 %");
        assert_eq!(soft_sprite_alpha(1.0), 0.0, "borda do quad desliga");
        assert_eq!(soft_sprite_alpha(1.42), 0.0, "canto (r = √2) desliga");
        let mid = soft_sprite_alpha(0.625);
        assert!((0.0..1.0).contains(&mid), "declive suave no meio: {mid}");

        let data = soft_sprite_pixels(64);
        assert_eq!(data.len(), (64 * 64 * 4) as usize);
        let alpha = |x: u32, y: u32| data[((y * 64 + x) * 4 + 3) as usize];
        assert!(alpha(31, 31) > 240, "centro ~1: {}", alpha(31, 31));
        assert!(alpha(0, 0) < 8, "canto ~0: {}", alpha(0, 0));
        assert!(alpha(63, 63) < 8, "canto oposto ~0: {}", alpha(63, 63));
        // Meio do falloff (r ≈ 0.61 no pixel 51 da linha central): entre os
        // dois extremos — o pixel 1 já está no r ≈ 0.95, quase desligado.
        let edge = alpha(51, 31);
        assert!((30..=230).contains(&edge), "meio do falloff: {edge}");
        // RGB é branco em todo o lado (a cor real vem das vertex colors).
        assert_eq!(data[0], 255);
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
    fn test_emitter_cull_state_has_hysteresis() {
        // Fresh emitter: only past the full distance.
        assert!(!cull_state(EMITTER_CULL_DISTANCE - 1.0, false));
        assert!(cull_state(EMITTER_CULL_DISTANCE + 1.0, false));
        // Already culled: stays culled until it comes back inside the band.
        assert!(cull_state(EMITTER_CULL_DISTANCE - 1.0, true));
        assert!(!cull_state(
            EMITTER_CULL_DISTANCE - EMITTER_CULL_HYSTERESIS - 1.0,
            true
        ));
    }

    #[test]
    fn test_particle_mesh_carries_the_constant_corner_uvs() {
        // `write_billboards` no longer rewrites UVs/normals/indices, so the
        // mesh has to ship them correct from the start.
        let mesh = particle_mesh(2);
        let uvs = mesh.attribute(bevy::mesh::Mesh::ATTRIBUTE_UV_0).unwrap();
        let bevy::mesh::VertexAttributeValues::Float32x2(uvs) = uvs else {
            panic!("uv attribute type");
        };
        assert_eq!(uvs.len(), 8);
        assert_eq!(uvs[0], [0.0, 0.0]);
        assert_eq!(uvs[1], [1.0, 0.0]);
        assert_eq!(uvs[2], [1.0, 1.0]);
        assert_eq!(uvs[3], [0.0, 1.0]);
        assert_eq!(uvs[4], [0.0, 0.0]);
        assert!(mesh.indices().is_some());
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

    /// A capacidade do emissor é UMA fórmula partilhada pelos três sites
    /// (`<ParticleSystem>`, `spawn_looping`, `spawn_looping_in_world`).
    /// Antes viviam DUAS (`+8`/512 vs `+16`/1024) e o mesmo XML truncava o
    /// render de forma diferente consoante o caminho que spawna o emissor.
    #[test]
    fn test_capacity_quads_is_the_single_shared_formula() {
        // Fórmula: rate × vida-máx arredondado para cima + folga fixa.
        assert_eq!(
            capacity_quads(20.0, 1.0, EMITTER_MESH_CAP),
            20 + CAPACITY_HEADROOM
        );
        // Piso: rate 0 (sim de burst reutiliza `seeded`) nunca dá mesh vazio.
        assert_eq!(
            capacity_quads(0.0, 1.0, AMBIENT_MESH_CAP),
            CAPACITY_HEADROOM
        );
        // Tetos POR SITE mantidos: declarativo 512, ambiente 1024.
        assert_eq!(
            capacity_quads(10_000.0, 1.0, EMITTER_MESH_CAP),
            EMITTER_MESH_CAP
        );
        assert_eq!(
            capacity_quads(10_000.0, 1.0, AMBIENT_MESH_CAP),
            AMBIENT_MESH_CAP
        );
        // O rain da engine (500/s × 0.85 s) cabe no teto declarativo — mundos
        // atuais não disparam o warn de truncamento.
        assert!(capacity_quads(500.0, 0.85, EMITTER_MESH_CAP) <= EMITTER_MESH_CAP);
        // A assinatura antiga (usada por `recipes::spawn`) respeita o teto.
        let mut big = preset("fire");
        big.emission_rate = 10_000.0;
        assert_eq!(emitter_capacity(&big), EMITTER_MESH_CAP);
    }

    /// O asset store NÃO cresce com os bursts: a malha volta ao pool no
    /// despawn e o material é partilhado por modo de blend. Antes do pool,
    /// cada burst deixava um `Mesh` + um `StandardMaterial` residentes para
    /// sempre (o Bevy não faz GC de assets) — em combate e com os ripples da
    /// chuva (~14/s) era crescimento ilimitado.
    #[test]
    fn test_burst_assets_nao_acumulam() {
        use bevy::ecs::world::CommandQueue;

        let mut app = App::new();
        app.init_resource::<Time>()
            .init_resource::<Assets<bevy::mesh::Mesh>>()
            .init_resource::<Assets<StandardMaterial>>()
            .init_resource::<BurstPool>()
            .add_systems(Update, burst_despawn_system);

        for round in 0..5 {
            let mut queue = CommandQueue::default();
            {
                let world = app.world_mut();
                let mut commands = Commands::new(&mut queue, world);
                spawn_burst(&mut commands, &fire_spec(), Vec3::ZERO, 40 + round);
            }
            queue.apply(app.world_mut());
            // Expira o burst: o despawn devolve a malha ao pool. (Sem
            // `clear_entities` entre rondas — no Bevy 0.19 ele invalida os
            // fetches de recursos, incluindo o pool que se quer medir.)
            for mut burst in app
                .world_mut()
                .query::<&mut ParticleBurst>()
                .iter_mut(app.world_mut())
            {
                burst.timer = 0.0;
            }
            app.update();
            let _ = round;
        }

        let world = app.world();
        assert_eq!(
            world.resource::<Assets<bevy::mesh::Mesh>>().len(),
            1,
            "uma malha no pool, reutilizada em todas as rondas"
        );
        assert_eq!(
            world.resource::<Assets<StandardMaterial>>().len(),
            1,
            "um material por modo de blend (o fire é additive)"
        );
        assert!(
            world
                .resource::<BurstPool>()
                .meshes
                .values()
                .map(Vec::len)
                .sum::<usize>()
                >= 1,
            "a malha está PARQUEADA no pool, não abandonada no store"
        );
    }

}
