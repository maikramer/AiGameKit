//! CPU feature extraction for auto-preset detection (F2).
//!
//! Sampling strategy: stratified interior grid (N=10000) for global features,
//! plus full border rows/cols for `tile_mse` (border continuity needs full rows,
//! not subsamples).

use image::Rgba;

use crate::preset::Preset;

const INTERIOR_SAMPLES: u32 = 10_000;
const HIST_BINS: usize = 12;

#[derive(Debug, Clone, PartialEq)]
pub struct ImageFeatures {
    pub luma_mean: f32,
    pub luma_std: f32,
    pub sat_mean: f32,
    pub sat_std: f32,
    pub hue_hist: [u32; HIST_BINS],
    pub edge_density: f32,
    pub local_contrast_variance: f32,
    pub tile_mse: f32,
    pub alpha_coverage: f32,
}

#[derive(Debug, Clone)]
pub struct Classification {
    pub preset: Preset,
    pub confidence: f32,
    pub features: ImageFeatures,
}

fn rgb_to_hsl(rgb: [f32; 3]) -> [f32; 3] {
    let [r, g, b] = rgb;
    let max = r.max(g).max(b);
    let min = r.min(g).min(b);
    let delta = max - min;
    let l = (max + min) * 0.5;
    let s = if delta > 1e-6 {
        let denom = (1.0 - (2.0 * l - 1.0).abs()).max(1e-6);
        delta / denom
    } else {
        0.0
    };
    let h = if delta > 1e-6 {
        let raw = if (max - r).abs() < 1e-6 {
            (g - b) / delta + if g < b { 6.0 } else { 0.0 }
        } else if (max - g).abs() < 1e-6 {
            (b - r) / delta + 2.0
        } else {
            (r - g) / delta + 4.0
        };
        raw / 6.0
    } else {
        0.0
    };
    [h, s, l]
}

fn luma(p: Rgba<u8>) -> f32 {
    let r = p[0] as f32 / 255.0;
    let g = p[1] as f32 / 255.0;
    let b = p[2] as f32 / 255.0;
    0.2126 * r + 0.7152 * g + 0.0722 * b
}

fn luma_at(view: &image::ImageBuffer<Rgba<u8>, Vec<u8>>, x: u32, y: u32) -> f32 {
    luma(*view.get_pixel(x, y))
}

pub fn analyze(image: &image::DynamicImage) -> ImageFeatures {
    let rgba = image.to_rgba8();
    let (w, h) = rgba.dimensions();
    let total = (w as u64) * (h as u64);

    // === Stratified interior sampling ===
    let step_x = ((w as f32).sqrt().max(2.0)).round() as u32;
    let step_y = ((h as f32).sqrt().max(2.0)).round() as u32;
    let mut lumas = Vec::with_capacity(INTERIOR_SAMPLES as usize);
    let mut sats = Vec::with_capacity(INTERIOR_SAMPLES as usize);
    let mut hues: Vec<f32> = Vec::with_capacity(INTERIOR_SAMPLES as usize);
    let mut hue_hist = [0u32; HIST_BINS];
    let mut alpha_transparent = 0u32;
    let mut alpha_total = 0u32;

    let mut visited = 0u32;
    let mut luma_sum = 0.0f64;
    let mut luma_sum_sq = 0.0f64;
    let mut sat_sum = 0.0f64;
    let mut sat_sum_sq = 0.0f64;

    let mut y = 0u32;
    while y < h {
        let mut x = 0u32;
        while x < w {
            if visited >= INTERIOR_SAMPLES {
                break;
            }
            let p = *rgba.get_pixel(x, y);
            let rgb_f = [
                p[0] as f32 / 255.0,
                p[1] as f32 / 255.0,
                p[2] as f32 / 255.0,
            ];
            let l = luma(p);
            let [hue, sat, _l2] = rgb_to_hsl(rgb_f);
            lumas.push(l);
            sats.push(sat);
            if sat > 0.1 {
                hues.push(hue);
                let bin = ((hue * HIST_BINS as f32) as usize) % HIST_BINS;
                hue_hist[bin] += 1;
            }
            luma_sum += l as f64;
            luma_sum_sq += (l * l) as f64;
            sat_sum += sat as f64;
            sat_sum_sq += (sat * sat) as f64;
            visited += 1;

            alpha_total += 1;
            if (p[3] as f32 / 255.0) < 1.0 {
                alpha_transparent += 1;
            }
            x += step_x;
        }
        if visited >= INTERIOR_SAMPLES {
            break;
        }
        y += step_y;
    }

    let n = visited.max(1) as f64;
    let luma_mean = (luma_sum / n) as f32;
    let luma_var = ((luma_sum_sq / n) - (luma_sum / n).powi(2)).max(0.0);
    let luma_std = luma_var.sqrt() as f32;
    let sat_mean = (sat_sum / n) as f32;
    let sat_var = ((sat_sum_sq / n) - (sat_sum / n).powi(2)).max(0.0);
    let sat_std = sat_var.sqrt() as f32;
    let alpha_coverage = if alpha_total > 0 {
        alpha_transparent as f32 / alpha_total as f32
    } else {
        0.0
    };

    // === Sobel edge density on a sub-sample (5×5 grid spacing) ===
    let mut edge_count = 0u32;
    let mut edge_total = 0u32;
    let edge_step = 4u32;
    let ey_start = edge_step;
    let ey_end = h.saturating_sub(edge_step);
    let ex_start = edge_step;
    let ex_end = w.saturating_sub(edge_step);
    let mut ey = ey_start;
    while ey < ey_end {
        let mut ex = ex_start;
        while ex < ex_end {
            let l00 = luma_at(&rgba, ex - 1, ey - 1);
            let l10 = luma_at(&rgba, ex, ey - 1);
            let l20 = luma_at(&rgba, ex + 1, ey - 1);
            let l01 = luma_at(&rgba, ex - 1, ey);
            let l21 = luma_at(&rgba, ex + 1, ey);
            let l02 = luma_at(&rgba, ex - 1, ey + 1);
            let l12 = luma_at(&rgba, ex, ey + 1);
            let l22 = luma_at(&rgba, ex + 1, ey + 1);
            let gx = -l00 + l20 - 2.0 * l01 + 2.0 * l21 - l02 + l22;
            let gy = -l00 - 2.0 * l10 - l20 + l02 + 2.0 * l12 + l22;
            let mag = (gx * gx + gy * gy).sqrt();
            if mag > 0.15 {
                edge_count += 1;
            }
            edge_total += 1;
            ex += edge_step;
        }
        ey += edge_step;
    }
    let edge_density = if edge_total > 0 {
        edge_count as f32 / edge_total as f32
    } else {
        0.0
    };

    // === Local contrast variance (5×5 windows, subsampled) ===
    let mut var_sum = 0.0f64;
    let mut var_count = 0u32;
    let lc_step = 8u32;
    let mut lcy = lc_step;
    while lcy + lc_step < h {
        let mut lcx = lc_step;
        while lcx + lc_step < w {
            let mut sum = 0.0f64;
            let mut n = 0.0f64;
            for dy in -2i32..=2 {
                for dx in -2i32..=2 {
                    let xx = (lcx as i32 + dx).max(0).min((w - 1) as i32) as u32;
                    let yy = (lcy as i32 + dy).max(0).min((h - 1) as i32) as u32;
                    sum += luma_at(&rgba, xx, yy) as f64;
                    n += 1.0;
                }
            }
            let mean = sum / n;
            let mut v = 0.0f64;
            for dy in -2i32..=2 {
                for dx in -2i32..=2 {
                    let xx = (lcx as i32 + dx).max(0).min((w - 1) as i32) as u32;
                    let yy = (lcy as i32 + dy).max(0).min((h - 1) as i32) as u32;
                    let l = luma_at(&rgba, xx, yy) as f64;
                    v += (l - mean) * (l - mean);
                }
            }
            var_sum += v / n;
            var_count += 1;
            lcx += lc_step;
        }
        lcy += lc_step;
    }
    let local_contrast_variance = if var_count > 0 {
        (var_sum / var_count as f64) as f32
    } else {
        0.0
    };

    // === tile_mse: full top↔bottom and left↔right border rows/cols ===
    let rows = h.min(8);
    let cols = w.min(8);
    let mut sq_err_sum = 0.0f64;
    let mut sq_err_count = 0u64;
    for r in 0..rows {
        for x in 0..w {
            let top = luma_at(&rgba, x, r);
            let bot = luma_at(&rgba, x, h - 1 - r);
            let d = top - bot;
            sq_err_sum += (d * d) as f64;
            sq_err_count += 1;
        }
    }
    for c in 0..cols {
        for y in 0..h {
            let lft = luma_at(&rgba, c, y);
            let rgt = luma_at(&rgba, w - 1 - c, y);
            let d = lft - rgt;
            sq_err_sum += (d * d) as f64;
            sq_err_count += 1;
        }
    }
    let tile_mse = if sq_err_count > 0 {
        (sq_err_sum / sq_err_count as f64) as f32
    } else {
        1.0
    };

    let _ = total;
    ImageFeatures {
        luma_mean,
        luma_std,
        sat_mean,
        sat_std,
        hue_hist,
        edge_density,
        local_contrast_variance,
        tile_mse,
        alpha_coverage,
    }
}

fn dominant_bin(hist: &[u32; HIST_BINS]) -> Option<usize> {
    let (idx, &val) = hist
        .iter()
        .enumerate()
        .max_by_key(|(_, v)| *v)
        .unwrap_or((0, &0));
    if val == 0 { None } else { Some(idx) }
}

fn bin_to_hue_centre(bin: usize) -> f32 {
    (bin as f32 + 0.5) / HIST_BINS as f32
}

pub fn classify(f: &ImageFeatures) -> Classification {
    let sat_low = f.sat_mean < 0.15;
    let gray_dominant = f.sat_std < 0.08 || f.sat_mean < 0.10;
    let luma_bright = f.luma_mean > 0.40;
    let chroma_peak = dominant_bin(&f.hue_hist)
        .map(bin_to_hue_centre)
        .unwrap_or(-1.0);

    // Confidence helpers: distance from threshold normalised to the threshold.
    let conf = |v: f32, t: f32| ((t - v).max(0.0) / t).clamp(0.0, 1.0);

    let (preset, confidence) = if sat_low && luma_bright && gray_dominant && f.luma_mean < 0.92 {
        // Pure white (luma ~1.0) is NOT metal; cap below 0.92 to avoid false positives
        // on white backgrounds/textures.
        (Preset::Metal, conf(f.sat_mean, 0.15).max(0.6))
    } else if f.sat_mean > 0.30 && chroma_peak > 0.06 && chroma_peak < 0.17 && f.luma_mean > 0.30 {
        (Preset::Metal, 0.7)
    } else if f.edge_density < 0.05
        && f.local_contrast_variance < 0.003
        && f.sat_mean < 0.25
        && f.sat_mean > 0.05
    {
        // Skin needs some chroma (warm tones); pure white (sat~0) falls through.
        (Preset::Skin, 0.7)
    } else if f.local_contrast_variance > 0.015
        && f.edge_density > 0.20
        && (0.06..=0.13).contains(&chroma_peak)
    {
        (Preset::Wood, 0.7)
    } else if f.edge_density > 0.25 && f.sat_mean < 0.18 && f.luma_mean < 0.45 {
        (Preset::Stone, 0.7)
    } else if f.sat_mean > 0.18 && (0.22..=0.40).contains(&chroma_peak) {
        (Preset::Foliage, 0.7)
    } else if f.tile_mse < 0.005 && f.local_contrast_variance > 0.015 {
        (Preset::Floor, 0.65)
    } else {
        (Preset::Default, 0.4)
    };

    Classification {
        preset,
        confidence,
        features: f.clone(),
    }
}

pub fn format_report(c: &Classification) -> String {
    let f = &c.features;
    let peak = dominant_bin(&f.hue_hist)
        .map(|b| format!("{:.2}", bin_to_hue_centre(b)))
        .unwrap_or_else(|| "n/a".to_string());
    format!(
        "Detected: {} (confidence {:.2})\n\
         features:\n\
         \x20  luma_mean={:.3} luma_std={:.3}\n\
         \x20  sat_mean={:.3} sat_std={:.3}\n\
         \x20  hue_peak={} edge_density={:.3}\n\
         \x20  local_contrast_var={:.4} tile_mse={:.4}\n\
         \x20  alpha_coverage={:.3}",
        c.preset,
        c.confidence,
        f.luma_mean,
        f.luma_std,
        f.sat_mean,
        f.sat_std,
        peak,
        f.edge_density,
        f.local_contrast_variance,
        f.tile_mse,
        f.alpha_coverage
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{DynamicImage, Rgba, RgbaImage};

    fn flat(w: u32, h: u32, rgba: Rgba<u8>) -> DynamicImage {
        DynamicImage::ImageRgba8(RgbaImage::from_pixel(w, h, rgba))
    }

    #[test]
    fn test_white_features() {
        let img = flat(64, 64, Rgba([255, 255, 255, 255]));
        let f = analyze(&img);
        assert!((f.luma_mean - 1.0).abs() < 0.01);
        assert!(f.sat_mean < 0.01);
        assert!(f.edge_density < 0.05);
    }

    #[test]
    fn test_black_features() {
        let img = flat(64, 64, Rgba([0, 0, 0, 255]));
        let f = analyze(&img);
        assert!(f.luma_mean < 0.01);
    }

    #[test]
    fn test_horizontal_gradient_has_edges() {
        // Sharp vertical boundary (left half black, right half white) → strong edges.
        let mut img = RgbaImage::new(64, 64);
        for x in 0..64 {
            let v = if x < 32 { 0u8 } else { 255u8 };
            for y in 0..64 {
                img.put_pixel(x, y, Rgba([v, v, v, 255]));
            }
        }
        let dyn_img = DynamicImage::ImageRgba8(img);
        let f = analyze(&dyn_img);
        assert!(f.edge_density > 0.05, "edge_density={}", f.edge_density);
    }

    #[test]
    fn test_tileable_low_mse() {
        let tile = flat(32, 32, Rgba([100, 150, 200, 255]));
        let f = analyze(&tile);
        assert!(f.tile_mse < 0.01, "tile_mse={}", f.tile_mse);
    }

    #[test]
    fn test_rgb_to_hsl_pure_red() {
        let [h, s, l] = rgb_to_hsl([1.0, 0.0, 0.0]);
        assert!((h - 0.0).abs() < 0.01 || (h - 1.0).abs() < 0.01);
        assert!((s - 1.0).abs() < 0.01);
        assert!((l - 0.5).abs() < 0.01);
    }

    #[test]
    fn test_classify_white_is_default() {
        let img = flat(64, 64, Rgba([255, 255, 255, 255]));
        let f = analyze(&img);
        let c = classify(&f);
        assert_eq!(c.preset, Preset::Default);
    }

    #[test]
    fn test_classify_red_metal_or_default() {
        let img = flat(64, 64, Rgba([180, 80, 30, 255]));
        let f = analyze(&img);
        let c = classify(&f);
        assert!(matches!(c.preset, Preset::Metal | Preset::Default));
    }

    #[test]
    fn test_format_report_includes_preset() {
        let img = flat(32, 32, Rgba([255, 255, 255, 255]));
        let f = analyze(&img);
        let c = classify(&f);
        let s = format_report(&c);
        assert!(s.contains("Detected:"));
        assert!(s.contains("confidence"));
    }
}
