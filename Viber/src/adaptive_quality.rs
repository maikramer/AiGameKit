//! `<AdaptiveQuality target-fps="60" mode="auto">` — escalonador de qualidade
//! por frame-time (port do plugin `adaptive-quality` do VibeGame).
//!
//! Mede o frame REAL (`Time<Real>`, imune ao hit-stop) numa EMA; frames
//! seguidos acima do orçamento descem um tier, folga sustentada sobe um. As
//! constantes (EMA, streaks, histerese, cooldown) são as do VibeGame — lá
//! foram afinadas contra o flapping na banda do limiar.
//!
//! Tiers: 0 = Max (a lente toda), 1 = High, 2 = Medium, 3 = Low. As alavancas
//! são as caras do Viber — qualidade do SSAO, passos do volumétrico, orçamento
//! de sombras de ponto e, nos tiers baixos, cortes de efeitos inteiros — e
//! entram pela fonte "qualidade" dos gates de pós-processo
//! ([`crate::postfx::set_quality_gates`]), separada da do bridge/QA.
//!
//! `min-pixel-ratio`/`max-pixel-ratio` são aceites por paridade com o
//! VibeGame mas não têm alavanca: o Bevy não tem render-scale interno sem
//! mexer na escala da UI.
//!
//! Env: `VIBER_QUALITY=auto|low|medium|high|max` sobrepõe o `mode` do mundo;
//! `VIBER_ADAPTIVE_QUALITY=0` desliga o escalonador (fica no tier Max).

use bevy::pbr::ScreenSpaceAmbientOcclusionQualityLevel as Ssao;
use bevy::prelude::*;

use crate::postfx::QualityGates;
use crate::worldsys::EngineConfigs;

/// Suavização da EMA do frame-time (~1 s de janela a 60 fps).
pub const EMA_ALPHA: f32 = 0.08;
/// Frames acima do orçamento seguidos antes de descer um tier.
pub const HOT_FRAMES_TO_DOWNSCALE: u32 = 45;
/// Frames com folga seguidos antes de subir um tier.
pub const COLD_FRAMES_TO_UPSCALE: u32 = 300;
/// "Quente" = EMA acima de orçamento × isto.
pub const DOWNSCALE_HYSTERESIS: f32 = 1.35;
/// "Frio" = EMA abaixo de orçamento × isto.
pub const UPSCALE_HYSTERESIS: f32 = 0.55;
/// Intervalo mínimo entre transições (s); subir espera 1.5× isto.
pub const TRANSITION_COOLDOWN_S: f32 = 10.0;
/// Tier mais baixo (Low).
pub const LOWEST_TIER: u8 = 3;
/// `target-fps` por omissão (o do VibeGame).
pub const DEFAULT_TARGET_FPS: f32 = 55.0;

/// `auto` escalona; os outros prendem um tier.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QualityMode {
    Auto,
    Pinned(u8),
}

impl QualityMode {
    pub fn parse(raw: &str) -> Option<Self> {
        Some(match raw.trim().to_ascii_lowercase().as_str() {
            "auto" => Self::Auto,
            "max" | "ultra" => Self::Pinned(0),
            "high" | "alto" => Self::Pinned(1),
            "medium" | "medio" | "médio" => Self::Pinned(2),
            "low" | "baixo" => Self::Pinned(3),
            _ => return None,
        })
    }
}

/// Nome do tier para logs/leituras.
pub fn tier_name(tier: u8) -> &'static str {
    match tier {
        0 => "max",
        1 => "high",
        2 => "medium",
        _ => "low",
    }
}

/// O que cada tier corta: gates de pós-processo + teto de sombras de ponto.
pub fn tier_levers(tier: u8) -> (QualityGates, Option<usize>) {
    match tier {
        0 => (QualityGates::default(), None),
        1 => (
            QualityGates {
                off: Vec::new(),
                ssao: Some(Ssao::Medium),
                volumetric_steps: Some(32),
            },
            Some(8),
        ),
        2 => (
            QualityGates {
                off: vec!["DOF", "MOTION_BLUR", "CONTACT_SHADOWS"],
                ssao: Some(Ssao::Low),
                volumetric_steps: Some(24),
            },
            Some(4),
        ),
        _ => (
            QualityGates {
                off: vec![
                    "DOF",
                    "MOTION_BLUR",
                    "CONTACT_SHADOWS",
                    "SSAO",
                    "VOLUMETRICS",
                    "CHROMATIC",
                ],
                ssao: Some(Ssao::Low),
                volumetric_steps: Some(24),
            },
            Some(0),
        ),
    }
}

/// Estado do escalonador — a decisão é pura ([`Self::step`]) para os testes
/// não dependerem do relógio.
#[derive(Debug, Clone, Resource)]
pub struct AdaptiveQuality {
    pub target_fps: f32,
    pub mode: QualityMode,
    /// Tier em vigor (0 = Max … 3 = Low).
    pub tier: u8,
    /// EMA do frame-time real (ms); `None` até à primeira amostra.
    pub ema_ms: Option<f32>,
    hot_frames: u32,
    cold_frames: u32,
    since_transition: f32,
    pub transitions: u32,
}

impl AdaptiveQuality {
    pub fn new(target_fps: f32, mode: QualityMode) -> Self {
        let tier = match mode {
            QualityMode::Auto => 0,
            QualityMode::Pinned(t) => t.min(LOWEST_TIER),
        };
        Self {
            target_fps: if target_fps.is_finite() && target_fps > 0.0 {
                target_fps
            } else {
                DEFAULT_TARGET_FPS
            },
            mode,
            tier,
            ema_ms: None,
            hot_frames: 0,
            cold_frames: 0,
            // O arranque (shaders, streaming) é lento por natureza: a
            // primeira transição espera o cooldown inteiro.
            since_transition: 0.0,
            transitions: 0,
        }
    }

    /// Amostra um frame de `dt` segundos reais; devolve o tier novo quando
    /// houve transição.
    pub fn step(&mut self, dt: f32) -> Option<u8> {
        let sample_ms = dt * 1000.0;
        // Tab em background/breakpoint: amostra absurda envenenava a EMA.
        if !(sample_ms > 0.0 && sample_ms <= 1000.0) {
            return None;
        }
        self.since_transition += dt;
        let ema = match self.ema_ms {
            Some(prev) => prev * (1.0 - EMA_ALPHA) + sample_ms * EMA_ALPHA,
            None => sample_ms,
        };
        self.ema_ms = Some(ema);
        let QualityMode::Auto = self.mode else {
            return None;
        };
        let target_ms = 1000.0 / self.target_fps;
        self.hot_frames = if ema > target_ms * DOWNSCALE_HYSTERESIS {
            self.hot_frames + 1
        } else {
            0
        };
        self.cold_frames = if ema < target_ms * UPSCALE_HYSTERESIS {
            self.cold_frames + 1
        } else {
            0
        };
        let next = if self.hot_frames >= HOT_FRAMES_TO_DOWNSCALE
            && self.tier < LOWEST_TIER
            && self.since_transition >= TRANSITION_COOLDOWN_S
        {
            self.tier + 1
        } else if self.cold_frames >= COLD_FRAMES_TO_UPSCALE
            && self.tier > 0
            && self.since_transition >= TRANSITION_COOLDOWN_S * 1.5
        {
            self.tier - 1
        } else {
            return None;
        };
        self.tier = next;
        self.since_transition = 0.0;
        self.hot_frames = 0;
        self.cold_frames = 0;
        self.transitions += 1;
        Some(next)
    }
}

fn apply_tier(tier: u8) {
    let (gates, shadow_cap) = tier_levers(tier);
    crate::postfx::set_quality_gates(gates);
    crate::ambient::set_quality_shadow_cap(shadow_cap);
}

/// Resolve o `mode`: env `VIBER_QUALITY` > atributo do mundo > `auto`.
fn resolve_mode(attr: Option<&str>) -> QualityMode {
    if let Ok(raw) = std::env::var("VIBER_QUALITY") {
        match QualityMode::parse(&raw) {
            Some(mode) => return mode,
            None => {
                warn!("adaptive-quality: VIBER_QUALITY='{raw}' inválido (auto|low|medium|high|max)")
            }
        }
    }
    match attr.map(|raw| (raw, QualityMode::parse(raw))) {
        Some((_, Some(mode))) => mode,
        Some((raw, None)) => {
            warn!("adaptive-quality: mode='{raw}' inválido (auto|low|medium|high|max) — auto");
            QualityMode::Auto
        }
        None => QualityMode::Auto,
    }
}

fn install_adaptive_quality(mut commands: Commands, configs: Option<Res<EngineConfigs>>) {
    let Some(tag) = configs.as_ref().and_then(|c| c.first("adaptivequality")) else {
        return;
    };
    if std::env::var("VIBER_ADAPTIVE_QUALITY").as_deref() == Ok("0")
        || matches!(
            tag.attr("enabled").map(str::trim),
            Some("0" | "false" | "off")
        )
    {
        info!("adaptive-quality: desligado — tier max fixo");
        return;
    }
    let state = AdaptiveQuality::new(
        tag.f32_attr("target-fps").unwrap_or(DEFAULT_TARGET_FPS),
        resolve_mode(tag.attr("mode")),
    );
    apply_tier(state.tier);
    info!(
        "adaptive-quality: alvo {:.0} fps, modo {:?}, tier inicial {}",
        state.target_fps,
        state.mode,
        tier_name(state.tier)
    );
    commands.insert_resource(state);
}

fn drive_adaptive_quality(time: Res<Time<Real>>, state: Option<ResMut<AdaptiveQuality>>) {
    let Some(mut state) = state else {
        return;
    };
    if let Some(tier) = state.step(time.delta_secs()) {
        apply_tier(tier);
        info!(
            "adaptive-quality: tier → {} (EMA {:.1} ms, alvo {:.1} ms)",
            tier_name(tier),
            state.ema_ms.unwrap_or(0.0),
            1000.0 / state.target_fps
        );
    }
}

/// Liga o `<AdaptiveQuality>`; inerte em mundos sem a tag.
pub struct AdaptiveQualityPlugin;

impl Plugin for AdaptiveQualityPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(
            Startup,
            install_adaptive_quality.after(crate::recipes::spawn::startup),
        )
        .add_systems(Update, drive_adaptive_quality);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Corre `frames` frames de `ms` cada, devolvendo as transições.
    fn run(q: &mut AdaptiveQuality, frames: u32, ms: f32) -> Vec<u8> {
        (0..frames).filter_map(|_| q.step(ms / 1000.0)).collect()
    }

    #[test]
    fn test_sustained_overload_downscales_one_tier_after_cooldown() {
        let mut q = AdaptiveQuality::new(60.0, QualityMode::Auto);
        // 40 ms ≫ 16.7 × 1.35: quente desde o 1.º frame, mas o cooldown de
        // arranque (10 s = 250 frames) segura a transição até lá.
        assert!(run(&mut q, 240, 40.0).is_empty(), "9.6 s < cooldown");
        assert_eq!(run(&mut q, 60, 40.0), vec![1]);
        assert_eq!(q.tier, 1);
    }

    #[test]
    fn test_overload_walks_down_to_low_and_stops() {
        let mut q = AdaptiveQuality::new(60.0, QualityMode::Auto);
        let changes = run(&mut q, 2000, 40.0);
        assert_eq!(changes, vec![1, 2, 3]);
        assert_eq!(q.tier, LOWEST_TIER);
    }

    #[test]
    fn test_headroom_upscales_slower_than_overload_downscales() {
        let mut q = AdaptiveQuality::new(60.0, QualityMode::Auto);
        run(&mut q, 2000, 40.0);
        assert_eq!(q.tier, 3);
        // 5 ms < 16.7 × 0.55: folga. Subir pede 300 frames frios seguidos
        // (a EMA ainda gasta ~26 frames a descer dos 40 ms)…
        assert!(run(&mut q, 300, 5.0).is_empty());
        assert_eq!(run(&mut q, 100, 5.0), vec![2]);
        // …e 15 s (3000 frames de 5 ms) entre subidas.
        assert!(run(&mut q, 2900, 5.0).is_empty());
        assert_eq!(run(&mut q, 3200, 5.0), vec![1, 0]);
    }

    #[test]
    fn test_in_band_frame_times_never_transition() {
        let mut q = AdaptiveQuality::new(60.0, QualityMode::Auto);
        // 20 ms: acima do alvo (16.7) mas dentro da histerese (< 22.5).
        assert!(run(&mut q, 5000, 20.0).is_empty());
        assert_eq!(q.tier, 0);
    }

    #[test]
    fn test_pinned_mode_never_moves() {
        let mut q = AdaptiveQuality::new(60.0, QualityMode::Pinned(2));
        assert_eq!(q.tier, 2);
        assert!(run(&mut q, 2000, 40.0).is_empty());
        assert!(run(&mut q, 5000, 5.0).is_empty());
        assert_eq!(q.tier, 2);
    }

    #[test]
    fn test_absurd_samples_are_ignored() {
        let mut q = AdaptiveQuality::new(60.0, QualityMode::Auto);
        q.step(0.016);
        let ema = q.ema_ms;
        q.step(5.0);
        q.step(0.0);
        q.step(f32::NAN);
        assert_eq!(q.ema_ms, ema);
    }

    #[test]
    fn test_mode_parse_and_invalid_target_fps() {
        assert_eq!(QualityMode::parse(" Auto "), Some(QualityMode::Auto));
        assert_eq!(QualityMode::parse("low"), Some(QualityMode::Pinned(3)));
        assert_eq!(QualityMode::parse("MAX"), Some(QualityMode::Pinned(0)));
        assert_eq!(QualityMode::parse("turbo"), None);
        assert_eq!(
            AdaptiveQuality::new(0.0, QualityMode::Auto).target_fps,
            DEFAULT_TARGET_FPS
        );
    }

    #[test]
    fn test_tier_levers_are_monotonic() {
        let mut prev_off = 0;
        let mut prev_cap = usize::MAX;
        for tier in 0..=LOWEST_TIER {
            let (gates, cap) = tier_levers(tier);
            assert!(
                gates.off.len() >= prev_off,
                "tier {tier} corta menos que o anterior"
            );
            let cap = cap.unwrap_or(usize::MAX);
            assert!(cap <= prev_cap);
            prev_off = gates.off.len();
            prev_cap = cap;
        }
        assert_eq!(
            tier_levers(0),
            (QualityGates::default(), None),
            "Max = sem cortes"
        );
    }
}
