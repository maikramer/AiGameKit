//! Geração dos YAML que o GameAssets lê (`game.yaml` + `manifest.yaml`).
//!
//! Os ficheiros são determinísticos a partir de [`StudioConfig`]: a app nunca
//! faz round-trip de YAML escrito à mão.

use serde::Serialize;
use serde_yaml::{Mapping, Value};

use crate::config::{PipelineStage, StudioConfig};

pub const OUTPUT_DIR: &str = ".";
pub const PATH_LAYOUT: &str = "split";
pub const IMAGES_SUBDIR: &str = "images";
pub const MESHES_SUBDIR: &str = "meshes";
pub const IMAGE_EXT: &str = "png";

fn s(value: impl Into<String>) -> Value {
    Value::String(value.into())
}

fn f(value: f32) -> Value {
    Value::Number(serde_yaml::Number::from(value as f64))
}

fn u(value: u64) -> Value {
    Value::Number(serde_yaml::Number::from(value))
}

fn i(value: i64) -> Value {
    Value::Number(serde_yaml::Number::from(value))
}

fn b(value: bool) -> Value {
    Value::Bool(value)
}

/// Mapping compacto, ignorando valores `None`.
fn mapping<const N: usize>(pairs: [(&str, Option<Value>); N]) -> Value {
    let mut map = Mapping::new();
    for (key, value) in pairs {
        if let Some(value) = value {
            map.insert(s(key), value);
        }
    }
    Value::Mapping(map)
}

#[derive(Debug, Serialize)]
struct ProfileDoc {
    title: String,
    genre: String,
    tone: String,
    style_preset: String,
    output_dir: String,
    path_layout: String,
    images_subdir: String,
    meshes_subdir: String,
    image_ext: String,
    seed_base: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    generation: Option<String>,
    text3d: Value,
    refine: Value,
    animator3d: Value,
    lod: Value,
    paint3d: Value,
    collision: Value,
}

#[derive(Debug, Serialize)]
struct ManifestDoc {
    output_dir: String,
    path_layout: String,
    images_subdir: String,
    meshes_subdir: String,
    assets: Vec<Value>,
}

fn text3d_profile(config: &StudioConfig) -> Value {
    let preset = config.text3d.preset.clone().unwrap_or_else(|| "hq".into());
    let mc_level = match config.text3d.mc_level {
        Some(level) => i(level as i64),
        None => s("auto"),
    };
    mapping([
        ("preset", Some(s(preset))),
        ("export_origin", Some(s("feet"))),
        ("bounds_mode", Some(s("auto"))),
        ("mc_level", Some(mc_level)),
        (
            "split_at_height",
            Some(b(config.text3d.split_at_height.unwrap_or(true))),
        ),
        ("sdnq_preset", Some(s("sdnq-int4"))),
        ("memory_efficient", Some(b(true))),
        (
            "octree_resolution",
            config.text3d.octree_resolution.map(|v| u(v as u64)),
        ),
    ])
}

fn refine_profile(config: &StudioConfig) -> Value {
    mapping([
        ("enabled", Some(b(config.refine.enabled))),
        ("resolution", Some(s("auto"))),
        ("steps", Some(u(25))),
        ("max_faces_scale", Some(f(4.0))),
        ("views", Some(u(config.refine.views as u64))),
    ])
}

fn animator_profile(config: &StudioConfig) -> Value {
    mapping([
        (
            "preset",
            Some(s(config
                .animate
                .preset
                .clone()
                .unwrap_or_else(|| "humanoid".into()))),
        ),
        (
            "anim_pack",
            Some(s(config
                .animate
                .anim_pack
                .clone()
                .unwrap_or_else(|| "all".into()))),
        ),
    ])
}

fn lod_profile(config: &StudioConfig) -> Value {
    mapping([
        (
            "rig_max_level",
            Some(u(config.lod.rig_max_level.unwrap_or(2) as u64)),
        ),
        ("patch_pbr", Some(b(false))),
    ])
}

fn paint_profile(config: &StudioConfig) -> Value {
    mapping([
        (
            "preserve_origin",
            Some(b(config.paint.preserve_origin.unwrap_or(true))),
        ),
        (
            "texture_size",
            Some(u(config.paint.texture_size.unwrap_or(2048) as u64)),
        ),
        (
            "pbr_enrich",
            Some(b(config.paint.pbr_enrich.unwrap_or(false))),
        ),
    ])
}

fn collision_profile(config: &StudioConfig) -> Value {
    mapping([
        (
            "mode",
            Some(s(config
                .collision
                .mode
                .clone()
                .unwrap_or_else(|| "hull".into()))),
        ),
        ("max_faces", config.collision.max_faces.map(|v| u(v as u64))),
        ("voxel_size", config.collision.voxel_size.map(f)),
        ("inflate", config.collision.inflate.map(f)),
    ])
}

pub fn build_game_yaml(config: &StudioConfig) -> Result<String, serde_yaml::Error> {
    let doc = ProfileDoc {
        title: config.asset.id.clone(),
        genre: "fantasy".into(),
        tone: "stylized game asset, readable silhouette".into(),
        style_preset: config.asset.style_preset.clone(),
        output_dir: OUTPUT_DIR.into(),
        path_layout: PATH_LAYOUT.into(),
        images_subdir: IMAGES_SUBDIR.into(),
        meshes_subdir: MESHES_SUBDIR.into(),
        image_ext: IMAGE_EXT.into(),
        seed_base: 7777,
        generation: config.asset.quality.clone(),
        text3d: text3d_profile(config),
        refine: refine_profile(config),
        animator3d: animator_profile(config),
        lod: lod_profile(config),
        paint3d: paint_profile(config),
        collision: collision_profile(config),
    };
    let header = "# Gerado pelo AiGameKit Studio a partir de studio.toml.\n";
    Ok(format!("{header}{}", serde_yaml::to_string(&doc)?))
}

fn omni_row(config: &StudioConfig) -> Option<Value> {
    let omni = &config.omni;
    let size = omni.size_m.map(|size| {
        Value::Sequence(size.iter().copied().map(f).collect())
    });
    let value = mapping([
        ("control_type", omni.control_type.clone().map(s)),
        ("bbox_preset", omni.bbox_preset.clone().map(s)),
        ("size_m", size),
        ("pose_preset", omni.pose_preset.clone().map(s)),
        ("pose_file", omni.pose_file.clone().map(s)),
    ]);
    match value {
        Value::Mapping(map) if map.is_empty() => None,
        other => Some(other),
    }
}

fn text3d_row(config: &StudioConfig) -> Option<Value> {
    let text3d = &config.text3d;
    let value = mapping([
        ("preset", text3d.preset.clone().map(s)),
        (
            "octree_resolution",
            text3d.octree_resolution.map(|v| u(v as u64)),
        ),
        ("mc_level", text3d.mc_level.map(|v| i(v as i64))),
        ("morph_close", text3d.morph_close.map(f)),
        ("split_at_height", text3d.split_at_height.map(b)),
    ]);
    match value {
        Value::Mapping(map) if map.is_empty() => None,
        other => Some(other),
    }
}

fn refine_row(config: &StudioConfig) -> Value {
    if !config.refine.enabled {
        return b(false);
    }
    mapping([
        ("enabled", Some(b(true))),
        ("views", Some(u(config.refine.views as u64))),
    ])
}

fn paint_row(config: &StudioConfig) -> Option<Value> {
    let paint = &config.paint;
    let value = mapping([
        ("texture_size", paint.texture_size.map(|v| u(v as u64))),
        ("quality", paint.quality.clone().map(s)),
        ("pbr_enrich", paint.pbr_enrich.map(b)),
        ("preserve_origin", paint.preserve_origin.map(b)),
    ]);
    match value {
        Value::Mapping(map) if map.is_empty() => None,
        other => Some(other),
    }
}

fn animate_row(config: &StudioConfig) -> Option<Value> {
    let animate = &config.animate;
    let value = mapping([
        ("preset", animate.preset.clone().map(s)),
        ("clips", animate.clips.clone().map(s)),
        ("anim_pack", animate.anim_pack.clone().map(s)),
        ("procedural", animate.procedural.map(b)),
        ("force_preset", animate.force_preset.map(b)),
    ]);
    match value {
        Value::Mapping(map) if map.is_empty() => None,
        other => Some(other),
    }
}

fn collision_row(config: &StudioConfig) -> Option<Value> {
    let collision = &config.collision;
    let value = mapping([
        ("mode", collision.mode.clone().map(s)),
        ("max_faces", collision.max_faces.map(|v| u(v as u64))),
        ("voxel_size", collision.voxel_size.map(f)),
        ("inflate", collision.inflate.map(f)),
    ]);
    match value {
        Value::Mapping(map) if map.is_empty() => None,
        other => Some(other),
    }
}

fn asset_row(config: &StudioConfig) -> Value {
    let pipeline = config
        .pipeline
        .manifest_keywords()
        .into_iter()
        .map(Value::String)
        .collect();
    mapping([
        ("id", Some(s(config.asset.id.clone()))),
        ("idea", Some(s(config.asset.idea.clone()))),
        ("kind", config.asset.kind.clone().map(s)),
        ("category", Some(s(config.asset.category.clone()))),
        ("seed", config.asset.seed.map(i)),
        ("pipeline", Some(Value::Sequence(pipeline))),
        ("omni", omni_row(config)),
        ("text3d", text3d_row(config)),
        ("refine", Some(refine_row(config))),
        ("paint3d", paint_row(config)),
        ("animate", animate_row(config)),
        ("collision", collision_row(config)),
        ("gpu", Some(s(config.run.gpu.as_str()))),
    ])
}

pub fn build_manifest_yaml(config: &StudioConfig) -> Result<String, serde_yaml::Error> {
    let doc = ManifestDoc {
        output_dir: OUTPUT_DIR.into(),
        path_layout: PATH_LAYOUT.into(),
        images_subdir: IMAGES_SUBDIR.into(),
        meshes_subdir: MESHES_SUBDIR.into(),
        assets: vec![asset_row(config)],
    };
    let header = "# Gerado pelo AiGameKit Studio a partir de studio.toml.\n";
    Ok(format!("{header}{}", serde_yaml::to_string(&doc)?))
}

/// Nome de ficheiro estável para um artefacto intermediário.
pub fn intermediate_name(id: &str, suffix: &str) -> String {
    format!("{id}_{suffix}.glb")
}

/// `true` quando a etapa pede rig (para o `animate` do animator3d).
pub fn wants_animate(config: &StudioConfig) -> bool {
    config.pipeline.has(PipelineStage::Animate)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::GpuMode;

    #[test]
    fn game_yaml_has_required_profile_keys() {
        let config = StudioConfig::new("crate", "a wooden crate", "prop");
        let yaml = build_game_yaml(&config).expect("yaml");
        for key in [
            "title:",
            "genre:",
            "tone:",
            "style_preset:",
            "output_dir:",
            "text3d:",
            "refine:",
            "lod:",
            "paint3d:",
            "collision:",
        ] {
            assert!(yaml.contains(key), "falta {key} em:\n{yaml}");
        }
        assert!(!yaml.contains("generation:")); // sem quality -> omitido
    }

    #[test]
    fn manifest_has_one_asset_and_pipeline() {
        let mut config = StudioConfig::new("crate", "a wooden crate", "prop");
        config.run.gpu = GpuMode::Remote;
        config.paint.texture_size = Some(1024);
        let yaml = build_manifest_yaml(&config).expect("yaml");
        assert!(yaml.contains("id: crate"));
        assert!(yaml.contains("pipeline:"));
        assert!(yaml.contains("- 3d"));
        assert!(yaml.contains("- paint"));
        assert!(yaml.contains("- lod"));
        assert!(yaml.contains("- collision"));
        assert!(yaml.contains("gpu: remote"));
        assert!(yaml.contains("texture_size: 1024"));
    }

    #[test]
    fn refine_false_is_serialized_as_bool() {
        let mut config = StudioConfig::new("crate", "a crate", "prop");
        config.refine.enabled = false;
        let yaml = build_manifest_yaml(&config).expect("yaml");
        assert!(yaml.contains("refine: false"), "{yaml}");
    }

    #[test]
    fn character_preset_turns_on_full_pipeline() {
        let config = StudioConfig::new("hero", "a hero", "humanoid");
        let keywords = config.pipeline.manifest_keywords();
        assert_eq!(keywords, vec!["3d", "paint", "rig", "animate", "lod", "collision"]);
        assert_eq!(
            config.omni.pose_preset.as_deref(),
            Some("quaternius-apose")
        );
    }
}
