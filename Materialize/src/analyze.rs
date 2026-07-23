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

    // Natural-material rules run BEFORE the metal rules: per-pixel colour alone
    // cannot separate golden sand from brushed gold or bright snow from silver,
    // but the global statistics can — and a grass/sand/snow texture classified
    // as metal poisons the whole PBR set (metallic_scale 1.5 + smoothness
    // boost). Real metals still reach their rules because they miss these
    // gates: polished metal has strong specular streaks (high luma_std) and
    // low edge density; organic surfaces are the opposite.
    let (preset, confidence) = if f.luma_mean > 0.70 && f.sat_mean < 0.18 && f.luma_mean < 0.97 {
        // Bright + desaturated = snow/ice field, not silver. Pure white
        // (luma ~1.0, e.g. blank background) stays Default via the 0.97 cap.
        (Preset::Snow, 0.7)
    } else if f.sat_mean > 0.15 && (0.17..=0.45).contains(&chroma_peak) {
        // Green-dominant = vegetation. Band starts at 0.17 (yellow-green
        // grass peaks near 0.21) and runs to cyan-green.
        (Preset::Foliage, 0.7)
    } else if f.sat_mean > 0.30
        && (0.06..0.17).contains(&chroma_peak)
        && f.luma_std < 0.12
        && f.luma_mean > 0.45
    {
        // Warm gold-band hue with a FLAT luminance histogram is sand/clay.
        // Actual gold/brass shows specular highlights (luma_std well above
        // 0.12), so it falls through to the chromatic-metal rule below.
        (Preset::Sand, 0.7)
    } else if sat_low && luma_bright && gray_dominant && f.luma_mean < 0.92 && f.edge_density < 0.30
    {
        // Achromatic metal (steel/silver/aluminum). Pure white (luma ~1.0) is
        // NOT metal; cap below 0.92 to avoid false positives on white
        // backgrounds/textures. Granular gray surfaces (stone/concrete) have
        // high edge density and are excluded — polished metal reads smooth.
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
    } else if f.edge_density > 0.25 && f.sat_mean < 0.18 && f.luma_mean < 0.55 {
        // luma cap 0.55 (was 0.45): light-gray granular rock that the metal
        // rule's new edge-density veto rejects should land here, not Default.
        (Preset::Stone, 0.7)
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

    /// Deterministic tiny hash for reproducible pseudo-noise in tests.
    fn hash01(x: u32, y: u32) -> f32 {
        let mut v = x.wrapping_mul(374761393) ^ y.wrapping_mul(668265263);
        v = (v ^ (v >> 13)).wrapping_mul(1274126177);
        ((v ^ (v >> 16)) & 0xffff) as f32 / 65535.0
    }

    #[test]
    fn test_classify_grass_is_foliage_not_metal() {
        // Green noise with desaturated gray specks (dry blades) — the specks
        // must not drag the classification to Metal.
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let n = hash01(x, y);
                let p = if n > 0.9 {
                    Rgba([120, 125, 118, 255]) // gray speck
                } else {
                    let g = 120 + (n * 60.0) as u8;
                    Rgba([60, g, 40, 255])
                };
                img.put_pixel(x, y, p);
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        let c = classify(&f);
        assert_eq!(c.preset, Preset::Foliage, "features: {f:?}");
    }

    #[test]
    fn test_classify_sand_not_gold() {
        // Warm tan with a flat luminance histogram (no specular streaks).
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let n = (hash01(x, y) * 20.0) as u8;
                img.put_pixel(x, y, Rgba([210 - n, 170 - n, 110 - n, 255]));
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        let c = classify(&f);
        assert_eq!(c.preset, Preset::Sand, "features: {f:?}");
    }

    #[test]
    fn test_classify_snow_not_silver() {
        // Bright, desaturated, slightly noisy — snow field, not metal.
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let n = (hash01(x, y) * 25.0) as u8;
                img.put_pixel(x, y, Rgba([230 - n, 232 - n, 238 - n, 255]));
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        let c = classify(&f);
        assert_eq!(c.preset, Preset::Snow, "features: {f:?}");
    }

    #[test]
    fn test_classify_gold_with_speculars_is_metal() {
        // Same warm hue as sand but with strong specular streaks (high
        // luma_std): brushed gold must STILL classify as metal.
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let streak = ((x / 8) % 2) as f32; // alternating bright bands
                let base = 90.0 + streak * 140.0;
                let n = hash01(x, y) * 15.0;
                img.put_pixel(
                    x,
                    y,
                    Rgba([
                        (base + n).min(255.0) as u8,
                        (base * 0.78 + n).min(255.0) as u8,
                        (base * 0.30).min(255.0) as u8,
                        255,
                    ]),
                );
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        let c = classify(&f);
        assert_eq!(c.preset, Preset::Metal, "features: {f:?}");
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

    #[test]
    fn test_rgb_to_hsl_pure_green() {
        let [h, s, l] = rgb_to_hsl([0.0, 1.0, 0.0]);
        assert!((h - 1.0 / 3.0).abs() < 0.02 || (h - 2.0 / 3.0).abs() < 0.02);
        assert!((s - 1.0).abs() < 0.01);
        assert!((l - 0.5).abs() < 0.01);
    }

    #[test]
    fn test_rgb_to_hsl_pure_blue() {
        let [h, s, _l] = rgb_to_hsl([0.0, 0.0, 1.0]);
        assert!((h - 2.0 / 3.0).abs() < 0.02);
        assert!((s - 1.0).abs() < 0.01);
    }

    #[test]
    fn test_rgb_to_hsl_gray_zero_saturation() {
        let [h, s, l] = rgb_to_hsl([0.5, 0.5, 0.5]);
        assert!(s < 0.01);
        assert!((l - 0.5).abs() < 0.01);
        let _ = h;
    }

    #[test]
    fn test_rgb_to_hsl_black() {
        let [_, s, l] = rgb_to_hsl([0.0, 0.0, 0.0]);
        assert!(s < 0.01);
        assert!(l < 0.01);
    }

    #[test]
    fn test_rgb_to_hsl_white() {
        let [_, s, l] = rgb_to_hsl([1.0, 1.0, 1.0]);
        assert!(s < 0.01);
        assert!((l - 1.0).abs() < 0.01);
    }

    #[test]
    fn test_rgb_to_hsl_cyan() {
        let [h, s, l] = rgb_to_hsl([0.0, 1.0, 1.0]);
        assert!(s > 0.9);
        assert!((l - 0.5).abs() < 0.05);
        assert!(h > 0.4 && h < 0.6);
    }

    #[test]
    fn test_rgb_to_hsl_magenta() {
        let [h, s, _] = rgb_to_hsl([1.0, 0.0, 1.0]);
        assert!(s > 0.9);
        assert!(h < 0.2 || h > 0.8);
    }

    #[test]
    fn test_rgb_to_hsl_yellow() {
        let [h, s, l] = rgb_to_hsl([1.0, 1.0, 0.0]);
        assert!(s > 0.9);
        assert!((l - 0.5).abs() < 0.05);
        assert!(h > 0.12 && h < 0.22);
    }

    #[test]
    fn test_luma_pure_red() {
        let l = luma(Rgba([255, 0, 0, 255]));
        assert!(l > 0.2 && l < 0.35);
    }

    #[test]
    fn test_luma_pure_green() {
        let l = luma(Rgba([0, 255, 0, 255]));
        assert!(l > 0.6);
    }

    #[test]
    fn test_luma_pure_blue() {
        let l = luma(Rgba([0, 0, 255, 255]));
        assert!(l < 0.15);
    }

    #[test]
    fn test_dominant_bin_all_zero() {
        assert_eq!(dominant_bin(&[0; HIST_BINS]), None);
    }

    #[test]
    fn test_dominant_bin_single_peak() {
        let mut h = [0u32; HIST_BINS];
        h[5] = 42;
        assert_eq!(dominant_bin(&h), Some(5));
    }

    #[test]
    fn test_dominant_bin_last_wins_on_tie() {
        let mut h = [0u32; HIST_BINS];
        h[2] = 10;
        h[7] = 10;
        // `max_by_key` keeps the last maximum when counts tie.
        assert_eq!(dominant_bin(&h), Some(7));
    }

    #[test]
    fn test_bin_to_hue_centre_bin_zero() {
        assert!((bin_to_hue_centre(0) - 0.5 / HIST_BINS as f32).abs() < 1e-6);
    }

    #[test]
    fn test_bin_to_hue_centre_last_bin() {
        let centre = bin_to_hue_centre(HIST_BINS - 1);
        assert!((centre - (11.5 / 12.0)).abs() < 1e-6);
    }

    #[test]
    fn test_analyze_flat_red() {
        let f = analyze(&flat(48, 48, Rgba([200, 40, 40, 255])));
        assert!(f.sat_mean > 0.5);
        assert!(f.luma_mean > 0.2);
    }

    #[test]
    fn test_analyze_flat_green_high_sat() {
        let f = analyze(&flat(48, 48, Rgba([30, 180, 30, 255])));
        assert!(f.sat_mean > 0.4);
    }

    #[test]
    fn test_analyze_flat_blue() {
        let f = analyze(&flat(48, 48, Rgba([20, 20, 220, 255])));
        assert!(f.sat_mean > 0.7);
    }

    #[test]
    fn test_analyze_alpha_transparency_coverage() {
        let mut img = RgbaImage::new(32, 32);
        for y in 0..32 {
            for x in 0..32 {
                let a = if x < 16 { 128u8 } else { 255u8 };
                img.put_pixel(x, y, Rgba([100, 100, 100, a]));
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        assert!(f.alpha_coverage > 0.0 && f.alpha_coverage < 1.0);
    }

    #[test]
    fn test_analyze_vertical_gradient_edges() {
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let v = if y < 32 { 0u8 } else { 255u8 };
                img.put_pixel(x, y, Rgba([v, v, v, 255]));
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        assert!(f.edge_density > 0.05);
    }

    #[test]
    fn test_analyze_non_tileable_high_mse() {
        let mut img = RgbaImage::new(32, 32);
        for y in 0..32 {
            for x in 0..32 {
                let v = (x * 8) as u8;
                img.put_pixel(x, y, Rgba([v, v, v, 255]));
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        assert!(f.tile_mse > 0.01);
    }

    #[test]
    fn test_analyze_tiny_image() {
        let f = analyze(&flat(2, 2, Rgba([128, 128, 128, 255])));
        assert!(f.luma_mean > 0.4 && f.luma_mean < 0.6);
    }

    #[test]
    fn test_analyze_checkerboard_local_variance() {
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let v = if (x / 4 + y / 4) % 2 == 0 {
                    30u8
                } else {
                    220u8
                };
                img.put_pixel(x, y, Rgba([v, v, v, 255]));
            }
        }
        let f = analyze(&DynamicImage::ImageRgba8(img));
        assert!(f.local_contrast_variance > 0.001);
        assert!(f.edge_density > 0.1);
    }

    #[test]
    fn test_analyze_hue_hist_red_dominant() {
        let f = analyze(&flat(64, 64, Rgba([220, 30, 30, 255])));
        let peak = dominant_bin(&f.hue_hist).expect("hue peak");
        let centre = bin_to_hue_centre(peak);
        assert!(centre < 0.08 || centre > 0.92);
    }

    #[test]
    fn test_analyze_hue_hist_green_dominant() {
        let f = analyze(&flat(64, 64, Rgba([40, 200, 50, 255])));
        let peak = dominant_bin(&f.hue_hist);
        assert!(peak.is_some());
        let c = bin_to_hue_centre(peak.unwrap());
        assert!(c > 0.15 && c < 0.45);
    }

    #[test]
    fn test_format_report_contains_feature_lines() {
        let c = classify(&analyze(&flat(16, 16, Rgba([128, 64, 32, 255]))));
        let s = format_report(&c);
        assert!(s.contains("luma_mean="));
        assert!(s.contains("sat_mean="));
        assert!(s.contains("edge_density="));
        assert!(s.contains("tile_mse="));
        assert!(s.contains("alpha_coverage="));
    }

    #[test]
    fn test_format_report_shows_preset_name() {
        let c = classify(&analyze(&flat(16, 16, Rgba([255, 255, 255, 255]))));
        let s = format_report(&c);
        assert!(s.contains("default"));
    }

    #[test]
    fn test_format_report_hue_peak_na_on_gray() {
        let c = classify(&analyze(&flat(32, 32, Rgba([128, 128, 128, 255]))));
        let s = format_report(&c);
        assert!(s.contains("hue_peak=n/a") || s.contains("hue_peak=0."));
    }

    #[test]
    fn test_classify_clones_features() {
        let f = analyze(&flat(32, 32, Rgba([100, 100, 100, 255])));
        let c = classify(&f);
        assert_eq!(c.features.luma_mean, f.luma_mean);
        assert_eq!(c.features.tile_mse, f.tile_mse);
    }

    #[test]
    fn test_classify_confidence_bounded() {
        let f = analyze(&flat(32, 32, Rgba([128, 128, 128, 255])));
        let c = classify(&f);
        assert!(c.confidence >= 0.0 && c.confidence <= 1.0);
    }

    #[test]
    fn test_classify_skin_tone_synthetic() {
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                img.put_pixel(x, y, Rgba([210, 160, 140, 255]));
            }
        }
        let c = classify(&analyze(&DynamicImage::ImageRgba8(img)));
        assert!(matches!(
            c.preset,
            Preset::Skin | Preset::Default | Preset::Sand
        ));
    }

    #[test]
    fn test_classify_dark_stone_gray() {
        let mut img = RgbaImage::new(64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let n = hash01(x, y) * 40.0;
                let v = (80.0 + n) as u8;
                img.put_pixel(x, y, Rgba([v, v - 5, v - 10, 255]));
            }
        }
        let c = classify(&analyze(&DynamicImage::ImageRgba8(img)));
        assert!(matches!(
            c.preset,
            Preset::Stone | Preset::Default | Preset::Metal
        ));
    }

    #[test]
    fn test_luma_at_matches_luma_pixel() {
        let img = RgbaImage::from_pixel(4, 4, Rgba([100, 150, 200, 255]));
        let l0 = luma(Rgba([100, 150, 200, 255]));
        let l1 = luma_at(&img, 0, 0);
        assert!((l0 - l1).abs() < 1e-6);
    }

    #[test]
    fn test_rgb_to_hsl_mid_orange() {
        let [h, s, l] = rgb_to_hsl([1.0, 0.5, 0.0]);
        assert!(h > 0.05 && h < 0.15);
        assert!(s > 0.9);
        assert!((l - 0.5).abs() < 0.05);
    }

    #[test]
    fn test_analyze_luma_std_zero_on_flat() {
        let f = analyze(&flat(64, 64, Rgba([90, 90, 90, 255])));
        assert!(f.luma_std < 0.01);
    }

    #[test]
    fn test_analyze_sat_std_low_on_flat_color() {
        let f = analyze(&flat(64, 64, Rgba([90, 90, 90, 255])));
        assert!(f.sat_std < 0.05);
    }

    #[test]
    fn test_image_features_equality() {
        let f1 = analyze(&flat(8, 8, Rgba([1, 2, 3, 255])));
        let f2 = f1.clone();
        assert_eq!(f1, f2);
    }

    #[test]
    fn test_classify_bright_silver_metal_candidate() {
        let f = analyze(&flat(64, 64, Rgba([180, 185, 190, 255])));
        let c = classify(&f);
        assert!(matches!(
            c.preset,
            Preset::Metal | Preset::Snow | Preset::Default
        ));
    }

    #[test]
    fn test_format_report_confidence_two_decimals() {
        let c = classify(&analyze(&flat(8, 8, Rgba([50, 50, 50, 255]))));
        let s = format_report(&c);
        assert!(s.contains("confidence"));
        assert!(s.matches('.').count() >= 2);
    }

    #[test]
    fn test_classify_default_confidence_is_point_four() {
        let c = classify(&analyze(&flat(32, 32, Rgba([255, 255, 255, 255]))));
        assert_eq!(c.preset, Preset::Default);
        assert!((c.confidence - 0.4).abs() < 1e-6);
    }

    #[test]
    fn test_rgb_to_hsl_equal_rgb_is_achromatic() {
        let [h, s, l] = rgb_to_hsl([0.2, 0.2, 0.2]);
        assert!(s < 0.01);
        assert!((l - 0.2).abs() < 0.01);
        let _ = h;
    }
}
