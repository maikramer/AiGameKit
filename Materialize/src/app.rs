//! Application entry logic (CLI run loop). Kept in the library so
//! integration tests can drive it and import the same modules.

use std::path::Path;
use std::process::ExitCode;

use clap::{CommandFactory, Parser};
use clap_complete::{Shell, generate as generate_completion};
use image::DynamicImage;

use crate::analyze::{analyze, classify, format_report};
use crate::batch::{build_selection, expand_inputs, run_batch};
use crate::cli::{Cli, CliSubcommand, MAP_NAMES, PRESET_DESCRIPTIONS};
use crate::error::{MaterializeError, Result};
use crate::pipeline::Pipeline;
use crate::preset::{Preset, PresetParams};

pub async fn main_entry() -> ExitCode {
    init_logger();
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("Error: {e}");
            ExitCode::from(e.exit_code())
        }
    }
}

fn init_logger() {
    let level = std::env::var("MATERIALIZE_LOG").unwrap_or_else(|_| "warn".to_string());
    let filter = match level.to_lowercase().as_str() {
        "error" => "error",
        "warn" => "warn",
        "info" => "info",
        "debug" => "debug",
        "trace" => "trace",
        _ => "warn",
    };
    let _ = env_logger::Builder::new()
        .parse_filters(filter)
        .filter_level(log::LevelFilter::Warn)
        .try_init();
}

pub async fn run() -> Result<()> {
    let args = Cli::parse();

    // === Short-circuits that don't need an input image ===
    if args.list_presets {
        print_presets_table();
        return Ok(());
    }
    if args.list_maps {
        print_maps_table();
        return Ok(());
    }
    if let Some(shell) = args.generate_completions {
        emit_completions(shell);
        return Ok(());
    }

    // === Subcommands ===
    match &args.subcommand {
        Some(CliSubcommand::Skill(skill)) => {
            if matches!(skill.subcommand, crate::cli::SkillSubcommand::Install) {
                return crate::skill_install::run().map_err(MaterializeError::from);
            }
        }
        Some(CliSubcommand::Info { input }) => {
            return run_info(input);
        }
        Some(CliSubcommand::Decompose { input, output }) => {
            return run_decompose_cmd(input, output);
        }
        None => {}
    }

    let input = args.input.clone().ok_or_else(|| {
        MaterializeError::Other(anyhow::anyhow!(
            "Missing required argument: <INPUT>. Use 'materialize --help' for usage."
        ))
    })?;

    let input_path = Path::new(&input);
    let is_batch = input_path.is_dir() || contains_glob_metachar(&input);

    if is_batch {
        // Batch needs the pipeline up-front; GPU init happens here.
        let pipeline = Pipeline::new()
            .await
            .map_err(|e: anyhow::Error| MaterializeError::Gpu(e.to_string()))?;
        if args.verbose {
            println!("GPU: {}", pipeline.adapter_info);
        }
        run_batch_mode(&args, &pipeline).await
    } else {
        // Single-file mode: validate input exists BEFORE GPU init so a missing
        // file produces a NotFound exit code (2) even on CI runners without a GPU.
        run_single_mode(&args, &input).await
    }
}

fn contains_glob_metachar(s: &str) -> bool {
    s.contains('*') || s.contains('?') || s.contains('[')
}

fn run_info(input: &str) -> Result<()> {
    let image = crate::io::load_image(input).map_err(MaterializeError::from)?;
    let features = analyze(&image);
    let classification = classify(&features);
    println!("{}", format_report(&classification));
    Ok(())
}

/// `materialize decompose <img> -o <dir>` — delegação pura ao vramd.
fn run_decompose_cmd(input: &str, output: &str) -> Result<()> {
    let image = crate::io::load_image(input).map_err(MaterializeError::from)?;
    let _ = &image; // valida a existência/legibilidade antes de ir à rede
    let paths = crate::vramd::decompose_via_vramd(
        Path::new(input),
        Path::new(output),
        std::time::Duration::from_secs(600),
    )
    .map_err(|e| {
        MaterializeError::Other(anyhow::anyhow!(
            "{e}\n  hints: 'vramd start' | ./install.sh intrinsic (licença académica — Intrinsic/README.md)"
        ))
    })?;
    println!("Decomposed:");
    println!("  - {}", paths.albedo.display());
    println!("  - {}", paths.shading.display());
    println!("  - {}", paths.specular.display());
    Ok(())
}

/// Tenta a decomposição intrínseca via vramd; `None` = indisponível (o
/// chamador continua com o pipeline heurístico + warning).
fn try_intrinsic(
    input: &str,
    output_dir: &str,
    verbose: bool,
) -> Option<crate::pipeline::IntrinsicMaps> {
    let stem = Path::new(input)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("image");
    let intrinsic_dir = format!("{output_dir}/{stem}_intrinsic");
    match crate::vramd::decompose_via_vramd(
        Path::new(input),
        Path::new(&intrinsic_dir),
        std::time::Duration::from_secs(600),
    ) {
        Ok(paths) => {
            if verbose {
                println!("Intrinsic: albedo/shading/specular de {}", intrinsic_dir);
            }
            let albedo = crate::io::load_image(&paths.albedo.to_string_lossy()).ok()?;
            let shading = crate::io::load_image(&paths.shading.to_string_lossy()).ok()?;
            let specular = crate::io::load_image(&paths.specular.to_string_lossy()).ok()?;
            Some(crate::pipeline::IntrinsicMaps {
                albedo,
                shading,
                specular,
            })
        }
        Err(e) => {
            eprintln!("Warning: --intrinsic indisponível ({e}); a continuar heurístico");
            None
        }
    }
}

async fn run_single_mode(args: &Cli, input: &str) -> Result<()> {
    // Load + validate input first so a missing file yields exit code 2 even on
    // machines without a GPU (CI runners).
    let image = crate::io::load_image(input).map_err(MaterializeError::from)?;
    let (width, height) = (image.width(), image.height());

    let pipeline = Pipeline::new()
        .await
        .map_err(|e: anyhow::Error| MaterializeError::Gpu(e.to_string()))?;

    if args.verbose {
        println!("GPU: {}", pipeline.adapter_info);
    }

    let (resolved_preset, base_params) = resolve_base_params(args, &image);
    let params = apply_overrides_and_auto_scale(args, &image, resolved_preset, base_params);

    if args.verbose {
        println!("Loaded: {} ({}x{})", input, width, height);
        println!("Preset: {}", resolved_preset);
        if args.preset == Preset::Auto {
            println!("{}", format_report(&classify(&analyze(&image))));
        }
    }
    if args.quality == 0 && args.verbose {
        println!("Warning: --quality 0 clamped to 1 for JPEG encoder");
    }

    let selection = build_selection(args)?;
    let mut options = resolve_process_options(args, &image, &params);
    let mut params = params;
    let intrinsic_maps = if args.intrinsic {
        try_intrinsic(input, &args.output, args.verbose)
    } else {
        None
    };
    if let Some(maps) = &intrinsic_maps {
        // Shape-from-shading entra no height por omissão quando a
        // decomposição está disponível (preset default = 0 = off).
        if params.shading_height_mix == 0.0 {
            params.shading_height_mix = 0.25;
        }
        options.intrinsic = Some(maps);
    }
    let (maps, timings) = pipeline
        .process_with(&image, &params, &selection, &options)
        .await
        .map_err(|e: anyhow::Error| MaterializeError::Gpu(e.to_string()))?;

    if args.verbose {
        print_timings(&timings);
    }

    let format_str = format!("{}", args.format);
    let paths =
        crate::io::get_output_paths(input, &args.output, &format_str, &selection, args.roughness);
    let image_format = crate::io::output_format_to_image_format(&args.format);

    save_maps(
        &maps,
        width,
        height,
        &paths,
        image_format,
        args.quality,
        args.roughness,
    )?;

    if !args.quiet {
        println!("Generated:");
        for p in [
            &paths.height_path,
            &paths.normal_path,
            &paths.metallic_path,
            &paths.smoothness_path,
            &paths.edge_path,
            &paths.ao_path,
            &paths.curvature_path,
        ]
        .into_iter()
        .flatten()
        {
            println!("  - {}", p);
        }
    }
    if args.verbose {
        println!("Total: {}ms", timings.total_ms);
    }

    Ok(())
}

async fn run_batch_mode(args: &Cli, pipeline: &Pipeline) -> Result<()> {
    let input = args.input.as_deref().unwrap_or(".");
    let inputs = expand_inputs(input)?;
    if inputs.is_empty() {
        println!("No supported images found in '{}'", input);
        return Ok(());
    }
    if args.verbose {
        println!("Found {} image(s) to process", inputs.len());
    }

    let result = run_batch(pipeline, inputs, args, &|img: &DynamicImage| {
        // Classify EACH image (a dummy 1×1 always resolved to Default, so
        // `-p auto` applied Default's params to every image in the batch —
        // unlike single-file mode).
        let (resolved_preset, base_params) = resolve_base_params(args, img);
        apply_overrides_and_auto_scale(args, img, resolved_preset, base_params)
    })?;

    println!(
        "Batch complete: {} processed, {} skipped, {} failed",
        result.processed,
        result.skipped,
        result.failed.len()
    );
    for (path, msg) in &result.failed {
        eprintln!("  FAILED {} — {}", path.display(), msg);
    }

    if !result.failed.is_empty() {
        return Err(MaterializeError::Other(anyhow::anyhow!(
            "{} image(s) failed during batch",
            result.failed.len()
        )));
    }
    Ok(())
}

/// Build the per-image process options: make-seamless tier (skipped when the
/// texture is already tileable — wrap sampling is enough) and intrinsic maps.
fn resolve_process_options<'a>(
    args: &Cli,
    image: &'a DynamicImage,
    params: &PresetParams,
) -> crate::pipeline::ProcessOptions<'a> {
    let mut options = crate::pipeline::ProcessOptions::default();
    if let Some(tier) = args.make_seamless {
        let features = analyze(image);
        if features.tile_mse < 0.005 {
            if args.verbose {
                println!(
                    "make-seamless: input already tileable (mse={:.4}); skipping roll/blend",
                    features.tile_mse
                );
            }
        } else {
            options.seamless_mode = tier.to_mode();
            let _ = params;
        }
    }
    options
}

fn resolve_base_params(args: &Cli, image: &DynamicImage) -> (Preset, PresetParams) {
    if args.preset == Preset::Auto {
        let features = analyze(image);
        let class = classify(&features);
        (class.preset, class.preset.params())
    } else {
        (args.preset, args.preset.params())
    }
}

fn apply_overrides_and_auto_scale(
    args: &Cli,
    image: &DynamicImage,
    resolved_preset: Preset,
    mut params: PresetParams,
) -> PresetParams {
    let ov = args.overrides();

    if let Some(v) = ov.height_contrast {
        params.height_contrast = v;
    }
    if let Some(v) = ov.height_sigma {
        params.height_sigma_base = v.max(0.1);
    }
    if let Some(v) = ov.height_blur {
        // Additive offset on the pyramid σ0 (kept for 2.0 flag compatibility).
        params.height_sigma_base = (params.height_sigma_base + v).max(0.1);
    }
    if let Some(v) = ov.guided_radius {
        params.guided_radius = v.max(0.0);
    }
    if let Some(v) = ov.detail_mix {
        params.detail_mix = v.clamp(0.0, 1.0);
    }
    if let Some(v) = ov.normal_strength {
        params.normal_strength = v;
    }
    if let Some(fmt) = ov.normal_format {
        params.normal_flip_y = fmt.to_flag();
    }
    if let Some(v) = ov.metallic_scale {
        params.metallic_scale = v;
    }
    if let Some(v) = ov.metallic_local_variance {
        params.metallic_local_variance_factor = v.clamp(0.0, 1.0);
    }
    if let Some(v) = ov.smoothness_base {
        params.smoothness_base = v;
    }
    if let Some(v) = ov.smoothness_boost {
        params.smoothness_metallic_boost = v;
    }
    if let Some(v) = ov.smoothness_roughness {
        params.smoothness_roughness_factor = v;
    }
    if let Some(v) = ov.edge_contrast {
        params.edge_contrast = v;
    }
    if let Some(v) = ov.ao_depth_scale {
        params.ao_depth_scale = v;
    }
    if let Some(q) = ov.ao_quality {
        let (dirs, steps) = q.dirs_and_steps();
        params.ao_directions = dirs as f32;
        params.ao_steps = steps as f32;
    }

    // Gray-world gains (F5): computed from the image on CPU so the shader
    // stays branch-free. Neutral when the feature is off.
    if params.metallic_gray_world == 1 {
        let (gr, gg, gb) = crate::analyze::gray_world_gains(image);
        params.gray_gain_r = gr;
        params.gray_gain_g = gg;
        params.gray_gain_b = gb;
    }

    // Auto-tile (F2.4): only override seamless if neither flag is set.
    if args.seamless {
        params.seamless = 1;
    } else if args.no_seamless {
        params.seamless = 0;
    } else if resolved_preset == Preset::Auto || args.preset == Preset::Auto {
        let features = analyze(image);
        if features.tile_mse < 0.005 {
            params.seamless = 1;
            if args.verbose {
                println!(
                    "Auto-tile: detected seamless texture (mse={:.4})",
                    features.tile_mse
                );
            }
        }
    }

    // Auto-scale (A2): tune contrast/strength by edge density when on auto.
    if args.preset == Preset::Auto {
        let features = analyze(image);
        params.height_contrast *= 0.7 + 0.6 * features.edge_density;
        params.normal_strength *= 1.2 - features.edge_density.min(1.0);
    }

    params
}

fn print_timings(t: &crate::pipeline::StageTimings) {
    println!(
        "Timings: seam={}ms height={}ms normal={}ms metallic={}ms smoothness={}ms edge={}ms ao={}ms curvature={}ms readback={}ms total={}ms",
        t.seam_ms,
        t.height_ms,
        t.normal_ms,
        t.metallic_ms,
        t.smoothness_ms,
        t.edge_ms,
        t.ao_ms,
        t.curvature_ms,
        t.readback_ms,
        t.total_ms
    );
}

fn save_maps(
    maps: &crate::pipeline::PbrMaps,
    width: u32,
    height: u32,
    paths: &crate::io::OutputPaths,
    image_format: image::ImageFormat,
    quality: u8,
    roughness: bool,
) -> Result<()> {
    if let Some(p) = &paths.height_path {
        let img = crate::io::height_to_image(width, height, &maps.height);
        crate::io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.normal_path {
        let img = crate::io::normal_to_image(width, height, &maps.normal);
        crate::io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.metallic_path {
        let img = crate::io::metallic_to_image(width, height, &maps.metallic);
        crate::io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.smoothness_path {
        let img = if roughness {
            crate::io::roughness_to_image(width, height, &maps.smoothness)
        } else {
            crate::io::smoothness_to_image(width, height, &maps.smoothness)
        };
        crate::io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.edge_path {
        let img = crate::io::edge_to_image(width, height, &maps.edge);
        crate::io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.ao_path {
        let img = crate::io::ao_to_image(width, height, &maps.ao);
        crate::io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.curvature_path {
        let img = crate::io::curvature_to_image(width, height, &maps.curvature);
        crate::io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    Ok(())
}

fn print_presets_table() {
    println!("Available presets:");
    for (name, desc) in PRESET_DESCRIPTIONS.iter() {
        println!("  {:<10} {}", name, desc);
    }
}

fn print_maps_table() {
    println!("Generated maps (suffixes appended to input stem):");
    for &name in MAP_NAMES {
        let desc = match name {
            "height" => "Height / displacement (grayscale, guided-filter base+detail)",
            "normal" => "Tangent-space normal (RGB; Sobel/Scharr)",
            "metallic" => "Metallic mask (grayscale; gray-world + specular)",
            "smoothness" => "Smoothness (grayscale); use --roughness to invert",
            "edge" => "Crease map from normal variation (grayscale)",
            "ao" => "Horizon-based AO, multi-scale (grayscale)",
            "curvature" => "Multi-scale convex/concave curvature (grayscale; --include-curvature)",
            _ => "",
        };
        println!("  {:<12} {}", name, desc);
    }
}

fn emit_completions(shell: crate::cli::ShellKind) {
    let mut cmd = Cli::command();
    let name = "materialize";
    let shell_enum: Shell = match shell {
        crate::cli::ShellKind::Bash => Shell::Bash,
        crate::cli::ShellKind::Zsh => Shell::Zsh,
        crate::cli::ShellKind::Fish => Shell::Fish,
        crate::cli::ShellKind::Elvish => Shell::Elvish,
        crate::cli::ShellKind::Powershell => Shell::PowerShell,
    };
    generate_completion(shell_enum, &mut cmd, name, &mut std::io::stdout());
}

#[cfg(test)]
mod tests {
    use image::{Rgba, RgbaImage};

    use super::*;

    #[test]
    fn test_contains_glob_metachar() {
        assert!(contains_glob_metachar("*.png"));
        assert!(contains_glob_metachar("a?b"));
        assert!(contains_glob_metachar("dir/[0-9].png"));
        assert!(contains_glob_metachar("textures/*.png"));
        assert!(!contains_glob_metachar("plain.png"));
        assert!(!contains_glob_metachar("dir/a.png"));
        assert!(!contains_glob_metachar("normal name.png"));
    }

    fn white_image() -> DynamicImage {
        DynamicImage::ImageRgba8(RgbaImage::from_pixel(64, 64, Rgba([255, 255, 255, 255])))
    }

    #[test]
    fn test_apply_overrides_override_each_field() {
        // Non-Auto preset so the auto-scale/auto-tile branches (which call analyze)
        // are skipped and the override values pass through unchanged.
        let args = Cli::parse_from([
            "materialize",
            "x.png",
            "--height-contrast",
            "9",
            "--normal-strength",
            "5",
            "--metallic-scale",
            "0",
            "--ao-depth-scale",
            "1",
            "--height-sigma",
            "2.5",
            "--detail-mix",
            "0.1",
            "--metallic-local-variance",
            "5",
        ]);
        let img = DynamicImage::ImageRgba8(RgbaImage::new(4, 4));
        let params =
            apply_overrides_and_auto_scale(&args, &img, Preset::Default, Preset::Default.params());

        // Scalar overrides replace the preset value verbatim.
        assert_eq!(params.height_contrast, 9.0);
        assert_eq!(params.normal_strength, 5.0);
        assert_eq!(params.metallic_scale, 0.0);
        assert_eq!(params.ao_depth_scale, 1.0);
        assert_eq!(params.height_sigma_base, 2.5);
        assert!((params.detail_mix - 0.1).abs() < 1e-6);

        // metallic_local_variance clamps to [0, 1]; 5 → 1.0.
        assert_eq!(params.metallic_local_variance_factor, 1.0);
    }

    #[test]
    fn test_apply_overrides_height_blur_offsets_sigma() {
        // --height-blur is ADDITIVE on σ0 (2.0 compat: it used to offset radii).
        let args = Cli::parse_from(["materialize", "x.png", "--height-blur", "1.5"]);
        let img = DynamicImage::ImageRgba8(RgbaImage::new(4, 4));
        let params =
            apply_overrides_and_auto_scale(&args, &img, Preset::Default, Preset::Default.params());
        assert!((params.height_sigma_base - 2.5).abs() < 1e-6);
    }

    #[test]
    fn test_apply_overrides_height_blur_clamped_positive() {
        let args = Cli::parse_from(["materialize", "x.png", "--height-blur=-99"]);
        let img = DynamicImage::ImageRgba8(RgbaImage::new(4, 4));
        let params =
            apply_overrides_and_auto_scale(&args, &img, Preset::Default, Preset::Default.params());
        assert!((params.height_sigma_base - 0.1).abs() < 1e-6);
    }

    #[test]
    fn test_apply_overrides_ao_quality_tiers() {
        for (tier, dirs, steps) in [
            ("fast", 8.0f32, 8.0f32),
            ("medium", 16.0, 12.0),
            ("high", 32.0, 24.0),
        ] {
            let flag = format!("--ao-quality={tier}");
            let args = Cli::parse_from(["materialize", "x.png", flag.as_str()]);
            let img = DynamicImage::ImageRgba8(RgbaImage::new(4, 4));
            let params = apply_overrides_and_auto_scale(
                &args,
                &img,
                Preset::Default,
                Preset::Default.params(),
            );
            assert_eq!(params.ao_directions, dirs);
            assert_eq!(params.ao_steps, steps);
        }
    }

    #[test]
    fn test_gray_world_gains_neutral_on_gray() {
        let (r, g, b) = crate::analyze::gray_world_gains(&white_image());
        assert!((r - 1.0).abs() < 0.02);
        assert!((g - 1.0).abs() < 0.02);
        assert!((b - 1.0).abs() < 0.02);
    }

    #[test]
    fn test_gray_world_gains_correct_tint() {
        // Uniform blue tint: the gains must pull R up and B down so the mean
        // matches luminance.
        let img =
            DynamicImage::ImageRgba8(RgbaImage::from_pixel(32, 32, Rgba([60, 120, 220, 255])));
        let (r, _g, b) = crate::analyze::gray_world_gains(&img);
        assert!(r > 1.0, "r={r}");
        assert!(b < 1.0, "b={b}");
    }

    #[test]
    fn test_resolve_base_params_non_auto() {
        // Non-Auto: params come straight from the preset (no analyze call).
        let args = Cli::parse_from(["materialize", "x.png", "-p", "stone"]);
        let img = DynamicImage::ImageRgba8(RgbaImage::new(4, 4));
        let (preset, params) = resolve_base_params(&args, &img);
        assert_eq!(preset, Preset::Stone);
        let stone = Preset::Stone.params();
        assert_eq!(params.height_contrast, stone.height_contrast);
        assert_eq!(params.normal_strength, stone.normal_strength);
        assert_eq!(params.ao_depth_scale, stone.ao_depth_scale);
    }

    #[test]
    fn test_resolve_base_params_auto_white_resolves_default() {
        // Mirrors analyze::tests::test_classify_white_is_default: a solid-white
        // image must resolve to Preset::Default even on the Auto path.
        let args = Cli::parse_from(["materialize", "x.png", "-p", "auto"]);
        let (preset, params) = resolve_base_params(&args, &white_image());
        assert_eq!(preset, Preset::Default);
        assert_eq!(
            params.height_contrast,
            Preset::Default.params().height_contrast
        );
    }
}
