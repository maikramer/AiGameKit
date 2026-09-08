use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Preset {
    Default,
    Skin,
    Floor,
    Metal,
    Fabric,
    Wood,
    Stone,
    Concrete,
    Leather,
    Marble,
    Sand,
    Foliage,
    Plaster,
    Asphalt,
    Brick,
    Ice,
    Snow,
    Lava,
    Water,
    /// Special: resolve at runtime via `analyze` module.
    Auto,
}

impl fmt::Display for Preset {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            Preset::Default => "default",
            Preset::Skin => "skin",
            Preset::Floor => "floor",
            Preset::Metal => "metal",
            Preset::Fabric => "fabric",
            Preset::Wood => "wood",
            Preset::Stone => "stone",
            Preset::Concrete => "concrete",
            Preset::Leather => "leather",
            Preset::Marble => "marble",
            Preset::Sand => "sand",
            Preset::Foliage => "foliage",
            Preset::Plaster => "plaster",
            Preset::Asphalt => "asphalt",
            Preset::Brick => "brick",
            Preset::Ice => "ice",
            Preset::Snow => "snow",
            Preset::Lava => "lava",
            Preset::Water => "water",
            Preset::Auto => "auto",
        };
        f.write_str(s)
    }
}

impl std::str::FromStr for Preset {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "default" => Ok(Preset::Default),
            "skin" => Ok(Preset::Skin),
            "floor" => Ok(Preset::Floor),
            "metal" => Ok(Preset::Metal),
            "fabric" => Ok(Preset::Fabric),
            "wood" => Ok(Preset::Wood),
            "stone" => Ok(Preset::Stone),
            "concrete" => Ok(Preset::Concrete),
            "leather" => Ok(Preset::Leather),
            "marble" => Ok(Preset::Marble),
            "sand" => Ok(Preset::Sand),
            "foliage" => Ok(Preset::Foliage),
            "plaster" => Ok(Preset::Plaster),
            "asphalt" => Ok(Preset::Asphalt),
            "brick" => Ok(Preset::Brick),
            "ice" => Ok(Preset::Ice),
            "snow" => Ok(Preset::Snow),
            "lava" => Ok(Preset::Lava),
            "water" => Ok(Preset::Water),
            "auto" => Ok(Preset::Auto),
            _ => Err(format!(
                "Unknown preset '{}'. Available: default, skin, floor, metal, fabric, wood, stone, concrete, leather, marble, sand, foliage, plaster, asphalt, brick, ice, snow, lava, water, auto",
                s
            )),
        }
    }
}

/// GPU-aligned parameters passed to all shaders via uniform buffer.
/// Layout: 40 × 4 bytes = 160 bytes (multiple of 16, satisfies WGSL uniform
/// alignment).
///
/// Field order MUST be kept in sync with the `struct Params { ... }` in
/// `src/shaders/params.wgsl` — the single WGSL source of truth, concatenated
/// in front of every shader body at pipeline creation.
/// See `test_preset_params_size` for the layout-size invariant.
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable, PartialEq)]
pub struct PresetParams {
    // === Height (gaussian pyramid + guided filter, F1) ===
    /// σ0 of pyramid level 0 (σk = σ0·2^k via two σ0·√3 blur rounds).
    pub height_sigma_base: f32, // 1
    /// Pyramid levels, 1..7.
    pub height_pyramid_levels: f32, // 2
    pub height_contrast: f32, // 3

    // === Guided filter (He et al. 2010, F1) ===
    /// Box window radius (px) for mean/variance.
    pub guided_radius: f32, // 4
    /// Variance threshold ε — smaller ⇒ stronger edge preservation.
    pub guided_eps: f32, // 5
    /// Weight of the detail layer in the final height.
    pub detail_mix: f32, // 6

    // === Normal (F2) ===
    pub normal_strength: f32, // 7
    /// 0 = OpenGL (Y-up), 1 = DirectX (Y-down).
    pub normal_flip_y: u32, // 8
    /// 0 = Sobel, 1 = Scharr (÷4 normalised, same magnitude scale).
    pub normal_operator: u32, // 9
    /// z component of the normal — physical derivative scale (texels).
    pub normal_slope_z: f32, // 10
    /// σ of the pre-gradient gaussian; 0 = off.
    pub normal_prefilter_sigma: f32, // 11

    // === Metallic (F5) ===
    /// Scale applied to detection result; 0.0 forces non-metallic.
    pub metallic_scale: f32, // 12
    /// 0..1 damping strength for local luminance variance.
    pub metallic_local_variance_factor: f32, // 13
    /// Apply gray-world channel gains before detection (Buchsbaum 1980).
    pub metallic_gray_world: u32, // 14
    /// CPU-computed channel gains (luma_mean / channel_mean).
    pub gray_gain_r: f32, // 15
    pub gray_gain_g: f32, // 16
    pub gray_gain_b: f32, // 17
    /// Specular-evidence weight; 0 = disabled.
    pub metallic_specular_gain: f32, // 18

    // === Smoothness (F5) ===
    pub smoothness_base: f32,             // 19
    pub smoothness_metallic_boost: f32,   // 20
    pub smoothness_roughness_factor: f32, // 21
    /// Gain of the GGX slope-variance proxy (Walter et al. 2007).
    pub roughness_slope_scale: f32, // 22
    /// 0 disables the slope term (2.0 behaviour).
    pub roughness_slope_mix: f32, // 23

    // === Edge ===
    pub edge_contrast: f32, // 24

    // === AO (HBAO-style, F3) ===
    /// World-space height of one unit of heightfield value.
    pub ao_depth_scale: f32, // 25
    /// Ray-march directions (4..32).
    pub ao_directions: f32, // 26
    /// Geometric steps per direction (4..64).
    pub ao_steps: f32, // 27
    /// Maximum sample radius (px).
    pub ao_radius: f32, // 28
    /// Blend of macro AO (base layer at 4× radius).
    pub ao_macro_mix: f32, // 29

    // === Curvature (F2) ===
    /// LoG levels 1..3 (σ = 1, 2, 4).
    pub curvature_levels: f32, // 30
    pub curvature_gain: f32, // 31

    // === Intrinsic pre-pass (F6) ===
    /// High-frequency shading → height injection; 0 = off.
    pub shading_height_mix: f32, // 32

    // === Mode flags ===
    /// 0 = clamp sampling at borders, 1 = wrap (seamless).
    pub seamless: u32, // 33
    /// Make-seamless band width (px).
    pub seam_band: f32, // 34

    pub _pad0: f32, // 35
    pub _pad1: f32, // 36
    pub _pad2: f32, // 37
    pub _pad3: f32, // 38
    pub _pad4: f32, // 39
    pub _pad5: f32, // 40
}

/// Uniform for the generic separable filter passes (blur / scale / blend).
/// Mirrors `FilterParams` in `src/shaders/params.wgsl`.
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct FilterParams {
    pub radius: f32,
    pub sigma: f32,
    /// 0 = box, 1 = gaussian.
    pub kind: u32,
    pub seamless: u32,
    pub weight: f32,
    pub _p0: f32,
    pub _p1: f32,
    pub _p2: f32,
}

impl FilterParams {
    pub fn gaussian(sigma: f32, seamless: bool) -> Self {
        Self {
            radius: (sigma * 3.0).ceil().max(1.0),
            sigma: sigma.max(0.05),
            kind: 1,
            seamless: u32::from(seamless),
            weight: 0.0,
            _p0: 0.0,
            _p1: 0.0,
            _p2: 0.0,
        }
    }

    pub fn boxed(radius: f32, seamless: bool) -> Self {
        Self {
            radius: radius.max(0.0),
            sigma: 0.0,
            kind: 0,
            seamless: u32::from(seamless),
            weight: 0.0,
            _p0: 0.0,
            _p1: 0.0,
            _p2: 0.0,
        }
    }

    pub fn with_weight(mut self, weight: f32) -> Self {
        self.weight = weight;
        self
    }
}

impl PresetParams {
    /// Pyramid level weights for L levels: w_k = 2·(L−k)/(L·(L+1)) — the
    /// 0.5/0.3/0.2-style decaying blend, normalised (L=3 → 0.5/0.333/0.167,
    /// L=4 → 0.4/0.3/0.2/0.1).
    pub fn pyramid_weights(&self) -> Vec<f32> {
        let levels = self.height_pyramid_levels.round().clamp(1.0, 7.0) as usize;
        let l = levels as f32;
        (0..levels)
            .map(|k| 2.0 * (l - k as f32) / (l * (l + 1.0)))
            .collect()
    }
}

/// Neutral "common" parameter block; every preset overrides the fields it
/// cares about via struct-update syntax so new fields default in one place.
fn common() -> PresetParams {
    PresetParams {
        height_sigma_base: 1.0,
        height_pyramid_levels: 4.0,
        height_contrast: 1.5,
        guided_radius: 8.0,
        guided_eps: 0.010,
        detail_mix: 0.80,
        normal_strength: 2.0,
        normal_flip_y: 0,
        normal_operator: 0,
        normal_slope_z: 1.0,
        normal_prefilter_sigma: 0.5,
        metallic_scale: 1.0,
        metallic_local_variance_factor: 0.5,
        metallic_gray_world: 1,
        gray_gain_r: 1.0,
        gray_gain_g: 1.0,
        gray_gain_b: 1.0,
        metallic_specular_gain: 0.30,
        smoothness_base: 0.25,
        smoothness_metallic_boost: 0.65,
        smoothness_roughness_factor: 0.3,
        roughness_slope_scale: 1.5,
        roughness_slope_mix: 0.25,
        edge_contrast: 2.0,
        ao_depth_scale: 3.0,
        ao_directions: 16.0,
        ao_steps: 12.0,
        ao_radius: 16.0,
        ao_macro_mix: 0.30,
        curvature_levels: 2.0,
        curvature_gain: 8.0,
        shading_height_mix: 0.0,
        seamless: 0,
        seam_band: 24.0,
        _pad0: 0.0,
        _pad1: 0.0,
        _pad2: 0.0,
        _pad3: 0.0,
        _pad4: 0.0,
        _pad5: 0.0,
    }
}

impl Preset {
    pub fn params(self) -> PresetParams {
        match self {
            Preset::Default | Preset::Auto => common(),
            Preset::Skin => PresetParams {
                height_sigma_base: 1.6,
                guided_radius: 12.0,
                guided_eps: 0.005,
                detail_mix: 0.50,
                height_contrast: 0.8,
                normal_strength: 1.0,
                normal_slope_z: 1.2,
                normal_prefilter_sigma: 0.8,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                metallic_specular_gain: 0.10,
                smoothness_base: 0.45,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.15,
                roughness_slope_mix: 0.15,
                edge_contrast: 0.8,
                ao_depth_scale: 1.5,
                ao_directions: 12.0,
                ao_steps: 10.0,
                ao_radius: 14.0,
                ao_macro_mix: 0.25,
                ..common()
            },
            Preset::Floor => PresetParams {
                height_sigma_base: 0.8,
                guided_radius: 6.0,
                guided_eps: 0.020,
                detail_mix: 0.90,
                height_contrast: 2.0,
                normal_strength: 2.5,
                normal_slope_z: 0.9,
                normal_prefilter_sigma: 0.4,
                metallic_scale: 0.1,
                metallic_local_variance_factor: 0.3,
                metallic_specular_gain: 0.20,
                smoothness_base: 0.15,
                smoothness_metallic_boost: 0.4,
                smoothness_roughness_factor: 0.5,
                roughness_slope_mix: 0.30,
                edge_contrast: 2.5,
                ao_depth_scale: 4.0,
                ao_directions: 16.0,
                ao_steps: 16.0,
                ao_macro_mix: 0.40,
                ..common()
            },
            Preset::Metal => PresetParams {
                height_sigma_base: 1.0,
                height_pyramid_levels: 3.0,
                guided_radius: 8.0,
                guided_eps: 0.008,
                detail_mix: 0.70,
                height_contrast: 1.2,
                normal_strength: 1.5,
                normal_operator: 1,
                normal_slope_z: 0.8,
                metallic_scale: 1.5,
                metallic_local_variance_factor: 0.7,
                metallic_specular_gain: 0.50,
                smoothness_base: 0.5,
                smoothness_metallic_boost: 0.45,
                smoothness_roughness_factor: 0.2,
                roughness_slope_mix: 0.40,
                edge_contrast: 3.0,
                ao_depth_scale: 2.5,
                ao_macro_mix: 0.20,
                ..common()
            },
            Preset::Fabric => PresetParams {
                guided_radius: 6.0,
                guided_eps: 0.010,
                detail_mix: 0.90,
                height_contrast: 1.0,
                normal_strength: 1.8,
                normal_slope_z: 1.1,
                normal_prefilter_sigma: 0.6,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                metallic_specular_gain: 0.10,
                smoothness_base: 0.1,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.4,
                roughness_slope_mix: 0.35,
                edge_contrast: 1.0,
                ao_depth_scale: 2.0,
                ao_directions: 12.0,
                ao_steps: 10.0,
                ao_macro_mix: 0.35,
                ..common()
            },
            Preset::Wood => PresetParams {
                height_sigma_base: 1.2,
                guided_radius: 8.0,
                detail_mix: 0.85,
                height_contrast: 1.3,
                normal_strength: 1.8,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                metallic_specular_gain: 0.15,
                smoothness_base: 0.2,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.35,
                roughness_slope_mix: 0.30,
                edge_contrast: 1.5,
                ao_depth_scale: 2.5,
                ..common()
            },
            Preset::Stone => PresetParams {
                height_sigma_base: 0.7,
                height_pyramid_levels: 3.0,
                guided_radius: 5.0,
                guided_eps: 0.020,
                detail_mix: 0.95,
                height_contrast: 2.5,
                normal_strength: 3.0,
                normal_slope_z: 0.8,
                normal_prefilter_sigma: 0.7,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                metallic_specular_gain: 0.10,
                smoothness_base: 0.08,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.55,
                roughness_slope_mix: 0.30,
                edge_contrast: 2.0,
                ao_depth_scale: 5.0,
                ao_directions: 24.0,
                ao_steps: 16.0,
                ao_radius: 20.0,
                ao_macro_mix: 0.45,
                ..common()
            },
            Preset::Concrete => PresetParams {
                height_sigma_base: 0.8,
                guided_radius: 6.0,
                guided_eps: 0.020,
                detail_mix: 0.90,
                height_contrast: 1.8,
                normal_strength: 2.5,
                normal_slope_z: 0.9,
                normal_prefilter_sigma: 0.6,
                metallic_local_variance_factor: 0.5,
                metallic_specular_gain: 0.10,
                smoothness_base: 0.1,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.55,
                roughness_slope_mix: 0.30,
                edge_contrast: 2.0,
                ao_depth_scale: 4.0,
                ao_directions: 20.0,
                ao_steps: 14.0,
                ao_radius: 18.0,
                ao_macro_mix: 0.40,
                ..common()
            },
            Preset::Leather => PresetParams {
                guided_radius: 7.0,
                detail_mix: 0.85,
                height_contrast: 1.6,
                normal_strength: 2.0,
                normal_prefilter_sigma: 0.6,
                metallic_local_variance_factor: 0.2,
                metallic_specular_gain: 0.15,
                smoothness_base: 0.3,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.3,
                roughness_slope_mix: 0.30,
                edge_contrast: 1.8,
                ao_depth_scale: 3.0,
                ..common()
            },
            Preset::Marble => PresetParams {
                height_sigma_base: 0.9,
                height_pyramid_levels: 3.0,
                guided_radius: 8.0,
                guided_eps: 0.006,
                detail_mix: 0.75,
                height_contrast: 1.5,
                normal_strength: 2.0,
                normal_prefilter_sigma: 0.4,
                metallic_local_variance_factor: 0.3,
                metallic_specular_gain: 0.25,
                smoothness_base: 0.55,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.2,
                roughness_slope_mix: 0.20,
                edge_contrast: 1.8,
                ao_depth_scale: 2.5,
                ao_macro_mix: 0.25,
                ..common()
            },
            Preset::Sand => PresetParams {
                height_sigma_base: 0.7,
                height_pyramid_levels: 3.0,
                guided_radius: 5.0,
                guided_eps: 0.020,
                detail_mix: 0.95,
                height_contrast: 1.3,
                normal_strength: 2.8,
                normal_slope_z: 0.9,
                normal_prefilter_sigma: 0.7,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                metallic_specular_gain: 0.10,
                smoothness_base: 0.15,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.6,
                roughness_slope_mix: 0.30,
                edge_contrast: 1.6,
                ao_depth_scale: 3.5,
                ao_macro_mix: 0.35,
                ..common()
            },
            Preset::Foliage => PresetParams {
                guided_radius: 6.0,
                detail_mix: 0.90,
                height_contrast: 1.2,
                normal_strength: 2.5,
                normal_slope_z: 1.1,
                normal_prefilter_sigma: 0.6,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                metallic_specular_gain: 0.05,
                smoothness_base: 0.12,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.5,
                roughness_slope_mix: 0.30,
                edge_contrast: 1.5,
                ao_depth_scale: 3.0,
                ao_directions: 12.0,
                ao_steps: 10.0,
                ao_macro_mix: 0.35,
                ..common()
            },
            Preset::Plaster => PresetParams {
                height_sigma_base: 1.5,
                guided_radius: 12.0,
                guided_eps: 0.008,
                detail_mix: 0.60,
                height_contrast: 1.0,
                normal_strength: 1.2,
                normal_slope_z: 1.2,
                normal_prefilter_sigma: 0.7,
                metallic_local_variance_factor: 0.3,
                metallic_specular_gain: 0.15,
                smoothness_base: 0.35,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.25,
                roughness_slope_mix: 0.25,
                edge_contrast: 1.0,
                ao_depth_scale: 2.0,
                ao_directions: 12.0,
                ao_steps: 10.0,
                ao_macro_mix: 0.30,
                ..common()
            },
            Preset::Asphalt => PresetParams {
                height_sigma_base: 0.7,
                height_pyramid_levels: 3.0,
                guided_radius: 5.0,
                guided_eps: 0.020,
                detail_mix: 0.95,
                height_contrast: 2.0,
                normal_strength: 2.8,
                normal_slope_z: 0.8,
                normal_prefilter_sigma: 0.7,
                metallic_local_variance_factor: 0.4,
                metallic_specular_gain: 0.10,
                smoothness_base: 0.08,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.55,
                roughness_slope_mix: 0.30,
                edge_contrast: 2.2,
                ao_depth_scale: 4.5,
                ao_directions: 24.0,
                ao_steps: 16.0,
                ao_radius: 20.0,
                ao_macro_mix: 0.45,
                ..common()
            },
            Preset::Brick => PresetParams {
                height_sigma_base: 0.8,
                guided_radius: 6.0,
                guided_eps: 0.020,
                detail_mix: 0.90,
                height_contrast: 2.2,
                normal_strength: 2.5,
                normal_slope_z: 0.9,
                normal_prefilter_sigma: 0.5,
                metallic_local_variance_factor: 0.3,
                metallic_specular_gain: 0.15,
                smoothness_base: 0.18,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.4,
                roughness_slope_mix: 0.30,
                edge_contrast: 3.0,
                ao_depth_scale: 4.0,
                ao_directions: 20.0,
                ao_steps: 14.0,
                ao_radius: 18.0,
                ao_macro_mix: 0.40,
                ..common()
            },
            Preset::Ice => PresetParams {
                height_pyramid_levels: 3.0,
                guided_radius: 10.0,
                guided_eps: 0.004,
                detail_mix: 0.60,
                height_contrast: 1.4,
                normal_strength: 1.8,
                normal_slope_z: 1.1,
                normal_prefilter_sigma: 0.6,
                metallic_local_variance_factor: 0.5,
                metallic_specular_gain: 0.30,
                smoothness_base: 0.7,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.15,
                roughness_slope_mix: 0.10,
                edge_contrast: 1.5,
                ao_depth_scale: 2.0,
                ao_macro_mix: 0.20,
                ..common()
            },
            Preset::Snow => PresetParams {
                height_sigma_base: 1.5,
                guided_radius: 10.0,
                guided_eps: 0.005,
                detail_mix: 0.50,
                height_contrast: 0.8,
                normal_strength: 1.5,
                normal_slope_z: 1.2,
                normal_prefilter_sigma: 0.8,
                metallic_local_variance_factor: 0.2,
                metallic_specular_gain: 0.10,
                smoothness_base: 0.45,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.3,
                roughness_slope_mix: 0.20,
                edge_contrast: 1.0,
                ao_depth_scale: 2.5,
                ao_directions: 12.0,
                ao_steps: 10.0,
                ao_macro_mix: 0.30,
                ..common()
            },
            Preset::Lava => PresetParams {
                guided_radius: 7.0,
                detail_mix: 0.85,
                height_contrast: 2.0,
                normal_strength: 2.5,
                metallic_scale: 0.6,
                metallic_local_variance_factor: 0.4,
                metallic_specular_gain: 0.30,
                smoothness_base: 0.3,
                smoothness_metallic_boost: 0.3,
                smoothness_roughness_factor: 0.35,
                roughness_slope_mix: 0.30,
                edge_contrast: 2.2,
                ao_depth_scale: 3.5,
                ..common()
            },
            Preset::Water => PresetParams {
                height_sigma_base: 1.2,
                guided_radius: 10.0,
                guided_eps: 0.004,
                detail_mix: 0.60,
                height_contrast: 1.2,
                normal_strength: 1.8,
                normal_slope_z: 1.2,
                normal_prefilter_sigma: 0.6,
                metallic_local_variance_factor: 0.3,
                metallic_specular_gain: 0.25,
                smoothness_base: 0.7,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.15,
                roughness_slope_mix: 0.10,
                edge_contrast: 1.2,
                ao_depth_scale: 1.8,
                ao_macro_mix: 0.20,
                ..common()
            },
        }
    }

    /// All selectable presets except `Auto` (Auto has no fixed params and is resolved at runtime).
    #[allow(dead_code)]
    pub const ALL: &'static [Preset] = &[
        Preset::Default,
        Preset::Skin,
        Preset::Floor,
        Preset::Metal,
        Preset::Fabric,
        Preset::Wood,
        Preset::Stone,
        Preset::Concrete,
        Preset::Leather,
        Preset::Marble,
        Preset::Sand,
        Preset::Foliage,
        Preset::Plaster,
        Preset::Asphalt,
        Preset::Brick,
        Preset::Ice,
        Preset::Snow,
        Preset::Lava,
        Preset::Water,
    ];
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_preset_params_size() {
        assert_eq!(std::mem::size_of::<PresetParams>(), 160);
    }

    #[test]
    fn test_filter_params_size() {
        assert_eq!(std::mem::size_of::<FilterParams>(), 32);
    }

    #[test]
    fn test_preset_roundtrip_str() {
        for preset in Preset::ALL {
            let s = preset.to_string();
            let parsed: Preset = s.parse().unwrap();
            assert_eq!(s, parsed.to_string());
        }
    }

    #[test]
    fn test_preset_parse_case_insensitive() {
        let p: Preset = "SKIN".parse().unwrap();
        assert_eq!(p.to_string(), "skin");
    }

    #[test]
    fn test_preset_parse_unknown() {
        let result: Result<Preset, _> = "unknown".parse();
        assert!(result.is_err());
    }

    #[test]
    fn test_default_preset_matches_original_hardcoded() {
        let p = Preset::Default.params();
        assert_eq!(p.height_contrast, 1.5);
        assert_eq!(p.normal_strength, 2.0);
        assert_eq!(p.smoothness_base, 0.25);
        assert_eq!(p.smoothness_metallic_boost, 0.65);
        assert_eq!(p.edge_contrast, 2.0);
        assert_eq!(p.ao_depth_scale, 3.0);
    }

    #[test]
    fn test_skin_has_zero_metallic() {
        let p = Preset::Skin.params();
        assert_eq!(p.metallic_scale, 0.0);
    }

    fn assert_params_sane(p: PresetParams) {
        assert!(p.height_sigma_base > 0.0);
        assert!(p.height_pyramid_levels >= 1.0 && p.height_pyramid_levels <= 7.0);
        assert!(p.height_contrast > 0.0);
        assert!(p.guided_radius >= 0.0);
        assert!(p.guided_eps > 0.0);
        assert!((0.0..=1.0).contains(&p.detail_mix));
        assert!(p.normal_strength >= 0.0);
        assert!(p.normal_operator <= 1);
        assert!(p.normal_slope_z > 0.0);
        assert!(p.normal_prefilter_sigma >= 0.0);
        assert!(p.metallic_scale >= 0.0);
        assert!((0.0..=1.0).contains(&p.metallic_local_variance_factor));
        assert!(p.metallic_gray_world <= 1);
        assert!(p.gray_gain_r > 0.0 && p.gray_gain_g > 0.0 && p.gray_gain_b > 0.0);
        assert!(p.metallic_specular_gain >= 0.0);
        assert!((0.0..=1.0).contains(&p.smoothness_base));
        assert!(p.smoothness_metallic_boost >= 0.0);
        assert!(p.smoothness_roughness_factor >= 0.0);
        assert!(p.roughness_slope_scale >= 0.0);
        assert!((0.0..=1.0).contains(&p.roughness_slope_mix));
        assert!(p.edge_contrast >= 0.0);
        assert!(p.ao_depth_scale >= 0.0);
        assert!(p.ao_directions >= 4.0 && p.ao_directions <= 32.0);
        assert!(p.ao_steps >= 4.0 && p.ao_steps <= 64.0);
        assert!(p.ao_radius > 0.0);
        assert!((0.0..=1.0).contains(&p.ao_macro_mix));
        assert!(p.curvature_levels >= 1.0 && p.curvature_levels <= 3.0);
        assert!(p.curvature_gain > 0.0);
        assert!(p.shading_height_mix >= 0.0);
        assert!(p.normal_flip_y <= 1);
        assert!(p.seamless <= 1);
        assert!(p.seam_band >= 4.0);
        assert!(p.pyramid_weights().iter().sum::<f32>() > 0.99);
        assert!(p.pyramid_weights().iter().sum::<f32>() < 1.01);
    }

    #[test]
    fn test_all_preset_display_roundtrips() {
        for preset in Preset::ALL {
            let s = preset.to_string();
            let parsed: Preset = s.parse().unwrap();
            assert_eq!(parsed.to_string(), s);
        }
    }

    #[test]
    fn test_auto_display_and_parse() {
        assert_eq!(Preset::Auto.to_string(), "auto");
        let p: Preset = "AUTO".parse().unwrap();
        assert_eq!(p, Preset::Auto);
    }

    #[test]
    fn test_from_str_mixed_case_metal() {
        let p: Preset = "MeTaL".parse().unwrap();
        assert_eq!(p, Preset::Metal);
    }

    #[test]
    fn test_from_str_mixed_case_foliage() {
        let p: Preset = "FoLiAgE".parse().unwrap();
        assert_eq!(p, Preset::Foliage);
    }

    #[test]
    fn test_unknown_preset_lists_available() {
        let err: Result<Preset, _> = "diamond".parse();
        let err = err.unwrap_err();
        assert!(err.contains("Unknown preset"));
        assert!(err.contains("diamond"));
        assert!(err.contains("default"));
    }

    #[test]
    fn test_unknown_preset_empty_string() {
        assert!("".parse::<Preset>().is_err());
    }

    #[test]
    fn test_unknown_preset_whitespace_not_trimmed() {
        assert!(" skin".parse::<Preset>().is_err());
    }

    #[test]
    fn test_preset_params_size_is_160_bytes() {
        assert_eq!(std::mem::size_of::<PresetParams>(), 160);
        assert_eq!(std::mem::align_of::<PresetParams>(), 4);
    }

    #[test]
    fn test_pyramid_weights_normalised_and_decaying() {
        let p = Preset::Default.params();
        let w = p.pyramid_weights();
        assert_eq!(w.len(), 4);
        let sum: f32 = w.iter().sum();
        assert!((sum - 1.0).abs() < 1e-5);
        // Decaying: w0 > w1 > w2 > w3.
        assert!(w[0] > w[1] && w[1] > w[2] && w[2] > w[3]);
        // L=4 → 0.4 / 0.3 / 0.2 / 0.1.
        assert!((w[0] - 0.4).abs() < 1e-5);
        assert!((w[3] - 0.1).abs() < 1e-5);
    }

    #[test]
    fn test_filter_params_gaussian_radius_is_3sigma() {
        let f = FilterParams::gaussian(2.0, false);
        assert_eq!(f.radius, 6.0);
        assert_eq!(f.kind, 1);
        assert_eq!(f.seamless, 0);
    }

    #[test]
    fn test_filter_params_boxed() {
        let f = FilterParams::boxed(5.0, true).with_weight(0.25);
        assert_eq!(f.kind, 0);
        assert_eq!(f.seamless, 1);
        assert_eq!(f.weight, 0.25);
    }

    #[test]
    fn test_all_presets_params_sane() {
        for preset in Preset::ALL {
            assert_params_sane(preset.params());
        }
        assert_params_sane(Preset::Auto.params());
    }

    #[test]
    fn test_auto_params_match_default() {
        let a = Preset::Auto.params();
        let d = Preset::Default.params();
        assert_eq!(a, d);
    }

    #[test]
    fn test_each_selectable_preset_has_positive_height_sigma() {
        for preset in Preset::ALL {
            let p = preset.params();
            assert!(p.height_sigma_base > 0.0);
            assert!(p.height_contrast > 0.0);
        }
    }

    #[test]
    fn test_skin_smoothness_metallic_boost_zero() {
        let p = Preset::Skin.params();
        assert_eq!(p.smoothness_metallic_boost, 0.0);
    }

    #[test]
    fn test_metal_has_metallic_variance() {
        let p = Preset::Metal.params();
        assert!(p.metallic_local_variance_factor > 0.0);
    }

    #[test]
    fn test_metal_is_the_only_scharr_preset() {
        for preset in Preset::ALL {
            let is_scharr = preset.params().normal_operator == 1;
            assert_eq!(is_scharr, *preset == Preset::Metal);
        }
    }

    #[test]
    fn test_gray_gains_neutral_by_default() {
        for preset in Preset::ALL {
            let p = preset.params();
            assert_eq!(p.gray_gain_r, 1.0);
            assert_eq!(p.gray_gain_g, 1.0);
            assert_eq!(p.gray_gain_b, 1.0);
        }
    }

    #[test]
    fn test_shading_height_mix_off_by_default() {
        for preset in Preset::ALL {
            assert_eq!(preset.params().shading_height_mix, 0.0);
        }
    }

    #[test]
    fn test_display_strings_are_lowercase_snake() {
        for preset in Preset::ALL {
            let s = preset.to_string();
            assert_eq!(s, s.to_lowercase());
            assert!(!s.contains(' '));
        }
    }

    #[test]
    fn test_parse_each_all_member() {
        for preset in Preset::ALL {
            let parsed: Preset = preset.to_string().parse().unwrap();
            assert_eq!(parsed, *preset);
        }
    }

    #[test]
    fn test_pad_fields_zeroed_on_all_presets() {
        for preset in Preset::ALL {
            let p = preset.params();
            assert_eq!(p._pad0, 0.0);
            assert_eq!(p._pad1, 0.0);
            assert_eq!(p._pad2, 0.0);
            assert_eq!(p._pad3, 0.0);
            assert_eq!(p._pad4, 0.0);
            assert_eq!(p._pad5, 0.0);
        }
    }

    #[test]
    fn test_seamless_flag_default_off() {
        for preset in Preset::ALL {
            assert_eq!(preset.params().seamless, 0);
        }
    }
}
