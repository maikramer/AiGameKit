//! Probes regionais — o terceiro pilar do caminho raster de reflexões
//! (SSR na água/chão molhado + IBL do céu + ESTES probes por bioma).
//!
//! O IBL mundial ([`crate::ibl`]) é um probe do tamanho do mundo: o
//! ambiente/reflexo especular dos props é o MESMO céu em todo o lado. Estes
//! probes REGIONAIS nascem automaticamente nos `<TerrainPad>` (plazas,
//! aldeias — os sítios autorais onde há props à volta) cujo `<BiomeRegion>`
//! declara `tint`: o cubemap é o MESMO pintor do IBL
//! ([`crate::ibl::sky_cubemap_tinted`]) com o tint misturado no hemisfério de
//! baixo — o "bounce" local (pântano esverdeado, deserto arenoso). O Bevy
//! 0.19 aplica parallax correction automática por AABB (influence volume),
//! portanto o especular dos props lê-se LOCAL perto do pad e devolve o céu
//! mundial fora dele — sem double-counting (o clustering de probes do 0.19
//! mistura por peso).
//!
//! **Barato por construção:** pintura CPU reutilizando o painter do IBL
//! (128²×6, uma vez por fase do dia — o MESMO ciclo do probe mundial); nada
//! de render-to-cubemap (bake de cena = follow-up, VISUAL_ROADMAP P2.18).
//!
//! **Regras:** máx. [`MAX_PROBES`], pads a <[`MIN_SPACING_M`]> colapsam para
//! um, pads FORA de região com tint não geram probe (seria um clone do céu
//! mundial = ganho zero). Gate `VIBER_PROBES=0` desliga tudo.

use bevy::light::{GeneratedEnvironmentMapLight, LightProbe};
use bevy::prelude::*;

use crate::terrain::runtime::TerrainRuntime;
use crate::terrain::sampler::ResolvedPad;
use crate::worldsys::{AtmosphereState, BiomeRegionData, BiomeRegions, DayCycleState};

/// Teto de probes regionais (98 KB/cubemap CPU + filtro GPU por fase).
const MAX_PROBES: usize = 8;
/// Pads mais perto do que isto colapsam para um probe (fica o 1.º).
const MIN_SPACING_M: f32 = 40.0;
/// Meia-extensão horizontal do influence volume (m) — clampada.
const INFLUENCE_MIN: f32 = 45.0;
const INFLUENCE_MAX: f32 = 140.0;
/// Altura do influence volume (m) — cobre os props de um assentamento.
const INFLUENCE_HEIGHT: f32 = 45.0;

/// Tint regional do probe (guardado para o repaint por fase do dia).
#[derive(Component)]
struct ProbeTint([f32; 3]);

/// Marcador dos probes regionais (o mundial do IBL não o tem).
#[derive(Component)]
struct RegionalProbe;

/// Gate: `VIBER_PROBES=0` desliga (default ON — caminho raster oficial).
pub fn probes_requested() -> bool {
    !matches!(
        std::env::var("VIBER_PROBES")
            .ok()
            .map(|v| v.trim().to_ascii_lowercase())
            .as_deref(),
        Some("0" | "false" | "no" | "off")
    )
}

pub struct RegionalProbesPlugin;

impl Plugin for RegionalProbesPlugin {
    fn build(&self, app: &mut App) {
        if !probes_requested() {
            return;
        }
        app.add_systems(Update, update_regional_probes);
    }
}

/// Região a conter o ponto (even-odd point-in-polygon). A 1.ª que der match.
fn region_at<'a>(regions: &'a BiomeRegions, p: Vec2) -> Option<&'a BiomeRegionData> {
    regions.list.iter().find(|r| {
        let poly = &r.polygon;
        if poly.len() < 3 {
            return false;
        }
        let mut inside = false;
        let mut j = poly.len() - 1;
        for i in 0..poly.len() {
            let (xi, zi) = (poly[i][0], poly[i][1]);
            let (xj, zj) = (poly[j][0], poly[j][1]);
            if ((zi > p.y) != (zj > p.y))
                && (p.x < (xj - xi) * (p.y - zi) / (zj - zi + f32::EPSILON) + xi)
            {
                inside = !inside;
            }
            j = i;
        }
        inside
    })
}

/// Escolhe os pads que ganham probe: dentro de região com tint, espaçados.
fn pick_pads<'a>(pads: &'a [ResolvedPad], regions: &BiomeRegions) -> Vec<(&'a ResolvedPad, [f32; 3])> {
    let mut picked: Vec<(&ResolvedPad, [f32; 3])> = Vec::new();
    for pad in pads {
        if picked.len() >= MAX_PROBES {
            break;
        }
        let Some(region) = region_at(regions, pad.at) else {
            continue;
        };
        let Some(tint) = region.tint else {
            continue; // sem tint = clone do céu mundial = ganho zero
        };
        if picked
            .iter()
            .any(|(p, _)| p.at.distance(pad.at) < MIN_SPACING_M)
        {
            continue;
        }
        picked.push((pad, tint));
    }
    picked
}

/// Fase do dia (0..6) — o MESMO ciclo que regenera o probe mundial
/// ([`crate::ibl`]); os regionais acompanham para o tint acompanhar a luz.
fn day_phase(clock: Option<&DayCycleState>) -> Option<u32> {
    let clock = clock?;
    let day = crate::worldsys::daylight_factor(
        clock.minute_of_day,
        clock.dawn_minute,
        clock.dusk_minute,
    );
    Some((day.clamp(0.0, 1.0) * 6.0).floor() as u32)
}

/// Nasce uma vez (quando o bootstrap publica o `TerrainRuntime`) e repinta
/// quando a fase do dia muda.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn update_regional_probes(
    mut commands: Commands,
    runtime: Option<Res<TerrainRuntime>>,
    regions: Option<Res<BiomeRegions>>,
    clock: Option<Res<DayCycleState>>,
    atmosphere: Option<Res<AtmosphereState>>,
    model_state: Option<Res<crate::sky::SkyModelState>>,
    mut images: ResMut<Assets<Image>>,
    probes: Query<(Entity, &ProbeTint, &GeneratedEnvironmentMapLight), With<RegionalProbe>>,
    mut spawned: Local<bool>,
    mut last_phase: Local<Option<Option<u32>>>,
) {
    let phase = day_phase(clock.as_deref());
    if *spawned && last_phase.is_some_and(|last| last == phase) {
        return;
    }
    let (Some(runtime), Some(regions)) = (runtime.as_deref(), regions.as_deref()) else {
        return; // bootstrap ainda não publicou o mundo
    };
    // Sem relógio, a fase é None (pinta UMA vez como o IBL mundial).
    let atmosphere = atmosphere
        .as_deref()
        .cloned()
        .unwrap_or_else(AtmosphereState::default);
    let nishita = model_state
        .as_deref()
        .filter(|s| s.model == crate::sky::SkyModel::Nishita)
        .map(|s| s.nishita);
    let pads = pick_pads(&runtime.pads, regions);

    if !*spawned {
        *spawned = true;
        for (pad, tint) in &pads {
            let handle = images.add(crate::ibl::sky_cubemap_tinted(
                &atmosphere,
                nishita.as_ref(),
                Some(*tint),
            ));
            let extent_x = (pad.size.x * 0.9).clamp(INFLUENCE_MIN, INFLUENCE_MAX);
            let extent_z = (pad.size.y * 0.9).clamp(INFLUENCE_MIN, INFLUENCE_MAX);
            commands.spawn((
                Name::new(format!("regional probe ({:.0},{:.0})", pad.at.x, pad.at.y)),
                RegionalProbe,
                ProbeTint(*tint),
                LightProbe::default(),
                Transform::from_translation(Vec3::new(pad.at.x, pad.height + 2.0, pad.at.y))
                    .with_scale(Vec3::new(extent_x, INFLUENCE_HEIGHT, extent_z)),
                GeneratedEnvironmentMapLight {
                    environment_map: handle,
                    intensity: 1.0,
                    ..Default::default()
                },
            ));
        }
        if !pads.is_empty() {
            info!(
                "viber: {} probes regionais nos pads (tint de bioma, parallax AABB)",
                pads.len()
            );
        }
    } else {
        // Repaint por fase: trocar o handle do cubemap mantendo o tint de
        // cada probe (o filtro GPU do bevy re-corre no asset novo; o velho
        // sai dos Assets explicitamente — o padrão do IBL mundial).
        for (entity, tint, current) in &probes {
            let old = current.environment_map.clone();
            let handle = images.add(crate::ibl::sky_cubemap_tinted(
                &atmosphere,
                nishita.as_ref(),
                Some(tint.0),
            ));
            commands
                .entity(entity)
                .try_insert(GeneratedEnvironmentMapLight {
                    environment_map: handle,
                    intensity: 1.0,
                    ..Default::default()
                });
            if old != Handle::default() {
                images.remove(old.id());
            }
        }
    }
    *last_phase = Some(phase);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn region(polygon: &[[f32; 2]], tint: Option<[f32; 3]>) -> BiomeRegionData {
        BiomeRegionData {
            id: "r".into(),
            display_name: String::new(),
            polygon: polygon.to_vec(),
            fog_density: 0.0,
            tint,
            pp_exposure: None,
            pp_bloom_strength: None,
        }
    }

    #[test]
    fn test_point_in_polygon() {
        let regions = BiomeRegions {
            list: vec![region(
                &[[-80.0, -90.0], [20.0, -90.0], [20.0, -10.0], [-80.0, -10.0]],
                None,
            )],
        };
        // dentro
        assert!(region_at(&regions, Vec2::new(-34.0, -42.0)).is_some());
        // fora (nordeste e leste)
        assert!(region_at(&regions, Vec2::new(0.0, 0.0)).is_none());
        assert!(region_at(&regions, Vec2::new(200.0, -42.0)).is_none());
    }

    #[test]
    fn test_pads_sem_tint_nao_geram_probe() {
        let regions = BiomeRegions {
            list: vec![region(
                &[[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]],
                None,
            )],
        };
        let pads = vec![ResolvedPad {
            at: Vec2::new(50.0, 50.0),
            size: Vec2::splat(48.0),
            falloff: 8.0,
            corner_radius: 4.0,
            height: 10.0,
        }];
        assert!(
            pick_pads(&pads, &regions).is_empty(),
            "região sem tint ⇒ sem probe (seria clone do céu mundial)"
        );
    }

    #[test]
    fn test_pads_com_tint_e_espacamento() {
        let regions = BiomeRegions {
            list: vec![region(
                &[[-200.0, -200.0], [200.0, -200.0], [200.0, 200.0], [-200.0, 200.0]],
                Some([0.4, 0.5, 0.3]),
            )],
        };
        let mk = |x: f32, y: f32| ResolvedPad {
            at: Vec2::new(x, y),
            size: Vec2::splat(48.0),
            falloff: 8.0,
            corner_radius: 4.0,
            height: 10.0,
        };
        let pads = vec![mk(0.0, 0.0), mk(10.0, 5.0), mk(120.0, 80.0), mk(500.0, 500.0)];
        let picked = pick_pads(&pads, &regions);
        // colapsa os dois próximos (10,5 fica de fora), apanha o distante,
        // e o pad FORA da região nem entra na conta.
        assert_eq!(picked.len(), 2);
        assert_eq!(picked[0].0.at, Vec2::new(0.0, 0.0));
        assert_eq!(picked[1].0.at, Vec2::new(120.0, 80.0));
    }
}
