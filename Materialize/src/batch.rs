//! Batch processing: directory/glob input. GPU work is serialised on the caller
//! thread (wgpu Device/Queue cannot be shared across threads for concurrent
//! dispatch, see plan v2 §F3.1). `--jobs N` is accepted for API stability but
//! GPU dispatch stays single-threaded; CPU load/analyse is the only parallelism.

use std::path::{Path, PathBuf};

use image::DynamicImage;

use crate::cli::Cli;
use crate::error::{MaterializeError, Result};
use crate::io::{self, MapSelection};
use crate::pipeline::Pipeline;
use crate::preset::PresetParams;

pub struct BatchResult {
    pub processed: usize,
    pub skipped: usize,
    pub failed: Vec<(PathBuf, String)>,
}

pub fn expand_inputs(input: &str) -> Result<Vec<PathBuf>> {
    let p = Path::new(input);
    if p.is_dir() {
        let mut out = Vec::new();
        for entry in std::fs::read_dir(p)
            .map_err(|e| MaterializeError::Io(format!("read_dir {}: {}", p.display(), e)))?
        {
            let entry = entry.map_err(|e| MaterializeError::Io(e.to_string()))?;
            let path = entry.path();
            if is_supported_image(&path) {
                out.push(path);
            }
        }
        out.sort();
        Ok(out)
    } else {
        let entries: Vec<PathBuf> = glob::glob(input)
            .map_err(|e| MaterializeError::Other(anyhow::anyhow!("glob error: {e}")))?
            .filter_map(|r| r.ok())
            .filter(|p| is_supported_image(p))
            .collect();
        Ok(entries)
    }
}

fn is_supported_image(p: &Path) -> bool {
    matches!(
        p.extension()
            .and_then(|e| e.to_str())
            .map(|s| s.to_lowercase())
            .as_deref(),
        Some("png") | Some("jpg") | Some("jpeg") | Some("tga") | Some("exr")
    )
}

pub fn build_selection(cli: &Cli) -> Result<MapSelection> {
    let mut sel = if let Some(only) = &cli.only {
        MapSelection::parse_only(only)
            .map_err(|e| MaterializeError::Other(anyhow::anyhow!("--only: {e}")))?
    } else if let Some(skip) = &cli.skip {
        MapSelection::parse_skip(skip)
            .map_err(|e| MaterializeError::Other(anyhow::anyhow!("--skip: {e}")))?
    } else {
        MapSelection::all(false)
    };
    if cli.include_curvature {
        sel.curvature = true;
    }
    Ok(sel)
}

fn output_exists(
    input: &Path,
    output_dir: &str,
    format_str: &str,
    selection: &MapSelection,
) -> bool {
    if !selection.height {
        return false;
    }
    let name = input
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");
    let ext = match format_str {
        "jpg" | "jpeg" => "jpg",
        _ => format_str,
    };
    Path::new(&format!("{output_dir}/{name}_height.{ext}")).exists()
}

pub fn run_batch(
    pipeline: &Pipeline,
    inputs: Vec<PathBuf>,
    cli: &Cli,
    resolve_params: &dyn Fn(&DynamicImage) -> PresetParams,
) -> Result<BatchResult> {
    let selection = build_selection(cli)?;
    let format_str = format!("{}", cli.format);
    let image_format = io::output_format_to_image_format(&cli.format);

    let mut processed = 0usize;
    let mut failed: Vec<(PathBuf, String)> = Vec::new();
    let mut skipped = 0usize;

    let total = inputs.len();
    for (idx, input_path) in inputs.into_iter().enumerate() {
        if cli.skip_existing && output_exists(&input_path, &cli.output, &format_str, &selection) {
            skipped += 1;
            if cli.verbose || cli.progress {
                println!(
                    "[{}/{}] skip {} (exists)",
                    idx + 1,
                    total,
                    input_path.display()
                );
            }
            continue;
        }

        if cli.verbose || cli.progress {
            print!("[{}/{}] {} ... ", idx + 1, total, input_path.display());
            use std::io::Write;
            let _ = std::io::stdout().flush();
        }

        let img = match image::open(&input_path) {
            Ok(im) => im,
            Err(e) => {
                if cli.verbose || cli.progress {
                    println!("FAIL");
                }
                failed.push((input_path, format!("load: {e}")));
                continue;
            }
        };

        let params = resolve_params(&img);
        let (width, height) = (img.width(), img.height());

        let result = pipeline.process_blocking(&img, &params, &selection);
        match result {
            Ok((maps, timings)) => {
                if let Err(e) = save_job(
                    &input_path,
                    &cli.output,
                    &format_str,
                    image_format,
                    cli.quality,
                    cli.roughness,
                    &selection,
                    width,
                    height,
                    &maps,
                ) {
                    failed.push((input_path, format!("save: {e}")));
                    if cli.verbose || cli.progress {
                        println!("FAIL (save)");
                    }
                    continue;
                }
                processed += 1;
                if cli.verbose || cli.progress {
                    println!("done ({}ms)", timings.total_ms);
                }
            }
            Err(e) => {
                failed.push((input_path, format!("process: {e}")));
                if cli.verbose || cli.progress {
                    println!("FAIL");
                }
            }
        }
    }

    Ok(BatchResult {
        processed,
        skipped,
        failed,
    })
}

#[allow(clippy::too_many_arguments)]
fn save_job(
    input_path: &Path,
    output_dir: &str,
    format_str: &str,
    image_format: image::ImageFormat,
    quality: u8,
    roughness_instead_of_smoothness: bool,
    selection: &MapSelection,
    width: u32,
    height: u32,
    maps: &crate::pipeline::PbrMaps,
) -> Result<()> {
    let paths = io::get_output_paths(
        input_path.to_str().unwrap_or("output"),
        output_dir,
        format_str,
        selection,
        roughness_instead_of_smoothness,
    );

    if let Some(p) = &paths.height_path {
        let img = io::height_to_image(width, height, &maps.height);
        io::save_image(&img, p, image_format, quality)?;
    }
    if let Some(p) = &paths.normal_path {
        let img = io::normal_to_image(width, height, &maps.normal);
        io::save_image(&img, p, image_format, quality)?;
    }
    if let Some(p) = &paths.metallic_path {
        let img = io::metallic_to_image(width, height, &maps.metallic);
        io::save_image(&img, p, image_format, quality)?;
    }
    if let Some(p) = &paths.smoothness_path {
        let img = if roughness_instead_of_smoothness {
            io::roughness_to_image(width, height, &maps.smoothness)
        } else {
            io::smoothness_to_image(width, height, &maps.smoothness)
        };
        io::save_image(&img, p, image_format, quality)?;
    }
    if let Some(p) = &paths.edge_path {
        let img = io::edge_to_image(width, height, &maps.edge);
        io::save_image(&img, p, image_format, quality)?;
    }
    if let Some(p) = &paths.ao_path {
        let img = io::ao_to_image(width, height, &maps.ao);
        io::save_image(&img, p, image_format, quality)?;
    }
    if let Some(p) = &paths.curvature_path {
        let img = io::curvature_to_image(width, height, &maps.curvature);
        io::save_image(&img, p, image_format, quality)?;
    }
    Ok(())
}
