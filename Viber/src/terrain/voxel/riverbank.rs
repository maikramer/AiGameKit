//! Margens voxel para rios e lagos — os estilos `bank="gorge"` e
//! `bank="overhang"` ([`crate::terrain::water::BankStyle`]).
//!
//! O carve do heightfield (lower-only) não consegue rocha por cima da
//! lâmina: uma parede de margem fica presa a rampas ≤ vertical. Aqui a
//! parede nasce do SÓLIDO NATURAL que o carve preservou (os estilos voxel
//! não esculpem a rampa) e é cortada por [`CliffFaceMod`]s — o mesmo
//! maquinismo dos `<Cliff>`, alimentado com bandas construídas a partir das
//! estações do [`WaterBody`].
//!
//! * **gorge** — face vertical dos dois lados, a descer até
//!   [`TOE_SUBMERGE`] abaixo da lâmina local (o pé fica submerso: água
//!   encosta à rocha).
//! * **overhang** — perfil [`CliffProfile::Overhang`]: o beiral avança SOBRE
//!   a água (undercut real — impossível em 2.5D).
//!
//! As bandas devolvidas alimentam dois consumidores: `into_mods()` (o sólido
//! 3D, no bootstrap do voxel) e `CliffMask::add_authored_bands` (pedra no
//! splat + exclusão de relva/spawners na faixa da parede). Puro e
//! determinístico: toda a sondagem ao grid acontece aqui, uma vez.

use bevy::math::{Vec2, Vec3};

use super::super::cliffs::{CliffProfile, hash01};
use super::super::mesh::HeightField;
use super::super::paths::{PathHit, nearest_on_path};
use super::cliff::CliffBand;
use super::mods::{CapsuleMod, ModOp, VoxelMod};
use crate::terrain::water::{BankStyle, LakeSpec, RiverSpec, WaterBody};

/// Seed determinística para as bandas, derivada do ANCORAGEM do corpo
/// (RiverSpec/LakeSpec não têm seed própria — mesma família de
/// `shore_rocks::body_seed`).
fn body_seed(at: Vec2) -> u64 {
    (at.x.to_bits() as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ ((at.y.to_bits() as u64).rotate_left(17))
}

/// Assento plano entre a lâmina e o pé da parede (m) — evita a água tocar
/// diretamente na base irregular do heightfield.
const BENCH: f32 = 0.6;
/// Profundidade (m) que o pé da parede desce abaixo da lâmina local.
const TOE_SUBMERGE: f32 = 0.8;
/// Largura da face numa margem gorge (m).
const GORGE_WALL: f32 = 2.2;
/// Avanço do beiral numa margem overhang (m).
const OVERHANG_RUN: f32 = 1.6;
/// Sondagem (m) além da crista onde o banco natural ainda é o topo.
const TOP_PROBE: f32 = 2.0;

/// Bandas de parede para as DUAS margens de um rio (`bank` voxel).
pub fn river_banks(
    spec: &RiverSpec,
    body: &WaterBody,
    base: &dyn HeightField,
    texel: f32,
) -> Vec<CliffBand> {
    let n = body.stations.len();
    if n < 2 || !spec.bank.is_voxel() {
        return Vec::new();
    }
    let profile = match spec.bank {
        BankStyle::Overhang => CliffProfile::Overhang,
        _ => CliffProfile::Vertical,
    };
    // As larguras do registry são EFETIVAS (linha de água — o carve guarda
    // `half · reach`, ver `WaterBody::half_width`); a parede e a sonda do
    // topo vivem no espaço de DESIGN (o assento carvado vai até à
    // meia-largura de design + `bank_width`), pelo que a crista volta à
    // largura de design — mundos voxel existentes mantêm a parede onde
    // sempre esteve.
    let wl_reach =
        super::super::water::waterline_reach(spec.depth, spec.water_offset).clamp(0.4, 1.0);
    let design_half = |i: usize| -> f32 {
        if body.half_width.is_empty() {
            body.water_width * 0.5
        } else {
            body.half_width_at(i) / wl_reach
        }
    };
    let mut bands = Vec::with_capacity(2);
    for side in [-1.0f32, 1.0f32] {
        let mut stations = Vec::with_capacity(n);
        let mut top_y = Vec::with_capacity(n);
        let mut bot_y = Vec::with_capacity(n);
        let mut width = Vec::with_capacity(n);
        let mut arc = Vec::with_capacity(n);
        let mut drop_normal = Vec::with_capacity(n);
        let mut toe_ground = Vec::with_capacity(n);
        let seed = body_seed(body.at);
        let phase = hash01(seed, side.to_bits() as u64, 3) * std::f32::consts::TAU;
        let base_width = match spec.bank {
            BankStyle::Overhang => OVERHANG_RUN,
            _ => GORGE_WALL,
        };
        let mut acc = 0.0f32;
        for (i, st) in body.stations.iter().enumerate() {
            let next = body.stations[(i + 1).min(n - 1)];
            let prev = body.stations[i.saturating_sub(1)];
            let dir = (next - prev).normalize_or_zero();
            // Normal para FORA do canal; a face pende para DENTRO (o rio é
            // o lado baixo da banda).
            let outward = Vec2::new(-dir.y, dir.x) * side;
            let half = design_half(i);
            let crest = *st + outward * (half + BENCH);
            stations.push(crest);
            // Topo: o banco natural além da crista (o carve voxel preservou-
            // o); nunca abaixo da lâmina — margem submersa não tem parede.
            let probe = crest + outward * TOP_PROBE;
            top_y.push(base.sample(probe.x, probe.y).max(body.surface_y[i]));
            bot_y.push(body.surface_y[i] - TOE_SUBMERGE);
            if i > 0 {
                acc += crest.distance(stations[i - 1]);
            }
            arc.push(acc);
            let w = (base_width * (1.0 + 0.15 * (acc * 0.25 + phase).sin())).max(texel * 1.5);
            width.push(w);
            drop_normal.push(-outward);
            toe_ground.push(body.surface_y[i] - TOE_SUBMERGE);
        }
        bands.push(CliffBand {
            stations,
            drop_normal,
            top_y,
            bot_y,
            width,
            arc,
            columns: Vec::new(),
            profile,
            seed: seed ^ (side.to_bits() as u64),
            toe_ground,
            talus_run: vec![0.0; n],
            talus: false,
            talus_angle: 36.0,
        });
    }
    bands
}

/// Banda de parede em ANEL FECHADO no contorno de um lago (`bank="gorge"`).
/// O topo lê o banco natural fora da lâmina (o carve voxel não escavou a
/// rampa); o pé desce [`TOE_SUBMERGE`] abaixo do espelho.
pub fn lake_shore_band(
    spec: &LakeSpec,
    body: &WaterBody,
    base: &dyn HeightField,
    texel: f32,
) -> Option<CliffBand> {
    if !spec.bank.is_voxel() || spec.radius <= 0.0 {
        return None;
    }
    // Anel fechado: amostra o contorno harmónico na linha de água real e
    // fecha repetindo a primeira estação no fim (into_mods corta por pares).
    let segments = 96;
    let shape =
        super::super::water::LakeShape::from_authoring(spec.at, &spec.shape);
    let reach = (super::super::water::waterline_reach(spec.depth, spec.water_offset)
        * super::super::water::CARVE_MARGIN)
        .clamp(0.5, 1.6);
    let mut stations = Vec::with_capacity(segments + 1);
    let mut top_y = Vec::with_capacity(segments + 1);
    let mut bot_y = Vec::with_capacity(segments + 1);
    let mut width = Vec::with_capacity(segments + 1);
    let mut arc = Vec::with_capacity(segments + 1);
    let mut drop_normal = Vec::with_capacity(segments + 1);
    let mut toe_ground = Vec::with_capacity(segments + 1);
    let phase = hash01(body_seed(spec.at), 11, 5) * std::f32::consts::TAU;
    let base_width = GORGE_WALL;
    let mut acc = 0.0f32;
    let mut prev = Vec2::ZERO;
    for k in 0..=segments {
        let theta = k as f32 / segments as f32 * std::f32::consts::TAU;
        // A crista fica FORA da lâmina (a parede sobe a partir dela); a
        // sonda do topo, mais para fora ainda — o banco natural.
        let waterline = shape.contour(spec.radius, theta) * reach;
        let crest_r = waterline + BENCH;
        let crest = spec.at + Vec2::new(theta.cos(), theta.sin()) * crest_r;
        let probe = spec.at + Vec2::new(theta.cos(), theta.sin()) * (crest_r + TOP_PROBE);
        stations.push(crest);
        top_y.push(base.sample(probe.x, probe.y).max(body.water_y));
        bot_y.push(body.water_y - TOE_SUBMERGE);
        if k > 0 {
            acc += crest.distance(prev);
        }
        arc.push(acc);
        width.push((base_width * (1.0 + 0.12 * (acc * 0.18 + phase).sin())).max(texel * 1.5));
        // O lado baixo é o lago — normal APONTA PARA O CENTRO.
        drop_normal.push(Vec2::new(-theta.cos(), -theta.sin()));
        toe_ground.push(body.water_y - TOE_SUBMERGE);
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
        profile: CliffProfile::Vertical,
        seed: body_seed(spec.at),
        toe_ground,
        talus_run: vec![0.0; segments + 1],
        talus: false,
        talus_angle: 36.0,
    })
}

/// Banda em FERRADURA na estação 0 de um rio (`spring="1"`): o anel de
/// rocha rodeia a nascente com a ABERTURA voltada a jusante — a água
/// "sai" da rocha por baixo do arco. Mesma entrega das margens: mods no
/// bootstrap + máscara (pedra no splat, sem relva no anel).
pub fn spring_band(
    spec: &RiverSpec,
    body: &WaterBody,
    base: &dyn HeightField,
    texel: f32,
) -> Option<CliffBand> {
    if !spec.spring || body.stations.is_empty() {
        return None;
    }
    let center = body.stations[0];
    let water = body.surface_y[0];
    let next = body.stations[1.min(body.stations.len() - 1)];
    let downstream = (next - center).normalize_or_zero();
    if downstream == Vec2::ZERO {
        return None;
    }
    let perp = Vec2::new(-downstream.y, downstream.x);
    // Raio em espaço de DESIGN (ver nota em `river_banks` — as larguras do
    // registry são efetivas, da linha de água).
    let wl_reach =
        super::super::water::waterline_reach(spec.depth, spec.water_offset).clamp(0.4, 1.0);
    let radius = if body.half_width.is_empty() {
        body.water_width * 0.5
    } else {
        body.half_width_at(0) / wl_reach
    } + 1.8;
    // Meia-abertura (rad) voltada a JUSANTE: a rocha cobre o arco de
    // montante; a boca de 90° fica virada para onde o rio corre.
    let opening = 45.0f32.to_radians();
    let segments = 24;
    let mut stations = Vec::new();
    let mut top_y = Vec::new();
    let mut bot_y = Vec::new();
    let mut width = Vec::new();
    let mut arc = Vec::new();
    let mut drop_normal = Vec::new();
    let mut toe_ground = Vec::new();
    let phase = hash01(body_seed(center), 21, 9) * std::f32::consts::TAU;
    let mut acc = 0.0f32;
    let mut prev = Vec2::ZERO;
    for k in 0..=segments {
        // α: 0 = montante (−downstream), π = jusante. Ignora o arco central
        // da abertura (|α − π| < opening).
        let alpha =
            opening + (std::f32::consts::TAU - 2.0 * opening) * (k as f32 / segments as f32);
        let radial = -downstream * alpha.cos() + perp * alpha.sin();
        let crest = center + radial * radius;
        stations.push(crest);
        // Topo: o banco natural para fora do anel.
        let probe = center + radial * (radius + TOP_PROBE);
        top_y.push(base.sample(probe.x, probe.y).max(water));
        bot_y.push(water - TOE_SUBMERGE);
        if k > 0 {
            acc += crest.distance(prev);
        }
        arc.push(acc);
        width.push((GORGE_WALL * (1.0 + 0.12 * (acc * 0.3 + phase).sin())).max(texel * 1.5));
        drop_normal.push(-radial);
        toe_ground.push(water - TOE_SUBMERGE);
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
        profile: CliffProfile::Vertical,
        seed: body_seed(center),
        toe_ground,
        talus_run: vec![0.0; segments + 1],
        talus: false,
        talus_angle: 36.0,
    })
}

/// Folga (m) além de `meia-largura da banda + meia-largura do canal` ao
/// testar se uma queda do rio cruza a parede.
const WALL_MATCH_MARGIN: f32 = 1.0;
/// Recuo (m) da fenda de spill para dentro da crista — a água "sai" da
/// fenda em vez de beijar a parede.
const SPILL_BORE: f32 = 2.5;
/// Voo (m) da fenda para além do pé da banda — a fenda atravessa a parede
/// inteira, e um pouco a jusante, para se ler de frente.
const SPILL_RUN: f32 = 1.5;

/// Cachoeiras de PAREDE: anota as quedas dos rios que despencam numa banda
/// de `<Cliff>` e devolve os mods subtractivos de spill.
///
/// O carve do rio já conduziu o perfil pelos hints do pre-pass (hold a
/// montante da crista + queda garantida a jusante —
/// [`crate::terrain::water::river_cliff_crossings`]); aqui, com as bandas
/// JÁ resolvidas contra a grid carvada, cada queda grande cujo meio cai
/// dentro da pegada de uma banda passa a `wall = true`: a face vai do brow
/// ao toe da banda (`bot_y` = pé da banda, nunca acima da lâmina a
/// jusante) e o mesh desenha UM quadro de parede em vez de faces por
/// segmento. A fenda (capsule subtract no brow, largura ∝ canal) abre a
/// crista por onde a água despenca — a parede em si fica sólida.
///
/// Determinístico: só leituras de bandas/corpos já resolvidos, sem RNG.
pub fn wall_waterfalls(
    rivers: &[RiverSpec],
    bodies: &mut [WaterBody],
    water_specs: &[(bool, usize)],
    bands: &[CliffBand],
) -> Vec<Box<dyn VoxelMod>> {
    let mut mods: Vec<Box<dyn VoxelMod>> = Vec::new();
    if bands.is_empty() {
        return mods;
    }
    for (body, &(is_lake, spec_i)) in bodies.iter_mut().zip(water_specs) {
        if is_lake || spec_i >= rivers.len() {
            continue;
        }
        let spec = &rivers[spec_i];
        // Leituras de que a anotação precisa, clonadas ANTES do borrow
        // mutável das quedas (estações/meias-larguras são pequenas —
        // bootstrap-only).
        let stations = body.stations.clone();
        let halfs = body.half_width.clone();
        let water_width = body.water_width;
        let half_at = |i: usize| halfs.get(i).copied().unwrap_or(water_width * 0.5);
        for c in body.cascades.iter_mut() {
            if !c.waterfall || c.wall {
                continue;
            }
            let mid = (c.lip + c.base) / 2;
            let Some(st) = stations.get(mid) else {
                continue;
            };
            // A banda mais próxima do meio da queda.
            let mut best: Option<(f32, usize, PathHit)> = None;
            for (bi, band) in bands.iter().enumerate() {
                let Some(hit) = nearest_on_path(&band.stations, *st) else {
                    continue;
                };
                let d = hit.point.distance(*st);
                if best.as_ref().map_or(true, |(bd, _, _)| d < *bd) {
                    best = Some((d, bi, hit));
                }
            }
            let Some((dist, bi, hit)) = best else {
                continue;
            };
            let band = &bands[bi];
            // Leituras na estação da banda mais próxima do cruzamento.
            let idx =
                (hit.station(band.stations.len()).round() as usize).min(band.stations.len() - 1);
            let band_w = band.width[idx];
            if dist > band_w * 0.5 + half_at(mid) + WALL_MATCH_MARGIN {
                continue;
            }
            // A face cola-se ao pé da parede (submerso no caldeirão se ele
            // desce abaixo do pé — nunca acima da lâmina a jusante).
            let bot = band.bot_y[idx].min(c.bot_y);
            if c.top_y - bot < c.drop * 0.6 {
                continue;
            }
            c.wall = true;
            c.bot_y = bot;
            c.drop = (c.top_y - bot).max(0.0);
            if !spec.waterfall_notch {
                continue;
            }
            // Fenda de spill: cápsula atravessando a crista no cruzamento,
            // centrada ligeiramente ACIMA da lâmina (remove o sill entre o
            // canal e a face sem rasgar o caldeirão abaixo).
            let crest = hit.point;
            let normal = band.drop_normal[idx];
            let radius = (half_at(c.lip) * 1.2).max(0.8);
            let a = Vec3::new(
                crest.x - normal.x * SPILL_BORE,
                c.top_y + radius * 0.25,
                crest.y - normal.y * SPILL_BORE,
            );
            let b = Vec3::new(
                crest.x + normal.x * (band_w + SPILL_RUN),
                c.top_y + radius * 0.25,
                crest.y + normal.y * (band_w + SPILL_RUN),
            );
            mods.push(Box::new(CapsuleMod::new(
                format!("spill:{spec_i}:{}", c.lip),
                a,
                b,
                radius,
                ModOp::Subtract,
            )));
        }
    }
    mods
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::brush::BrushGrid;
    use crate::terrain::water::{LakeSpec, carve_lake, carve_river};
    use bevy::math::Vec3;

    fn flat_grid() -> BrushGrid {
        let mut grid = BrushGrid::new(vec![0; 96 * 96], 96, 96, 96.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("flat");
        for i in 0..96 * 96 {
            grid.set_cell_height(i % 96, i / 96, 8.0);
        }
        grid.commit_stroke();
        grid
    }

    /// Rio gorge: as bandas cobrem as duas margens, o pé fica abaixo da
    /// lâmina e o topo no banco natural.
    #[test]
    fn test_river_bands_straddle_the_channel() {
        let mut grid = flat_grid();
        let spec = RiverSpec {
            path: vec![Vec2::new(-40.0, 0.0), Vec2::new(40.0, 0.0)],
            width: 6.0,
            depth: 2.0,
            bank: BankStyle::Gorge,
            ..RiverSpec::default()
        };
        let body = carve_river(&mut grid, &spec, 0, &[]).expect("river");
        let bands = river_banks(&spec, &body, &grid, grid.texel());
        assert_eq!(bands.len(), 2, "uma banda por margem");
        for band in &bands {
            assert_eq!(band.stations.len(), body.stations.len());
            for (i, st) in band.stations.iter().enumerate() {
                // A crista fica fora da água; o pé abaixo da lâmina.
                assert!(
                    st.distance(
                        *body
                            .stations
                            .iter()
                            .min_by(|a, b| { a.distance(*st).total_cmp(&b.distance(*st)) })
                            .expect("stations")
                    ) > 0.1,
                    "crest off the centerline"
                );
                assert!(
                    band.bot_y[i] < body.surface_y[i],
                    "toe submerged: {}",
                    band.bot_y[i]
                );
                assert!(band.top_y[i] >= band.bot_y[i]);
            }
        }
        // Margens opostas em lados opostos do canal (o path corre em X —
        // as cristas divergem em Z e partilham o X da estação).
        let left = bands[0].stations[10];
        let right = bands[1].stations[10];
        assert!(
            (left.x - right.x).abs() < 1e-3,
            "alinhadas no eixo: {} vs {}",
            left.x,
            right.x
        );
        assert!(
            (left.y + right.y).abs() < 1e-3,
            "simétricas: {} vs {}",
            left.y,
            right.y
        );
    }

    /// Lago gorge: banda em anel fechado (primeira estação == última).
    #[test]
    fn test_lake_band_is_a_closed_ring() {
        let mut grid = flat_grid();
        let spec = LakeSpec {
            at: Vec2::new(0.0, 0.0),
            radius: 12.0,
            depth: 3.0,
            bank: BankStyle::Gorge,
            ..LakeSpec::default()
        };
        let body = carve_lake(&mut grid, &spec, 0).expect("lake");
        let band = lake_shore_band(&spec, &body, &grid, grid.texel()).expect("band");
        let n = band.stations.len();
        assert!(
            band.stations[0].distance(band.stations[n - 1]) < 1e-3,
            "anel fechado"
        );
        // Todas as cristas acima do espelho; todos os pés abaixo.
        for i in 0..n {
            assert!(band.top_y[i] >= band.bot_y[i]);
            assert!(band.bot_y[i] < body.water_y);
        }
        // Soft/Beach NÃO geram banda.
        let soft = LakeSpec {
            bank: BankStyle::Soft,
            ..spec.clone()
        };
        assert!(lake_shore_band(&soft, &body, &grid, grid.texel()).is_none());
    }

    /// O overhang é o teste do sólido: o ponto sob o beiral (fora da
    /// crista, acima do pé) era SÓLIDO no heightfield natural — o mod do
    /// perfil Overhang escava-o em ar; a crista mantém-se sólida (brow).
    #[test]
    fn test_overhang_carves_air_under_the_brow() {
        let mut grid = flat_grid();
        let spec = RiverSpec {
            path: vec![Vec2::new(-40.0, 0.0), Vec2::new(40.0, 0.0)],
            width: 6.0,
            depth: 2.0,
            bank: BankStyle::Overhang,
            ..RiverSpec::default()
        };
        let body = carve_river(&mut grid, &spec, 0, &[]).expect("river");
        let bands = river_banks(&spec, &body, &grid, grid.texel());
        assert_eq!(bands.len(), 2);
        let mut mods = Vec::new();
        for (i, band) in bands.iter().enumerate() {
            mods.extend(band.clone().into_mods(&format!("bank:{i}")));
        }
        assert!(!mods.is_empty(), "bands produce face mods");
        let field = crate::terrain::voxel::VoxelField::new(mods, 96.0, 32.0);

        let i = body.stations.len() / 2;
        let st = body.stations[i];
        let surf = body.surface_y[i];
        let top = bands[1].top_y[i];
        let bot = bands[1].bot_y[i];
        let crest = bands[1].stations[i];
        let outward = -bands[1].drop_normal[i]; // para fora do canal

        // A socava (undercut): junto à lâmina, a face recua PARA DENTRO do
        // banco — o ponto 1.3 m para dentro da crista, acima do pé, é AR
        // (o heightfield natural teria aqui sólido até à borda do canal;
        // o perfil Overhang deslocou o pé da parede para dentro).
        let inward = -outward;
        let under = Vec3::new(
            crest.x + inward.x * 1.6,
            bot + 0.2,
            crest.y + inward.y * 1.6,
        );
        assert!(
            field.density(&grid, under) >= 0.0,
            "undercut must carve air: density={}",
            field.density(&grid, under)
        );
        // O corpo da parede (fora da crista, a meio da face): SÓLIDO —
        // a rocha está de pé sobre o rio.
        let wall = Vec3::new(
            crest.x + outward.x * 0.4,
            (top + bot) * 0.5,
            crest.y + outward.y * 0.4,
        );
        assert!(
            field.density(&grid, wall) < 0.0,
            "wall body stays solid: density={}",
            field.density(&grid, wall)
        );
        // Ao nível da lâmina no eixo do canal: AR (o rio corre).
        assert!(
            field.density(&grid, Vec3::new(st.x, surf, st.y)) >= 0.0,
            "the channel stays open"
        );
        let _ = (surf, top, bot);
    }

    /// Cachoeira de parede: a anotação cola a face ao PÉ da banda (quando
    /// ele desce abaixo do perfil), marca `wall` e devolve a cápsula de
    /// spill no brow.
    #[test]
    fn test_wall_waterfalls_annotate_and_emit_spill() {
        let mut grid = flat_grid();
        let spec = RiverSpec {
            path: vec![Vec2::new(-40.0, 0.0), Vec2::new(40.0, 0.0)],
            width: 6.0,
            depth: 2.0,
            ..RiverSpec::default()
        };
        let mut body = carve_river(&mut grid, &spec, 0, &[]).expect("river");
        // Queda no meio do rio (como o hint do pre-pass a produz) + tier
        // cachoeira: 8 m de perfil, o pé da banda desce ABAIXO disso.
        let mid = body.stations.len() / 2;
        let top = body.surface_y[mid];
        body.surface_y[mid + 1] = top - 8.0;
        body.cascades.push(crate::terrain::water::CascadeInfo {
            lip: mid,
            base: mid + 1,
            drop: 8.0,
            waterfall: true,
            wall: false,
            top_y: top,
            bot_y: top - 8.0,
        });
        // Banda de cliff a cruzar o rio a right angles no meio: crista
        // sobre a estação central, topo 2 m acima da lâmina, pé 9 m abaixo.
        let crest = body.stations[mid];
        let band = CliffBand {
            stations: vec![
                Vec2::new(crest.x, crest.y - 20.0),
                Vec2::new(crest.x, crest.y + 20.0),
            ],
            drop_normal: vec![Vec2::new(1.0, 0.0), Vec2::new(1.0, 0.0)],
            top_y: vec![top + 2.0, top + 2.0],
            bot_y: vec![top - 9.0, top - 9.0],
            width: vec![5.0, 5.0],
            arc: vec![0.0, 40.0],
            columns: Vec::new(),
            profile: CliffProfile::Vertical,
            seed: 7,
            toe_ground: vec![top - 9.0, top - 9.0],
            talus_run: vec![0.0, 0.0],
            talus: false,
            talus_angle: 36.0,
        };
        let mut bodies = vec![body];
        let specs = vec![(false, 0usize)];
        let mods = wall_waterfalls(&[spec], &mut bodies, &specs, &[band]);
        let c = &bodies[0].cascades[0];
        assert!(c.wall, "annotated as a wall fall: {c:?}");
        assert!(
            (c.bot_y - (top - 9.0)).abs() < 1e-4,
            "face bottom glued to the band toe: {} vs {}",
            c.bot_y,
            top - 9.0
        );
        assert!(
            (c.drop - 9.0).abs() < 1e-4,
            "drop follows the face: {}",
            c.drop
        );
        assert_eq!(mods.len(), 1, "one spill capsule for one fall");
    }
}
