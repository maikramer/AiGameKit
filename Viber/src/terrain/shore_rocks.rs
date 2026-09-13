//! Pedras de margem automáticas — `rocks="1"` no `<Lake>`/`<River>`.
//!
//! Caminha a linha de água de cada corpo (contorno harmónico do lago ou as
//! estações do rio) e devolve CANDIDATOS XZ determinísticos na banda de
//! margem: a maioria em seco (a faixa que a maré do splat pinta de areia),
//! uma fração deliberadamente dentro da água rasa — o clássico "pedras a
//! sair da água". As posições alimentam o pipeline de spawner como
//! `fixed_candidates` ([`crate::recipes::StaticSpawnerSpec`]), que aplica os
//! gates normais (ocupação partilhada, estradas, declive, align-to-terrain)
//! e a escolha de template/escala/yaw pelo RNG — mesma seed, mesmo mundo.
//!
//! Puro: sem ECS, sem assets — unit-testável headless (a entrega via
//! `collect_spawn_groups` é a única parte viva).

use bevy::math::Vec2;

use super::water::{CARVE_MARGIN, LakeShape, waterline_reach};

/// GLBs de pedra do pool, em ordem de "pretensão" (boulder > musgo > seixo).
/// Um é escolhido por instância pelo RNG do grupo de spawner.
pub const ROCK_TEMPLATES: [&str; 4] = [
    "/assets/meshes/props/rock_boulder_lod0.glb",
    "/assets/meshes/props/rock_mossy_lod0.glb",
    "/assets/meshes/swamp/moss_rock_lod0.glb",
    "/assets/meshes/farm/flint_rock_lod0.glb",
];

/// Ladders de LOD por template (mesmo índice de [`ROCK_TEMPLATES`]) —
/// `(lod1, lod2)`, thresholds próximos aos dos mundos do pool (50/120 m).
pub const ROCK_LODS: [(Option<&str>, Option<&str>); 4] = [
    (
        Some("/assets/meshes/props/rock_boulder_lod1.glb"),
        Some("/assets/meshes/props/rock_boulder_lod2.glb"),
    ),
    (
        Some("/assets/meshes/props/rock_mossy_lod1.glb"),
        Some("/assets/meshes/props/rock_mossy_lod2.glb"),
    ),
    (
        Some("/assets/meshes/swamp/moss_rock_lod1.glb"),
        Some("/assets/meshes/swamp/moss_rock_lod2.glb"),
    ),
    (
        Some("/assets/meshes/farm/flint_rock_lod1.glb"),
        Some("/assets/meshes/farm/flint_rock_lod2.glb"),
    ),
];

/// Parâmetros parseados (`rocks-density` / `rocks-scale-max`, seed derivada
/// da posição do corpo).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ShoreRocksSpec {
    /// Pedras por metro de linha de água (default 0.12 — ~1 pedra a cada
    /// 8 m; lagos pequenos ficam com 4–6, rios ganham fileiras ralas).
    pub density: f32,
    /// Escala máxima do GLB (mínimo fixo em 0.55 — nunca migalhas).
    pub scale_max: f32,
}

impl Default for ShoreRocksSpec {
    fn default() -> Self {
        Self {
            density: 0.12,
            scale_max: 1.4,
        }
    }
}

/// Teto por corpo — uma margem nunca vira um muro de pedra.
const MAX_PER_BODY: usize = 160;
/// Recuo/avanço radial em relação à linha de água (m): ~30% nascem dentro
/// da água rasa, o resto espalha-se até aqui em seco.
const DRY_SPREAD: f32 = 3.5;
const WET_DEPTH: f32 = 1.2;

/// SplitMix64 partilhado ([`crate::rng::Rng`]) semeado com XOR fixo — os
/// outputs têm de continuar idênticos aos do `Rng` local que vivia aqui.
use crate::rng::Rng;

/// Seed derivada da posição do corpo (estável entre runs, distinta por
/// corpo sobreposto).
fn body_seed(at: Vec2, index: usize) -> u64 {
    (at.x.to_bits() as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ ((at.y.to_bits() as u64).rotate_left(17))
        ^ (index as u64).wrapping_mul(0xBF58_476D_1CE4_E5B9)
}

/// Sal fixo que o `Rng` local antigo aplicava no `new` — preservado para o
/// seq shuffler das pedras não mudar.
const SHORE_SEED_SALT: u64 = 0x5EED_5EED_5EED_5EED;

/// Candidatos ao longo do contorno orgânico de um lago (espaçamento angular
/// uniforme + jitter em θ e raio — o contorno varia ±28%, o que já quebra
/// qualquer ritmo visível).
pub fn lake_candidates(
    at: Vec2,
    radius: f32,
    depth: f32,
    water_offset: f32,
    rocks: &ShoreRocksSpec,
    index: usize,
) -> Vec<Vec2> {
    if radius <= 0.0 {
        return Vec::new();
    }
    let shape = LakeShape::new(at);
    let reach = (waterline_reach(depth, water_offset) * CARVE_MARGIN).clamp(0.5, 1.6);
    // Perímetro do contorno amostrado — de onde vem a contagem.
    let samples = 96;
    let mut perimeter = 0.0;
    let mut prev: Option<Vec2> = None;
    for i in 0..=samples {
        let theta = i as f32 / samples as f32 * std::f32::consts::TAU;
        let r = shape.contour(radius, theta) * reach;
        let p = at + Vec2::new(theta.cos(), theta.sin()) * r;
        if let Some(q) = prev {
            perimeter += p.distance(q);
        }
        prev = Some(p);
    }
    let step = 1.0 / rocks.density.max(0.01);
    let count = ((perimeter / step) as usize).min(MAX_PER_BODY);
    if count == 0 {
        return Vec::new();
    }
    let mut rng = Rng::new(body_seed(at, index) ^ SHORE_SEED_SALT);
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        let theta = i as f32 / count as f32 * std::f32::consts::TAU
            + rng.range(-0.5, 0.5) / count as f32 * std::f32::consts::TAU;
        // Jitter radial: do lado húmido (dentro do espelho, água rasa) até
        // DRY_SPREAD metros em seco, em torno da linha de água real.
        let waterline = shape.contour(radius, theta) * reach;
        let radial = waterline + rng.range(-WET_DEPTH, DRY_SPREAD);
        out.push(at + Vec2::new(theta.cos(), theta.sin()) * radial);
    }
    out
}

/// Candidatos ao longo das estações de um rio (alternando margens).
pub fn river_candidates(
    stations: &[Vec2],
    width: f32,
    depth: f32,
    water_offset: f32,
    bank_width: f32,
    rocks: &ShoreRocksSpec,
    index: usize,
) -> Vec<Vec2> {
    if stations.len() < 2 || width <= 0.0 {
        return Vec::new();
    }
    // Meia-água real (o espelho acaba em t_wl·half) — as pedras nascem em
    // torno DESSA linha, não da borda do canal esculpido.
    let t_wl = waterline_reach(depth, water_offset).clamp(0.4, 1.0);
    let waterline = width * 0.5 * t_wl;
    let outer = width * 0.5 + bank_width.max(0.0) + 2.0;
    let mut length = 0.0;
    for pair in stations.windows(2) {
        length += pair[0].distance(pair[1]);
    }
    let step = 1.0 / rocks.density.max(0.01);
    let count = ((length / step) as usize).min(MAX_PER_BODY);
    if count == 0 {
        return Vec::new();
    }
    let walk_step = length / count as f32;
    let mut rng = Rng::new(body_seed(stations[0], index) ^ SHORE_SEED_SALT);
    let mut out = Vec::with_capacity(count);
    let mut target = walk_step * 0.5;
    let mut walked = 0.0;
    for pair in stations.windows(2) {
        let seg = pair[0].distance(pair[1]);
        if seg < 1e-4 {
            continue;
        }
        let dir = (pair[1] - pair[0]) / seg;
        let perp = Vec2::new(-dir.y, dir.x);
        while target <= walked + seg && out.len() < count {
            let along = target - walked;
            let base = pair[0] + dir * along;
            // Alternância de margens com deslize longitudinal — fileiras
            // opostas lêem-se como artificialmente ritmadas.
            let side = if rng.next_u64() & 1 == 0 { 1.0 } else { -1.0 };
            let lateral = waterline * 0.85 + rng.range(-WET_DEPTH, outer - waterline * 0.85);
            let slide = rng.range(-1.5, 1.5);
            out.push(base + perp * (side * lateral) + dir * slide);
            target += walk_step;
        }
        walked += seg;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lake_candidates_ring_the_shore() {
        let rocks = ShoreRocksSpec::default();
        let a = lake_candidates(Vec2::new(-40.0, 20.0), 12.0, 3.0, 0.5, &rocks, 0);
        let b = lake_candidates(Vec2::new(-40.0, 20.0), 12.0, 3.0, 0.5, &rocks, 0);
        assert_eq!(a, b, "same body → same candidates");
        assert!(!a.is_empty(), "a 12 m lake has shore rocks");
        // Todos os candidatos ficam perto do contorno (banda húmida+seca).
        for p in &a {
            let d = p.distance(Vec2::new(-40.0, 20.0));
            assert!(
                d > 6.0 && d < 12.0 * 1.25 + DRY_SPREAD + 1.0,
                "candidate {p} off the shore band (d={d})"
            );
        }
        // Densidade maior → mais pedras.
        let dense = ShoreRocksSpec {
            density: 0.4,
            ..rocks
        };
        let more = lake_candidates(Vec2::new(-40.0, 20.0), 12.0, 3.0, 0.5, &dense, 0);
        assert!(more.len() > a.len(), "density drives count");
    }

    #[test]
    fn test_river_candidates_line_the_banks() {
        let rocks = ShoreRocksSpec::default();
        let stations = vec![Vec2::new(0.0, -30.0), Vec2::new(0.0, 30.0)];
        let cands = river_candidates(&stations, 6.0, 2.0, 0.4, 2.0, &rocks, 0);
        assert!(!cands.is_empty());
        // Nenhum candidato no centro do canal: |x| > waterline·0.85 − WET.
        let waterline = 3.0 * waterline_reach(2.0, 0.4).clamp(0.4, 1.0);
        for p in &cands {
            assert!(
                p.x.abs() > (waterline * 0.85 - WET_DEPTH - 0.5).max(0.0),
                "candidate {p} in mid-channel"
            );
        }
    }

    #[test]
    fn test_degenerate_bodies_give_no_candidates() {
        let rocks = ShoreRocksSpec::default();
        assert!(lake_candidates(Vec2::ZERO, 0.0, 2.0, 0.5, &rocks, 0).is_empty());
        assert!(river_candidates(&[], 6.0, 2.0, 0.4, 2.0, &rocks, 0).is_empty());
    }
}
