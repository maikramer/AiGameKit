//! DLSS Super Resolution (NVIDIA NGX) — a via NATIVA do Bevy 0.20
//! (`bevy_anti_alias` feature `dlss`, por cima do `dlss_wgpu`).
//!
//! **Contrato de build**: a feature `dlss` do Viber NÃO é default — o
//! `dlss_wgpu` exige o SDK da NVIDIA em compile time (env `DLSS_SDK` → clone
//! de github.com/NVIDIA/DLSS na tag v310.9.1, + `VULKAN_SDK` com headers e
//! libclang para o bindgen). O binário jogável constrói-se com
//! `cargo build --release --features dlss` ou `viber run --dlss`; sem a
//! feature, este módulo reduz-se ao parser (testável em CI sem SDK).
//!
//! **Contrato de runtime**: com a feature compilada, o DLSS fica LIGADO por
//! omissão (compilar com a feature é o opt-in explícito) e a câmara troca o
//! TAA pelo componente `Dlss` — o Bevy trata sozinho do jitter HALTON, do
//! mip bias, do motion-vector prepass e do `MainPassResolutionOverride`
//! (main pass a resolução reduzida, upscale no `EarlyPostProcess`, postfx
//! restante e UI a full-res). Sem RTX/NGX/backend Vulkan a engine cai para o
//! TAA de sempre com um warn — a câmara nunca fica sem AA.
//!
//! Gate: `VIBER_DLSS` > `dlss:` do config.yaml > `Auto`. On/off é gate de
//! arranque (como o TAA); o MODO troca-se ao vivo pela REPL
//! (`viber.debug.dlss("performance")`, src/bridge/lua.rs).

/// Modo de DLSS pedido. Puro de propósito: parse e hierarquia do gate são
/// testáveis sem a feature e sem SDK.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum DlssSetting {
    Off,
    Auto,
    Dlaa,
    Quality,
    Balanced,
    Performance,
    Ultra,
}

impl DlssSetting {
    /// Parse tolerante (a casa dos bools do XML estendeu-se às envs): aceita
    /// `0/1/off/on` e os modos por nome, PT ou EN, case/acentos ignorados.
    pub fn parse(value: &str) -> Option<Self> {
        let value = value.trim().to_lowercase();
        match value.as_str() {
            "0" | "off" | "desligado" | "false" | "no" | "nao" | "não" => Some(Self::Off),
            "1" | "auto" => Some(Self::Auto),
            "dlaa" => Some(Self::Dlaa),
            "quality" | "qualidade" => Some(Self::Quality),
            "balanced" | "equilibrado" => Some(Self::Balanced),
            "performance" | "desempenho" => Some(Self::Performance),
            "ultra" | "ultra-performance" | "ultraperformance" => Some(Self::Ultra),
            _ => None,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Auto => "auto",
            Self::Dlaa => "dlaa",
            Self::Quality => "quality",
            Self::Balanced => "balanced",
            Self::Performance => "performance",
            Self::Ultra => "ultra",
        }
    }
}

/// Resolução ÚNICA por processo (o padrão `PRESET` do graphics.rs):
/// `VIBER_DLSS` > `dlss:` do config.yaml > `Auto`. Valores inválidos avisam
/// e caem para o degrau seguinte — nunca derrubam o arranque.
pub fn requested(config: Option<&str>) -> DlssSetting {
    static SETTING: std::sync::OnceLock<DlssSetting> = std::sync::OnceLock::new();
    *SETTING.get_or_init(|| resolve_once(std::env::var("VIBER_DLSS").ok().as_deref(), config))
}

/// A hierarquia do gate, pura (sem OnceLock, sem ambiente) — é isto que os
/// testes exercitam; [`requested`] é o wrapper que a congela por processo.
fn resolve_once(env: Option<&str>, config: Option<&str>) -> DlssSetting {
    if let Some(value) = env {
        match DlssSetting::parse(value) {
            Some(setting) => return setting,
            None => bevy::log::warn!(
                "dlss: VIBER_DLSS={value:?} não reconhecido (off|auto|dlaa|quality|balanced|performance|ultra)"
            ),
        }
    }
    if let Some(raw) = config {
        match DlssSetting::parse(raw) {
            Some(setting) => return setting,
            None => {
                bevy::log::warn!("dlss: config.yaml dlss={raw:?} não reconhecido — a usar auto")
            }
        }
    }
    DlssSetting::Auto
}

// Tudo o que segue toca nos tipos do NGX — só existe com a feature compilada.

/// O modo pedido como `DlssPerfQualityMode` do bevy (`None` = desligado).
#[cfg(feature = "dlss")]
pub fn quality_mode(setting: DlssSetting) -> Option<bevy::anti_alias::dlss::DlssPerfQualityMode> {
    use bevy::anti_alias::dlss::DlssPerfQualityMode;
    match setting {
        DlssSetting::Off => None,
        DlssSetting::Auto => Some(DlssPerfQualityMode::Auto),
        DlssSetting::Dlaa => Some(DlssPerfQualityMode::Dlaa),
        DlssSetting::Quality => Some(DlssPerfQualityMode::Quality),
        DlssSetting::Balanced => Some(DlssPerfQualityMode::Balanced),
        DlssSetting::Performance => Some(DlssPerfQualityMode::Performance),
        DlssSetting::Ultra => Some(DlssPerfQualityMode::UltraPerformance),
    }
}

/// ID fixo do produto (guia de programação do NGX: um UUID por aplicação,
/// NÃO copiar o dos exemplos do bevy). O `DlssInitPlugin` panica sem ele.
#[cfg(feature = "dlss")]
pub fn project_id() -> bevy::anti_alias::dlss::DlssProjectId {
    use bevy::anti_alias::dlss::DlssProjectId;
    DlssProjectId(bevy::asset::uuid::uuid!(
        "9c4d1f6a-8b2e-4c73-9a5d-e1f2b7c8d0e4"
    ))
}

/// Teleporte = corte de câmara: o histórico temporal do DLSS deixou de
/// representar o frame e sem limpeza o frame anterior "vaza" para o destino
/// (smear). `reset = true` por um frame — o extract do próprio bevy repõe a
/// `false` no fim do frame. O DLSS é mais sensível a cortes que o TAA (o
/// TAA nunca teve este tratamento; os teleportes do simple-rpg passam todos
/// por `TeleportSettle`, ver player.rs).
#[cfg(feature = "dlss")]
fn reset_on_teleport(
    _trigger: bevy::ecs::observer::On<bevy::ecs::lifecycle::Add<crate::player::TeleportSettle>>,
    mut cameras: bevy::ecs::system::Query<&mut bevy::anti_alias::dlss::Dlss>,
) {
    for mut dlss in &mut cameras {
        dlss.reset = true;
    }
}

/// Registo do observer (chamado do `PostFxPlugin` — o hub da integração).
#[cfg(feature = "dlss")]
pub(crate) fn register_teleport_reset(app: &mut bevy::app::App) {
    app.add_observer(reset_on_teleport);
}

#[cfg(test)]
mod tests {
    use super::DlssSetting as S;

    #[test]
    fn parses_modes_en_pt_and_bools() {
        assert_eq!(S::parse("quality"), Some(S::Quality));
        assert_eq!(S::parse("  PERFORMANCE "), Some(S::Performance));
        assert_eq!(S::parse("Ultra-Performance"), Some(S::Ultra));
        assert_eq!(S::parse("equilibrado"), Some(S::Balanced));
        assert_eq!(S::parse("qualidade"), Some(S::Quality));
        assert_eq!(S::parse("desempenho"), Some(S::Performance));
        assert_eq!(S::parse("0"), Some(S::Off));
        assert_eq!(S::parse("off"), Some(S::Off));
        assert_eq!(S::parse("não"), Some(S::Off));
        assert_eq!(S::parse("1"), Some(S::Auto));
        assert_eq!(S::parse("dlaa"), Some(S::Dlaa));
        assert_eq!(S::parse(""), None);
        assert_eq!(S::parse("turbo"), None);
    }

    #[test]
    fn requested_falls_back_to_auto_without_env_or_config() {
        // A hierarquia em pure form (requested() congela por processo — o
        // OnceLock é o padrão do graphics.rs e não é testável em série).
        use super::resolve_once;
        assert_eq!(resolve_once(None, None), S::Auto);
        assert_eq!(resolve_once(None, Some("off")), S::Off);
        assert_eq!(resolve_once(None, Some("lixo")), S::Auto);
        assert_eq!(
            resolve_once(Some("performance"), Some("off")),
            S::Performance
        );
        assert_eq!(resolve_once(Some("0"), Some("quality")), S::Off);
        assert_eq!(resolve_once(Some("que??"), Some("quality")), S::Quality);
    }
}
