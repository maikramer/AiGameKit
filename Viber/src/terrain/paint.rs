//! Camada de PINCEL de material do terreno: overrides por célula sobre os pesos splat
//! procedurais (a "paint brush" do editor de terreno — Fase 2).
//!
//! Cada célula guarda, quando pintada, o slot do pool (`splat::DEFAULT_LAYERS`) e um peso
//! 0..1. Na geração dos splats de chunk ([`super::splat::generate_chunk_splats`]) o peso
//! pintado INTERPOLA o vetor procedural em direção ao slot pintado; um slot fortemente
//! pintado num chunk é forçado à eleição top-8 (como o leito e a areia da margem já eram).
//!
//! Persistência: ficheiro próprio ao lado do `.ahgt` (`` `<ahgt>`.paint ``) — magic +
//! world_size + lado + células (slot u8, peso u8). A pintura é aditiva por célula (o
//! máximo entre o peso existente e o novo); `amount = 0` limpa.

use anyhow::{Context, Result};
use bevy::math::Vec2;
use serde::{Deserialize, Serialize};

use super::delta::falloff;
use super::splat::{DEFAULT_LAYERS, LAYER_COUNT};

pub const PAINT_MAGIC: &[u8; 8] = b"VPAINT01";

/// Retângulo de células `[i0, i0+cols) × [j0, j0+rows)` — o par do [`crate::scene::terrain::CellRect`]
/// do lado do motor (o módulo de cena converte).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CellWindow {
    pub i0: usize,
    pub j0: usize,
    pub cols: usize,
    pub rows: usize,
}

/// Slot do pool por alias (`"grass"`, `"dirt_trail"`, …) — índice em `DEFAULT_LAYERS`.
pub fn slot_for_alias(alias: &str) -> Option<usize> {
    DEFAULT_LAYERS
        .iter()
        .position(|layer| layer.eq_ignore_ascii_case(alias))
}

/// O alias canónico de um slot (para relatórios e UI).
pub fn alias_for_slot(slot: usize) -> Option<&'static str> {
    DEFAULT_LAYERS.get(slot).copied()
}

/// Gre­lha de pintura alinhada ao mundo. `slots`/`weights` têm `side × side` células
/// (o mesmo lado da grelha de alturas); peso 0 = célula limpa.
#[derive(Debug, Clone)]
pub struct PaintGrid {
    world_size: f32,
    side: usize,
    slots: Vec<u8>,
    weights: Vec<u8>,
}

/// Vazia e inócua (nenhuma amostra pintada) — literals de teste.
impl Default for PaintGrid {
    fn default() -> Self {
        Self::new(64.0, 2)
    }
}

impl PaintGrid {
    pub fn new(world_size: f32, side: usize) -> Self {
        Self {
            world_size,
            side: side.max(2),
            slots: vec![0; side.max(2) * side.max(2)],
            weights: vec![0; side.max(2) * side.max(2)],
        }
    }

    pub fn side(&self) -> usize {
        self.side
    }

    pub fn world_size(&self) -> f32 {
        self.world_size
    }

    pub fn is_empty(&self) -> bool {
        self.weights.iter().all(|&weight| weight == 0)
    }

    fn cell_index(&self, i: usize, j: usize) -> Option<usize> {
        if i < self.side && j < self.side {
            Some(j * self.side + i)
        } else {
            None
        }
    }

    /// Centro mundo XZ da célula.
    pub fn cell_center(&self, i: usize, j: usize) -> Vec2 {
        let half = self.world_size * 0.5;
        let step = self.world_size / (self.side - 1) as f32;
        Vec2::new(-half + i as f32 * step, -half + j as f32 * step)
    }

    /// A célula que contém `x z` (None fora do mapa).
    pub fn cell_at(&self, x: f32, z: f32) -> Option<(usize, usize)> {
        let half = self.world_size * 0.5;
        if !(-half..=half).contains(&x) || !(-half..=half).contains(&z) {
            return None;
        }
        let step = self.world_size / (self.side - 1) as f32;
        let i = (((x + half) / step).round() as i64).clamp(0, self.side as i64 - 1) as usize;
        let j = (((z + half) / step).round() as i64).clamp(0, self.side as i64 - 1) as usize;
        Some((i, j))
    }

    /// A pintura numa posição mundo: `(slot, peso 0..1)` quando a célula está pintada.
    pub fn sample(&self, x: f32, z: f32) -> Option<(usize, f32)> {
        let (i, j) = self.cell_at(x, z)?;
        let index = self.cell_index(i, j)?;
        let weight = self.weights[index];
        (weight > 0).then(|| (self.slots[index] as usize, weight as f32 / 255.0))
    }

    /// Janela que cobre o círculo `at`/`radius` (None se totalmente fora do mapa).
    pub fn window_around(&self, at: Vec2, radius: f32) -> Option<CellWindow> {
        let (min_i, min_j) = self.cell_at(at.x - radius, at.y - radius)?;
        let (max_i, max_j) = self.cell_at(at.x + radius, at.y + radius)?;
        Some(CellWindow {
            i0: min_i.min(max_i),
            j0: min_j.min(max_j),
            cols: max_i.max(min_i) - min_i.min(max_i) + 1,
            rows: max_j.max(min_j) - min_j.min(max_j) + 1,
        })
    }

    /// Pinta um círculo no slot com o falloff suave do pincel de alturas. O peso da célula
    /// fica no MÁXIMO entre o existente e o novo (pintar nunca escurece); `amount = 0`
    /// LIMPA as células do círculo. Devolve a janela tocada (None se fora do mapa).
    pub fn paint_circle(
        &mut self,
        at: Vec2,
        radius: f32,
        slot: usize,
        amount: f32,
    ) -> Option<CellWindow> {
        let radius = radius.max(0.05);
        let window = self.window_around(at, radius)?;
        if slot >= LAYER_COUNT {
            return None;
        }
        for j in window.j0..window.j0 + window.rows {
            for i in window.i0..window.i0 + window.cols {
                let t = self.cell_center(i, j).distance(at) / radius;
                if t > 1.0 {
                    continue;
                }
                let Some(index) = self.cell_index(i, j) else {
                    continue;
                };
                if amount <= 0.0 {
                    self.slots[index] = 0;
                    self.weights[index] = 0;
                    continue;
                }
                let wanted = (amount.clamp(0.0, 1.0) * falloff(t) * 255.0).round() as u8;
                if wanted > self.weights[index] {
                    self.slots[index] = slot as u8;
                    self.weights[index] = wanted;
                }
            }
        }
        Some(window)
    }

    /// A moldura mundo de TUDO o que está pintado (None se a camada está
    /// limpa) — quantos chunks a repintura ao vivo tem de cobrir.
    pub fn painted_bounds(&self) -> Option<(Vec2, Vec2)> {
        let mut min: Option<(usize, usize)> = None;
        let mut max: Option<(usize, usize)> = None;
        for j in 0..self.side {
            for i in 0..self.side {
                let Some(index) = self.cell_index(i, j) else {
                    continue;
                };
                if self.weights[index] == 0 {
                    continue;
                }
                min = Some(match min {
                    Some((mi, mj)) => (mi.min(i), mj.min(j)),
                    None => (i, j),
                });
                max = Some(match max {
                    Some((mi, mj)) => (mi.max(i), mj.max(j)),
                    None => (i, j),
                });
            }
        }
        let (min, max) = (min?, max?);
        let a = self.cell_center(min.0, min.1);
        let b = self.cell_center(max.0, max.1);
        Some((a, b))
    }

    /// Snapshot dos bytes (slot,peso) de uma janela — a inversa de um pincel.
    pub fn snapshot(&self, window: CellWindow) -> Vec<u8> {
        let mut out = Vec::with_capacity(window.cols * window.rows * 2);
        for j in window.j0..window.j0 + window.rows {
            for i in window.i0..window.i0 + window.cols {
                match self.cell_index(i, j) {
                    Some(index) => {
                        out.push(self.slots[index]);
                        out.push(self.weights[index]);
                    }
                    None => {
                        out.push(0);
                        out.push(0);
                    }
                }
            }
        }
        out
    }

    /// Repõe os bytes de uma janela (mesmo formato do snapshot).
    pub fn restore(&mut self, window: CellWindow, data: &[u8]) -> Result<()> {
        let expected = window.cols * window.rows * 2;
        if data.len() != expected {
            anyhow::bail!("paint: esperados {expected} bytes, vieram {}", data.len());
        }
        let mut bytes = data.iter();
        for j in window.j0..window.j0 + window.rows {
            for i in window.i0..window.i0 + window.cols {
                let (Some(slot), Some(weight)) = (bytes.next(), bytes.next()) else {
                    break;
                };
                if let Some(index) = self.cell_index(i, j) {
                    self.slots[index] = *slot;
                    self.weights[index] = *weight;
                }
            }
        }
        Ok(())
    }

    /// Serializa (magic + world_size + lado + células).
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(8 + 4 + 4 + self.slots.len() * 2);
        out.extend_from_slice(PAINT_MAGIC);
        out.extend_from_slice(&self.world_size.to_le_bytes());
        out.extend_from_slice(&(self.side as u32).to_le_bytes());
        for (slot, weight) in self.slots.iter().zip(&self.weights) {
            out.push(*slot);
            out.push(*weight);
        }
        out
    }

    /// O simétrico de [`Self::to_bytes`].
    pub fn from_bytes(bytes: &[u8]) -> Result<Self> {
        if bytes.len() < 16 || &bytes[..8] != PAINT_MAGIC {
            anyhow::bail!("paint: cabeçalho inválido");
        }
        let world_size = f32::from_le_bytes(bytes[8..12].try_into().expect("4 bytes"));
        let side = u32::from_le_bytes(bytes[12..16].try_into().expect("4 bytes")) as usize;
        let cells = side.saturating_mul(side);
        if bytes.len() != 16 + cells * 2 {
            anyhow::bail!("paint: tamanho inesperado (lado {side})");
        }
        let mut slots = vec![0u8; cells];
        let mut weights = vec![0u8; cells];
        for (cell, pair) in bytes[16..].as_chunks::<2>().0.iter().enumerate() {
            slots[cell] = pair[0];
            weights[cell] = pair[1];
        }
        Ok(Self {
            world_size,
            side,
            slots,
            weights,
        })
    }

    /// Grava ao lado do `.ahgt` (`` `<ahgt>`.paint ``).
    pub fn save(&self, ahgt: &std::path::Path) -> Result<()> {
        let path = paint_path(ahgt);
        crate::scene::doc::write_atomic(&path, &self.to_bytes())
            .with_context(|| format!("escrever {}", path.display()))
    }

    /// Carrega `` `<ahgt>`.paint `` se existir (None de outra forma). O `world_size` declarado
    /// tem de bater com o do mundo.
    pub fn load(ahgt: &std::path::Path, world_size: f32) -> Result<Option<Self>> {
        let path = paint_path(ahgt);
        let Ok(bytes) = std::fs::read(&path) else {
            return Ok(None);
        };
        let grid = Self::from_bytes(&bytes).with_context(|| format!("ler {}", path.display()))?;
        if (grid.world_size - world_size).abs() > 1.0 {
            anyhow::bail!(
                "{}: world-size {} ≠ {} do mundo",
                path.display(),
                grid.world_size,
                world_size
            );
        }
        Ok(Some(grid))
    }
}

/// O caminho do ficheiro de pintura de um `.ahgt` (`terrain.ahgt` → `terrain.ahgt.paint`).
pub fn paint_path(ahgt: &std::path::Path) -> std::path::PathBuf {
    ahgt.with_extension("ahgt.paint")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aliases_and_slots_roundtrip() {
        assert_eq!(slot_for_alias("Grass"), Some(0));
        assert_eq!(slot_for_alias("dirt_trail"), Some(3));
        assert_eq!(slot_for_alias("nope"), None);
        assert_eq!(alias_for_slot(12), Some("pebbles"));
    }

    #[test]
    fn paint_circle_paints_with_falloff_and_never_darkens() {
        let mut grid = PaintGrid::new(200.0, 64);
        let center = Vec2::new(10.0, -4.0);
        let window = grid
            .paint_circle(center, 12.0, 7, 0.8)
            .expect("dentro do mapa");
        let (ci, cj) = grid.cell_at(center.x, center.y).unwrap();
        assert!(window.i0 <= ci && ci < window.i0 + window.cols);
        assert!(window.j0 <= cj && cj < window.j0 + window.rows);
        let (slot, weight) = grid.sample(center.x, center.y).unwrap();
        assert_eq!(slot, 7);
        assert!(
            (weight - 0.8).abs() < 0.02,
            "centro ≈ amount, veio {weight}"
        );
        // borda do círculo: falloff quase zero
        let edge = grid.sample(center.x + 11.5, center.y);
        assert!(edge.map(|(_, w)| w < 0.1).unwrap_or(true), "borda ~0");
        // voltar a pintar mais fraco NÃO escurece o centro
        grid.paint_circle(center, 12.0, 2, 0.2);
        let (slot2, weight2) = grid.sample(center.x, center.y).unwrap();
        assert_eq!(slot2, 7, "peso anterior ganha");
        assert!((weight2 - 0.8).abs() < 0.02);
    }

    #[test]
    fn clear_with_amount_zero_and_out_of_map() {
        let mut grid = PaintGrid::new(200.0, 64);
        grid.paint_circle(Vec2::ZERO, 5.0, 1, 1.0);
        assert!(grid.sample(0.0, 0.0).is_some());
        grid.paint_circle(Vec2::ZERO, 5.0, 1, 0.0);
        assert!(grid.sample(0.0, 0.0).is_none(), "amount 0 limpa");
        assert!(
            grid.paint_circle(Vec2::new(999.0, 0.0), 5.0, 1, 1.0)
                .is_none()
        );
    }

    #[test]
    fn snapshot_restore_and_bytes_roundtrip() {
        let mut grid = PaintGrid::new(200.0, 32);
        // pintar no CENTRO de uma célula: o peso amostrado volta a ser ~amount
        let at = grid.cell_center(10, 18);
        let window = grid.paint_circle(at, 15.0, 9, 0.9).unwrap();
        let snap = grid.snapshot(window);
        let bytes = grid.to_bytes();
        let mut reloaded = PaintGrid::from_bytes(&bytes).unwrap();
        assert_eq!(reloaded.side(), 32);
        let (slot, weight) = reloaded.sample(at.x, at.y).unwrap();
        assert_eq!(slot, 9);
        assert!((weight - 0.9).abs() < 0.02, "peso {weight}");
        // limpar e restaurar devolve a pintura
        reloaded.paint_circle(at, 15.0, 9, 0.0);
        assert!(reloaded.sample(at.x, at.y).is_none());
        reloaded.restore(window, &snap).unwrap();
        assert_eq!(reloaded.sample(at.x, at.y).unwrap().0, 9);
        // bytes corrompidos são recusados sem pânico
        assert!(PaintGrid::from_bytes(&bytes[..20]).is_err());
    }

    #[test]
    fn save_load_next_to_the_ahgt() {
        let tmp = tempfile::tempdir().unwrap();
        let ahgt = tmp.path().join("terrain.ahgt");
        assert!(PaintGrid::load(&ahgt, 200.0).unwrap().is_none());
        let mut grid = PaintGrid::new(200.0, 16);
        let at = grid.cell_center(8, 8);
        grid.paint_circle(at, 8.0, 4, 1.0);
        grid.save(&ahgt).unwrap();
        assert!(ahgt.with_extension("ahgt.paint").exists());
        let back = PaintGrid::load(&ahgt, 200.0).unwrap().unwrap();
        assert_eq!(back.sample(at.x, at.y).unwrap().0, 4);
        // mundo com outro tamanho recusa
        assert!(PaintGrid::load(&ahgt, 500.0).is_err());
    }
}
