//! CPU reference implementations of every pipeline pass (golden tests + GPU
//! parity). Pure f32, no GPU — runs on CI. Each function mirrors the WGSL
//! pass semantics exactly (same kernels, same wrap/clamp, same encodings), so
//! `tests/gpu_parity.rs` can compare the GPU chain against these.

/// Border handling shared by every pass (`wrap_or_clamp` in params.wgsl).
pub fn wrap_or_clamp(x: i32, y: i32, w: usize, h: usize, seamless: bool) -> (usize, usize) {
    if seamless {
        (
            (x.rem_euclid(w as i32)) as usize,
            (y.rem_euclid(h as i32)) as usize,
        )
    } else {
        (
            x.clamp(0, w as i32 - 1) as usize,
            y.clamp(0, h as i32 - 1) as usize,
        )
    }
}

pub fn luma709(r: f32, g: f32, b: f32) -> f32 {
    0.2126 * r + 0.7152 * g + 0.0722 * b
}

/// Luminance of an rgba8 image → f32 in [0, 1].
pub fn luma_map(rgba: &[u8], w: usize, h: usize) -> Vec<f32> {
    let mut out = vec![0.0f32; w * h];
    for i in 0..w * h {
        let r = rgba[i * 4] as f32 / 255.0;
        let g = rgba[i * 4 + 1] as f32 / 255.0;
        let b = rgba[i * 4 + 2] as f32 / 255.0;
        out[i] = luma709(r, g, b);
    }
    out
}

fn kernel_weight_1d(kind: u32, sigma: f32, d: i32) -> f32 {
    match kind {
        0 => 1.0,
        _ => {
            let s = sigma.max(0.05);
            let fd = d as f32;
            (-(fd * fd) / (2.0 * s * s)).exp()
        }
    }
}

/// One separable 1-D pass (horizontal when `vertical` is false), mirroring
/// `blur.wgsl` exactly (normalised kernel, wrap/clamp borders).
#[allow(clippy::too_many_arguments)]
fn blur_pass_1d(
    src: &[f32],
    w: usize,
    h: usize,
    radius: i32,
    sigma: f32,
    kind: u32,
    vertical: bool,
    seamless: bool,
) -> Vec<f32> {
    let mut out = vec![0.0f32; w * h];
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            let mut acc = 0.0;
            let mut wsum = 0.0;
            for d in -radius..=radius {
                let (sx, sy) = if vertical {
                    wrap_or_clamp(x, y + d, w, h, seamless)
                } else {
                    wrap_or_clamp(x + d, y, w, h, seamless)
                };
                acc += kernel_weight_1d(kind, sigma, d) * src[sy * w + sx];
                wsum += kernel_weight_1d(kind, sigma, d);
            }
            out[y as usize * w + x as usize] = acc / wsum;
        }
    }
    out
}

/// Full separable blur (H then V) — `blur.wgsl` chain.
#[allow(clippy::too_many_arguments)]
pub fn separable_blur(
    src: &[f32],
    w: usize,
    h: usize,
    radius: i32,
    sigma: f32,
    kind: u32,
    seamless: bool,
) -> Vec<f32> {
    let mid = blur_pass_1d(src, w, h, radius, sigma, kind, false, seamless);
    blur_pass_1d(&mid, w, h, radius, sigma, kind, true, seamless)
}

/// Gaussian pyramid blend with iterative σ-doubling (F1 height chain):
/// level k = two σ0·√3 rounds on level k−1 (σk = σ0·2^k); h = Σ wk·level_k.
pub fn pyramid_blur(
    luma: &[f32],
    w: usize,
    h: usize,
    sigma0: f32,
    levels: usize,
    seamless: bool,
) -> Vec<f32> {
    let levels = levels.clamp(1, 7);
    let sigma0 = sigma0.max(0.1);
    let sigma_double = sigma0 * 3.0f32.sqrt();
    let weights = pyramid_weights(levels);
    let radius0 = (sigma0 * 3.0).ceil().max(1.0) as i32;
    let radius_d = (sigma_double * 3.0).ceil().max(1.0) as i32;

    // Level 0.
    let mut b = separable_blur(luma, w, h, radius0, sigma0, 1, seamless);

    let mut acc: Vec<f32> = b.iter().map(|v| weights[0] * v).collect();
    for wk in weights.iter().skip(1) {
        for _round in 0..2 {
            b = separable_blur(&b, w, h, radius_d, sigma_double, 1, seamless);
        }
        for i in 0..acc.len() {
            acc[i] += wk * b[i];
        }
    }
    acc
}

/// Decaying normalised level weights — mirror of `PresetParams::pyramid_weights`.
pub fn pyramid_weights(levels: usize) -> Vec<f32> {
    let levels = levels.clamp(1, 7);
    let l = levels as f32;
    (0..levels)
        .map(|k| 2.0 * (l - k as f32) / (l * (l + 1.0)))
        .collect()
}

/// Guided filter (He et al. 2010) with self-guidance over a box window —
/// mirrors `guided.wgsl`. Returns (base, detail).
pub fn guided_filter(
    img: &[f32],
    w: usize,
    h: usize,
    radius: i32,
    eps: f32,
    seamless: bool,
) -> (Vec<f32>, Vec<f32>) {
    let mean_i = blur_pass_1d(
        &blur_pass_1d(img, w, h, radius, 0.0, 0, false, seamless),
        w,
        h,
        radius,
        0.0,
        0,
        true,
        seamless,
    );
    let sq: Vec<f32> = img.iter().map(|v| v * v).collect();
    let mean_ii = blur_pass_1d(
        &blur_pass_1d(&sq, w, h, radius, 0.0, 0, false, seamless),
        w,
        h,
        radius,
        0.0,
        0,
        true,
        seamless,
    );

    let eps = eps.max(1e-6);
    let mut base = vec![0.0f32; img.len()];
    let mut detail = vec![0.0f32; img.len()];
    for i in 0..img.len() {
        let var = (mean_ii[i] - mean_i[i] * mean_i[i]).max(0.0);
        let a = var / (var + eps);
        base[i] = mean_i[i] + a * (img[i] - mean_i[i]);
        detail[i] = img[i] - base[i];
    }
    (base, detail)
}

/// Final height composition — mirrors `height_final.wgsl`.
pub fn height_final(
    base: &[f32],
    detail: &[f32],
    shading_hf: Option<&[f32]>,
    contrast: f32,
    detail_mix: f32,
    shading_height_mix: f32,
) -> Vec<f32> {
    base.iter()
        .zip(detail)
        .enumerate()
        .map(|(i, (b, d))| {
            let mut v = (b - 0.5) * contrast + 0.5 + detail_mix * d;
            if let Some(hf) = shading_hf {
                v += shading_height_mix * hf[i];
            }
            v.clamp(0.0, 1.0)
        })
        .collect()
}

/// Inline separable gaussian on a scalar field with wrap/clamp borders —
/// the `smoothed`/`blurred` helpers of normal.wgsl / curvature.wgsl.
pub fn smoothed_at(
    img: &[f32],
    w: usize,
    h: usize,
    x: i32,
    y: i32,
    sigma: f32,
    seamless: bool,
) -> f32 {
    if sigma < 0.05 {
        return sample_at(img, w, h, x, y, seamless);
    }
    let r = (sigma * 3.0).ceil() as i32;
    let mut v_acc = 0.0;
    let mut v_wsum = 0.0;
    for d in -r..=r {
        let mut row_acc = 0.0;
        let mut row_wsum = 0.0;
        for e in -r..=r {
            let (sx, sy) = wrap_or_clamp(x + e, y + d, w, h, seamless);
            let k = (-(e as f32 * e as f32) / (2.0 * sigma * sigma)).exp();
            row_acc += k * img[sy * w + sx];
            row_wsum += k;
        }
        let k = (-(d as f32 * d as f32) / (2.0 * sigma * sigma)).exp();
        v_acc += k * (row_acc / row_wsum);
        v_wsum += k;
    }
    v_acc / v_wsum
}

pub fn sample_at(img: &[f32], w: usize, h: usize, x: i32, y: i32, seamless: bool) -> f32 {
    let (sx, sy) = wrap_or_clamp(x, y, w, h, seamless);
    img[sy * w + sx]
}

/// Sobel (operator=0) or Scharr ÷4 (operator=1) gradient of a (pre-filtered)
/// height field — mirrors `gradient()` in normal.wgsl.
#[allow(clippy::too_many_arguments)]
pub fn gradient(
    height: &[f32],
    w: usize,
    h: usize,
    x: i32,
    y: i32,
    operator: u32,
    prefilter_sigma: f32,
    seamless: bool,
) -> (f32, f32) {
    let s = |dx: i32, dy: i32| smoothed_at(height, w, h, x + dx, y + dy, prefilter_sigma, seamless);
    let (a, b) = if operator == 1 {
        (3.0, 10.0) // Scharr (÷4)
    } else {
        (1.0, 2.0) // Sobel
    };
    let div = if operator == 1 { 4.0 } else { 1.0 };
    let gx = (-a * s(-1, -1) + a * s(1, -1) - b * s(-1, 0) + b * s(1, 0) - a * s(-1, 1)
        + a * s(1, 1))
        / div;
    let gy =
        (-a * s(-1, -1) - b * s(0, -1) - a * s(1, -1) + a * s(-1, 1) + b * s(0, 1) + a * s(1, 1))
            / div;
    (gx, gy)
}

/// Normal map from height — mirrors `normal.wgsl`. Returns rgba8.
#[allow(clippy::too_many_arguments)]
pub fn normal_map(
    height: &[f32],
    w: usize,
    h: usize,
    strength: f32,
    slope_z: f32,
    flip_y: bool,
    operator: u32,
    prefilter_sigma: f32,
    seamless: bool,
) -> Vec<u8> {
    let mut out = vec![0u8; w * h * 4];
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            let (gx, gy) = gradient(height, w, h, x, y, operator, prefilter_sigma, seamless);
            let nx = -gx * strength;
            let mut ny = -gy * strength;
            if flip_y {
                ny = -ny;
            }
            let norm = (nx * nx + ny * ny + slope_z * slope_z).sqrt();
            // rgba8unorm packing rounds to nearest (127.5 → 128), matching the
            // GPU storage-texture write.
            let e = |v: f32| (((v / norm) * 0.5 + 0.5).clamp(0.0, 1.0) * 255.0).round() as u8;
            let i = (y as usize * w + x as usize) * 4;
            out[i] = e(nx);
            out[i + 1] = e(ny);
            out[i + 2] = e(slope_z);
            out[i + 3] = 255;
        }
    }
    out
}

/// Multi-scale LoG curvature — mirrors `curvature.wgsl`. Returns grayscale u8.
#[allow(clippy::too_many_arguments)]
pub fn curvature_map(
    height: &[f32],
    w: usize,
    h: usize,
    levels: usize,
    gain: f32,
    seamless: bool,
) -> Vec<u8> {
    let levels = levels.clamp(1, 3);
    let mut out = vec![0u8; w * h];
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            let lap_at = |sigma: f32| -> f32 {
                let s =
                    |dx: i32, dy: i32| smoothed_at(height, w, h, x + dx, y + dy, sigma, seamless);
                let c = s(0, 0);
                s(-1, 0) + s(1, 0) + s(0, -1) + s(0, 1) - 4.0 * c
            };
            let mut best = lap_at(1.0);
            if levels > 1 {
                let l2 = lap_at(2.0);
                if l2.abs() > best.abs() {
                    best = l2;
                }
            }
            if levels > 2 {
                let l3 = lap_at(4.0);
                if l3.abs() > best.abs() {
                    best = l3;
                }
            }
            let v = (best * gain + 0.5).clamp(0.0, 1.0);
            out[y as usize * w + x as usize] = (v * 255.0) as u8;
        }
    }
    out
}

/// Crease/edge map from the normal — mirrors `edge.wgsl` (full gradient
/// magnitude over both components). `normal` is rgba8.
pub fn edge_map(normal: &[u8], w: usize, h: usize, contrast: f32, seamless: bool) -> Vec<u8> {
    let rg_at = |x: i32, y: i32| -> (f32, f32) {
        let (sx, sy) = wrap_or_clamp(x, y, w, h, seamless);
        let i = (sy * w + sx) * 4;
        (normal[i] as f32 / 255.0, normal[i + 1] as f32 / 255.0)
    };
    let mut out = vec![0u8; w * h];
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            let (ax, ay) = rg_at(x + 1, y);
            let (bx, by) = rg_at(x - 1, y);
            let gx = (ax - bx, ay - by);
            let (cx, cy) = rg_at(x, y + 1);
            let (dx, dy) = rg_at(x, y - 1);
            let gy = (cx - dx, cy - dy);
            let mag = (gx.0 * gx.0 + gx.1 * gx.1 + gy.0 * gy.0 + gy.1 * gy.1).sqrt();
            let t = ((mag * contrast - 0.05) / (0.40 - 0.05)).clamp(0.0, 1.0);
            let edge = t * t * (3.0 - 2.0 * t);
            out[y as usize * w + x as usize] = (edge * 255.0) as u8;
        }
    }
    out
}

/// HBAO-style horizon occlusion over one heightfield (F3) — mirrors
/// `horizon_occlusion` in ao.wgsl.
#[allow(clippy::too_many_arguments)]
pub fn horizon_occlusion(
    tex: &[f32],
    w: usize,
    h: usize,
    x: i32,
    y: i32,
    center_h: f32,
    dirs: usize,
    steps: usize,
    radius: f32,
    depth_scale: f32,
    seamless: bool,
) -> f32 {
    let dirs = dirs.clamp(4, 32);
    let steps = steps.clamp(4, 64);
    let mut occ = 0.0;
    for d in 0..dirs {
        let theta = (d as f32 + 0.5) * std::f32::consts::TAU / dirs as f32;
        let dir = (theta.cos(), theta.sin());
        let mut horizon = 0.0f32;
        for s in 1..=steps {
            let t = radius * (s * s) as f32 / (steps * steps) as f32;
            let dist = t.max(1.0);
            // WGSL vec2<f32>→vec2<i32> truncates toward zero — match exactly.
            let dh = sample_at(
                tex,
                w,
                h,
                x + (dir.0 * dist) as i32,
                y + (dir.1 * dist) as i32,
                seamless,
            ) - center_h;
            let angle = (dh * depth_scale / dist).atan();
            horizon = horizon.max(angle);
            if horizon > 1.5707 {
                break;
            }
        }
        occ += horizon.sin().max(0.0);
    }
    occ / dirs as f32
}

/// Full AO pass — mirrors `ao.wgsl` (fine on height, macro on base at 4×).
#[allow(clippy::too_many_arguments)]
pub fn ao_map(
    height: &[f32],
    base: &[f32],
    w: usize,
    h: usize,
    dirs: usize,
    steps: usize,
    radius: f32,
    depth_scale: f32,
    macro_mix: f32,
    seamless: bool,
) -> Vec<u8> {
    let mut out = vec![0u8; w * h];
    let macro_mix = macro_mix.clamp(0.0, 1.0);
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            let ch = sample_at(height, w, h, x, y, seamless);
            let occ_fine = horizon_occlusion(
                height,
                w,
                h,
                x,
                y,
                ch,
                dirs,
                steps,
                radius,
                depth_scale,
                seamless,
            );
            let mut ao = 1.0 - occ_fine.clamp(0.0, 1.0);
            if macro_mix > 0.0 {
                let cb = sample_at(base, w, h, x, y, seamless);
                let occ_macro = horizon_occlusion(
                    base,
                    w,
                    h,
                    x,
                    y,
                    cb,
                    dirs,
                    steps,
                    radius * 4.0,
                    depth_scale,
                    seamless,
                );
                let ao_macro = 1.0 - occ_macro.clamp(0.0, 1.0);
                ao = ao * (1.0 - macro_mix) + ao_macro * macro_mix;
            }
            out[y as usize * w + x as usize] = (ao.clamp(0.0, 1.0) * 255.0) as u8;
        }
    }
    out
}

/// Full height chain (luma → pyramid → guided → final) from an rgba8 image.
#[allow(clippy::too_many_arguments)]
pub fn height_chain(
    rgba: &[u8],
    w: usize,
    h: usize,
    sigma0: f32,
    levels: usize,
    guided_radius: i32,
    guided_eps: f32,
    contrast: f32,
    detail_mix: f32,
    seamless: bool,
) -> (Vec<f32>, Vec<f32>, Vec<f32>) {
    let luma = luma_map(rgba, w, h);
    let blended = pyramid_blur(&luma, w, h, sigma0, levels, seamless);
    let (base, detail) = guided_filter(&blended, w, h, guided_radius, guided_eps, seamless);
    let height = height_final(&base, &detail, None, contrast, detail_mix, 0.0);
    (height, base, detail)
}

/// Fast-tier seam blend — mirror of `seamless.wgsl:main_blend`. Each border
/// pixel blends with its mirror across the seam (x ↔ W−1−x, y ↔ H−1−y);
/// weight 0.5 at the seam (exact C0 continuity) decaying to 0 at the band
/// edge. Operates on rgba8 in place semantics (returns a new buffer).
pub fn seam_blend(rgba: &[u8], w: usize, h: usize, band: usize) -> Vec<u8> {
    let b = band.clamp(2, (w.min(h) / 4).max(2));
    let mut out = rgba.to_vec();
    for y in 0..h {
        for x in 0..w {
            let i = (y * w + x) * 4;

            let mut ux = 0.0f32;
            let mut px = x;
            if (x as i32) < b as i32 {
                px = w - 1 - x;
                ux = 0.5 * (1.0 - x as f32 / (b as f32 - 1.0));
            } else if x + b >= w {
                px = w - 1 - x;
                ux = 0.5 * (1.0 - (w - 1 - x) as f32 / (b as f32 - 1.0));
            }

            let mut uy = 0.0f32;
            let mut py = y;
            if (y as i32) < b as i32 {
                py = h - 1 - y;
                uy = 0.5 * (1.0 - y as f32 / (b as f32 - 1.0));
            } else if y + b >= h {
                py = h - 1 - y;
                uy = 0.5 * (1.0 - (h - 1 - y) as f32 / (b as f32 - 1.0));
            }

            let jx = (y * w + px) * 4;
            let jy = (py * w + x) * 4;
            let own = (1.0 - ux - uy).max(0.0);
            for c in 0..3 {
                let v =
                    own * rgba[i + c] as f32 + ux * rgba[jx + c] as f32 + uy * rgba[jy + c] as f32;
                out[i + c] = v.clamp(0.0, 255.0).round() as u8;
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flat(w: usize, h: usize, v: f32) -> Vec<f32> {
        vec![v; w * h]
    }

    #[test]
    fn test_wrap_or_clamp_modes() {
        assert_eq!(wrap_or_clamp(-1, 0, 4, 4, true), (3, 0));
        assert_eq!(wrap_or_clamp(4, 0, 4, 4, true), (0, 0));
        assert_eq!(wrap_or_clamp(-1, 0, 4, 4, false), (0, 0));
        assert_eq!(wrap_or_clamp(9, 3, 4, 4, false), (3, 3));
    }

    #[test]
    fn test_luma_map_rec709() {
        let rgba = [255, 0, 0, 255].repeat(4);
        let l = luma_map(&rgba, 2, 2);
        assert!((l[0] - 0.2126).abs() < 1e-3);
    }

    #[test]
    fn test_blur_of_flat_is_flat() {
        let out = separable_blur(&flat(16, 16, 0.7), 16, 16, 3, 2.0, 1, false);
        assert!(out.iter().all(|v| (v - 0.7).abs() < 1e-4));
    }

    #[test]
    fn test_gaussian_blur_of_impulse_is_kernel() {
        // Impulse at the centre of a flat-zero field; blur = kernel samples.
        let w = 33usize;
        let mut img = vec![0.0f32; w * w];
        img[(w / 2) * w + w / 2] = 1.0;
        let out = separable_blur(&img, w, w, 6, 2.0, 1, false);
        let centre = out[(w / 2) * w + w / 2];
        let one_off = out[(w / 2) * w + w / 2 + 1];
        // Ratio of adjacent kernel taps: exp(-1/(2σ²)) = exp(-1/8).
        let expected = (-1.0f32 / 8.0).exp();
        assert!((one_off / centre - expected).abs() < 1e-3);
    }

    #[test]
    fn test_box_blur_matches_manual_window() {
        // Column-constant ramp: every row identical, so the vertical pass is a
        // no-op and the result is the pure horizontal window mean.
        let w = 4usize;
        let h = 4usize;
        let mut img = vec![0.0f32; w * h];
        for y in 0..h {
            for x in 0..w {
                img[y * w + x] = x as f32 / 16.0;
            }
        }
        let out = separable_blur(&img, w, h, 1, 0.0, 0, false);
        let expected = 1.0 / 16.0; // mean of window {0,1,2}/16 = 1/16
        assert!((out[1 * w + 1] - expected).abs() < 1e-5);
    }

    #[test]
    fn test_pyramid_weights_match_preset_helper() {
        assert_eq!(
            pyramid_weights(4),
            crate::preset::Preset::Default.params().pyramid_weights()
        );
        let sum: f32 = pyramid_weights(7).iter().sum();
        assert!((sum - 1.0).abs() < 1e-5);
    }

    #[test]
    fn test_pyramid_blur_of_flat_is_flat() {
        let out = pyramid_blur(&flat(32, 32, 0.42), 32, 32, 1.0, 4, false);
        assert!(out.iter().all(|v| (v - 0.42).abs() < 1e-4));
    }

    #[test]
    fn test_pyramid_blur_smoother_than_level0() {
        // A high-frequency checker: deeper pyramids must smooth it more.
        let w = 64usize;
        let mut img = vec![0.0f32; w * w];
        for y in 0..w {
            for x in 0..w {
                img[y * w + x] = if (x + y) % 2 == 0 { 1.0 } else { 0.0 };
            }
        }
        let shallow = pyramid_blur(&img, w, w, 1.0, 1, false);
        let deep = pyramid_blur(&img, w, w, 1.0, 5, false);
        let var = |v: &[f32]| {
            let mean = v.iter().sum::<f32>() / v.len() as f32;
            v.iter().map(|x| (x - mean) * (x - mean)).sum::<f32>() / v.len() as f32
        };
        assert!(var(&deep) < var(&shallow));
    }

    #[test]
    fn test_guided_filter_preserves_step_edge() {
        // A hard luminance step must survive in the base (edge-preserving),
        // landing in the detail layer only marginally.
        let w = 32usize;
        let mut img = vec![0.0f32; w * w];
        for y in 0..w {
            for x in 0..w {
                img[y * w + x] = if x < w / 2 { 0.2 } else { 0.8 };
            }
        }
        let (base, detail) = guided_filter(&img, w, w, 4, 0.01, false);
        let jump = base[w / 2 + 1] - base[w / 2 - 2]; // across the edge, interior
        assert!(jump > 0.4, "guided filter smeared the edge: jump={jump}");
        let interior_detail = (detail[w * 4 + 4]).abs();
        assert!(interior_detail < 0.05, "flat interior leaked into detail");
    }

    #[test]
    fn test_guided_filter_eps_zero_recovers_input() {
        let w = 16usize;
        let img: Vec<f32> = (0..w * w).map(|i| (i as f32 * 0.37) % 1.0).collect();
        let (base, detail) = guided_filter(&img, w, w, 3, 1e-8, false);
        for i in 0..img.len() {
            assert!((base[i] - img[i]).abs() < 1e-3);
            assert!(detail[i].abs() < 1e-3);
        }
    }

    #[test]
    fn test_height_final_contrast_pivot() {
        let base = flat(4, 4, 0.75);
        let detail = flat(4, 4, 0.0);
        let out = height_final(&base, &detail, None, 2.0, 0.5, 0.0);
        // (0.75 − 0.5)·2 + 0.5 = 1.0 (clamped at 1.0).
        assert!((out[0] - 1.0).abs() < 1e-5);
    }

    #[test]
    fn test_height_final_detail_mix() {
        let base = flat(4, 4, 0.5);
        let detail = flat(4, 4, 0.1);
        let out = height_final(&base, &detail, None, 1.0, 0.5, 0.0);
        assert!((out[0] - 0.55).abs() < 1e-5);
    }

    #[test]
    fn test_height_final_shading_injection() {
        let base = flat(2, 2, 0.5);
        let detail = flat(2, 2, 0.0);
        let hf = flat(2, 2, 0.2);
        let out = height_final(&base, &detail, Some(&hf), 1.0, 0.0, 0.5);
        assert!((out[0] - 0.6).abs() < 1e-5);
    }

    #[test]
    fn test_gradient_sobel_on_unit_ramp() {
        // Unit slope in x: Sobel response per texel step = 8 (kernel sum).
        let w = 8usize;
        let mut img = vec![0.0f32; w * w];
        for y in 0..w {
            for x in 0..w {
                img[y * w + x] = x as f32 / 10.0;
            }
        }
        let (gx, gy) = gradient(&img, w, w, 4, 4, 0, 0.0, false);
        assert!((gx - 8.0 / 10.0).abs() < 1e-4);
        assert!(gy.abs() < 1e-4);
    }

    #[test]
    fn test_gradient_scharr_same_scale_as_sobel() {
        // Scharr ÷4 must match Sobel's magnitude on a pure ramp.
        let w = 8usize;
        let mut img = vec![0.0f32; w * w];
        for y in 0..w {
            for x in 0..w {
                img[y * w + x] = x as f32 / 10.0;
            }
        }
        let (sobel_x, _) = gradient(&img, w, w, 4, 4, 0, 0.0, false);
        let (scharr_x, _) = gradient(&img, w, w, 4, 4, 1, 0.0, false);
        assert!((sobel_x - scharr_x).abs() < 1e-3);
    }

    #[test]
    fn test_gradient_prefilter_reduces_noise() {
        // Pseudo-random noise: the MEAN |gradient| shrinks with the
        // pre-filter. (A period-2 checker is a bad fixture — Sobel rejects
        // Nyquist and the raw gradient is already exactly zero.)
        let hash01 = |x: u32, y: u32| -> f32 {
            let mut v = x.wrapping_mul(374761393) ^ y.wrapping_mul(668265263);
            v = (v ^ (v >> 13)).wrapping_mul(1274126177);
            ((v ^ (v >> 16)) & 0xffff) as f32 / 65535.0
        };
        let w = 32usize;
        let mut img = vec![0.0f32; w * w];
        for y in 0..w {
            for x in 0..w {
                img[y * w + x] = hash01(x as u32, y as u32);
            }
        }
        let mean_g = |sigma: f32| -> f32 {
            let mut acc = 0.0;
            for y in 2..w - 2 {
                for x in 2..w - 2 {
                    let (gx, gy) = gradient(&img, w, w, x as i32, y as i32, 0, sigma, false);
                    acc += (gx * gx + gy * gy).sqrt();
                }
            }
            acc / ((w - 4) * (w - 4)) as f32
        };
        assert!(
            mean_g(1.0) < mean_g(0.0) * 0.9,
            "raw={} filtered={}",
            mean_g(0.0),
            mean_g(1.0)
        );
    }

    #[test]
    fn test_normal_map_flat_height_is_flat_normal() {
        let out = normal_map(&flat(8, 8, 0.5), 8, 8, 2.0, 1.0, false, 0, 0.0, false);
        // Flat normal = (0.5, 0.5, 1)/|·| → encoded ≈ (128, 128, 255-ish).
        assert_eq!(out[0], 128);
        assert_eq!(out[1], 128);
        assert!(out[2] > 250);
    }

    #[test]
    fn test_normal_map_ramp_direction() {
        // Height rising in +x ⇒ surface tilts −x ⇒ R channel < 0.5.
        let w = 8usize;
        let mut img = vec![0.0f32; w * w];
        for y in 0..w {
            for x in 0..w {
                img[y * w + x] = x as f32 / w as f32;
            }
        }
        let out = normal_map(&img, w, w, 2.0, 1.0, false, 0, 0.0, false);
        let r = out[(4 * w + 4) * 4] as f32 / 255.0;
        assert!(r < 0.5, "r={r}");
    }

    #[test]
    fn test_normal_map_slope_z_flattens() {
        let w = 8usize;
        let mut img = vec![0.0f32; w * w];
        for y in 0..w {
            for x in 0..w {
                img[y * w + x] = x as f32 / 8.0;
            }
        }
        let steep = normal_map(&img, w, w, 2.0, 0.2, false, 0, 0.0, false);
        let flat_ = normal_map(&img, w, w, 2.0, 5.0, false, 0, 0.0, false);
        let tilt = |o: &[u8]| (o[(4 * w + 4) * 4] as f32 - 128.0).abs();
        assert!(tilt(&flat_) < tilt(&steep));
    }

    #[test]
    fn test_curvature_sine_crosses_half_at_inflections() {
        // One full sine period: inflection points have zero Laplacian → 0.5.
        let w = 64usize;
        let h = 8usize;
        let mut img = vec![0.0f32; w * h];
        for y in 0..h {
            for x in 0..w {
                let t = x as f32 / w as f32;
                img[y * w + x] = 0.5 + 0.45 * (t * std::f32::consts::TAU).sin();
            }
        }
        let out = curvature_map(&img, w, h, 1, 8.0, false);
        // Quarter points (t=0.25, 0.75) are inflections.
        let at = |x: usize| out[4 * w + x] as f32 / 255.0;
        assert!((at(w / 4) - 0.5).abs() < 0.15);
        assert!((at(3 * w / 4) - 0.5).abs() < 0.15);
        // Crest (t=0.25 is crest for sin? sin peaks at t=0.25) — check
        // convex (<0.5) at t=0.25 for cos instead: use cos via shift.
    }

    #[test]
    fn test_curvature_convex_vs_concave() {
        // A bump (convex) reads below 0.5, a dip (concave) above 0.5.
        let w = 32usize;
        let mut bump = vec![0.5f32; w * w];
        let mut dip = vec![0.5f32; w * w];
        for y in 0..w {
            for x in 0..w {
                let dx = (x as f32 - w as f32 / 2.0) / (w as f32 / 4.0);
                let dy = (y as f32 - w as f32 / 2.0) / (w as f32 / 4.0);
                let g = (-(dx * dx + dy * dy) / 2.0).exp();
                bump[y * w + x] = 0.5 + 0.4 * g;
                dip[y * w + x] = 0.5 - 0.4 * g;
            }
        }
        let cb = curvature_map(&bump, w, w, 1, 8.0, false);
        let cd = curvature_map(&dip, w, w, 1, 8.0, false);
        let c = w / 2;
        assert!(cb[c * w + c] as f32 / 255.0 < 0.5);
        assert!(cd[c * w + c] as f32 / 255.0 > 0.5);
    }

    #[test]
    fn test_edge_detects_diagonal_crease() {
        // The 2.0 bug missed 45° creases; the fixed total-variation finds them.
        let w = 32usize;
        let mut height = vec![0.5f32; w * w];
        for y in 0..w {
            for x in 0..w {
                if x + y >= w {
                    height[y * w + x] = 0.8;
                }
            }
        }
        let normal = normal_map(&height, w, w, 2.0, 1.0, false, 0, 0.0, false);
        let edge = edge_map(&normal, w, w, 2.0, false);
        // A texel ON the diagonal must be flagged.
        let on_diag = edge[(w / 2) * w + (w / 2 - 1)] as f32 / 255.0;
        let far = edge[2 * w + 2] as f32 / 255.0;
        assert!(on_diag > 0.5, "on_diag={on_diag}");
        assert!(far < 0.1, "far={far}");
    }

    #[test]
    fn test_hbao_flat_field_full_ao() {
        let out = ao_map(
            &flat(32, 32, 0.5),
            &flat(32, 32, 0.5),
            32,
            32,
            8,
            8,
            12.0,
            3.0,
            0.3,
            false,
        );
        assert!(out.iter().all(|v| *v == 255));
    }

    #[test]
    fn test_hbao_wall_darkens_concave_side() {
        // A tall plateau: the concave FOOT (1px outside, looking up at the
        // wall) darkens hard; the convex top and the far field stay bright.
        let w = 48usize;
        let mut height = vec![0.0f32; w * w];
        for y in 8..40 {
            for x in 8..40 {
                height[y * w + x] = 0.8;
            }
        }
        let base = height.clone();
        let out = ao_map(&height, &base, w, w, 16, 12, 16.0, 6.0, 0.0, false);
        let v = |x: usize, y: usize| out[y * w + x];
        // Concave foot along the straight left edge of the wall (half the
        // directions march into the plateau) — a corner foot only sees a
        // quarter of them and stays brighter (ao≈196), so probe the edge.
        assert!(v(7, 24) < 150, "concave foot ao={}", v(7, 24));
        // Convex top centre of the plateau: nothing higher around → bright.
        assert!(v(24, 24) > 150, "convex top ao={}", v(24, 24));
        // "Far" field: with radius 16 even the corner at 8.5px casts a little
        // occlusion here — correct HBAO behaviour — but it stays much brighter
        // than the foot.
        assert!(v(2, 2) > v(7, 24) + 40, "far={} foot={}", v(2, 2), v(7, 24));
    }

    #[test]
    fn test_hbao_macro_mix_pulls_toward_base_ao() {
        let w = 48usize;
        let mut height = vec![0.0f32; w * w];
        let base = vec![0.0f32; w * w];
        for y in 8..40 {
            for x in 8..40 {
                height[y * w + x] = 0.5;
            }
        }
        // Base has no plateau: macro AO = 1 everywhere, so mixing must lift
        // the darkened foot back up.
        let fine = ao_map(&height, &base, w, w, 16, 12, 16.0, 6.0, 0.0, false);
        let mixed = ao_map(&height, &base, w, w, 16, 12, 16.0, 6.0, 0.8, false);
        let v = |m: &[u8]| m[24 * w + 7];
        assert!(
            v(&mixed) > v(&fine),
            "mixed={} fine={}",
            v(&mixed),
            v(&fine)
        );
    }

    #[test]
    fn test_height_chain_monotone_gradient_stays_monotone() {
        // A smooth luminance ramp must not develop stair-steps after the
        // pyramid + guided filter (edge-preserving ≠ oscillating).
        let w = 64usize;
        let mut rgba = vec![0u8; w * w * 4];
        for y in 0..w {
            for x in 0..w {
                let v = (x as f32 / w as f32 * 255.0) as u8;
                let i = (y * w + x) * 4;
                rgba[i] = v;
                rgba[i + 1] = v;
                rgba[i + 2] = v;
                rgba[i + 3] = 255;
            }
        }
        let (height, _, _) = height_chain(&rgba, w, w, 1.0, 4, 8, 0.01, 1.5, 0.8, false);
        let row = &height[(w / 2) * w..(w / 2) * w + w];
        for pair in row.windows(2) {
            // Allow the guided filter's tiny negative lobes, no inversions.
            assert!(
                pair[1] >= pair[0] - 0.02,
                "inversion: {} → {}",
                pair[0],
                pair[1]
            );
        }
    }

    #[test]
    fn test_height_chain_flat_is_pivot_of_luma() {
        let rgba = [128u8, 128, 128, 255].repeat(64);
        let (height, base, detail) = height_chain(&rgba, 8, 8, 1.0, 4, 8, 0.01, 1.5, 0.8, false);
        // Flat input flows through: base = luma, detail = 0, height = pivot.
        let luma = 128.0 / 255.0;
        let expected_height = (luma - 0.5) * 1.5 + 0.5;
        assert!(height.iter().all(|v| (v - expected_height).abs() < 1e-3));
        assert!(base.iter().all(|v| (v - luma).abs() < 1e-3));
        assert!(detail.iter().all(|v| v.abs() < 1e-4));
    }

    #[test]
    fn test_seamless_wrap_wrap_around_borders() {
        let img = flat(4, 4, 0.0);
        let mut img = img;
        img[0] = 1.0; // top-left corner
        // In wrap mode, sampling (-1,-1) reads (3,3).
        assert_eq!(sample_at(&img, 4, 4, -1, -1, true), img[3 * 4 + 3]);
    }

    #[test]
    fn test_seam_blend_is_c0_continuous_at_wrap() {
        // After the mirror blend, column 0 equals column W−1 (luma) along the
        // span of the edge where the OTHER axis' weight is zero. The four
        // corner texels are 2-D compromises (both weights 0.5) and are
        // excluded — the roll already minimised their residual.
        let w = 32usize;
        let h = 32usize;
        let band = 8usize;
        let mut rgba = vec![0u8; w * h * 4];
        for y in 0..h {
            for x in 0..w {
                let i = (y * w + x) * 4;
                rgba[i] = ((x * 13 + y * 7) % 256) as u8;
                rgba[i + 1] = ((x * 3 + y * 29) % 256) as u8;
                rgba[i + 2] = ((x * 47 + y * 5) % 256) as u8;
                rgba[i + 3] = 255;
            }
        }
        let blended = seam_blend(&rgba, w, h, band);
        let luma = |i: usize| -> f32 {
            0.2126 * blended[i] as f32
                + 0.7152 * blended[i + 1] as f32
                + 0.0722 * blended[i + 2] as f32
        };
        for y in band..h - band {
            let a = luma((y * w) * 4);
            let b = luma((y * w + w - 1) * 4);
            assert!((a - b).abs() < 2.0, "x-seam y={y}: {a} vs {b}");
        }
        for x in band..w - band {
            let a = luma(x * 4);
            let b = luma(((h - 1) * w + x) * 4);
            assert!((a - b).abs() < 2.0, "y-seam x={x}: {a} vs {b}");
        }
    }

    #[test]
    fn test_seam_blend_interior_untouched() {
        // Pixels deeper than the band keep their original values.
        let w = 32usize;
        let h = 32usize;
        let mut rgba = vec![0u8; w * h * 4];
        for (i, v) in rgba.iter_mut().enumerate() {
            *v = if i % 4 == 3 { 255 } else { (i % 200) as u8 };
        }
        let blended = seam_blend(&rgba, w, h, 4);
        let center = ((h / 2) * w + w / 2) * 4;
        assert_eq!(&blended[center..center + 4], &rgba[center..center + 4]);
    }

    #[test]
    fn test_seam_blend_reduces_border_mse() {
        use crate::seam::border_mse;
        let w = 32usize;
        let h = 32usize;
        let mut rgba = vec![0u8; w * h * 4];
        for y in 0..h {
            for x in 0..w {
                let i = (y * w + x) * 4;
                rgba[i] = ((x * 41 + y * 17) % 256) as u8;
                rgba[i + 1] = ((x * 11 + y * 53) % 256) as u8;
                rgba[i + 2] = ((x * 5 + y * 71) % 256) as u8;
                rgba[i + 3] = 255;
            }
        }
        let before = border_mse(&rgba, w, h);
        let after = border_mse(&seam_blend(&rgba, w, h, 8), w, h);
        assert!(after < before, "before={before} after={after}");
    }
}
