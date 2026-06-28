mod analyze;
mod batch;
mod cli;
mod error;
mod gpu;
mod io;
mod pipeline;
mod preset;
mod skill_install;

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

#[tokio::main]
async fn main() -> ExitCode {
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

async fn run() -> Result<()> {
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
            if matches!(skill.subcommand, cli::SkillSubcommand::Install) {
                return skill_install::run().map_err(MaterializeError::from);
            }
        }
        Some(CliSubcommand::Info { input }) => {
            return run_info(input);
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
    let image = io::load_image(input).map_err(MaterializeError::from)?;
    let features = analyze(&image);
    let classification = classify(&features);
    println!("{}", format_report(&classification));
    Ok(())
}

async fn run_single_mode(args: &Cli, input: &str) -> Result<()> {
    // Load + validate input first so a missing file yields exit code 2 even on
    // machines without a GPU (CI runners).
    let image = io::load_image(input).map_err(MaterializeError::from)?;
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
    let (maps, timings) = pipeline
        .process(&image, &params, &selection)
        .await
        .map_err(|e: anyhow::Error| MaterializeError::Gpu(e.to_string()))?;

    if args.verbose {
        print_timings(&timings);
    }

    let format_str = format!("{}", args.format);
    let paths = io::get_output_paths(input, &args.output, &format_str, &selection, args.roughness);
    let image_format = io::output_format_to_image_format(&args.format);

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

    let (resolved_preset, base_params) =
        resolve_base_params(args, &DynamicImage::ImageRgba8(image::RgbaImage::new(1, 1)));

    let result = run_batch(pipeline, inputs, args, &|img: &DynamicImage| {
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
    if let Some(v) = ov.height_blur {
        params.height_blur_radius_0 = (params.height_blur_radius_0 + v).max(0.0);
        params.height_blur_radius_1 = (params.height_blur_radius_1 + v).max(0.0);
        params.height_blur_radius_2 = (params.height_blur_radius_2 + v).max(0.0);
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

fn print_timings(t: &pipeline::StageTimings) {
    println!(
        "Timings: height={}ms normal={}ms metallic={}ms smoothness={}ms edge={}ms ao={}ms curvature={}ms readback={}ms total={}ms",
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
    maps: &pipeline::PbrMaps,
    width: u32,
    height: u32,
    paths: &io::OutputPaths,
    image_format: image::ImageFormat,
    quality: u8,
    roughness: bool,
) -> Result<()> {
    if let Some(p) = &paths.height_path {
        let img = io::height_to_image(width, height, &maps.height);
        io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.normal_path {
        let img = io::normal_to_image(width, height, &maps.normal);
        io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.metallic_path {
        let img = io::metallic_to_image(width, height, &maps.metallic);
        io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.smoothness_path {
        let img = if roughness {
            io::roughness_to_image(width, height, &maps.smoothness)
        } else {
            io::smoothness_to_image(width, height, &maps.smoothness)
        };
        io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.edge_path {
        let img = io::edge_to_image(width, height, &maps.edge);
        io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.ao_path {
        let img = io::ao_to_image(width, height, &maps.ao);
        io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
    }
    if let Some(p) = &paths.curvature_path {
        let img = io::curvature_to_image(width, height, &maps.curvature);
        io::save_image(&img, p, image_format, quality).map_err(MaterializeError::from)?;
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
            "height" => "Height / displacement (grayscale)",
            "normal" => "Tangent-space normal (RGB)",
            "metallic" => "Metallic mask (grayscale)",
            "smoothness" => "Smoothness (grayscale); use --roughness to invert",
            "edge" => "Edge detection from normal gradient (grayscale)",
            "ao" => "Ambient occlusion, cavity-style (grayscale)",
            "curvature" => "Convex/concave curvature (grayscale; --include-curvature)",
            _ => "",
        };
        println!("  {:<12} {}", name, desc);
    }
}

fn emit_completions(shell: cli::ShellKind) {
    let mut cmd = cli::Cli::command();
    let name = "materialize";
    let shell_enum: Shell = match shell {
        cli::ShellKind::Bash => Shell::Bash,
        cli::ShellKind::Zsh => Shell::Zsh,
        cli::ShellKind::Fish => Shell::Fish,
        cli::ShellKind::Elvish => Shell::Elvish,
        cli::ShellKind::Powershell => Shell::PowerShell,
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
            "--height-blur",
            "1",
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

        // --height-blur is ADDITIVE on top of the preset radii (default 1/2/4).
        assert_eq!(params.height_blur_radius_0, 2.0);
        assert_eq!(params.height_blur_radius_1, 3.0);
        assert_eq!(params.height_blur_radius_2, 5.0);

        // metallic_local_variance clamps to [0, 1]; 5 → 1.0.
        assert_eq!(params.metallic_local_variance_factor, 1.0);
    }

    #[test]
    fn test_apply_overrides_height_blur_clamped_nonnegative() {
        // A large negative offset would push radii below zero; clamp keeps them >= 0.
        let args = Cli::parse_from(["materialize", "x.png", "--height-blur=-5"]);
        let img = DynamicImage::ImageRgba8(RgbaImage::new(4, 4));
        let params =
            apply_overrides_and_auto_scale(&args, &img, Preset::Default, Preset::Default.params());
        assert_eq!(params.height_blur_radius_0, 0.0);
        assert_eq!(params.height_blur_radius_1, 0.0);
        assert_eq!(params.height_blur_radius_2, 0.0);
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
