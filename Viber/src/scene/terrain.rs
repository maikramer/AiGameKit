//! O heightmap como parte do documento do mundo: pincéis de terreno (agentes e editor) são
//! OPERAÇÕES (`terrain_brush`, com a inversa exata `terrain_patch`) — desfazíveis, gravadas no
//! `.ahgt` do `<Terrain heightmap>` e refletidas ao vivo pelo sync (overlay
//! `terrain::delta::DeltaGrid`).
//!
//! Alturas guardadas como no ficheiro (`u16` normalizado a `max_height`); a rede de células é
//! a da `BrushGrid` do runtime (`(-world/2, +world/2)`, passo `world / (n-1)`), por isso uma
//! célula do documento é a mesma célula do terreno vivo.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, anyhow, bail};
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use bevy::math::{Vec2, Vec3};
use serde::{Deserialize, Serialize};

use crate::terrain::delta::{EDIT_MAX_DEPTH, EDIT_MAX_RADIUS, crater_profile, falloff};
use crate::terrain::heightmap::{AhgtMeta, HeightMapU16};
use crate::terrain::paint::CellWindow;

/// O pincel.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BrushMode {
    /// `h += amount · falloff` (metros).
    Raise,
    /// `h -= amount · falloff`.
    Lower,
    /// Aproxima de `height` (omisso: a cota no centro) com força `amount` (0..1, omisso 1).
    Flatten,
    /// Aproxima da média 3×3 com força `amount` (0..1, omisso 1).
    Smooth,
    /// Tigela funda de `amount` metros com rebordo.
    Crater,
    /// Ruído determinístico por célula (hash i,j): `±amount` m com o falloff do pincel.
    Noise,
}

/// Ruído determinístico por célula (‑1..1): um hash rápido de (i, j) sem estado —
/// o mesmo dab produz sempre o mesmo resultado (undo/reload incluído).
fn noise_at(i: usize, j: usize) -> f32 {
    let mut state = (i as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ (j as u64).wrapping_mul(0xC2B2_AE3D_27D4_EB4F);
    state ^= state >> 15;
    state = state.wrapping_mul(0x2545_F491_4F6C_DD1D);
    state ^= state >> 13;
    ((state >> 11) as f32 / (1u64 << 53) as f32) * 2.0 - 1.0
}

/// O heightmap do mundo, carregado quando a primeira operação de terreno precisa dele.
#[derive(Debug, Clone)]
pub struct TerrainDoc {
    /// Ficheiro `.ahgt`.
    pub path: PathBuf,
    pub map: HeightMapU16,
    pub meta: AhgtMeta,
    pub world_size: f32,
    pub max_height: f32,
    pub dirty: bool,
    /// Camada de PINCEL de material (a mesma do motor), ao lado do `.ahgt`.
    pub paint: crate::terrain::paint::PaintGrid,
    pub paint_dirty: bool,
}

impl From<CellWindow> for CellRect {
    fn from(window: CellWindow) -> Self {
        Self {
            i0: window.i0,
            j0: window.j0,
            cols: window.cols,
            rows: window.rows,
        }
    }
}

/// Retângulo de células `[i0, i0+cols) × [j0, j0+rows)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CellRect {
    pub i0: usize,
    pub j0: usize,
    pub cols: usize,
    pub rows: usize,
}

impl TerrainDoc {
    /// Lê o `.ahgt`; `world_size`/`max_height` vêm do meta do ficheiro ou, na falta, do XML.
    pub fn load(path: &Path, world_size: Option<f32>, max_height: Option<f32>) -> Result<Self> {
        let bytes = std::fs::read(path).with_context(|| format!("ler {}", path.display()))?;
        let (map, meta) =
            HeightMapU16::from_ahgt(&bytes).with_context(|| path.display().to_string())?;
        let world_size = meta.world_size.or(world_size).ok_or_else(|| {
            anyhow!(
                "{}: sem world-size (nem no ficheiro, nem no <Terrain>)",
                path.display()
            )
        })?;
        let max_height = meta
            .max_height
            .or(max_height)
            .ok_or_else(|| anyhow!("{}: sem max-height", path.display()))?;
        // A camada de pintura é um sidecar: ficheiro ilegível não bloqueia a
        // edição — arranca limpa.
        let paint = crate::terrain::paint::PaintGrid::load(path, world_size)
            .inspect_err(|error| {
                // best-effort: o `.ahgt` manda, a pintura recomeça
                let _ = error;
            })
            .ok()
            .flatten()
            .unwrap_or_else(|| crate::terrain::paint::PaintGrid::new(world_size, map.width.max(2)));
        Ok(Self {
            path: path.to_path_buf(),
            map,
            meta,
            world_size,
            max_height,
            dirty: false,
            paint,
            paint_dirty: false,
        })
    }

    pub fn save(&mut self) -> Result<()> {
        let bytes = self.map.to_ahgt(&self.meta)?;
        super::doc::write_atomic(&self.path, &bytes)
            .with_context(|| format!("escrever {}", self.path.display()))?;
        self.dirty = false;
        if self.paint_dirty {
            self.paint.save(&self.path)?;
            self.paint_dirty = false;
        }
        Ok(())
    }

    /// Pinta um círculo de material (alias do pool → slot). Devolve o
    /// retângulo tocado (None fora do mapa).
    pub fn paint_circle(
        &mut self,
        at: Vec2,
        radius: f32,
        slot: usize,
        amount: f32,
    ) -> Option<CellRect> {
        let touched = self
            .paint
            .paint_circle(at, radius.min(EDIT_MAX_RADIUS), slot, amount)?;
        self.paint_dirty = true;
        Some(touched.into())
    }

    /// Snapshot base64 da pintura de um retângulo (a inversa de um pincel).
    pub fn paint_snapshot(&self, rect: CellRect) -> String {
        let window = CellWindow {
            i0: rect.i0,
            j0: rect.j0,
            cols: rect.cols,
            rows: rect.rows,
        };
        BASE64.encode(self.paint.snapshot(window))
    }

    /// Repõe um snapshot de pintura.
    pub fn paint_restore(&mut self, rect: CellRect, data: &str) -> Result<()> {
        let window = CellWindow {
            i0: rect.i0,
            j0: rect.j0,
            cols: rect.cols,
            rows: rect.rows,
        };
        let bytes = BASE64
            .decode(data)
            .with_context(|| "paint: base64 inválido")?;
        self.paint.restore(window, &bytes)?;
        self.paint_dirty = true;
        Ok(())
    }

    fn step(&self) -> Vec2 {
        Vec2::new(
            self.world_size / (self.map.width.max(2) - 1) as f32,
            self.world_size / (self.map.depth.max(2) - 1) as f32,
        )
    }

    /// Centro mundo XZ da célula.
    pub fn cell_center(&self, i: usize, j: usize) -> Vec2 {
        Vec2::new(i as f32, j as f32) * self.step() - Vec2::splat(self.world_size * 0.5)
    }

    /// Altura (m) da célula (índices fora saturam).
    pub fn cell(&self, i: usize, j: usize) -> f32 {
        let i = i.min(self.map.width - 1);
        let j = j.min(self.map.depth - 1);
        self.map.data[j * self.map.width + i] as f32 / u16::MAX as f32 * self.max_height
    }

    fn raw(&self, h: f32) -> u16 {
        ((h / self.max_height).clamp(0.0, 1.0) * u16::MAX as f32).round() as u16
    }

    /// Altura bilinear em XZ mundo.
    pub fn sample(&self, x: f32, z: f32) -> f32 {
        let f = (Vec2::new(x, z) + Vec2::splat(self.world_size * 0.5)) / self.step();
        let fx = f.x.clamp(0.0, (self.map.width - 1) as f32);
        let fz = f.y.clamp(0.0, (self.map.depth - 1) as f32);
        let (i, j) = (fx.floor() as usize, fz.floor() as usize);
        let (tx, tz) = (fx - i as f32, fz - j as f32);
        let a = self.cell(i, j) + (self.cell(i + 1, j) - self.cell(i, j)) * tx;
        let b = self.cell(i, j + 1) + (self.cell(i + 1, j + 1) - self.cell(i, j + 1)) * tx;
        a + (b - a) * tz
    }

    /// As células que um disco de `radius` em `at` toca (com uma de margem), recortadas ao mapa.
    pub fn rect_around(&self, at: Vec2, radius: f32) -> Option<CellRect> {
        let step = self.step();
        let half = Vec2::splat(self.world_size * 0.5);
        let lo = ((at - Vec2::splat(radius) + half) / step).floor() - Vec2::ONE;
        let hi = ((at + Vec2::splat(radius) + half) / step).ceil() + Vec2::ONE;
        let i0 = lo.x.max(0.0) as usize;
        let j0 = lo.y.max(0.0) as usize;
        let i1 = (hi.x.max(0.0) as usize).min(self.map.width - 1);
        let j1 = (hi.y.max(0.0) as usize).min(self.map.depth - 1);
        (i1 > i0 && j1 > j0).then(|| CellRect {
            i0,
            j0,
            cols: i1 - i0 + 1,
            rows: j1 - j0 + 1,
        })
    }

    /// Os valores crus de um retângulo, codificados (o `terrain_patch` / a inversa exata).
    pub fn snapshot(&self, rect: CellRect) -> String {
        let mut bytes = Vec::with_capacity(rect.cols * rect.rows * 2);
        for j in rect.j0..rect.j0 + rect.rows {
            for i in rect.i0..rect.i0 + rect.cols {
                bytes.extend_from_slice(&self.map.data[j * self.map.width + i].to_le_bytes());
            }
        }
        BASE64.encode(bytes)
    }

    /// Escreve um retângulo de valores crus (de [`Self::snapshot`]).
    pub fn restore(&mut self, rect: CellRect, encoded: &str) -> Result<()> {
        let bytes = BASE64
            .decode(encoded)
            .context("terrain_patch: base64 inválido")?;
        if bytes.len() != rect.cols * rect.rows * 2 {
            bail!(
                "terrain_patch: {} bytes para {}×{} células",
                bytes.len(),
                rect.cols,
                rect.rows
            );
        }
        self.check(rect)?;
        let mut values = bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|b| u16::from_le_bytes(*b));
        for j in rect.j0..rect.j0 + rect.rows {
            for i in rect.i0..rect.i0 + rect.cols {
                self.map.data[j * self.map.width + i] = values.next().expect("tamanho verificado");
            }
        }
        self.dirty = true;
        Ok(())
    }

    fn check(&self, rect: CellRect) -> Result<()> {
        if rect.cols == 0
            || rect.rows == 0
            || rect.i0 + rect.cols > self.map.width
            || rect.j0 + rect.rows > self.map.depth
        {
            bail!(
                "terrain: retângulo {rect:?} fora do mapa {}×{}",
                self.map.width,
                self.map.depth
            );
        }
        Ok(())
    }

    /// Aplica um pincel; devolve o retângulo tocado.
    pub fn brush(
        &mut self,
        mode: BrushMode,
        at: Vec2,
        radius: f32,
        amount: f32,
        height: Option<f32>,
    ) -> Result<CellRect> {
        if !at.is_finite() || !radius.is_finite() || radius <= 0.0 {
            bail!("terrain_brush: centro/raio inválidos");
        }
        if !amount.is_finite() || amount.abs() > EDIT_MAX_DEPTH {
            bail!("terrain_brush: amount fora de ±{EDIT_MAX_DEPTH}");
        }
        let radius = radius.min(EDIT_MAX_RADIUS);
        let rect = self
            .rect_around(at, radius)
            .ok_or_else(|| anyhow!("terrain_brush: fora do mapa"))?;
        let center = self.sample(at.x, at.y);
        let strength = amount.clamp(0.0, 1.0);
        let mut out = Vec::with_capacity(rect.cols * rect.rows);
        for j in rect.j0..rect.j0 + rect.rows {
            for i in rect.i0..rect.i0 + rect.cols {
                let cur = self.cell(i, j);
                let t = self.cell_center(i, j).distance(at) / radius;
                let w = falloff(t);
                let h = match mode {
                    BrushMode::Raise => cur + amount * w,
                    BrushMode::Lower => cur - amount * w,
                    BrushMode::Flatten => cur + (height.unwrap_or(center) - cur) * w * strength,
                    BrushMode::Smooth => {
                        let mut sum = 0.0;
                        for dj in -1i64..=1 {
                            for di in -1i64..=1 {
                                let ni = (i as i64 + di).max(0) as usize;
                                let nj = (j as i64 + dj).max(0) as usize;
                                sum += self.cell(ni, nj);
                            }
                        }
                        cur + (sum / 9.0 - cur) * w * strength
                    }
                    BrushMode::Crater => cur + amount * crater_profile(t),
                    BrushMode::Noise => cur + noise_at(i, j) * amount * w,
                };
                out.push(self.raw(h));
            }
        }
        let mut values = out.into_iter();
        for j in rect.j0..rect.j0 + rect.rows {
            for i in rect.i0..rect.i0 + rect.cols {
                self.map.data[j * self.map.width + i] = values.next().expect("mesmo tamanho");
            }
        }
        self.dirty = true;
        Ok(rect)
    }

    /// As alturas (m) de um retângulo, para o overlay vivo.
    pub fn heights(&self, rect: CellRect) -> Vec<f32> {
        let mut out = Vec::with_capacity(rect.cols * rect.rows);
        for j in rect.j0..rect.j0 + rect.rows {
            for i in rect.i0..rect.i0 + rect.cols {
                out.push(self.cell(i, j));
            }
        }
        out
    }
}

/// Primeiro ponto do raio sobre o terreno (marcha de 0.5 m + bisseção), por uma função de
/// altura — o cursor do pincel no editor e o `viber.scene.pick` no chão.
pub fn ray_ground(
    origin: Vec3,
    dir: Vec3,
    max_distance: f32,
    height: impl Fn(f32, f32) -> f32,
) -> Option<Vec3> {
    const STEP: f32 = 0.5;
    let above = |p: Vec3| p.y - height(p.x, p.z);
    let mut previous = 0.0f32;
    if above(origin) <= 0.0 {
        return Some(origin);
    }
    let mut t = STEP;
    while t <= max_distance {
        if above(origin + dir * t) <= 0.0 {
            let (mut lo, mut hi) = (previous, t);
            for _ in 0..24 {
                let mid = (lo + hi) * 0.5;
                if above(origin + dir * mid) <= 0.0 {
                    hi = mid;
                } else {
                    lo = mid;
                }
            }
            return Some(origin + dir * hi);
        }
        previous = t;
        t += STEP;
    }
    None
}

/// `viber terrain …` — atalhos de agente sobre a op `terrain_brush` (a mesma do editor).
#[derive(Debug, clap::Subcommand)]
pub enum TerrainCommand {
    /// Aplica um pincel ao heightmap pré-gerado (`raise`/`lower` em metros, `flatten`/
    /// `smooth` com força 0..1, `crater` em metros de profundidade)
    Brush {
        #[arg(value_enum)]
        mode: BrushArg,
        /// Centro XZ mundo: "x z" (negativos aceites: `--at "-70 -70"`)
        #[arg(long, allow_hyphen_values = true)]
        at: String,
        #[arg(long, default_value_t = 8.0)]
        radius: f32,
        #[arg(long, default_value_t = 1.0)]
        amount: f32,
        /// Cota alvo do flatten (omisso: a do centro)
        #[arg(long, allow_hyphen_values = true)]
        height: Option<f32>,
        #[arg(long)]
        dry_run: bool,
    },
    /// Pinta o material do terreno (camada do pool: grass, dirt_trail, …;
    /// `amount 0` limpa)
    Paint {
        #[arg(long, allow_hyphen_values = true)]
        at: String,
        #[arg(long, default_value_t = 8.0)]
        radius: f32,
        /// Camada do pool (grass, dirt, gravel, mountain_stone, sand, …)
        #[arg(long)]
        layer: String,
        #[arg(long, default_value_t = 1.0)]
        amount: f32,
        #[arg(long)]
        dry_run: bool,
    },
    /// Altura do heightmap (documento) em XZ mundo
    Sample {
        #[arg(allow_hyphen_values = true)]
        x: f32,
        #[arg(allow_hyphen_values = true)]
        z: f32,
    },
}

#[derive(Debug, Clone, Copy, clap::ValueEnum)]
pub enum BrushArg {
    Raise,
    Lower,
    Flatten,
    Smooth,
    Crater,
    Noise,
}

impl From<BrushArg> for BrushMode {
    fn from(arg: BrushArg) -> Self {
        match arg {
            BrushArg::Raise => BrushMode::Raise,
            BrushArg::Lower => BrushMode::Lower,
            BrushArg::Flatten => BrushMode::Flatten,
            BrushArg::Smooth => BrushMode::Smooth,
            BrushArg::Crater => BrushMode::Crater,
            BrushArg::Noise => BrushMode::Noise,
        }
    }
}

fn brush_op(
    mode: BrushArg,
    at: &str,
    radius: f32,
    amount: f32,
    height: Option<f32>,
) -> Result<super::ops::EditOp> {
    let [x, z] = crate::xml::values::parse_vec2(at, "--at")?;
    Ok(super::ops::EditOp::TerrainBrush {
        mode: mode.into(),
        at: [x, z],
        radius,
        amount,
        height,
    })
}

fn paint_op(at: &str, radius: f32, layer: &str, amount: f32) -> Result<super::ops::EditOp> {
    let [x, z] = crate::xml::values::parse_vec2(at, "--at")?;
    Ok(super::ops::EditOp::TerrainPaint {
        at: [x, z],
        radius,
        layer: layer.to_owned(),
        amount,
    })
}

/// Offline: aplica aos ficheiros do mundo e grava (salvo `dry_run`).
pub fn run_cli(world: &Path, command: TerrainCommand) -> Result<serde_json::Value> {
    let mut doc = super::doc::SceneDoc::load(world)?;
    match command {
        TerrainCommand::Paint {
            at,
            radius,
            layer,
            amount,
            dry_run,
        } => {
            let op = paint_op(&at, radius, &layer, amount)?;
            let tx = super::ops::apply_batch(&mut doc, "terrain paint", &[op])?;
            let files = doc.dirty_paths();
            if !dry_run {
                doc.save()?;
            }
            Ok(super::cli::transaction_json(&doc, &tx, &files, !dry_run))
        }
        TerrainCommand::Sample { x, z } => {
            let terrain = doc.terrain_mut()?;
            Ok(serde_json::json!({ "x": x, "z": z, "height": terrain.sample(x, z) }))
        }
        TerrainCommand::Brush {
            mode,
            at,
            radius,
            amount,
            height,
            dry_run,
        } => {
            let op = brush_op(mode, &at, radius, amount, height)?;
            let tx = super::ops::apply_batch(&mut doc, "terrain brush", &[op])?;
            let files = doc.dirty_paths();
            if !dry_run {
                doc.save()?;
            }
            Ok(super::cli::transaction_json(&doc, &tx, &files, !dry_run))
        }
    }
}

/// Com `--live`: o método e os params para a engine a correr.
pub fn live_request(command: &TerrainCommand) -> Result<(&'static str, serde_json::Value)> {
    match command {
        TerrainCommand::Sample { .. } => bail!(
            "`terrain sample` lê o documento — corre sem --live (grava antes com `viber scene --live save`)"
        ),
        TerrainCommand::Paint { dry_run: true, .. } => bail!("--dry-run não existe com --live"),
        TerrainCommand::Paint {
            at,
            radius,
            layer,
            amount,
            ..
        } => {
            let op = paint_op(at, *radius, layer, *amount)?;
            Ok((
                "viber.scene.apply",
                serde_json::json!({ "ops": [op], "label": "terrain paint" }),
            ))
        }
        TerrainCommand::Brush { dry_run: true, .. } => bail!("--dry-run não existe com --live"),
        TerrainCommand::Brush {
            mode,
            at,
            radius,
            amount,
            height,
            ..
        } => {
            let op = brush_op(*mode, at, *radius, *amount, *height)?;
            Ok((
                "viber.scene.apply",
                serde_json::json!({ "ops": [op], "label": "terrain brush" }),
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 65×65 células a 10 m, sobre 64 m de mundo (passo 1 m), máx 100 m.
    fn doc() -> TerrainDoc {
        TerrainDoc {
            path: PathBuf::from("t.ahgt"),
            map: HeightMapU16::filled(65, 65, (10.0 / 100.0 * u16::MAX as f32) as u16),
            meta: AhgtMeta::default(),
            world_size: 64.0,
            max_height: 100.0,
            dirty: false,
            paint: crate::terrain::paint::PaintGrid::new(64.0, 65),
            paint_dirty: false,
        }
    }

    #[test]
    fn raise_peaks_at_the_center_and_fades_out() {
        let mut t = doc();
        let rect = t
            .brush(BrushMode::Raise, Vec2::ZERO, 8.0, 4.0, None)
            .unwrap();
        assert!(
            (t.sample(0.0, 0.0) - 14.0).abs() < 0.01,
            "{}",
            t.sample(0.0, 0.0)
        );
        assert!(
            (t.sample(9.0, 0.0) - 10.0).abs() < 0.01,
            "fora do raio fica"
        );
        assert!(t.sample(4.0, 0.0) > 10.0 && t.sample(4.0, 0.0) < 14.0);
        assert!(rect.cols <= 20 && t.dirty);
    }

    #[test]
    fn snapshot_restore_is_exact() {
        let mut t = doc();
        let rect = t.rect_around(Vec2::new(3.0, -2.0), 6.0).unwrap();
        let before = t.snapshot(rect);
        let original = t.map.clone();
        t.brush(BrushMode::Crater, Vec2::new(3.0, -2.0), 6.0, 5.0, None)
            .unwrap();
        assert_ne!(t.map, original);
        t.restore(rect, &before).unwrap();
        assert_eq!(t.map, original);
    }

    #[test]
    fn flatten_and_smooth_pull_toward_their_targets() {
        let mut t = doc();
        t.brush(BrushMode::Raise, Vec2::ZERO, 6.0, 8.0, None)
            .unwrap();
        t.brush(BrushMode::Flatten, Vec2::ZERO, 10.0, 1.0, Some(12.0))
            .unwrap();
        assert!(
            (t.sample(0.0, 0.0) - 12.0).abs() < 0.05,
            "{}",
            t.sample(0.0, 0.0)
        );
        let peak = t.sample(0.0, 0.0);
        t.brush(BrushMode::Smooth, Vec2::new(30.0, 30.0), 4.0, 1.0, None)
            .unwrap();
        assert!(
            (t.sample(0.0, 0.0) - peak).abs() < 1e-3,
            "smooth longe não toca"
        );
    }

    #[test]
    fn rejects_bad_requests() {
        let mut t = doc();
        assert!(
            t.brush(BrushMode::Raise, Vec2::NAN, 4.0, 1.0, None)
                .is_err()
        );
        assert!(
            t.brush(BrushMode::Raise, Vec2::ZERO, 0.0, 1.0, None)
                .is_err()
        );
        assert!(
            t.brush(BrushMode::Raise, Vec2::ZERO, 4.0, 1000.0, None)
                .is_err()
        );
        assert!(
            t.brush(BrushMode::Raise, Vec2::splat(5000.0), 4.0, 1.0, None)
                .is_err()
        );
        let rect = t.rect_around(Vec2::ZERO, 2.0).unwrap();
        assert!(t.restore(rect, "AAAA").is_err());
    }

    #[test]
    fn ray_hits_the_ground() {
        let t = doc();
        let hit = ray_ground(
            Vec3::new(0.0, 50.0, 0.0),
            Vec3::new(0.0, -1.0, 0.0),
            100.0,
            |x, z| t.sample(x, z),
        )
        .unwrap();
        assert!((hit.y - 10.0).abs() < 0.01);
        assert!(
            ray_ground(Vec3::new(0.0, 50.0, 0.0), Vec3::Y, 100.0, |x, z| t
                .sample(x, z))
            .is_none()
        );
    }
}
