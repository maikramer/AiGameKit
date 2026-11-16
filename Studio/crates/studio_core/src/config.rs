//! Configuração persistida em `studio.toml`.
//!
//! É a fonte de verdade da app: `game.yaml` e `manifest.yaml` são gerados a
//! partir daqui (nunca editados à mão pela app).

use serde::{Deserialize, Serialize};

/// Modo de GPU propagado ao vramd / às tools.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum GpuMode {
    #[default]
    Auto,
    Local,
    Remote,
}

impl GpuMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Local => "local",
            Self::Remote => "remote",
        }
    }

    pub const ALL: [Self; 3] = [Self::Auto, Self::Local, Self::Remote];
}

/// Etapas opcionais do pipeline (o shape/3d é sempre a primeira).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PipelineStage {
    Paint,
    Rig,
    Animate,
    Lod,
    Collision,
}

impl PipelineStage {
    pub const ALL: [Self; 5] = [
        Self::Paint,
        Self::Rig,
        Self::Animate,
        Self::Lod,
        Self::Collision,
    ];

    pub fn keyword(self) -> &'static str {
        match self {
            Self::Paint => "paint",
            Self::Rig => "rig",
            Self::Animate => "animate",
            Self::Lod => "lod",
            Self::Collision => "collision",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Paint => "Pintura",
            Self::Rig => "Rig",
            Self::Animate => "Animação",
            Self::Lod => "LODs",
            Self::Collision => "Colisão",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AssetConfig {
    pub id: String,
    pub idea: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    pub category: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seed: Option<i64>,
    /// Tier do `generation:` do perfil.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quality: Option<String>,
    pub style_preset: String,
}

impl Default for AssetConfig {
    fn default() -> Self {
        Self {
            id: "asset".into(),
            idea: String::new(),
            kind: None,
            category: "prop".into(),
            seed: None,
            quality: None,
            style_preset: "painterly".into(),
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Text3dParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub preset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub octree_resolution: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mc_level: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub morph_close: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub split_at_height: Option<bool>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OmniParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub control_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bbox_preset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size_m: Option<[f32; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pose_preset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pose_file: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RefineParams {
    pub enabled: bool,
    pub views: u32,
}

impl Default for RefineParams {
    fn default() -> Self {
        Self {
            enabled: true,
            views: 1,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PaintParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub texture_size: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quality: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pbr_enrich: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub preserve_origin: Option<bool>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RigParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quality: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seed: Option<i64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AnimateParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub preset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub clips: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub anim_pack: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub procedural: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub force_preset: Option<bool>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LodParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rig_max_level: Option<u32>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CollisionParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_faces: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub voxel_size: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub inflate: Option<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PipelineConfig {
    pub stages: Vec<PipelineStage>,
}

impl Default for PipelineConfig {
    fn default() -> Self {
        Self {
            stages: vec![
                PipelineStage::Paint,
                PipelineStage::Lod,
                PipelineStage::Collision,
            ],
        }
    }
}

impl PipelineConfig {
    /// Ordem canónica, sem duplicados.
    pub fn normalized(&self) -> Vec<PipelineStage> {
        PipelineStage::ALL
            .into_iter()
            .filter(|stage| self.stages.contains(stage))
            .collect()
    }

    pub fn has(&self, stage: PipelineStage) -> bool {
        self.stages.contains(&stage)
    }

    pub fn set(&mut self, stage: PipelineStage, enabled: bool) {
        self.stages.retain(|s| *s != stage);
        if enabled {
            self.stages.push(stage);
        }
        self.stages = self.normalized();
    }

    /// `["3d", "paint", …]` como o GameAssets espera.
    pub fn manifest_keywords(&self) -> Vec<String> {
        let mut out = vec!["3d".to_string()];
        out.extend(self.normalized().into_iter().map(|s| s.keyword().to_string()));
        out
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunConfig {
    pub gpu: GpuMode,
    pub fail_fast: bool,
}

impl Default for RunConfig {
    fn default() -> Self {
        Self {
            // A preferência do Vael é gerar na GPU remota; `auto` cai para
            // local quando VRAMD_REMOTE não está definido.
            gpu: GpuMode::Auto,
            fail_fast: true,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MotionParams {
    pub duration_s: Option<f32>,
    pub looped: Option<bool>,
    pub foot_lock: Option<bool>,
    pub quality: Option<String>,
    pub attempts: Option<u32>,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct CameraState {
    pub yaw_deg: f32,
    pub pitch_deg: f32,
    pub distance: f32,
    pub target: [f32; 3],
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct UiConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_artifact: Option<String>,
    #[serde(default)]
    pub wireframe: bool,
    #[serde(default = "default_true")]
    pub show_grid: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub camera: Option<CameraState>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct StudioConfig {
    pub asset: AssetConfig,
    pub text3d: Text3dParams,
    pub omni: OmniParams,
    pub refine: RefineParams,
    pub paint: PaintParams,
    pub rig: RigParams,
    pub animate: AnimateParams,
    pub lod: LodParams,
    pub collision: CollisionParams,
    pub pipeline: PipelineConfig,
    pub run: RunConfig,
    pub motion: MotionParams,
    pub ui: UiConfig,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IssueLevel {
    Error,
    Warning,
}

#[derive(Debug, Clone)]
pub struct Issue {
    pub level: IssueLevel,
    pub message: String,
}

impl Issue {
    fn error(message: impl Into<String>) -> Self {
        Self {
            level: IssueLevel::Error,
            message: message.into(),
        }
    }

    fn warning(message: impl Into<String>) -> Self {
        Self {
            level: IssueLevel::Warning,
            message: message.into(),
        }
    }
}

impl StudioConfig {
    pub fn new(id: impl Into<String>, idea: impl Into<String>, category: impl Into<String>) -> Self {
        let mut config = Self::default();
        config.asset.id = id.into();
        config.asset.idea = idea.into();
        config.asset.category = category.into();
        if config.asset.category == "humanoid" || config.asset.category == "creature" {
            config.pipeline = PipelineConfig {
                stages: PipelineStage::ALL.to_vec(),
            };
            config.omni.control_type = Some("pose".into());
            if config.asset.category == "humanoid" {
                config.omni.pose_preset = Some("quaternius-apose".into());
            }
            config.animate.preset = Some(
                if config.asset.category == "creature" {
                    "creature"
                } else {
                    "humanoid"
                }
                .into(),
            );
        }
        config
    }

    pub fn to_toml(&self) -> Result<String, String> {
        toml::to_string_pretty(self).map_err(|err| format!("toml: {err}"))
    }

    pub fn from_toml(text: &str) -> Result<Self, String> {
        toml::from_str(text).map_err(|err| format!("toml: {err}"))
    }

    pub fn validate(&self) -> Vec<Issue> {
        let mut issues = Vec::new();
        if self.asset.id.trim().is_empty() {
            issues.push(Issue::error("id vazio"));
        } else if !self
            .asset
            .id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        {
            issues.push(Issue::error(
                "id só pode ter letras, números, '_' e '-' (sem espaços/slashes)",
            ));
        }
        if self.asset.idea.trim().is_empty() {
            issues.push(Issue::error("prompt (idea) vazio"));
        }
        if self.asset.category.trim().is_empty() {
            issues.push(Issue::error("categoria vazia"));
        }
        if self.asset.style_preset.trim().is_empty() {
            issues.push(Issue::error("style_preset vazio"));
        }
        if self.pipeline.has(PipelineStage::Rig) && !self.pipeline.has(PipelineStage::Paint) {
            issues.push(Issue::error("rig exige paint (o rigging corre sobre o painted)"));
        }
        if self.pipeline.has(PipelineStage::Animate) && !self.pipeline.has(PipelineStage::Rig) {
            issues.push(Issue::error("animate exige rig"));
        }
        if self.refine.enabled && self.refine.views > 1 {
            issues.push(Issue::warning(
                "refine.views > 1: no Vael o FLUX Klein copiava a referência; só vale a pena com vistas reais",
            ));
        }
        if let Some(size) = self.paint.texture_size {
            if !size.is_power_of_two() {
                issues.push(Issue::warning("texture_size não é potência de 2"));
            }
        }
        if !self.pipeline.has(PipelineStage::Paint) {
            issues.push(Issue::warning(
                "sem paint o LOD0 pode ficar sem baseColorTexture e o estado nunca chega a done",
            ));
        }
        issues
    }

    pub fn has_errors(&self) -> bool {
        self.validate()
            .iter()
            .any(|issue| issue.level == IssueLevel::Error)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_round_trips_through_toml() {
        let mut config = StudioConfig::new("hero", "a hero", "humanoid");
        config.paint.texture_size = Some(2048);
        config.run.gpu = GpuMode::Remote;
        let text = config.to_toml().expect("toml");
        let back = StudioConfig::from_toml(&text).expect("parse");
        assert_eq!(back.asset.id, "hero");
        assert_eq!(back.run.gpu, GpuMode::Remote);
        assert_eq!(back.paint.texture_size, Some(2048));
        assert!(back.pipeline.has(PipelineStage::Rig));
    }

    #[test]
    fn pipeline_order_is_canonical() {
        let mut pipeline = PipelineConfig {
            stages: vec![PipelineStage::Collision, PipelineStage::Paint, PipelineStage::Lod],
        };
        pipeline.set(PipelineStage::Rig, true);
        assert_eq!(
            pipeline.manifest_keywords(),
            vec!["3d", "paint", "rig", "lod", "collision"]
        );
    }

    #[test]
    fn validation_catches_rig_without_paint() {
        let mut config = StudioConfig::new("x", "y", "humanoid");
        config.pipeline.stages = vec![PipelineStage::Rig];
        let issues = config.validate();
        assert!(issues.iter().any(|i| i.message.contains("rig exige paint")));
    }
}
