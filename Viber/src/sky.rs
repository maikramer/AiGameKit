//! `<Sky>` procedural dome: atmospheric gradient, sun disc + glow, FBM
//! clouds drifting with the wind, moon and stars at night — composed in a
//! custom WGSL fragment shader on a camera-following inverted sphere.

use bevy::asset::RenderAssetUsages;
use bevy::light::NotShadowCaster;
use bevy::math::{Vec2, Vec3};
use bevy::pbr::Material;
use bevy::prelude::*;
use bevy::render::mesh::PrimitiveTopology;
use bevy::render::render_resource::AsBindGroup;
use bevy::shader::ShaderRef;

/// Fonte WGSL do céu (embutida no binário e escrita no world dir).
pub const SKY_WGSL: &str = include_str!("sky.wgsl");

/// Marker + material handle for the sky dome entity.
#[derive(Debug, Component)]
pub struct SkyDome {
    pub material: Handle<SkyMaterial>,
}

/// GPU uniform block (mirrored in `sky.wgsl` as `SkyUniform`).
#[derive(Debug, Clone, Default, bevy::render::render_resource::ShaderType)]
pub struct SkyUniform {
    pub sun_dir: Vec3,
    pub time: f32,
    /// 0 = full day, 1 = full night.
    pub night: f32,
    pub turbidity: f32,
    pub rayleigh: f32,
    pub mie: f32,
    pub mie_g: f32,
    pub sun_intensity: f32,
    pub cloud_coverage: f32,
    pub cloud_density: f32,
    pub cloud_elevation: f32,
    pub wind: Vec2,
}

/// Custom sky material (WGSL in `src/sky.wgsl`, written to the world asset
/// root by `run()` before the renderer specializes the material).
#[derive(Debug, Clone, Asset, TypePath, AsBindGroup)]
pub struct SkyMaterial {
    #[uniform(0)]
    pub uniform: SkyUniform,
}

impl Material for SkyMaterial {
    fn fragment_shader() -> ShaderRef {
        "shaders/sky.wgsl".into()
    }

    // The dome is seen from the inside. Bevy 0.19 no longer exposes
    // `cull_mode` on `Material` (it moved into the specialization pass), so
    // the dome mesh itself is wound inward instead.
}

/// Spawn the dome + material for a `<Sky>` element from its raw attributes.
pub fn build_sky(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    sky_mats: &mut Assets<SkyMaterial>,
    attrs: &[(String, String)],
) {
    let f = |name: &str, default: f32| -> f32 {
        attrs
            .iter()
            .find(|(k, _)| k == name)
            .and_then(|(_, v)| v.trim().parse::<f32>().ok())
            .unwrap_or(default)
    };
    let sun_elevation = f("sun-elevation", 17.0);
    let sun_azimuth = f("sun-azimuth", 205.0);
    let turbidity = f("turbidity", 2.4).max(0.05);
    let rayleigh = f("rayleigh", 2.8);
    let mie = f("mie-coefficient", 0.0035);
    let mie_g = f("mie-directional-g", 0.8).clamp(-0.99, 0.99);
    let cloud_coverage = f("cloud-coverage", 0.45).clamp(0.0, 1.0);
    let cloud_density = f("cloud-density", 0.32).clamp(0.0, 1.0);
    let cloud_elevation = f("cloud-elevation", 0.55).clamp(0.0, 1.0);
    let _ = f("environment-intensity", 0.38);
    let sun_intensity = f("sun-intensity", 2.6).max(0.1);
    let _ = f("drive-light", 1.0);

    let (el, az) = (sun_elevation.to_radians(), sun_azimuth.to_radians());
    let sun_dir = Vec3::new(el.cos() * az.sin(), el.sin(), el.cos() * az.cos());

    let mesh = meshes.add(sky_dome_mesh());
    let material = sky_mats.add(SkyMaterial {
        uniform: SkyUniform {
            sun_dir,
            time: 0.0,
            night: 0.0,
            turbidity,
            rayleigh,
            mie,
            mie_g,
            sun_intensity,
            cloud_coverage,
            cloud_density,
            cloud_elevation,
            wind: Vec2::new(0.7, 0.25),
        },
    });
    world.spawn((
        Name::new("sky"),
        Mesh3d(mesh),
        MeshMaterial3d::<SkyMaterial>(material.clone()),
        Transform::from_scale(Vec3::splat(4000.0)),
        Visibility::Visible,
        NotShadowCaster,
        SkyDome {
            material: material.clone(),
        },
    ));
}

/// Inverted-normal UV sphere (seen from inside) at unit radius — scale it up
/// via the camera-follow transform.
pub fn sky_dome_mesh() -> bevy::mesh::Mesh {
    let bands = 48;
    let slices = 96;
    let mut positions = Vec::with_capacity((bands + 1) * (slices + 1));
    let mut uvs = Vec::with_capacity((bands + 1) * (slices + 1));
    for b in 0..=bands {
        let phi = std::f32::consts::PI * (b as f32) / (bands as f32);
        for s in 0..=slices {
            let theta = std::f32::consts::TAU * (s as f32) / (slices as f32);
            let y = phi.cos();
            let r = phi.sin();
            positions.push([r * theta.cos(), y, r * theta.sin()]);
            uvs.push([s as f32 / slices as f32, b as f32 / bands as f32]);
        }
    }
    let mut indices = Vec::with_capacity(bands * slices * 6);
    for b in 0..bands {
        for s in 0..slices {
            let row = b * (slices + 1);
            let a = (row + s) as u32;
            let c = (row + s + 1) as u32;
            let d = ((b + 1) * (slices + 1) + s) as u32;
            let e = ((b + 1) * (slices + 1) + s + 1) as u32;
            // Winding invertido: o interior da esfera é o lado visível.
            // Os dois triângulos do quad têm de partilhar a MESMA orientação
            // — [a,e,d] ficava com a normal para FORA e era culled visto de
            // dentro (xadrez de um-triângulo-sim-um-triângulo-não no céu).
            indices.extend([a, d, e, a, e, c]);
        }
    }
    let mut mesh = bevy::mesh::Mesh::new(
        PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_POSITION, positions);
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_UV_0, uvs);
    mesh.insert_indices(bevy::mesh::Indices::U32(indices));
    mesh
}

/// Keep the dome centered on the camera (the world is 8 km wide; the dome
/// must never be walked out of).
pub fn sky_follow_camera(
    cameras: Query<&GlobalTransform, With<Camera>>,
    mut domes: Query<&mut Transform, With<SkyDome>>,
) {
    let Ok(camera) = cameras.single() else {
        return;
    };
    for mut transform in &mut domes {
        transform.translation = camera.translation();
    }
}

/// Publish the live sun/clock/weather into the dome's material.
///
/// The material asset is RE-CREATED every frame instead of mutated: Bevy
/// 0.19 promotes a plain `#[uniform(0)]` to a slot-1 storage buffer that is
/// never re-uploaded on `AssetEvent::Modified` (mutating it froze the sky on
/// its first-frame values), and reading it back through the binding yielded
/// unrelated buffer contents, which drove the day/night/cloud flicker. The
/// `Added` path — a fresh asset — provably uploads correctly, and one 64-byte
/// buffer per frame is nothing.
#[allow(clippy::needless_pass_by_value)]
pub fn sky_update(
    time: Res<Time>,
    sun: Res<crate::worldsys::SunState>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    mut sky_mats: ResMut<Assets<SkyMaterial>>,
    mut domes: Query<&mut MeshMaterial3d<SkyMaterial>>,
) {
    for mut binding in &mut domes {
        let mut uniform = sky_mats
            .get(&binding.0)
            .map(|material| material.uniform.clone())
            .unwrap_or_default();
        uniform.sun_dir = sun.dir;
        uniform.night = sun.night;
        uniform.time = time.elapsed_secs();
        if let Some(w) = weather.as_deref() {
            uniform.wind = Vec2::new(w.wind[0], w.wind[1]);
        }
        let handle = sky_mats.add(SkyMaterial { uniform });
        **binding = handle;
    }
}
