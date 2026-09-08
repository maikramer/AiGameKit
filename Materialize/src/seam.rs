//! Make-seamless (F4): minimum-error offset search + roll, CPU side.
//!
//! Kwatra et al., *Graphcut Textures* (SIGGRAPH 2003) contribute the
//! minimum-error boundary cut. For straight seams the search is separable:
//! the vertical wrap seam of the rolled image sits between columns (dx−1, dx)
//! of the ORIGINAL, so its cost is `V(dx) = Σ_y |I(dx,y) − I(dx−1,y)|`; the
//! horizontal seam costs `H(dy) = Σ_x |I(x,dy) − I(x,dy−1)|`. Two O(W·H)
//! passes produce all seam costs at once; the argmins (excluding trivial
//! near-zero shifts) are the best roll offset.
//!
//! After rolling, the seams sit at the borders with minimal (but non-zero)
//! mismatch; the compute pre-pass (`seamless.wgsl`: mirror cross-fade,
//! optional Poisson) then makes the wrap C0-continuous.

/// Squared-RGB distance between two texels.
fn rgb_dist2(rgba: &[u8], a: usize, b: usize) -> f64 {
    let dr = rgba[a * 4] as f64 - rgba[b * 4] as f64;
    let dg = rgba[a * 4 + 1] as f64 - rgba[b * 4 + 1] as f64;
    let db = rgba[a * 4 + 2] as f64 - rgba[b * 4 + 2] as f64;
    dr * dr + dg * dg + db * db
}

/// Seam-cost profiles: V[c] = cost of cutting between columns c−1 and c
/// (summed over all rows), H[r] = cost of cutting between rows r−1 and r.
#[allow(clippy::needless_range_loop)]
pub fn seam_cost_profiles(rgba: &[u8], w: usize, h: usize) -> (Vec<f64>, Vec<f64>) {
    let mut v = vec![0.0f64; w];
    let mut hh = vec![0.0f64; h];
    for y in 0..h {
        for x in 0..w {
            let i = y * w + x;
            // Wrap: column 0 pairs with column w−1.
            let left = if x == 0 { y * w + (w - 1) } else { i - 1 };
            v[x] += rgb_dist2(rgba, i, left);
            let up = if y == 0 { (h - 1) * w + x } else { i - w };
            hh[y] += rgb_dist2(rgba, i, up);
        }
    }
    (v, hh)
}

#[allow(clippy::needless_range_loop)]
fn argmin_excluding_trivial(profile: &[f64], min_gap: i32) -> i32 {
    let n = profile.len() as i32;
    let mut best = -1i32;
    let mut best_cost = f64::MAX;
    // Scan the full period; skip indices within ±gap of the wrap point 0
    // (the no-op cut, which always scores ~0 against itself).
    for c in 0..n {
        let d = c.min(n - c); // distance to the trivial cut at 0
        if d < min_gap.max(1) {
            continue;
        }
        if profile[c as usize] < best_cost {
            best_cost = profile[c as usize];
            best = c;
        }
    }
    // Profile index c means the seam sits before column c; the equivalent
    // roll offset is −c (out[x] = in[x−c]).
    if best <= n / 2 { -best } else { n - best }
}

/// Find the roll offset minimising the wrap-seam cost. Trivial offsets
/// (|dx| ≤ w/8, |dy| ≤ h/8) are excluded — the no-op roll always wins.
pub fn find_best_offset(rgba: &[u8], w: usize, h: usize, _band: usize) -> (i32, i32) {
    let (v, hh) = seam_cost_profiles(rgba, w, h);
    let dx = argmin_excluding_trivial(&v, (w as i32 / 8).max(1));
    let dy = argmin_excluding_trivial(&hh, (h as i32 / 8).max(1));
    (dx, dy)
}

/// Roll (out[y][x] = in[(y+dy) mod h][(x+dx) mod w]).
pub fn roll_image(rgba: &[u8], w: usize, h: usize, dx: i32, dy: i32) -> Vec<u8> {
    let mut out = vec![0u8; rgba.len()];
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            let sx = (x + dx).rem_euclid(w as i32) as usize;
            let sy = (y + dy).rem_euclid(h as i32) as usize;
            let d = (y as usize * w + x as usize) * 4;
            let s = (sy * w + sx) * 4;
            out[d..d + 4].copy_from_slice(&rgba[s..s + 4]);
        }
    }
    out
}

/// `tile_mse`-style luma MSE across the wrap borders (mirror of
/// `analyze::tile_mse`, over rolled-and-blended RGBA bytes).
pub fn border_mse(rgba: &[u8], w: usize, h: usize) -> f32 {
    let rows = h.min(8);
    let cols = w.min(8);
    let luma = |x: usize, y: usize| -> f32 {
        let i = (y * w + x) * 4;
        0.2126 * rgba[i] as f32 / 255.0
            + 0.7152 * rgba[i + 1] as f32 / 255.0
            + 0.0722 * rgba[i + 2] as f32 / 255.0
    };
    let mut acc = 0.0f64;
    let mut n = 0u64;
    for r in 0..rows {
        for x in 0..w {
            let d = luma(x, r) - luma(x, h - 1 - r);
            acc += (d * d) as f64;
            n += 1;
        }
    }
    for c in 0..cols {
        for y in 0..h {
            let d = luma(c, y) - luma(w - 1 - c, y);
            acc += (d * d) as f64;
            n += 1;
        }
    }
    if n == 0 { 1.0 } else { (acc / n as f64) as f32 }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn from_luma(w: usize, h: usize, f: impl Fn(usize, usize) -> u8) -> Vec<u8> {
        let mut v = vec![255u8; w * h * 4];
        for y in 0..h {
            for x in 0..w {
                let i = (y * w + x) * 4;
                let l = f(x, y);
                v[i] = l;
                v[i + 1] = l;
                v[i + 2] = l;
            }
        }
        v
    }

    #[test]
    fn test_roll_is_np_roll_semantics() {
        // out[x] = in[(x+dx) mod w]: 4×1 image [A, B, C, D]; roll dx=1 → [B, C, D, A].
        let img: Vec<u8> = vec![10, 0, 0, 255, 20, 0, 0, 255, 30, 0, 0, 255, 40, 0, 0, 255];
        let out = roll_image(&img, 4, 1, 1, 0);
        assert_eq!(&out[0..4], &[20, 0, 0, 255]);
        assert_eq!(&out[4..8], &[30, 0, 0, 255]);
        assert_eq!(&out[8..12], &[40, 0, 0, 255]);
        assert_eq!(&out[12..16], &[10, 0, 0, 255]);
    }

    #[test]
    fn test_roll_inverse() {
        let img = from_luma(16, 16, |x, y| ((x * 7 + y * 13) % 256) as u8);
        let a = roll_image(&img, 16, 16, 5, -3);
        let b = roll_image(&a, 16, 16, -5, 3);
        assert_eq!(img, b);
    }

    #[test]
    fn test_seam_cost_profiles_find_flat_boundary() {
        // Flat background with a bright vertical stripe at columns 30..34:
        // background cuts cost exactly zero; the stripe edges are the most
        // expensive cuts in the image.
        let w = 64usize;
        let h = 32usize;
        let img = from_luma(w, h, |x, _| if (30..34).contains(&x) { 220 } else { 40 });
        let (v, _) = seam_cost_profiles(&img, w, h);
        let min_cost = v.iter().cloned().fold(f64::MAX, f64::min);
        assert_eq!(min_cost, 0.0, "flat background cuts are free");
        assert_eq!(v[10], 0.0);
        // Both stripe edges (entering at 30, leaving at 34) are the maxima.
        let max_cost = v.iter().cloned().fold(0.0f64, f64::max);
        assert_eq!(v[30], max_cost);
        assert_eq!(v[34], max_cost);
        assert!(v[32] < max_cost / 100.0, "inside the stripe is flat");
    }

    #[test]
    fn test_find_best_offset_moves_feature_to_border() {
        // Flat noise background with a bright stripe TOUCHING the right
        // border: border MSE is high (stripe | background). The best roll
        // shifts the stripe so both border columns land on background.
        let w = 64usize;
        let h = 64usize;
        let hash = |x: u32, y: u32| -> u8 {
            let mut v = x.wrapping_mul(374761393) ^ y.wrapping_mul(668265263);
            v = (v ^ (v >> 13)).wrapping_mul(1274126177);
            ((v ^ (v >> 16)) & 0xf) as u8
        };
        let img = from_luma(w, h, |x, y| {
            if x >= w - 4 {
                220
            } else {
                40 + hash(x as u32, y as u32)
            }
        });
        let before = border_mse(&img, w, h);
        assert!(
            before > 0.05,
            "fixture must start with a bad seam: {before}"
        );
        let (dx, dy) = find_best_offset(&img, w, h, 8);
        let rolled = roll_image(&img, w, h, dx, dy);
        let after = border_mse(&rolled, w, h);
        assert!(
            after < before * 0.6,
            "offset=({dx},{dy}) before={before:.4} after={after:.4}"
        );
    }

    #[test]
    fn test_find_best_offset_excludes_trivial() {
        // Noise: no periodicity anywhere; the search still must not return
        // the no-op (0,0) (or any trivial shift under w/8).
        let w = 64usize;
        let h = 64usize;
        let img = from_luma(w, h, |x, y| ((x * 31 + y * 57 + x * y) % 256) as u8);
        let (dx, dy) = find_best_offset(&img, w, h, 8);
        let trivial = dx.abs() < (w as i32 / 8) && dy.abs() < (h as i32 / 8);
        assert!(!trivial, "trivial offset chosen: ({dx},{dy})");
    }

    #[test]
    fn test_border_mse_zero_on_flat() {
        let img = from_luma(16, 16, |_, _| 128);
        assert!(border_mse(&img, 16, 16) < 1e-6);
    }
}
