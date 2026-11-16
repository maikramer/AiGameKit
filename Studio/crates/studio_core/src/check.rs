//! Tipos do payload de `gameassets studio check --json`.
//!
//! O contrato é tolerante: campos novos do Python não podem quebrar a app, e
//! campos em falta ficam a `None`/default.

use std::collections::BTreeMap;

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize, Default)]
pub struct CheckPayload {
    #[serde(default)]
    pub version: u32,
    #[serde(default)]
    pub project_dir: String,
    #[serde(default)]
    pub profile_path: String,
    #[serde(default)]
    pub manifest_path: String,
    #[serde(default)]
    pub asset: Asset,
    #[serde(default)]
    pub prompt: Prompt,
    #[serde(default)]
    pub refine: Refine,
    #[serde(default)]
    pub layout: Layout,
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub stale: Stale,
    #[serde(default)]
    pub promotion: Promotion,
    #[serde(default)]
    pub artifacts: BTreeMap<String, Artifact>,
    #[serde(default)]
    pub deliverables: BTreeMap<String, Artifact>,
    #[serde(default)]
    pub lock: Option<LockInfo>,
    #[serde(default)]
    pub tools: BTreeMap<String, Option<String>>,
    #[serde(default)]
    pub gpu: Gpu,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Asset {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub idea: String,
    #[serde(default)]
    pub kind: Option<String>,
    #[serde(default)]
    pub category: String,
    #[serde(default)]
    pub pipeline: Vec<String>,
    #[serde(default)]
    pub generate_3d: bool,
    #[serde(default)]
    pub generate_paint: bool,
    #[serde(default)]
    pub generate_rig: bool,
    #[serde(default)]
    pub generate_animate: bool,
    #[serde(default)]
    pub generate_lod: bool,
    #[serde(default)]
    pub generate_collision: bool,
    #[serde(default)]
    pub seed: Option<i64>,
    #[serde(default)]
    pub gpu: String,
    #[serde(default)]
    pub target_faces: Option<u64>,
    #[serde(default)]
    pub paint_texture_size: Option<u32>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Prompt {
    #[serde(default)]
    pub text2d: String,
    #[serde(default)]
    pub text3d_hint: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Refine {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub views: Vec<String>,
    #[serde(default)]
    pub skipped: bool,
    #[serde(default)]
    pub stale: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Layout {
    #[serde(default)]
    pub output_dir: String,
    #[serde(default)]
    pub path_layout: String,
    #[serde(default)]
    pub images_subdir: String,
    #[serde(default)]
    pub meshes_subdir: String,
    #[serde(default)]
    pub image_ext: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Stale {
    #[serde(default)]
    pub omni: bool,
    #[serde(default)]
    pub refine: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Promotion {
    #[serde(default)]
    pub lod0: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Artifact {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub canonical: Option<String>,
    #[serde(default)]
    pub exists: bool,
    #[serde(default)]
    pub size: Option<u64>,
    #[serde(default)]
    pub mtime: Option<f64>,
    #[serde(default)]
    pub glb: Option<GlbSummary>,
    #[serde(default)]
    pub gates: Option<Gates>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct GlbSummary {
    #[serde(default)]
    pub parsed: bool,
    #[serde(default)]
    pub materials: u64,
    #[serde(default)]
    pub skins: u64,
    #[serde(default)]
    pub animations: u64,
    #[serde(default)]
    pub animation_names: Vec<String>,
    #[serde(default)]
    pub base_color_texture: bool,
    #[serde(default)]
    pub extensions_used: Vec<String>,
    #[serde(default)]
    pub extensions_required: Vec<String>,
    #[serde(default)]
    pub meshes: u64,
    #[serde(default)]
    pub primitive_count: u64,
    #[serde(default)]
    pub vertex_count_total: u64,
    #[serde(default)]
    pub triangle_count_total: u64,
    #[serde(default)]
    pub v_per_tri: Option<f64>,
    #[serde(default)]
    pub attributes_present: Vec<String>,
    #[serde(default)]
    pub has_normals: bool,
    #[serde(default)]
    pub has_tangents: bool,
    #[serde(default)]
    pub has_uv: bool,
    #[serde(default)]
    pub has_joints: bool,
    #[serde(default)]
    pub has_weights: bool,
    #[serde(default)]
    pub byte_size: u64,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Gates {
    #[serde(default)]
    pub skins: bool,
    #[serde(default)]
    pub animations: bool,
    #[serde(default)]
    pub materials: bool,
    #[serde(default)]
    pub duplicate_clips: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct LockInfo {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub live: bool,
    #[serde(default)]
    pub pid: Option<u32>,
    #[serde(default)]
    pub host: Option<String>,
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub started_at: Option<String>,
    #[serde(default)]
    pub invalid: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Gpu {
    #[serde(default)]
    pub mode_env: Option<String>,
    #[serde(default)]
    pub remote: Option<String>,
}

impl CheckPayload {
    pub fn parse(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }

    /// A etapa pendente segundo o classificador do GameAssets.
    pub fn next_stage(&self) -> &str {
        self.state
            .strip_prefix("need_")
            .unwrap_or(self.state.as_str())
    }

    pub fn artifact(&self, name: &str) -> Option<&Artifact> {
        self.artifacts.get(name)
    }

    pub fn has_artifact(&self, name: &str) -> bool {
        self.artifact(name).is_some_and(|a| a.exists)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_payload_with_unknown_fields() {
        let payload = CheckPayload::parse(
            r#"{
                "version": 1,
                "project_dir": "/tmp/p",
                "asset": {"id": "crate", "idea": "a crate", "pipeline": ["3d"], "target_faces": 24000},
                "state": "need_paint",
                "stale": {"omni": false},
                "promotion": {"lod0": "none"},
                "artifacts": {
                    "shape": {"path": "/tmp/p/meshes/_intermediate/crate_shape.glb", "exists": true, "size": 10,
                              "glb": {"parsed": true, "triangle_count_total": 64, "animation_names": ["walk"]},
                              "gates": {"materials": true}}
                },
                "lock": null,
                "tools": {"text3d": "/usr/bin/text3d"},
                "gpu": {"mode_env": null, "remote": "192.168.0.100:8790"},
                "future_field": {"ignored": true}
            }"#,
        )
        .expect("payload");

        assert_eq!(payload.asset.id, "crate");
        assert_eq!(payload.next_stage(), "paint");
        assert!(payload.has_artifact("shape"));
        assert!(!payload.has_artifact("painted"));
        assert_eq!(
            payload.artifact("shape").unwrap().glb.as_ref().unwrap().triangle_count_total,
            64
        );
    }

    #[test]
    fn missing_optional_sections_default() {
        let payload = CheckPayload::parse(
            r#"{"version":1,"state":"need_image","asset":{"id":"x"}}"#,
        )
        .expect("payload");
        assert_eq!(payload.asset.id, "x");
        assert!(payload.artifacts.is_empty());
        assert!(payload.lock.is_none());
        assert_eq!(payload.gpu.remote, None);
    }
}
