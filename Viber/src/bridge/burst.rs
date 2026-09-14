//! Burst de screenshots — N frames do render compostos numa ÚNICA folha
//! (grid √N, row-major) para QA de movimento/flicker sem N round-trips nem
//! N ficheiros soltos. As células têm o MESMO formato do frame — sem
//! letterbox: a folha tem 4096 no lado comprido (frames 16:9 → folha
//! 4096×2304, seja 2×2, 3×3 ou 4×4). O frame skip é configurável: `skip`
//! é o número de frames RENDERIZADOS entre capturas (`0` = consecutivos;
//! cada +1 estica o intervalo de tempo coberto pela mesma folha).
//!
//! Pipeline: o handler BRP enfileira em [`BurstStore`]; o sistema
//! `Update` do bridge spawna UMA entidade `Screenshot` por frame (o render
//! só aceita 1 target de janela por frame — duplicado é despawnado com
//! warn), o observer de `ScreenshotCaptured` deposita a imagem CRUA (sem
//! encode PNG por frame) e, quando a última chega, a composição + encode
//! correm numa thread (a folha 4096² não pode parar o frame).

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Instant;

use base64::Engine as _;
use bevy::ecs::observer::On;
use bevy::ecs::world::World;
use bevy::log::warn;
use bevy::render::view::screenshot::{Screenshot, ScreenshotCaptured};
use image::{ImageEncoder as _, RgbaImage};
use serde::Serialize;

use super::BridgeShared;

/// Lado comprido da folha composta — o contrato pedido: uma imagem 4096.
/// As células têm o formato do frame, portanto o outro lado é 4096 ×
/// (fh/fw) × grid (16:9 → 2304).
pub const SHEET_SIZE: u32 = 4096;

/// Contagens de frames aceites: `4` → 2×2, `9` → 3×3, `16` → 4×4.
pub const ALLOWED_FRAMES: [u32; 3] = [4, 9, 16];

/// Frames por omissão do `viber.burst` / `viber debug burst`.
pub const DEFAULT_FRAMES: u32 = 9;

/// Cap do `skip` — 600 frames entre capturas é ~10 s a 60 fps; mais do que
/// isso é um erro de unidade (ms em vez de frames) e devia falhar cedo.
pub const MAX_SKIP: u32 = 600;

/// Capturas em voo por burst — cada `Screenshot` vivo segura textura de
/// render + buffer de readback do tamanho da janela até o map_async
/// completar; sem teto, um `skip=0` podia empilhar 16 delas se o readback
/// atolasse. Com o teto, `skip=0` segue a cadência do readback (1-2 frames),
/// que é o "consecutivos" possível.
pub const MAX_IN_FLIGHT: u32 = 4;

/// Burst sem progresso (spawn nem deposit) há mais disto → erro. Engine
/// minimizada/pausada não captura; sem o guard o cliente girava até ao
/// timeout sem saber o motivo.
pub const STALL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// Burst retidos em memória (o `png_base64` da folha 4096² pesa dezenas de
/// MB) — o PNG em disco fica.
const RETAINED: u64 = 4;

/// Fundo da folha (entre frames e nas letterbox bars).
const BG: image::Rgba<u8> = image::Rgba([11, 11, 14, 255]);
/// Linhas de separação entre células.
const SEP: image::Rgba<u8> = image::Rgba([42, 42, 50, 255]);

/// Geometria da folha — ecoada no status para o cliente imprimir.
#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
pub struct SheetLayout {
    pub sheet_w: u32,
    pub sheet_h: u32,
    pub grid: u32,
    pub cell_w: u32,
    pub cell_h: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BurstStatus {
    /// A spawnar capturas (uma por frame).
    Capturing,
    /// Todas as frames chegaram; thread de composição a trabalhar.
    Composing,
    /// Folha codificada (e escrita em disco).
    Captured,
    /// Falhou — `error` diz porquê.
    Error,
}

impl BurstStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            BurstStatus::Capturing => "capturing",
            BurstStatus::Composing => "composing",
            BurstStatus::Captured => "captured",
            BurstStatus::Error => "error",
        }
    }

    /// Um burst activo NUNCA é evitado do store (o id alto já protege; isto
    /// é para o caso patológico de 5+ bursts concorrentes).
    fn active(self) -> bool {
        !matches!(self, BurstStatus::Captured | BurstStatus::Error)
    }
}

/// Resultado de um burst capturado.
#[derive(Clone, Serialize)]
pub struct BurstResult {
    pub bytes: usize,
    pub png_base64: String,
    pub layout: SheetLayout,
}

/// Estatística de LUMA de um frame capturado (`mean`/`std` 0..255) — o
/// veredicto numérico do flicker sem olhar para a folha (`--stats`).
#[derive(Debug, Clone, Copy, Serialize)]
pub struct FrameStat {
    pub index: u32,
    pub mean: f32,
    pub std: f32,
}

/// Um burst em curso — ver documentação do módulo.
pub struct BurstState {
    /// `Some` por slot (ordem de depósito; huecos só se fora de ordem).
    pub frame_stats: Vec<Option<FrameStat>>,
    pub id: u64,
    pub frames: u32,
    pub skip: u32,
    pub path: PathBuf,
    pub status: BurstStatus,
    /// Capturas já spawnadas (uma por frame renderizado, no máximo).
    pub spawned: u32,
    /// Frames renderizados restantes até à próxima spawn (pacing do skip).
    pub cooldown: u32,
    /// Frames crus por slot — `None` até o observer depositar.
    collected: Vec<Option<RgbaImage>>,
    pub collected_count: u32,
    pub last_progress: Instant,
    pub result: Option<BurstResult>,
    pub error: Option<String>,
}

/// Guarda dos bursts — partilhado entre handlers BRP (PreUpdate/RemoteLast)
/// e o sistema `Update` do bridge, via `Arc<Mutex<…>>` (os observers do
/// `ScreenshotCaptured` correm no Update do bevy e depositam por aqui).
#[derive(Default)]
pub struct BurstStore {
    next_id: u64,
    bursts: BTreeMap<u64, BurstState>,
}

impl BurstStore {
    /// Valida e cria um burst. `output` ausente → PNG no dir temporário das
    /// capturas do processo (o PNG em disco fica, como nos screenshots).
    pub fn request(
        &mut self,
        frames: u32,
        skip: u32,
        output: Option<PathBuf>,
    ) -> Result<(u64, PathBuf), String> {
        if !ALLOWED_FRAMES.contains(&frames) {
            return Err(format!(
                "frames `{frames}` inválido — aceites 4, 9 ou 16 (grids 2×2, 3×3, 4×4)"
            ));
        }
        if skip > MAX_SKIP {
            return Err(format!("skip `{skip}` acima do teto de {MAX_SKIP} frames"));
        }
        self.next_id += 1;
        let id = self.next_id;
        let dir = std::env::temp_dir().join(format!("viber-bridge-{}", std::process::id()));
        if let Err(error) = std::fs::create_dir_all(&dir) {
            warn!("bridge: falha ao criar {} para bursts: {error}", dir.display());
        }
        let path = output.unwrap_or_else(|| dir.join(format!("burst-{id}.png")));
        self.bursts.insert(
            id,
            BurstState {
                id,
                frames,
                skip,
                path: path.clone(),
                status: BurstStatus::Capturing,
                spawned: 0,
                cooldown: 0,
                collected: vec![None; frames as usize],
                frame_stats: Vec::new(),
                collected_count: 0,
                last_progress: Instant::now(),
                result: None,
                error: None,
            },
        );
        let cutoff = id.saturating_sub(RETAINED);
        self.bursts
            .retain(|key, burst| *key > cutoff || burst.status.active());
        Ok((id, path))
    }

    pub fn get(&self, id: u64) -> Option<&BurstState> {
        self.bursts.get(&id)
    }

    /// Tick de pacing do `Update` — devolve `(id, slot)` se UMA captura deve
    /// ser spawnada neste frame. Cadência: captura a cada `skip + 1` frames
    /// (`skip=0` → consecutivos, limitados pelo readback via MAX_IN_FLIGHT).
    /// O chamador garante ≤1 spawn por frame no total (single-shots incluídos).
    pub fn next_capture(&mut self) -> Option<(u64, u32)> {
        for burst in self.bursts.values_mut() {
            if burst.status == BurstStatus::Capturing {
                burst.cooldown = burst.cooldown.saturating_sub(1);
            }
        }
        let chosen = self.bursts.iter().find_map(|(id, burst)| {
            if burst.status != BurstStatus::Capturing
                || burst.spawned >= burst.frames
                || burst.cooldown > 0
                || burst.spawned - burst.collected_count >= MAX_IN_FLIGHT
            {
                None
            } else {
                Some((*id, burst.spawned))
            }
        })?;
        let burst = self.bursts.get_mut(&chosen.0).expect("escolhido existe");
        burst.spawned += 1;
        // skip+1: spawn no frame F, `skip` frames de pausa, próxima spawn no
        // F+skip+1 — "captura a cada skip+1 frames renderizados".
        burst.cooldown = burst.skip + 1;
        burst.last_progress = Instant::now();
        Some(chosen)
    }

    /// Deposit do observer — devolve `Ok(true)` quando o burst ficou
    /// completo (todas as frames depositadas).
    pub fn deposit(&mut self, id: u64, slot: u32, image: RgbaImage) -> Result<bool, String> {
        let Some(burst) = self.bursts.get_mut(&id) else {
            // Evitado/terminado entretanto — a captura órfã ignora-se.
            return Ok(false);
        };
        if burst.status != BurstStatus::Capturing {
            return Ok(false);
        }
        let Some(cell) = burst.collected.get_mut(slot as usize) else {
            return Err(format!("burst {id}: slot {slot} fora da folha"));
        };
        // Estatística POR FRAME no momento do readback (luma média + desvio):
        // é o veredicto numérico do flicker — dois frames do mesmo mundo
        // parado devem ter mean≈ igual e std≈ igual; oscilação entre células
        // = flicker (VIBER_POSTFX_ORDER_FLICKER.md). No deposit porque é
        // thread-side: não custa frame time.
        let (mean, std) = image_luma_stats(&image);
        if burst.frame_stats.len() != slot as usize {
            // fora de ordem: preencher até ao slot para manter o índice
            while burst.frame_stats.len() < slot as usize {
                burst.frame_stats.push(None);
            }
        }
        burst.frame_stats.push(Some(FrameStat { index: slot, mean, std }));
        *cell = Some(image);
        burst.collected_count += 1;
        burst.last_progress = Instant::now();
        Ok(burst.collected_count == burst.frames)
    }

    /// Ids completos ainda `Capturing` — prontos para composição.
    fn ready_ids(&self) -> Vec<u64> {
        self.bursts
            .values()
            .filter(|burst| {
                burst.status == BurstStatus::Capturing && burst.collected_count == burst.frames
            })
            .map(|burst| burst.id)
            .collect()
    }

    /// Move TODOS os bursts completos para `Composing`; devolve
    /// `(id, células, caminho de saída)` para a thread de composição.
    pub fn take_ready(&mut self) -> Vec<(u64, Vec<RgbaImage>, PathBuf)> {
        self.ready_ids()
            .into_iter()
            .filter_map(|id| self.take_cells(id).map(|(cells, path)| (id, cells, path)))
            .collect()
    }

    /// Move as frames recolhidas para a composição (`Capturing` →
    /// `Composing`); `None` se o burst não está completo.
    pub fn take_cells(&mut self, id: u64) -> Option<(Vec<RgbaImage>, PathBuf)> {
        let burst = self.bursts.get_mut(&id)?;
        if burst.status != BurstStatus::Capturing || burst.collected_count != burst.frames {
            return None;
        }
        burst.status = BurstStatus::Composing;
        let cells = std::mem::take(&mut burst.collected)
            .into_iter()
            .map(|cell| cell.expect("burst completo tem todos os slots"))
            .collect();
        Some((cells, burst.path.clone()))
    }

    /// Marca a folha codificada (chamado pela thread de composição).
    pub fn finish(&mut self, id: u64, bytes: usize, png_base64: String, layout: SheetLayout) {
        let Some(burst) = self.bursts.get_mut(&id) else {
            return; // evitado entretanto — resultado largado, o ficheiro ficou
        };
        burst.status = BurstStatus::Captured;
        burst.result = Some(BurstResult {
            bytes,
            png_base64,
            layout,
        });
    }

    /// Marca um erro (não sobrescreve um `Captured` — a thread pode perder a
    /// corrida ao guard de stall).
    pub fn fail(&mut self, id: u64, message: String) -> Option<String> {
        let burst = self.bursts.get_mut(&id)?;
        if burst.status.active() {
            burst.status = BurstStatus::Error;
            burst.error = Some(message.clone());
            Some(message)
        } else {
            None
        }
    }

    /// Falha bursts sem progresso há mais de [`STALL_TIMEOUT`]; devolve as
    /// mensagens para o log do sistema.
    pub fn fail_stalled(&mut self) -> Vec<String> {
        let stalled: Vec<u64> = self
            .bursts
            .values()
            .filter(|burst| {
                burst.status == BurstStatus::Capturing
                    && burst.last_progress.elapsed() > STALL_TIMEOUT
            })
            .map(|burst| burst.id)
            .collect();
        stalled
            .into_iter()
            .filter_map(|id| {
                self.fail(id, "burst parou sem progresso (janela minimizada? render pausado?)".into())
            })
            .collect()
    }
}

/// Grid √N — `4`→2, `9`→3, `16`→4; `None` fora da lista aceite.
/// Luma média + desvio-padrão (0..255) de uma imagem RGBA — subamostragem
/// de 1 em 4 píxeis em x e y (a estatística de flicker não precisa do
/// frame inteiro; ¼×¼ mantém o custo de deposit irrelevante).
pub fn image_luma_stats(image: &RgbaImage) -> (f32, f32) {
    let (width, height) = (image.width(), image.height());
    let mut sum = 0.0_f64;
    let mut sum_sq = 0.0_f64;
    let mut count = 0_u64;
    for y in (0..height).step_by(4) {
        for x in (0..width).step_by(4) {
            let pixel = image.get_pixel(x, y);
            // Rec.709 luma
            let luma = 0.2126 * f64::from(pixel[0])
                + 0.7152 * f64::from(pixel[1])
                + 0.0722 * f64::from(pixel[2]);
            sum += luma;
            sum_sq += luma * luma;
            count += 1;
        }
    }
    if count == 0 {
        return (0.0, 0.0);
    }
    let mean = (sum / count as f64) as f32;
    let variance = (sum_sq / count as f64) - f64::from(mean * mean);
    let std = (variance.max(0.0)).sqrt() as f32;
    (mean, std)
}

pub fn grid_for(frames: u32) -> Option<u32> {
    if !ALLOWED_FRAMES.contains(&frames) {
        return None;
    }
    Some((frames as f64).sqrt().round() as u32)
}

/// Guard do mutex — o padrão do bridge (`PoisonError::into_inner`).
fn lock<T>(mutex: &std::sync::Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Coração do burst no sistema `Update` do bridge (chamado por
/// `process_capture_requests`, que passa `slot_taken` quando já spawnoU um
/// single-shot neste frame):
///
/// 1. spawna NO MÁX UMA entidade `Screenshot` por frame (o `extract` do
///    bevy rejeita duplicados da mesma janela), com um observer que deposita
///    a imagem CRUA no store — sem encode PNG por frame;
/// 2. lança a thread de composição para os bursts completos (a folha 4096²
///    não pode parar o frame);
/// 3. falha bursts sem progresso há mais de [`STALL_TIMEOUT`].
pub fn drive(world: &mut World, slot_taken: bool) {
    if !slot_taken {
        let next = {
            let shared = world.resource::<BridgeShared>();
            let mut store = lock(&shared.bursts);
            store.next_capture()
        };
        if let Some((id, slot)) = next {
            let store = world.resource::<BridgeShared>().bursts.clone();
            world
                .spawn(Screenshot::primary_window())
                .observe(move |trigger: On<ScreenshotCaptured>| {
                    let image = trigger.image.clone();
                    let store = store.clone();
                    let outcome = image
                        .try_into_dynamic()
                        .map(|dynamic| dynamic.to_rgba8())
                        .map_err(|error| format!("conversão do frame falhou: {error}"))
                        .and_then(|frame| {
                            let mut store = lock(&store);
                            store.deposit(id, slot, frame)
                        });
                    if let Err(message) = outcome {
                        let mut store = lock(&store);
                        if let Some(message) = store.fail(id, message) {
                            warn!("bridge: burst falhou: {message}");
                        }
                    }
                });
        }
    }

    let jobs = {
        let shared = world.resource::<BridgeShared>();
        let mut store = lock(&shared.bursts);
        store.take_ready()
    };
    for (id, cells, path) in jobs {
        let store = world.resource::<BridgeShared>().bursts.clone();
        std::thread::spawn(move || {
            let outcome = compose(&cells, SHEET_SIZE)
                .and_then(|(sheet, layout)| encode_png(&sheet).map(|bytes| (bytes, layout)));
            let mut store = lock(&store);
            match outcome {
                Ok((bytes, layout)) => {
                    if let Err(error) = std::fs::write(&path, &bytes) {
                        let message =
                            format!("falha ao escrever {}: {error}", path.display());
                        if let Some(message) = store.fail(id, message) {
                            warn!("bridge: burst falhou: {message}");
                        }
                    } else {
                        let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
                        store.finish(id, bytes.len(), encoded, layout);
                    }
                }
                Err(message) => {
                    if let Some(message) = store.fail(id, message) {
                        warn!("bridge: burst falhou: {message}");
                    }
                }
            }
        });
    }

    let stalled = {
        let shared = world.resource::<BridgeShared>();
        let mut store = lock(&shared.bursts);
        store.fail_stalled()
    };
    for message in stalled {
        warn!("bridge: burst: {message}");
    }
}

/// Dígito 3×5 (3 bits por linha, MSB à esquerda) — carimbo do índice do
/// frame no canto de cada célula, para ler a ordem sem abrir a folha ao pé.
const DIGITS: [[u8; 5]; 10] = [
    [0b010, 0b101, 0b101, 0b101, 0b010], // 0
    [0b010, 0b110, 0b010, 0b010, 0b111], // 1
    [0b111, 0b001, 0b010, 0b100, 0b111], // 2
    [0b111, 0b001, 0b011, 0b001, 0b111], // 3
    [0b101, 0b101, 0b111, 0b001, 0b001], // 4
    [0b111, 0b100, 0b111, 0b001, 0b111], // 5
    [0b111, 0b100, 0b111, 0b101, 0b111], // 6
    [0b111, 0b001, 0b010, 0b010, 0b010], // 7
    [0b111, 0b101, 0b111, 0b101, 0b111], // 8
    [0b111, 0b101, 0b111, 0b001, 0b111], // 9
];

/// Escreve um pixel com clamp — o carimbo vive no canto da célula e nunca
/// devia sair da folha, mas um clamp barato vale mais do que um panic.
fn plot(sheet: &mut RgbaImage, x: u32, y: u32, color: image::Rgba<u8>) {
    if x < sheet.width() && y < sheet.height() {
        sheet.put_pixel(x, y, color);
    }
}

/// Carimba `index` (1-based) no canto superior esquerdo da célula em
/// `(cell_x, cell_y)` de largura `cell`: rect preto + dígitos brancos, por
/// cima do frame (sem letterbox, o canto é do conteúdo — o rect preto
/// mantém o número legível em qualquer fundo). A escala adapta-se à célula
/// (produção: cell ≥ 1024 → 6).
fn stamp_index(sheet: &mut RgbaImage, cell_x: u32, cell_y: u32, cell: u32, index: u32) {
    let text = index.to_string();
    let scale = (cell / (4 * text.len() as u32 + 2)).clamp(1, 6);
    let glyph_w = 4 * scale; // 3 colunas + 1 de espaço
    let x0 = cell_x + scale;
    let y0 = cell_y + scale;
    let pad = scale;
    let bg_w = text.len() as u32 * glyph_w + pad * 2;
    let bg_h = 5 * scale + pad * 2;
    for dy in 0..bg_h {
        for dx in 0..bg_w {
            plot(sheet, x0 + dx, y0 + dy, image::Rgba([0, 0, 0, 255]));
        }
    }
    for (i, char) in text.chars().enumerate() {
        let Some(digit) = char.to_digit(10) else { continue };
        let glyph = &DIGITS[digit as usize];
        for (row, bits) in glyph.iter().enumerate() {
            for col in 0..3 {
                if bits & (0b100 >> col) == 0 {
                    continue;
                }
                for dy in 0..scale {
                    for dx in 0..scale {
                        plot(
                            sheet,
                            x0 + pad + i as u32 * glyph_w + col * scale + dx,
                            y0 + pad + row as u32 * scale + dy,
                            image::Rgba([255, 255, 255, 255]),
                        );
                    }
                }
            }
        }
    }
}

/// Compõe a folha: grid √N × √N de células com o MESMO formato do frame
/// (sem letterbox — cada frame é reescalado a preencher a célula inteira),
/// índice carimbado no canto e linhas de separação. A folha tem
/// `long_side` no lado comprido; o formato vem do PRIMEIRO frame (a janela
/// não muda de tamanho no meio de um burst). `long_side` parametrizável
/// para os testes.
pub fn compose(cells: &[RgbaImage], long_side: u32) -> Result<(RgbaImage, SheetLayout), String> {
    let frames = cells.len() as u32;
    let Some(grid) = grid_for(frames) else {
        return Err(format!("{frames} frames não formam grid (aceites 4, 9, 16)"));
    };
    let first = cells.first().ok_or("burst sem frames")?;
    let (fw, fh) = (first.width(), first.height());
    if fw == 0 || fh == 0 {
        return Err("frame com dimensão 0 (janela minimizada?)".into());
    }
    if long_side < grid {
        return Err(format!("folha {long_side} pequena demais para {grid}×{grid}"));
    }
    // Células com o formato do frame: o lado comprido da célula é
    // long_side/grid e o outro sai da razão do frame (arredondado — a
    // distorção é sub-pixel).
    let (cell_w, cell_h, sheet_w, sheet_h) = if fw >= fh {
        let cell_w = long_side / grid;
        let cell_h = ((cell_w as u64 * fh as u64 + fw as u64 / 2) / fw as u64).max(1) as u32;
        (cell_w, cell_h, long_side, cell_h * grid)
    } else {
        let cell_h = long_side / grid;
        let cell_w = ((cell_h as u64 * fw as u64 + fh as u64 / 2) / fh as u64).max(1) as u32;
        (cell_w, cell_h, cell_w * grid, long_side)
    };
    let mut sheet = RgbaImage::from_pixel(sheet_w, sheet_h, BG);
    for (index, frame) in cells.iter().enumerate() {
        let (w, h) = (frame.width(), frame.height());
        if w == 0 || h == 0 {
            return Err("frame com dimensão 0 (janela minimizada?)".into());
        }
        let resized =
            image::imageops::resize(frame, cell_w, cell_h, image::imageops::FilterType::Triangle);
        let x = (index as u32 % grid) * cell_w;
        let y = (index as u32 / grid) * cell_h;
        image::imageops::overlay(&mut sheet, &resized, x as i64, y as i64);
        let stamp_cell = cell_w.min(cell_h);
        stamp_index(
            &mut sheet,
            x,
            y,
            stamp_cell,
            index as u32 + 1,
        );
    }
    for k in 1..grid {
        let line_w = k * cell_w;
        let line_h = k * cell_h;
        for width in 0..2u32 {
            for y in 0..sheet_h {
                plot(&mut sheet, (line_w + width).min(sheet_w - 1), y, SEP);
            }
            for x in 0..sheet_w {
                plot(&mut sheet, x, (line_h + width).min(sheet_h - 1), SEP);
            }
        }
    }
    Ok((
        sheet,
        SheetLayout {
            sheet_w,
            sheet_h,
            grid,
            cell_w,
            cell_h,
        },
    ))
}

/// Encode PNG rápido (Fast/Adaptive — QA, não arquivo; a folha 4096² com a
/// compressão default custaria segundos de CPU).
pub fn encode_png(sheet: &RgbaImage) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    let encoder = image::codecs::png::PngEncoder::new_with_quality(
        &mut bytes,
        image::codecs::png::CompressionType::Fast,
        image::codecs::png::FilterType::Adaptive,
    );
    encoder
        .write_image(
            sheet.as_raw(),
            sheet.width(),
            sheet.height(),
            image::ExtendedColorType::Rgba8,
        )
        .map_err(|error| format!("encode PNG da folha falhou: {error}"))?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::Rgba;

    fn solid(color: [u8; 4], w: u32, h: u32) -> RgbaImage {
        RgbaImage::from_pixel(w, h, Rgba(color))
    }

    #[test]
    fn test_grid_for_aceites_e_rejeitados() {
        assert_eq!(grid_for(4), Some(2));
        assert_eq!(grid_for(9), Some(3));
        assert_eq!(grid_for(16), Some(4));
        assert_eq!(grid_for(5), None);
        assert_eq!(grid_for(1), None);
        assert_eq!(grid_for(64), None);
    }

    #[test]
    fn test_request_valida_frames_e_skip() {
        let mut store = BurstStore::default();
        let (id, _path) = store.request(9, 0, None).expect("9 frames é válido");
        assert_eq!(store.get(id).expect("existe").frames, 9);
        assert_eq!(
            store.request(5, 0, None).unwrap_err(),
            "frames `5` inválido — aceites 4, 9 ou 16 (grids 2×2, 3×3, 4×4)"
        );
        assert!(store.request(16, MAX_SKIP + 1, None).is_err(), "skip acima do teto falha");
        assert!(store.request(16, MAX_SKIP, None).is_ok(), "skip no teto passa");
    }

    #[test]
    fn test_pacing_captura_a_cada_skip_mais_um() {
        let mut store = BurstStore::default();
        let (id, _) = store.request(4, 2, None).expect("burst");
        // tick 1: spawn imediato; ticks 2-3: cooldown 3; tick 4: 2.ª spawn —
        // capturas nos frames 1 e 4 = "a cada skip+1 = 3 frames".
        assert_eq!(store.next_capture(), Some((id, 0)), "1.ª captura é imediata");
        assert_eq!(store.next_capture(), None, "cooldown — tick sem spawn");
        assert_eq!(store.next_capture(), None, "cooldown — tick sem spawn");
        assert_eq!(
            store.next_capture(),
            Some((id, 1)),
            "a cada skip+1=3 frames há spawn"
        );
        // Esgota as restantes slots (cooldown continua a contar entre spawns).
        for expected_slot in 2..4u32 {
            let mut spawned_here = false;
            for _ in 0..16 {
                if store.next_capture() == Some((id, expected_slot)) {
                    spawned_here = true;
                    break;
                }
            }
            assert!(spawned_here, "slot {expected_slot} devia spawnar em ~3 ticks");
        }
        assert_eq!(
            store.next_capture(),
            None,
            "todas as frames spawnadas — nada mais a fazer"
        );
    }

    #[test]
    fn test_skip_zero_e_in_flight() {
        let mut store = BurstStore::default();
        let (id, _) = store.request(16, 0, None).expect("burst");
        // skip=0: spawn em TODOS os ticks até MAX_IN_FLIGHT travar.
        for slot in 0..MAX_IN_FLIGHT {
            assert_eq!(
                store.next_capture(),
                Some((id, slot)),
                "skip=0 é um tick por captura"
            );
        }
        assert_eq!(
            store.next_capture(),
            None,
            "MAX_IN_FLIGHT trava enquanto nada é depositado"
        );
        // Um deposit liberta uma vaga — a próxima spawn é o slot seguinte.
        assert!(!store.deposit(id, 0, solid([1, 2, 3, 255], 4, 4)).expect("deposit"));
        assert_eq!(store.next_capture(), Some((id, MAX_IN_FLIGHT)));
    }

    #[test]
    fn test_deposit_completa_e_take_cells_muda_estado() {
        let mut store = BurstStore::default();
        let (id, _) = store.request(4, 0, None).expect("burst");
        for slot in 0..3 {
            assert!(!store.deposit(id, slot, solid([0, 255, 0, 255], 8, 8)).expect("deposit"));
        }
        assert!(store.deposit(id, 3, solid([0, 0, 255, 255], 8, 8)).expect("deposit"));
        let (cells, _path) = store.take_cells(id).expect("completo → células");
        assert_eq!(cells.len(), 4);
        assert_eq!(store.get(id).expect("existe").status, BurstStatus::Composing);
        assert!(
            store.take_cells(id).is_none(),
            "2.º take não repete (Composing já não é Capturing)"
        );
    }

    #[test]
    fn test_compose_layout_row_major_sem_letterbox() {
        // 4 frames 32×18 (16:9) numa folha de lado comprido 512 → células
        // 256×144 (MESMO formato do frame, sem barras), folha 512×288.
        let colors = [[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255], [255, 255, 0, 255]];
        let cells: Vec<RgbaImage> = colors.iter().map(|c| solid(*c, 32, 18)).collect();
        let (sheet, layout) = compose(&cells, 512).expect("compose");
        assert_eq!(
            layout,
            SheetLayout { sheet_w: 512, sheet_h: 288, grid: 2, cell_w: 256, cell_h: 144 }
        );
        assert_eq!((sheet.width(), sheet.height()), (512, 288));
        // Row-major: vermelho na célula 0 (canto sup esq), verde à direita,
        // azul em baixo, amarelo no canto inf direito.
        assert_eq!(sheet.get_pixel(128, 72), &Rgba([255, 0, 0, 255]));
        assert_eq!(sheet.get_pixel(384, 72), &Rgba([0, 255, 0, 255]));
        assert_eq!(sheet.get_pixel(128, 216), &Rgba([0, 0, 255, 255]));
        assert_eq!(sheet.get_pixel(384, 216), &Rgba([255, 255, 0, 255]));
        // Sem letterbox: até no canto inferior direito da célula 0 é frame.
        assert_eq!(sheet.get_pixel(250, 140), &Rgba([255, 0, 0, 255]));
        // Linha de separação vertical no x=256.
        assert_eq!(sheet.get_pixel(256, 100), &SEP, "separador entre colunas");
    }

    #[test]
    fn test_compose_portrait_folha_deitada_na_altura() {
        // Frames em retrato (18×32): o lado comprido de 512 vai para a
        // ALTURA da folha — células 96×170, folha 288×512.
        let cells = vec![solid([10, 200, 30, 255], 18, 32); 9];
        let (sheet, layout) = compose(&cells, 512).expect("compose");
        assert_eq!(
            layout,
            SheetLayout { sheet_w: 288, sheet_h: 512, grid: 3, cell_w: 96, cell_h: 170 }
        );
        assert_eq!((sheet.width(), sheet.height()), (288, 512));
        assert_eq!(sheet.get_pixel(48, 85), &Rgba([10, 200, 30, 255]));
    }

    #[test]
    fn test_compose_carimbo_indice_no_canto() {
        let cells = vec![solid([90, 90, 90, 255], 32, 18); 4];
        let (sheet, _layout) = compose(&cells, 512).expect("compose");
        // Sem letterbox o carimbo pinta por cima do frame no canto (0,0):
        // rect preto + dígitos brancos têm de existir junto à origem.
        let mut has_black = false;
        for y in 0..20 {
            for x in 0..40 {
                if sheet.get_pixel(x, y) == &Rgba([0, 0, 0, 255]) {
                    has_black = true;
                }
            }
        }
        assert!(has_black, "carimbo do índice devia estar no canto da célula");
    }

    #[test]
    fn test_encode_png_produz_assinatura_valida() {
        let cells = vec![solid([200, 200, 200, 255], 16, 16); 4];
        let (sheet, _) = compose(&cells, 32).expect("compose");
        let bytes = encode_png(&sheet).expect("encode");
        assert!(bytes.starts_with(b"\x89PNG\r\n\x1a\n"), "assinatura PNG");
        assert!(super::super::png_complete(&bytes), "PNG completo");
    }

    #[test]
    fn test_fail_stalled_so_toca_capturing_antigo() {
        let mut store = BurstStore::default();
        let (id, _) = store.request(4, 0, None).expect("burst");
        // Sem progresso desde o request — mas ainda dentro do timeout.
        assert!(store.fail_stalled().is_empty(), "recém-criado não está parado");
        // Força o relógio: deposita e usa um request velho via fail directo.
        let _ = store.deposit(id, 0, solid([1, 2, 3, 255], 4, 4));
        let msg = store.fail(id, "x".into());
        assert_eq!(msg.as_deref(), Some("x"));
        // fail não sobrescreve estado terminal:
        assert_eq!(store.fail(id, "y".into()), None);
        assert_eq!(store.get(id).expect("existe").error.as_deref(), Some("x"));
    }
}
