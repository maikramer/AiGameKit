use clap::{Parser, Subcommand, ValueEnum};
use std::sync::LazyLock;

use crate::preset::Preset;

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum OutputFormat {
    Png,
    Jpg,
    Tga,
    Exr,
}

impl std::str::FromStr for OutputFormat {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "png" => Ok(OutputFormat::Png),
            "jpg" | "jpeg" => Ok(OutputFormat::Jpg),
            "tga" => Ok(OutputFormat::Tga),
            "exr" => Ok(OutputFormat::Exr),
            _ => Err(format!("Unsupported format: {}", s)),
        }
    }
}

impl std::fmt::Display for OutputFormat {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OutputFormat::Png => write!(f, "png"),
            OutputFormat::Jpg => write!(f, "jpg"),
            OutputFormat::Tga => write!(f, "tga"),
            OutputFormat::Exr => write!(f, "exr"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum NormalFormat {
    Opengl,
    Directx,
}

impl NormalFormat {
    pub fn to_flag(self) -> u32 {
        match self {
            NormalFormat::Opengl => 0,
            NormalFormat::Directx => 1,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct InlineOverrides {
    pub height_contrast: Option<f32>,
    pub height_blur: Option<f32>,
    pub normal_strength: Option<f32>,
    pub normal_format: Option<NormalFormat>,
    pub metallic_scale: Option<f32>,
    pub metallic_local_variance: Option<f32>,
    pub smoothness_base: Option<f32>,
    pub smoothness_boost: Option<f32>,
    pub smoothness_roughness: Option<f32>,
    pub edge_contrast: Option<f32>,
    pub ao_depth_scale: Option<f32>,
}

#[derive(Parser, Debug)]
#[command(name = "materialize", version = env!("CARGO_PKG_VERSION"))]
#[command(
    about = "Generate PBR maps (height, normal, metallic, smoothness/roughness, edge, AO, curvature) from diffuse textures"
)]
#[command(
    after_help = "EXAMPLES:\n  materialize texture.png -o ./out/\n  materialize skin.png -p skin -v\n  materialize ./textures/ -o ./pbr/ --jobs 4\n  materialize texture.png -p auto -v\n  materialize info texture.png   (analyse without generating)\n  materialize --list-presets\n\nPRESETS:\n  default skin floor metal fabric wood stone\n  concrete leather marble sand foliage plaster asphalt brick ice snow lava water\n  auto   (auto-detect from texture analysis)"
)]
pub struct Cli {
    #[command(subcommand)]
    pub subcommand: Option<CliSubcommand>,

    /// Input image or directory/glob (required unless using --list-* or a subcommand).
    pub input: Option<String>,

    #[arg(short, long, help = "Output directory", default_value = ".")]
    pub output: String,

    #[arg(
        short,
        long,
        help = "Output format (png, jpg, tga, exr)",
        default_value = "png"
    )]
    pub format: OutputFormat,

    #[arg(
        short = 'p',
        long,
        help = "Material preset (default, skin, floor, metal, fabric, wood, stone, concrete, leather, marble, sand, foliage, plaster, asphalt, brick, ice, snow, lava, water, auto)",
        default_value = "default"
    )]
    pub preset: Preset,

    #[arg(short, long, help = "JPEG quality 1-100 (ignored for other formats)", default_value = "95", value_parser = clap::value_parser!(u8).range(0..=100))]
    pub quality: u8,

    #[arg(short, long, help = "Verbose output (timing, adapter, detection)")]
    pub verbose: bool,

    #[arg(long, help = "Suppress 'Generated' file list on success")]
    pub quiet: bool,

    #[arg(long, help = "Generate shell completion script and exit", value_enum)]
    pub generate_completions: Option<ShellKind>,

    #[arg(long, help = "List available presets and exit")]
    pub list_presets: bool,

    #[arg(long, help = "List generated map names and exit")]
    pub list_maps: bool,

    #[arg(long, help = "Generate curvature map (7th output, opt-in)")]
    pub include_curvature: bool,

    #[arg(long, help = "Output roughness (1-smoothness) instead of smoothness")]
    pub roughness: bool,

    #[arg(
        long,
        help = "Normal map convention: opengl (Y-up, default) | directx (Y-down)",
        value_enum
    )]
    pub normal_format: Option<NormalFormat>,

    #[arg(long, help = "Force seamless (wrap) sampling at borders")]
    pub seamless: bool,

    #[arg(long, help = "Force non-seamless (clamp) sampling at borders")]
    pub no_seamless: bool,

    #[arg(
        long,
        help = "Only generate these maps (comma list)",
        conflicts_with = "skip"
    )]
    pub only: Option<String>,

    #[arg(long, help = "Skip these maps (comma list)", conflicts_with = "only")]
    pub skip: Option<String>,

    #[arg(
        long,
        help = "Parallel CPU jobs for batch processing",
        default_value = "1"
    )]
    pub jobs: u32,

    #[arg(long, help = "Skip images whose height output already exists (resume)")]
    pub skip_existing: bool,

    #[arg(long, help = "Show progress bar during batch processing")]
    pub progress: bool,

    // === Inline overrides (applied on top of preset) ===
    #[arg(long, help = "Override height_contrast")]
    pub height_contrast: Option<f32>,
    #[arg(long, help = "Override all 3 height blur radii (offset)")]
    pub height_blur: Option<f32>,
    #[arg(long, help = "Override normal_strength")]
    pub normal_strength: Option<f32>,
    #[arg(long, help = "Override metallic_scale")]
    pub metallic_scale: Option<f32>,
    #[arg(long, help = "Override metallic_local_variance damping (0..1)")]
    pub metallic_local_variance: Option<f32>,
    #[arg(long, help = "Override smoothness_base")]
    pub smoothness_base: Option<f32>,
    #[arg(long, help = "Override smoothness_metallic_boost")]
    pub smoothness_boost: Option<f32>,
    #[arg(long, help = "Override smoothness_roughness_factor")]
    pub smoothness_roughness: Option<f32>,
    #[arg(long, help = "Override edge_contrast")]
    pub edge_contrast: Option<f32>,
    #[arg(long, help = "Override ao_depth_scale")]
    pub ao_depth_scale: Option<f32>,
}

impl Cli {
    pub fn overrides(&self) -> InlineOverrides {
        InlineOverrides {
            height_contrast: self.height_contrast,
            height_blur: self.height_blur,
            normal_strength: self.normal_strength,
            normal_format: self.normal_format,
            metallic_scale: self.metallic_scale,
            metallic_local_variance: self.metallic_local_variance,
            smoothness_base: self.smoothness_base,
            smoothness_boost: self.smoothness_boost,
            smoothness_roughness: self.smoothness_roughness,
            edge_contrast: self.edge_contrast,
            ao_depth_scale: self.ao_depth_scale,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum ShellKind {
    Bash,
    Zsh,
    Fish,
    Elvish,
    Powershell,
}

#[derive(Subcommand, Debug)]
pub enum CliSubcommand {
    /// Manage the materialize-cli Cursor skill.
    Skill(SkillCommand),
    /// Analyse a texture without generating maps (auto-detect + features).
    Info {
        /// Input image path.
        input: String,
    },
}

#[derive(Parser, Debug)]
pub struct SkillCommand {
    #[command(subcommand)]
    pub subcommand: SkillSubcommand,
}

#[derive(Subcommand, Debug)]
pub enum SkillSubcommand {
    /// Install the materialize-cli skill into this project's .cursor/skills
    Install,
}

/// String table for --list-presets output (kept here so cli is the single source of truth).
pub static PRESET_DESCRIPTIONS: LazyLock<Vec<(&'static str, &'static str)>> = LazyLock::new(|| {
    vec![
        ("default", "General purpose (balanced)"),
        ("skin", "Human/character skin (no metallic, smooth)"),
        ("floor", "Ground surfaces (deep height, strong AO)"),
        ("metal", "Metallic surfaces (boosted, sharp edges)"),
        ("fabric", "Cloth/textile (matte, soft)"),
        ("wood", "Wood grain (moderate detail)"),
        ("stone", "Rock/stone (very rough, deep AO)"),
        ("concrete", "Concrete (rough, gray)"),
        ("leather", "Leather (pebbled, semi-smooth)"),
        ("marble", "Marble (polished, veined)"),
        ("sand", "Sand (fine grain, very rough)"),
        ("foliage", "Leaves/grass (organic, low metallic)"),
        ("plaster", "Plaster/stucco (flat, soft normals)"),
        ("asphalt", "Asphalt (dark, rough, dense edges)"),
        ("brick", "Brick (sharp edges, rough surface)"),
        ("ice", "Ice (very smooth, slight detail)"),
        ("snow", "Snow (soft, diffuse)"),
        ("lava", "Lava (molten, semi-metallic)"),
        ("water", "Water (very smooth, flowing)"),
        ("auto", "Auto-detect preset from texture analysis"),
    ]
});

pub static MAP_NAMES: &[&str] = &[
    "height",
    "normal",
    "metallic",
    "smoothness",
    "edge",
    "ao",
    "curvature",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_output_format_parse_roundtrip() {
        let png: OutputFormat = "png".parse().unwrap();
        assert_eq!(png, OutputFormat::Png);
        assert_eq!(png.to_string(), "png");

        let jpg: OutputFormat = "jpg".parse().unwrap();
        assert_eq!(jpg, OutputFormat::Jpg);
        assert_eq!(jpg.to_string(), "jpg");

        // "jpeg" is an accepted alias that normalises to the "jpg" token.
        let jpeg: OutputFormat = "jpeg".parse().unwrap();
        assert_eq!(jpeg, OutputFormat::Jpg);
        assert_eq!(jpeg.to_string(), "jpg");

        let tga: OutputFormat = "tga".parse().unwrap();
        assert_eq!(tga, OutputFormat::Tga);
        assert_eq!(tga.to_string(), "tga");

        let exr: OutputFormat = "exr".parse().unwrap();
        assert_eq!(exr, OutputFormat::Exr);
        assert_eq!(exr.to_string(), "exr");
    }

    #[test]
    fn test_output_format_case_insensitive() {
        assert_eq!("PNG".parse::<OutputFormat>().unwrap(), OutputFormat::Png);
        assert_eq!("JPG".parse::<OutputFormat>().unwrap(), OutputFormat::Jpg);
        assert_eq!("TGA".parse::<OutputFormat>().unwrap(), OutputFormat::Tga);
        assert_eq!("Exr".parse::<OutputFormat>().unwrap(), OutputFormat::Exr);
    }

    #[test]
    fn test_output_format_rejects_unknown() {
        for bad in ["gif", "webp", "bmp"] {
            let err = bad.parse::<OutputFormat>().unwrap_err();
            assert!(
                err.contains("Unsupported format"),
                "expected 'Unsupported format' in error for {bad:?}, got: {err}"
            );
            assert!(
                err.contains(bad),
                "error should echo the bad value {bad:?}: {err}"
            );
        }
        assert!("".parse::<OutputFormat>().is_err());
    }

    #[test]
    fn test_normal_format_to_flag() {
        // These integers are uploaded into a WGSL uniform (normal_flip_y) and are
        // part of the shader contract; both values are asserted exactly.
        assert_eq!(NormalFormat::Opengl.to_flag(), 0);
        assert_eq!(NormalFormat::Directx.to_flag(), 1);
    }
}
