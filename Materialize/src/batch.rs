//! Batch processing: directory/glob input. GPU work is serialised on the caller
//! thread (wgpu Device/Queue cannot be shared across threads for concurrent
//! dispatch); the per-image chain is already one encoder/submit.

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
    resolve_params: &(
         dyn Fn(&DynamicImage, Option<&crate::analyze::ImageFeatures>) -> PresetParams + Sync
     ),
) -> Result<BatchResult> {
    let selection = build_selection(cli)?;
    let format_str = format!("{}", cli.format);
    let image_format = io::output_format_to_image_format(&cli.format);
    let needs_features = cli.preset == crate::preset::Preset::Auto || cli.make_seamless.is_some();

    let mut processed = 0usize;
    let mut failed: Vec<(PathBuf, String)> = Vec::new();
    let mut skipped = 0usize;

    let total = inputs.len();

    enum LoadedItem {
        Skipped {
            idx: usize,
            path: PathBuf,
        },
        LoadError {
            idx: usize,
            path: PathBuf,
            err: String,
        },
        Ready {
            idx: usize,
            path: PathBuf,
            img: DynamicImage,
            params: PresetParams,
            seamless_mode: crate::pipeline::SeamlessMode,
        },
    }

    struct SaveItem {
        idx: usize,
        path: PathBuf,
        width: u32,
        height: u32,
        maps: crate::pipeline::PbrMaps,
        total_ms: u128,
    }

    // Pipeline CPU decode/analyze (producer thread) → GPU compute (caller
    // thread) → parallel CPU PNG/JPG encode (saver thread via Rayon) so the
    // GPU is never stalled waiting on disk I/O or image compression.
    let (load_tx, load_rx) = crossbeam_channel::bounded::<LoadedItem>(2);
    let (save_tx, save_rx) = crossbeam_channel::bounded::<SaveItem>(2);

    let save_errors: std::sync::Mutex<Vec<(PathBuf, String)>> = std::sync::Mutex::new(Vec::new());
    let save_ok = std::sync::atomic::AtomicUsize::new(0);

    std::thread::scope(|scope| {
        let sel_ref = &selection;
        let fmt_ref = &format_str;

        // Producer thread: load + RGBA8 convert + single analyze/resolve_params.
        scope.spawn(move || {
            for (idx, input_path) in inputs.into_iter().enumerate() {
                if cli.skip_existing && output_exists(&input_path, &cli.output, fmt_ref, sel_ref) {
                    if load_tx
                        .send(LoadedItem::Skipped {
                            idx,
                            path: input_path,
                        })
                        .is_err()
                    {
                        break;
                    }
                    continue;
                }

                let img = match image::open(&input_path) {
                    Ok(im) => DynamicImage::ImageRgba8(im.into_rgba8()),
                    Err(e) => {
                        if load_tx
                            .send(LoadedItem::LoadError {
                                idx,
                                path: input_path,
                                err: format!("load: {e}"),
                            })
                            .is_err()
                        {
                            break;
                        }
                        continue;
                    }
                };

                let features = needs_features.then(|| crate::analyze::analyze(&img));
                let params = resolve_params(&img, features.as_ref());
                let seamless_mode = match cli.make_seamless {
                    Some(tier) => {
                        let mse = features
                            .as_ref()
                            .map(|f| f.tile_mse)
                            .unwrap_or_else(|| crate::analyze::analyze(&img).tile_mse);
                        if mse >= 0.005 {
                            tier.to_mode()
                        } else {
                            crate::pipeline::SeamlessMode::Off
                        }
                    }
                    None => crate::pipeline::SeamlessMode::Off,
                };

                if load_tx
                    .send(LoadedItem::Ready {
                        idx,
                        path: input_path,
                        img,
                        params,
                        seamless_mode,
                    })
                    .is_err()
                {
                    break;
                }
            }
        });

        // Saver thread: encodes and writes each completed image's maps in parallel.
        let save_errors_ref = &save_errors;
        let save_ok_ref = &save_ok;
        scope.spawn(move || {
            for item in save_rx {
                match save_job(
                    &item.path,
                    &cli.output,
                    fmt_ref,
                    image_format,
                    cli.quality,
                    cli.roughness,
                    sel_ref,
                    item.width,
                    item.height,
                    &item.maps,
                ) {
                    Ok(()) => {
                        save_ok_ref.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        if cli.verbose || cli.progress {
                            println!(
                                "[{}/{}] {} ... done ({}ms)",
                                item.idx + 1,
                                total,
                                item.path.display(),
                                item.total_ms
                            );
                        }
                    }
                    Err(e) => {
                        if cli.verbose || cli.progress {
                            println!(
                                "[{}/{}] {} ... FAIL (save)",
                                item.idx + 1,
                                total,
                                item.path.display()
                            );
                        }
                        save_errors_ref
                            .lock()
                            .unwrap()
                            .push((item.path, format!("save: {e}")));
                    }
                }
            }
        });

        // Caller thread: owns GPU dispatches exclusively.
        for loaded in load_rx {
            match loaded {
                LoadedItem::Skipped { idx, path } => {
                    skipped += 1;
                    if cli.verbose || cli.progress {
                        println!("[{}/{}] skip {} (exists)", idx + 1, total, path.display());
                    }
                }
                LoadedItem::LoadError { idx, path, err } => {
                    if cli.verbose || cli.progress {
                        println!("[{}/{}] {} ... FAIL", idx + 1, total, path.display());
                    }
                    failed.push((path, err));
                }
                LoadedItem::Ready {
                    idx,
                    path,
                    img,
                    params,
                    seamless_mode,
                } => {
                    let (width, height) = (img.width(), img.height());
                    let options = crate::pipeline::ProcessOptions {
                        seamless_mode,
                        intrinsic: None,
                    };
                    match pipeline.process_blocking_with(&img, &params, sel_ref, &options) {
                        Ok((maps, timings)) => {
                            let _ = save_tx.send(SaveItem {
                                idx,
                                path,
                                width,
                                height,
                                maps,
                                total_ms: timings.total_ms,
                            });
                        }
                        Err(e) => {
                            failed.push((path.clone(), format!("process: {e}")));
                            if cli.verbose || cli.progress {
                                println!("[{}/{}] {} ... FAIL", idx + 1, total, path.display());
                            }
                        }
                    }
                }
            }
        }
        drop(save_tx);
    });

    processed += save_ok.into_inner();
    failed.extend(save_errors.into_inner().unwrap());

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

    io::save_maps_parallel(
        maps,
        width,
        height,
        &paths,
        image_format,
        quality,
        roughness_instead_of_smoothness,
    )
    .map_err(MaterializeError::from)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;

    use clap::Parser;

    use super::*;
    use crate::cli::Cli;
    use crate::io::MapSelection;

    #[test]
    fn test_is_supported_image_extensions() {
        for ext in ["png", "jpg", "jpeg", "tga", "exr"] {
            assert!(
                is_supported_image(&PathBuf::from(format!("x.{ext}"))),
                ".{ext} should be supported"
            );
        }
        // Extension matching is case-insensitive (paths from some scanners are upper-case).
        assert!(is_supported_image(&PathBuf::from("IMG.PNG")));
        assert!(is_supported_image(&PathBuf::from("Photo.JPG")));

        for bad in ["gif", "bmp", "txt", "tif"] {
            assert!(
                !is_supported_image(&PathBuf::from(format!("x.{bad}"))),
                ".{bad} should NOT be supported"
            );
        }
        assert!(!is_supported_image(&PathBuf::from("noext")));
        assert!(!is_supported_image(&PathBuf::from("README")));
    }

    #[test]
    fn test_expand_inputs_directory_sorted_and_filtered() {
        let dir = tempfile::tempdir().expect("tempdir");
        // Written in non-sorted order; c.txt must be filtered out.
        fs::write(dir.path().join("d.jpg"), b"x").expect("write d.jpg");
        fs::write(dir.path().join("a.png"), b"x").expect("write a.png");
        fs::write(dir.path().join("c.txt"), b"x").expect("write c.txt");
        fs::write(dir.path().join("b.exr"), b"x").expect("write b.exr");

        let dir_str = dir.path().to_str().expect("utf8 path");
        let got = expand_inputs(dir_str).expect("expand_inputs");
        let expected = vec![
            dir.path().join("a.png"),
            dir.path().join("b.exr"),
            dir.path().join("d.jpg"),
        ];
        assert_eq!(got, expected);
    }

    #[test]
    fn test_build_selection_only() {
        let cli = Cli::parse_from(["materialize", "x.png", "--only", "height,normal"]);
        let sel = build_selection(&cli).expect("build_selection");
        assert!(sel.height);
        assert!(sel.normal);
        assert!(!sel.metallic);
        assert!(!sel.smoothness);
        assert!(!sel.edge);
        assert!(!sel.ao);
        assert!(!sel.curvature);
    }

    #[test]
    fn test_build_selection_skip() {
        let cli = Cli::parse_from(["materialize", "x.png", "--skip", "height"]);
        let sel = build_selection(&cli).expect("build_selection");
        assert!(!sel.height);
        assert!(sel.normal);
        assert!(sel.metallic);
        assert!(sel.smoothness);
        assert!(sel.edge);
        assert!(sel.ao);
        assert!(!sel.curvature);
    }

    #[test]
    fn test_build_selection_include_curvature() {
        // Default selection is all(false) (six maps on, curvature off); the flag
        // then forces curvature on.
        let cli = Cli::parse_from(["materialize", "x.png", "--include-curvature"]);
        let sel = build_selection(&cli).expect("build_selection");
        assert!(sel.height);
        assert!(sel.normal);
        assert!(sel.metallic);
        assert!(sel.smoothness);
        assert!(sel.edge);
        assert!(sel.ao);
        assert!(sel.curvature);
    }

    #[test]
    fn test_output_exists_height_marker() {
        let dir = tempfile::tempdir().expect("tempdir");
        let dir_str = dir.path().to_str().expect("utf8 path");
        let sel = MapSelection::all(false);

        assert!(!output_exists(
            &PathBuf::from("foo.png"),
            dir_str,
            "png",
            &sel
        ));

        fs::write(dir.path().join("foo_height.png"), b"marker").expect("write marker");
        assert!(output_exists(
            &PathBuf::from("foo.png"),
            dir_str,
            "png",
            &sel
        ));

        // "jpeg" extension collapses to ".jpg" to mirror io::get_output_paths.
        fs::write(dir.path().join("bar_height.jpg"), b"marker").expect("write jpg marker");
        assert!(output_exists(
            &PathBuf::from("bar.jpeg"),
            dir_str,
            "jpeg",
            &sel
        ));

        // selection.height == false short-circuits to false even with a marker present.
        let sel_no_height = MapSelection::default();
        assert!(!output_exists(
            &PathBuf::from("foo.png"),
            dir_str,
            "png",
            &sel_no_height
        ));
    }
}
