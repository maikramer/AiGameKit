//! `<StaticSpawner>` runtime: deterministic instance placement on the terrain.
//!
//! Placement is a pure function of (spec, occupancy, terrain sampler) driven
//! by a SplitMix64 RNG — the same seed produces the same forest. The live
//! sampler wraps [`TerrainRuntime`]; tests pass closures.
//!
//! Features ported from the VibeGame spawner: terrain alignment via a
//! weighted 3×3 sample matrix with a clamped partial tilt, a cell-bucketed
//! occupancy registry shared by every spawner group (rocks never spawn inside
//! trees, nothing spawns inside a `<SpawnExclusion>`), conservative
//! footprint testing with breathing room, and a density-per-km² count mode.

use std::collections::HashMap;

use bevy::asset::LoadState;
use bevy::camera::primitives::MeshAabb as _;
use bevy::gltf::{Gltf, GltfMesh};
use bevy::math::Vec3;
use bevy::prelude::*;
use bevy::world_serialization::WorldAssetRoot;

use crate::recipes::StaticSpawnerSpec;
use crate::terrain::cliffs::CliffMask;
use crate::terrain::runtime::TerrainRuntime;

/// Small deterministic RNG — vive em [`crate::rng`] (um só SplitMix64 na
/// engine); re-exportado aqui porque metade do código de gameplay refere
/// `crate::spawner::Rng`.
pub use crate::rng::Rng;

/// Todos os discos `<SpawnExclusion>` do mundo, como RECURSO.
///
/// Separado do [`PendingSpawnGroups`] (que é estado de uma passagem só e é
/// removido no fim da colocação) porque os discos são dados AUTORAIS e têm
/// mais do que um consumidor: os spawners (colocação) e a relva procedural
/// (`src/grass.rs`, que desenha um tapete por cima de tudo e ignorava os
/// discos — um acampamento numa clareira acordava enterrado em tufos).
#[derive(Debug, Default, Resource)]
pub struct SpawnExclusions(pub Vec<SpawnExclusion>);

impl SpawnExclusions {
    /// `(x, z)` está dentro de algum disco?
    pub fn contains(&self, x: f32, z: f32) -> bool {
        self.0.iter().any(|e| {
            let dx = x - e.center.x;
            let dz = z - e.center.y;
            dx * dx + dz * dz <= e.radius * e.radius
        })
    }
}

/// Global no-spawn circle (`<SpawnExclusion at="x z" radius="n">`).
#[derive(Debug, Clone, Copy)]
pub struct SpawnExclusion {
    pub center: bevy::math::Vec2,
    pub radius: f32,
}

/// One placed template instance.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedInstance {
    pub position: Vec3,
    /// Heading alone (diagnóstico/testes); a rotação completa vive em
    /// [`PlacedInstance::rotation`].
    pub yaw_deg: f32,
    /// Rotação final mundo: yaw e (com `align-to-terrain`) o tilt parcial
    /// clampado em direção à normal do terreno.
    pub rotation: Quat,
    pub scale: Vec3,
    /// Index into the spawner's `template_urls` (and `handles`).
    pub template_index: usize,
}

/// Largura da célula do registo de ocupação (metros) — props são
/// escala-metro, e células de 4 m mantêm cada consulta num vizinhança 3×3.
const OCCUPANCY_CELL: f32 = 4.0;
/// Folga (metros) entre footprints registados: props nunca nascem "colados"
/// uns aos outros nem às zonas excluídas (VibeGame `SPAWN_CLEARANCE`).
const SPAWN_CLEARANCE: f32 = 0.6;
/// Limite de espera (segundos) por assets de template presos em Loading até
/// o grupo ser desistido — o gate de todos-os-grupos não pode pendurar o
/// mundo para sempre.
const SPAWN_WAIT_TIMEOUT: f32 = 60.0;
/// Pontos do anel de pegada: o declive no PÉ do prop não diz nada sobre o
/// degrau a um metro do tronco — o anel partilha os gates do centro (declive
/// pela matriz 3×3, água, cliff) com a pegada inteira.
const SPAWN_RING_SAMPLES: usize = 8;
/// Raio mínimo (metros) do anel de pegada quando o footprint não o define —
/// cobre a zona da base mesmo em props sem `footprint-radius`.
const SPAWN_RING_MIN_RADIUS: f32 = 1.5;
/// Spread de altura (metros) centro↔anel acima do qual o candidato está a
/// montar um degrau/terraço — o gate de declive não apanha degraus que
/// cruzam a pegada com declive médio baixo.
const SPAWN_LEDGE_DROP: f32 = 2.0;
/// Discos com raio acima disto entram na lista `large` de [`SpawnOccupancy`]
/// e são testados directamente (distância centro-a-centro), não por células:
/// um raio patológico do XML (ex.: `<SpawnExclusion radius="100000">`)
/// bucketizado são centenas de milhões de entradas. O teste em `is_free` é
/// idêntico ao das células — só o índice muda.
const LARGE_DISK_RADIUS: f32 = 256.0;

/// Registo de ocupação XZ partilhado por TODOS os spawners do mundo (port do
/// `occupancy.ts` do VibeGame): cada instância colocada (e cada
/// `<SpawnExclusion>`) registra um disco; candidatos cujo disco encosta um
/// disco registado — com folga — são rejeitados. Rochas não nascem dentro de
/// árvores, nada nasce dentro de uma exclusão, e árvores não nascem dentro da
/// cabana: quem registra depois desvia de quem registou antes.
///
/// Buckets por célula XZ mantêm cada consulta nos poucos discos que a podem
/// tocar — uma lista plana tornava a geração quadrática no nº de props.
pub struct SpawnOccupancy {
    cells: HashMap<i64, Vec<(f32, f32, f32)>>,
    /// Discos gigantes (exclusões "mundiais"), testados sem células.
    large: Vec<(f32, f32, f32)>,
}

impl Default for SpawnOccupancy {
    fn default() -> Self {
        Self::new()
    }
}

impl SpawnOccupancy {
    pub fn new() -> Self {
        Self {
            cells: HashMap::new(),
            large: Vec::new(),
        }
    }

    /// Chave da célula: intercalação de dois i32 — coordenadas até ±2³¹
    /// células (±8.6 M km a 4 m) nunca colidem na mesma chave.
    fn cell_key(cx: i32, cz: i32) -> i64 {
        ((cx as i64) << 32) | (cz as i64 & 0xFFFF_FFFF)
    }

    /// Registra um disco XZ. O disco entra em TODAS as células que toca, para
    /// uma consulta que caia em qualquer delas o encontrar.
    pub fn register(&mut self, x: f32, z: f32, radius: f32) {
        if radius <= 0.0 || radius.is_nan() {
            return;
        }
        if radius > LARGE_DISK_RADIUS {
            self.large.push((x, z, radius));
            return;
        }
        let x0 = ((x - radius) / OCCUPANCY_CELL).floor() as i32;
        let x1 = ((x + radius) / OCCUPANCY_CELL).floor() as i32;
        let z0 = ((z - radius) / OCCUPANCY_CELL).floor() as i32;
        let z1 = ((z + radius) / OCCUPANCY_CELL).floor() as i32;
        for cz in z0..=z1 {
            for cx in x0..=x1 {
                self.cells
                    .entry(Self::cell_key(cx, cz))
                    .or_default()
                    .push((x, z, radius));
            }
        }
    }

    /// True quando um disco em `(x, z)` não encosta nenhum disco registado
    /// (contando a folga). O alcance da busca é o PRÓPRIO raio + folga: se
    /// dois discos se sobrepõem, um ponto do menor fica dentro do maior, e as
    /// AABBs dos dois cobrem a célula desse ponto — partilham sempre um
    /// bucket, mesmo que o disco registado seja muito maior.
    pub fn is_free(&self, x: f32, z: f32, radius: f32) -> bool {
        if self.cells.is_empty() && self.large.is_empty() {
            return true;
        }
        // Discos gigantes: o mesmo teste geométrico dos buckets, sem célula.
        for &(fx, fz, fr) in &self.large {
            let dx = fx - x;
            let dz = fz - z;
            let min_dist = fr + radius + SPAWN_CLEARANCE;
            if dx * dx + dz * dz < min_dist * min_dist {
                return false;
            }
        }
        let reach = radius + SPAWN_CLEARANCE;
        let x0 = ((x - reach) / OCCUPANCY_CELL).floor() as i32;
        let x1 = ((x + reach) / OCCUPANCY_CELL).floor() as i32;
        let z0 = ((z - reach) / OCCUPANCY_CELL).floor() as i32;
        let z1 = ((z + reach) / OCCUPANCY_CELL).floor() as i32;
        for cz in z0..=z1 {
            for cx in x0..=x1 {
                let Some(bucket) = self.cells.get(&Self::cell_key(cx, cz)) else {
                    continue;
                };
                for &(fx, fz, fr) in bucket {
                    let dx = fx - x;
                    let dz = fz - z;
                    let min_dist = fr + radius + SPAWN_CLEARANCE;
                    if dx * dx + dz * dz < min_dist * min_dist {
                        return false;
                    }
                }
            }
        }
        true
    }
}

/// Inclinação mínima (rad) abaixo da qual o solo conta como plano — só yaw
/// (VibeGame `partialAlignEuler`, ~5°).
const ALIGN_MIN_SLOPE: f32 = 0.087;
/// Tilt máximo (rad) — troncos seguem o declive mas nunca deitam em falésias
/// (π/3 = 60°; o gate de `max-slope-deg` já filtra posições absurdas).
const ALIGN_MAX_TILT: f32 = core::f32::consts::FRAC_PI_3;

/// Rotação de alinhamento parcial ao terreno: abaixo de ~5° de declive fica
/// só o yaw; acima, o +Y do modelo inclina-se para a normal com inclinação
/// linear e clamp a 60°. O yaw é aplicado sobre o eixo do tronco ANTES do
/// tilt (`q_tilt * q_yaw`) — árvores continuam a girar sobre si mesmas
/// enquanto se inclinem encosta abaixo.
pub fn terrain_align_rotation(normal: Vec3, yaw_rad: f32, slope_rad: f32) -> Quat {
    if slope_rad < ALIGN_MIN_SLOPE || normal.y > 0.9999 {
        return Quat::from_rotation_y(yaw_rad);
    }
    let n = normal.normalize_or_zero();
    let tilt_axis = Vec3::Y.cross(n);
    if tilt_axis.length_squared() < 1e-12 {
        return Quat::from_rotation_y(yaw_rad);
    }
    let tilt_axis = tilt_axis.normalize();
    let t = ((slope_rad - ALIGN_MIN_SLOPE) / (ALIGN_MAX_TILT - ALIGN_MIN_SLOPE)).clamp(0.0, 1.0);
    let tilt = t * ALIGN_MAX_TILT;
    Quat::from_axis_angle(tilt_axis, tilt) * Quat::from_rotation_y(yaw_rad)
}

/// Contagem efetiva do grupo: `count` directo, ou o modo densidade —
/// `density-per-km2 × área km² da região XZ`, com teto `max-instances`
/// (`0` = sem teto). O modo densidade só liga quando `count` é zero, como no
/// VibeGame.
fn resolved_count(spec: &StaticSpawnerSpec) -> u32 {
    if spec.density_per_km2 > 0.0 && spec.count == 0 {
        let dx = (spec.region_max[0] - spec.region_min[0]).abs();
        let dz = (spec.region_max[2] - spec.region_min[2]).abs();
        let n = (spec.density_per_km2 * dx * dz / 1.0e6).round().max(1.0);
        let capped = if spec.max_instances > 0 {
            n.min(spec.max_instances as f32)
        } else {
            n
        };
        capped as u32
    } else {
        spec.count
    }
}

/// Terrain query result for one candidate position.
#[derive(Debug, Clone, Copy)]
pub struct TerrainSample {
    pub height: f32,
    /// Surface normal (normalized by the sampler).
    pub normal: Vec3,
    pub water: bool,
    /// Y da lâmina de água quando o ponto está sobre um corpo de água
    /// (`in-water` assenta À SUPERFÍCIE, não no fundo escavado); `None` em
    /// seco.
    pub water_surface: Option<f32>,
    /// Point is on dry land but close enough to water to count as shoreline.
    pub near_water: bool,
    /// Point sits on a carved road ribbon (`<Road>` / `<RoadNetwork>`).
    pub road: bool,
    /// Point is cliff terrain or within the group's `cliff-margin` of the
    /// cliff mask (`CliffMask::is_cliff_within` — distância REAL em metros,
    /// margem + pegada já cozidas pela closure do runtime).
    pub cliff: bool,
    /// Standing surface is a THIN slab floating over hollow ground — the
    /// band of an `<Arch>`, the brow of a tight overhang
    /// (`TerrainRuntime::has_thin_roof`). Sempre `false` em mundo flat.
    pub roof: bool,
}

/// Motivos de rejeição de uma passagem de colocação — um candidato
/// rejeitado conta EXATAMENTE um motivo (o primeiro gate que o apanhou,
/// pela ordem: distância → ocupação → água → in-water → margem → estrada →
/// cliff → tecto → declive-centro → anel → degrau). Alimenta o log por grupo
/// e os testes; QA vive no `viber debug logs`.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct PlacementStats {
    /// Candidatos avaliados (cada um queima uma tentativa de
    /// `max-slope-attempts`).
    pub attempts: u32,
    pub placed: usize,
    pub rejected_max_distance: u32,
    pub rejected_occupancy: u32,
    pub rejected_water: u32,
    pub rejected_in_water: u32,
    pub rejected_near_water: u32,
    pub rejected_road: u32,
    pub rejected_cliff: u32,
    pub rejected_roof: u32,
    pub rejected_slope_center: u32,
    pub rejected_slope_ring: u32,
    pub rejected_ledge: u32,
}

impl PlacementStats {
    /// Total de rejeições, todas as causas.
    pub fn rejected(&self) -> u32 {
        self.rejected_max_distance
            + self.rejected_occupancy
            + self.rejected_water
            + self.rejected_in_water
            + self.rejected_near_water
            + self.rejected_road
            + self.rejected_cliff
            + self.rejected_roof
            + self.rejected_slope_center
            + self.rejected_slope_ring
            + self.rejected_ledge
    }
}

/// Pick a position for one instance: cluster center + disc offset, clamped to
/// the region rectangle.
fn next_candidate(
    spec: &StaticSpawnerSpec,
    rng: &mut Rng,
    clusters: &[bevy::math::Vec2],
) -> bevy::math::Vec2 {
    let mut pos = if clusters.is_empty() {
        bevy::math::Vec2::new(
            rng.range(spec.region_min[0], spec.region_max[0]),
            rng.range(spec.region_min[2], spec.region_max[2]),
        )
    } else {
        let center = clusters[rng.next_u64() as usize % clusters.len()];
        center + rng.unit_disc() * spec.cluster_radius
    };
    pos.x = pos.x.clamp(
        spec.region_min[0].min(spec.region_max[0]),
        spec.region_min[0].max(spec.region_max[0]),
    );
    pos.y = pos.y.clamp(
        spec.region_min[2].min(spec.region_max[2]),
        spec.region_min[2].max(spec.region_max[2]),
    );
    pos
}

/// Compute every instance of a spawner group deterministically.
///
/// Each instance gets `max-slope-attempts` samples: a rejected candidate
/// (water, slope, road, cliff, occupancy, footprint ring) burns one attempt
/// and the position is re-rolled; attempts exhausted, the instance is
/// omitted — impossible regions yield fewer than `count`. Every candidate is
/// tested against the shared [`SpawnOccupancy`] (exclusion zones and other
/// groups' footprints are always honored); the group's own footprint discs
/// are registered only when `avoid-overlaps` is on, so dense carpets keep
/// packing among themselves while still steering clear of everything else.
///
/// Devolve as instâncias e as [`PlacementStats`] da passagem (motivos de
/// rejeição para o log de QA).
pub fn compute_placements(
    spec: &StaticSpawnerSpec,
    occupancy: &mut SpawnOccupancy,
    sample: &mut dyn FnMut(f32, f32) -> TerrainSample,
) -> (Vec<PlacedInstance>, PlacementStats) {
    // Candidatos explícitos (pedras de margem): um por entrada, avaliado
    // UMA vez — rejeitado = omitido, sem re-rolls.
    let fixed = !spec.fixed_candidates.is_empty();
    let count = if fixed {
        spec.fixed_candidates.len()
    } else {
        resolved_count(spec) as usize
    };
    let mut stats = PlacementStats::default();
    if count == 0 {
        return (Vec::new(), stats);
    }
    let mut rng = Rng::new(spec.seed ^ 0x5EED_5EED_5EED_5EED);
    let clusters: Vec<bevy::math::Vec2> = if spec.cluster_count > 0 {
        (0..spec.cluster_count)
            .map(|_| {
                bevy::math::Vec2::new(
                    rng.range(spec.region_min[0], spec.region_max[0]),
                    rng.range(spec.region_min[2], spec.region_max[2]),
                )
            })
            .collect()
    } else {
        Vec::new()
    };

    let slope_limit = spec.max_slope_deg.to_radians().cos();
    let attempts = spec.max_slope_attempts.max(1);
    // O grupo registra os próprios discos só com `avoid-overlaps`; contra o
    // que já está registrado (exclusões, outros grupos) TODO candidato é
    // testado. O raio de teste é conservador — footprint × maior escala
    // possível, antes de saber a escala real da instância (VibeGame).
    let footprint = if spec.avoid_overlaps {
        spec.footprint_radius.max(0.0)
    } else {
        0.0
    };
    let scale_span = spec.scale_min.max(spec.scale_max).max(0.0);
    let test_radius = footprint * scale_span;
    // Anel de pegada: a extensão FÍSICA do prop manda, `avoid-overlaps` não —
    // um degrau a um metro do tronco derruba a árvore independentemente do
    // overlap.
    let ring_radius = (spec.footprint_radius.max(0.0) * scale_span).max(SPAWN_RING_MIN_RADIUS);

    let mut out = Vec::with_capacity(count);
    let mut fixed_idx = 0usize;
    for _ in 0..count {
        let mut accepted: Option<PlacedInstance> = None;
        'attempts: for _ in 0..attempts {
            let pos = if fixed {
                if fixed_idx >= spec.fixed_candidates.len() {
                    break;
                }
                let p = spec.fixed_candidates[fixed_idx];
                fixed_idx += 1;
                p
            } else {
                next_candidate(spec, &mut rng, &clusters)
            };
            stats.attempts += 1;
            if spec.max_distance > 0.0 && pos.length() > spec.max_distance {
                stats.rejected_max_distance += 1;
                continue;
            }
            if !occupancy.is_free(pos.x, pos.y, test_radius) {
                stats.rejected_occupancy += 1;
                continue;
            }
            let terrain = sample(pos.x, pos.y);
            // Water and road placement rules — exclusão com exceção:
            // `avoid-water` keeps scenery out of lakes and river channels;
            // `near-water` / `in-water` are the inverse (reeds on a shoreline,
            // aquatic plants ONLY on the water); `avoid-road` (default ON)
            // keeps trees and rocks off the carved ribbons so a road stays
            // walkable.
            if spec.avoid_water && terrain.water {
                stats.rejected_water += 1;
                continue;
            }
            if spec.in_water && !terrain.water {
                stats.rejected_in_water += 1;
                continue;
            }
            if spec.near_water && !terrain.water && !terrain.near_water {
                stats.rejected_near_water += 1;
                continue;
            }
            if spec.avoid_road && terrain.road {
                stats.rejected_road += 1;
                continue;
            }
            // Cliff: a máscara regional (face + vergem) é intransponível —
            // nada nasce em parede nem a `cliff-margin` dela. `in-water` é a
            // exceção habitual (planta aquática à lâmina junto a falésia).
            if spec.avoid_cliff && !spec.in_water && terrain.cliff {
                stats.rejected_cliff += 1;
                continue;
            }
            // Tecto fino: a superfície de pé é uma laje suspensa sobre vazio
            // (banda de um `<Arch>`, brow raso de overhang). O candidato
            // até caberia em cima, mas uma coroa de árvore em pedra de 3 m
            // lê-se como gruda — o vão lá em baixo é que é o sítio honesto,
            // e esse não é de spawner.
            if terrain.roof {
                stats.rejected_roof += 1;
                continue;
            }
            let normal = terrain.normal.normalize_or_zero();
            // `in-water` planta SOBRE a lâmina de água — que é plana: o gate
            // de declive e o tilt leem a normal do FUNDO escavado e
            // rejeitavam/inclinavam bizarramente vitórias-régias em lagoas
            // fundas.
            if !spec.in_water && normal.y < slope_limit {
                stats.rejected_slope_center += 1;
                continue;
            }
            // Anel da pegada com a MESMA matriz 3×3 que alimenta o tilt: o
            // centro plano não impede o tronco de nascer a cavalo num degrau
            // ou à beira de uma parede. Cada ponto partilha os gates do
            // centro (declive, água, cliff); em conjunto com o centro
            // deteta spreads de altura de degrau/terraço. `in-water` salta
            // (a lâmina é plana; o anel haveria de ler margens do leito).
            if !spec.in_water {
                let mut min_h = terrain.height;
                let mut max_h = terrain.height;
                for i in 0..SPAWN_RING_SAMPLES {
                    let a = (i as f32 + 0.5) / SPAWN_RING_SAMPLES as f32 * std::f32::consts::TAU;
                    let point = bevy::math::Vec2::new(
                        pos.x + a.cos() * ring_radius,
                        pos.y + a.sin() * ring_radius,
                    );
                    let ring = sample(point.x, point.y);
                    min_h = min_h.min(ring.height);
                    max_h = max_h.max(ring.height);
                    let ring_normal = ring.normal.normalize_or_zero();
                    if ring_normal.y < slope_limit {
                        stats.rejected_slope_ring += 1;
                        continue 'attempts;
                    }
                    if spec.avoid_water && ring.water {
                        stats.rejected_water += 1;
                        continue 'attempts;
                    }
                    if spec.avoid_cliff && ring.cliff {
                        stats.rejected_cliff += 1;
                        continue 'attempts;
                    }
                }
                if max_h - min_h > SPAWN_LEDGE_DROP {
                    stats.rejected_ledge += 1;
                    continue;
                }
            }

            let scale_u = rng.range(spec.scale_min, spec.scale_max);
            let axis = Vec3::new(
                rng.range(spec.scale_axis_min, spec.scale_axis_max),
                1.0,
                rng.range(spec.scale_axis_min, spec.scale_axis_max),
            );
            let yaw = if spec.random_yaw {
                rng.range(0.0, 360.0)
            } else {
                0.0
            };
            let template_index = if spec.template_urls.len() > 1 {
                (rng.next_u64() as usize) % spec.template_urls.len()
            } else {
                0
            };
            // Registra o disco REAL da instância (escala conhecida: `scale_u`
            // × o maior eixo XZ amostrado) para os grupos seguintes. Usar o
            // produto máximo em ambas as mãos fazia o disco registado exceder
            // o testado quando `scale-axis-max > 1` — rejeições dependentes
            // da ordem do XML, contra o contrato "o teste usa footprint ×
            // scale-max (conservador); o registo usa a escala real".
            if footprint > 0.0 {
                occupancy.register(pos.x, pos.y, footprint * scale_u * axis.x.max(axis.z));
            }
            // Y de assentamento: plantas aquáticas À SUPERFÍCIE da água
            // (VibeGame "in-water: só superfície do lago, Y = waterY"), nunca
            // no fundo da escavação; em seco, o terreno renderizado (ou a
            // base da região com align-to-terrain = 0).
            let y = if spec.in_water {
                terrain.water_surface.unwrap_or(terrain.height)
            } else if spec.align_to_terrain {
                terrain.height
            } else {
                spec.region_min[1]
            };
            let slope_rad = if spec.in_water {
                0.0
            } else {
                normal.y.clamp(-1.0, 1.0).acos()
            };
            let rotation = if spec.align_to_terrain && !spec.in_water {
                terrain_align_rotation(normal, yaw.to_radians(), slope_rad)
            } else {
                Quat::from_rotation_y(yaw.to_radians())
            };
            accepted = Some(PlacedInstance {
                position: Vec3::new(pos.x, y + spec.base_y_offset, pos.y),
                yaw_deg: yaw,
                rotation,
                scale: Vec3::splat(scale_u) * axis,
                template_index,
            });
            break;
        }
        if let Some(instance) = accepted {
            out.push(instance);
            stats.placed += 1;
        }
    }
    (out, stats)
}

/// One collected `<StaticSpawner>`: spec plus one handle per template url.
pub struct SpawnGroupState {
    pub spec: StaticSpawnerSpec,
    pub handles: Vec<Handle<Gltf>>,
    pub done: bool,
    /// Template falhado (load Failed, sem cena, timeout) — o grupo é
    /// desistido com warn e as restantes instâncias do mundo nascem na mesma.
    pub failed: bool,
    /// True para `<DynamicSpawner>` — as instâncias nascem com
    /// [`crate::ai::EnemyCreature`] e o driver de IA conduz-as.
    pub dynamic: bool,
    /// Script do template (criaturas): cada instância spawna com
    /// [`LuaScriptRef`] e o comportamento vive no Luau.
    pub template_script: Option<String>,
    /// Raio de ativação (congelamento) replicado às instâncias.
    pub activation_radius: f32,
    /// Collider do template: cada instância nasce com colisão.
    pub template_collider: Option<crate::physics::ColliderShape>,
    /// Destrutível do template: cada instância nasce colhível
    /// ([`crate::harvest::Destructible`]).
    pub template_destructible: Option<crate::recipes::DestructibleSpec>,
    /// Handle do glTF de colisão do template (pré-carregado).
    pub collider_handle: Option<bevy::asset::Handle<bevy::gltf::Gltf>>,
    /// Ladder de LOD por template: `(lod1, lod2, near, mid)`, mesmo índice
    /// de `handles`. Vazio = o template não autora malhas alternativas.
    #[allow(clippy::type_complexity)]
    pub lod_handles: Vec<(Option<Handle<Gltf>>, Option<Handle<Gltf>>, f32, f32)>,
}

impl SpawnGroupState {
    /// Raio de render das instâncias deste grupo.
    ///
    /// `cull-distance="0"` no XML significa "nunca cortar" — devolve
    /// infinito, e o sistema de culling nunca esconde a instância.
    /// Criaturas de `<DynamicSpawner>` que não autoram o atributo caem no
    /// default dinâmico (mais curto: o script já congela aos 45 m).
    fn cull_radius(&self) -> f32 {
        let authored = self.spec.cull_distance;
        if authored <= 0.0 {
            return f32::INFINITY;
        }
        if self.dynamic && authored == crate::render_lod::DEFAULT_STATIC_CULL {
            return crate::render_lod::DEFAULT_DYNAMIC_CULL;
        }
        authored
    }
}

/// All spawner groups collected at startup; consumed by
/// [`instantiate_spawn_groups`] once the terrain runtime and the template
/// assets are ready.
#[derive(Resource)]
pub struct PendingSpawnGroups {
    pub groups: Vec<SpawnGroupState>,
    /// `<SpawnExclusion>` circles collected across the whole world.
    pub exclusions: Vec<SpawnExclusion>,
    /// Registo de ocupação partilhado: exclusões registadas primeiro, depois
    /// os grupos pela ordem do XML — árvores desviam-se das rochas.
    pub occupancy: SpawnOccupancy,
    /// Segundos à espera que os templates resolvam (gate de todos-os-grupos).
    pub age: f32,
}

/// Meia-largura XZ do AABB do template glTF — o footprint automático quando o
/// mundo não autora `footprint-radius` (VibeGame: auto = meia-largura do GLB,
/// fallback 0.8 para templates sem malha resolvida).
fn template_footprint_radius(
    gltfs: &Assets<Gltf>,
    gltf_meshes: &Assets<GltfMesh>,
    meshes: &Assets<Mesh>,
    handle: &Handle<Gltf>,
) -> Option<f32> {
    let gltf = gltfs.get(handle)?;
    let mut half_extent = 0.0f32;
    let mut found = false;
    for mesh_handle in &gltf.meshes {
        let Some(mesh_asset) = gltf_meshes.get(mesh_handle) else {
            continue;
        };
        for primitive in &mesh_asset.primitives {
            let Some(mesh) = meshes.get(&primitive.mesh) else {
                continue;
            };
            if let Some(aabb) = mesh.compute_aabb() {
                found = true;
                half_extent = half_extent.max(aabb.half_extents.x);
                half_extent = half_extent.max(aabb.half_extents.z);
            }
        }
    }
    found.then(|| half_extent.max(0.1))
}

/// Spawn every instance of every loaded spawner group. Runs each frame until
/// all groups are done, then removes itself.
///
/// Gate determinístico: as posições de TODOS os grupos são computadas no
/// MESMO frame (quando o último template resolve), pela ordem do XML — o
/// registo de ocupação é partilhado, e deixar cada grupo computar no frame
/// em que os seus assets chegam tornaria a floresta dependente da ordem de
/// load. Um template preso em Loading mais de [`SPAWN_WAIT_TIMEOUT`] segundos
/// é desistido com warn para não pendurar o mundo para sempre.
/// Aplica o collider do template a uma instância recém-spawnada:
/// `Box` é imediato; `Mesh`/`Precompute` viram [`PendingCollider`] (o
/// resolver tem timeout com fallback de AABB, então a instância sempre
/// termina com colisão).
///
/// `moving` (criaturas de `DynamicSpawner`) insere também um
/// `RigidBody::KinematicPositionBased`: um collider sem corpo é FIXED no
/// Rapier e não acompanha o Transform que a IA/scripts escrevem — ficava
/// fantasma no sítio de nascimento ("lobo-parede"). Com o corpo kinematic o
/// plugin sincroniza (`set_next_kinematic_position` em `Changed<
/// GlobalTransform>`) e o collider — próprio, filho ou pendente, ligado ao
/// ancestral com corpo por `collider_offset` — segue a criatura. Mesmo
/// caminho do herói (`PlayerGltf` em `recipes/spawn.rs`).
fn apply_template_collider(
    entity: &mut bevy::ecs::system::EntityCommands,
    shape: &crate::physics::ColliderShape,
    handle: &Option<bevy::asset::Handle<bevy::gltf::Gltf>>,
    moving: bool,
) {
    if moving {
        if let Some((body, gravity)) =
            crate::physics::body_bundle(crate::physics::BodyKind::Kinematic, None)
        {
            entity.insert((body, gravity));
        }
    }
    match shape {
        crate::physics::ColliderShape::None => {}
        crate::physics::ColliderShape::Box { .. } => {
            if let Some((collider, offset)) = crate::physics::immediate_collider(shape) {
                if offset.translation == Vec3::ZERO {
                    entity.insert(collider);
                } else {
                    let parent = entity.id();
                    entity.commands().spawn((
                        Name::new("collider"),
                        collider,
                        offset,
                        bevy::ecs::hierarchy::ChildOf(parent),
                    ));
                }
            }
        }
        _ => {
            entity.insert(crate::physics::PendingCollider {
                shape: shape.clone(),
                gltf: handle.clone(),
                age: 0.0,
            });
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub fn instantiate_spawn_groups(
    mut commands: Commands,
    gltfs: Res<Assets<Gltf>>,
    gltf_meshes: Res<Assets<GltfMesh>>,
    meshes: Res<Assets<Mesh>>,
    server: Res<AssetServer>,
    runtime: Option<Res<TerrainRuntime>>,
    cliffs: Option<Res<CliffMask>>,
    time: Res<Time>,
    mut pending: Option<ResMut<PendingSpawnGroups>>,
) {
    let Some(pending) = pending.as_mut() else {
        return;
    };
    let Some(runtime) = runtime else {
        return; // terrain bootstrap has not published the carved world yet
    };
    // Split-borrow: cada campo tem um caminho de mutação só — grupos no gate
    // e no spawn, occupancy apenas no passe de colocação.
    let PendingSpawnGroups {
        groups,
        exclusions,
        occupancy,
        age,
    } = &mut **pending;

    // Passe 1 — readiness: só computa colocações quando TODOS os grupos
    // resolveram (ou falharam). A ocupação é partilhada e computar um grupo
    // no frame em que os seus assets chegam tornaria a floresta dependente
    // da ordem de load — a promessa é "mesma seed, mesmo mundo".
    *age += time.delta_secs();
    let mut waiting = false;
    for group in groups.iter_mut() {
        if group.done {
            continue;
        }
        if group.spec.template_urls.is_empty() {
            // Sem template glTF: já avisado no parse. O grupo não tem
            // handles/scenes — cair em baixo seria panic (len() - 1 com
            // vec vazio). Skip é o comportamento prometido pelo warning.
            group.done = true;
            group.failed = true;
            continue;
        }
        let load_states: Vec<Option<LoadState>> = group
            .handles
            .iter()
            .map(|handle| server.get_load_state(handle))
            .collect();
        if load_states
            .iter()
            .any(|state| matches!(state, Some(LoadState::Failed(_))))
        {
            bevy::log::warn!(
                "spawner template failed to load — group skipped ({})",
                group
                    .spec
                    .template_urls
                    .first()
                    .map(String::as_str)
                    .unwrap_or("?")
            );
            group.done = true;
            group.failed = true;
            continue;
        }
        if !load_states
            .iter()
            .all(|state| matches!(state, Some(LoadState::Loaded)))
        {
            waiting = true;
            continue;
        }
    }
    if waiting {
        if *age < SPAWN_WAIT_TIMEOUT {
            return;
        }
        // Timeout: desiste dos templates presos e spawna o resto — um asset
        // que nunca chega não pode pendurar o mundo para sempre.
        for group in groups.iter_mut() {
            if group.done {
                continue;
            }
            let ready = group
                .handles
                .iter()
                .all(|handle| matches!(server.get_load_state(handle), Some(LoadState::Loaded)));
            if !ready {
                bevy::log::warn!(
                    "spawner template still loading after {:.0} s — group skipped ({})",
                    *age,
                    group
                        .spec
                        .template_urls
                        .first()
                        .map(String::as_str)
                        .unwrap_or("?")
                );
                group.done = true;
                group.failed = true;
            }
        }
    }

    // Passe 2 — colocações: exclusões primeiro (sempre honradas, mesmo com
    // `avoid-overlaps="0"`), depois os grupos pela ordem do XML.
    for exclusion in exclusions.iter() {
        occupancy.register(exclusion.center.x, exclusion.center.y, exclusion.radius);
    }
    for group in groups.iter_mut() {
        if group.done {
            continue;
        }
        let scenes: Vec<Option<bevy::asset::Handle<bevy::world_serialization::WorldAsset>>> = group
            .handles
            .iter()
            .map(|handle| {
                gltfs
                    .get(handle)
                    .and_then(|gltf| gltf.default_scene.clone())
            })
            .collect();
        if scenes.iter().any(Option::is_none) {
            bevy::log::warn!("spawner template has no default scene — group skipped");
            group.done = true;
            continue;
        }
        // Footprint automático: `footprint-radius` ausente (0) usa a
        // meia-largura XZ do AABB do GLB — o teste contra a ocupação fica
        // honesto sem o mundo autorar raios à mão. Fallback 0.8 m (VibeGame).
        if group.spec.avoid_overlaps && group.spec.footprint_radius <= 0.0 {
            let auto = group
                .handles
                .iter()
                .filter_map(|handle| {
                    template_footprint_radius(&gltfs, &gltf_meshes, &meshes, handle)
                })
                .fold(None::<f32>, |acc, r| Some(acc.map_or(r, |max| max.max(r))));
            group.spec.footprint_radius = auto.unwrap_or(0.8);
        }
        // Criaturas ficam verticais (VibeGame, perfil `creature`): movem-se
        // depois do spawn — um tilt de nascimento ficava colado ao corpo.
        if group.dynamic {
            group.spec.align_to_terrain = false;
        }
        let near_radius = group.spec.near_water_radius;
        // Margem de cliff em metros reais: a máscara é amostrada com folga =
        // margem autoral + meia-largura da pegada na maior escala possível —
        // "a 2 m do cliff" conta a partir do SÍTIO onde o prop vai caber.
        let cliff_clearance = group.spec.cliff_margin.max(0.0)
            + group.spec.footprint_radius.max(0.0)
                * group.spec.scale_min.max(group.spec.scale_max).max(0.0);
        let mut sample = |x: f32, z: f32| {
            // Altura da SUPERFÍCIE renderizada (lattice LOD0): entre vértices
            // o mesh só desenha cordas lineares, e a amostra analítica
            // flutuava acima delas nas cristas.
            let height = runtime.sample_mesh_surface(x, z);
            TerrainSample {
                height,
                // Gradiente do CAMPO voxel no ponto (fiel a cliffs/caves/arch
                // — a matriz 3×3 da grelha crua era cega aos mods e inclinava
                // props para paredes que a grelha nem via).
                normal: runtime.voxel.gradient(
                    &runtime.base(),
                    bevy::math::Vec3::new(x, height, z),
                    0.5,
                ),
                water: runtime.in_water(x, z),
                // Lâmina de água no ponto (`in-water` assenta à superfície).
                water_surface: runtime.water_surface_at(x, z),
                // A shoreline test: banda EXATA em torno da linha de água
                // (distance_to_waterline, a mesma métrica do splatter) — as
                // 4 sondas antigas liam o carve radius (que passa da lâmina
                // vários metros) e punham "margem" em encosta seca.
                near_water: runtime.water.iter().any(|body| {
                    body.distance_to_waterline(bevy::math::Vec2::new(x, z))
                        .abs()
                        <= near_radius
                }),
                road: runtime.on_road(x, z),
                cliff: cliffs.as_deref().is_some_and(|mask| {
                    mask.is_cliff_within(bevy::math::Vec2::new(x, z), cliff_clearance)
                }),
                // Laje fina por cima de vazio (banda de arco, brow de overhang
                // raso): o gate rejeita — raízes não nascem em pedra suspensa.
                // Em mundo flat é sempre false, sem custo.
                roof: runtime.has_thin_roof(x, z),
            }
        };
        // As exclusões são lidas por referência: clonar aqui (por grupo, por
        // frame de loading) era lixo puro — a Vec pode ter centenas de
        // entradas.
        // Uma vez por grupo: o raio e a política de sombra são do template,
        // não da instância.
        let cull = crate::render_lod::CullDistance::new(group.cull_radius());
        let cast_shadows = group.spec.cast_shadows;
        // Ladder de LOD por template. Um tier só entra quando o seu glTF já
        // resolveu — um `lod1-url` ainda a carregar degrada para a ladder
        // curta em vez de spawnar uma cena vazia.
        let lod_scenes: Vec<Option<crate::render_lod::MeshLod>> = (0..scenes.len())
            .map(|index| {
                let (lod1, lod2, near, mid) = group.lod_handles.get(index)?;
                let mut tiers = vec![scenes[index].clone()?];
                // O handle de Gltf de cada tier acompanha a cena: os clips de
                // animação são assets por-Gltf, e o re-bind pós-swap
                // (`animation::rearm_after_scene_swap`) precisa do do tier
                // residente.
                let mut gltf_tiers = vec![group.handles[index].clone()];
                for lod in [lod1, lod2] {
                    let Some(gltf) = lod.clone() else {
                        continue;
                    };
                    let Some(scene) = gltfs.get(&gltf).and_then(|gltf| gltf.default_scene.clone())
                    else {
                        continue; // ainda a carregar — ladder curta
                    };
                    tiers.push(scene);
                    gltf_tiers.push(gltf);
                }
                (tiers.len() > 1).then(|| crate::render_lod::MeshLod {
                    tiers,
                    gltf_tiers,
                    near: *near,
                    mid: *mid,
                    current: 0,
                    no_shadows: !cast_shadows,
                })
            })
            .collect();
        let (instances, stats) = compute_placements(&group.spec, occupancy, &mut sample);
        // Uma linha por grupo com o breakdown de rejeições — QA no
        // `viber debug logs`; grupos limpos ficam em debug.
        let line = format!(
            "spawn '{}': {} placed / {} attempts (rejected {} — slope {}, ring-slope {}, ledge {}, cliff {}, roof {}, water {}, road {}, occupancy {}, distance {})",
            group
                .spec
                .template_urls
                .first()
                .map(String::as_str)
                .unwrap_or("?"),
            stats.placed,
            stats.attempts,
            stats.rejected(),
            stats.rejected_slope_center,
            stats.rejected_slope_ring,
            stats.rejected_ledge,
            stats.rejected_cliff,
            stats.rejected_roof,
            stats.rejected_water,
            stats.rejected_road,
            stats.rejected_occupancy,
            stats.rejected_max_distance,
        );
        if stats.rejected() > 0 {
            bevy::log::info!("{line}");
        } else {
            bevy::log::debug!("{line}");
        }
        for instance in &instances {
            spawn_instance(&mut commands, group, instance, &scenes, &lod_scenes, cull);
        }
        group.done = true;
    }
    commands.remove_resource::<PendingSpawnGroups>();
}

/// Spawna UMA instância do grupo com a sua cena/ladder/colisor/destrutível.
///
/// Os quatro casos (dinâmico±script, estático±script) eram um bloco copiado —
/// a variação vive agora só no `match`: dinâmico+script → Luau + Health +
/// AnimatedScene; dinâmico sem script → FSM Rust + AnimatedScene; estático
/// com script → Luau (o congelamento por raio mantém 380 árvores baratas);
/// estático puro → só a cena.
fn spawn_instance(
    commands: &mut Commands,
    group: &SpawnGroupState,
    instance: &PlacedInstance,
    scenes: &[Option<bevy::asset::Handle<bevy::world_serialization::WorldAsset>>],
    lod_scenes: &[Option<crate::render_lod::MeshLod>],
    cull: crate::render_lod::CullDistance,
) {
    let mut transform = Transform::from_translation(instance.position);
    transform.rotation = instance.rotation;
    transform.scale = instance.scale;
    let Some(scene) = scenes[instance.template_index.min(scenes.len() - 1)].clone() else {
        return;
    };
    let mut entity = commands.spawn((transform, Visibility::Inherited, WorldAssetRoot(scene)));
    // LOD de render: o mesmo raio para todas as instâncias do grupo (ver
    // `render_lod`).
    entity.insert(cull);
    if let Some(lod) = lod_scenes
        .get(
            instance
                .template_index
                .min(lod_scenes.len().saturating_sub(1)),
        )
        .and_then(Option::as_ref)
    {
        entity.insert(lod.clone());
    }
    if !group.spec.cast_shadows {
        entity.insert(crate::render_lod::NoShadowSubtree);
    }
    match (group.dynamic, group.template_script.as_ref()) {
        // Com script no template o comportamento é do Luau (a engine só
        // provê os blocos).
        (true, Some(script)) => {
            entity.insert((
                crate::luau::LuaScriptRef {
                    path: script.clone(),
                },
                crate::luau::ScriptActivation {
                    radius: group.activation_radius,
                },
                // Vitals para o combate (dano/morte).
                crate::vitals::Health::default(),
                // Sem isto as criaturas nunca ligavam o AnimationPlayer e
                // patrulhavam em bind pose.
                crate::animation::AnimatedScene {
                    gltf: group.handles[instance.template_index.min(group.handles.len() - 1)]
                        .clone(),
                },
            ));
        }
        (true, None) => {
            entity.insert((
                crate::ai::EnemyCreature::default(),
                crate::animation::AnimatedScene {
                    gltf: group.handles[instance.template_index.min(group.handles.len() - 1)]
                        .clone(),
                },
            ));
        }
        // Estático com script (ex.: árvores/rochas colhíveis).
        (false, Some(script)) => {
            entity.insert((
                crate::luau::LuaScriptRef {
                    path: script.clone(),
                },
                crate::luau::ScriptActivation {
                    radius: group.activation_radius,
                },
            ));
        }
        (false, None) => {}
    }
    if let Some(shape) = &group.template_collider {
        apply_template_collider(&mut entity, shape, &group.collider_handle, group.dynamic);
    }
    // Destrutível é coisa de prop estático — criaturas morrem por combate.
    if !group.dynamic {
        if let Some(destructible) = &group.template_destructible {
            entity.insert(crate::harvest::Destructible::from_spec(destructible));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::recipes::StaticSpawnerSpec;

    fn spec() -> StaticSpawnerSpec {
        StaticSpawnerSpec {
            seed: 42,
            count: 10,
            region_min: [-100.0, 0.0, -100.0],
            region_max: [100.0, 0.0, 100.0],
            cluster_count: 0,
            cluster_radius: 0.0,
            footprint_radius: 0.0,
            avoid_overlaps: false,
            max_slope_deg: 90.0,
            avoid_water: false,
            in_water: false,
            near_water: false,
            near_water_radius: 4.0,
            avoid_road: false,
            avoid_cliff: true,
            cliff_margin: 2.0,
            align_to_terrain: true,
            scale_min: 1.0,
            scale_max: 1.0,
            scale_axis_min: 1.0,
            scale_axis_max: 1.0,
            random_yaw: false,
            max_distance: 0.0,
            template_urls: vec!["/assets/meshes/a.glb".into()],
            template_script: None,
            template_collider: None,
            template_destructible: None,
            activation_radius: 45.0,
            template_lods: Vec::new(),
            cull_distance: crate::render_lod::DEFAULT_STATIC_CULL,
            cast_shadows: true,
            base_y_offset: 0.0,
            max_slope_attempts: 32,
            density_per_km2: 0.0,
            max_instances: 0,
            fixed_candidates: Vec::new(),
        }
    }

    /// `compute_placements` descartando as stats — a maioria dos testes só
    /// quer as instâncias.
    fn place(
        spec: &StaticSpawnerSpec,
        occupancy: &mut SpawnOccupancy,
        sample: &mut dyn FnMut(f32, f32) -> TerrainSample,
    ) -> Vec<PlacedInstance> {
        compute_placements(spec, occupancy, sample).0
    }

    fn flat(_x: f32, _z: f32) -> TerrainSample {
        TerrainSample {
            height: 3.0,
            normal: Vec3::Y,
            water: false,
            water_surface: None,
            near_water: false,
            road: false,
            cliff: false,
            roof: false,
        }
    }

    #[test]
    fn test_rng_is_deterministic_per_seed() {
        let mut a = Rng::new(7);
        let mut b = Rng::new(7);
        for _ in 0..8 {
            assert_eq!(a.next_u64(), b.next_u64());
        }
        let mut c = Rng::new(8);
        assert_ne!(a.next_u64(), c.next_u64());
    }

    #[test]
    fn test_rng_range_stays_in_bounds() {
        let mut rng = Rng::new(1);
        for _ in 0..200 {
            let v = rng.range(2.5, 7.5);
            assert!((2.5..=7.5).contains(&v));
        }
    }

    #[test]
    fn test_placements_respect_count_region_and_height() {
        let out = place(&spec(), &mut SpawnOccupancy::new(), &mut flat);
        assert_eq!(out.len(), 10);
        for instance in &out {
            assert!(instance.position.x >= -100.0 && instance.position.x <= 100.0);
            assert!(instance.position.z >= -100.0 && instance.position.z <= 100.0);
            assert_eq!(instance.position.y, 3.0, "align_to_terrain uses sampler");
            assert_eq!(instance.yaw_deg, 0.0, "no random yaw by default");
            assert_eq!(instance.scale, Vec3::ONE);
            assert_eq!(instance.template_index, 0);
        }
    }

    #[test]
    fn test_placements_are_deterministic() {
        let a = place(&spec(), &mut SpawnOccupancy::new(), &mut flat);
        let b = place(&spec(), &mut SpawnOccupancy::new(), &mut flat);
        assert_eq!(a, b);
    }

    #[test]
    fn test_slope_filter_rejects_steep_terrain() {
        let mut s = spec();
        s.max_slope_deg = 40.0;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut |_: f32, _: f32| {
            TerrainSample {
                height: 0.0,
                normal: Vec3::new(1.0, 0.2, 0.0),
                water: false,
                water_surface: None,
                near_water: false,
                road: false,
                cliff: false,
                roof: false,
            }
        });
        assert!(out.is_empty(), "all candidates exceed the 40° slope limit");
    }

    #[test]
    fn test_water_filter_rejects_water() {
        let mut s = spec();
        s.avoid_water = true;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut |_: f32, _: f32| {
            TerrainSample {
                height: -1.0,
                normal: Vec3::Y,
                water: true,
                water_surface: None,
                near_water: false,
                road: false,
                cliff: false,
                roof: false,
            }
        });
        assert!(out.is_empty());
    }

    #[test]
    fn test_overlap_filter_enforces_footprint() {
        let mut s = spec();
        s.count = 5;
        s.avoid_overlaps = true;
        s.footprint_radius = 5.0;
        s.region_min = [0.0, 0.0, 0.0];
        s.region_max = [1.0, 0.0, 1.0]; // everything within 5 m of everything
        let out = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        assert_eq!(out.len(), 1, "only the first candidate fits the footprint");
    }

    #[test]
    fn test_spawn_exclusion_rejects_circle() {
        let mut s = spec();
        s.count = 20;
        // flat terrain everywhere; the whole region is otherwise valid
        let mut no_excl = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        // As exclusões são pré-registradas na ocupação partilhada — mesmo
        // caminho do gate runtime (vale com `avoid-overlaps="0"`).
        let mut occupancy = SpawnOccupancy::new();
        occupancy.register(0.0, 0.0, 150.0);
        let with_excl = place(&s, &mut occupancy, &mut flat);
        assert!(!no_excl.is_empty());
        // every instance outside the exclusion circle
        for instance in &with_excl {
            let d = bevy::math::Vec2::new(instance.position.x, instance.position.z).length();
            assert!(
                d >= 150.0,
                "instance at {d:.1} m inside the 150 m exclusion"
            );
        }
        no_excl.clear();
    }

    #[test]
    fn test_vegetation_spec_to_spawner_spec_caps_count() {
        let mut v = crate::recipes::VegetationSpec {
            meshes: vec!["/assets/meshes/vegetation/grass.glb".into()],
            density_per_km2: 100_000.0,
            seed: 601,
            region_min: [-190.0, 0.0, 116.0],
            region_max: [190.0, 0.0, 380.0],
            scale_min: 0.9,
            scale_max: 1.5,
            scale_axis_min: 0.9,
            scale_axis_max: 1.1,
            max_slope_deg: 26.0,
            avoid_water: true,
            avoid_road: false,
            avoid_cliff: true,
            cliff_margin: 2.0,
            avoid_overlaps: true,
            random_yaw: true,
            max_distance: 110.0,
            cluster_count: 128,
            cluster_radius: 8.8,
            max_instances: 800,
            cull_distance: crate::render_lod::DEFAULT_VEGETATION_CULL,
            cast_shadows: false,
        };
        // 380×264 m = 0.1 km² × 100k = ~10 028 → capped at 800
        assert_eq!(v.instance_count(), 800);
        let group = v.to_spawner_spec();
        assert_eq!(group.count, 800);
        assert_eq!(group.template_urls.len(), 1);
        assert!(group.avoid_water && group.random_yaw);
        // small density: uncapped, rounded up
        v.density_per_km2 = 100.0;
        v.max_instances = 800;
        assert_eq!(v.instance_count(), 11);
    }

    #[test]
    fn test_random_yaw_and_scale_jitter() {
        let mut s = spec();
        s.random_yaw = true;
        s.scale_min = 0.8;
        s.scale_max = 1.4;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        let yaws: Vec<f32> = out.iter().map(|i| i.yaw_deg).collect();
        assert!(yaws.iter().any(|y| *y > 0.0), "yaws should vary: {yaws:?}");
        for instance in &out {
            assert!((0.8..=1.4).contains(&instance.scale.x));
        }
    }

    /// Water and road rules pick opposite sides of the same query, so each one
    /// is checked against a field that is half water / half road.
    #[test]
    fn test_placement_water_and_road_rules() {
        let base = || {
            let mut s = spec();
            s.count = 40;
            s.region_min = [-50.0, 0.0, -50.0];
            s.region_max = [50.0, 0.0, 50.0];
            s.avoid_overlaps = false;
            s
        };
        // West half is water, east half is road.
        let mut field = |x: f32, _z: f32| TerrainSample {
            height: 0.0,
            normal: Vec3::Y,
            water: x < 0.0,
            water_surface: None,
            near_water: (0.0..6.0).contains(&x),
            road: x > 0.0,
            cliff: false,
            roof: false,
        };

        let mut s = base();
        s.avoid_water = true;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut field);
        assert!(!out.is_empty(), "some candidates land on dry ground");
        assert!(
            out.iter().all(|i| i.position.x >= 0.0),
            "avoid-water keeps every instance out of the water half"
        );

        let mut s = base();
        s.in_water = true;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut field);
        assert!(!out.is_empty());
        assert!(
            out.iter().all(|i| i.position.x < 0.0),
            "in-water places only inside the water half"
        );

        let mut s = base();
        s.avoid_road = true;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut field);
        assert!(!out.is_empty());
        assert!(
            out.iter().all(|i| i.position.x <= 0.0),
            "avoid-road keeps every instance off the road half"
        );

        let mut s = base();
        s.near_water = true;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut field);
        assert!(!out.is_empty());
        assert!(
            out.iter().all(|i| i.position.x < 6.0),
            "near-water places on the bank (or in the water), not inland"
        );
    }

    /// `random-yaw` is what stops a stand of identical trees reading as clones.
    #[test]
    fn test_random_yaw_spreads_headings() {
        let mut s = spec();
        s.count = 24;
        s.region_min = [-50.0, 0.0, -50.0];
        s.region_max = [50.0, 0.0, 50.0];
        s.avoid_overlaps = false;
        s.random_yaw = true;
        let mut flat = |_: f32, _: f32| TerrainSample {
            height: 0.0,
            normal: Vec3::Y,
            water: false,
            water_surface: None,
            near_water: false,
            road: false,
            cliff: false,
            roof: false,
        };
        let out = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        let yaws: Vec<f32> = out.iter().map(|i| i.yaw_deg).collect();
        assert!(yaws.len() > 4);
        let distinct = yaws.iter().filter(|y| (**y - yaws[0]).abs() > 1.0).count();
        assert!(distinct > 0, "headings vary: {yaws:?}");
        assert!(
            yaws.iter().all(|y| (0.0..=360.0).contains(y)),
            "headings stay in range"
        );
    }

    #[test]
    fn test_occupancy_disc_clearance_and_cells() {
        assert!(
            SpawnOccupancy::new().is_free(0.0, 0.0, 10.0),
            "empty is free"
        );
        let mut occ = SpawnOccupancy::new();
        occ.register(0.0, 0.0, 2.0);
        assert!(!occ.is_free(0.0, 0.0, 0.0), "inside the disc");
        // A 2.5 m ainda encosta (2 + 0.6 de folga); além disso, livre.
        assert!(!occ.is_free(2.5, 0.0, 0.0), "clearance band is enforced");
        assert!(occ.is_free(2.7, 0.0, 0.0));
        // O raio do candidato soma ao teste.
        assert!(occ.is_free(4.5, 0.0, 0.5));
        assert!(!occ.is_free(3.0, 0.0, 0.5));
        // Disco registado grande atravessa células: a busca pelo alcance do
        // PRÓPRIO raio + folga tem de o encontrar na mesma.
        let mut big = SpawnOccupancy::new();
        big.register(0.0, 0.0, 50.0);
        assert!(!big.is_free(10.0, 0.0, 0.0));
        assert!(!big.is_free(-48.0, 0.0, 0.0));
        assert!(big.is_free(51.5, 0.0, 0.0));
    }

    #[test]
    fn test_occupancy_giant_radius_rejected_without_world_sized_buckets() {
        // Exclusão "mundial": o disco vai para a lista `large` (sem iterar
        // milhões de células) e o teste geométrico é idêntico ao das células.
        let mut occ = SpawnOccupancy::new();
        occ.register(0.0, 0.0, 100_000.0);
        assert!(!occ.is_free(0.0, 0.0, 0.0), "inside the giant exclusion");
        assert!(
            !occ.is_free(50_000.0, 0.0, 1.0),
            "50 km out is still inside"
        );
        // Raio + folga do disco: além disso (100000 + 1 + 0.6), livre.
        assert!(occ.is_free(100_002.0, 0.0, 1.0));
    }

    /// `footprint-radius` × escala: o teste usa a escala máxima (conservador)
    /// e o registo a escala real — pares aceites ficam a ≥ 2r + folga.
    #[test]
    fn test_footprint_keeps_breathing_room_between_instances() {
        let mut s = spec();
        s.count = 60;
        s.avoid_overlaps = true;
        s.footprint_radius = 2.0;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        assert!(out.len() > 1);
        let clearance = 2.0 * 2.0 + SPAWN_CLEARANCE;
        for (i, a) in out.iter().enumerate() {
            for b in &out[i + 1..] {
                let d = a.position.distance(b.position);
                assert!(
                    d >= clearance - 1e-3,
                    "instances {d:.2} m apart (< {clearance})"
                );
            }
        }
    }

    #[test]
    fn test_terrain_align_rotation() {
        // Plano: só yaw.
        let q = terrain_align_rotation(Vec3::Y, 1.23, 0.0);
        assert!(q.angle_between(Quat::from_rotation_y(1.23)) < 1e-2);
        // Encosta 45° a subir para −x (normal +x): o tronco inclina-se para a
        // linha de queda (+x), entre 5° e 60°, e o yaw não mexe no eixo.
        let normal = Vec3::new(1.0, 1.0, 0.0).normalize();
        let slope = normal.y.acos();
        let q = terrain_align_rotation(normal, 2.0, slope);
        let up = q * Vec3::Y;
        let tilt_deg = up.angle_between(Vec3::Y).to_degrees();
        assert!(tilt_deg > 5.0 && tilt_deg <= 60.0, "tilt {tilt_deg}");
        assert!(up.x > 0.0, "leans downhill: {up}");
        // Falésia: clamp a 60° — nunca deita o tronco.
        let cliff = Vec3::new(0.98, 0.05, 0.0).normalize();
        let q = terrain_align_rotation(cliff, 0.0, cliff.y.acos());
        let tilt = (q * Vec3::Y).angle_between(Vec3::Y).to_degrees();
        assert!((tilt - 60.0).abs() < 0.5, "clamped tilt: {tilt}");
    }

    /// `align-to-terrain` com declive: a rotação da instância é o tilt parcial
    /// (não só yaw), e `base-y-offset` soma ao assentamento.
    #[test]
    fn test_aligned_instances_tilt_and_base_offset_lifts() {
        let mut s = spec();
        s.count = 4;
        let mut slope = |_: f32, _: f32| TerrainSample {
            height: 3.0,
            normal: Vec3::new(0.6, 0.8, 0.0).normalize(),
            water: false,
            water_surface: None,
            near_water: false,
            road: false,
            cliff: false,
            roof: false,
        };
        let out = place(&s, &mut SpawnOccupancy::new(), &mut slope);
        assert!(out.iter().any(|i| {
            let tilt = (i.rotation * Vec3::Y).angle_between(Vec3::Y).to_degrees();
            tilt > 1.0
        }));
        s.base_y_offset = 2.5;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut slope);
        assert!(out.iter().all(|i| (i.position.y - 5.5).abs() < 1e-4));
    }

    #[test]
    fn test_density_per_km2_count_mode_and_cap() {
        let mut s = spec();
        s.count = 0;
        s.region_min = [-100.0, 0.0, -100.0];
        s.region_max = [100.0, 0.0, 100.0]; // 200×200 m = 0.04 km²
        s.density_per_km2 = 10_000.0; // 0.04 × 10 000 = 400
        let out = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        assert_eq!(out.len(), 400);
        // Teto absoluto: densidade alta com `max-instances` baixo.
        s.density_per_km2 = 1_000_000.0;
        s.max_instances = 50;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        assert_eq!(out.len(), 50, "max-instances caps density runs");
        // `count` explícito ganha sempre ao modo densidade.
        s.count = 7;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut flat);
        assert_eq!(out.len(), 7);
    }

    /// Região impossível: cada instância queima as suas tentativas e é
    /// omitida — saída vazia sem loop infinito.
    #[test]
    fn test_attempts_exhaustion_omits_instances() {
        let mut s = spec();
        s.count = 12;
        s.avoid_water = true;
        s.max_slope_attempts = 4;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut |_: f32, _: f32| {
            TerrainSample {
                height: -1.0,
                normal: Vec3::Y,
                water: true,
                water_surface: None,
                near_water: false,
                road: false,
                cliff: false,
                roof: false,
            }
        });
        assert!(out.is_empty(), "impossible region yields nothing");
    }

    /// Exceção à exclusão: `in-water` assenta À SUPERFÍCIE da lâmina (nunca
    /// no fundo escavado), fica vertical e ignora o declive do leito —
    /// vitórias-régias em lagoas fundas.
    #[test]
    fn test_in_water_anchors_to_surface_upright() {
        let mut s = spec();
        s.count = 6;
        s.in_water = true;
        s.align_to_terrain = true;
        s.max_slope_deg = 40.0;
        let mut lake = |_: f32, _: f32| TerrainSample {
            height: -2.0,                                 // fundo escavado da lagoa
            normal: Vec3::new(0.6, 0.8, 0.0).normalize(), // margem íngreme
            water: true,
            water_surface: Some(5.0), // lâmina de água
            near_water: false,
            road: false,
            cliff: false,
            roof: false,
        };
        let out = place(&s, &mut SpawnOccupancy::new(), &mut lake);
        assert_eq!(out.len(), 6, "open water passes regardless of bed slope");
        for instance in &out {
            assert!(
                (instance.position.y - 5.0).abs() < 1e-4,
                "on the blade, not the bed: {}",
                instance.position.y
            );
            let tilt = (instance.rotation * Vec3::Y)
                .angle_between(Vec3::Y)
                .to_degrees();
            assert!(tilt < 1.0, "upright on water: {tilt}");
        }
    }

    /// `in-water` é a exceção da `avoid-water`: só nasce EM água — candidato
    /// em seco é sempre rejeitado.
    #[test]
    fn test_in_water_rejects_dry_ground() {
        let mut s = spec();
        s.count = 5;
        s.in_water = true;
        let out = place(&s, &mut SpawnOccupancy::new(), &mut |_: f32, _: f32| {
            TerrainSample {
                height: 3.0,
                normal: Vec3::Y,
                water: false,
                water_surface: None,
                near_water: true, // margem não chega — tem de ser DENTRO
                road: false,
                cliff: false,
                roof: false,
            }
        });
        assert!(out.is_empty(), "dry ground never receives in-water spawns");
    }

    /// Cliff: terreno de falésia é zona de não-spawn por omissão;
    /// `avoid-cliff="0"` restaura. A margem real em metros vive na
    /// `CliffMask::is_cliff_within` (testada em cliffs.rs) — aqui o gate.
    #[test]
    fn test_cliff_gate_rejects_and_respects_opt_out() {
        let mut s = spec();
        let mut cliffy = |_: f32, _: f32| TerrainSample {
            height: 3.0,
            normal: Vec3::Y,
            water: false,
            water_surface: None,
            near_water: false,
            road: false,
            cliff: true,
            roof: false,
        };
        let (out, stats) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut cliffy);
        assert!(out.is_empty(), "cliff terrain is a no-spawn zone");
        assert_eq!(
            stats.rejected_cliff, stats.attempts,
            "every attempt hits the cliff gate"
        );
        s.avoid_cliff = false;
        let (out, stats) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut cliffy);
        assert_eq!(out.len(), 10, "opt-out restores placement");
        assert_eq!(stats.rejected_cliff, 0);
        // in-water é a exceção: planta à lâmina mesmo com cliff à volta.
        let mut s = spec();
        s.in_water = true;
        let mut lily = |_: f32, _: f32| TerrainSample {
            height: -2.0,
            normal: Vec3::Y,
            water: true,
            water_surface: Some(5.0),
            near_water: false,
            road: false,
            cliff: true,
            roof: false,
        };
        let (out, _) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut lily);
        assert_eq!(out.len(), 10, "in-water ignores the cliff gate");
    }

    /// Anel da pegada pela matriz 3×3: pé plano com anel íngreme rejeita —
    /// a amostra central sozinha passava (o bug que o anel existe p/ matar).
    #[test]
    fn test_footprint_ring_rejects_steep_vicinity() {
        let mut s = spec();
        s.max_slope_deg = 45.0;
        s.footprint_radius = 2.0;
        // Região inteira dentro do disco plano: só o anel pode ser íngreme.
        s.region_min = [-1.0, 0.0, -1.0];
        s.region_max = [1.0, 0.0, 1.0];
        // Plano no centro; parede de 60° a partir de 1,5 m do centro (o anel
        // a 2 m cai dentro).
        let mut mixed = |x: f32, z: f32| {
            let steep = (x * x + z * z).sqrt() > 1.5;
            TerrainSample {
                height: 0.0,
                normal: if steep {
                    Vec3::new(0.87, 0.5, 0.0).normalize()
                } else {
                    Vec3::Y
                },
                water: false,
                water_surface: None,
                near_water: false,
                road: false,
                cliff: false,
                roof: false,
            }
        };
        let (out, stats) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut mixed);
        assert!(out.is_empty(), "the ring owns the gate, not the foot");
        assert!(
            stats.rejected_slope_ring > 0,
            "rejection reason is the ring"
        );
        // Com o limite frouxo, centro e anel passam — prova de que o centro
        // nunca foi o bloqueio.
        s.max_slope_deg = 89.0;
        let (out, stats) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut mixed);
        assert_eq!(out.len(), 10);
        assert_eq!(stats.rejected_slope_ring, 0);
    }

    /// Degrau na pegada: declive médio baixo (normais planas) mas spread de
    /// altura > SPAWN_LEDGE_DROP — o gate de declive não apanha, o de degrau sim.
    #[test]
    fn test_ledge_step_rejects_height_spread() {
        let mut s = spec();
        s.max_slope_deg = 89.0;
        s.footprint_radius = 2.0;
        // Região inteira a menos de um anel do degrau em x=0: TODO candidato
        // monta o passo (altura 0 → 3 m, spread > SPAWN_LEDGE_DROP).
        s.region_min = [-1.0, 0.0, -1.0];
        s.region_max = [1.0, 0.0, 1.0];
        let mut step = |x: f32, _: f32| TerrainSample {
            height: if x >= 0.0 { 3.0 } else { 0.0 },
            normal: Vec3::Y,
            water: false,
            water_surface: None,
            near_water: false,
            road: false,
            cliff: false,
            roof: false,
        };
        let (out, stats) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut step);
        assert!(out.is_empty(), "no prop straddles a 3 m step");
        assert_eq!(stats.rejected_ledge, stats.attempts);
    }

    /// Stats: motivos contam exatamente um por candidato rejeitado e o
    /// total bate com tentativas − colocadas.
    #[test]
    fn test_placement_stats_count_reasons() {
        let mut s = spec();
        s.count = 8;
        s.avoid_water = true;
        let mut lake = |_: f32, _: f32| TerrainSample {
            height: -1.0,
            normal: Vec3::Y,
            water: true,
            water_surface: None,
            near_water: false,
            road: false,
            cliff: false,
            roof: false,
        };
        let (out, stats) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut lake);
        assert!(out.is_empty());
        assert_eq!(stats.placed, 0);
        assert_eq!(stats.rejected_water, stats.attempts);
        assert_eq!(stats.rejected(), stats.attempts, "one reason per rejection");
        // Em terreno limpo: todas colocadas, zero rejeições.
        let (out, stats) = compute_placements(&s, &mut SpawnOccupancy::new(), &mut flat);
        assert_eq!(out.len(), 8);
        assert_eq!(stats.placed, 8);
        assert_eq!(stats.rejected(), 0);
        assert!(stats.attempts >= 8);
    }
}
