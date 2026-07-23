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
/// Layout: 16 × 4 bytes = 64 bytes (multiple of 16, satisfies WGSL uniform alignment).
///
/// Field order MUST be kept in sync with the `struct Params { ... }` declaration
/// duplicated at the top of every WGSL shader under `src/shaders/*.wgsl`.
/// See `test_preset_params_size` for the layout-size invariant.
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct PresetParams {
    // === Height shader ===
    pub height_blur_radius_0: f32, // 1
    pub height_blur_radius_1: f32, // 2
    pub height_blur_radius_2: f32, // 3
    pub height_contrast: f32,      // 4

    // === Normal shader ===
    pub normal_strength: f32, // 5
    /// 0 = OpenGL (Y-up), 1 = DirectX (Y-down).
    pub normal_flip_y: u32, // 6  (NEW 2.0)

    // === Metallic shader ===
    /// Scale applied to detection result; 0.0 forces non-metallic.
    pub metallic_scale: f32, // 7
    /// 0..1 damping strength for local luminance variance (F2.5). 0 = disabled.
    pub metallic_local_variance_factor: f32, // 8  (NEW 2.0)

    // === Smoothness shader ===
    pub smoothness_base: f32,           // 9
    pub smoothness_metallic_boost: f32, // 10
    /// Spatial roughness contribution from local contrast (F4.1). 0 = disabled.
    pub smoothness_roughness_factor: f32, // 11 (NEW 2.0)

    // === Edge shader ===
    pub edge_contrast: f32, // 12

    // === AO shader ===
    pub ao_depth_scale: f32, // 13

    // === Mode flags (shared) ===
    /// 0 = clamp sampling at borders, 1 = wrap (seamless). F2.4.
    pub seamless: u32, // 14 (NEW 2.0)

    // Padding to reach 64 bytes (16 × 4). WGSL uniform alignment requires multiple of 16.
    pub _pad0: f32, // 15
    pub _pad1: f32, // 16
}

impl Preset {
    pub fn params(self) -> PresetParams {
        match self {
            Preset::Default => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 1.5,
                normal_strength: 2.0,
                normal_flip_y: 0,
                metallic_scale: 1.0,
                metallic_local_variance_factor: 0.5,
                smoothness_base: 0.25,
                smoothness_metallic_boost: 0.65,
                smoothness_roughness_factor: 0.3,
                edge_contrast: 2.0,
                ao_depth_scale: 3.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Skin => PresetParams {
                height_blur_radius_0: 2.0,
                height_blur_radius_1: 4.0,
                height_blur_radius_2: 8.0,
                height_contrast: 0.8,
                normal_strength: 1.0,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                smoothness_base: 0.45,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.15,
                edge_contrast: 0.8,
                ao_depth_scale: 1.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Floor => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 3.0,
                height_contrast: 2.0,
                normal_strength: 2.5,
                normal_flip_y: 0,
                metallic_scale: 0.1,
                metallic_local_variance_factor: 0.3,
                smoothness_base: 0.15,
                smoothness_metallic_boost: 0.4,
                smoothness_roughness_factor: 0.5,
                edge_contrast: 2.5,
                ao_depth_scale: 4.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Metal => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 1.2,
                normal_strength: 1.5,
                normal_flip_y: 0,
                metallic_scale: 1.5,
                metallic_local_variance_factor: 0.7,
                smoothness_base: 0.5,
                smoothness_metallic_boost: 0.45,
                smoothness_roughness_factor: 0.2,
                edge_contrast: 3.0,
                ao_depth_scale: 2.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Fabric => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 1.0,
                normal_strength: 1.8,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                smoothness_base: 0.1,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.4,
                edge_contrast: 1.0,
                ao_depth_scale: 2.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Wood => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 3.0,
                height_blur_radius_2: 6.0,
                height_contrast: 1.3,
                normal_strength: 1.8,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                smoothness_base: 0.2,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.35,
                edge_contrast: 1.5,
                ao_depth_scale: 2.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Stone => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 1.0,
                height_blur_radius_2: 2.0,
                height_contrast: 2.5,
                normal_strength: 3.0,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                smoothness_base: 0.08,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.55,
                edge_contrast: 2.0,
                ao_depth_scale: 5.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Concrete => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 3.0,
                height_contrast: 1.8,
                normal_strength: 2.5,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.5,
                smoothness_base: 0.1,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.55,
                edge_contrast: 2.0,
                ao_depth_scale: 4.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Leather => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 1.6,
                normal_strength: 2.0,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.2,
                smoothness_base: 0.3,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.3,
                edge_contrast: 1.8,
                ao_depth_scale: 3.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Marble => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 1.0,
                height_blur_radius_2: 2.0,
                height_contrast: 1.5,
                normal_strength: 2.0,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.3,
                smoothness_base: 0.55,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.2,
                edge_contrast: 1.8,
                ao_depth_scale: 2.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Sand => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 1.0,
                height_blur_radius_2: 2.0,
                height_contrast: 1.3,
                normal_strength: 2.8,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                smoothness_base: 0.15,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.6,
                edge_contrast: 1.6,
                ao_depth_scale: 3.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Foliage => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 1.2,
                normal_strength: 2.5,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.0,
                smoothness_base: 0.12,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.5,
                edge_contrast: 1.5,
                ao_depth_scale: 3.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Plaster => PresetParams {
                height_blur_radius_0: 2.0,
                height_blur_radius_1: 4.0,
                height_blur_radius_2: 8.0,
                height_contrast: 1.0,
                normal_strength: 1.2,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.3,
                smoothness_base: 0.35,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.25,
                edge_contrast: 1.0,
                ao_depth_scale: 2.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Asphalt => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 1.0,
                height_blur_radius_2: 2.0,
                height_contrast: 2.0,
                normal_strength: 2.8,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.4,
                smoothness_base: 0.08,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.55,
                edge_contrast: 2.2,
                ao_depth_scale: 4.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Brick => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 3.0,
                height_contrast: 2.2,
                normal_strength: 2.5,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.3,
                smoothness_base: 0.18,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.4,
                edge_contrast: 3.0,
                ao_depth_scale: 4.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Ice => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 1.4,
                normal_strength: 1.8,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.5,
                smoothness_base: 0.7,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.15,
                edge_contrast: 1.5,
                ao_depth_scale: 2.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Snow => PresetParams {
                height_blur_radius_0: 2.0,
                height_blur_radius_1: 4.0,
                height_blur_radius_2: 8.0,
                height_contrast: 0.8,
                normal_strength: 1.5,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.2,
                smoothness_base: 0.45,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.3,
                edge_contrast: 1.0,
                ao_depth_scale: 2.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Lava => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 2.0,
                normal_strength: 2.5,
                normal_flip_y: 0,
                metallic_scale: 0.6,
                metallic_local_variance_factor: 0.4,
                smoothness_base: 0.3,
                smoothness_metallic_boost: 0.3,
                smoothness_roughness_factor: 0.35,
                edge_contrast: 2.2,
                ao_depth_scale: 3.5,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Water => PresetParams {
                height_blur_radius_0: 2.0,
                height_blur_radius_1: 4.0,
                height_blur_radius_2: 8.0,
                height_contrast: 1.2,
                normal_strength: 1.8,
                normal_flip_y: 0,
                metallic_scale: 0.0,
                metallic_local_variance_factor: 0.3,
                smoothness_base: 0.7,
                smoothness_metallic_boost: 0.0,
                smoothness_roughness_factor: 0.15,
                edge_contrast: 1.2,
                ao_depth_scale: 1.8,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
            },
            Preset::Auto => PresetParams {
                height_blur_radius_0: 1.0,
                height_blur_radius_1: 2.0,
                height_blur_radius_2: 4.0,
                height_contrast: 1.5,
                normal_strength: 2.0,
                normal_flip_y: 0,
                metallic_scale: 1.0,
                metallic_local_variance_factor: 0.5,
                smoothness_base: 0.25,
                smoothness_metallic_boost: 0.65,
                smoothness_roughness_factor: 0.3,
                edge_contrast: 2.0,
                ao_depth_scale: 3.0,
                seamless: 0,
                _pad0: 0.0,
                _pad1: 0.0,
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
        assert_eq!(std::mem::size_of::<PresetParams>(), 64);
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
        assert!(p.metallic_scale >= 0.0);
        assert!(p.height_blur_radius_0 >= 0.0);
        assert!(p.height_blur_radius_1 >= p.height_blur_radius_0);
        assert!(p.normal_strength >= 0.0);
        assert!(p.smoothness_base >= 0.0 && p.smoothness_base <= 1.0);
        assert!(p.edge_contrast >= 0.0);
        assert!(p.ao_depth_scale >= 0.0);
        assert!(p.normal_flip_y <= 1);
        assert!(p.seamless <= 1);
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
    fn test_preset_params_size_is_64_bytes() {
        assert_eq!(std::mem::size_of::<PresetParams>(), 64);
        assert_eq!(std::mem::align_of::<PresetParams>(), 4);
    }

    #[test]
    fn test_default_params_sane() {
        assert_params_sane(Preset::Default.params());
    }

    #[test]
    fn test_skin_params_sane() {
        assert_params_sane(Preset::Skin.params());
    }

    #[test]
    fn test_floor_params_sane() {
        assert_params_sane(Preset::Floor.params());
    }

    #[test]
    fn test_metal_params_sane() {
        let p = Preset::Metal.params();
        assert_params_sane(p);
        assert!(p.metallic_scale >= 1.0);
    }

    #[test]
    fn test_fabric_params_sane() {
        assert_params_sane(Preset::Fabric.params());
    }

    #[test]
    fn test_wood_params_sane() {
        assert_params_sane(Preset::Wood.params());
    }

    #[test]
    fn test_stone_params_sane() {
        assert_params_sane(Preset::Stone.params());
    }

    #[test]
    fn test_concrete_params_sane() {
        assert_params_sane(Preset::Concrete.params());
    }

    #[test]
    fn test_leather_params_sane() {
        assert_params_sane(Preset::Leather.params());
    }

    #[test]
    fn test_marble_params_sane() {
        assert_params_sane(Preset::Marble.params());
    }

    #[test]
    fn test_sand_params_sane() {
        assert_params_sane(Preset::Sand.params());
    }

    #[test]
    fn test_foliage_params_sane() {
        assert_params_sane(Preset::Foliage.params());
    }

    #[test]
    fn test_plaster_params_sane() {
        assert_params_sane(Preset::Plaster.params());
    }

    #[test]
    fn test_asphalt_params_sane() {
        assert_params_sane(Preset::Asphalt.params());
    }

    #[test]
    fn test_brick_params_sane() {
        assert_params_sane(Preset::Brick.params());
    }

    #[test]
    fn test_ice_params_sane() {
        assert_params_sane(Preset::Ice.params());
    }

    #[test]
    fn test_snow_params_sane() {
        assert_params_sane(Preset::Snow.params());
    }

    #[test]
    fn test_lava_params_sane() {
        assert_params_sane(Preset::Lava.params());
    }

    #[test]
    fn test_water_params_sane() {
        assert_params_sane(Preset::Water.params());
    }

    #[test]
    fn test_auto_params_match_default() {
        let a = Preset::Auto.params();
        let d = Preset::Default.params();
        assert_eq!(a.height_contrast, d.height_contrast);
        assert_eq!(a.metallic_scale, d.metallic_scale);
    }

    #[test]
    fn test_each_selectable_preset_has_positive_height_blur() {
        for preset in Preset::ALL {
            let p = preset.params();
            assert!(p.height_blur_radius_0 > 0.0);
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
        }
    }

    #[test]
    fn test_seamless_flag_default_off() {
        for preset in Preset::ALL {
            assert_eq!(preset.params().seamless, 0);
        }
    }
}
