//! Edições VIVAS do terreno (Fase 3) — um overlay esparso sobre a grid
//! carvada, o "destructible terrain" que o bootstrap não tinha.
//!
//! # Desenho
//!
//! A grid do bootstrap é imutável (`Arc<BrushGrid>`, partilhada com o
//! [`TerrainReader`] do Luau) e NÃO é tocada: cada edição escreve um RECORTE
//! denso (`DeltaRect`) com alturas ABSOLUTAS em f32 sobre a grelha de
//! texéis. [`DeltaGrid::sample`] consulta os recortes por ordem inversa
//! (o mais recente ganha) e cai na grid base fora deles; o
//! [`EditedBase`] embrulha grid+recortes num [`HeightField`], por onde
//! TODOS os consumidores (mesher transvoxel, `VoxelField::density`,
//! queries de gameplay, `TerrainReader` do Luau) vêem as alturas editadas
//! sem uma segunda fonte de verdade.
//!
//! A reconstrução é por revisão: cada commit incrementa
//! [`DeltaGrid::revision`]; o plugin de LOD compara a revisão gravada em
//! cada coluna e re-mesha (mesh + collider trimesh, pelo swap atómico de
//! sempre) só as colunas cujo rect cruza [`DeltaGrid::bounds`].
//!
//! Determinismo: o bootstrap continua determinístico; as EDIÇÕES são
//! runtime por definição (entram por script/bridge, não pelo XML) e NÃO
//! vão para o save nesta versão — documentado no AGENTS.md.

use bevy::math::{Vec2, Vec3};
use bevy::prelude::Resource;

use super::brush::BrushGrid;
use super::mesh::HeightField;

/// Raio máximo de uma edição (m) — um script a pedir 1 km não congela o
/// frame (o rect é (2·raio/texel)² amostras).
pub const EDIT_MAX_RADIUS: f32 = 96.0;
/// Profundidade/altura máxima de uma edição (m).
pub const EDIT_MAX_DEPTH: f32 = 64.0;
/// Recortes vivos antes da fusão forçada (o par ADJACENTE de menor união
/// funde-se; a amostragem varre-os por ordem inversa).
const MAX_RECTS: usize = 8;
/// Teto duro de recortes quando nenhuma fusão cabe em [`MERGE_CELL_CAP`]
/// (edições muito afastadas): acima dele funde-se na mesma.
const MAX_RECTS_HARD: usize = 32;
/// Células máximas de um recorte fundido (16 MiB de f32).
const MERGE_CELL_CAP: usize = 4 * 1_048_576;
/// Pedidos em fila antes de o `push` recusar (um script em loop não enche
/// a memória nem agenda minutos de edições).
pub const EDIT_QUEUE_CAP: usize = 256;
/// Edições aplicadas por frame (o resto fica em fila) — um script em loop
/// não transforma um frame num carve de mundo.
pub const EDITS_PER_FRAME: usize = 4;

/// Um recorte denso de alturas (f32, ABSOLUTAS) sobre a grelha de texéis da
/// grid carvada, ancorado no lattice dela (min alinhado a texel +- 1e-3).
#[derive(Debug, Clone, PartialEq)]
struct DeltaRect {
    min: Vec2,
    texel: f32,
    cols: usize,
    rows: usize,
    data: Vec<f32>,
    lo: f32,
    hi: f32,
}

impl DeltaRect {
    /// Bilinear dentro do recorte; `None` fora (a grid base responde).
    fn sample(&self, x: f32, z: f32) -> Option<f32> {
        let fx = (x - self.min.x) / self.texel;
        let fz = (z - self.min.y) / self.texel;
        if fx < 0.0 || fz < 0.0 || fx > (self.cols - 1) as f32 || fz > (self.rows - 1) as f32 {
            return None;
        }
        let x0 = fx.floor() as usize;
        let z0 = fz.floor() as usize;
        let x1 = (x0 + 1).min(self.cols - 1);
        let z1 = (z0 + 1).min(self.rows - 1);
        let tx = fx - x0 as f32;
        let tz = fz - z0 as f32;
        let at = |ix: usize, iz: usize| self.data[iz * self.cols + ix];
        let a = at(x0, z0) + (at(x1, z0) - at(x0, z0)) * tx;
        let b = at(x0, z1) + (at(x1, z1) - at(x0, z1)) * tx;
        Some(a + (b - a) * tz)
    }

    fn max_corner(&self) -> Vec2 {
        self.min
            + Vec2::new((self.cols - 1) as f32, (self.rows - 1) as f32) * self.texel
    }

    /// min/max do sub-rectângulo que intersecta o box dado; `None` quando
    /// não há sobreposição.
    fn range_over(&self, min_x: f32, min_z: f32, max_x: f32, max_z: f32) -> Option<(f32, f32)> {
        if max_x < self.min.x || min_x > self.max_corner().x || max_z < self.min.y || min_z > self.max_corner().y {
            return None;
        }
        let i0 = (((min_x - self.min.x) / self.texel).floor().max(0.0)) as usize;
        let j0 = (((min_z - self.min.y) / self.texel).floor().max(0.0)) as usize;
        let i1 = (((max_x - self.min.x) / self.texel).ceil().min((self.cols - 1) as f32)) as usize;
        let j1 = (((max_z - self.min.y) / self.texel).ceil().min((self.rows - 1) as f32)) as usize;
        let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
        for j in j0..=j1 {
            for i in i0..=i1 {
                let v = self.data[j * self.cols + i];
                lo = lo.min(v);
                hi = hi.max(v);
            }
        }
        (lo <= hi).then_some((lo, hi))
    }
}

/// A camada de edições: recortes densos, o mais recente a ganhar.
#[derive(Debug, Clone, Default)]
pub struct DeltaGrid {
    rects: Vec<DeltaRect>,
    revision: u64,
}

impl DeltaGrid {
    /// Altura editada em XZ, se algum recorte a cobrir (o mais recente
    /// primeiro); `None` = a grid base manda.
    pub fn sample(&self, x: f32, z: f32) -> Option<f32> {
        self.rects.iter().rev().find_map(|r| r.sample(x, z))
    }

    /// Revisão monotónica — a chave de staleness das colunas.
    pub fn revision(&self) -> u64 {
        self.revision
    }

    pub fn is_empty(&self) -> bool {
        self.rects.is_empty()
    }

    /// Bounding XZ de todas as edições (a área a re-meshar), com margem de
    /// um texel.
    pub fn bounds(&self) -> Option<(Vec2, Vec2)> {
        let mut it = self.rects.iter();
        let first = it.next()?;
        let mut lo = first.min;
        let mut hi = first.max_corner();
        for r in it {
            lo = lo.min(r.min);
            hi = hi.max(r.max_corner());
        }
        let pad = Vec2::splat(first.texel);
        Some((lo - pad, hi + pad))
    }

    /// min/max das alturas editadas dentro do box; `None` sem edições lá.
    pub fn range_over(&self, min_x: f32, min_z: f32, max_x: f32, max_z: f32) -> Option<(f32, f32)> {
        let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
        for r in &self.rects {
            if let Some((l, h)) = r.range_over(min_x, min_z, max_x, max_z) {
                lo = lo.min(l);
                hi = hi.max(h);
            }
        }
        (lo <= hi).then_some((lo, hi))
    }

    /// min/max GLOBAIS armazenados (diagnóstico/testes).
    pub fn stored_range(&self) -> Option<(f32, f32)> {
        let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
        for r in &self.rects {
            lo = lo.min(r.lo);
            hi = hi.max(r.hi);
        }
        (lo <= hi).then_some((lo, hi))
    }

    /// Commita um recorte. Funde SÓ com o recorte mais recente (o topo da
    /// pilha) quando as bboxes se tocam: fundir com um mais antigo punha a
    /// edição nova por baixo dos recortes intermédios, que a escondiam.
    /// Senão empurra; acima de [`MAX_RECTS`] funde o par adjacente de menor
    /// união — adjacente preserva o "mais recente ganha".
    fn commit(&mut self, grid: &BrushGrid, rect: DeltaRect) {
        let top = self.rects.len().wrapping_sub(1);
        let merge_top = self
            .rects
            .last()
            .is_some_and(|last| overlaps(last, &rect) && union_cells(last, &rect) <= MERGE_CELL_CAP);
        if merge_top {
            let grown = grow_pair(&self.rects[top], &rect, |p| {
                sample_below(&self.rects[..top], grid, p)
            });
            self.rects[top] = grown;
        } else {
            self.rects.push(rect);
            if self.rects.len() > MAX_RECTS {
                let (i, cells) = (0..self.rects.len() - 1)
                    .map(|i| (i, union_cells(&self.rects[i], &self.rects[i + 1])))
                    .min_by_key(|&(_, cells)| cells)
                    .expect("len > MAX_RECTS ≥ 2");
                if cells <= MERGE_CELL_CAP || self.rects.len() > MAX_RECTS_HARD {
                    let b = self.rects.remove(i + 1);
                    let grown = grow_pair(&self.rects[i], &b, |p| {
                        sample_below(&self.rects[..i], grid, p)
                    });
                    self.rects[i] = grown;
                }
            }
        }
        self.revision = self.revision.wrapping_add(1);
    }
}

fn overlaps(a: &DeltaRect, b: &DeltaRect) -> bool {
    let (a_max, b_max) = (a.max_corner(), b.max_corner());
    a.min.x <= b_max.x && b.min.x <= a_max.x && a.min.y <= b_max.y && b.min.y <= a_max.y
}

/// Células do envelope da união de dois recortes (mesma grelha).
fn union_cells(a: &DeltaRect, b: &DeltaRect) -> usize {
    let lo = a.min.min(b.min);
    let hi = a.max_corner().max(b.max_corner());
    let cols = ((hi.x - lo.x) / a.texel).round() as usize + 1;
    let rows = ((hi.y - lo.y) / a.texel).round() as usize + 1;
    cols.saturating_mul(rows)
}

/// A vista por BAIXO de um recorte: os mais antigos, depois a grid base.
fn sample_below(older: &[DeltaRect], grid: &BrushGrid, p: Vec2) -> f32 {
    older
        .iter()
        .rev()
        .find_map(|r| r.sample(p.x, p.y))
        .unwrap_or_else(|| grid.sample(p.x, p.y))
}

/// Cresce `a`/`b` ao envelope da união: `b` é o mais RECENTE (ganha onde
/// cobre), `a` preenche o resto e as células que nenhum cobre (cantos da
/// bbox da união) recebem a vista de baixo — o que lá se via antes.
fn grow_pair(a: &DeltaRect, b: &DeltaRect, below: impl Fn(Vec2) -> f32) -> DeltaRect {
    let texel = a.texel;
    let lo = a.min.min(b.min);
    let hi = a.max_corner().max(b.max_corner());
    let cols = ((hi.x - lo.x) / texel).round() as usize + 1;
    let rows = ((hi.y - lo.y) / texel).round() as usize + 1;
    let mut data = Vec::with_capacity(cols * rows);
    let (mut lo_h, mut hi_h) = (f32::INFINITY, f32::NEG_INFINITY);
    for j in 0..rows {
        for i in 0..cols {
            let p = lo + Vec2::new(i as f32, j as f32) * texel;
            let v = b
                .sample(p.x, p.y)
                .or_else(|| a.sample(p.x, p.y))
                .unwrap_or_else(|| below(p));
            data.push(v);
            lo_h = lo_h.min(v);
            hi_h = hi_h.max(v);
        }
    }
    DeltaRect {
        min: lo,
        texel,
        cols,
        rows,
        data,
        lo: lo_h,
        hi: hi_h,
    }
}

/// Grid + edições como um só [`HeightField`] — a vista que TODOS os
/// consumidores usam (mesher, `VoxelField`, queries de gameplay, Luau).
#[derive(Clone, Copy)]
pub struct EditedBase<'a> {
    pub grid: &'a BrushGrid,
    pub deltas: &'a DeltaGrid,
}

impl HeightField for EditedBase<'_> {
    fn sample(&self, world_x: f32, world_z: f32) -> f32 {
        self.deltas
            .sample(world_x, world_z)
            .unwrap_or_else(|| self.grid.sample(world_x, world_z))
    }

    fn sample_normal(&self, world_x: f32, world_z: f32, epsilon: f32) -> Vec3 {
        let e = if epsilon.is_finite() && epsilon > 0.0 {
            epsilon
        } else {
            self.grid.texel().max(1e-3)
        };
        let hx0 = self.sample(world_x - e, world_z);
        let hx1 = self.sample(world_x + e, world_z);
        let hz0 = self.sample(world_x, world_z - e);
        let hz1 = self.sample(world_x, world_z + e);
        Vec3::new(hx0 - hx1, 2.0 * e, hz0 - hz1).normalize_or_zero()
    }

    fn max_height(&self) -> f32 {
        self.grid.max_height()
    }

    fn range_over(&self, min_x: f32, min_z: f32, max_x: f32, max_z: f32) -> Option<(f32, f32)> {
        let base = self.grid.range_over(min_x, min_z, max_x, max_z);
        let edits = self.deltas.range_over(min_x, min_z, max_x, max_z);
        match (base, edits) {
            (Some((a, b)), Some((c, d))) => Some((a.min(c), b.max(d))),
            (Some(v), None) => Some(v),
            (None, Some(v)) => Some(v),
            (None, None) => None,
        }
    }
}

/// Uma edição pedida por script/bridge (a fila aplica-a ao
/// [`super::runtime::TerrainRuntime`] no máximo [`EDITS_PER_FRAME`] por
/// frame).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TerrainEdit {
    /// Abaixa (mineração/explosão pontual): `h = cur − depth·w(t)`.
    Lower { at: Vec2, radius: f32, depth: f32 },
    /// Levanta (aterro/colina): `h = cur + height·w(t)`.
    Raise { at: Vec2, radius: f32, height: f32 },
    /// Achata a `height` (ou à cota do centro quando `None`) com blend `w`.
    Flatten {
        at: Vec2,
        radius: f32,
        height: Option<f32>,
    },
    /// Cratera clássica: tigela funda + rebordo saliente.
    Crater { at: Vec2, radius: f32, depth: f32 },
}

/// Fila de edições (Luau/bridge → engine). Vive separada do runtime para o
/// sistema de aplicação poder ser um `ResMut` próprio.
#[derive(Resource, Default, Debug)]
pub struct TerrainEditQueue {
    pub pending: std::collections::VecDeque<TerrainEdit>,
    /// Total aplicado desde o boot (diagnóstico).
    pub applied: u64,
    /// Pedidos descartados por inválidos (NaN, raio 0) ou com a fila cheia.
    pub rejected: u64,
}

impl TerrainEditQueue {
    /// Enfileira `edit`; `false` (e conta em `rejected`) quando o pedido é
    /// malformado ou a fila já tem [`EDIT_QUEUE_CAP`] pendentes.
    pub fn push(&mut self, edit: TerrainEdit) -> bool {
        if !edit.is_well_formed() || self.pending.len() >= EDIT_QUEUE_CAP {
            self.rejected += 1;
            return false;
        }
        self.pending.push_back(edit);
        true
    }
}

impl TerrainEdit {
    fn center_radius(&self) -> (Vec2, f32) {
        match self {
            TerrainEdit::Lower { at, radius, .. }
            | TerrainEdit::Raise { at, radius, .. }
            | TerrainEdit::Flatten { at, radius, .. }
            | TerrainEdit::Crater { at, radius, .. } => (*at, *radius),
        }
    }

    /// Parâmetros utilizáveis: centro/raio finitos, raio > 0, profundidade
    /// ou altura RELATIVA finita e ≤ [`EDIT_MAX_DEPTH`]; o `Flatten` leva uma
    /// cota ABSOLUTA (só tem de ser finita — o resultado é clampado a
    /// `0..=max_height` como todas as edições).
    pub fn is_well_formed(&self) -> bool {
        let (at, radius) = self.center_radius();
        if !at.is_finite() || !radius.is_finite() || radius <= 0.0 {
            return false;
        }
        match self {
            TerrainEdit::Lower { depth: v, .. }
            | TerrainEdit::Raise { height: v, .. }
            | TerrainEdit::Crater { depth: v, .. } => v.is_finite() && v.abs() <= EDIT_MAX_DEPTH,
            TerrainEdit::Flatten { height, .. } => height.is_none_or(f32::is_finite),
        }
    }
}

/// Falloff suave `1 → 0` em `t = d/raio` (smootherstep), com o corte a 1.
fn falloff(t: f32) -> f32 {
    if t >= 1.0 {
        return 0.0;
    }
    let u = 1.0 - t;
    u * u * u * (u * (u * 6.0 - 15.0) + 10.0)
}

/// Perfil ASSINADO da cratera (fracção da profundidade): tigela NEGATIVA
/// até 0.7·r, rebordo POSITIVO 0.7..1.0·r (zeros em t=0.7 e t=1 — contínuo).
fn crater_profile(t: f32) -> f32 {
    const RIM: f32 = 0.7;
    if t >= 1.0 {
        0.0
    } else if t <= RIM {
        let u = t / RIM;
        -(1.0 - u * u)
    } else {
        0.35 * (std::f32::consts::PI * (t - RIM) / (1.0 - RIM)).sin()
    }
}

/// Aplica UMA edição ao overlay. Devolve `false` quando o pedido é
/// malformado (ver [`TerrainEdit::is_well_formed`]) ou cai fora do mundo.
pub fn apply_edit(grid: &BrushGrid, deltas: &mut DeltaGrid, edit: &TerrainEdit) -> bool {
    if !edit.is_well_formed() {
        return false;
    }
    let (at, radius) = edit.center_radius();
    let radius = radius.min(EDIT_MAX_RADIUS);

    let texel = grid.texel();
    // Rect ancorado ao lattice da grid (origem em -world_size/2), com uma
    // célula de margem (o falloff chega a zero no raio, portanto a borda do
    // rect já é igual à base) e recortado ao mundo: fora dele não há mesh,
    // e um recorte distante faria o envelope da próxima fusão explodir.
    let origin = -grid.world_size() * 0.5;
    let half = radius + texel;
    let lo = (at - Vec2::splat(half)).max(Vec2::splat(origin - texel));
    let hi = (at + Vec2::splat(half)).min(Vec2::splat(-origin + texel));
    if lo.x >= hi.x || lo.y >= hi.y {
        return false;
    }
    let snap = |v: f32, round: fn(f32) -> f32| origin + round((v - origin) / texel) * texel;
    let min = Vec2::new(snap(lo.x, f32::floor), snap(lo.y, f32::floor));
    let max = Vec2::new(snap(hi.x, f32::ceil), snap(hi.y, f32::ceil));
    let cols = (((max.x - min.x) / texel).round() as usize) + 1;
    let rows = (((max.y - min.y) / texel).round() as usize) + 1;
    if cols < 2 || rows < 2 || cols * rows > 1_048_576 {
        return false;
    }

    // FASE 1 (só leitura): a vista JÁ editada dá o `cur` — edições empilham.
    let view = EditedBase { grid, deltas };
    let center = view.sample(at.x, at.y);
    let mut data = Vec::with_capacity(cols * rows);
    let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
    for j in 0..rows {
        for i in 0..cols {
            let p = min + Vec2::new(i as f32, j as f32) * texel;
            let cur = view.sample(p.x, p.y);
            let t = p.distance(at) / radius;
            let h = if t >= 1.0 {
                cur
            } else {
                match edit {
                    TerrainEdit::Lower { depth, .. } => cur - depth * falloff(t),
                    TerrainEdit::Raise { height, .. } => cur + height * falloff(t),
                    TerrainEdit::Flatten { height, .. } => {
                        let target = height.unwrap_or(center);
                        cur + (target - cur) * falloff(t)
                    }
                    TerrainEdit::Crater { depth, .. } => cur + depth * crater_profile(t),
                }
            };
            let h = h.clamp(0.0, grid.max_height());
            data.push(h);
            lo = lo.min(h);
            hi = hi.max(h);
        }
    }

    // FASE 2 (escrita): commit (funde com o recorte do topo se o tocar).
    deltas.commit(grid, DeltaRect {
        min,
        texel,
        cols,
        rows,
        data,
        lo,
        hi,
    });
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::brush::BrushGrid;

    fn flat_grid() -> BrushGrid {
        let mut grid = BrushGrid::new(vec![0; 96 * 96], 96, 96, 96.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("flat");
        for i in 0..96 * 96 {
            grid.set_cell_height(i % 96, i / 96, 10.0);
        }
        grid.commit_stroke();
        grid
    }

    /// Overlay vazio = a base manda; uma edição cobre só o seu raio.
    #[test]
    fn test_delta_sample_falls_back_to_the_base() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        assert!(deltas.sample(0.0, 0.0).is_none());
        let view = EditedBase {
            grid: &grid,
            deltas: &deltas,
        };
        assert_eq!(view.sample(0.0, 0.0), 10.0);

        assert!(apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Lower {
                at: Vec2::ZERO,
                radius: 10.0,
                depth: 4.0
            }
        ));
        assert!(deltas.sample(0.0, 0.0).unwrap() < 7.0, "fundo da tigela");
        assert!(deltas.sample(20.0, 0.0).is_none(), "fora do raio, base");
        let view = EditedBase {
            grid: &grid,
            deltas: &deltas,
        };
        assert_eq!(view.sample(20.0, 0.0), 10.0);
        assert!(view.sample(0.0, 0.0) < 7.0);
        assert_eq!(deltas.revision(), 1);
    }

    /// `range_over`/`bounds` veem as edições — é o que faz o stack-Y das
    /// colunas e o `region_state` acompanharem um raise.
    #[test]
    fn test_delta_range_and_bounds_include_edits() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Raise {
                at: Vec2::new(30.0, -20.0),
                radius: 8.0,
                height: 6.0,
            },
        );
        let (lo, hi) = deltas.range_over(20.0, -30.0, 40.0, -10.0).expect("range");
        assert!(hi > 15.5, "o raise entra no range: {lo}..{hi}");
        assert!((lo - 10.0).abs() < 0.5, "bordas na base: {lo}");
        assert!(deltas.range_over(-90.0, -90.0, -80.0, -80.0).is_none());
        let (min, max) = deltas.bounds().expect("bounds");
        assert!(min.x <= 22.0 && max.x >= 38.0 && min.y <= -28.0 && max.y >= -12.0);
    }

    /// Edições empilham: um segundo lower escava mais fundo; um lower sobre
    /// um raise soma na vista.
    #[test]
    fn test_edits_stack_through_the_edited_view() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Lower {
                at: Vec2::ZERO,
                radius: 10.0,
                depth: 3.0,
            },
        );
        let first = deltas.sample(0.0, 0.0).unwrap();
        apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Lower {
                at: Vec2::ZERO,
                radius: 10.0,
                depth: 3.0,
            },
        );
        let second = deltas.sample(0.0, 0.0).unwrap();
        assert!(second < first - 2.0, "escava em camadas: {first} → {second}");
    }

    /// A cratera tem tigela E rebordo; fora do raio nada muda.
    #[test]
    fn test_crater_has_a_bowl_and_a_rim() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Crater {
                at: Vec2::ZERO,
                radius: 20.0,
                depth: 8.0,
            },
        );
        let center = deltas.sample(0.0, 0.0).unwrap();
        let rim = deltas.sample(16.0, 0.0).unwrap(); // 0.8·r
        assert!(center < 3.0, "fundo da tigela: {center}");
        assert!(rim > 11.5, "rebordo saliente: {rim}");
        assert!(deltas.sample(25.0, 0.0).is_none(), "fora do raio");
    }

    /// Os tetos de raio/profundidade e o clamp de altura protegem o frame.
    #[test]
    fn test_edit_sanitizes_extremes() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        assert!(!apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Lower {
                at: Vec2::new(f32::NAN, 0.0),
                radius: 5.0,
                depth: 1.0
            }
        ));
        assert!(!apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Raise {
                at: Vec2::ZERO,
                radius: 0.0,
                height: 1.0
            }
        ));
        assert!(deltas.is_empty(), "inválidos não escrevem");
        // Raise absurdo: clampado à altura máxima da grid (nunca acima).
        apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Raise {
                at: Vec2::ZERO,
                radius: 5.0,
                height: 60.0,
            },
        );
        assert!(deltas.sample(0.0, 0.0).unwrap() <= grid.max_height() + 1e-3);
    }

    /// A fusão mantém o número de recortes limitado e a vista correcta.
    #[test]
    fn test_rects_merge_and_stay_bounded() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        for k in 0..12 {
            apply_edit(
                &grid,
                &mut deltas,
                &TerrainEdit::Lower {
                    at: Vec2::new(k as f32 * 3.0, 0.0),
                    radius: 4.0,
                    depth: 1.0,
                },
            );
        }
        assert!(
            deltas.rects.len() <= MAX_RECTS,
            "recortes fundem: {}",
            deltas.rects.len()
        );
        // Todas as covas ficaram escavadas na vista.
        let view = EditedBase {
            grid: &grid,
            deltas: &deltas,
        };
        for k in 0..12 {
            let h = view.sample(k as f32 * 3.0, 0.0);
            assert!(h < 9.6, "edição {k} sobreviveu à fusão: {h}");
        }
    }

    /// A união de dois recortes em diagonal tem cantos que nenhum cobre:
    /// recebem a base (antes ficavam NaN — altura NaN no mesh e na física).
    #[test]
    fn test_merge_corners_take_the_base_not_nan() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        for at in [Vec2::new(0.0, 10.0), Vec2::new(10.0, 0.0)] {
            assert!(apply_edit(
                &grid,
                &mut deltas,
                &TerrainEdit::Raise {
                    at,
                    radius: 8.0,
                    height: 2.0
                }
            ));
        }
        assert_eq!(deltas.rects.len(), 1, "as bboxes tocam-se: fundem");
        let r = &deltas.rects[0];
        assert!(r.data.iter().all(|v| v.is_finite()), "sem NaN no recorte fundido");
        let corner = deltas.sample(r.min.x + 0.5, r.min.y + 0.5).expect("dentro");
        assert!((corner - 10.0).abs() < 1e-3, "canto descoberto = base: {corner}");
    }

    /// A edição nova ganha SEMPRE: fundir com um recorte antigo punha-a por
    /// baixo dos intermédios, que a escondiam.
    #[test]
    fn test_newest_edit_wins_over_intermediate_rects() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        let lower = |x: f32| TerrainEdit::Lower {
            at: Vec2::new(x, 0.0),
            radius: 8.0,
            depth: 2.0,
        };
        assert!(apply_edit(&grid, &mut deltas, &lower(-20.0)));
        assert!(apply_edit(&grid, &mut deltas, &lower(20.0)));
        assert_eq!(deltas.rects.len(), 2, "afastadas: dois recortes");
        // Toca os dois; cobre x≈13 onde o lower de x=20 também manda.
        assert!(apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Flatten {
                at: Vec2::ZERO,
                radius: 14.0,
                height: Some(20.0),
            }
        ));
        let view = EditedBase {
            grid: &grid,
            deltas: &deltas,
        };
        let expected = {
            let fresh = DeltaGrid::default();
            let mut solo = fresh.clone();
            apply_edit(&grid, &mut solo, &lower(20.0));
            let before = EditedBase {
                grid: &grid,
                deltas: &solo,
            }
            .sample(13.0, 0.0);
            before + (20.0 - before) * falloff(13.0 / 14.0)
        };
        let got = view.sample(13.0, 0.0);
        assert!((got - expected).abs() < 0.05, "o flatten novo manda em x=13: {got} vs {expected}");
        assert!(view.sample(0.0, 0.0) > 19.9, "centro achatado a 20 m");
    }

    /// Cota ABSOLUTA do flatten acima do teto relativo é legítima; fora do
    /// mundo é recusado (o envelope da fusão seguinte explodiria).
    #[test]
    fn test_flatten_absolute_height_and_world_bounds() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        let high = TerrainEdit::Flatten {
            at: Vec2::ZERO,
            radius: 6.0,
            height: Some(45.0),
        };
        assert!(high.is_well_formed());
        assert!(apply_edit(&grid, &mut deltas, &high));
        assert!(deltas.sample(0.0, 0.0).unwrap() > 40.0);

        let far = TerrainEdit::Raise {
            at: Vec2::new(1.0e6, 0.0),
            radius: 6.0,
            height: 2.0,
        };
        assert!(far.is_well_formed());
        assert!(!apply_edit(&grid, &mut deltas, &far), "fora do mundo");
        assert_eq!(deltas.rects.len(), 1);
        assert!(
            !TerrainEdit::Raise {
                at: Vec2::ZERO,
                radius: 6.0,
                height: EDIT_MAX_DEPTH + 1.0
            }
            .is_well_formed()
        );
        assert!(
            !TerrainEdit::Flatten {
                at: Vec2::ZERO,
                radius: 6.0,
                height: Some(f32::INFINITY)
            }
            .is_well_formed()
        );
    }

    /// O recorte fica no lattice da grid (origem em −world/2), não no de 0.
    #[test]
    fn test_rect_is_anchored_to_the_grid_lattice() {
        let grid = flat_grid();
        let mut deltas = DeltaGrid::default();
        apply_edit(
            &grid,
            &mut deltas,
            &TerrainEdit::Lower {
                at: Vec2::new(3.3, -7.1),
                radius: 5.0,
                depth: 1.0,
            },
        );
        let r = &deltas.rects[0];
        let origin = -grid.world_size() * 0.5;
        for v in [r.min.x, r.min.y] {
            let k = (v - origin) / grid.texel();
            assert!((k - k.round()).abs() < 1e-3, "min fora do lattice: {v} (k = {k})");
        }
    }

    /// A fila recusa malformados e para no teto.
    #[test]
    fn test_queue_rejects_malformed_and_caps() {
        let mut q = TerrainEditQueue::default();
        let ok = TerrainEdit::Lower {
            at: Vec2::ZERO,
            radius: 2.0,
            depth: 1.0,
        };
        assert!(!q.push(TerrainEdit::Lower {
            at: Vec2::ZERO,
            radius: -1.0,
            depth: 1.0
        }));
        for _ in 0..EDIT_QUEUE_CAP {
            assert!(q.push(ok));
        }
        assert!(!q.push(ok), "fila cheia");
        assert_eq!(q.pending.len(), EDIT_QUEUE_CAP);
        assert_eq!(q.rejected, 2);
    }
}
