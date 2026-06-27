use anyhow::{Context, Result};
use image::{DynamicImage, ImageFormat};
use std::path::Path;

/// Flags selecting which PBR maps to generate. `curvature` is opt-in.
#[derive(Debug, Clone, Default)]
pub struct MapSelection {
    pub height: bool,
    pub normal: bool,
    pub metallic: bool,
    pub smoothness: bool,
    pub edge: bool,
    pub ao: bool,
    pub curvature: bool,
}

impl MapSelection {
    pub fn all(include_curvature: bool) -> Self {
        Self {
            height: true,
            normal: true,
            metallic: true,
            smoothness: true,
            edge: true,
            ao: true,
            curvature: include_curvature,
        }
    }

    pub fn parse_only(spec: &str) -> std::result::Result<Self, String> {
        let mut sel = Self::default();
        for token in spec.split(',').map(str::trim) {
            match token.to_lowercase().as_str() {
                "height" => sel.height = true,
                "normal" => sel.normal = true,
                "metallic" => sel.metallic = true,
                "smoothness" => sel.smoothness = true,
                "edge" => sel.edge = true,
                "ao" => sel.ao = true,
                "curvature" => sel.curvature = true,
                "" => continue,
                other => return Err(format!("unknown map '{other}'")),
            }
        }
        Ok(sel)
    }

    pub fn parse_skip(spec: &str) -> std::result::Result<Self, String> {
        let mut sel = Self::all(false);
        for token in spec.split(',').map(str::trim) {
            match token.to_lowercase().as_str() {
                "height" => sel.height = false,
                "normal" => sel.normal = false,
                "metallic" => sel.metallic = false,
                "smoothness" => sel.smoothness = false,
                "edge" => sel.edge = false,
                "ao" => sel.ao = false,
                "curvature" => sel.curvature = false,
                "" => continue,
                other => return Err(format!("unknown map '{other}'")),
            }
        }
        Ok(sel)
    }

    #[allow(dead_code)]
    pub fn count(&self) -> usize {
        [
            self.height,
            self.normal,
            self.metallic,
            self.smoothness,
            self.edge,
            self.ao,
            self.curvature,
        ]
        .iter()
        .filter(|&&b| b)
        .count()
    }
}

#[derive(Debug, Clone, Default)]
pub struct OutputPaths {
    pub height_path: Option<String>,
    pub normal_path: Option<String>,
    pub metallic_path: Option<String>,
    pub smoothness_path: Option<String>,
    pub edge_path: Option<String>,
    pub ao_path: Option<String>,
    pub curvature_path: Option<String>,
}

pub fn load_image(path: &str) -> Result<DynamicImage> {
    let path = Path::new(path);

    if !path.exists() {
        anyhow::bail!("Input file '{}' not found", path.display());
    }

    let img =
        image::open(path).with_context(|| format!("Failed to load image: {}", path.display()))?;

    Ok(img)
}

pub fn save_image(
    image: &DynamicImage,
    path: &str,
    format: ImageFormat,
    quality: u8,
) -> Result<()> {
    let path = Path::new(path);

    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("Failed to create directory: {}", parent.display()))?;
    }

    match format {
        ImageFormat::Jpeg => {
            let rgb = image.to_rgb8();
            let file = std::fs::File::create(path)
                .with_context(|| format!("Failed to create file: {}", path.display()))?;
            let mut writer = std::io::BufWriter::new(file);
            let q = quality.clamp(1, 100);
            let mut encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut writer, q);
            encoder
                .encode_image(&rgb)
                .with_context(|| format!("Failed to save JPEG: {}", path.display()))?;
        }
        _ => {
            image
                .save_with_format(path, format)
                .with_context(|| format!("Failed to save image: {}", path.display()))?;
        }
    }

    Ok(())
}

pub fn get_output_paths(
    input_path: &str,
    output_dir: &str,
    format: &str,
    selection: &MapSelection,
    roughness_instead_of_smoothness: bool,
) -> OutputPaths {
    let input_name = Path::new(input_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");

    let ext = match format {
        "jpg" | "jpeg" => "jpg",
        _ => format,
    };

    let smooth_suffix = if roughness_instead_of_smoothness {
        "roughness"
    } else {
        "smoothness"
    };

    let mut out = OutputPaths::default();
    if selection.height {
        out.height_path = Some(format!("{}/{}_height.{}", output_dir, input_name, ext));
    }
    if selection.normal {
        out.normal_path = Some(format!("{}/{}_normal.{}", output_dir, input_name, ext));
    }
    if selection.metallic {
        out.metallic_path = Some(format!("{}/{}_metallic.{}", output_dir, input_name, ext));
    }
    if selection.smoothness {
        out.smoothness_path = Some(format!(
            "{}/{}_{}.{}",
            output_dir, input_name, smooth_suffix, ext
        ));
    }
    if selection.edge {
        out.edge_path = Some(format!("{}/{}_edge.{}", output_dir, input_name, ext));
    }
    if selection.ao {
        out.ao_path = Some(format!("{}/{}_ao.{}", output_dir, input_name, ext));
    }
    if selection.curvature {
        out.curvature_path = Some(format!("{}/{}_curvature.{}", output_dir, input_name, ext));
    }
    out
}

pub fn height_to_image(width: u32, height: u32, data: &[f32]) -> DynamicImage {
    use image::{ImageBuffer, Luma};

    let mut img = ImageBuffer::new(width, height);

    for (x, y, pixel) in img.enumerate_pixels_mut() {
        let idx = (y * width + x) as usize;
        let value = (data[idx] * 255.0).clamp(0.0, 255.0) as u8;
        *pixel = Luma([value]);
    }

    DynamicImage::ImageLuma8(img)
}

pub fn normal_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    use image::{ImageBuffer, Rgb};

    let mut img = ImageBuffer::new(width, height);

    for (x, y, pixel) in img.enumerate_pixels_mut() {
        let idx = ((y * width + x) * 4) as usize;
        let r = data[idx];
        let g = data[idx + 1];
        let b = data[idx + 2];
        *pixel = Rgb([r, g, b]);
    }

    DynamicImage::ImageRgb8(img)
}

fn channel_r8_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    let img = image::GrayImage::from_raw(width, height, data.to_vec())
        .expect("Invalid image dimensions for channel data");
    DynamicImage::ImageLuma8(img)
}

pub fn metallic_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

pub fn smoothness_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

pub fn roughness_to_image(width: u32, height: u32, smoothness: &[u8]) -> DynamicImage {
    let inverted: Vec<u8> = smoothness
        .iter()
        .map(|&v| 255u8.saturating_sub(v))
        .collect();
    channel_r8_to_image(width, height, &inverted)
}

pub fn edge_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

pub fn ao_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

pub fn curvature_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

pub fn output_format_to_image_format(format: &super::cli::OutputFormat) -> ImageFormat {
    match format {
        super::cli::OutputFormat::Png => ImageFormat::Png,
        super::cli::OutputFormat::Jpg => ImageFormat::Jpeg,
        super::cli::OutputFormat::Tga => ImageFormat::Tga,
        super::cli::OutputFormat::Exr => ImageFormat::OpenExr,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_load_image_not_found() {
        let result = load_image("nonexistent.png");
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("not found"));
    }

    #[test]
    fn test_get_output_paths() {
        let sel = MapSelection::all(false);
        let p = get_output_paths("textures/brick.png", "./output", "png", &sel, false);
        assert_eq!(p.height_path.as_deref(), Some("./output/brick_height.png"));
        assert_eq!(p.normal_path.as_deref(), Some("./output/brick_normal.png"));
        assert_eq!(
            p.metallic_path.as_deref(),
            Some("./output/brick_metallic.png")
        );
        assert_eq!(
            p.smoothness_path.as_deref(),
            Some("./output/brick_smoothness.png")
        );
        assert_eq!(p.edge_path.as_deref(), Some("./output/brick_edge.png"));
        assert_eq!(p.ao_path.as_deref(), Some("./output/brick_ao.png"));
        assert!(p.curvature_path.is_none());
    }

    #[test]
    fn test_get_output_paths_jpg() {
        let sel = MapSelection::all(false);
        let p = get_output_paths("textures/brick.png", "./output", "jpg", &sel, false);
        assert_eq!(p.height_path.as_deref(), Some("./output/brick_height.jpg"));
        assert_eq!(p.ao_path.as_deref(), Some("./output/brick_ao.jpg"));
    }

    #[test]
    fn test_get_output_paths_roughness_replaces_smoothness_suffix() {
        let sel = MapSelection::all(false);
        let p = get_output_paths("textures/brick.png", "./output", "png", &sel, true);
        assert_eq!(
            p.smoothness_path.as_deref(),
            Some("./output/brick_roughness.png")
        );
    }

    #[test]
    fn test_get_output_paths_curvature_only_when_selected() {
        let sel_off = MapSelection::all(false);
        let p = get_output_paths("a.png", "o", "png", &sel_off, false);
        assert!(p.curvature_path.is_none());

        let mut sel_on = MapSelection::all(true);
        sel_on.curvature = true;
        let p2 = get_output_paths("a.png", "o", "png", &sel_on, false);
        assert_eq!(p2.curvature_path.as_deref(), Some("o/a_curvature.png"));
    }

    #[test]
    fn test_height_to_image() {
        let data = vec![0.0f32, 0.5, 1.0, 0.25];
        let img = height_to_image(2, 2, &data);
        assert_eq!(img.width(), 2);
        assert_eq!(img.height(), 2);

        // Check pixel values (scaled to 0-255, truncation)
        let luma = img.to_luma8();
        assert_eq!(luma.get_pixel(0, 0)[0], 0);
        assert_eq!(luma.get_pixel(1, 0)[0], 127); // 0.5 * 255 truncated
        assert_eq!(luma.get_pixel(0, 1)[0], 255);
    }

    #[test]
    fn test_normal_to_image_rgba() {
        let data: Vec<u8> = (0..16).collect();
        let img = normal_to_image(2, 2, &data);
        assert_eq!(img.width(), 2);
        assert_eq!(img.height(), 2);
    }

    #[test]
    fn test_metallic_smoothness_edge_ao_images() {
        let w = 2u32;
        let h = 2u32;
        let d = vec![10u8, 20, 30, 40];
        let m = metallic_to_image(w, h, &d);
        let s = smoothness_to_image(w, h, &d);
        let e = edge_to_image(w, h, &d);
        let a = ao_to_image(w, h, &d);
        assert_eq!(m.width(), w);
        assert_eq!(s.height(), h);
        assert_eq!(e.to_luma8().get_pixel(0, 0)[0], 10);
        assert_eq!(a.to_luma8().get_pixel(1, 1)[0], 40);
    }

    #[test]
    fn test_output_format_to_image_format_maps() {
        use crate::cli::OutputFormat;
        use image::ImageFormat;
        assert_eq!(
            output_format_to_image_format(&OutputFormat::Png),
            ImageFormat::Png
        );
        assert_eq!(
            output_format_to_image_format(&OutputFormat::Jpg),
            ImageFormat::Jpeg
        );
        assert_eq!(
            output_format_to_image_format(&OutputFormat::Tga),
            ImageFormat::Tga
        );
        assert_eq!(
            output_format_to_image_format(&OutputFormat::Exr),
            ImageFormat::OpenExr
        );
    }

    #[test]
    fn test_get_output_paths_stem_no_extension() {
        let sel = MapSelection::all(false);
        let p = get_output_paths("folder/name", "./out", "png", &sel, false);
        assert!(p.height_path.unwrap().contains("name_height.png"));
    }

    #[test]
    fn test_get_output_paths_jpeg_alias() {
        let sel = MapSelection::all(false);
        let p = get_output_paths("a.png", "./o", "jpeg", &sel, false);
        assert!(p.ao_path.unwrap().ends_with(".jpg"));
    }

    #[test]
    fn test_load_image_reads_png() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("tiny.png");
        let img = image::RgbImage::from_pixel(1, 1, image::Rgb([128u8, 64, 32]));
        img.save(&path).expect("save png");
        let loaded = load_image(path.to_str().unwrap()).expect("load");
        assert_eq!(loaded.width(), 1);
    }

    #[test]
    fn test_save_image_png_roundtrip() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("out.png");
        let img = image::RgbImage::from_pixel(2, 2, image::Rgb([1, 2, 3]));
        let dyn_img = image::DynamicImage::ImageRgb8(img);
        save_image(
            &dyn_img,
            path.to_str().unwrap(),
            image::ImageFormat::Png,
            95,
        )
        .expect("save");
        assert!(path.exists());
    }

    #[test]
    fn test_height_to_image_clamps_high() {
        let data = vec![2.0f32, 2.0, 2.0, 2.0];
        let img = height_to_image(2, 2, &data);
        let luma = img.to_luma8();
        assert_eq!(luma.get_pixel(0, 0)[0], 255);
    }

    #[test]
    fn test_get_output_paths_preserves_nested_dir() {
        let sel = MapSelection::all(false);
        let p = get_output_paths("textures/sub/tile.png", "out", "tga", &sel, false);
        let np = p.normal_path.unwrap();
        assert!(np.contains("tile_normal.tga"));
        assert!(np.starts_with("out/"));
    }

    #[test]
    fn test_get_output_paths_unknown_ext_defaults_to_format() {
        let sel = MapSelection::all(false);
        let p = get_output_paths("file.xyz", "o", "exr", &sel, false);
        assert!(p.height_path.unwrap().ends_with("_height.exr"));
    }

    #[test]
    fn test_normal_to_image_reads_first_three_channels() {
        let mut data = vec![0u8; 16];
        data[0] = 10;
        data[1] = 20;
        data[2] = 30;
        data[4] = 255;
        data[5] = 128;
        data[6] = 64;
        let img = normal_to_image(2, 2, &data);
        let rgb = img.to_rgb8();
        assert_eq!(rgb.get_pixel(0, 0)[0], 10);
        assert_eq!(rgb.get_pixel(1, 0)[0], 255);
    }

    #[test]
    fn test_load_image_rejects_missing_even_with_parent() {
        let r = load_image("definitely/missing/path.png");
        assert!(r.is_err());
    }

    #[test]
    fn test_save_image_jpeg_creates_file() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("q.jpg");
        let img = image::RgbImage::from_pixel(1, 1, image::Rgb([200u8, 100, 50]));
        let dyn_img = image::DynamicImage::ImageRgb8(img);
        save_image(
            &dyn_img,
            path.to_str().unwrap(),
            image::ImageFormat::Jpeg,
            90,
        )
        .expect("jpeg");
        assert!(path.exists());
    }

    #[test]
    fn test_metallic_single_pixel() {
        let img = metallic_to_image(1, 1, &[200u8]);
        assert_eq!(img.to_luma8().get_pixel(0, 0)[0], 200);
    }
}
