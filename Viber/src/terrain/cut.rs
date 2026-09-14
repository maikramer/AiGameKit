//! `<Cut>` — trincheira/cânion SECO ao longo de um path: o heightfield
//! desce até ao piso da vala (lower-only, [`carve_cut`]) e as DUAS
//! PAREDES voxel cortam-se a prumo nas margens
//! ([`cut_wall_bands`], consumido no bootstrap como as bandas de margem
//! gorge dos rios).
//!
//! É o "road cut" autoral: uma estrada autorada DEPOIS do cut (estradas
//! carvam por último) é surveyada DENTRO da vala — o piso já está no
//! grid — e as paredes ficam-lhe dos lados. Também serve sozinho como
//! cânion erosionado (`wall="concave|convex|columnar|terraced|overhang"`).
//!
//! Porquê heightfield + banda e não só voxel: o piso tem de existir no
//! grid para estradas/spawners/splat o lerem, e o sólido natural entre as
//! paredes é o que a banda fatia — o mesmo partido dos
//! `bank="gorge"` ([`super::voxel::riverbank`]).
//!
//! Determinístico por construção: o piso segue o path suavizado (Chaikin ×2
//! + resample), sem RNG no carve; o noise fica para a largura das paredes,
//! semeado por `seed` (0 = hash da posição).

use bevy::math::Vec2;

use super::brush::{BrushGrid, BrushMode, BrushRequest, min_effective};
use super::cliffs::{CliffProfile, hash01};
use super::mesh::HeightField;
use super::paths::{chaikin_smooth, nearest_on_path, resample, station_lerp};
use super::voxel::CliffBand;

/// Espaçamento das estações do piso (m) — o mesmo suavizado no carve e nas
/// bandas, para as cristas partilharem o eixo.
const STATION_SPACING: f32 = 2.0;
/// Recuo (m) da crista para fora da borda do piso — o corpo da parede não
/// mastiga a largura útil do piso.
const BENCH: f32 = 0.6;
/// Sondagem (m) para fora da crista onde o banco natural é o topo.
const TOP_PROBE: f32 = 2.0;
/// Assento (m) do pé da parede abaixo do piso esculpido.
const FOOT_EMBED: f32 = 0.3;
/// Largura base da face (m) — modulada por noise ao longo do path.
const WALL_WIDTH: f32 = 2.2;

/// Declarative cut (`<Cut path width depth wall seed>`).
#[derive(Debug, Clone, PartialEq)]
pub struct CutSpec {
    /// Centerline do PISO em XZ mundo (`"x z x z …"`, ≥ 2 pontos).
    pub path: Vec<Vec2>,
    /// Largura útil do piso (m, total).
    pub width: f32,
    /// Profundidade da vala abaixo do terreno natural suavizado (m).
    pub depth: f32,
    /// Perfil das paredes (`wall="vertical|concave|convex|columnar|
    /// terraced|overhang"` — o vocabulário do `<Cliff>` menos `arch`).
    pub wall: CliffProfile,
    /// Seed do noise das paredes (0 = derivado da posição).
    pub seed: u64,
}

impl Default for CutSpec {
    fn default() -> Self {
        Self {
            path: Vec::new(),
            width: 8.0,
            depth: 4.0,
            wall: CliffProfile::Vertical,
            seed: 0,
        }
    }
}

/// Seed determinística das bandas (0 = ancoragem no início do path).
fn band_seed(spec: &CutSpec) -> u64 {
    if spec.seed != 0 {
        return spec.seed;
    }
    let at = spec.path.first().copied().unwrap_or(Vec2::ZERO);
    (at.x.to_bits() as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ (at.y.to_bits() as u64).rotate_left(17)
}

/// Estações do piso: o MESMO suavizado no carve e nas bandas.
fn floor_stations(path: &[Vec2]) -> Vec<Vec2> {
    let smoothed = chaikin_smooth(path, 2, false);
    resample(&smoothed, STATION_SPACING)
}

/// Média móvel (janela ímpar) sobre as alturas — mata os degraus do
/// path autoral sem deslocar as extremidades (janela encolhida nas pontas).
fn smooth_heights(heights: &[f32]) -> Vec<f32> {
    const HALF: usize = 2;
    heights
        .iter()
        .enumerate()
        .map(|(i, _)| {
            let lo = i.saturating_sub(HALF);
            let hi = (i + HALF + 1).min(heights.len());
            let n = (hi - lo) as f32;
            heights[lo..hi].iter().sum::<f32>() / n
        })
        .collect()
}

/// Carves the trench floor into the heightfield — LOWER-only, peso 1
/// dentro da meia-largura e rampa mínima para fora (as paredes voxel fazem
/// o prumo; o carve só desce o piso). Lower-only: onde o terreno natural já
/// desce abaixo do piso (a vala "abre" num vale), o carve é no-op — a
/// trincheira morre na encosta em vez de aterrada o vale. Devolve as
/// alturas do piso por estação, ou `None` quando degenerado.
pub fn carve_cut(grid: &mut BrushGrid, spec: &CutSpec, index: usize) -> Option<Vec<f32>> {
    if spec.path.len() < 2 || spec.width <= 0.0 || spec.depth <= 0.0 {
        return None;
    }
    let texel = grid.texel();
    let stations = floor_stations(&spec.path);
    // Regra de determinismo: amostrar o terreno ANTES de abrir o stroke.
    let natural: Vec<f32> = stations.iter().map(|p| grid.sample(p.x, p.y)).collect();
    let floors = smooth_heights(&natural)
        .iter()
        .map(|h| h - spec.depth)
        .collect::<Vec<f32>>();
    let half = spec.width * 0.5;
    // Rampa mínima fora do piso — 1.5 texéis como todo o brush (o prumo é
    // da banda voxel; aqui só o piso desce).
    let feather = min_effective(1.0, texel);

    let owner = format!("cut:{index}");
    grid.begin_stroke(&owner);
    let mut weight = |p: Vec2| {
        let Some(hit) = nearest_on_path(&stations, p) else {
            return 0.0;
        };
        let d = hit.point.distance(p);
        if d <= half {
            1.0
        } else if d < half + feather {
            1.0 - (d - half) / feather
        } else {
            0.0
        }
    };
    let mut target = |p: Vec2| match nearest_on_path(&stations, p) {
        Some(hit) => station_lerp(&floors, &hit),
        None => natural[0] - spec.depth,
    };
    let (mut min_x, mut min_z) = (f32::INFINITY, f32::INFINITY);
    let (mut max_x, mut max_z) = (f32::NEG_INFINITY, f32::NEG_INFINITY);
    for p in &stations {
        min_x = min_x.min(p.x);
        min_z = min_z.min(p.y);
        max_x = max_x.max(p.x);
        max_z = max_z.max(p.y);
    }
    grid.apply(BrushRequest {
        mode: BrushMode::Lower,
        min_x: min_x - half - feather,
        min_z: min_z - half - feather,
        max_x: max_x + half + feather,
        max_z: max_z + half + feather,
        target: &mut target,
        weight: &mut weight,
    });
    grid.commit_stroke();
    Some(floors)
}

/// As DUAS bandas de parede do cut — crista fora da borda do piso, topo no
/// banco natural sondado para fora, pé com assento abaixo do piso. O lado
/// baixo (`drop_normal`) aponta PARA DENTRO da vala. Lê o grid JÁ carvado:
/// o piso da vala é o que o carve deixou (uma estrada posterior afina o
/// piso e as paredes acompanham).
pub fn cut_wall_bands(spec: &CutSpec, grid: &dyn HeightField, texel: f32) -> Vec<CliffBand> {
    if spec.path.len() < 2 || spec.width <= 0.0 || spec.depth <= 0.0 {
        return Vec::new();
    }
    let stations = floor_stations(&spec.path);
    if stations.len() < 2 {
        return Vec::new();
    }
    let n = stations.len();
    let half = spec.width * 0.5;
    let seed = band_seed(spec);
    let phase = hash01(seed, 3, 11) * std::f32::consts::TAU;
    // O piso carvado — a fonte de verdade das cotas do pé (re-amostrado no
    // eixo, nunca o valor do carve: uma estrada posterior pode tê-lo
    // afiado e as paredes têm de acompanhar o piso COMO FICOU).
    let floors: Vec<f32> = stations.iter().map(|p| grid.sample(p.x, p.y)).collect();

    let mut bands = Vec::with_capacity(2);
    for side in [-1.0_f32, 1.0] {
        let mut crest_stations = Vec::with_capacity(n);
        let mut top_y = Vec::with_capacity(n);
        let mut bot_y = Vec::with_capacity(n);
        let mut width = Vec::with_capacity(n);
        let mut arc = Vec::with_capacity(n);
        let mut drop_normal = Vec::with_capacity(n);
        let mut toe_ground = Vec::with_capacity(n);
        let mut acc = 0.0_f32;
        for (i, st) in stations.iter().enumerate() {
            let next = stations[(i + 1).min(n - 1)];
            let prev = stations[i.saturating_sub(1)];
            let dir = (next - prev).normalize_or_zero();
            // Normal para FORA da vala; a face pende para DENTRO (a vala é
            // o lado baixo da banda).
            let outward = Vec2::new(-dir.y, dir.x) * side;
            let crest = *st + outward * (half + BENCH);
            crest_stations.push(crest);
            // Topo: o banco natural além da crista; nunca abaixo do piso —
            // parede submersa no piso não tem face.
            let probe = crest + outward * TOP_PROBE;
            top_y.push(grid.sample(probe.x, probe.y).max(floors[i] + 1.0));
            bot_y.push(floors[i] - FOOT_EMBED);
            if i > 0 {
                acc += crest.distance(crest_stations[i - 1]);
            }
            arc.push(acc);
            width.push((WALL_WIDTH * (1.0 + 0.15 * (acc * 0.25 + phase).sin())).max(texel * 1.5));
            drop_normal.push(-outward);
            toe_ground.push(floors[i] - FOOT_EMBED);
        }
        bands.push(CliffBand {
            stations: crest_stations,
            drop_normal,
            top_y,
            bot_y,
            width,
            arc,
            columns: Vec::new(),
            profile: spec.wall,
            seed: seed ^ (side.to_bits() as u64),
            toe_ground,
            talus_run: vec![0.0; n],
            talus: false,
            talus_angle: 36.0,
        });
    }
    bands
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::math::Vec3;
    use crate::terrain::brush::BrushGrid;

    fn flat_grid() -> BrushGrid {
        let mut grid =
            BrushGrid::new(vec![0; 96 * 96], 96, 96, 96.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("flat");
        for i in 0..96 * 96 {
            grid.set_cell_height(i % 96, i / 96, 8.0);
        }
        grid.commit_stroke();
        grid
    }

    fn spec() -> CutSpec {
        CutSpec {
            path: vec![Vec2::new(-40.0, 0.0), Vec2::new(40.0, 0.0)],
            width: 8.0,
            depth: 5.0,
            ..CutSpec::default()
        }
    }

    /// O piso desce `depth` abaixo do natural e o terreno FORA do corredor
    /// fica intacto. LOWER-only: nenhum texel sobe. (Um 2.º carve não é
    /// no-op de propósito: o perfil re-amostra o natural JÁ carvado, como
    /// o carve dos lagos — idempotência é do journal com revert, não do
    /// re-apply às cegas.)
    #[test]
    fn test_carve_cut_lowers_the_floor_only() {
        let mut grid = flat_grid();
        let spec = spec();
        let before = grid.raw().to_vec();
        carve_cut(&mut grid, &spec, 0).expect("cut");
        let floor = grid.sample(0.0, 0.0);
        assert!(
            (floor - (8.0 - 5.0)).abs() < 0.05,
            "floor at natural − depth: {floor}"
        );
        // Nenhum texel subiu (lower-only)…
        for i in 0..before.len() {
            assert!(
                grid.raw()[i] <= before[i],
                "texel {i} went up: {} > {}",
                grid.raw()[i],
                before[i]
            );
        }
        // …e o corredor é local: a 20 m do eixo nada mudou.
        for x in [-46.0_f32, 46.0] {
            for z in [-20.0_f32, 0.0, 20.0] {
                let i = ((z + 48.0) as usize) * 96 + ((x + 48.0) as usize);
                assert_eq!(
                    grid.raw()[i], before[i],
                    "texel outside the corridor moved at ({x},{z})"
                );
            }
        }
    }

    /// Degenerado (path curto, width/depth ≤ 0) não carva nada.
    #[test]
    fn test_carve_cut_rejects_degenerate() {
        let mut grid = flat_grid();
        let before = grid.raw().to_vec();
        assert!(carve_cut(&mut grid, &CutSpec::default(), 0).is_none());
        assert!(
            carve_cut(
                &mut grid,
                &CutSpec {
                    depth: 0.0,
                    ..spec()
                },
                0
            )
            .is_none()
        );
        assert_eq!(grid.raw(), before.as_slice());
    }

    /// Duas bandas, lados opostos do piso, normais para DENTRO, pé abaixo
    /// do piso, topo acima do pé. Path reto ao longo de X → cristas
    /// simétricas em Z e normais verticais (±Y).
    #[test]
    fn test_cut_bands_straddle_the_trench() {
        let mut grid = flat_grid();
        let spec = spec();
        carve_cut(&mut grid, &spec, 0).expect("cut");
        let bands = cut_wall_bands(&spec, &grid, grid.texel());
        assert_eq!(bands.len(), 2, "one band per side");
        let (left, right) = (&bands[0], &bands[1]);
        assert_eq!(left.stations.len(), right.stations.len());
        for (l, r) in left.stations.iter().zip(&right.stations) {
            assert!(l.y * r.y < 0.0, "crests on opposite sides: {l} vs {r}");
            assert!((l.x - r.x).abs() < 1e-3, "aligned on the axis");
        }
        for band in &bands {
            for i in 0..band.stations.len() {
                assert!(
                    band.top_y[i] > band.bot_y[i] + 0.5,
                    "wall has real drop"
                );
                assert!(
                    band.bot_y[i] < 8.0 - spec.depth + 0.6,
                    "toe near the carved floor: {}",
                    band.bot_y[i]
                );
            }
        }
        // Normais para DENTRO da vala: a banda de crests y<0 aponta +Y, a
        // de crests y>0 aponta −Y.
        assert!(left.drop_normal.iter().all(|n| n.y > 0.0));
        assert!(right.drop_normal.iter().all(|n| n.y < 0.0));
    }

    /// O wedge da banda no SÓLIDO: dentro da vala à cota do piso é AR (o
    /// corte passa), o corpo da parede fica de pé.
    #[test]
    fn test_cut_walls_hold_the_channel_open() {
        let mut grid = flat_grid();
        let spec = spec();
        carve_cut(&mut grid, &spec, 0).expect("cut");
        let bands = cut_wall_bands(&spec, &grid, grid.texel());
        let mut mods = Vec::new();
        for (i, band) in bands.iter().enumerate() {
            mods.extend(band.clone().into_mods(&format!("cut:0:{i}")));
        }
        assert!(!mods.is_empty());
        let field = crate::terrain::voxel::VoxelField::new(mods, 96.0, 32.0);

        // Eixo da vala, ao nível do piso: AR.
        let floor_y = grid.sample(0.0, 0.0);
        assert!(
            field.density(&grid, Vec3::new(0.0, floor_y + 0.2, 0.0)) >= 0.0,
            "the channel stays open"
        );
        // Corpo da parede (fora da crista, a meio da face): SÓLIDO.
        let right = &bands[1];
        let i = right.stations.len() / 2;
        let crest = right.stations[i];
        let outward = -right.drop_normal[i];
        let wall = Vec3::new(
            crest.x + outward.x * 0.4,
            (right.top_y[i] + right.bot_y[i]) * 0.5,
            crest.y + outward.y * 0.4,
        );
        assert!(
            field.density(&grid, wall) < 0.0,
            "wall body stays solid: {}",
            field.density(&grid, wall)
        );
    }

    /// O perfil autoral (`wall="terraced"`) chega às bandas; o parse partilha
    /// o vocabulário do `<Cliff>` menos `arch`.
    #[test]
    fn test_cut_wall_profile_parse() {
        use crate::terrain::plateau::wall_profile_from_name;
        assert_eq!(
            wall_profile_from_name("terraced"),
            Some(CliffProfile::Terraced)
        );
        assert_eq!(wall_profile_from_name("arch"), None, "arch não é muro");
        assert_eq!(wall_profile_from_name("banana"), None);
    }
}
