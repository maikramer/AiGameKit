//! Diff de píxeis nativo (`viber debug diff a.png b.png`) — a asserção
//! visual NUMÉRICA que faltava ao protocolo de QA: o estudo do flicker
//! (VIBER_POSTFX_ORDER_FLICKER.md) mediu "média de N screenshots" à mão,
//! fora do repo. Golden images + exit code → regressão visual em CI.
//!
//! Métricas sobre o delta MAX-CHANNEL por píxel (R|G|B — alpha ignora-se:
//! capturas são opacas): `mean_delta`, `max_delta`, `changed_pct` (píxeis
//! com delta > 0), `p99`. Imagens com dimensões diferentes são um erro.

use std::path::Path;

/// Resultado do diff entre duas capturas.
#[derive(Debug, Clone, serde::Serialize)]
pub struct DiffResult {
    pub width: u32,
    pub height: u32,
    /// Delta médio por píxel (0..255, canal máximo).
    pub mean_delta: f64,
    /// Maior delta absoluto (canal máximo).
    pub max_delta: u32,
    /// % de píxeis com delta > 0.
    pub changed_pct: f64,
    /// 99.º percentil do delta.
    pub p99: u32,
}

/// ROI `"x,y,w,h"` (píxeis) — validada contra a imagem no diff.
pub fn parse_roi(roi: &str) -> Result<(u32, u32, u32, u32), String> {
    let parts: Vec<&str> = roi.split(',').map(str::trim).collect();
    if parts.len() != 4 {
        return Err(format!("roi '{roi}' inválida — usa x,y,w,h"));
    }
    let nums: Result<Vec<u32>, _> = parts.iter().map(|p| p.parse::<u32>().map_err(|e| e.to_string())).collect();
    let nums = nums.map_err(|e| format!("roi '{roi}': {e}"))?;
    Ok((nums[0], nums[1], nums[2], nums[3]))
}

/// Decodifica um PNG/Rgba.
fn load_rgba(path: &Path) -> Result<image::RgbaImage, String> {
    image::io::Reader::open(path)
        .map_err(|e| format!("a abrir {}: {e}", path.display()))?
        .decode()
        .map_err(|e| format!("a descodificar {}: {e}", path.display()))
        .map(|img| img.to_rgba8())
}

/// Diff completo entre dois ficheiros de imagem.
pub fn diff_files(a: &Path, b: &Path, roi: Option<(u32, u32, u32, u32)>) -> Result<DiffResult, String> {
    let img_a = load_rgba(a)?;
    let img_b = load_rgba(b)?;
    if img_a.dimensions() != img_b.dimensions() {
        return Err(format!(
            "dimensões diferentes: {} {}×{} vs {} {}×{}",
            a.display(),
            img_a.width(),
            img_a.height(),
            b.display(),
            img_b.width(),
            img_b.height()
        ));
    }
    let (width, height) = img_a.dimensions();
    let (x0, y0, rw, rh) = roi.unwrap_or((0, 0, width, height));
    if x0 + rw > width || y0 + rh > height {
        return Err(format!(
            "roi {x0},{y0},{rw},{rh} fora da imagem {width}×{height}"
        ));
    }

    // Histograma 0..=255 do delta (canal máximo) — dá p99 sem ordenar.
    let mut histogram = [0_u64; 256];
    let mut sum = 0_u64;
    let mut max_delta = 0_u32;
    let mut changed = 0_u64;
    let mut total = 0_u64;
    for y in y0..(y0 + rh) {
        for x in x0..(x0 + rw) {
            let pa = img_a.get_pixel(x, y);
            let pb = img_b.get_pixel(x, y);
            let delta = pa[0]
                .abs_diff(pb[0])
                .max(pa[1].abs_diff(pb[1]))
                .max(pa[2].abs_diff(pb[2]));
            histogram[delta.min(255) as usize] += 1;
            sum += u64::from(delta);
            max_delta = max_delta.max(u32::from(delta));
            if delta > 0 {
                changed += 1;
            }
            total += 1;
        }
    }
    if total == 0 {
        return Err("roi vazia (0 píxeis)".into());
    }
    // p99: primeiro valor cuja CDF ≥ 99 %.
    let mut cumulative = 0_u64;
    let mut p99 = 0;
    for (delta, count) in histogram.iter().enumerate() {
        cumulative += count;
        if cumulative * 100 >= total * 99 {
            p99 = delta as u32;
            break;
        }
    }
    Ok(DiffResult {
        width,
        height,
        mean_delta: sum as f64 / total as f64,
        max_delta,
        changed_pct: changed as f64 * 100.0 / total as f64,
        p99,
    })
}

/// Compara `actual` com o golden `<dir>/<name>.png`.
///
/// Baseline ausente → COPIA o atual para lá e devolve `None` (o chamador
/// reporta "baseline criada" e sai com sucesso — o primeiro run de CI
/// semeia os goldens em vez de falhar).
pub fn compare_baseline(
    dir: &Path,
    name: &str,
    actual: &Path,
) -> Result<Option<(DiffResult, std::path::PathBuf)>, String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("a criar {}: {e}", dir.display()))?;
    let golden = dir.join(format!("{name}.png"));
    if !golden.exists() {
        std::fs::copy(actual, &golden)
            .map_err(|e| format!("a criar baseline {}: {e}", golden.display()))?;
        return Ok(None);
    }
    let result = diff_files(&golden, actual, None)?;
    Ok(Some((result, golden)))
}

/// Regrava o golden com o conteúdo de `actual` (aceitar a mudança).
pub fn update_baseline(dir: &Path, name: &str, actual: &Path) -> Result<std::path::PathBuf, String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("a criar {}: {e}", dir.display()))?;
    let golden = dir.join(format!("{name}.png"));
    std::fs::copy(actual, &golden).map_err(|e| format!("a gravar {}: {e}", golden.display()))?;
    Ok(golden)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Duas imagens idênticas → zero em tudo; um píxel pintado → detetado.
    #[test]
    fn diff_detects_changed_pixels() {
        let dir = std::env::temp_dir().join(format!("viber-diff-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let a = dir.join("a.png");
        let b = dir.join("b.png");
        let mut img = image::RgbaImage::new(64, 64);
        image::DynamicImage::ImageRgba8(img.clone()).save(&a).unwrap();
        img.put_pixel(10, 10, image::Rgba([255, 0, 0, 255]));
        image::DynamicImage::ImageRgba8(img).save(&b).unwrap();

        let result = diff_files(&a, &b, None).unwrap();
        assert_eq!(result.max_delta, 255);
        assert!(result.changed_pct > 0.0);
        assert!(result.changed_pct < 1.0, "um píxel em 4096 ≈ 0.02 %");
        assert!(result.p99 == 0, "99 % dos píxeis estão iguais");

        let roi = parse_roi("0,0,4,4").unwrap();
        let inside = diff_files(&a, &b, Some(roi)).unwrap();
        assert_eq!(inside.max_delta, 0, "ROI longe do píxel pintado");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Baseline: 1.º run semeia (None), o seguinte compara, `update` aceita.
    #[test]
    fn baseline_seeds_then_compares() {
        let dir = std::env::temp_dir().join(format!("viber-golden-test-{}", std::process::id()));
        let golden_dir = dir.join("golden");
        let actual = dir.join("atual.png");
        std::fs::create_dir_all(&dir).unwrap();
        image::DynamicImage::new_rgba8(32, 32).save(&actual).unwrap();

        let seeded = compare_baseline(&golden_dir, "shot", &actual).unwrap();
        assert!(seeded.is_none(), "primeiro run semeia o golden");
        assert!(golden_dir.join("shot.png").exists());

        let compared = compare_baseline(&golden_dir, "shot", &actual).unwrap();
        let (result, _) = compared.expect("segundo run compara");
        assert_eq!(result.max_delta, 0, "imagens iguais");

        // Muda o conteúdo e aceita a mudança.
        let mut img = image::RgbaImage::new(32, 32);
        img.put_pixel(1, 1, image::Rgba([255, 255, 255, 255]));
        image::DynamicImage::ImageRgba8(img).save(&actual).unwrap();
        let changed = compare_baseline(&golden_dir, "shot", &actual).unwrap();
        assert!(changed.expect("compara").0.max_delta > 0);
        update_baseline(&golden_dir, "shot", &actual).unwrap();
        let after = compare_baseline(&golden_dir, "shot", &actual).unwrap();
        assert_eq!(after.expect("compara").0.max_delta, 0, "golden atualizado");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
