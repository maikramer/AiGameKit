use anyhow::{Context, Result};
use image::{DynamicImage, ImageFormat};
use std::path::Path;

/// Paths for the six PBR map outputs.
#[derive(Debug, Clone)]
pub struct OutputPaths {
    pub height_path: String,
    pub normal_path: String,
    pub metallic_path: String,
    pub smoothness_path: String,
    pub edge_path: String,
    pub ao_path: String,
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
            // Quality 0 is invalid for JPEG; encoder expects 1-100
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

pub fn get_output_paths(input_path: &str, output_dir: &str, format: &str) -> OutputPaths {
    let input_name = Path::new(input_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");

    let ext = match format {
        "jpg" | "jpeg" => "jpg",
        _ => format,
    };

    OutputPaths {
        height_path: format!("{}/{}_height.{}", output_dir, input_name, ext),
        normal_path: format!("{}/{}_normal.{}", output_dir, input_name, ext),
        metallic_path: format!("{}/{}_metallic.{}", output_dir, input_name, ext),
        smoothness_path: format!("{}/{}_smoothness.{}", output_dir, input_name, ext),
        edge_path: format!("{}/{}_edge.{}", output_dir, input_name, ext),
        ao_path: format!("{}/{}_ao.{}", output_dir, input_name, ext),
    }
}

/// Convert height map (f32) to grayscale image
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

/// Convert normal map (RGBA8) to RGB image
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

/// Convert metallic map (R8) to grayscale image
pub fn metallic_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

/// Convert smoothness map (R8) to grayscale image
pub fn smoothness_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

/// Convert edge map (R8) to grayscale image
pub fn edge_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

/// Convert AO map (R8) to grayscale image
pub fn ao_to_image(width: u32, height: u32, data: &[u8]) -> DynamicImage {
    channel_r8_to_image(width, height, data)
}

/// Map OutputFormat to ImageFormat
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
        let p = get_output_paths("textures/brick.png", "./output", "png");
        assert_eq!(p.height_path, "./output/brick_height.png");
        assert_eq!(p.normal_path, "./output/brick_normal.png");
        assert_eq!(p.metallic_path, "./output/brick_metallic.png");
        assert_eq!(p.smoothness_path, "./output/brick_smoothness.png");
        assert_eq!(p.edge_path, "./output/brick_edge.png");
        assert_eq!(p.ao_path, "./output/brick_ao.png");
    }

    #[test]
    fn test_get_output_paths_jpg() {
        let p = get_output_paths("textures/brick.png", "./output", "jpg");
        assert_eq!(p.height_path, "./output/brick_height.jpg");
        assert_eq!(p.normal_path, "./output/brick_normal.jpg");
        assert_eq!(p.metallic_path, "./output/brick_metallic.jpg");
        assert_eq!(p.smoothness_path, "./output/brick_smoothness.jpg");
        assert_eq!(p.edge_path, "./output/brick_edge.jpg");
        assert_eq!(p.ao_path, "./output/brick_ao.jpg");
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
        let p = get_output_paths("folder/name", "./out", "png");
        assert!(p.height_path.contains("name_height.png"));
    }

    #[test]
    fn test_get_output_paths_jpeg_alias() {
        let p = get_output_paths("a.png", "./o", "jpeg");
        assert!(p.ao_path.ends_with(".jpg"));
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
        let p = get_output_paths("textures/sub/tile.png", "out", "tga");
        assert!(p.normal_path.contains("tile_normal.tga"));
        assert!(p.normal_path.starts_with("out/"));
    }

    #[test]
    fn test_get_output_paths_unknown_ext_defaults_to_format() {
        let p = get_output_paths("file.xyz", "o", "exr");
        assert!(p.height_path.ends_with("_height.exr"));
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
