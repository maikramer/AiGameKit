//! GPU↔CPU parity tests. Gated behind `MATERIALIZE_GPU_TESTS=1` so CI (no GPU)
//! skips them; run locally with:
//!     MATERIALIZE_GPU_TESTS=1 cargo test --test gpu_parity

use image::{DynamicImage, Rgba, RgbaImage};

use materialize_cli::io::MapSelection;
use materialize_cli::pipeline::Pipeline;
use materialize_cli::preset::{Preset, PresetParams};
use materialize_cli::reference as r;

fn gpu_gated() -> bool {
    std::env::var("MATERIALIZE_GPU_TESTS")
        .map(|v| v != "0")
        .unwrap_or(false)
}

/// One GPU device at a time — a single consumer GPU (shared with vramd
/// workers) cannot host several concurrent wgpu devices.
static GPU_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// Synthetic 64×64 texture: horizontal gradient + checker quadrant + dark
/// plateau — exercises gradients, high-frequency and large-scale structure.
fn synthetic(w: u32, h: u32) -> DynamicImage {
    let mut img = RgbaImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let base = x as f32 / w as f32;
            let checker = if ((x / 4) + (y / 4)) % 2 == 0 {
                0.25
            } else {
                -0.25
            };
            let plateau = if x > w / 2 && y > h / 2 { 0.3 } else { 0.0 };
            let v = ((base + checker * 0.3 + plateau).clamp(0.0, 1.0) * 255.0) as u8;
            img.put_pixel(x, y, Rgba([v, (v / 2).min(255), (255 - v).min(255), 255]));
        }
    }
    DynamicImage::ImageRgba8(img)
}

fn params_for(preset: Preset) -> PresetParams {
    // gray gains are applied by the CLI against a real image; keep them neutral
    // here so the reference (which does not apply gains) matches the shader.
    let mut p = preset.params();
    p.gray_gain_r = 1.0;
    p.gray_gain_g = 1.0;
    p.gray_gain_b = 1.0;
    p
}

fn max_abs_diff(a: &[f32], b: &[f32]) -> f32 {
    a.iter()
        .zip(b)
        .map(|(x, y)| (x - y).abs())
        .fold(0.0f32, f32::max)
}

fn max_u8_diff(a: &[u8], b: &[u8]) -> i32 {
    a.iter()
        .zip(b)
        .map(|(x, y)| (*x as i32 - *y as i32).abs())
        .fold(0, i32::max)
}

#[tokio::test]
async fn parity_height_chain() {
    if !gpu_gated() {
        eprintln!("skipping (MATERIALIZE_GPU_TESTS not set)");
        return;
    }
    let _guard = GPU_LOCK.lock().await;
    let (w, h) = (64u32, 64u32);
    let img = synthetic(w, h);
    let params = params_for(Preset::Default);
    let pipeline = Pipeline::new().await.expect("pipeline");
    let sel = MapSelection {
        height: true,
        ..Default::default()
    };
    let (maps, _) = pipeline
        .process(&img, &params, &sel)
        .await
        .expect("process");

    let rgba = img.to_rgba8();
    let raw = rgba.as_raw();
    let (ref_height, _, _) = r::height_chain(
        raw,
        w as usize,
        h as usize,
        params.height_sigma_base,
        params.height_pyramid_levels as usize,
        params.guided_radius as i32,
        params.guided_eps,
        params.height_contrast,
        params.detail_mix,
        false,
    );
    let diff = max_abs_diff(&maps.height, &ref_height);
    assert!(diff < 8e-3, "height max abs diff = {diff}");
}

#[tokio::test]
async fn parity_normal() {
    if !gpu_gated() {
        eprintln!("skipping (MATERIALIZE_GPU_TESTS not set)");
        return;
    }
    let _guard = GPU_LOCK.lock().await;
    let (w, h) = (64u32, 64u32);
    let img = synthetic(w, h);
    let params = params_for(Preset::Default);
    let pipeline = Pipeline::new().await.expect("pipeline");
    let sel = MapSelection {
        height: true,
        normal: true,
        ..Default::default()
    };
    let (maps, _) = pipeline
        .process(&img, &params, &sel)
        .await
        .expect("process");

    let rgba = img.to_rgba8();
    let raw = rgba.as_raw();
    let (ref_height, _, _) = r::height_chain(
        raw,
        w as usize,
        h as usize,
        params.height_sigma_base,
        params.height_pyramid_levels as usize,
        params.guided_radius as i32,
        params.guided_eps,
        params.height_contrast,
        params.detail_mix,
        false,
    );
    let ref_normal = r::normal_map(
        &ref_height,
        w as usize,
        h as usize,
        params.normal_strength,
        params.normal_slope_z,
        false,
        params.normal_operator,
        params.normal_prefilter_sigma,
        false,
    );
    let diff = max_u8_diff(&maps.normal, &ref_normal);
    assert!(diff <= 3, "normal max channel diff = {diff}");
}

#[tokio::test]
async fn parity_ao() {
    if !gpu_gated() {
        eprintln!("skipping (MATERIALIZE_GPU_TESTS not set)");
        return;
    }
    let _guard = GPU_LOCK.lock().await;
    let (w, h) = (64u32, 64u32);
    let img = synthetic(w, h);
    let params = params_for(Preset::Default);
    let pipeline = Pipeline::new().await.expect("pipeline");
    let sel = MapSelection {
        height: true,
        ao: true,
        ..Default::default()
    };
    let (maps, _) = pipeline
        .process(&img, &params, &sel)
        .await
        .expect("process");

    let rgba = img.to_rgba8();
    let raw = rgba.as_raw();
    let (ref_height, ref_base, _) = r::height_chain(
        raw,
        w as usize,
        h as usize,
        params.height_sigma_base,
        params.height_pyramid_levels as usize,
        params.guided_radius as i32,
        params.guided_eps,
        params.height_contrast,
        params.detail_mix,
        false,
    );
    let ref_ao = r::ao_map(
        &ref_height,
        &ref_base,
        w as usize,
        h as usize,
        params.ao_directions as usize,
        params.ao_steps as usize,
        params.ao_radius,
        params.ao_depth_scale,
        params.ao_macro_mix,
        false,
    );
    let diff = max_u8_diff(&maps.ao, &ref_ao);
    assert!(diff <= 4, "ao max diff = {diff}");
}

#[tokio::test]
async fn parity_curvature_and_edge() {
    if !gpu_gated() {
        eprintln!("skipping (MATERIALIZE_GPU_TESTS not set)");
        return;
    }
    let _guard = GPU_LOCK.lock().await;
    let (w, h) = (64u32, 64u32);
    let img = synthetic(w, h);
    let params = params_for(Preset::Default);
    let pipeline = Pipeline::new().await.expect("pipeline");
    let sel = MapSelection {
        height: true,
        normal: true,
        edge: true,
        curvature: true,
        ..Default::default()
    };
    let (maps, _) = pipeline
        .process(&img, &params, &sel)
        .await
        .expect("process");

    let rgba = img.to_rgba8();
    let raw = rgba.as_raw();
    let (ref_height, _, _) = r::height_chain(
        raw,
        w as usize,
        h as usize,
        params.height_sigma_base,
        params.height_pyramid_levels as usize,
        params.guided_radius as i32,
        params.guided_eps,
        params.height_contrast,
        params.detail_mix,
        false,
    );
    let ref_curv = r::curvature_map(
        &ref_height,
        w as usize,
        h as usize,
        params.curvature_levels as usize,
        params.curvature_gain,
        false,
    );
    let diff = max_u8_diff(&maps.curvature, &ref_curv);
    assert!(diff <= 4, "curvature max diff = {diff}");

    // Edge parity in isolation: feed the GPU's own normal map so only the
    // edge kernel is under test (normal parity has its own test).
    let ref_edge = r::edge_map(
        &maps.normal,
        w as usize,
        h as usize,
        params.edge_contrast,
        false,
    );
    let diff = max_u8_diff(&maps.edge, &ref_edge);
    assert!(diff <= 4, "edge max diff = {diff}");
}

#[tokio::test]
async fn parity_seamless_wrap_matches_reference() {
    if !gpu_gated() {
        eprintln!("skipping (MATERIALIZE_GPU_TESTS not set)");
        return;
    }
    let _guard = GPU_LOCK.lock().await;
    let (w, h) = (64u32, 64u32);
    let img = synthetic(w, h);
    let mut params = params_for(Preset::Default);
    params.seamless = 1;
    let pipeline = Pipeline::new().await.expect("pipeline");
    let sel = MapSelection {
        height: true,
        ..Default::default()
    };
    let (maps, _) = pipeline
        .process(&img, &params, &sel)
        .await
        .expect("process");

    let rgba = img.to_rgba8();
    let raw = rgba.as_raw();
    let (ref_height, _, _) = r::height_chain(
        raw,
        w as usize,
        h as usize,
        params.height_sigma_base,
        params.height_pyramid_levels as usize,
        params.guided_radius as i32,
        params.guided_eps,
        params.height_contrast,
        params.detail_mix,
        true,
    );
    let diff = max_abs_diff(&maps.height, &ref_height);
    assert!(diff < 8e-3, "seamless height max abs diff = {diff}");
}
