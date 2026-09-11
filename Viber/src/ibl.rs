//! IBL vivo do céu — image-based lighting gerado da paleta da atmosfera.
//!
//! O ambiente era uma cor chapada (`GlobalAmbientLight`): nada refletia o
//! céu, a água só tinha um tint hardcoded. Aqui a engine mantém um
//! [`LightProbe`] com [`GeneratedEnvironmentMapLight`] cujo cubemap BASE é
//! pintado em CPU (64² × 6 faces, `Rgba16Float`) a partir do
//! [`crate::worldsys::AtmosphereState`] — gradiente zénite↔horizonte, disco e
//! halo do sol na direção real, chão escurecido sob o horizonte — e a GPU
//! (`EnvironmentMapGenerationPlugin`, já no `PbrPlugin`) filtra o diffuse
//! Lambertiano + specular GGX por mip em compute.
//!
//! O cubemap é REGERADO quando a fase do dia muda (6 fases): à noite o
//! ambiente fica azulado pela lua, na golden hour dourado, ao meio-dia
//! azul-neutro. O terreno, a água e os props ganham o IBL **sem tocar em
//! shaders** — o ramo `ENVIRONMENT_MAP` vive dentro de
//! `apply_pbr_lighting` (que o chunk/water já chamam) e o define vem do
//! view key quando a view tem light probe.
//!
//! Degradação: sem `AtmosphereState`/`DayCycle` gera UMA vez com a paleta
//! default e segue; a intensidade compõe com o `GlobalAmbientLight`
//! existente (que se mantém como chão de ambiente).

use bevy::image::Image;
use bevy::light::{GeneratedEnvironmentMapLight, LightProbe};
use bevy::prelude::*;
use bevy::asset::RenderAssetUsages;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat, TextureViewDescriptor, TextureViewDimension};

use crate::worldsys::{AtmosphereState, DayCycleState};

/// Faces por lado do cubemap (128²: os mips especulares de 64² eram blobby
/// — o reflexo do céu na água lia-se como um borrão sem estrutura; 128²
/// mantém o paint CPU trivial, 98 k texels por fase).
const FACE_SIZE: u32 = 128;

/// Fases do dia que regeram o cubemap. Mais fases = transições mais suaves,
/// cada uma custa um re-filtro compute (~1 frame). 12 (era 6, 2026-09-09):
/// os saltos discretos de ambiente às trocas de fase liam-se nas encostas.
const DAY_PHASES: u32 = 12;

/// Quanto do chão default o tint regional substitui (probes regionais).
const TINT_MIX_GROUND: f32 = 0.65;

/// Escala da intensidade do IBL gerado. O filtro integra a RADIÂNCIA do
/// cubemap — a paleta do `AtmosphereState` vive na mesma escala que o domo
/// do céu desenha, portanto 1.0 deixa o ambiente proporcional ao céu visível.
const IBL_INTENSITY: f32 = 1.0;

/// Marcador do probe do IBL (um só, tamanho do mundo).
#[derive(Component)]
struct SkyIblProbe;

/// Mantém o [`LightProbe`] do IBL: gera o cubemap base na primeira frame
/// útil (depois do primeiro `atmosphere_drive`) e regenera quando a fase do
/// dia muda.
pub struct SkyIblPlugin;

impl Plugin for SkyIblPlugin {
    fn build(&self, app: &mut App) {
        // `VIBER_NO_IBL=1` desliga (A/B de QA / GPUs sem compute storage).
        if std::env::var_os("VIBER_NO_IBL").is_some() {
            info!("ibl: desligado por VIBER_NO_IBL");
            return;
        }
        app.add_systems(Startup, spawn_sky_probe);
        app.add_systems(Update, update_sky_cubemap);
    }
}

fn spawn_sky_probe(mut commands: Commands) {
    // Probe REGIONAL cobrindo o mundo inteiro (a parallax correction usa o
    // AABB do probe — com 4 km de lado é efetivamente "para toda a view").
    // SEM `GeneratedEnvironmentMapLight` aqui: nascer com `Handle::default()`
    // deixava o gerador de alvos do bevy resolver o handle vazio ANTES da
    // primeira geração (ordenação indefinida de sistemas do Update) — o
    // cubemap default 1×1 produzia specular de 1 mip e os
    // `radiance_bind_group_mip_N` inválidos (crash com <DayCycle>, r3).
    // O componente entra com o handle REAL na primeira geração.
    commands.spawn((
        SkyIblProbe,
        LightProbe::default(),
        Transform::from_scale(Vec3::splat(4000.0)),
    ));
}

/// Fase quantizada do dia (0..DAY_PHASES). `None` até haver relógio útil.
fn day_phase(clock: Option<&DayCycleState>) -> Option<u32> {
    let clock = clock?;
    let day = crate::worldsys::daylight_factor(
        clock.minute_of_day,
        clock.dawn_minute,
        clock.dusk_minute,
    );
    Some((day.clamp(0.0, 1.0) * DAY_PHASES as f32).floor() as u32)
}

#[allow(clippy::type_complexity)]
fn update_sky_cubemap(
    mut commands: Commands,
    clock: Option<Res<DayCycleState>>,
    atmosphere: Res<AtmosphereState>,
    model_state: Option<Res<crate::sky::SkyModelState>>,
    mut images: ResMut<Assets<Image>>,
    probes: Query<Entity, With<SkyIblProbe>>,
    // `None` até à primeira geração; depois, a fase que a gerou (mundos sem
    // relógio guardam `Some(None)` = nunca mais mexer — regenerar por frame
    // era o estado acidental pré-r3 e custava um re-filtro GPU por frame).
    mut last_phase: Local<Option<Option<u32>>>,
    current: Query<&GeneratedEnvironmentMapLight, With<SkyIblProbe>>,
) {
    let phase = day_phase(clock.as_deref());
    if last_phase.is_some_and(|last| last == phase) {
        return;
    }
    *last_phase = Some(phase);
    let Ok(probe_entity) = probes.single() else {
        return;
    };
    // O modelo do céu do mundo (attr `<Sky model>`/env): o cubemap pinta a
    // MESMA radiância que o domo desenha (analítico ou nishita).
    let nishita = model_state
        .as_deref()
        .filter(|s| s.model == crate::sky::SkyModel::Nishita)
        .map(|s| s.nishita);
    let handle = images.add(sky_cubemap(&atmosphere, nishita.as_ref()));
    // Primeira geração OU troca de fase: o componente entra (re-entra) com
    // o handle REAL. Na troca, o handle antigo sai dos Assets — o filtro GPU
    // re-emite por Added do novo (6/dia × ~260 KB — GC explícito).
    let old = current
        .single()
        .ok()
        .map(|c| c.environment_map.clone())
        .filter(|h| *h != Handle::default());
    commands.entity(probe_entity).insert(GeneratedEnvironmentMapLight {
        environment_map: handle,
        intensity: IBL_INTENSITY,
        ..Default::default()
    });
    if let Some(old) = old {
        images.remove(old.id());
    }
}

/// Pinta o cubemap base de 6×64² a partir da paleta da atmosfera, JÁ com a
/// mip chain completa (box filter por face): a geração GPU do
/// `GeneratedEnvironmentMapLight` cria views de storage POR MIP do cubemap
/// fonte — sem mips o render panica ("storage_view_mip_6 … only has 1 total
/// mip level"), crash real a 2026-09-07 no qa-enriched.
pub fn sky_cubemap(a: &AtmosphereState, nishita: Option<&crate::sky_nishita::NishitaModel>) -> Image {
    sky_cubemap_tinted(a, nishita, None)
}

/// Como [`sky_cubemap`], mas com um TINT opcional misturado no hemisfério de
/// baixo (o "bounce" local) — usado pelos probes regionais dos pads
/// (`crate::probes`): pântano esverdeado, deserto arenoso, etc. `None` = o
/// pintor padrão do IBL mundial.
pub fn sky_cubemap_tinted(
    a: &AtmosphereState,
    nishita: Option<&crate::sky_nishita::NishitaModel>,
    ground_tint: Option<[f32; 3]>,
) -> Image {
    // 128 = 2^7 → 8 níveis de mip (128..1): a geração GPU acede ao mip_7.
    let mips = FACE_SIZE.trailing_zeros() + 1;
    // `Image::new` valida os bytes contra o MIPEL 0 — o buffer estende-se à
    // cadeia completa DEPOIS da construção (resize + preencher abaixo).
    let mut data = vec![0u8; (FACE_SIZE * FACE_SIZE * 6) as usize * 8];
    let sun = a.sun_dir.normalize_or_zero();
    let moon = a.moon_dir.normalize_or_zero();
    let write = |data: &mut [u8], size: u32, face: u32, x: u32, y: u32, rgba: [u16; 4]| {
        let base = (face * size * size + y * size + x) as usize * 8;
        for c in 0..4 {
            data[base + c * 2..base + c * 2 + 2].copy_from_slice(&rgba[c].to_le_bytes());
        }
    };
    // Mip 0: o modelo de radiância por direção.
    for face in 0..6u32 {
        for y in 0..FACE_SIZE {
            for x in 0..FACE_SIZE {
                let uc = (x as f32 + 0.5) / FACE_SIZE as f32 * 2.0 - 1.0;
                let vc = (y as f32 + 0.5) / FACE_SIZE as f32 * 2.0 - 1.0;
                let dir = face_direction(face, uc, vc);
                let color = sky_radiance(a, dir, sun, moon, nishita, ground_tint);
                let rgba = [
                    f32_to_f16(color[0]),
                    f32_to_f16(color[1]),
                    f32_to_f16(color[2]),
                    0x3c00, // alpha = 1.0 em f16
                ];
                write(&mut data, FACE_SIZE, face, x, y, rgba);
            }
        }
    }
    // Estende o buffer à cadeia completa e preenche os mips 1..n: média 2×2
    // do pai, POR FACE (sem cross-face bleeding).
    data.resize(mip_chain_bytes(FACE_SIZE, mips), 0);
    for level in 1..mips {
        let size = FACE_SIZE >> level;
        let parent_size = FACE_SIZE >> (level - 1);
        let parent_off = mip_offset(FACE_SIZE, level - 1);
        let level_off = mip_offset(FACE_SIZE, level);
        for face in 0..6u32 {
            for y in 0..size {
                for x in 0..size {
                    let mut acc = [0f32; 4];
                    for (dy, dx) in [(0u32, 0u32), (0, 1), (1, 0), (1, 1)] {
                        let px = (x * 2 + dx).min(parent_size - 1);
                        let py = (y * 2 + dy).min(parent_size - 1);
                        let base = parent_off
                            + (face * parent_size * parent_size + py * parent_size + px) as usize * 8;
                        for c in 0..4 {
                            let bits = u16::from_le_bytes([data[base + c * 2], data[base + c * 2 + 1]]);
                            acc[c] += f16_to_f32(bits);
                        }
                    }
                    let rgba = [
                        f32_to_f16(acc[0] * 0.25),
                        f32_to_f16(acc[1] * 0.25),
                        f32_to_f16(acc[2] * 0.25),
                        0x3c00,
                    ];
                    write(&mut data, size, face, x, y, rgba);
                }
            }
        }
        let _ = level_off;
    }
    // Construção MANUAL (Image::default + descriptor): o `Image::new`
    // valida os bytes contra a mip chain QUE ELE calcula (log2, sem o 1×1),
    // que não é a nossa — a geração GPU do bevy precisa de mips 0..6
    // INCLUSIVE (o storage_view_mip_6 do crash). Sem validação a meio,
    // o descriptor com 7 níveis + buffer da cadeia completa.
    let mut image = Image::default();
    image.texture_descriptor.size = Extent3d {
        width: FACE_SIZE,
        height: FACE_SIZE,
        depth_or_array_layers: 6,
    };
    image.texture_descriptor.dimension = TextureDimension::D2;
    image.texture_descriptor.format = TextureFormat::Rgba16Float;
    image.texture_descriptor.mip_level_count = mips;
    // A geração GPU cria views de STORAGE sobre o cubemap fonte (SPD/copy)
    // — sem STORAGE_BINDING no usage a view é inválida e o prepare do
    // GpuImage panica (storage_view_mip_N).
    image.texture_descriptor.usage = bevy::render::render_resource::TextureUsages::TEXTURE_BINDING
        | bevy::render::render_resource::TextureUsages::STORAGE_BINDING
        | bevy::render::render_resource::TextureUsages::COPY_DST;
    image.asset_usage = RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD;
    image.data = Some(data);
    // View CUBE explícito: sem ele o GpuImage infere pela contagem de
    // layers, mas o explícito é o contrato do exemplo canónico do bevy.
    image.texture_view_descriptor = Some(TextureViewDescriptor {
        dimension: Some(TextureViewDimension::Cube),
        ..Default::default()
    });
    image
}

/// Tamanho total em BYTES da mip chain de um cubemap `face_size`² com
/// `mips` níveis (RGBA16Float = 8 B/texel, 6 faces por nível).
fn mip_chain_bytes(face_size: u32, mips: u32) -> usize {
    (0..mips).map(|l| {
        let size = face_size >> l;
        (size * size * 6) as usize * 8
    }).sum()
}

/// Offset (bytes) do início do mip `level` (layout bevy: mip a mip, 6 faces
/// por mip).
fn mip_offset(face_size: u32, level: u32) -> usize {
    (0..level).map(|l| {
        let size = face_size >> l;
        (size * size * 6) as usize * 8
    }).sum()
}

/// f16 → f32 (o inverso de [`f32_to_f16`], para o downsample dos mips).
fn f16_to_f32(bits: u16) -> f32 {
    let sign = ((bits & 0x8000) as u32) << 16;
    let exp = ((bits >> 10) & 0x1f) as i32;
    let mant = (bits & 0x03ff) as u32;
    let value = if exp == 0 {
        // subnormal (ou zero) — chega para cores escuras
        (mant as f32) / 1024.0 / 65536.0
    } else if exp == 31 {
        f32::INFINITY
    } else {
        (1.0 + mant as f32 / 1024.0) * (2.0f32).powi(exp - 15)
    };
    f32::from_bits(sign | value.to_bits())
}

/// Direção da amostra (u,v) da face `f` — convenção WebGPU/Vulkan
/// (+X, −X, +Y, −Y, +Z, −Z), normalizada.
fn face_direction(face: u32, uc: f32, vc: f32) -> Vec3 {
    let dir = match face {
        0 => Vec3::new(1.0, -vc, -uc),
        1 => Vec3::new(-1.0, -vc, uc),
        2 => Vec3::new(uc, 1.0, vc),
        3 => Vec3::new(uc, -1.0, -vc),
        4 => Vec3::new(uc, -vc, 1.0),
        _ => Vec3::new(-uc, -vc, -1.0),
    };
    dir.normalize()
}

/// Radiância do céu numa direção — o MESMO modelo visual do domo
/// (`crate::sky`), em versão CPU: gradiente por elevação (ou o scattering
/// físico nishita quando o mundo o pede), halo+disco do sol (só no ramo
/// analítico — o nishita já traz o pico de Mie), lua à noite e chão
/// escurecido sob o horizonte.
fn sky_radiance(
    a: &AtmosphereState,
    dir: Vec3,
    sun: Vec3,
    moon: Vec3,
    nishita: Option<&crate::sky_nishita::NishitaModel>,
    ground_tint: Option<[f32; 3]>,
) -> [f32; 3] {
    let up = dir.y;
    // Céu: horizonte → zénite; chão: horizonte → chão escuro (a bounce do
    // terreno entra pelo fog dessaturado).
    let sky = match nishita {
        Some(model) => {
            // Port CPU do raymarch do domo + o piso de airglow noturno
            // (o MESMO CFG_NISHITA_NIGHT_FLOOR do WGSL — sem ele o IBL
            // noturno ficava mais escuro que o domo).
            let c = crate::sky_nishita::radiance(dir, sun, 1.0, model);
            let floor = mix_rgb(a.horizon, a.zenith, smoothstep(0.0, 0.45, up))
                .map(|v| v * a.night * crate::sky_nishita::NIGHT_FLOOR);
            [c[0] + floor[0], c[1] + floor[1], c[2] + floor[2]]
        }
        None => mix_rgb(a.horizon, a.zenith, smoothstep(0.0, 0.45, up)),
    };
    let ground_default = a.horizon.map(|c| c * 0.18 + a.fog_of(c));
    let ground = match ground_tint {
        Some(t) => mix_rgb(ground_default, t, TINT_MIX_GROUND),
        None => ground_default,
    };
    let mut color = mix_rgb(ground, sky, smoothstep(-0.12, 0.06, up));

    // Lua: fria e fraca, só quando o sol está baixo (ambos os modelos).
    let md = dir.dot(moon).max(0.0) * a.night;
    let moon_glow = (md.powi(64)) * 6.0 + (md.powi(8)) * 0.15;
    // Sol analítico: disco + halo largo (forward scatter). O disco não passa
    // de um teto para não incendiar o diffuse filtrado. No ramo nishita a
    // radiância física já pica na direção do sol — não duplicar.
    let mut sun_add = [0.0f32; 3];
    if nishita.is_none() {
        let sd = dir.dot(sun).max(0.0);
        let halo = (sd.powi(8)) * 0.35 + (sd.powi(64)) * 1.2;
        let disc = smoothstep(0.9995, 0.99985, sd) * 60.0;
        for i in 0..3 {
            sun_add[i] = a.sun_tint[i] * (halo + disc);
        }
    }
    for i in 0..3 {
        color[i] += sun_add[i] + [0.55, 0.68, 1.0][i] * moon_glow;
    }
    color
}

/// f32 → f16 (half) com round-to-nearest-even; a textura é `Rgba16Float`
/// e os bytes têm de ser gravados em f16. Infinitos/NaN colapsam a zero —
/// a paleta é sempre finita.
fn f32_to_f16(v: f32) -> u16 {
    let bits = v.to_bits();
    let sign = ((bits >> 16) & 0x8000) as u16;
    let exp = ((bits >> 23) & 0xff) as i32;
    let mant = bits & 0x007f_ffff;
    if exp == 0xff {
        // inf/nan → 0 (defensivo; não ocorre com a paleta)
        return sign;
    }
    let unbiased = exp - 127;
    if unbiased >= 16 {
        return sign | 0x7c00; // overflow → inf
    }
    if unbiased >= -14 {
        // normal
        let e = (unbiased + 15) as u32;
        let m = mant >> 13;
        let mut out = (e << 10) | m;
        // round-to-nearest-even pelo resto
        let rest = mant & 0x1fff;
        if rest > 0x1000 || (rest == 0x1000 && (m & 1) == 1) {
            out += 1;
        }
        return sign | out as u16;
    }
    if unbiased >= -25 {
        // subnormal
        let mut m = (mant | 0x0080_0000) >> (14 - unbiased);
        let rest = (mant | 0x0080_0000) & ((1 << (14 - unbiased)) - 1);
        if rest > (1 << (13 - unbiased)) || (rest == (1 << (13 - unbiased)) && (m & 1) == 1) {
            m += 1;
        }
        return sign | m as u16;
    }
    sign // underflow → 0
}

fn mix_rgb(a: [f32; 3], b: [f32; 3], t: f32) -> [f32; 3] {
    [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

impl AtmosphereState {
    /// O chão sob o horizonte mistura o fog (bounce do terreno); helper
    /// local para não expor `fog` como método.
    fn fog_of(&self, horizon_channel: f32) -> f32 {
        horizon_channel * 0.5 + (self.fog[0] + self.fog[1] + self.fog[2]) / 3.0 * 0.5
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cubemap_tem_6_faces_validas() {
        let a = AtmosphereState::default();
        let image = sky_cubemap(&a, None);
        assert_eq!(image.size(), bevy::math::UVec2::splat(FACE_SIZE));
        assert_eq!(
            image.texture_descriptor.array_layer_count(),
            6,
            "6 faces do cubemap"
        );
        // Bytes de TODA a mip chain (a geração GPU exige views por mip):
        // 128 = 2^7 → 8 níveis (128..1) — o mip_7 (1×1) existe.
        let mips = FACE_SIZE.trailing_zeros() + 1;
        let bytes = image.data.as_ref().map_or(0, Vec::len);
        assert_eq!(bytes, mip_chain_bytes(FACE_SIZE, mips));
        assert_eq!(image.texture_descriptor.mip_level_count, mips);
    }

    #[test]
    fn faces_sao_ortonormais_e_distintas() {
        // Cada face aponta para o seu quadrante; o centro de +Y tem de ser
        // o zénite (para o gradiente sair direito no filtro).
        assert!((face_direction(2, 0.0, 0.0) - Vec3::Y).length() < 1e-4);
        assert!((face_direction(0, 0.0, 0.0) - Vec3::X).length() < 1e-4);
        assert!((face_direction(5, 0.0, 0.0) + Vec3::Z).length() < 1e-4);
        for face in 0..6u32 {
            let d = face_direction(face, 0.37, -0.71);
            assert!((d.length() - 1.0).abs() < 1e-4, "direção normalizada");
        }
    }

    #[test]
    fn sol_acesce_a_face_certa_e_noite_fica_azulada() {
        let mut a = AtmosphereState::default();
        a.sun_dir = Vec3::new(0.0, 1.0, 0.0);
        a.night = 0.0;
        let at_sun = sky_radiance(&a, Vec3::Y, a.sun_dir, a.moon_dir, None, None);
        let away = sky_radiance(&a, -Vec3::Y, a.sun_dir, a.moon_dir, None, None);
        assert!(at_sun[1] > away[1], "olhar para o sol é mais luminoso");

        // Noite: zénite azulado (b > r) e escuro.
        a.night = 1.0;
        a.day = 0.0;
        a.zenith = [0.02, 0.03, 0.08];
        a.horizon = [0.04, 0.05, 0.10];
        a.sun_dir = -Vec3::Y;
        a.moon_dir = Vec3::Y;
        let night = sky_radiance(&a, Vec3::Y, a.sun_dir, a.moon_dir, None, None);
        assert!(night[2] > night[0], "noite azulada");
    }

    #[test]
    fn ramo_nishita_pinta_o_sol_e_escurece_a_noite() {
        let mut a = AtmosphereState::default();
        a.sun_dir = Vec3::new(0.0, 1.0, 0.0);
        a.night = 0.0;
        a.day = 1.0;
        let model = crate::sky_nishita::NishitaModel::default();
        let at_sun = sky_radiance(&a, Vec3::Y, a.sun_dir, a.moon_dir, Some(&model), None);
        let off = sky_radiance(
            &a,
            Vec3::new(0.2, 0.8, 0.5).normalize(),
            a.sun_dir,
            a.moon_dir,
            Some(&model),
            None,
        );
        // A direção do sol (zénite) domina a radiância física; tudo finito.
        assert!(
            at_sun.iter().all(|v| v.is_finite()),
            "radiância nishita finita: {at_sun:?}"
        );
        assert!(
            at_sun.iter().sum::<f32>() > off.iter().sum::<f32>(),
            "sol mais brilhante que fora do eixo"
        );
        // Noite física ≈ piso de airglow da paleta (escuro mas não NaN/negativo).
        a.night = 1.0;
        a.day = 0.0;
        a.sun_dir = -Vec3::Y;
        let night = sky_radiance(&a, Vec3::Y, a.sun_dir, a.moon_dir, Some(&model), None);
        assert!(
            night.iter().all(|v| v.is_finite() && *v >= 0.0),
            "noite nishita sã: {night:?}"
        );
        assert!(
            night.iter().sum::<f32>() < at_sun.iter().sum::<f32>(),
            "noite bem mais escura que o meio-dia"
        );
    }
}
