//! `<Plateau>` — mesa/planalto autoral: um TOPO PLANO elevado no
//! heightfield (raise, [`carve_plateau`]) e um ANEL DE PAREDE voxel que
//! corta a borda vertical ([`plateau_wall_band`], consumido no bootstrap
//! como as bandas de margem gorge).
//!
//! O heightfield sozinho não consegue a parede: um raise em modo Blend é
//! uma rampa, e o passo de um texel lê-se serrilhado em LOD. O carve sobe
//! o volume da mesa (roads/pads/spawners leem-no no grid), e a banda
//!voxel — o mesmo maquinismo dos cliffs/`bank="gorge"` — fatia a saia
//! verticalmente: face a prumo, skin de pedra via `CliffMask`
//! (`add_authored_bands`), relva e spawners excluídos do anel.
//!
//! Determinístico por construção: amostrar o terreno ANTES de abrir o
//! stroke; variação só via `seed` (0 = hash da posição).

use bevy::math::Vec2;

use super::brush::{BrushGrid, BrushMode, BrushRequest, min_effective, smoothstep01};
use super::cliffs::{CliffProfile, hash01};
use super::mesh::HeightField;
use super::voxel::CliffBand;

/// Recuo (m) da crista para dentro da borda do topo — a parede sobe A
/// PARTIR da borda, sem mastigar o topo plano.
const BENCH: f32 = 0.4;
/// Sondagem (m) para fora da crista onde o banco natural ainda é o pé.
const TOP_PROBE: f32 = 2.0;
/// Assento (m) do pé da parede abaixo do banco natural.
const FOOT_EMBED: f32 = 0.3;
/// Largura base da face (m) — modulada por noise ao longo do anel.
const WALL_WIDTH: f32 = 2.6;
/// Passo (m) de amostragem do perímetro do anel.
const RING_SPACING: f32 = 3.0;

/// Declarative plateau (`<Plateau at size height falloff corner-radius
/// wall seed>`).
#[derive(Debug, Clone, PartialEq)]
pub struct PlateauSpec {
    /// Centro do topo em XZ mundo.
    pub at: Vec2,
    /// Extensão TOTAL do topo (m) — como `<TerrainPad size>`.
    pub size: Vec2,
    /// Subida do topo acima do terreno natural no centro (m).
    pub height: f32,
    /// Saia FORA da parede (m; 0 = a parede morde o chão directamente). A
    /// parede voxel corta-a na vertical de qualquer forma — saia larga só
    /// vale quando `wall` é um perfil que a preserva… que nenhum é. Fica
    /// para autores que queiram a mesa "aterrada" em mundo sem voxel.
    pub falloff: f32,
    /// Raio dos cantos do topo (m).
    pub corner_radius: f32,
    /// Perfil da parede (`wall="vertical|concave|convex|columnar|terraced|
    /// overhang"` — o mesmo vocabulário do `<Cliff>` menos `arch`).
    pub wall: CliffProfile,
    /// Seed do noise da parede (0 = derivado da posição).
    pub seed: u64,
}

impl Default for PlateauSpec {
    fn default() -> Self {
        Self {
            at: Vec2::ZERO,
            size: Vec2::splat(24.0),
            height: 6.0,
            falloff: 0.0,
            corner_radius: 4.0,
            wall: CliffProfile::Vertical,
            seed: 0,
        }
    }
}

/// `wall="…"` partilhado por `<Plateau>` e `<Cut>` — o vocabulário do
/// `<Cliff>` menos `arch` (uma janela furada no meio da parede não é um
/// perfil de muro).
pub fn wall_profile_from_name(name: &str) -> Option<CliffProfile> {
    match CliffProfile::parse(name)? {
        CliffProfile::Arch => None,
        profile => Some(profile),
    }
}

/// Seed determinística da banda (0 = ancoragem na posição, como
/// `riverbank::body_seed`). O XOR final impede que a origem (bits 0)
/// produza a seed 0 — que é o sentinel de "auto".
fn band_seed(spec: &PlateauSpec) -> u64 {
    if spec.seed != 0 {
        return spec.seed;
    }
    ((spec.at.x.to_bits() as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ (spec.at.y.to_bits() as u64).rotate_left(17))
        ^ 0x5EED_FA11
}

/// Raio efetivo dos cantos, clampado à meia-largura menor.
fn corner_radius(spec: &PlateauSpec) -> f32 {
    spec.corner_radius
        .clamp(0.0, spec.size.x.min(spec.size.y) * 0.5)
}

/// SDF de retângulo com cantos redondos (negativo dentro) — o mesmo do
/// `flatten_rect` do pad. `inner = half − radius`; o SDF só precisa dos dois.
fn rounded_rect_sd(p: Vec2, at: Vec2, inner: Vec2, radius: f32) -> f32 {
    let d = (p - at).abs() - inner;
    Vec2::max(d, Vec2::ZERO).length() + d.max_element().min(0.0) - radius
}

fn emit_edge(out: &mut Vec<(Vec2, Vec2)>, a: Vec2, b: Vec2, n: Vec2) {
    let steps = (a.distance(b) / RING_SPACING).ceil().max(1.0) as usize;
    for k in 0..steps {
        let t = k as f32 / steps as f32;
        out.push((a.lerp(b, t), n));
    }
}

fn emit_arc(out: &mut Vec<(Vec2, Vec2)>, center: Vec2, r: f32, from: f32, steps: usize) {
    for k in 0..steps {
        let theta = from + (k as f32 / steps as f32) * std::f32::consts::FRAC_PI_2;
        let n = Vec2::new(theta.cos(), theta.sin());
        out.push((center + n * r, n));
    }
}

/// Perímetro CCW do topo (retângulo com cantos redondos): devolve
/// `(ponto, normal exterior)` espaçado a [`RING_SPACING`]. Amostrado
/// analiticamente (4 arestas + 4 arcos), sem RNG — a forma do anel é da
/// autoria, o noise fica para a largura da parede.
fn top_perimeter(spec: &PlateauSpec) -> Vec<(Vec2, Vec2)> {
    let half = spec.size * 0.5;
    let r = corner_radius(spec);
    let inner = half - Vec2::splat(r);
    let c = |sx: f32, sz: f32| spec.at + Vec2::new(sx * inner.x, sz * inner.y);
    let mut out = Vec::new();
    // Leste, arco NE, norte, arco NO, oeste, arco SO, sul, arco SE.
    emit_edge(
        &mut out,
        spec.at + Vec2::new(half.x, -(inner.y)),
        spec.at + Vec2::new(half.x, inner.y),
        Vec2::X,
    );
    let arc_steps = ((std::f32::consts::FRAC_PI_2 * r / RING_SPACING).ceil() as usize).max(1);
    emit_arc(&mut out, c(1.0, 1.0), r, 0.0, arc_steps);
    emit_edge(
        &mut out,
        spec.at + Vec2::new(inner.x, half.y),
        spec.at + Vec2::new(-inner.x, half.y),
        Vec2::Y,
    );
    emit_arc(
        &mut out,
        c(-1.0, 1.0),
        r,
        std::f32::consts::FRAC_PI_2,
        arc_steps,
    );
    emit_edge(
        &mut out,
        spec.at + Vec2::new(-half.x, inner.y),
        spec.at + Vec2::new(-half.x, -inner.y),
        -Vec2::X,
    );
    emit_arc(&mut out, c(-1.0, -1.0), r, std::f32::consts::PI, arc_steps);
    emit_edge(
        &mut out,
        spec.at + Vec2::new(-inner.x, -half.y),
        spec.at + Vec2::new(inner.x, -half.y),
        -Vec2::Y,
    );
    // O último arco fecha o anel de volta ao 1.º ponto.
    emit_arc(
        &mut out,
        c(1.0, -1.0),
        r,
        3.0 * std::f32::consts::FRAC_PI_2,
        arc_steps + 1,
    );
    out
}

/// Carves the plateau top into the heightfield — RAISE-only, SDF de
/// retângulo redondo a peso 1 no core. Devolve a cota resolvida do topo
/// (`natural no centro + height`), ou `None` quando degenerado.
pub fn carve_plateau(grid: &mut BrushGrid, spec: &PlateauSpec, index: usize) -> Option<f32> {
    if spec.size.x <= 0.0 || spec.size.y <= 0.0 || spec.height <= 0.0 {
        return None;
    }
    let texel = grid.texel();
    // Regra de determinismo: amostrar ANTES de abrir o stroke.
    let top = grid.sample(spec.at.x, spec.at.y) + spec.height;
    let half = spec.size * 0.5;
    let radius = corner_radius(spec);
    let inner = half - Vec2::splat(radius);
    let falloff = min_effective(spec.falloff.max(0.0), texel);
    let at = spec.at;

    let owner = format!("plateau:{index}");
    grid.begin_stroke(&owner);
    let mut weight = |p: Vec2| {
        let sd = rounded_rect_sd(p, at, inner, radius);
        if sd <= 0.0 {
            1.0
        } else if sd < falloff {
            1.0 - smoothstep01(sd / falloff)
        } else {
            0.0
        }
    };
    let mut target = |_| top;
    grid.apply(BrushRequest {
        mode: BrushMode::Raise,
        min_x: at.x - half.x - falloff,
        min_z: at.y - half.y - falloff,
        max_x: at.x + half.x + falloff,
        max_z: at.y + half.y + falloff,
        target: &mut target,
        weight: &mut weight,
    });
    grid.commit_stroke();
    Some(top)
}

/// Anel de parede FECHADO na borda do topo — a crista fica a [`BENCH`]
/// para fora do topo plano, o pé assenta no banco natural sondado para
/// fora (`TOP_PROBE`), com [`FOOT_EMBED`] de assento. No lado em que o
/// terreno natural sobe ATÉ ao topo (planalto cortado na encosta), a
/// queda da parede encolhe para o assento mínimo — o wedge fica
/// naturalmente no-op, sem vala.
pub fn plateau_wall_band(
    spec: &PlateauSpec,
    grid: &dyn HeightField,
    texel: f32,
) -> Option<CliffBand> {
    if spec.size.x <= 0.0 || spec.size.y <= 0.0 || spec.height <= 0.0 {
        return None;
    }
    let top = grid.sample(spec.at.x, spec.at.y);
    let ring = top_perimeter(spec);
    if ring.len() < 3 {
        return None;
    }
    let seed = band_seed(spec);
    let phase = hash01(seed, 7, 3) * std::f32::consts::TAU;
    let n = ring.len();
    let mut stations = Vec::with_capacity(n + 1);
    let mut top_y = Vec::with_capacity(n + 1);
    let mut bot_y = Vec::with_capacity(n + 1);
    let mut width = Vec::with_capacity(n + 1);
    let mut arc = Vec::with_capacity(n + 1);
    let mut drop_normal = Vec::with_capacity(n + 1);
    let mut toe_ground = Vec::with_capacity(n + 1);
    let mut acc = 0.0_f32;
    let mut prev = Vec2::ZERO;
    // Anel fechado: repete o primeiro ponto no fim (into_mods corta por pares).
    for k in 0..=n {
        let (p, outward) = ring[k % n];
        let crest = p + outward * BENCH;
        let probe = p + outward * (BENCH + TOP_PROBE);
        let ground = grid.sample(probe.x, probe.y);
        stations.push(crest);
        top_y.push(top);
        bot_y.push((ground - FOOT_EMBED).min(top - 0.3));
        if k > 0 && k <= n {
            acc += crest.distance(prev);
        }
        arc.push(acc);
        width.push((WALL_WIDTH * (1.0 + 0.12 * (acc * 0.21 + phase).sin())).max(texel * 1.5));
        // O lado baixo é FORA do planalto.
        drop_normal.push(outward);
        toe_ground.push(ground);
        prev = crest;
    }
    Some(CliffBand {
        stations,
        drop_normal,
        top_y,
        bot_y,
        width,
        arc,
        columns: Vec::new(),
        profile: spec.wall,
        seed,
        toe_ground,
        talus_run: vec![0.0; n + 1],
        talus: false,
        talus_angle: 36.0,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::math::Vec3;
    use crate::terrain::brush::BrushGrid;

    /// 96×96 grid plana a 8 m.
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

    fn spec_at(at: Vec2) -> PlateauSpec {
        PlateauSpec {
            at,
            size: Vec2::splat(24.0),
            height: 6.0,
            ..PlateauSpec::default()
        }
    }

    /// O carve sobe o core à cota resolvida e NUNCA desce texel nenhum
    /// (raise-only): fora da mesa o terreno fica intacto.
    #[test]
    fn test_carve_plateau_raises_the_core() {
        let mut grid = flat_grid();
        let spec = spec_at(Vec2::new(0.0, 0.0));
        let top = carve_plateau(&mut grid, &spec, 0).expect("plateau");
        assert!((top - 14.0).abs() < 1e-3, "top = 8 + 6, got {top}");
        assert!(
            (grid.sample(0.0, 0.0) - top).abs() < 0.05,
            "core flat at the resolved top: {}",
            grid.sample(0.0, 0.0)
        );
        // Dentro do topo (perto de um canto) também.
        let corner = Vec2::new(9.0, 9.0);
        assert!(
            (grid.sample(corner.x, corner.y) - top).abs() < 0.05,
            "raised inside the rounded corner"
        );
        // Fora da mesa, intacto.
        assert!(
            (grid.sample(30.0, 0.0) - 8.0).abs() < 1e-3,
            "outside untouched: {}",
            grid.sample(30.0, 0.0)
        );
    }

    /// Degenerado (size/height ≤ 0) não carva nada.
    #[test]
    fn test_carve_plateau_rejects_degenerate() {
        let mut grid = flat_grid();
        let before = grid.raw().to_vec();
        assert!(
            carve_plateau(
                &mut grid,
                &PlateauSpec {
                    height: 0.0,
                    ..spec_at(Vec2::ZERO)
                },
                0
            )
            .is_none()
        );
        assert_eq!(grid.raw(), before.as_slice(), "no writes on degenerate");
    }

    /// O anel de parede fecha sobre si próprio, a crista acima do pé e o
    /// pé abaixo do topo; a normal aponta PARA FORA (o lado baixo).
    #[test]
    fn test_plateau_band_is_a_closed_outward_ring() {
        let mut grid = flat_grid();
        let spec = spec_at(Vec2::ZERO);
        let _top = carve_plateau(&mut grid, &spec, 0).expect("plateau");
        let band = plateau_wall_band(&spec, &grid, grid.texel()).expect("band");
        let n = band.stations.len();
        assert!(
            band.stations[0].distance(band.stations[n - 1]) < 1e-3,
            "closed ring"
        );
        let center = spec.at;
        for i in 0..n {
            let crest = band.stations[i];
            assert!(
                band.top_y[i] > band.bot_y[i],
                "wall has drop everywhere"
            );
            assert!(
                band.drop_normal[i].dot(crest - center) > 0.0,
                "normal points outward"
            );
            // A crista fica fora da borda do topo, não dentro dele.
            let half = spec.size.x * 0.5;
            assert!(crest.x.abs() > half - 1.0 || crest.y.abs() > half - 1.0);
        }
    }

    /// O wedge da banda — o teste do SÓLIDO: fora do anel, entre o pé e o
    /// topo, o terreno natural é CORTADO (ar); dentro, a mesa fica de pé.
    #[test]
    fn test_plateau_band_wedge_cuts_the_outside() {
        let mut grid = flat_grid();
        let spec = spec_at(Vec2::ZERO);
        carve_plateau(&mut grid, &spec, 0).expect("plateau");
        let band = plateau_wall_band(&spec, &grid, grid.texel()).expect("band");
        let mut mods = band.clone().into_mods("plateau:0");
        assert!(!mods.is_empty(), "ring produces face mods");
        let field = crate::terrain::voxel::VoxelField::new(mods, 96.0, 32.0);

        let i = band.stations.len() / 2;
        let crest = band.stations[i];
        let outward = band.drop_normal[i];
        let top = band.top_y[i];
        let bot = band.bot_y[i];
        // A meio da face, FORA da crista: ar (a parede foi cortada a prumo).
        let outside = Vec3::new(
            crest.x + outward.x * 0.8,
            (top + bot) * 0.5,
            crest.y + outward.y * 0.8,
        );
        assert!(
            field.density(&grid, outside) >= 0.0,
            "outside the ring is air: {}",
            field.density(&grid, outside)
        );
        // Dentro da crista à mesma cota: sólido (a mesa).
        let inside = Vec3::new(
            crest.x - outward.x * 1.5,
            top - 0.5,
            crest.y - outward.y * 1.5,
        );
        assert!(
            field.density(&grid, inside) < 0.0,
            "the mesa body stays solid: {}",
            field.density(&grid, inside)
        );
    }

    /// A autoria da parede (`wall="terraced"`) chega à banda; seed 0 deriva
    /// da posição e mantém-se estável.
    #[test]
    fn test_plateau_wall_profile_and_seed() {
        let mut grid = flat_grid();
        let spec = PlateauSpec {
            wall: CliffProfile::Terraced,
            ..spec_at(Vec2::ZERO)
        };
        carve_plateau(&mut grid, &spec, 0).expect("plateau");
        let band = plateau_wall_band(&spec, &grid, grid.texel()).expect("band");
        assert_eq!(band.profile, CliffProfile::Terraced);
        let again = plateau_wall_band(&spec, &grid, grid.texel()).expect("band");
        assert_eq!(band.seed, again.seed, "seed stable across calls");
        assert_ne!(band.seed, 0, "seed 0 derives from the position");
    }
}
