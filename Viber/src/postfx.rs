//! Pós-processamento da câmara: exposição, bloom e ambient occlusion.
//!
//! A câmara era um `Camera3d::default()` puro — sem bloom, sem AO, sem
//! exposição autoral —, pelo que a cena lia achatada e lavada: o sol a
//! 10 000 lux com a exposição por omissão do Bevy nunca deixa nada saturar e
//! nada brilha. Os XMLs do `simple-rpg` já pedem tudo isto
//! (`pp-exposure`, `pp-bloom-strength` por `<BiomeRegion>`); o migrador
//! listou-os como *dropped attrs*.
//!
//! O que este módulo liga, por ordem de impacto visual:
//!
//! 1. **[`Exposure`]** — a exposição fotográfica da câmara. `EV100_SUNLIGHT`
//!    seria correcto para 100 000 lux reais; os mundos autoram o sol a
//!    ~10 000 lux, logo a base fica entre o interior e o dia claro e os
//!    biomas ajustam-na (`pp-exposure`, onde 1.0 = base).
//! 2. **[`Bloom`]** — só o que já está acima do branco floresce (fogueiras,
//!    materiais emissivos, o disco solar do domo). Preset `NATURAL`, que é
//!    energy-conserving: não injecta luz nova na imagem.
//! 3. **[`ScreenSpaceAmbientOcclusion`]** — o contacto entre objectos e chão.
//!    É o que dá "assentado" às árvores, às bancas e ao herói; sem ele tudo
//!    parece autocolante sobre o terreno. Exige os prepasses de profundidade
//!    e normal, que o próprio componente declara via `#[require]`.
//!
//! Passe visual r1 ("Luz & Atmosfera") acrescentou à lente:
//!
//! 4. **[`DepthOfField`]** — bokeh subtil com o foco no herói
//!    ([`drive_dof_focus`] persegue a distância câmara↔herói); teto de CoC
//!    20 px e `max_depth` 700 m para o horizonte não virar sopa.
//! 5. **[`Vignette`] + [`ChromaticAberration`]** — a "lente" fotográfica,
//!    doseada muito abaixo dos defaults.
//! 6. **[`VolumetricFog`] + [`FogVolume`]** — god-rays: o volume segue o
//!    herói e o `VolumetricLight` do sol (spawn.rs) acende-o onde a luz
//!    atravessa geometria. Desligável com `VIBER_NO_VOLUMETRICS=1`.
//!
//! Passe visual P1.10 (VISUAL_ROADMAP) acrescentou à lente:
//!
//! 7. **[`MotionBlur`]** — desfoque de movimento por pixel com os defaults
//!    cinematográficos (shutter 180°, 1 amostra). O `#[require]` insere o
//!    `MotionVectorPrepass`; o TAA já exige o MESMO prepass, portanto os
//!    motion vectors são partilhados entre os dois.
//! 8. **[`ContrastAdaptiveSharpening`]** — sharpening adaptativo ao contraste
//!    (CAS), a correr no post-process DEPOIS do AA: devolve o detalhe que o
//!    TAA (e o FXAA do fallback) suavizam.
//!
//! LOOP B (hierarquia de valores, gauntlet BOTW) acrescentou:
//!
//! 9. **Split-tone pós-tonemap** ([`SplitToneSettings`], passe fullscreen do
//!    `FullscreenMaterialPlugin`) — sombras frias / highlights quentes pela
//!    hora: a manhã/tarde baixas deixam de ser um "warm wash" uniforme (o
//!    `temperature` GLOBAL do ColorGrading aquece tudo) e ganham o par
//!    quente/frio que faz a leitura BOTW; à noite o azul das sombras aprofunda
//!    e as poças quentes ganham contraste de MATIZ. Normalizado à luminância:
//!    nunca mexe na exposição (a LOOP A fica intacta por construção). O braço
//!    diurno lê a ELEVAÇÃO REAL do sol ([`low_sun_split_weight`]) — a
//!    gaussiana `golden` da atmosfera é estreita demais (07:30 do gauntlet:
//!    sol a 26,9°, `golden` ≈ 0,0003 — o split era identidade na própria
//!    cena do crítico; medido 2026-09-13).
//! 10. **KEY da lua** ([`moon_key_drive`]) — a luz direcional que o
//!     `sun_drive` aponta à lua ganha ×[`MOON_KEY_GAIN`] de iluminância e um
//!     azul mais frio à noite: topo iluminado pela lua lê contra o vale em
//!     sombra, que é o que desenha SILHUETAS. Zero VRAM extra (mesma luz,
//!     mesmas cascatas de sombra).
//!
//! `VIBER_NO_POSTFX=1` desliga tudo (comparações A/B e GPUs fracas).
//!
//! LOOP C (luzes que TRABALHAM + perspetiva aérea, gauntlet BOTW)
//! acrescentou:
//!
//! 11. **Bloom NOCTURNO** ([`NIGHT_BLOOM_THRESHOLD`]) — o prefilter a 700
//!     matava TODO o bloom à noite (ver o const); à noite o threshold desce
//!     para 0,70 (rampa com `night`) e a intensidade sobe: as CHAMAS das
//!     lanternas (~1,0 no buffer) e os MIÚDOS das poças florescem com halo;
//!     a massa do chão ao luar (0,1..0,3) e o céu ficam fora. O dia mantém
//!     o 700 histórico intacto.
//! 12. **Perspetiva aérea** ([`AERIAL_WGSL`], passe fullscreen depth-aware)
//!     — o longe dessatura e desvia para cinza-azul em vez de branquear:
//!     lê `dist + profundidade` do prepass, rampa 140→620 m, e mistura o
//!     píxel para `luminância × tint azul-cinza` com um leve escurecer de
//!     contraste — as serras viram CAMADAS contra o céu, o look BOTW que o
//!     crítico pediu contra a "névoa branca de bug".

use std::sync::OnceLock;

use bevy::anti_alias::contrast_adaptive_sharpening::ContrastAdaptiveSharpening;
use bevy::anti_alias::fxaa::Fxaa;
use bevy::anti_alias::taa::{TemporalAntiAliasing, temporal_anti_alias};
use bevy::camera::Exposure;
use bevy::core_pipeline::fullscreen_material::{FullscreenMaterial, FullscreenMaterialPlugin};
use bevy::core_pipeline::prepass::{DepthPrepass, NormalPrepass, ViewPrepassTextures};
use bevy::core_pipeline::schedule::Core3d;
use bevy::core_pipeline::tonemapping::Tonemapping;
use bevy::core_pipeline::tonemapping::tonemapping as tonemapping_pass;
use bevy::core_pipeline::{Core3dSystems, FullscreenShader};
use bevy::light::{FogVolume, VolumetricFog};
use bevy::pbr::{
    ContactShadows, ScreenSpaceAmbientOcclusion, ScreenSpaceAmbientOcclusionQualityLevel,
};
use bevy::post_process::auto_exposure::{AutoExposure, AutoExposureCompensationCurve};
use bevy::post_process::bloom::{Bloom, BloomPrefilter};
use bevy::post_process::dof::{DepthOfField, DepthOfFieldMode};
use bevy::post_process::effect_stack::{ChromaticAberration, Vignette};
use bevy::post_process::motion_blur::{MotionBlur, motion_blur};
use bevy::prelude::*;
use bevy::render::extract_component::{ExtractComponent, ExtractComponentPlugin};
use bevy::render::render_resource::{
    BindGroup, BindGroupEntries, BindGroupLayoutDescriptor, BindGroupLayoutEntry, BindingType,
    Buffer, BufferBindingType, BufferDescriptor, BufferSize, BufferUsages, CachedRenderPipelineId,
    Canonical, ColorTargetState, ColorWrites, FragmentState, Operations, PipelineCache,
    RenderPassColorAttachment, RenderPassDescriptor, RenderPipeline, RenderPipelineDescriptor,
    Sampler, SamplerBindingType, SamplerDescriptor, ShaderStages, ShaderType, Specializer,
    SpecializerKey, TextureFormat, TextureSampleType, TextureView, TextureViewDimension,
    TextureViewId, Variants, VertexState,
};
use bevy::render::renderer::{RenderContext, RenderDevice, RenderQueue, ViewQuery};
use bevy::render::view::{ExtractedView, Msaa, ViewTarget};
use bevy::render::{Extract, ExtractSchedule, Render, RenderApp, RenderStartup, RenderSystems};
use bevy::shader::Shader;

use crate::ambient::point_in_polygon;
use crate::player::Player;
use crate::profiler::{Group, timed};
use crate::worldsys::BiomeRegions;
use crate::worldsys::SunLightBase;

/// Exposição base (EV100) — o mesmo default do Bevy (`EV100_BLENDER`).
///
/// Mantê-lo aqui é deliberado: os mundos autoram o sol a ~10 000 lux (e não
/// aos ~100 000 do `EV100_SUNLIGHT`), pelo que 9.7 é a exposição a que a cena
/// já foi iluminada. O ganho de contraste vem do AO, do bloom e das sombras,
/// não de escurecer a imagem por baixo do que o autor viu; quem quiser
/// escurecer usa o `pp-exposure` do bioma (o `simple-rpg` pede 0.70–0.78).
pub const BASE_EV100: f32 = 9.7;
/// Intensidade base do bloom (o preset `NATURAL` do Bevy usa 0.15).
pub const BASE_BLOOM: f32 = 0.12;
/// Teto do bloom — o clamp do bioma e o punch de impacto partilham-no.
pub const MAX_BLOOM: f32 = 0.5;

/// Threshold do prefilter de bloom de DIA — o valor histórico do módulo.
///
/// A escala do buffer HDR: os shaders PBR multiplicam por `view.exposure`
/// (EV100 9,7 ⇒ ÷1505), mas o DOMO do céu e as PARTÍCULAS não — escrevem
/// valores crus. De dia o domo (paleta 0,05..0,9) é o maior gradiente liso
/// do frame e o boost de baixa-frequência do preset NATURAL (0,7) transformava
/// qualquer threshold baixo numa wash de ecrã inteiro — o 700 é o guarda.
pub const DAY_BLOOM_THRESHOLD: f32 = 700.0;

/// Threshold do prefilter de bloom na NOITE plena (LOOP C).
///
/// **Âncoras do buffer HDR a EV100 9,7** (às 23:00 da vila; o bloom lê o
/// buffer PRÉ-grading — o ND de −2,8 EV da LOOP A e o medidor aplicam-se ao
/// tonemap, DEPOIS): a MÉDIA da noite mede ≈ **0,21** (o −2,25 EV do
/// histograma do AutoExposure), o chão ao luar anda 0,1..0,3, o miolo da
/// poça a 2-3 m de uma lanterna de 330 klm ≈ **0,5..1,0** e a chama
/// (partícula aditiva, sem `view.exposure`) ≈ **1,0**. O 700 histórico
/// ficava acima de TUDO — "não há bloom nenhum" era literal. **0,70** com
/// softness 0,5 (joelho 0,35..0,70) põe as chamas e os miúlos das poças
/// DENTRO e a massa do chão/céu FORA — o halo fica nas fontes, não no véu.
/// (Primeira tentativa a 0,30: joelho a meio da massa do frame ⇒ a
/// baixa-frequência do bloom lavava a noite inteira, média 0,155→0,312 —
/// medido 2026-09-13.)
pub const NIGHT_BLOOM_THRESHOLD: f32 = 0.70;

/// Reforço de intensidade do bloom na noite plena (LOOP C). A base do bioma
/// (0,12) + o `bloom_boost` da atmosfera à noite (0,16) + isto ≈ 0,48 — o
/// halo das lanternas lê-se a 60+ m sem chegar ao teto [`MAX_BLOOM`].
pub const NIGHT_BLOOM_INTENSITY: f32 = 0.20;

/// `low_frequency_boost` do bloom na noite plena (LOOP C) — o preset NATURAL
/// usa 0,7. A baixa frequência é o MILO dos halos: com o threshold nocturno
/// alto ([`NIGHT_BLOOM_THRESHOLD`], só chamas/poças/moon alimentam o bloom),
/// subir para 1,1 ALARGA o halo suave sem tocar na massa do frame — é isto
/// que faz uma chama sub-pixel a 90 m ler como fonte de luz com corpo.
/// O dia fica nos 0,7 do preset.
pub const NIGHT_BLOOM_LF_BOOST: f32 = 1.1;

/// `low_frequency_boost` corrente pela fração de noite: 0,7 de dia (o
/// NATURAL aprovado), [`NIGHT_BLOOM_LF_BOOST`] na noite plena.
pub fn night_bloom_lf_boost(night: f32) -> f32 {
    if !night.is_finite() {
        return 0.7;
    }
    0.7 + (NIGHT_BLOOM_LF_BOOST - 0.7) * night.clamp(0.0, 1.0)
}

/// Intensidade da vinheta na noite plena (LOOP C) — o dia mantém os 0,30 da
/// r1. A vinheta é um grade ESPACIAL: escurece a PERIFERIA (onde vive a
/// massa uniforme do chão ao luar) e poupa o CENTRO (o herói, a rua, as
/// poças) — é o único passe que separa o miolo do frame SEM mexer na
/// exposição (o medidor do AutoExposure normaliza a média e apaga qualquer
/// tentativa global de espalhar o histograma; medido 2026-09-13: lanternas
/// ×1,5 + ambiente −25% mudaram a MÉDIA e não a FORMA, rácio p90/mediana
/// 1,18→1,18).
pub const NIGHT_VIGNETTE: f32 = 0.48;

/// Threshold do prefilter pela fração de noite: 700 de dia (o look aprovado
/// não mexe), [`NIGHT_BLOOM_THRESHOLD`] na noite plena, rampa linear no meio
/// (o crepúsculo mantém thresholds altos — o céu ainda tem gradiente liso).
pub fn night_bloom_threshold(night: f32) -> f32 {
    if !night.is_finite() {
        return DAY_BLOOM_THRESHOLD;
    }
    let n = night.clamp(0.0, 1.0);
    DAY_BLOOM_THRESHOLD + (NIGHT_BLOOM_THRESHOLD - DAY_BLOOM_THRESHOLD) * n
}
/// Piso do escurecimento por punch (EV) — o "ai" do dano recebido não
/// transforma o ecrã em breu.
pub const MAX_DARKEN_EV: f32 = 2.5;
/// Velocidade do crossfade de exposição/bloom ao mudar de bioma (por segundo).
const BLEND_RATE: f32 = 1.6;

/// Constante de tempo do decay de um kick de exposição (s). ~3× isto lê-se
/// como "um flash que dura 1 s" — o kick do level-up vive neste regime.
pub const KICK_TAU: f32 = 0.32;
/// Teto do kick acumulado (EV) — dois level-ups seguidos não estouram a imagem.
pub const MAX_KICK_EV: f32 = 1.2;
/// Abaixo deste valor o kick corta a zero (fim determinístico do decay).
const KICK_EPS: f32 = 1e-3;

/// TAA ligado por omissão (passe r2): faz o AA, estabiliza o noise do PCSS
/// (`ShadowFilteringMethod::Temporal`), do SSAO `High` e do ContactShadows —
/// técnicas estocásticas desenhadas PARA acumulação temporal. O TAA do Bevy
/// não lida bem com meshes alpha-blended (a água), portanto `VIBER_NO_TAA=1`
/// devolve o FXAA + Gaussian e desce o SSAO a Medium (o fallback r1).
fn taa_enabled() -> bool {
    std::env::var_os("VIBER_NO_TAA").is_none()
}

/// Volumetrics (god-rays + volume de névoa) LIGADOS por omissão; desligar com
/// `VIBER_NO_VOLUMETRICS=1`.
///
/// História: na r1 (sem TAA) `VolumetricFog` + `FogVolume` + `VolumetricLight`
/// apagavam o frame inteiro (cena preta, HUD vivo, zero erros no log) —
/// bevy 0.19.1 + wgpu 29.0.4 + NV 595.84. Com o TAA da r2 a mesma stack
/// RENDERIZA (bisseção 2026-09-07: glow de scattering na direção do sol
/// confirmado em qa-visual). Custo: ~+8 ms de raymarch fixo por pixel —
/// em mundos pesados `VIBER_NO_VOLUMETRICS=1` devolve o orçamento.
/// Nível do SSAO (`VIBER_SSAO`); sem o env, o histórico: High com TAA,
/// Medium sem (o ruído de 18 spp sem acumulação temporal não se limpava).
fn ssao_quality(taa: bool) -> ScreenSpaceAmbientOcclusionQualityLevel {
    use ScreenSpaceAmbientOcclusionQualityLevel as Q;
    match std::env::var("VIBER_SSAO").as_deref() {
        Ok("low") | Ok("baixo") => Q::Low,
        Ok("medium") | Ok("medio") | Ok("médio") => Q::Medium,
        Ok("high") | Ok("alto") => Q::High,
        Ok("ultra") => Q::Ultra,
        _ if taa => Q::High,
        _ => Q::Medium,
    }
}

fn volumetrics_enabled() -> bool {
    std::env::var_os("VIBER_NO_VOLUMETRICS").is_none()
}

/// Passos do raymarch volumétrico (`VIBER_VOLUMETRIC_STEPS`).
///
/// É o único custo do volumétrico que se regula: o passe é full-res e não tem
/// knob de resolução interna. O `jitter` + o TAA acumulam os passos ao longo
/// dos frames, portanto a 40 passos a imagem CONVERGIDA é praticamente a mesma
/// (o que sobe é o ruído por frame, que o TAA já tem de limpar por causa do
/// PCSS). O gate existe para o A/B de QA: medir o frame e comparar a imagem
/// antes de mexer no default.
fn volumetric_steps() -> u32 {
    std::env::var("VIBER_VOLUMETRIC_STEPS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|steps| *steps > 0)
        .unwrap_or(64)
}

/// Marcador do [`FogVolume`] cinemático que segue o herói.
#[derive(Component)]
struct CinematicFogVolume;

/// Alvos de pós-processamento em vigor (base do mundo × bioma atual).
#[derive(Debug, Clone, Resource)]
pub struct PostFxState {
    /// EV100 alvo, já com o multiplicador do bioma aplicado.
    pub target_ev100: f32,
    /// Intensidade de bloom alvo.
    pub target_bloom: f32,
    /// Valores correntes (interpolados na direção dos alvos).
    pub ev100: f32,
    pub bloom: f32,
    /// Kick de juice em curso (EV, ≥0 — positivo CLAREIA a imagem). Soma-se
    /// à exposição interpolada e decai exponencialmente ([`decay_kick`]);
    /// é o flash do level-up (`vitals::LEVELUP_KICK_EV`).
    pub kick: f32,
}

impl Default for PostFxState {
    fn default() -> Self {
        Self {
            target_ev100: BASE_EV100,
            target_bloom: BASE_BLOOM,
            ev100: BASE_EV100,
            bloom: BASE_BLOOM,
            kick: 0.0,
        }
    }
}

impl PostFxState {
    /// Soma um kick de exposição (EV positivo clareia). Acumulativo até
    /// [`MAX_KICK_EV`]; o decay ([`decay_kick`], no `drive_postfx`) trata do
    /// resto — chamar de novo num kick a meio não reinicia o flash, soma-lhe.
    pub fn kick_exposure(&mut self, ev_delta: f32) {
        if !ev_delta.is_finite() || ev_delta <= 0.0 {
            return;
        }
        self.kick = (self.kick + ev_delta).clamp(0.0, MAX_KICK_EV);
    }
}

/// EV100 efetivo na câmara: o kick CLAREIA (menos EV = mais luz captada).
pub fn ev_with_kick(ev100: f32, kick: f32) -> f32 {
    ev100 - kick
}

/// Decay exponencial do kick (`dt` em segundos). Monotónico e determinístico:
/// corta a zero em [`KICK_EPS`] para o flash ter fim exato (e a escrita na
/// câmara não ficar eternamente em `Changed`).
pub fn decay_kick(kick: f32, dt: f32) -> f32 {
    if kick <= 0.0 || !kick.is_finite() {
        return 0.0;
    }
    let next = kick * (-dt.max(0.0) / KICK_TAU).exp();
    if next < KICK_EPS { 0.0 } else { next }
}

/// `pp-exposure` do XML é um multiplicador de exposição linear (0.78, 0.70…),
/// não um EV. Menos luz = mais EV100 na câmara, e a relação é logarítmica:
/// `ev = base − log2(mult)`. Um `pp-exposure` de 0.5 escurece exactamente um
/// stop.
pub fn ev100_for_exposure_multiplier(base_ev100: f32, multiplier: f32) -> f32 {
    if multiplier <= 0.0 || !multiplier.is_finite() {
        return base_ev100;
    }
    base_ev100 - multiplier.log2()
}

/// Teto do ganho da exposição AUTOMÁTICA nas cenas escuras, em stops.
///
/// **Âncora empírica (2026-09-12, vila do simple-rpg):** o histograma do
/// `AutoExposure` lê o buffer HDR, onde o fog e o domo escrevem a PALETA em
/// bruto e as poças/chamas/luas escrevem valores 10..30× acima — a MÉDIA da
/// noite da vila mede ≈ −2.25 EV e a golden hour ≈ −2.6 EV (derivado de
/// pares de screenshots com lift conhecido). O "escuro físico" de −6..−12
/// em que a curva original pensava nunca acontece; a curva até −3 é o que
/// protege o DIA e a GOLDEN HOUR de serem tocados, e o teto abaixo de −6 é
/// a rede de segurança para cenas mesmo sem luz nenhuma.
///
/// A legibilidade da NOITE não vive aqui — vive no desvio de grading
/// [`NIGHT_GRADING_EXPOSURE_EV`], que escurece o frame DEPOIS do medidor
/// (estável, não é corrigido). Este teto mantém-se 0.5: defensivo, e
/// intocado pelo look aprovado de dia.
/// `VIBER_NO_AECURVE=1` devolve a curva plana (o comportamento sem teto)
/// para A/B.
pub const NIGHT_LIFT_CAP_EV: f32 = 0.5;

/// Luminância média (log2, a unidade do histograma) a partir da qual a curva
/// começa a cercear o lift do medidor (rampa até ao teto).
///
/// **−6,0 (LOOP C; era −3,0).** A noite da vila MEDIA −2,25 EV quando a curva
/// foi calibrada (LOOP A, ambiente nocturno 0,13); o lote LOOP C baixou o
/// ambiente (0,075) e a média desceu para ≈ **−3,9** — CAIU DENTRO da rampa
/// antiga, que roubava ~1,65 stops ao medidor: com o ND de −2,8 EV por cima,
/// as poças esmagavam para ~0,15 e liam-se azul (o "no light doing work" do
/// crítico era em parte ISTO — medido 2026-09-13: brazeiro de 1M lm com a
/// curva antiga quente −0,09; com o joelho a −6 e a mesma cena, poça a
/// tonemap(1,5) ≈ 0,55 BLAZING). O joelho a −6 fica ABAIXO da noite mais
/// escura autorada (−3,9) com margem, e a golden hour (−2,6) e o dia
/// continuam em medição cheia — exactamente como estavam.
pub const NIGHT_LIFT_KNEE_EV: f32 = 6.0;

/// Compensação (stops) que o medidor soma ao alvo, para uma cena de
/// luminância média `x` (log2) — o `y` da curva de
/// [`night_capped_compensation_curve`], e a única fonte da regra:
/// `alvo = comp(x) − x`.
///
/// * `x ≥ −KNEE`: `0` — o alvo de sempre (`−x`, expor para o meio-cinza):
///   dia, crepúsculo, sombra funda e interiores ficam exactamente como eram.
/// * `x ≤ −2·KNEE`: `x + CAP` — o alvo fica preso em [`NIGHT_LIFT_CAP_EV`].
/// * Entre os dois: rampa linear do `y` entre `0` e `−2·KNEE + CAP`, para o
///   shutter não dar um degrau ao escurecer. (Rampa no `y` — rampear o ALVO
///   não é monótono, porque `−x` cresce mais depressa do que a rampa aperta.)
pub fn auto_exposure_compensation(x: f32) -> f32 {
    let knee = NIGHT_LIFT_KNEE_EV;
    if !x.is_finite() {
        return 0.0;
    }
    if x >= -knee {
        0.0
    } else if x <= -2.0 * knee {
        x + NIGHT_LIFT_CAP_EV
    } else {
        let s = (-x - knee) / knee;
        (NIGHT_LIFT_CAP_EV - 2.0 * knee) * s
    }
}

/// Ganho (stops) que a exposição automática aplica a uma cena de luminância
/// média `x` (log2): `comp(x) − x`. Ver [`auto_exposure_compensation`].
pub fn auto_exposure_target_lift(x: f32) -> f32 {
    auto_exposure_compensation(x) - x
}

/// `AutoExposureCompensationCurve` que implementa
/// [`auto_exposure_compensation`]: o medidor soma-a ao alvo.
///
/// A curva é a API desenhada pelo Bevy para isto (asset próprio, amostrado
/// por LUT de 256 valores no passe do medidor) — não há knob de "ganho
/// máximo" no componente.
pub fn night_capped_compensation_curve() -> AutoExposureCompensationCurve {
    use bevy::math::cubic_splines::LinearSpline;
    AutoExposureCompensationCurve::from_curve(LinearSpline::new(night_capped_curve_points()))
        .unwrap_or_default()
}

/// Pontos `(x = luminância média em log2, y = compensação em stops)` da curva
/// de [`night_capped_compensation_curve`] — amostram
/// [`auto_exposure_compensation`] nos cepos (e no escuro fundo, onde o valor
/// já é constante); separados para o teste poder verificar que a curva é
/// construível (monótona, sem descontinuidades).
///
/// EXACTIDÃO f32 OBRIGATÓRIA: o `from_curve` do Bevy valida a continuidade
/// com IGUALDADE BIT-A-BIT (`p0 + (p1 − p0) == p1`), o que só acontece em
/// coordenadas que são frações binárias EXACTAS (múltiplos de 0.25 aqui).
/// Amostrar em x "arbitrários" (−2.85…) fazia o `LinearSpline` falhar com
/// `DiscontinuityFound` e a curva caía silenciosamente na LUT PLANA (sem
/// teto — a noite voltava ao branco). Com `knee` = 2.0 os pontos da rampa
/// (y = 2x+4) herdam a exactidão dos x escolhidos.
fn night_capped_curve_points() -> [bevy::math::Vec2; 6] {
    use bevy::math::vec2;
    let knee = NIGHT_LIFT_KNEE_EV;
    let at = |x: f32| vec2(x, auto_exposure_compensation(x));
    [
        // Escuro fundo: o teto já está preso (dois pontos só para a LUT
        // cobrir toda a gama do histograma). −15/−12/−9/−6/0/8 são múltiplos
        // de 0.25 — exactidão f32 obrigatória (ver doc acima). NOTA: com
        // knee = 6, o 2.º ponto (−2·knee = −12) tem de ser DISTINTO do 1.º
        // (x duplicado = DiscontinuityFound → LUT plana silenciosa).
        at(-15.0),
        at(-2.0 * knee),
        at(-9.0),
        at(-knee),
        at(0.0),
        at(8.0),
    ]
}

/// Pulso de pós-processo num impacto de combate: `stops` de exposição
/// (positivo CLAREIA um instante — hit 0.25, crítico/finisher 0.5, abate 0.7;
/// negativo ESCURECE — o "ai" do dano recebido) + `bloom_add` de
/// florescimento. Clarear usa o kick de juice (teto [`MAX_KICK_EV`], decay
/// próprio); escurecer empurra o EV efetivo para cima (piso
/// [`MAX_DARKEN_EV`]) e o bloom soma até [`MAX_BLOOM`] — o `drive_postfx`
/// devolve tudo ao alvo do bioma a [`BLEND_RATE`]/s sozinho, o punch nunca
/// fica preso.
pub fn punch_impact(state: &mut PostFxState, stops: f32, bloom_add: f32) {
    if !stops.is_finite() || !bloom_add.is_finite() {
        return;
    }
    if stops > 0.0 {
        state.kick_exposure(stops);
    } else if stops < 0.0 {
        state.ev100 = (state.ev100 - stops).min(BASE_EV100 + MAX_DARKEN_EV);
    }
    if bloom_add != 0.0 {
        state.bloom = (state.bloom + bloom_add).clamp(0.0, MAX_BLOOM);
    }
}

/// Liga o pós-processamento e mantém exposição/bloom sincronizados com o
/// bioma do herói.
pub struct PostFxPlugin;

impl Plugin for PostFxPlugin {
    fn build(&self, app: &mut App) {
        // O estado vive MESMO com o pós-processo desligado: os punches de
        // combate/skills escrevem-no (kick_exposure/punch_impact) e é só o
        // drive que não corre — sem o init, esses sistemas panica-iam.
        app.init_resource::<PostFxState>();
        if std::env::var_os("VIBER_NO_POSTFX").is_some() {
            info!("postfx: desligado por VIBER_NO_POSTFX");
            return;
        }
        app.add_plugins(bevy::post_process::auto_exposure::AutoExposurePlugin);
        // LOOP B — split-tone pós-tonemap: o shader é INLINE (const), o
        // handle vive no OnceLock porque `fragment_shader()` é estática.
        let shader_handle = app
            .world_mut()
            .resource_mut::<Assets<Shader>>()
            .add(Shader::from_wgsl(SPLIT_TONE_WGSL, "viber_split_tone.wgsl"));
        let _ = SPLIT_TONE_SHADER.set(shader_handle);
        app.add_plugins(FullscreenMaterialPlugin::<SplitToneSettings>::default());
        // LOOP C — perspetiva aérea: passe fullscreen depth-aware (padrão
        // water_ssr, ANTES do TAA). Default-on, sem env de opt-out (é o
        // caminho oficial da distância, não um efeito opcional).
        let aerial_shader =
            app.world_mut()
                .resource_mut::<Assets<Shader>>()
                .add(Shader::from_wgsl(
                    AERIAL_WGSL,
                    "viber_aerial_perspective.wgsl",
                ));
        let _ = AERIAL_SHADER.set(aerial_shader);
        app.add_plugins(ExtractComponentPlugin::<AerialPerspective>::default());
        if let Some(render_app) = app.get_sub_app_mut(RenderApp) {
            render_app
                .init_resource::<ExtractedAerial>()
                .add_systems(ExtractSchedule, extract_aerial)
                .add_systems(RenderStartup, init_aerial_pipeline)
                .add_systems(
                    Render,
                    prepare_aerial.in_set(RenderSystems::PrepareBindGroups),
                )
                .add_systems(Core3d, aerial_schedule_configs());
        }
        app.add_systems(
            bevy::app::Update,
            (
                attach_postfx_to_cameras,
                // O grading lê [`crate::worldsys::AtmosphereState`] do MESMO
                // frame (publicada depois de `sun_drive`; o registo/glue vive
                // no `AmbientPlugin`, que também consome a paleta).
                timed(Group::Fx, drive_postfx).after(crate::worldsys::atmosphere_drive),
                timed(Group::Fx, drive_split_tone),
                // KEY da lua (LOOP B): depois do `sun_drive` (que reescreve a
                // luz a partir do SunLightBase) e depois da atmosfera (fonte
                // da fração `night`).
                timed(Group::Fx, moon_key_drive).after(crate::worldsys::sun_drive),
                // KEY dourada (LOOP D): mesma cadeia, janela de sol baixo —
                // depois da da lua para o crepúsculo compor por cima se
                // algum dia os pesos coexistirem (na prática não coexistem).
                timed(Group::Fx, golden_key_drive).after(crate::worldsys::sun_drive),
                // POÇAS de tocha (LOOP D): ganho nocturno ×8 nas PointLights
                // quentes (base capturada; dia = ganho 1). Depois da
                // atmosfera (fração `night`).
                timed(Group::Fx, torch_pool_drive).after(crate::worldsys::atmosphere_drive),
                timed(Group::Fx, drive_dof_focus),
            )
                .chain(),
        );
        if volumetrics_enabled() {
            app.add_systems(
                bevy::app::Startup,
                spawn_fog_volume, // precisa de Assets<Image> para a textura 3D
            );
            app.add_systems(
                bevy::app::Update,
                (follow_fog_volume, timed(Group::Fx, drive_fog_texture)),
            );
        }
    }
}

/// Equipa cada `Camera3d` que ainda não tem pós-processamento. Corre no
/// `Update` (e não num startup) porque a câmara do mundo nasce dentro do
/// spawn exclusivo, depois dos startup systems normais.
fn attach_postfx_to_cameras(
    mut commands: Commands,
    state: Res<PostFxState>,
    cameras: Query<Entity, (With<Camera3d>, Without<Bloom>)>,
    mut curves: ResMut<Assets<AutoExposureCompensationCurve>>,
    // Uma curva por processo, partilhada por todas as câmaras.
    mut curve: Local<Option<Handle<AutoExposureCompensationCurve>>>,
) {
    let compensation_curve = curve
        .get_or_insert_with(|| {
            if std::env::var_os("VIBER_NO_AECURVE").is_some() {
                info!("postfx: teto da exposição automática DESLIGADO (VIBER_NO_AECURVE)");
                curves.add(AutoExposureCompensationCurve::default())
            } else {
                curves.add(night_capped_compensation_curve())
            }
        })
        .clone();
    for camera in &cameras {
        commands.entity(camera).insert((
            Bloom {
                intensity: state.bloom,
                // O preset NATURAL tem threshold 0.0: COM TUDO a brilhar, o
                // boost de baixa-frequência (0.7) transforma regiões lisas e
                // grandes de HDR alto — o domo do céu, que escreve a PALETA
                // directamente no alvo HDR (SKY_RADIANCE = 1, ver sky.rs) —
                // numa wash de ecrã inteiro (superfícies texturizadas
                // cancelam-se nos mips; um gradiente liso não). Threshold 700
                // deixa o glow para o que é realmente brilhante; à NOITE o
                // `drive_postfx` desce-o (rampa `night`) — ver
                // [`NIGHT_BLOOM_THRESHOLD`].
                prefilter: BloomPrefilter {
                    threshold: DAY_BLOOM_THRESHOLD,
                    threshold_softness: 0.5,
                },
                ..Bloom::NATURAL
            },
            Exposure { ev100: state.ev100 },
            // TonyMcMapface (r5): o ACES esmagava o toe — sombras e o
            // primeiro plano da golden hour liam-se como preto puro (o
            // crítico reprovou 3×). O TMcMapface levanta a toe e amortece o
            // ombro: sombras com textura, highlights sem clipar a branco.
            Tonemapping::TonyMcMapface,
            // O AO precisa dos dois prepasses; o componente declara-os em
            // `#[require]`, mas inseri-los aqui deixa a dependência explícita
            // para quem ler o spawn da câmara.
            DepthPrepass,
            NormalPrepass,
            // TAA (r2): a acumulação temporal é o que faz o PCSS-temporal, o
            // SSAO High e os contact shadows lerem limpos (são estocásticos —
            // sem TAA ficam noisy). `#[require]` traz jitter/mip-bias/motion
            // vectors automaticamente. Água alpha-blended pode fantasmar;
            // VIBER_NO_TAA=1 devolve o FXAA.
            Msaa::Off,
            // Motion blur (P1.10): desfoque de movimento por pixel com os
            // defaults cinematográficos (shutter 180°, 1 amostra). O
            // `#[require]` insere o MotionVectorPrepass; o TAA já exige o
            // MESMO prepass, logo os motion vectors são partilhados.
            MotionBlur::default(),
            // SSAO High aproveita o denoise temporal do TAA (r1 era Medium
            // por causa do ruído sem acumulação). `VIBER_SSAO=medium|low|high|
            // ultra` sobrepõe para A/B — Medium (8 spp vs 18 do High) é a
            // alvanca listada no PERFORMANCE.md; o TAA limpa o ruído extra.
            ScreenSpaceAmbientOcclusion {
                quality_level: ssao_quality(taa_enabled()),
                ..ScreenSpaceAmbientOcclusion::default()
            },
            // Contact shadows: raymarch na depth por luz com sombras — as
            // sombras de contacto miúdas (herói→chão, poste→calçada) onde os
            // shadow maps não têm resolução. Passo linear 24 ≈ raios de 0.5 m.
            ContactShadows {
                linear_steps: 24,
                thickness: 0.2,
                length: 0.5,
            },
            // Exposição AUTO (r2): a câmara mede o histograma e adapta-se —
            // a noite da vila abre +2..3 stops sozinha (as lanternas POPAM)
            // e o meio-dia fecha. Combina com o EV autoral do bioma
            // (compensação multiplicativa). Velocidades cinematográficas:
            // abre devagar, fecha mais devagar ainda.
            //
            // A CURVA limita a abertura a [`NIGHT_LIFT_CAP_EV`] nos escuros:
            // sem ela o medidor satura de noite e abre os +6 EV do máximo,
            // que na escala da paleta (céu/névoa) é um frame branco.
            AutoExposure {
                range: -6.0..=8.0,
                speed_brighten: 1.2,
                speed_darken: 0.5,
                compensation_curve: compensation_curve.clone(),
                ..AutoExposure::default()
            },
            // DoF cinemático subtil: foco no herói (o `drive_dof_focus`
            // persegue a distância real câmara↔herói), bokeh com teto de 20 px
            // e `max_depth` a 700 m para o horizonte/fog não virar sopa.
            DepthOfField {
                mode: DepthOfFieldMode::Bokeh,
                focal_distance: 5.0,
                aperture_f_stops: 1.4,
                max_circle_of_confusion_diameter: 20.0,
                max_depth: 700.0,
                ..DepthOfField::default()
            },
            // Vinheta leve e aberração cromática subtil — a "lente" da câmara.
            // Intensidade muito abaixo dos defaults (1.0 / 0.02) para ler como
            // vidro fotográfico e não como filtro.
            Vignette {
                intensity: 0.30,
                radius: 0.85,
                smoothness: 2.5,
                ..Vignette::default()
            },
            ChromaticAberration {
                intensity: 0.0035,
                max_samples: 8,
                ..ChromaticAberration::default()
            },
            // CAS (P1.10): sharpening adaptativo ao contraste — corre no
            // post-process DEPOIS do AA e devolve o detalhe que o TAA (e o
            // FXAA do fallback) suavizam.
            ContrastAdaptiveSharpening::default(),
            // Color grading (CDL) — o `drive_postfx` conduz-o pela hora do dia.
            bevy::render::view::ColorGrading::default(),
        ));
        // Split-tone (LOOP B), à parte: o tuple acima já usa os 15 slots do
        // `Bundle` — sombras frias / highlights quentes pela hora, passe
        // fullscreen pós-tonemap conduzido pelo `drive_split_tone`.
        commands.entity(camera).insert(SplitToneSettings::default());
        // Perspetiva aérea (LOOP C): o marcador liga o passe depth-aware no
        // render app (as matrizes vêm do `ExtractedView` no prepare).
        commands.entity(camera).insert(AerialPerspective);
        if taa_enabled() {
            commands.entity(camera).insert((
                TemporalAntiAliasing::default(),
                // Espiral Jimenez (CoD:AW) + rotação por noise — desenho PARA
                // o TAA; suaviza a penumbra do PCSS.
                bevy::light::ShadowFilteringMethod::Temporal,
            ));
        } else {
            commands.entity(camera).insert((
                Fxaa::default(),
                bevy::light::ShadowFilteringMethod::Gaussian,
            ));
        }
        // SSR da água (`VIBER_WATER_SSR=1`, src/water_ssr.rs): o marcador
        // ativa o passe no Core3d (entre o TAA e o tonemapping). Os prepasses
        // depth/normal já foram inseridos acima para o SSAO.
        if crate::water_ssr::water_ssr_requested() {
            commands.entity(camera).insert(crate::water_ssr::WaterSsr);
        }
        // Volumetrics (god-rays + volume de névoa que segue o herói):
        // LIGADOS por omissão — desligar com `VIBER_NO_VOLUMETRICS=1` (ver
        // `volumetrics_enabled`; apagavam o frame na bisseção r1, renderizam
        // com o TAA da r2).
        if volumetrics_enabled() {
            commands.entity(camera).insert(VolumetricFog {
                // Sem EnvironmentMapLight no motor: ambient do volume a 0 —
                // a névoa brilha pela luz do sol (VolumetricLight), não por
                // si mesma (senão a noite fica com uma wash cinzenta).
                ambient_intensity: 0.0,
                // BANDING: sem jitter o raymarch amostra os MESMOS offsets
                // por raios de profundidade semelhante e as faixas de
                // integração aparecem como linhas horizontais no céu (raios
                // longos até ao domo, 850 m, com 32/64 passos). O jitter
                // desloca a origem do raio por noise — o TAA acumula e
                // dissolve as faixas.
                jitter: 1.0,
                step_count: volumetric_steps(),
                ..VolumetricFog::default()
            });
        }
    }
}

/// Temperatura do grading CDL pela hora: a golden hour aquece (+0.35), a
/// noite arrefece (−0.25) — o "film stock" muda com o dia.
pub fn grade_temperature(golden: f32, night: f32) -> f32 {
    grade_temperature_full(golden, 0.0, night)
}

/// Aquecimento GLOBAL do CDL na janela de sol baixo (LOOP D). O `golden` da
/// atmosfera é a gaussiana estreita (pico 4°, σ 8° — ~0 às 07:30); sem este
/// braço o stock só aquecia nos ~15 min do nascer. 0.28 pintava o frame
/// inteiro de laranja (crítica r1-D) — 0.10 mantém o aquecimento na KEY da
/// luz e no reboco sunlit; o resto do frame preserva o albedo.
pub const GOLDEN_GRADE_WARMTH: f32 = 0.10;

/// Temperatura do grading CDL pela hora, com o braço de sol baixo (LOOP D).
/// `low_sun` é [`low_sun_split_weight`] da elevação REAL do sol: a 07:30
/// (26,9°) soma ~0.10 de aquecimento global — o reboco sunlit aquece pela
/// KEY da luz e o resto do frame pelo stock; as sombras ficam para o
/// split-tone arrefecer (B > R medido em sombra a 07:30).
pub fn grade_temperature_full(golden: f32, low_sun: f32, night: f32) -> f32 {
    let g = if golden.is_finite() { golden } else { 0.0 };
    let l = if low_sun.is_finite() { low_sun } else { 0.0 };
    let n = if night.is_finite() { night } else { 0.0 };
    g * 0.35 + l * GOLDEN_GRADE_WARMTH - n * 0.25
}

/// Saturação pós-tonemap do grading CDL pela hora. A golden hour satura
/// (+0.18); a noite NÃO dessatura (2026-09-12): a paleta noturna (fog/céu)
/// já é um azul profundo pouco saturado à nascença (B:R ≈ 2.6 no fog) e o
/// −0.22 antigo esmagava-o a CINZENTO — a "lama" do crítico (saturação
/// medida 0.07 no frame inteiro às 23:00 da vila). A assinatura BOTW é azul
/// SATURADO: 1.0 deixa o azul da paleta chegar ao ecrã e as poças de
/// lanterna ganham o contraste de matiz que as separa do fundo.
pub fn grade_saturation(golden: f32, _night: f32) -> f32 {
    1.0 + golden * 0.18
}

/// Desvio de exposição CDL (stops) aplicado à noite, por [`night`].
///
/// A PALETA é que está clara demais para ler como noite: o fog e o domo
/// escrevem valores crus (fog noturno ≈ 0.05 linear) e o medidor (que lê o
/// buffer COM essas fontes) abre a cena toda para a média — resultado
/// medido 2026-09-12, vila às 23:00 pré-correcção: luminância média 0.28,
/// 92% do frame sem textura local, ZERO píxeis escuros — uma noite "de
/// estúdio" sem escuridão (a "lama cinzenta" do crítico). O BOTW lê-se
/// porque a noite É ESCURA: céu 0.05..0.13, chão ao luar ~0.10, poças e
/// lua por contraste.
///
/// Este desvio EMPURRA o frame inteiro para baixo (−2.8 stops na noite
/// cheia, rampando com `night`) DEPOIS do medidor — um filtro ND estável
/// que o `AutoExposure` não corrige (o histograma é computado no buffer
/// HDR pré-grading). As razões preservam-se: o fog e o céu escurecem, e o
/// que já estava 10..30× acima do fog (chamas emissivas, lua, estrelas e
/// as poças com as lanternas reforçadas no XML) continua acima e POPA.
/// Medido: média 0.28 → 0.114, "lama" 0.84 → 0.002, 52% do frame <0.10,
/// poças quentes visíveis, céu azul B:R ≈ 1.9.
///
/// O dia (night=0) fica EXACTAMENTE como estava — desvio 0.
/// **−6,8 (LOOP C; era −2,8 na LOOP A).** Com o joelho da curva do medidor
/// alargado (ver [`NIGHT_LIFT_KNEE_EV`]) as POÇAS voltaram a viver — mas o
/// medidor passa a centrar a noite ~2 stops acima do que a cena da LOOP A
/// pedia (medido 2026-09-13, vantage do crítico às 23:00: ND −2,8 → média
/// 0,61 "noite de estúdio"; ND −4,8 → 0,38, ND −6,8 → 0,21 — o ombro do TonyMcMapface come
/// um stop por cada par de EV). −7,4 pousa a noite na banda escura
/// (0,08–0,20) COM as poças e os halos por cima (flames ≥0,9 sobrevivem ao
/// filtro no ombro; o miolo das poças também). O dia (night = 0) fica
/// EXACTAMENTE como estava — desvio 0.
pub const NIGHT_GRADING_EXPOSURE_EV: f32 = -7.4;

/// Desvio de exposição (stops) para a fração de noite corrente — rampa
/// linear até [`NIGHT_GRADING_EXPOSURE_EV`] na noite cheia.
pub fn grade_night_exposure_offset(night: f32) -> f32 {
    NIGHT_GRADING_EXPOSURE_EV * night.clamp(0.0, 1.0)
}

// ── LOOP B: hierarquia de valores (key de lua + split-tone) ─────────────
//
// A LOOP A tornou a noite LEGÍVEL (azul escuro em vez de lama) — mas o
// crítico mantém dois gaps contra o BOTW:
//
// 1. **Noite sem âncora focal**: pontos quentes espalhados num vazio, sem
//    KEY dominante que desenhe silhuetas contra o céu. A luz direcional já
//    vira lua às 6% (`worldsys::sun_drive`, 600 lux do sol de 10 klx), o
//    que após o filtro ND de −2.8 EV da LOOP A deixa o chão ao luar NO
//    MESMO nível do fog — tudo meio-cinza-azulado, nada se destaca.
// 2. **Sem separação de temperatura**: o `ColorGrading` global aquece a
//    golden hour INTEIRA (sombras incluídas — "warm wash"), e as sombras
//    ficam pretos neutros que desligam do caster. O bevy 0.19 só tem
//    `temperature` GLOBAL (as secções shadows/midtones/highlights são
//    escalares: saturation/contrast/gamma/gain/lift), portanto o
//    split-tone quente/frio clássico precisa de um passe próprio.

/// Ganho de iluminância da luz direcional à noite (a KEY da lua).
///
/// O `sun_drive` deixa a lua a [`crate::worldsys::MOONLIGHT_RATIO`] = 6% do
/// sol autorado (600 lux num mundo de 10 klx). Com o filtro ND da LOOP A
/// (−2.8 EV) isso deixa o chão ao luar ≈ ao nível do fog noturno — sem
/// contraste entre "topo iluminado pela lua" e "vale em sombra", que é o
/// que faz uma silhueta ler. Este ganho multiplica POR CIMA do valor do
/// `sun_drive` (composição por frame, sem acumulação) e rampa com a MESMA
/// fração `night` da atmosfera, portanto o crepúsculo não dá salto.
///
/// **1.8 e não mais alto (medido 2026-09-13, vila às 23:00):** com 2.6 o
/// chão/treeline ao luar subia ao NÍVEL do céu noturno (0.16 vs 0.18 de
/// luma — contraste da banda de horizonte 1.09×, silhueta ilegível: a KEY
/// apagava a separação que devia criar). A 1.8 o chão desce para ~60% do
/// céu e a linha de árvores/ telhados lê como massa escura contra o brilho
/// do céu — o look BOTW (céu brilhante, terra escura, poças por contraste).
/// Ver [`MOON_KEY_COLOR_MIX`].
pub const MOON_KEY_GAIN: f32 = 1.8;

/// Fração do degrau de cor da key da lua (0 = fica o `MOON_COLOR` do
/// `sun_drive`, 1 = [`MOON_KEY_COLOR`] cheio à noite plena).
pub const MOON_KEY_COLOR_MIX: f32 = 0.45;

/// Cor linear da key da lua — mais fria e mais saturada que o
/// [`crate::worldsys::MOON_COLOR`] (0.40, 0.54, 0.86), que após a
/// dessaturação do TonyMcMapface nas sombras lia a cinzento-azulado.
/// O push de azul + a subida de iluminância dão o "banho de luar frio"
/// contra o qual as poças quentes (0xffb264) POPAM por MATIZ e não só
/// por brilho.
pub const MOON_KEY_COLOR: [f32; 3] = [0.30, 0.44, 0.98];

/// Multiplicador de iluminância da lua para a fração de noite corrente —
/// 1.0 de dia (o sol fica EXACTAMENTE como estava), [`MOON_KEY_GAIN`] na
/// noite plena, rampa linear no meio.
pub fn moon_illuminance_factor(night: f32) -> f32 {
    if !night.is_finite() {
        return 1.0;
    }
    1.0 + (MOON_KEY_GAIN - 1.0) * night.clamp(0.0, 1.0)
}

/// KEY da lua: reforça a luz direcional que o `sun_drive` já apontou à
/// lua (direção MOON_ELEVATION_DEG, sombras do sol reaproveitadas — ZERO
/// VRAM extra: é a mesma luz, o mesmo shadow map 4096²). Corre DEPOIS do
/// `sun_drive` (composição por frame, nunca acumula) e é no-op total de
/// dia (`night` = 0), portanto o look aprovado do dia/golden da LOOP A
/// não mexe.
fn moon_key_drive(
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    mut lights: Query<(&mut DirectionalLight, &SunLightBase)>,
) {
    let night = atmosphere.night.clamp(0.0, 1.0);
    if night <= 0.0 {
        return;
    }
    let gain = moon_illuminance_factor(night);
    let mix = MOON_KEY_COLOR_MIX * night;
    for (mut light, _base) in &mut lights {
        // Multiplicação PURA por cima do que o sun_drive escreveu neste
        // frame: o blend dia/noite do sun_drive fica intacto e não há
        // feedback (o SunLightBase nunca é alterado).
        light.illuminance *= gain;
        let c = light.color.to_linear();
        light.color = Color::LinearRgba(bevy::color::LinearRgba::rgb(
            c.red + (MOON_KEY_COLOR[0] - c.red) * mix,
            c.green + (MOON_KEY_COLOR[1] - c.green) * mix,
            c.blue + (MOON_KEY_COLOR[2] - c.blue) * mix,
        ));
    }
}

// ── LOOP D: KEY dourada do sol baixo ─────────────────────────────────────
//
// O `sun_drive` escreve a cor da direcional como `mix(MOON_COLOR, base.color,
// day)` — `base.color` é o valor AUTORADO (branco), não o `sun_tint` da
// atmosfera (que só pinta o DISCO no domo). Resultado medido pelo crítico da
// LOOP C às 07:49: "neutral-white overhead sun, warmth on one roof only" —
// TODA a superfície sunlit recebia a mesma luz branca; o aquecimento vivia
// só no grading (global, sombras incluídas) e num highlight tint meia-força.
//
// A key dourada aquece a LUZ DIRECCIONAL na janela do sol baixo — o mesmo
// lever físico da key da lua (LOOP B), no sentido oposto: sol rasante =
// caminho atmosférico longo = âmbar. Assim o aquecimento entra pela
// ILUMINAÇÃO (todas as superfícies viradas para o sol, cada uma com a sua
// albedo), o céu continua AZUL (fill frio do próprio domo) e a sombra fica
// para o split-tone arrefecer — quente/frio por CONSTRUÇÃO, não por tint.

/// Cor linear da key dourada — âmbar de sol rasante, **normalizada à
/// luminância** (luma = 1): misturar branco autoral para aqui NÃO escurece
/// a cena (a primeira tentativa com (1.0, 0.56, 0.22) cru — luma 0,61 —
/// cortava ~23% da luz do sol e o medidor do AutoExposure amplificava o
/// corte; medido 2026-09-13, golden 07:30: média do frame 0,62 → 0,47).
/// R > 1 em linear é HDR legítimo — é o que preserva a exposição do dia.
pub const GOLDEN_KEY_COLOR: [f32; 3] = [1.593, 0.889, 0.352];
/// Profundidade do degrau de cor da key dourada na janela cheia (0 = fica
/// a cor do `sun_drive`, 1 = [`GOLDEN_KEY_COLOR`] cheio). 0.40 mantém o
/// dourado na LUZ (reboco sunlit quente) sem virar lavagem laranja global —
/// 0.62 pintava o frame INTEIRO de laranja-monocromo (crítica r1-D: "filtro
/// sépia a 100%", bilhete regressou a "outra liga"); 0.40 preserva o albedo
/// (céu azul, relva verde, sombras frias) com a key ainda a ler-se dourada.
pub const GOLDEN_KEY_MIX: f32 = 0.40;

/// Fração do degrau de cor da key dourada pela elevação do sol e pela noite
/// (fn pura testável): [`GOLDEN_KEY_MIX`] × [`low_sun_split_weight`] na
/// janela, × (1 − night) para o crepúsculo nunca empilhar a key âmbar com a
/// key da lua (a lua só acende com elevação < −1°, mas o guard barato
/// fecha a porta a qualquer sobreposição).
pub fn golden_key_mix(elevation_deg: f32, night: f32) -> f32 {
    if !elevation_deg.is_finite() || !night.is_finite() || elevation_deg <= 0.0 {
        return 0.0;
    }
    GOLDEN_KEY_MIX * low_sun_split_weight(elevation_deg) * (1.0 - night.clamp(0.0, 1.0))
}

/// KEY dourada: aquece a COR da luz direcional na janela do sol baixo (ver
/// secção LOOP D acima). Corre DEPOIS do `sun_drive` (que reescreve a cor a
/// partir do `SunLightBase` a cada frame — a composição nunca acumula) e
/// depois da `moon_key_drive`; é no-op total à noite (elevação ≤ 0 ou
/// `night` = 1) e ao meio-dia (elevação ≥ [`SPLIT_TONE_LOW_SUN_ZERO_DEG`]),
/// portanto o look aprovado da noite LOOP C e do dia neutro não mexe.
fn golden_key_drive(
    sun: Res<crate::worldsys::SunState>,
    mut lights: Query<(&mut DirectionalLight, &SunLightBase)>,
) {
    let mix = golden_key_mix(sun.elevation_deg, sun.night);
    if mix <= 0.0 {
        return;
    }
    for (mut light, _base) in &mut lights {
        let c = light.color.to_linear();
        light.color = Color::LinearRgba(bevy::color::LinearRgba::rgb(
            c.red + (GOLDEN_KEY_COLOR[0] - c.red) * mix,
            c.green + (GOLDEN_KEY_COLOR[1] - c.green) * mix,
            c.blue + (GOLDEN_KEY_COLOR[2] - c.blue) * mix,
        ));
    }
}

// ── LOOP D: POÇAS de tocha — ganho NOCTURNO sobre as PointLights quentes ──
//
// O crítico da LOOP C: "tiny pools". O raio visível de uma poça cresce com
// a RAIZ CÚBICA da intensidade (falloff 1/d³ no chão: I·h/d³), portanto
// poças 2× pedem luz ×8. Fazer isso no XML (1M lm por tocha) EMPURRAVA O
// MEDIDOR do AutoExposure de DIA — os núcleos das tochas subiam a
// brilho-de-sol no histograma e o servo fechava a golden hour ~1,2 EV
// (medido 2026-09-13: média do frame 0,62 → 0,20; bissectado com o XML a
// 120k → 0,47). A solução é o MESMO padrão da key da lua: a base autoral é
// capturada UMA vez ([`TorchLightBase`]) e um drive compõe por frame —
// de dia `night` = 0 ⇒ ganho 1 ⇒ o dia aprovado fica EXACTAMENTE como
// estava; à noite as luzes QUENTES sobem ×[`TORCH_POOL_NIGHT_GAIN`].
//
// O gate é COLORIMÉTRICO (R > B linear): apanha tochas/braseiros/janelas
// (0xffa040, 0xffa83a, 0xffb264…) e poupa os acentos frios autorados
// (o cristal 0x3f8fff do posto). O teto [`TORCH_POOL_NIGHT_MAX_LUMEN`]
// guarda a NOITE de lavar (o medidor está preso no lift máximo: luz a mais
// clareia o display em vez de o servo absorver). Nada mais escreve
// `PointLight::intensity` no runtime (o orçamento de sombras do ambient.rs
// só toca `shadow_maps_enabled`/Visibility).

/// Ganho de intensidade das TOCHAS (PointLights quentes COM sombra autoral)
/// na noite plena (LOOP D).
///
/// **8×** às 120k lm das tochas de rua da LOOP C: raio da poça ×2 (6 m →
/// ~12 m, ainda sob o `range` default de 20 m do Bevy — o clamp não come a
/// saia). O filtro ND da noite (−7,4 EV) dá folga: mesmo os miolos a
/// brilho-de-sol ficam no ombro do TonyMcMapface (~0,7 display), não
/// clipam. A família é discriminada pelo marcador `AuthoredShadowLight`
/// (os postes de tocha do simple-rpg autoram `shadows="true"`; o marcador
/// não é removido quando o orçamento desliga a sombra).
pub const TORCH_POOL_NIGHT_GAIN: f32 = 8.0;

/// Ganho das DEMAIS luzes quentes (janelas/braseiros/faróis, sombra off) —
/// a luz ambiente quente da vila sobe um pouco (×2), sem competir com as
/// poças. A variante XML-1M validada usava EXACTAMENTE estes rácios
/// (tochas ×8, janelas ×2) e media: 11 poças quentes, estrelas visíveis,
/// noite ainda a ler noite.
pub const WARM_FILL_NIGHT_GAIN: f32 = 2.0;

/// Intensidade autoral de uma PointLight quente, capturada antes de o
/// [`torch_pool_drive`] começar a compor (o espelho de `SunLightBase`).
#[derive(Debug, Clone, Copy, Component)]
pub struct TorchLightBase {
    pub intensity: f32,
}

/// Multiplicador de uma tocha (sombra autoral) pela fração de noite: 1 de
/// dia (o valor authored fica EXACTAMENTE como estava),
/// [`TORCH_POOL_NIGHT_GAIN`] na noite plena, rampa linear no meio.
pub fn torch_pool_gain(night: f32) -> f32 {
    night_gain(TORCH_POOL_NIGHT_GAIN, night)
}

/// Multiplicador de uma luz quente SEM sombra autoral (janelas/braseiros):
/// [`WARM_FILL_NIGHT_GAIN`] na noite plena — ver const.
pub fn warm_fill_gain(night: f32) -> f32 {
    night_gain(WARM_FILL_NIGHT_GAIN, night)
}

fn night_gain(full: f32, night: f32) -> f32 {
    if !night.is_finite() {
        return 1.0;
    }
    1.0 + (full - 1.0) * night.clamp(0.0, 1.0)
}

/// Intensidade nocturna composta: `base × gain` — fn pura testável (o gain
/// já vem da família certa; ver os dois consts).
pub fn torch_pool_target(base: f32, gain: f32) -> f32 {
    if !base.is_finite() || base <= 0.0 || !gain.is_finite() || gain <= 0.0 {
        return base;
    }
    base * gain
}

/// Uma PointLight é "quente" (família tocha/braseiro/janela) pelo seu canal
/// linear: R acima de B pelo limiar. Fn pura testável.
pub fn torch_pool_is_warm(color: bevy::color::LinearRgba) -> bool {
    color.red.is_finite() && color.blue.is_finite() && color.red > color.blue + 0.05
}

/// Composição por frame do ganho nocturno das poças: captura a base na
/// primeira observação e escreve `base × torch_pool_gain(night)` — nunca
/// acumula (a base nunca é alterada). Corre no `Update`, depois da
/// `atmosphere_drive` (fonte da fração `night`).
fn torch_pool_drive(
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    mut commands: Commands,
    mut lights: Query<(
        Entity,
        &mut PointLight,
        Option<&TorchLightBase>,
        Option<&crate::ambient::AuthoredShadowLight>,
    )>,
) {
    let torch = torch_pool_gain(atmosphere.night);
    let fill = warm_fill_gain(atmosphere.night);
    if (torch - 1.0).abs() < 1e-4 && (fill - 1.0).abs() < 1e-4 {
        return;
    }
    for (entity, mut light, base, torch_authored) in &mut lights {
        if !torch_pool_is_warm(light.color.to_linear()) {
            continue;
        }
        let gain = if torch_authored.is_some() {
            torch
        } else {
            fill
        };
        match base {
            Some(base) => {
                let target = torch_pool_target(base.intensity, gain);
                if (light.intensity - target).abs() > base.intensity * 1e-3 {
                    light.intensity = target;
                }
            }
            None => {
                commands.entity(entity).insert(TorchLightBase {
                    intensity: light.intensity,
                });
            }
        }
    }
}

/// Extremo FRIO do split-tone na hora baixa de sol (sombras) — multiplicador
/// linear, normalizado à luminância no shader (não escurece: só matiz).
pub const SPLIT_TONE_GOLDEN_SHADOW: [f32; 3] = [0.78, 0.92, 1.22];
/// Extremo QUENTE do split-tone na hora baixa de sol (highlights).
///
/// **LOOP D** endureceu o braço quente ([1.18, 1.02, 0.80] → [1.24, 1.00,
/// 0.72]): o crítico mediu "warmth on one roof only" — o par antigo com peso
/// máximo 0.85 mal empurrava R−B para +0.03 em reboco sunlit a 07:49. Com a
/// janela alargada (ver [`SPLIT_TONE_LOW_SUN_FULL_DEG`]) o highlight tint mais
/// saturado é o que faz o par QUENTE/FRIO ler num relance: fachada ao sol
/// dourada, sombra azul por baixo.
pub const SPLIT_TONE_GOLDEN_HIGHLIGHT: [f32; 3] = [1.24, 1.00, 0.72];
/// Extremo FRIO do split-tone na noite (sombras — o azul mais fundo).
pub const SPLIT_TONE_NIGHT_SHADOW: [f32; 3] = [0.62, 0.82, 1.38];
/// Extremo do split-tone na noite para highlights: quente PRONUNCIADO
/// (LOOP D; era [1.12, 1.0, 0.86]) — as poças/braseiros são fontes 0xffa83a
/// mas o crítico media ~90% do frame num ÚNICO navy: o tint antigo mal
/// segurava o fim do falloff quente. [1.18, 0.98, 0.78] com o bloom nocturno
/// da LOOP C faz a POÇA inteira (não só a chama) ler laranja contra o chão
/// azul — o eixo quente/frio que o crítico pediu.
pub const SPLIT_TONE_NIGHT_HIGHLIGHT: [f32; 3] = [1.18, 0.98, 0.78];

/// Peso máximo do split-tone na hora baixa de sol (a LOOP A aprovou a golden
/// como evento QUENTE — o split entra a 85% para não a arrefecer).
pub const SPLIT_TONE_GOLDEN_WEIGHT: f32 = 0.85;
/// Peso máximo do split-tone na noite.
pub const SPLIT_TONE_NIGHT_WEIGHT: f32 = 1.0;

/// Elevação do sol abaixo da qual o split-tone quente/frio pesa MÁXIMO.
///
/// **Porquê ler a ELEVAÇÃO e não o `golden` da atmosfera** (medido
/// 2026-09-13): a gaussiana do `golden` é estreita — pico a 4°, σ = 8° —
/// pelo que no cenário de golden hour do gauntlet (07:30, sol já a 26,9°)
/// dá `golden ≈ 0,0003`: o split-tone era IDENTIDADE na própria cena que o
/// crítico aponta, e a "separação de temperatura" não existia em lado nenhum
/// fora da janela 05:40–06:15. A janela do SPLIT tem de cobrir a MANHÃ/TARDE
/// baixas (o "golden event" do BOTW vive no sol rasante, não só nos 10 min
/// do nascer).
///
/// **25° (LOOP D; era 12°).** O crítico da LOOP C mediu o 07:49 (sol a 30,8°)
/// como "neutral-white overhead sun, warmth on one roof only" — a janela
/// 12°/45° dava peso 0.57 a 26,9° e ~0.45 a 30,8°, e a multiplicação por
/// `SPLIT_TONE_GOLDEN_WEIGHT` (0.85) comia-o a 0.48: metade do tinte nunca
/// chegava ao frame. Com 25° o 07:30 pesa ~0.98 e o 07:49 ~0.87; o
/// meio-dia (62°) continua em 0 (ver [`SPLIT_TONE_LOW_SUN_ZERO_DEG`]).
pub const SPLIT_TONE_LOW_SUN_FULL_DEG: f32 = 25.0;
/// Elevação acima da qual o split-tone desliga por completo (meio-dia
/// neutro, sem tinte — o crítico pede "midday stays neutral").
///
/// **50° (LOOP D; era 45°).** O sol do simple-rpg passa os 62° ao meio-dia;
/// 50° deixa a manhã alta (07:49 @30,8°) DENTRO da rampa e o meio-dia
/// inteiro FORA — o dia neutro aprovado não mexe (peso 0 acima de 50°).
pub const SPLIT_TONE_LOW_SUN_ZERO_DEG: f32 = 50.0;

/// Peso do braço "hora baixa de sol" do split-tone pela elevação REAL do sol
/// (`SunState.elevation_deg`): 1 a ≤[`SPLIT_TONE_LOW_SUN_FULL_DEG`], 0 a
/// ≥[`SPLIT_TONE_LOW_SUN_ZERO_DEG`], smoothstep no meio. 07:30 do simple-rpg
/// (26,9°) ≈ 0,98; 07:49 (30,8°) ≈ 0,87; meio-dia (62°) = 0; alvorada (5°) =
/// 1. Debaixo do horizonte mantém 1 — a transição para o braço da NOITE é a
/// rampa `night` (que já rampa suavemente com a elevação negativa).
pub fn low_sun_split_weight(elevation_deg: f32) -> f32 {
    if !elevation_deg.is_finite() {
        return 0.0;
    }
    let t = ((SPLIT_TONE_LOW_SUN_ZERO_DEG - elevation_deg)
        / (SPLIT_TONE_LOW_SUN_ZERO_DEG - SPLIT_TONE_LOW_SUN_FULL_DEG))
        .clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Limiar inferior (luminância linear pós-tonemap) da banda de transição
/// sombras→highlights do split-tone. Ponto médio do grey 18% linear.
pub const SPLIT_TONE_LO: f32 = 0.045;
/// Limiar superior da banda — highlights "de verdade" (céu ao luar, poças,
/// fachadas ao sol rasante) ficam acima.
pub const SPLIT_TONE_HI: f32 = 0.30;

/// Resolve os tints do split-tone para a hora corrente (fn pura testável).
///
/// `low_sun` é o peso do braço de hora baixa ([`low_sun_split_weight`], pela
/// elevação REAL do sol — ver medição no const). Composição em cadeia a
/// partir do IDENTIDADE (1,1,1): primeiro a hora baixa, depois a noite por
/// cima — são regimes distintos do dia (o braço de hora baixa morre quando o
/// sol sobe além de 45°, o `night` só acende depois do horizonte), o
/// crepúsculo faz a passagem pelos dois com pesos parciais e SEM salto.
pub fn split_tone_for(low_sun: f32, night: f32) -> ([f32; 3], [f32; 3]) {
    // `f32::clamp` propaga NaN — sanitizar à mão (padrão do módulo).
    let frac = |v: f32| {
        if v.is_finite() {
            v.clamp(0.0, 1.0)
        } else {
            0.0
        }
    };
    let g = (frac(low_sun) * SPLIT_TONE_GOLDEN_WEIGHT).clamp(0.0, 1.0);
    let n = (frac(night) * SPLIT_TONE_NIGHT_WEIGHT).clamp(0.0, 1.0);
    let lerp3 = |a: [f32; 3], b: [f32; 3], t: f32| {
        [
            a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t,
        ]
    };
    let identity = [1.0, 1.0, 1.0];
    let shadow = lerp3(
        lerp3(identity, SPLIT_TONE_GOLDEN_SHADOW, g),
        SPLIT_TONE_NIGHT_SHADOW,
        n,
    );
    let highlight = lerp3(
        lerp3(identity, SPLIT_TONE_GOLDEN_HIGHLIGHT, g),
        SPLIT_TONE_NIGHT_HIGHLIGHT,
        n,
    );
    (shadow, highlight)
}

/// Uniform do passe de split-tone (uma por câmara, extraído por
/// [`ExtractComponent`]). O WGSL declara o struct ESPELHADO (mesmos
/// offsets do encase: vec3×2 + 3×f32).
#[derive(Component, Clone, Copy, PartialEq, ExtractComponent, ShaderType, Default)]
pub struct SplitToneSettings {
    /// Multiplicador linear do extremo das sombras.
    pub shadow_tint: Vec3,
    /// Multiplicador linear do extremo dos highlights.
    pub highlight_tint: Vec3,
    /// Banda da transição (luminância linear pós-tonemap).
    pub split_lo: f32,
    pub split_hi: f32,
}

/// Handle do shader inline — criado no `PostFxPlugin::build` (antes de
/// qualquer sistema de render); `fragment_shader()` é uma fn ESTÁTICA e
/// não pode ler o World, daí o OnceLock.
static SPLIT_TONE_SHADER: OnceLock<Handle<Shader>> = OnceLock::new();

/// WGSL do split-tone: um triângulo fullscreen PÓS-tonemap (depois do
/// `tonemapping` e do ColorGrading da LOOP A — lê valores finais de
/// exibição, portanto os limiares da banda são ESTÁVEIS face à
/// exposição automática). A multiplicação é NORMALIZADA à luminância
/// (`tint / dot(tint, LUMA)`): o passe muda MATIZ, nunca exposição — o
/// medidor e o filtro ND da LOOP A ficam intactos por construção.
const SPLIT_TONE_WGSL: &str = r#"
// O vértice vem do `FullscreenShader` embutido do plugin (triângulo
// fullscreen); o QUE importa aqui é o STRUCT do seu output — no bevy 0.19
// chama-se `FullscreenVertexOutput` (importar `fullscreen_vertex_out`, como
// nos exemplos antigos, deixa o identificador fora de scope e o naga REJEITA
// o shader: o passe salta em silêncio e o split-tone não existe no frame).
#import bevy_core_pipeline::fullscreen_vertex_shader::FullscreenVertexOutput

struct SplitToneSettings {
    shadow_tint: vec3<f32>,
    highlight_tint: vec3<f32>,
    split_lo: f32,
    split_hi: f32,
};

@group(0) @binding(0) var source: texture_2d<f32>;
@group(0) @binding(1) var source_sampler: sampler;
@group(0) @binding(2) var<uniform> settings: SplitToneSettings;

const LUMA = vec3<f32>(0.2126, 0.7152, 0.0722);

@fragment
fn fragment(in: FullscreenVertexOutput) -> @location(0) vec4<f32> {
    let color = textureSample(source, source_sampler, in.uv).rgb;
    let luma = dot(color, LUMA);
    let w = smoothstep(settings.split_lo, settings.split_hi, luma);
    let tint = mix(settings.shadow_tint, settings.highlight_tint, w);
    // Normalização à luminância: o split-tone nunca clareia/escurece o
    // frame — só desloca a temperatura por FAIXA (sombras↔highlights).
    let norm = max(dot(tint, LUMA), 1e-4);
    return vec4<f32>(color * tint / norm, 1.0);
}
"#;

impl FullscreenMaterial for SplitToneSettings {
    fn fragment_shader() -> bevy::shader::ShaderRef {
        // O handle é criado no build do plugin, antes de qualquer render;
        // se algo correr catastroficamente errado, o handle default deixa
        // o pipeline não-inicializado (o passe salta em silêncio).
        bevy::shader::ShaderRef::Handle(SPLIT_TONE_SHADER.get().cloned().unwrap_or_default())
    }

    fn schedule_configs(
        system: bevy::ecs::schedule::ScheduleConfigs<bevy::ecs::system::BoxedSystem>,
    ) -> bevy::ecs::schedule::ScheduleConfigs<bevy::ecs::system::BoxedSystem> {
        use bevy::ecs::schedule::IntoScheduleConfigs as _;
        system
            .in_set(bevy::core_pipeline::Core3dSystems::PostProcess)
            // DEPOIS do tonemap + ColorGrading: valores de exibição, com
            // o filtro ND da LOOP A já aplicado — a banda lo..hi é estável.
            .after(tonemapping_pass)
    }
}

/// Conduz o split-tone pela hora: o braço de hora baixa lê a ELEVAÇÃO REAL
/// do sol ([`SunState`] — a gaussiana `golden` da atmosfera é estreita demais
/// e dá 0 no próprio cenário 07:30 do gauntlet; ver [`low_sun_split_weight`]),
/// o braço de noite lê a fração [`AtmosphereState::night`] (como a LOOP A).
/// Meio-dia (sol alto, night=0): tints no IDENTIDADE — o passe multiplica
/// por 1 e o dia neutro aprovado fica EXACTAMENTE como estava.
fn drive_split_tone(
    sun: Res<crate::worldsys::SunState>,
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    mut cameras: Query<&mut SplitToneSettings, With<Camera3d>>,
) {
    let (shadow, highlight) =
        split_tone_for(low_sun_split_weight(sun.elevation_deg), atmosphere.night);
    for mut settings in &mut cameras {
        let next = SplitToneSettings {
            shadow_tint: Vec3::from(shadow),
            highlight_tint: Vec3::from(highlight),
            split_lo: SPLIT_TONE_LO,
            split_hi: SPLIT_TONE_HI,
        };
        if *settings != next {
            *settings = next;
        }
    }
}

// ── LOOP C: perspetiva aérea — desat + blue-shift por PROFUNDIDADE ──────
//
// O crítico (golden ticket, 2026-09-13): "BotW grades distance via
// desaturation + blue shift (aerial perspective). Ours bleaches to white
// fog — mountains read as a haze bug, not depth." A `DistanceFog` e a sua
// cor vivem no `ambient.rs` (interdito neste lote), mas o GRADE final do
// píxel é da lente: um passe fullscreen que lê a profundidade do prepass e,
// no longe, mistura a cor para `luminância × tint cinza-azul` com um leve
// escurecer de contraste — a serra vira CAMADA azulada por baixo de um céu
// mais claro (a silhueta que lê profundidade), em vez de massa branca.
//
// Arquitectura igual ao `water_ssr` (triângulo fullscreen + `post_process_write`),
// mas ANTES do TAA e do tonemap: trabalha em HDR linear (o desat linear é
// fisicamente correcto) e o TAA acumula os gradientes da rampa (sem shimmer
// nas silhuetas). O céu NÃO é tocado: o domo opaco (~850 m) oculta todo o
// terreno além dele, portanto `depth ≥ AERIAL_SKY_SKIP_M` = domo = passthrough.

/// Início da rampa (m): mais perto disto o passe é identidade — a
/// legibilidade de jogo (combate, leitura de props) não paga a atmosfera.
pub const AERIAL_START_M: f32 = 140.0;
/// Fim da rampa (m): fator máximo do grade.
pub const AERIAL_FULL_M: f32 = 620.0;
/// Cota (m) a partir da qual o píxel é tratado como DOMO do céu e fica
/// intocado: o domo está a ~850 m e é opaco (o terreno além dele nunca
/// aparece), pelo que 790 separa "última serra" de "céu" sem tocar no
/// gradiente aprovado do domo.
pub const AERIAL_SKY_SKIP_M: f32 = 790.0;
/// Peso do passe na noite plena: o fog noturno JÁ é azul profundo (LOOP A) —
/// dessaturar por cima a noite inteira lavaria a assinatura aprovada.
pub const AERIAL_NIGHT_WEIGHT: f32 = 0.30;
/// Tint do haze (linear): cinza-azul, a matiz BotW. A luminância vem do
/// próprio píxel (`luma × tint`), portanto o haze não clareia — só muda de
/// cor e mata a saturação.
pub const AERIAL_TINT: [f32; 3] = [0.84, 0.92, 1.12];
/// Escurecer máximo do haze (fração): o longe desce abaixo do céu → a
/// silhueta da serra LÊ contra o horizonte (contraste em vez de bleach).
pub const AERIAL_CONTRAST_DARKEN: f32 = 0.16;
/// Teto do mix: mantém 15% da cor original — as serras dessaturam, não
/// viram monocromo.
pub const AERIAL_MAX_MIX: f32 = 0.85;

/// Peso global do passe pela fração de noite: 1 de dia (a golden 07:30 do
/// gauntlet tem `night` = 0 → peso CHEIO, é o cenário do crítico),
/// [`AERIAL_NIGHT_WEIGHT`] na noite plena, rampa linear no meio.
pub fn aerial_weight(night: f32) -> f32 {
    if !night.is_finite() {
        return 1.0;
    }
    1.0 - (1.0 - AERIAL_NIGHT_WEIGHT) * night.clamp(0.0, 1.0)
}

/// Fator do grade por distância (fn pura espelho do WGSL): 0 abaixo de
/// [`AERIAL_START_M`], smoothstep até [`AERIAL_FULL_M`], 0 no céu
/// (dist ≥ [`AERIAL_SKY_SKIP_M`]). O resultado já inclui o `weight` e o
/// teto [`AERIAL_MAX_MIX`].
pub fn aerial_factor(dist_m: f32, weight: f32) -> f32 {
    if !dist_m.is_finite() || dist_m >= AERIAL_SKY_SKIP_M || weight <= 0.0 {
        return 0.0;
    }
    let t = ((dist_m - AERIAL_START_M) / (AERIAL_FULL_M - AERIAL_START_M)).clamp(0.0, 1.0);
    let s = t * t * (3.0 - 2.0 * t);
    (s * weight.clamp(0.0, 1.0)).min(1.0) * AERIAL_MAX_MIX
}

/// Espelho CPU do grade do WGSL (para testes pixel a pixel): dessat +
/// blue-shift + escurecer de contraste para um fator `f` de
/// [`aerial_factor`].
pub fn aerial_grade(color: [f32; 3], f: f32) -> [f32; 3] {
    const LUMA: [f32; 3] = [0.2126, 0.7152, 0.0722];
    let luma = color[0] * LUMA[0] + color[1] * LUMA[1] + color[2] * LUMA[2];
    let mix_t = f.clamp(0.0, 1.0);
    let dark = 1.0 - AERIAL_CONTRAST_DARKEN * mix_t;
    let graded = |c: usize| {
        let haze = luma * AERIAL_TINT[c];
        (color[c] + (haze - color[c]) * mix_t) * dark
    };
    [graded(0), graded(1), graded(2)]
}

/// Liga o passe de perspetiva aérea numa câmara (default-on; inserido pelo
/// `attach_postfx_to_cameras` junto do resto da lente).
#[derive(Component, Clone, Copy, Default, ExtractComponent)]
pub struct AerialPerspective;

/// Handle do shader inline — criado no `PostFxPlugin::build` (mesmo padrão
/// do split-tone; `init_aerial_pipeline` corre no render app).
static AERIAL_SHADER: OnceLock<Handle<Shader>> = OnceLock::new();

/// WGSL do passe — self-contained (zero `#import`, validável pelo harness
/// naga como o water_ssr). Uniform: 2 mat4 + 3 vec4 = 176 B (o packing CPU
/// `pack_aerial_uniform` tem de medir EXACTAMENTE isto).
pub const AERIAL_WGSL: &str = r#"
// Perspetiva aérea (LOOP C): desat + blue-shift por profundidade.
struct AerialUniform {
    inv_clip_from_view: mat4x4<f32>,
    world_from_view:    mat4x4<f32>,
    cam_pos: vec4<f32>,   // xyz = câmara no mundo
    ramp: vec4<f32>,      // start_m, full_m, sky_skip_m, weight
    tint: vec4<f32>,      // rgb = tint, a = contrast_darken
};

@group(0) @binding(0) var input_color: texture_2d<f32>;
@group(0) @binding(1) var samp: sampler;
@group(0) @binding(2) var prepass_depth: texture_depth_2d;
@group(0) @binding(3) var<uniform> aer: AerialUniform;

struct FullscreenVertexOutput {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

const LUMA = vec3<f32>(0.2126, 0.7152, 0.0722);
const MAX_MIX = 0.85;

@fragment
fn fragment(in: FullscreenVertexOutput) -> @location(0) vec4<f32> {
    var color = textureSample(input_color, samp, in.uv).rgb;
    if (aer.ramp.w <= 0.001) {
        return vec4<f32>(color, 1.0);
    }
    // reverse-Z: clear do depth = 0 (céu sem geometria) — intocado.
    let d = textureLoad(prepass_depth, vec2<i32>(floor(in.position.xy)), 0);
    if (d <= 0.0) {
        return vec4<f32>(color, 1.0);
    }
    let ndc = vec4<f32>(in.uv.x * 2.0 - 1.0, 1.0 - 2.0 * in.uv.y, d, 1.0);
    let v = aer.inv_clip_from_view * ndc;
    let world = (aer.world_from_view * vec4<f32>(v.xyz / v.w, 1.0)).xyz;
    let dist = distance(world, aer.cam_pos.xyz);
    // Domo do céu (~850 m, opaco): além disto é céu — o gradiente aprovado
    // não é tocado pelo passe.
    if (dist >= aer.ramp.z) {
        return vec4<f32>(color, 1.0);
    }
    let t = clamp((dist - aer.ramp.x) / max(aer.ramp.y - aer.ramp.x, 1.0), 0.0, 1.0);
    let s = t * t * (3.0 - 2.0 * t) * clamp(aer.ramp.w, 0.0, 1.0);
    let f = min(s, 1.0) * MAX_MIX;
    // O haze é MONOCROMO ao tint à luminância do píxel: dessatura e desvia
    // para azul-cinza sem clarear (a névoa branca de bug morre aqui).
    let luma = dot(color, LUMA);
    let haze = luma * aer.tint.rgb;
    let mixed = mix(color, haze, f);
    // Contraste: o longe desce um pouco — silhueta contra o céu mais claro.
    let out_c = mixed * (1.0 - aer.tint.w * f);
    return vec4<f32>(out_c, 1.0);
}
"#;

/// Tamanho do uniform do passe (B) — mat4×2 + vec4×3; o packing CPU e o
/// struct WGSL têm de bater EXACTAMENTE (guarda no harness naga).
pub const AERIAL_UNIFORM_BYTES: usize = 176;

#[derive(Resource, Default)]
struct ExtractedAerial {
    weight: f32,
}

fn extract_aerial(
    atmosphere: Extract<Res<crate::worldsys::AtmosphereState>>,
    mut out: ResMut<ExtractedAerial>,
) {
    out.weight = aerial_weight(atmosphere.night);
}

#[derive(Resource)]
struct AerialPipeline {
    layout: BindGroupLayoutDescriptor,
    sampler: Sampler,
    variants: Variants<RenderPipeline, AerialSpecializer>,
}

#[derive(PartialEq, Eq, Hash, Clone, Copy, SpecializerKey)]
struct AerialPipelineKey {
    target_format: TextureFormat,
}

struct AerialSpecializer;

impl Specializer<RenderPipeline> for AerialSpecializer {
    type Key = AerialPipelineKey;

    fn specialize(
        &self,
        key: Self::Key,
        descriptor: &mut RenderPipelineDescriptor,
    ) -> Result<Canonical<Self::Key>, bevy::ecs::error::BevyError> {
        let fragment = descriptor.fragment_mut()?;
        fragment.set_target(
            0,
            ColorTargetState {
                format: key.target_format,
                blend: None,
                write_mask: ColorWrites::ALL,
            },
        );
        Ok(key)
    }
}

fn init_aerial_pipeline(
    mut commands: Commands,
    render_device: Res<RenderDevice>,
    fullscreen_shader: Res<FullscreenShader>,
) {
    let entries = [
        BindGroupLayoutEntry {
            binding: 0,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Texture {
                sample_type: TextureSampleType::Float { filterable: true },
                view_dimension: TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 1,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Sampler(SamplerBindingType::Filtering),
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 2,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Texture {
                sample_type: TextureSampleType::Depth,
                view_dimension: TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 3,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Buffer {
                ty: BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: BufferSize::new(AERIAL_UNIFORM_BYTES as u64),
            },
            count: None,
        },
    ];
    let layout = BindGroupLayoutDescriptor::new("aerial_perspective_layout", &entries);
    let sampler = render_device.create_sampler(&SamplerDescriptor::default());
    let vertex_state = VertexState {
        shader: fullscreen_shader.shader(),
        shader_defs: Vec::new(),
        entry_point: Some("fullscreen_vertex_shader".into()),
        buffers: Vec::new(),
    };
    let desc = RenderPipelineDescriptor {
        label: Some("aerial_perspective_pipeline".into()),
        layout: vec![layout.clone()],
        vertex: vertex_state,
        fragment: Some(FragmentState {
            shader: AERIAL_SHADER.get().cloned().unwrap_or_default(),
            targets: vec![Some(ColorTargetState {
                format: TextureFormat::Rgba16Float,
                blend: None,
                write_mask: ColorWrites::ALL,
            })],
            ..Default::default()
        }),
        ..Default::default()
    };
    commands.insert_resource(AerialPipeline {
        layout,
        sampler,
        variants: Variants::new(AerialSpecializer, desc),
    });
}

/// Packing do uniform por view (176 B — ordem EXACTA do struct WGSL).
fn pack_aerial_uniform(
    clip_from_view: &bevy::math::Mat4,
    world_from_view: &bevy::math::Mat4,
    cam_pos: [f32; 3],
    weight: f32,
) -> Vec<u8> {
    let mut data = Vec::with_capacity(AERIAL_UNIFORM_BYTES);
    let mut push_mat = |m: &bevy::math::Mat4| {
        for f in m.to_cols_array() {
            data.extend_from_slice(&f.to_le_bytes());
        }
    };
    push_mat(&clip_from_view.inverse());
    push_mat(world_from_view);
    for f in [cam_pos[0], cam_pos[1], cam_pos[2], 0.0] {
        data.extend_from_slice(&f.to_le_bytes());
    }
    for f in [AERIAL_START_M, AERIAL_FULL_M, AERIAL_SKY_SKIP_M, weight] {
        data.extend_from_slice(&f.to_le_bytes());
    }
    for f in [
        AERIAL_TINT[0],
        AERIAL_TINT[1],
        AERIAL_TINT[2],
        AERIAL_CONTRAST_DARKEN,
    ] {
        data.extend_from_slice(&f.to_le_bytes());
    }
    debug_assert_eq!(data.len(), AERIAL_UNIFORM_BYTES);
    data
}

#[derive(Component)]
struct AerialViewGpu {
    buf: Buffer,
    a: (TextureViewId, BindGroup),
    b: (TextureViewId, BindGroup),
}

#[derive(Component)]
struct AerialPipelineId(CachedRenderPipelineId);

#[allow(clippy::type_complexity)]
fn prepare_aerial(
    mut commands: Commands,
    pipeline: Option<ResMut<AerialPipeline>>,
    pipeline_cache: Res<PipelineCache>,
    render_device: Res<RenderDevice>,
    queue: Res<RenderQueue>,
    aerial: Res<ExtractedAerial>,
    mut views: Query<
        (
            Entity,
            &ExtractedView,
            &ViewTarget,
            Option<&ViewPrepassTextures>,
            Option<&mut AerialViewGpu>,
        ),
        With<AerialPerspective>,
    >,
) {
    let Some(mut pipeline) = pipeline else {
        return;
    };
    let bind_group_layout = pipeline_cache.get_bind_group_layout(&pipeline.layout);
    for (entity, view, target, prepass, existing) in &mut views {
        let Some(prepass) = prepass else {
            continue;
        };
        let Some(depth) = prepass.depth.as_ref() else {
            continue;
        };
        let key = AerialPipelineKey {
            target_format: view.target_format,
        };
        let Ok(pid) = pipeline.variants.specialize(&pipeline_cache, key) else {
            continue;
        };
        let view_bytes = pack_aerial_uniform(
            &view.clip_from_view,
            &view.world_from_view.to_matrix(),
            view.world_from_view.translation().to_array(),
            aerial.weight,
        );
        let make = |texture: &TextureView, buf: &Buffer| {
            (
                texture.id(),
                render_device.create_bind_group(
                    "aerial_perspective_bind_group",
                    &bind_group_layout,
                    &BindGroupEntries::sequential((
                        texture,
                        &pipeline.sampler,
                        &depth.texture.default_view,
                        buf.as_entire_binding(),
                    )),
                ),
            )
        };
        if let Some(mut gpu) = existing {
            queue.write_buffer(&gpu.buf, 0, &view_bytes);
            let buf = gpu.buf.clone();
            // O ping-pong do ViewTarget troca a textura fonte entre frames:
            // recria o bind group quando o id não bate.
            if gpu.a.0 != target.main_texture_view().id() {
                gpu.a = make(&target.main_texture_view(), &buf);
            }
            if gpu.b.0 != target.main_texture_other_view().id() {
                gpu.b = make(&target.main_texture_other_view(), &buf);
            }
        } else {
            let buf = render_device.create_buffer(&BufferDescriptor {
                label: Some("aerial_perspective_uniform".into()),
                size: AERIAL_UNIFORM_BYTES as u64,
                usage: BufferUsages::COPY_DST | BufferUsages::UNIFORM,
                mapped_at_creation: false,
            });
            queue.write_buffer(&buf, 0, &view_bytes);
            let a = make(&target.main_texture_view(), &buf);
            let b = make(&target.main_texture_other_view(), &buf);
            commands
                .entity(entity)
                .insert((AerialPipelineId(pid), AerialViewGpu { buf, a, b }));
        }
    }
}

/// O passe corre DEPOIS do TAA (`VIBER_AERIAL_AFTER_TAA=1`)?
///
/// Default **não**: o passe é depth-aware e o prepass de profundidade é
/// renderizado COM o jitter do TAA, enquanto a cor que sai do TAA já está
/// estabilizada. Mascarar por essa depth DEPOIS do resolve devolve ao frame
/// exactamente o aliasing que o TAA tinha tirado: nas cristas contra o céu o
/// píxel alterna entre "serra" (hazed + 16% mais escuro) e "céu" (intocado)
/// ao ritmo do Halton — lê-se como VIBRAÇÃO na linha do horizonte. Antes do
/// TAA o haze entra na history e a rampa acumula limpa (que era a intenção
/// documentada do módulo desde o início).
///
/// O braço `=1` mantém-se para A/B com o histórico — e como rede se esta
/// stack repetir o sintoma que o `water_ssr` mediu a 2026-09-10 (imagem a
/// alternar gradientes cinza/castanho com um ping-pong pré-TAA).
fn aerial_after_taa() -> bool {
    matches!(
        std::env::var("VIBER_AERIAL_AFTER_TAA").as_deref(),
        Ok("1" | "true" | "on")
    )
}

/// Ordem do passe no `Core3d` — EXPLÍCITA nos dois braços.
///
/// Ordem declarada não é cosmética aqui: os passes do render graph do bevy
/// 0.19 são sistemas normais, o `RenderContext` é `Deferred` + `Res` (não
/// conflitua com nada) e portanto dois passes SEM relação de ordem podem
/// correr em paralelo. `ViewTarget::post_process_write` é um
/// `main_texture.fetch_xor(1)` GLOBAL: dois passes a fazer o XOR em paralelo
/// trocam source/destination um do outro e os command buffers ainda são
/// submetidos por ordem indefinida — frames alternados com o grade no buffer
/// errado (flashes/piscadelas). O `aerial_pass` nascia com `after(taa)` +
/// `before(tonemapping)` apenas, ou seja ambíguo contra o `water_ssr_pass`,
/// o `motion_blur`, o `bloom`, o DOF e o effect stack.
fn aerial_schedule_configs()
-> bevy::ecs::schedule::ScheduleConfigs<bevy::ecs::system::ScheduleSystem> {
    use bevy::ecs::schedule::IntoScheduleConfigs as _;
    if aerial_after_taa() {
        // Braço histórico: no MESMO set do `water_ssr_pass`, por isso a ordem
        // contra ele e contra o primeiro passe built-in (`motion_blur`, que
        // já está `before(bloom)`) tem de ser dita à mão.
        aerial_pass
            .in_set(Core3dSystems::PostProcess)
            .after(temporal_anti_alias)
            .before(crate::water_ssr::water_ssr_pass)
            .before(motion_blur)
            .before(tonemapping_pass)
            .into_configs()
    } else {
        // Braço default: `EarlyPostProcess` corre inteiro ANTES do
        // `PostProcess` na chain do `Core3d`, portanto ficar ANTES do TAA
        // ordena o passe contra TUDO o que vem depois (water SSR, motion
        // blur, bloom, DOF, effect stack, auto-exposure, tonemap) sem criar o
        // ciclo que o `before(temporal_anti_alias)` dava a partir do
        // `PostProcess`.
        aerial_pass
            .in_set(Core3dSystems::EarlyPostProcess)
            .before(temporal_anti_alias)
            .into_configs()
    }
}

/// O passe: triângulo fullscreen com `post_process_write` (padrão
/// `water_ssr`). DEPOIS do TAA e ANTES do tonemap — o grade corre em HDR
/// linear (o desat linear é o fisicamente correcto).
fn aerial_pass(
    view: ViewQuery<(&ViewTarget, &AerialViewGpu, &AerialPipelineId)>,
    pipeline_cache: Res<PipelineCache>,
    mut ctx: RenderContext,
) {
    let (view_target, view_gpu, pipeline_id) = view.into_inner();
    let Some(pipeline) = pipeline_cache.get_render_pipeline(pipeline_id.0) else {
        return;
    };
    let post_process = view_target.post_process_write();
    let source = post_process.source;
    let destination = post_process.destination;
    let (_, bind_group) = if view_gpu.a.0 == source.id() {
        &view_gpu.a
    } else {
        &view_gpu.b
    };
    let pass_descriptor = RenderPassDescriptor {
        label: Some("aerial_perspective_pass".into()),
        color_attachments: &[Some(RenderPassColorAttachment {
            view: destination,
            depth_slice: None,
            resolve_target: None,
            ops: Operations::default(),
        })],
        depth_stencil_attachment: None,
        timestamp_writes: None,
        occlusion_query_set: None,
        multiview_mask: None,
    };
    {
        let mut render_pass = ctx.command_encoder().begin_render_pass(&pass_descriptor);
        render_pass.set_pipeline(pipeline);
        render_pass.set_bind_group(0, bind_group, &[]);
        render_pass.draw(0..3, 0..1);
    }
}

/// Interpola exposição/bloom na direção do bioma onde o herói está.
#[allow(clippy::type_complexity)]
fn drive_postfx(
    time: Res<Time>,
    sun: Res<crate::worldsys::SunState>,
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    biomes: Option<Res<BiomeRegions>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut state: ResMut<PostFxState>,
    mut cameras: Query<
        (
            &mut Bloom,
            &mut Exposure,
            Option<&mut bevy::render::view::ColorGrading>,
            Option<&mut Vignette>,
        ),
        With<Camera3d>,
    >,
) {
    let mut exposure_mult = 1.0;
    let mut bloom = BASE_BLOOM;
    if let (Some(biomes), Ok(player)) = (biomes.as_deref(), players.single()) {
        let pos = player.translation();
        let region = biomes
            .list
            .iter()
            .find(|b| point_in_polygon(pos.x, pos.z, &b.polygon));
        if let Some(region) = region {
            exposure_mult = region.pp_exposure.unwrap_or(1.0);
            bloom = region.pp_bloom_strength.unwrap_or(BASE_BLOOM);
        }
    }
    // A HORA manda por cima do bioma: a noite tem de ser mesmo mais escura
    // (senão é "o dia com o brilho baixado") e o bloom sobe onde há fontes
    // quentes — fogueiras à noite, glare do sol rasante. O reforço nocturno
    // ([`NIGHT_BLOOM_INTENSITY`]) é o LOOP C: halo nas chamas/poças.
    state.target_ev100 = ev100_for_exposure_multiplier(
        BASE_EV100,
        exposure_mult * atmosphere.exposure_scale.max(0.05),
    );
    let night = atmosphere.night.clamp(0.0, 1.0);
    state.target_bloom =
        (bloom + atmosphere.bloom_boost + NIGHT_BLOOM_INTENSITY * night).clamp(0.0, MAX_BLOOM);

    let t = (time.delta_secs() * BLEND_RATE).clamp(0.0, 1.0);
    // O kick de juice decai SEMPRE (mesmo com o alvo de bioma atingido) —
    // é um flash transitório, não um estado.
    if state.kick > 0.0 {
        state.kick = decay_kick(state.kick, time.delta_secs());
    }
    // Early-out quando o alvo já foi atingido: sem isto a escrita por frame
    // mantinha `Bloom`/`Exposure` em `Changed` para sempre (mundo estático
    // re-disparava dependências desnecessariamente).
    if (state.target_ev100 - state.ev100).abs() > 1e-4 {
        state.ev100 += (state.target_ev100 - state.ev100) * t;
    }
    if (state.target_bloom - state.bloom).abs() > 1e-4 {
        state.bloom += (state.target_bloom - state.bloom) * t;
    }
    let (ev100, bloom) = (ev_with_kick(state.ev100, state.kick), state.bloom);
    // LOOP C: o joelho do prefilter segue a noite — ver [`NIGHT_BLOOM_THRESHOLD`].
    let threshold = night_bloom_threshold(night);
    let lf_boost = night_bloom_lf_boost(night);
    // Grading POR HORA (r3): a golden hour aquece e satura, a noite dessatura
    // e arrefece — o "film stock" muda com o dia. ASC CDL compõe com o
    // TonyMcMapface (aplicado pré-tonemap). O grading por BIOMA precisa de
    // attrs no parser (frente fria do worldsys/recipes) — fica para seguir.
    let golden = atmosphere.golden;
    // LOOP D: o braço de sol baixo lê a ELEVAÇÃO REAL (a gaussiana `golden`
    // é ~0 às 07:30) — aquecimento global na manhã/tarde baixas.
    let low_sun = low_sun_split_weight(sun.elevation_deg);
    let temperature = grade_temperature_full(golden, low_sun, night);
    let saturation = grade_saturation(golden, night);
    for (mut camera_bloom, mut exposure, grading, vignette) in &mut cameras {
        if camera_bloom.intensity != bloom {
            camera_bloom.intensity = bloom;
        }
        if camera_bloom.prefilter.threshold != threshold {
            camera_bloom.prefilter.threshold = threshold;
        }
        if (camera_bloom.low_frequency_boost - lf_boost).abs() > 1e-4 {
            camera_bloom.low_frequency_boost = lf_boost;
        }
        // Vinheta nocturna (LOOP C): grade ESPACIAL — periferia para baixo,
        // centro (herói/rua/poças) intacto. Ver [`NIGHT_VIGNETTE`].
        if let Some(mut vig) = vignette {
            let target = 0.30 + (NIGHT_VIGNETTE - 0.30) * night;
            if (vig.intensity - target).abs() > 1e-4 {
                vig.intensity = target;
            }
        }
        if exposure.ev100 != ev100 {
            exposure.ev100 = ev100;
        }
        // A câmara pode não ter ColorGrading (inserido pelo attach com o
        // resto do pós; defensivo se alguém a spawnar à mão).
        if let Some(mut grading) = grading {
            let g = &mut grading.global;
            // Filtro ND da noite (ver [`grade_night_exposure_offset`]): o
            // auto-exposure SOMA a sua compensação a este campo no passe de
            // render — o desvio entra como base estável.
            let night_offset = grade_night_exposure_offset(night);
            if g.exposure != night_offset {
                g.exposure = night_offset;
            }
            if g.temperature != temperature {
                g.temperature = temperature;
            }
            if g.post_saturation != saturation {
                g.post_saturation = saturation;
            }
            // Sombras levemente levantadas à noite (o toe do TonyMcMapface
            // já ajuda; o CDL acaba de dar).
            let lift = night * 0.06;
            for section in grading.all_sections_mut() {
                if section.lift != lift {
                    section.lift = lift;
                }
            }
        }
    }
}

/// Persegue a distância câmara↔herói com o foco do [`DepthOfField`] — a
/// câmara de terceira pessoa orbita o herói, portanto ELE é o foco da lente
/// e o mundo desfoca atrás (bokeh). Interpolada a 6/s para o zoom não saltar.
#[allow(clippy::type_complexity)]
fn drive_dof_focus(
    time: Res<Time>,
    players: Query<&GlobalTransform, With<Player>>,
    mut cameras: Query<(&GlobalTransform, &mut DepthOfField), With<Camera3d>>,
    mut focus: Local<Option<f32>>,
) {
    let Ok(player) = players.single() else {
        return;
    };
    let player_pos = player.translation();
    let t = (time.delta_secs() * 6.0).clamp(0.0, 1.0);
    for (camera_tf, mut dof) in &mut cameras {
        let target = camera_tf.translation().distance(player_pos).max(0.5);
        let next = match *focus {
            Some(prev) => prev + (target - prev) * t,
            None => target,
        };
        *focus = Some(next);
        if (dof.focal_distance - next).abs() > 1e-3 {
            dof.focal_distance = next;
        }
    }
}

/// Densidade BASE do volume de névoa (`FogVolume::density_factor`).
///
/// É a ÚNICA fonte do valor: o `drive_fog_texture` reescreve o campo em TODOS
/// os frames (hora/vento/pântano), portanto o que o spawn põe aqui só dura até
/// ao primeiro Update — com o número duplicado, baixar a densidade no spawn
/// não mudava nada (auditoria 2026-09-10: o driver fixava 0.10 e o "dia
/// leitoso" continuava).
const FOG_VOLUME_DENSITY: f32 = 0.07;

/// Bónus de densidade do bioma pântano (id contém "swamp") — a bruma que o
/// mundo pede ali; o resto do mundo usa a base.
const FOG_VOLUME_SWAMP_BONUS: f32 = 0.10;

/// Fração da densidade do volume que sobrevive à NOITE (o resto escala com o
/// dia). Ver [`drive_fog_texture`]: o volume existe para os god-rays, que são
/// um efeito de SOL — à noite o `light_attenuation` do bevy
/// (`exp(−densidade × raio do AABB × (abs+scat))`, ≈ 0 com estes números)
/// apaga a contribuição da luz (sol E lanternas) e o que sobra é um véu que
/// encobre a distância sem dar nada em troca. **0 = desligado à noite**; medido
/// no `qa-raster` à chuva: a densidade a 1/5 ainda comia 1/3 do contraste que
/// a cena ganha sem volume nenhum.
/// `VIBER_NO_VOLUMETRICS=1` desliga-o em absoluto (A/B).
const FOG_VOLUME_NIGHT_ATTENUATION: f32 = 0.0;

/// Spawn do volume de névoa volumétrica (god-rays): um cubo 900×600×900 m
/// com TEXTURA 3D de densidade FBM, raymarched contra a depth — o
/// [`bevy::light::VolumetricLight`] do sol (spawn.rs) acende-o quando a luz
/// atravessa geometria (copas, portas, desfiladeiros) e produz os shafts. A
/// textura faz a névoa ONDULAR (bancos e clareiras em vez de sopa uniforme);
/// o `drive_fog_texture` rola-a com o vento do `<Weather>`.
fn spawn_fog_volume(mut commands: Commands, mut images: ResMut<Assets<Image>>) {
    commands.spawn((
        CinematicFogVolume,
        FogVolume {
            // God-rays ADITIVOS: absorção ~nula — a transmissão nunca
            // escurece o céu para breu (as "faixas pretas" do topo); a
            // névoa SÓ soma luz espalhada na direcção do sol.
            // (2026-09-09: scattering/densidade baixados — o véu aditivo
            // branco lavava o céu inteiro visto do chão, o "dia leitoso"
            // do repro do utilizador; os shafts continuam, mais discretos.)
            absorption: 0.02,
            scattering: 0.16,
            density_factor: FOG_VOLUME_DENSITY,
            // Forward-scattering alto: os shafts ganham força quando a câmara
            // aponta para a fonte — o comportamento cinematográfico.
            scattering_asymmetry: 0.65,
            fog_color: Color::srgb(1.0, 0.97, 0.92),
            // Névoa com TEXTURA (bancos FBM que rolam com o vento).
            // `VIBER_NO_FOGTEX=1` volta à névoa uniforme (A/B de QA).
            density_texture: std::env::var_os("VIBER_NO_FOGTEX")
                .is_none()
                .then(|| images.add(fbm_density_texture())),
            ..FogVolume::default()
        },
        // ALTO e centrado ACIMA do herói: a aresta do cubo tem de ficar fora
        // do ecrã em enquadramentos normais (uma aresta que corta o céu lê-se
        // como faixa). 600 m de meio-largura vertical cobre colinas e voo
        // de câmara curto.
        Transform::from_scale(Vec3::new(900.0, 600.0, 900.0)),
    ));
}

/// Textura de densidade 64³ — FBM 3D (hash inteiro, 3 oitavas com rotação
/// de grelha): bancos de névoa com clareiras. Só o canal R é lido pelo
/// raymarch; gravamos R = densidade, G = uma segunda camada mais alto-freq
/// (futura variação por altura), sampler REPEAT para o scroll poder rolar.
///
/// Hash inteiro (u32): o hash float degenera em coordenadas grandes (ver
/// water.wgsl) — aqui é CPU e o mundo de células é pequeno, mas mantemos o
/// padrão determinístico da engine (mesma textura em todas as runs).
fn fbm_density_texture() -> Image {
    const N: u32 = 64;
    let mut data = vec![0u8; (N * N * N) as usize * 4];
    let mut idx = 0usize;
    for z in 0..N {
        for y in 0..N {
            for x in 0..N {
                let p = [
                    x as f32 / N as f32,
                    y as f32 / N as f32,
                    z as f32 / N as f32,
                ];
                let d = fbm_density(p);
                let fine = fbm_density([p[0] * 3.1 + 7.7, p[1] * 3.1, p[2] * 3.1]);
                data[idx] = (d * 255.0).round().clamp(0.0, 255.0) as u8;
                data[idx + 1] = (fine * 255.0).round().clamp(0.0, 255.0) as u8;
                data[idx + 2] = 0;
                data[idx + 3] = 255;
                idx += 4;
            }
        }
    }
    let mut image = Image::new(
        bevy::render::render_resource::Extent3d {
            width: N,
            height: N,
            depth_or_array_layers: N,
        },
        bevy::render::render_resource::TextureDimension::D3,
        data,
        bevy::render::render_resource::TextureFormat::Rgba8Unorm,
        bevy::asset::RenderAssetUsages::MAIN_WORLD | bevy::asset::RenderAssetUsages::RENDER_WORLD,
    );
    // MIP CHAIN 3D PRÓPRIA (downsample 2×2×2). DUPLA função: (1) o
    // `patch_image` do `crate::textures` faz early-return em texturas com
    // mips — senão cozinhava uma chain 2D em cima de uma D3 (mip_count 12
    // com bytes 2D; o wgpu esperava a chain 3D e o prepare do GpuImage
    // panica-va: "range end 1179648 out of range for slice of length
    // 1054036", crash da r3); (2) o filtering fica estável se o raymarch
    // alguma vez amostrar com bias.
    let mips = N.trailing_zeros() + 1;
    image.texture_descriptor.mip_level_count = mips;
    let chain_len = mip3_chain_bytes(N, mips);
    if let Some(full) = image.data.as_mut() {
        let base_len = full.len();
        full.resize(chain_len, 0);
        for level in 1..mips {
            let parent = N >> (level - 1);
            let size = N >> level;
            let src_off = mip3_chain_bytes(N, level - 1);
            let dst_off = mip3_chain_bytes(N, level);
            // Calcula o nível NUM VEC à parte: os índices de leitura (mip
            // anterior, já em `full`) e escrita (mip corrente) sobrepõem o
            // mesmo buffer — pré-computar evita o borrow partilhado e lê
            // valores consistentes.
            let mut next = vec![0u8; (size * size * size) as usize * 4];
            for z in 0..size {
                for y in 0..size {
                    for x in 0..size {
                        for c in 0..4 {
                            let texel = |xx: u32, yy: u32, zz: u32| {
                                let off =
                                    src_off + ((zz * parent + yy) * parent + xx) as usize * 4 + c;
                                full[off] as u32
                            };
                            let acc = texel(x * 2, y * 2, z * 2)
                                + texel(x * 2 + 1, y * 2, z * 2)
                                + texel(x * 2, y * 2 + 1, z * 2)
                                + texel(x * 2 + 1, y * 2 + 1, z * 2)
                                + texel(x * 2, y * 2, z * 2 + 1)
                                + texel(x * 2 + 1, y * 2, z * 2 + 1)
                                + texel(x * 2, y * 2 + 1, z * 2 + 1)
                                + texel(x * 2 + 1, y * 2 + 1, z * 2 + 1);
                            let idx = ((z * size + y) * size + x) as usize * 4 + c;
                            next[idx] = (acc / 8) as u8;
                        }
                    }
                }
            }
            full[dst_off..dst_off + next.len()].copy_from_slice(&next);
        }
        debug_assert_eq!(full.len(), chain_len.max(base_len));
    }
    // REPEAT: o offset anima (scroll infinito da névoa com o vento).
    image.sampler = bevy::image::ImageSampler::Descriptor(bevy::image::ImageSamplerDescriptor {
        address_mode_u: bevy::image::ImageAddressMode::Repeat,
        address_mode_v: bevy::image::ImageAddressMode::Repeat,
        address_mode_w: bevy::image::ImageAddressMode::Repeat,
        ..bevy::image::ImageSamplerDescriptor::linear()
    });
    image
}

/// Offset (bytes) do início do mip `level` na cadeia de uma textura 3D `n`³
/// RGBA8 (mip a mip, layout do wgpu). Com `level == mips` dá o TOTAL da
/// cadeia — usado para o resize do buffer.
fn mip3_chain_bytes(n: u32, level: u32) -> usize {
    (0..level)
        .map(|l| {
            let s = n >> l;
            (s * s * s) as usize * 4
        })
        .sum()
}

/// FBM 3D em [0,1] — 3 oitavas de value noise com rotação por oitava
/// (sem a rotação as oitavas alinham nos eixos e saem prateleiras).
fn fbm_density(p: [f32; 3]) -> f32 {
    fn hash(x: i32, y: i32, z: i32) -> f32 {
        let mut h = (x as u32).wrapping_mul(0x27d4eb2d)
            ^ (y as u32).wrapping_mul(0x165667b1)
            ^ (z as u32).wrapping_mul(0x9e3779b1);
        h = h.wrapping_mul(0x85ebca6b);
        h ^= h >> 13;
        h = h.wrapping_mul(0xc2b2ae35);
        h ^= h >> 16;
        (h & 0x00ff_ffff) as f32 / 16_777_216.0
    }
    fn vnoise(x: f32, y: f32, z: f32) -> f32 {
        let (xi, yi, zi) = (x.floor(), y.floor(), z.floor());
        let (xf, yf, zf) = (x - xi, y - yi, z - zi);
        let s = |t: f32| t * t * (3.0 - 2.0 * t);
        let (u, v, w) = (s(xf), s(yf), s(zf));
        let lerp = |a: f32, b: f32, t: f32| a + (b - a) * t;
        let c000 = hash(xi as i32, yi as i32, zi as i32);
        let c100 = hash(xi as i32 + 1, yi as i32, zi as i32);
        let c010 = hash(xi as i32, yi as i32 + 1, zi as i32);
        let c110 = hash(xi as i32 + 1, yi as i32 + 1, zi as i32);
        let c001 = hash(xi as i32, yi as i32, zi as i32 + 1);
        let c101 = hash(xi as i32 + 1, yi as i32, zi as i32 + 1);
        let c011 = hash(xi as i32, yi as i32 + 1, zi as i32 + 1);
        let c111 = hash(xi as i32 + 1, yi as i32 + 1, zi as i32 + 1);
        lerp(
            lerp(lerp(c000, c100, u), lerp(c010, c110, u), v),
            lerp(lerp(c001, c101, u), lerp(c011, c111, u), v),
            w,
        )
    }
    // Células ~8 (uma célula = N/8 texels): bancos grandes. Rotação por
    // oitava quebra o alinhamento axial.
    let mut sum = 0.0;
    let mut amp = 0.5;
    let mut norm = 0.0;
    let mut q = [p[0] * 8.0, p[1] * 8.0, p[2] * 8.0];
    for _ in 0..3 {
        sum += vnoise(q[0], q[1], q[2]) * amp;
        norm += amp;
        q = [
            q[0] * 0.80 + q[1] * 0.60 + 2.3,
            -q[0] * 0.60 + q[1] * 0.80 + 5.1,
            q[2] * 1.93 + 9.7,
        ];
        amp *= 0.5;
    }
    // Remap para CONTRASTE: névoa em bancos (0.2..1) com clareiras — uma
    // distribuição uniforme lia-se como ruído de TV, não névoa.
    let base = sum / norm;
    (base - 0.28).max(0.0) / 0.72
}

/// Rola a textura de densidade com o vento do `<Weather>` e ajusta a
/// densidade do volume pela HORA (alvorad/amanhecer e pântano mais densos):
/// a névoa passa a viver — desliza, engrossa ao amanhecer, dissolve ao
/// meio-dia.
#[allow(clippy::type_complexity)]
fn drive_fog_texture(
    time: Res<Time>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    biomes: Option<Res<crate::worldsys::BiomeRegions>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut volumes: Query<&mut FogVolume, With<CinematicFogVolume>>,
) {
    let (wind_x, wind_z) = weather
        .as_deref()
        .map(|w| (w.wind[0], w.wind[1]))
        .unwrap_or((0.7, 0.25));
    for mut volume in &mut volumes {
        // Scroll lento na direcção do vento (UV/s × força 0.004).
        let speed = 0.004 * time.delta_secs();
        volume.density_texture_offset.x += wind_x * speed;
        volume.density_texture_offset.z += wind_z * speed;
        // Engrossa à alvorada/crepúsculo e no pântano; meio-dia limpo. A base
        // é a MESMA do spawn ([`FOG_VOLUME_DENSITY`]) — escrever aqui um
        // literal tornava o valor do spawn letra morta.
        //
        // ESCALA COM O DIA ([`FOG_VOLUME_NIGHT_ATTENUATION`]): o volume
        // existe para os god-rays, que são um efeito de SOL. À noite a luz
        // direcional NÃO o acende (o `light_attenuation` do bevy é
        // `exp(−densidade × raio do AABB × (abs+scat))` ≈ 0 com estes
        // números) e ele só soma o véu — encobre a distância sem dar nada em
        // troca. Medido no `qa-raster` de noite com chuva: sem o volume o
        // contraste da cena sobe ~50 % (sd 3.2 → 4.8) e o frame clareia;
        // no pântano (que leva o bónus) o véu era o pior do mundo.
        let golden_haze = atmosphere.golden * 0.06;
        let day_att =
            FOG_VOLUME_NIGHT_ATTENUATION + (1.0 - FOG_VOLUME_NIGHT_ATTENUATION) * atmosphere.day;
        let mut density = FOG_VOLUME_DENSITY * day_att + golden_haze;
        if let (Some(biomes), Ok(player)) = (biomes.as_deref(), players.single()) {
            let pos = player.translation();
            if biomes.list.iter().any(|b| {
                b.id.contains("swamp") && crate::ambient::point_in_polygon(pos.x, pos.z, &b.polygon)
            }) {
                density += FOG_VOLUME_SWAMP_BONUS * day_att;
            }
        }
        // Só escreve quando muda (o componente em `Changed` por frame faz o
        // passe re-preparar o volume à toa).
        if (volume.density_factor - density).abs() > 1e-6 {
            volume.density_factor = density;
        }
    }
}

/// O volume segue o herói (centrado, +50 m acima dos pés para cobrir colinas
/// e voo de câmara) — sem textura de densidade, deslizar não tem artefactos.
#[allow(clippy::type_complexity)]
fn follow_fog_volume(
    players: Query<&GlobalTransform, With<Player>>,
    mut volumes: Query<&mut Transform, With<CinematicFogVolume>>,
) {
    let Ok(player) = players.single() else {
        return;
    };
    let p = player.translation();
    for mut volume in &mut volumes {
        let target = Vec3::new(p.x, p.y + 50.0, p.z);
        if volume.translation != target {
            volume.translation = target;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// O passe de perspetiva aérea corre ANTES do TAA por omissão: é
    /// depth-aware e a depth do prepass vem jitterada, por isso mascarar
    /// DEPOIS do resolve devolve o aliasing às cristas (vibração no
    /// horizonte). `VIBER_AERIAL_AFTER_TAA=1` é só o braço de A/B.
    #[test]
    fn aerial_runs_before_taa_by_default() {
        if std::env::var_os("VIBER_AERIAL_AFTER_TAA").is_none() {
            assert!(
                !aerial_after_taa(),
                "sem o env o passe tem de ficar no EarlyPostProcess, antes do TAA"
            );
        }
    }

    #[test]
    fn test_exposure_multiplier_maps_to_stops() {
        // Multiplicador 1 = sem alteração.
        assert!((ev100_for_exposure_multiplier(10.0, 1.0) - 10.0).abs() < 1e-5);
        // Metade da luz = mais um stop de EV (imagem mais escura).
        assert!((ev100_for_exposure_multiplier(10.0, 0.5) - 11.0).abs() < 1e-5);
        // O dobro = menos um stop.
        assert!((ev100_for_exposure_multiplier(10.0, 2.0) - 9.0).abs() < 1e-5);
        // Valores inválidos não mexem na base.
        assert!((ev100_for_exposure_multiplier(10.0, 0.0) - 10.0).abs() < 1e-5);
        assert!((ev100_for_exposure_multiplier(10.0, f32::NAN) - 10.0).abs() < 1e-5);
    }

    #[test]
    fn test_auto_exposure_lift_is_capped_in_the_dark() {
        // Escuro fundo (rede de segurança): o ganho fica preso no teto.
        // Com o joelho LOOP C (−6), o piso da rampa é −12.
        for x in [-13.0, -15.0] {
            assert!(
                (auto_exposure_target_lift(x) - NIGHT_LIFT_CAP_EV).abs() < 1e-5,
                "x={x} deve estar preso no teto"
            );
        }
        // DIA, GOLDEN HOUR (−2.6), a noite da LOOP A (−2.25) E a noite LOOP C
        // (≈−3.9, ambiente 0,075): medição cheia — o medidor abre como sempre
        // e as POÇAS sobrevivem ao ND (o bug do joelho a −3: a noite LOOP C
        // caía DENTRO da rampa e perdia ~1,65 stops — poças esmagadas).
        let knee = -NIGHT_LIFT_KNEE_EV;
        assert!((auto_exposure_target_lift(knee) + knee).abs() < 1e-5);
        assert!((auto_exposure_target_lift(0.0)).abs() < 1e-5);
        assert!((auto_exposure_target_lift(3.0) + 3.0).abs() < 1e-5);
        assert_eq!(auto_exposure_compensation(-2.6), 0.0, "golden hour intacta");
        assert_eq!(
            auto_exposure_compensation(-2.25),
            0.0,
            "noite da vila (LOOP A): curva 0"
        );
        assert_eq!(
            auto_exposure_compensation(-3.9),
            0.0,
            "noite da vila (LOOP C, ambiente 0,075): curva 0 — as poças vivem"
        );
        // A rampa é monótona entre o joelho e o dobro (sem degrau no shutter).
        let ramp: Vec<f32> = (0..=10)
            .map(|i| auto_exposure_target_lift(knee - i as f32 * 0.3))
            .collect();
        assert!(
            ramp.windows(2).all(|w| w[1] <= w[0] + 1e-6),
            "rampa não-monotónica: {ramp:?}"
        );
        // A curva tem de sair do `from_curve` (monótona, sem descontinuidades
        // — um `Err` cai no default, que é a LUT plana e sem teto).
        use bevy::math::cubic_splines::LinearSpline;
        assert!(
            AutoExposureCompensationCurve::from_curve(LinearSpline::new(
                night_capped_curve_points()
            ))
            .is_ok()
        );
    }

    #[test]
    fn test_night_readability_lives_in_grading_not_in_the_meter() {
        // A MÉDIA da noite da vila (−2.25: fog + poças + chamas no buffer)
        // fica ACIMA do joelho — o medidor abre como sempre (+2.25). A
        // legibilidade vem do DESVIO de grading, que escurece DEPOIS do
        // medidor (não é corrigido): net ≈ +2.25 − 2.8 < 0, a noite desce
        // abaixo da paleta cru.
        let meter_lift = auto_exposure_target_lift(-2.25);
        assert!((meter_lift - 2.25).abs() < 1e-5, "medição cheia de noite");
        let net = meter_lift + grade_night_exposure_offset(1.0);
        assert!(net < 0.0, "a noite tem de ficar abaixo da paleta: {net}");
        // E o dia fica EXACTAMENTE como estava: desvio 0, curva 0.
        assert_eq!(grade_night_exposure_offset(0.0), 0.0);
        assert_eq!(auto_exposure_compensation(-1.0), 0.0);
    }

    #[test]
    fn test_grade_saturation_keeps_night_blue() {
        // A noite NÃO dessatura: o azul da paleta (fog/céu) tem de chegar ao
        // ecrã (o −0.22 antigo produzia a "lama cinzenta" medida 0.07 de
        // saturação às 23:00).
        assert!((grade_saturation(0.0, 1.0) - 1.0).abs() < 1e-6);
        assert!((grade_saturation(0.0, 0.0) - 1.0).abs() < 1e-6);
        // Golden hour continua a saturar.
        assert!((grade_saturation(1.0, 0.0) - 1.18).abs() < 1e-6);
        // A noite arrefece e a golden aquece, na mesma proporção de sempre.
        assert!((grade_temperature(0.0, 1.0) + 0.25).abs() < 1e-6);
        assert!((grade_temperature(1.0, 0.0) - 0.35).abs() < 1e-6);
    }

    #[test]
    fn test_night_exposure_offset_ramps_and_spares_day() {
        // Dia e crepúsculo (night=0): SEM desvio — o look aprovado não mexe.
        assert_eq!(grade_night_exposure_offset(0.0), 0.0);
        // Noite cheia: o desvio completo.
        assert_eq!(grade_night_exposure_offset(1.0), NIGHT_GRADING_EXPOSURE_EV);
        assert!(NIGHT_GRADING_EXPOSURE_EV < -2.0, "a noite tem de escurecer");
        // Rampa linear e monotónica com a fração de noite (o shutter não dá
        // degraus ao anoitecer).
        let half = grade_night_exposure_offset(0.5);
        assert!((half - NIGHT_GRADING_EXPOSURE_EV * 0.5).abs() < 1e-6);
        assert!(half < 0.0 && half > NIGHT_GRADING_EXPOSURE_EV);
        // Lixo não finito não propaga.
        assert_eq!(
            grade_night_exposure_offset(0.3),
            NIGHT_GRADING_EXPOSURE_EV * 0.3
        );
    }

    // ── LOOP C: bloom nocturno + perspetiva aérea ──────────────────────

    #[test]
    fn test_night_bloom_threshold_ramps_and_spares_day() {
        // Dia e crepúsculo: o 700 histórico INTACTO — o look aprovado não mexe.
        assert_eq!(night_bloom_threshold(0.0), DAY_BLOOM_THRESHOLD);
        assert_eq!(night_bloom_threshold(0.0), 700.0);
        // Noite plena: o joelho desce ao valor da chama.
        assert!((night_bloom_threshold(1.0) - NIGHT_BLOOM_THRESHOLD).abs() < 1e-3);
        // A ÂNCORA: a chama (≈1,0 no buffer) passa o joelho INTEIRA; a massa
        // do chão ao luar (0,1..0,3, média do frame ≈0,21) fica FORA do
        // joelho (0,35..0,70 com softness 0,5) — sem véu.
        assert!(NIGHT_BLOOM_THRESHOLD < 1.0, "chama tem de florescer");
        assert!(
            NIGHT_BLOOM_THRESHOLD * 0.5 > 0.3,
            "o joelho começa acima da massa do chão ao luar"
        );
        // Rampa linear e monotónica (o anoitecer não dá salto de bloom).
        let half = night_bloom_threshold(0.5);
        assert!((half - (DAY_BLOOM_THRESHOLD + NIGHT_BLOOM_THRESHOLD) * 0.5).abs() < 1e-4);
        let mut prev = DAY_BLOOM_THRESHOLD;
        for i in 0..=10 {
            let t = night_bloom_threshold(i as f32 / 10.0);
            assert!(t <= prev + 1e-6, "threshold desce com a noite: {t}");
            prev = t;
        }
        // Lixo não finito não propaga.
        assert_eq!(night_bloom_threshold(f32::NAN), DAY_BLOOM_THRESHOLD);
    }

    #[test]
    fn test_night_bloom_target_respects_cap_and_day() {
        // O reforço nocturno cabe no teto MAX_BLOOM mesmo com o boost da
        // atmosfera à noite (0,16): 0,12 base + 0,16 + 0,20 = 0,48 ≤ 0,5.
        let night_target = (BASE_BLOOM + 0.16 + NIGHT_BLOOM_INTENSITY).clamp(0.0, MAX_BLOOM);
        assert!(
            (night_target - 0.48).abs() < 1e-4,
            "noite cheia usa o bloom todo sem clipar: {night_target}"
        );
        // De dia o reforço é ZERO (o drive soma × night).
        assert_eq!(NIGHT_BLOOM_INTENSITY * 0.0, 0.0);
    }

    #[test]
    fn test_night_bloom_lf_boost_ramps_and_spares_day() {
        // Dia: o 0,7 do preset NATURAL — o look aprovado não mexe.
        assert!((night_bloom_lf_boost(0.0) - 0.7).abs() < 1e-6);
        // Noite plena: o boost cheio, ACIMA do preset.
        assert!((night_bloom_lf_boost(1.0) - NIGHT_BLOOM_LF_BOOST).abs() < 1e-6);
        assert!(NIGHT_BLOOM_LF_BOOST > 0.7, "halo nocturno mais largo");
        // Rampa linear e lixo não finito não propaga.
        let half = night_bloom_lf_boost(0.5);
        assert!((half - (0.7 + (NIGHT_BLOOM_LF_BOOST - 0.7) * 0.5)).abs() < 1e-6);
        assert!((night_bloom_lf_boost(f32::NAN) - 0.7).abs() < 1e-6);
    }

    #[test]
    fn test_aerial_factor_ramp_sky_and_monotonic() {
        // Perto: identidade — a legibilidade de jogo não paga a atmosfera.
        assert_eq!(aerial_factor(0.0, 1.0), 0.0);
        assert_eq!(aerial_factor(AERIAL_START_M - 1.0, 1.0), 0.0);
        // Céu (domo ≥ AERIAL_SKY_SKIP_M): NUNCA é tocado.
        assert_eq!(aerial_factor(AERIAL_SKY_SKIP_M, 1.0), 0.0);
        assert_eq!(aerial_factor(5000.0, 1.0), 0.0);
        // O longe chega ao teto (serra a ~620 m com peso cheio).
        let full = aerial_factor(AERIAL_FULL_M, 1.0);
        assert!((full - AERIAL_MAX_MIX).abs() < 1e-5, "teto do mix: {full}");
        // A serra entre o início e o fim cresce MONOTONICAMENTE.
        let mut prev = 0.0f32;
        for d in 0..=40 {
            let f = aerial_factor(AERIAL_START_M + d as f32 * 12.0, 1.0);
            assert!(f >= prev - 1e-6, "factor cresce com a distância: {d}");
            prev = f;
        }
        // O peso da noite ESCALA (nunca amplifica) e lixo não propaga.
        let night_f = aerial_factor(600.0, aerial_weight(1.0));
        let day_f = aerial_factor(600.0, aerial_weight(0.0));
        assert!(night_f < day_f, "noite dessatura menos que o dia");
        assert_eq!(aerial_factor(f32::NAN, 1.0), 0.0);
        assert!((aerial_weight(f32::NAN) - 1.0).abs() < 1e-6);
        // A golden 07:30 do gauntlet (night = 0): peso CHEIO.
        assert_eq!(aerial_weight(0.0), 1.0);
    }

    #[test]
    fn test_aerial_grade_desaturates_blueshifts_and_darkens() {
        // O píxel de névoa BRANCA (o bleach do crítico): dessatura, desvia
        // para azul-cinza e DESCE (contraste contra o céu mais claro).
        let white = [0.60, 0.60, 0.60];
        let g = aerial_grade(white, 0.85);
        let luma = |c: [f32; 3]| 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
        assert!(g[2] > g[0], "blue-shift: {g:?}");
        assert!(luma(g) < luma(white), "o haze escurece: {:?}", luma(g));
        // A saturação MORRE: a distância entre canais encolhe.
        let spread = |c: [f32; 3]| {
            c.iter().cloned().fold(f32::MIN, f32::max) - c.iter().cloned().fold(f32::MAX, f32::min)
        };
        assert!(spread(g) < spread([0.8, 0.3, 0.2]) * 0.5 || spread(g) < 0.1);
        // Fator 0 = identidade (o primeiro plano fica EXACTAMENTE como era).
        let fg = aerial_grade([0.7, 0.5, 0.3], 0.0);
        for c in 0..3 {
            assert!((fg[c] - [0.7, 0.5, 0.3][c]).abs() < 1e-6);
        }
        // Um pixel de serra SATURADO (verde-floresta) à distância: perde a
        // matiz mas mantém a DIRECÇÃO do tint (azul ≥ vermelho).
        let far = aerial_grade([0.10, 0.30, 0.12], 0.8);
        assert!(
            far[2] >= far[0] - 1e-4,
            "serra distante lê azulada: {far:?}"
        );
    }

    #[test]
    fn test_aerial_wgsl_layout_matches_packing() {
        // Guardas de regressão do passe (o harness naga em
        // tests/aerial_shader.rs valida a compilação e o tamanho):
        // self-contained, bindings na ordem do pipeline, e o uniform
        // packado com EXACTAMENTE AERIAL_UNIFORM_BYTES.
        assert!(
            !AERIAL_WGSL.contains("#import"),
            "self-contained: sem imports"
        );
        assert!(AERIAL_WGSL.contains("texture_depth_2d"));
        assert!(AERIAL_WGSL.contains("fn fragment"));
        let clip = bevy::math::Mat4::IDENTITY;
        let world = bevy::math::Mat4::IDENTITY;
        let packed = pack_aerial_uniform(&clip, &world, [1.0, 2.0, 3.0], 0.5);
        assert_eq!(packed.len(), AERIAL_UNIFORM_BYTES);
        // O peso vai na posição certa (ramp.w): mats 0..128, cam 128..144,
        // ramp 144..160 — w no float 3 do vec4 = byte 156.
        let w_bytes: [u8; 4] = packed[156..160].try_into().unwrap();
        assert_eq!(f32::from_le_bytes(w_bytes), 0.5);
    }

    // ── LOOP B: split-tone + key de lua ────────────────────────────────

    /// Espelho do WGSL do split-tone (mesma matemática, f32) para os
    /// testes poderem verificar o efeito do passe pixel a pixel.
    fn graded_by_shader(color: [f32; 3], shadow: [f32; 3], highlight: [f32; 3]) -> [f32; 3] {
        const LUMA: [f32; 3] = [0.2126, 0.7152, 0.0722];
        let luma = color[0] * LUMA[0] + color[1] * LUMA[1] + color[2] * LUMA[2];
        let t = ((luma - SPLIT_TONE_LO) / (SPLIT_TONE_HI - SPLIT_TONE_LO)).clamp(0.0, 1.0);
        let w = t * t * (3.0 - 2.0 * t);
        let tint = [
            shadow[0] + (highlight[0] - shadow[0]) * w,
            shadow[1] + (highlight[1] - shadow[1]) * w,
            shadow[2] + (highlight[2] - shadow[2]) * w,
        ];
        let norm = (tint[0] * LUMA[0] + tint[1] * LUMA[1] + tint[2] * LUMA[2]).max(1e-4);
        [
            color[0] * tint[0] / norm,
            color[1] * tint[1] / norm,
            color[2] * tint[2] / norm,
        ]
    }

    #[test]
    fn test_split_tone_midday_is_identity() {
        let (shadow, highlight) = split_tone_for(0.0, 0.0);
        assert_eq!(shadow, [1.0, 1.0, 1.0]);
        assert_eq!(highlight, [1.0, 1.0, 1.0]);
        // E o passe sobre um pixel qualquer é no-op.
        for color in [[0.01, 0.01, 0.01], [0.2, 0.2, 0.2], [0.6, 0.6, 0.6]] {
            let graded = graded_by_shader(color, shadow, highlight);
            for c in 0..3 {
                assert!((graded[c] - color[c]).abs() < 1e-6);
            }
        }
    }

    #[test]
    fn test_low_sun_split_weight_covers_morning_and_spares_midday() {
        // Meio-dia do simple-rpg (62°) e qualquer sol acima de 50°: ZERO —
        // o dia neutro aprovado não ganha tinte (LOOP D: zero a 50°, era 45°).
        assert_eq!(low_sun_split_weight(62.0), 0.0);
        assert_eq!(low_sun_split_weight(50.0), 0.0);
        // Sol rasante (≤25°) e debaixo do horizonte: MÁXIMO (a transição
        // para o braço da noite é a rampa `night`).
        assert_eq!(low_sun_split_weight(25.0), 1.0);
        assert_eq!(low_sun_split_weight(5.0), 1.0);
        assert_eq!(low_sun_split_weight(-10.0), 1.0);
        // O CENÁRIO DO GAUNTLET (LOOP D: a janela ALARGOU para o cobrir —
        // o crítico da LOOP C mediu 07:30/07:49 como neutro com a janela
        // 12°/45°): 07:30 (26,9°) ~CHEIO e 07:49 (30,8°) ainda ≥ 0.8.
        let w0730 = low_sun_split_weight(26.9);
        assert!(w0730 >= 0.95, "07:30 pesa ~cheio: {w0730}");
        let w0749 = low_sun_split_weight(30.8);
        assert!(w0749 >= 0.8, "07:49 pesa forte: {w0749}");
        // Monotónica descendo com a elevação (o shutter não dá salto ao
        // sol subir) e lixo não finito não propaga.
        let mut prev = 1.0f32;
        for deg in [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0] {
            let w = low_sun_split_weight(deg);
            assert!(w <= prev + 1e-6, "peso desce com a elevação: {deg}°");
            prev = w;
        }
        assert_eq!(low_sun_split_weight(f32::NAN), 0.0);
    }

    #[test]
    fn test_golden_key_mix_window_gates_and_caps() {
        // Meio-dia (62°) e noite: SEM key — o dia neutro e o look nocturno
        // aprovados não mexem.
        assert_eq!(golden_key_mix(62.0, 0.0), 0.0);
        assert_eq!(
            golden_key_mix(26.9, 1.0),
            0.0,
            "noite nunca ganha key âmbar"
        );
        // Debaixo do horizonte: a direcional já é a LUA (moon_key_drive).
        assert_eq!(golden_key_mix(-10.0, 0.0), 0.0);
        assert_eq!(golden_key_mix(0.0, 0.0), 0.0);
        // A janela do gauntlet: 07:30 e 07:49 com a key quase cheia.
        let k0730 = golden_key_mix(26.9, 0.0);
        assert!(k0730 >= GOLDEN_KEY_MIX * 0.95, "07:30 key ~cheia: {k0730}");
        let k0749 = golden_key_mix(30.8, 0.0);
        assert!(k0749 >= GOLDEN_KEY_MIX * 0.8, "07:49 key forte: {k0749}");
        // Rasante (5°): o máximo, e nunca acima do teto.
        let klow = golden_key_mix(5.0, 0.0);
        assert!((klow - GOLDEN_KEY_MIX).abs() < 1e-6);
        assert!(GOLDEN_KEY_MIX <= 1.0);
        // Monotónica com a elevação (desce à medida que o sol sobe) e lixo
        // não finito não propaga.
        let mut prev = GOLDEN_KEY_MIX;
        for deg in [1.0, 10.0, 20.0, 30.0, 40.0, 55.0] {
            let k = golden_key_mix(deg, 0.0);
            assert!(k <= prev + 1e-6, "key desce com a elevação: {deg}°");
            prev = k;
        }
        assert_eq!(golden_key_mix(f32::NAN, 0.0), 0.0);
        assert_eq!(golden_key_mix(26.9, f32::NAN), 0.0);
    }

    #[test]
    fn test_golden_key_drive_warms_sun_and_spares_night_and_noon() {
        use bevy::ecs::system::RunSystemOnce as _;

        // 07:30 (26,9°): a cor autoral branca empurra para o âmbar da key.
        let mut world = bevy::ecs::world::World::default();
        world.insert_resource(crate::worldsys::SunState {
            elevation_deg: 26.9,
            night: 0.0,
            ..Default::default()
        });
        let day_entity = world
            .spawn((
                bevy::light::DirectionalLight {
                    illuminance: 10_000.0,
                    color: Color::WHITE,
                    ..Default::default()
                },
                crate::worldsys::SunLightBase {
                    illuminance: 10_000.0,
                    color: Color::WHITE,
                },
            ))
            .id();
        world.run_system_once(golden_key_drive).unwrap();
        let light = world
            .get::<bevy::light::DirectionalLight>(day_entity)
            .unwrap();
        let c = light.color.to_linear();
        assert!(c.red > c.green && c.green > c.blue, "âmbar: {c:?}");
        // A iluminância NÃO mexe (a key é de COR, não de força).
        assert_eq!(light.illuminance, 10_000.0);
        // E o SunLightBase continua autoral (composição, não acumulação).
        let base = world
            .get::<crate::worldsys::SunLightBase>(day_entity)
            .unwrap();
        assert_eq!(base.color, Color::WHITE);

        // Meio-dia e noite: EXACTAMENTE como o sun_drive deixou.
        for (elev, night) in [(62.0, 0.0), (-12.0, 1.0)] {
            let mut world = bevy::ecs::world::World::default();
            world.insert_resource(crate::worldsys::SunState {
                elevation_deg: elev,
                night,
                ..Default::default()
            });
            let entity = world
                .spawn((bevy::light::DirectionalLight::default(),))
                .id();
            world.run_system_once(golden_key_drive).unwrap();
            let light = world.get::<bevy::light::DirectionalLight>(entity).unwrap();
            assert_eq!(light.color, Color::WHITE, "{elev}° fica branco");
        }
    }

    #[test]
    fn test_grade_temperature_full_warms_low_sun_and_spares_midday() {
        // Meio-dia (peso 0) e a LOOP B pura: iguais — o dia neutro não mexe.
        assert_eq!(grade_temperature_full(0.0, 0.0, 0.0), 0.0);
        assert!((grade_temperature_full(0.35, 0.0, 0.0) - 0.35 * 0.35).abs() < 1e-6);
        // 07:30 do gauntlet (`golden` ~0, peso 26,9° ~1): aquecimento global
        // ~0.28 — o stock aquece a manhã que a gaussiana não cobria.
        let t0730 = grade_temperature_full(0.0, low_sun_split_weight(26.9), 0.0);
        assert!(t0730 >= GOLDEN_GRADE_WARMTH * 0.95, "07:30 aquece: {t0730}");
        // A noite continua a arrefecer NA MESMA proporção de sempre.
        assert!((grade_temperature_full(0.0, 0.0, 1.0) + 0.25).abs() < 1e-6);
        // Lixo não finito não propaga.
        assert_eq!(grade_temperature_full(f32::NAN, f32::NAN, f32::NAN), 0.0);
    }

    #[test]
    fn test_torch_pool_gain_ramps_and_spares_day() {
        // Dia e crepúsculo: ganho 1 — o valor autoral fica EXACTAMENTE
        // como estava (o medidor do AutoExposure não vê nada; medido
        // 2026-09-13, golden 07:30: 1M lm no XML fechava o frame ~1,2 EV).
        assert_eq!(torch_pool_gain(0.0), 1.0);
        assert_eq!(warm_fill_gain(0.0), 1.0);
        // Noite plena: TOCHAS ×8 (raio ×2 vs LOOP C, falloff 1/d³ no chão)
        // e o fill quente (janelas/braseiros) só ×2 — os rácios da variante
        // XML-1M validada (11 poças, estrelas vivas, noite ainda noite).
        assert_eq!(torch_pool_gain(1.0), TORCH_POOL_NIGHT_GAIN);
        assert_eq!(warm_fill_gain(1.0), WARM_FILL_NIGHT_GAIN);
        assert!(TORCH_POOL_NIGHT_GAIN >= 8.0, "poças 2×: ganho ≥ 8");
        assert!(
            WARM_FILL_NIGHT_GAIN < TORCH_POOL_NIGHT_GAIN,
            "fill não compete"
        );
        // Rampa linear/monotónica e lixo não finito não propaga.
        let half = torch_pool_gain(0.5);
        assert!((half - (1.0 + (TORCH_POOL_NIGHT_GAIN - 1.0) * 0.5)).abs() < 1e-6);
        assert_eq!(torch_pool_gain(f32::NAN), 1.0);
        assert_eq!(warm_fill_gain(f32::NAN), 1.0);
        // Composição: tocha 120k→960k, janela 160k→320k.
        assert_eq!(
            torch_pool_target(120_000.0, TORCH_POOL_NIGHT_GAIN),
            960_000.0,
            "tocha de rua 120k → 960k (raio ×2)"
        );
        assert_eq!(
            torch_pool_target(160_000.0, WARM_FILL_NIGHT_GAIN),
            320_000.0,
            "janela 160k → 320k (fill)"
        );
        assert_eq!(
            torch_pool_target(120_000.0, 1.0),
            120_000.0,
            "dia: authored"
        );
        assert!(
            torch_pool_target(f32::NAN, 8.0).is_nan(),
            "base lixo propaga"
        );
        assert_eq!(
            torch_pool_target(-5.0, 8.0),
            -5.0,
            "base inválida devolve o base"
        );
    }

    #[test]
    fn test_torch_pool_warm_gate_matches_torch_family() {
        use bevy::color::LinearRgba;
        // As tochas/braseiros/janelas do simple-rpg (hex → linear aprox.).
        for (r, g, b) in [(1.0, 0.63, 0.25), (1.0, 0.66, 0.23), (1.0, 0.70, 0.39)] {
            assert!(torch_pool_is_warm(LinearRgba::rgb(r, g, b)), "{r},{g},{b}");
        }
        // O acento FRIO autoral (cristal 0x3f8fff) fica de fora.
        assert!(!torch_pool_is_warm(LinearRgba::rgb(0.06, 0.28, 1.0)));
        // Branco neutro também não é tocha.
        assert!(!torch_pool_is_warm(LinearRgba::rgb(1.0, 1.0, 1.0)));
    }

    #[test]
    fn test_torch_pool_drive_composes_over_captured_base() {
        use bevy::ecs::system::RunSystemOnce as _;

        // Noite plena: TOCHA (sombra autoral) 120k → ×8; janela quente sem
        // sombra → ×2 (fill); luz fria intacta; as bases capturadas
        // preservam os valores autorais.
        let mut world = bevy::ecs::world::World::default();
        world.insert_resource(crate::worldsys::AtmosphereState {
            night: 1.0,
            ..Default::default()
        });
        let torch = world
            .spawn((
                bevy::light::PointLight {
                    intensity: 120_000.0,
                    color: Color::srgb(1.0, 0.63, 0.25),
                    ..Default::default()
                },
                crate::ambient::AuthoredShadowLight,
            ))
            .id();
        let window = world
            .spawn(bevy::light::PointLight {
                intensity: 160_000.0,
                color: Color::srgb(1.0, 0.70, 0.39),
                ..Default::default()
            })
            .id();
        let cool = world
            .spawn(bevy::light::PointLight {
                intensity: 45_000.0,
                color: Color::srgb(0.06, 0.28, 1.0),
                ..Default::default()
            })
            .id();
        // Primeira passada: captura a base (via Commands); a segunda compõe.
        world.run_system_once(torch_pool_drive).unwrap();
        world.clear_trackers();
        world.flush();
        world.run_system_once(torch_pool_drive).unwrap();
        let torch_light = world.get::<bevy::light::PointLight>(torch).unwrap();
        assert!(
            (torch_light.intensity - torch_pool_target(120_000.0, TORCH_POOL_NIGHT_GAIN)).abs()
                < 1.0,
            "tocha composta: {}",
            torch_light.intensity
        );
        let window_light = world.get::<bevy::light::PointLight>(window).unwrap();
        assert!(
            (window_light.intensity - torch_pool_target(160_000.0, WARM_FILL_NIGHT_GAIN)).abs()
                < 1.0,
            "janela composta: {}",
            window_light.intensity
        );
        assert_eq!(
            world.get::<TorchLightBase>(torch).unwrap().intensity,
            120_000.0,
            "base autoral preservada"
        );
        let cool_light = world.get::<bevy::light::PointLight>(cool).unwrap();
        assert_eq!(cool_light.intensity, 45_000.0, "luz fria intacta");
        assert!(
            world.get::<TorchLightBase>(cool).is_none(),
            "a luz fria nem ganha base"
        );

        // Dia (night = 0): o drive é no-op (early-out do ganho 1).
        let mut world = bevy::ecs::world::World::default();
        world.insert_resource(crate::worldsys::AtmosphereState {
            night: 0.0,
            ..Default::default()
        });
        let entity = world
            .spawn((
                bevy::light::PointLight {
                    intensity: 120_000.0,
                    color: Color::srgb(1.0, 0.63, 0.25),
                    ..Default::default()
                },
                crate::ambient::AuthoredShadowLight,
            ))
            .id();
        world.run_system_once(torch_pool_drive).unwrap();
        world.clear_trackers();
        world.flush();
        world.run_system_once(torch_pool_drive).unwrap();
        let light = world.get::<bevy::light::PointLight>(entity).unwrap();
        assert_eq!(light.intensity, 120_000.0, "dia: authored");
    }

    #[test]
    fn test_golden_key_color_is_luma_normalized() {
        // O âmbar da key tem de preservar a luminância (luma ≈ 1): a variante
        // cru (1.0, 0.56, 0.22) — luma 0,61 — escurecia a golden hour ~23%
        // e o AutoExposure amplificava (medido 2026-09-13).
        const LUMA: [f32; 3] = [0.2126, 0.7152, 0.0722];
        let luma: f32 = GOLDEN_KEY_COLOR.iter().zip(LUMA).map(|(c, l)| c * l).sum();
        assert!(
            (luma - 1.0).abs() < 0.02,
            "key âmbar luma-preservante: {luma}"
        );
        assert!(GOLDEN_KEY_COLOR[0] > GOLDEN_KEY_COLOR[2] + 1.0, "bem âmbar");
    }

    #[test]
    fn test_split_tone_low_sun_cools_shadows_and_warms_highlights() {
        let (shadow, highlight) = split_tone_for(1.0, 0.0);
        // Sombras FRIAS (azul > vermelho) e abaixo do identity no R.
        assert!(shadow[2] > shadow[0], "sombra fria: {shadow:?}");
        assert!(shadow[0] < 1.0);
        // Highlights QUENTES (vermelho > azul).
        assert!(
            highlight[0] > highlight[2],
            "highlight quente: {highlight:?}"
        );
        // O efeito no PIXEL: sombra escura arrefece, highlight aquece, e a
        // LUMINÂNCIA se preserva (o passe é de matiz, não de exposição).
        let dark = [0.02, 0.02, 0.02];
        let bright = [0.5, 0.5, 0.5];
        let graded_dark = graded_by_shader(dark, shadow, highlight);
        let graded_bright = graded_by_shader(bright, shadow, highlight);
        assert!(graded_dark[2] > graded_dark[0]);
        assert!(graded_bright[0] > graded_bright[2]);
        const LUMA: [f32; 3] = [0.2126, 0.7152, 0.0722];
        for (original, graded) in [(dark, graded_dark), (bright, graded_bright)] {
            let l0: f32 = original.iter().zip(LUMA).map(|(c, l)| c * l).sum();
            let l1: f32 = graded.iter().zip(LUMA).map(|(c, l)| c * l).sum();
            assert!((l0 - l1).abs() < 1e-4, "luma preservada: {l0} vs {l1}");
        }
        // A SEPARAÇÃO: o B:R da sombra gradingada excede o do highlight
        // (é isto que o crítico pede — quente/frio na MESMA cena).
        let br = |c: [f32; 3]| c[2] / c[0].max(1e-5);
        assert!(br(graded_dark) > br(graded_bright) + 0.15);
    }

    #[test]
    fn test_split_tone_night_has_the_coolest_shadows_and_warm_pools() {
        let (night_shadow, night_highlight) = split_tone_for(0.0, 1.0);
        let (golden_shadow, _) = split_tone_for(1.0, 0.0);
        // A noite empurra o azul das sombras MAIS fundo que a golden.
        assert!(night_shadow[2] > golden_shadow[2]);
        assert!(night_shadow[2] > night_shadow[0]);
        // Highlights noturnos continuam do lado QUENTE (poças/braseiros).
        assert!(night_highlight[0] > night_highlight[2]);
        // E um píxel de poça (brilhante) à noite aquece ligeiro.
        let pool = graded_by_shader([0.45, 0.32, 0.16], night_shadow, night_highlight);
        assert!(pool[0] > pool[2], "poça quente continua quente: {pool:?}");
    }

    #[test]
    fn test_split_tone_ramps_are_monotonic_and_clean() {
        for golden in [0.0, 0.25, 0.5, 0.75, 1.0] {
            let (s, h) = split_tone_for(golden, 0.0);
            assert!(s.iter().all(|v| v.is_finite()));
            assert!(h.iter().all(|v| v.is_finite()));
        }
        let mut prev_b = f32::MIN;
        for i in 0..=10 {
            let (s, _) = split_tone_for(i as f32 / 10.0, 0.0);
            assert!(s[2] >= prev_b, "azul da sombra sobe com o golden");
            prev_b = s[2];
        }
        let mut prev_b = f32::MIN;
        for i in 0..=10 {
            let (s, _) = split_tone_for(0.0, i as f32 / 10.0);
            assert!(s[2] >= prev_b, "azul da sombra sobe com a noite");
            prev_b = s[2];
        }
        // Banda da transição coerente (dentro do alcance útil linear).
        assert!(SPLIT_TONE_LO < SPLIT_TONE_HI);
        assert!(SPLIT_TONE_LO > 0.0 && SPLIT_TONE_HI < 1.0);
        // Lixo não finito não propaga (a rampa faz clamp).
        let (s, h) = split_tone_for(f32::NAN, f32::NAN);
        assert!(s.iter().chain(h.iter()).all(|v| v.is_finite()));
    }

    #[test]
    fn test_split_tone_shader_imports_the_vertex_struct() {
        // Guarda de regressão do import do WGSL: no bevy 0.19 o struct do
        // fullscreen chama-se `FullscreenVertexOutput` — o import antigo
        // (`fullscreen_vertex_out`) deixava o identificador fora de scope, o
        // naga rejeitava o shader e o PASSE SALTAVA EM SILÊNCIO (o split-tone
        // simplesmente não existia no frame; apanhado no boot de QA 2026-09-13).
        assert!(
            SPLIT_TONE_WGSL.contains(
                "#import bevy_core_pipeline::fullscreen_vertex_shader::FullscreenVertexOutput"
            ),
            "o WGSL tem de importar o struct FullscreenVertexOutput pelo nome exacto"
        );
        assert!(
            !SPLIT_TONE_WGSL.contains("fullscreen_vertex_shader::fullscreen_vertex_out"),
            "o nome antigo fullscreen_vertex_out não existe no módulo do bevy 0.19"
        );
        // Assinatura do fragment coerente com o struct importado.
        assert!(SPLIT_TONE_WGSL.contains("fn fragment(in: FullscreenVertexOutput)"));
    }

    #[test]
    fn test_moon_illuminance_factor_spares_day() {
        // Dia: SEM ganho — o sol aprovado fica exactamente como estava.
        assert_eq!(moon_illuminance_factor(0.0), 1.0);
        // Noite plena: o ganho cheio.
        assert_eq!(moon_illuminance_factor(1.0), MOON_KEY_GAIN);
        assert!(MOON_KEY_GAIN > 1.5, "a key tem de marcar presença");
        // Rampa linear/monotónica (o crepúsculo não dá salto).
        let half = moon_illuminance_factor(0.5);
        assert!((half - (1.0 + (MOON_KEY_GAIN - 1.0) * 0.5)).abs() < 1e-6);
        // Lixo não finito não propaga.
        assert_eq!(moon_illuminance_factor(f32::NAN), 1.0);
    }

    #[test]
    fn test_moon_key_drive_composes_over_sun_drive() {
        use bevy::ecs::system::RunSystemOnce as _;

        let mut world = bevy::ecs::world::World::default();
        world.insert_resource(crate::worldsys::AtmosphereState {
            night: 1.0,
            ..Default::default()
        });
        // Luz como o sun_drive a deixa à noite: base 10 klx × 0.06 = 600 lux,
        // cor = MOON_COLOR em LINEAR (é isso que o mix_linear do sun_drive
        // escreve — Color::LinearRgba, não sRGB).
        let base = 10_000.0;
        let sun_night_value = base * crate::worldsys::MOONLIGHT_RATIO;
        let moon = bevy::color::LinearRgba::rgb(
            crate::worldsys::MOON_COLOR[0],
            crate::worldsys::MOON_COLOR[1],
            crate::worldsys::MOON_COLOR[2],
        );
        let light_entity = world
            .spawn((
                bevy::light::DirectionalLight {
                    illuminance: sun_night_value,
                    color: Color::LinearRgba(moon),
                    ..Default::default()
                },
                crate::worldsys::SunLightBase {
                    illuminance: base,
                    color: Color::LinearRgba(moon),
                },
            ))
            .id();

        world.run_system_once(moon_key_drive).unwrap();

        let light = world
            .get::<bevy::light::DirectionalLight>(light_entity)
            .unwrap();
        // Composição: 600 lux × MOON_KEY_GAIN — sem acumulação, sem feedback
        // no SunLightBase (que continua a guardar o valor autoral).
        assert!(
            (light.illuminance - sun_night_value * MOON_KEY_GAIN).abs() < 1e-2,
            "illuminance composto: {}",
            light.illuminance
        );
        let base_state = world
            .get::<crate::worldsys::SunLightBase>(light_entity)
            .unwrap();
        assert_eq!(base_state.illuminance, base);
        // A cor empurra para o azul da key.
        let c = light.color.to_linear();
        assert!(c.blue > c.red);
        assert!(
            c.blue > crate::worldsys::MOON_COLOR[2],
            "mais azul que MOON_COLOR"
        );
    }

    #[test]
    fn test_moon_key_drive_is_noop_in_day() {
        use bevy::ecs::system::RunSystemOnce as _;

        let mut world = bevy::ecs::world::World::default();
        world.insert_resource(crate::worldsys::AtmosphereState {
            night: 0.0,
            ..Default::default()
        });
        let sun_value = 10_000.0;
        let light_entity = world
            .spawn((
                bevy::light::DirectionalLight {
                    illuminance: sun_value,
                    ..Default::default()
                },
                crate::worldsys::SunLightBase {
                    illuminance: sun_value,
                    color: Color::WHITE,
                },
            ))
            .id();

        world.run_system_once(moon_key_drive).unwrap();

        let light = world
            .get::<bevy::light::DirectionalLight>(light_entity)
            .unwrap();
        assert_eq!(light.illuminance, sun_value);
        assert_eq!(light.color, Color::WHITE);
    }

    #[test]
    fn test_biome_exposure_of_the_simple_rpg_darkens() {
        // `pp-exposure="0.70"` do pântano tem de escurecer face à base.
        let swamp = ev100_for_exposure_multiplier(BASE_EV100, 0.70);
        assert!(swamp > BASE_EV100, "0.70 escurece: {swamp} vs {BASE_EV100}");
        // E o deserto (0.74) escurece menos que o pântano.
        let desert = ev100_for_exposure_multiplier(BASE_EV100, 0.74);
        assert!(desert < swamp, "0.74 é mais claro que 0.70");
    }

    #[test]
    fn test_punch_impact_brightens_darkens_and_clamps() {
        // Hit normal: +0.25 stops de clareza via kick, bloom sobe.
        let mut state = PostFxState::default();
        punch_impact(&mut state, 0.25, 0.10);
        assert!((state.kick - 0.25).abs() < 1e-5, "kick {}", state.kick);
        assert!((state.bloom - (BASE_BLOOM + 0.10)).abs() < 1e-5);
        // Abate: kick acumula até ao teto e o bloom bate no MAX_BLOOM.
        let mut state = PostFxState::default();
        punch_impact(&mut state, MAX_KICK_EV, MAX_BLOOM);
        punch_impact(&mut state, MAX_KICK_EV, MAX_BLOOM);
        assert!((state.kick - MAX_KICK_EV).abs() < 1e-5, "teto do kick");
        assert!((state.bloom - MAX_BLOOM).abs() < 1e-5, "teto do bloom");
        // Dano recebido: escurece (EV sobe), com piso.
        let mut state = PostFxState::default();
        punch_impact(&mut state, -0.3, 0.0);
        assert!((state.ev100 - (BASE_EV100 + 0.3)).abs() < 1e-5);
        punch_impact(&mut state, -5.0, 0.0);
        assert!(
            (state.ev100 - (BASE_EV100 + MAX_DARKEN_EV)).abs() < 1e-5,
            "piso do escurecimento: {}",
            state.ev100
        );
        // Punch não mexe nos ALVOS (só no estado corrente/kick) — o bioma
        // continua a mandar.
        assert!((state.target_ev100 - BASE_EV100).abs() < 1e-5);
        // Lixo não finito é no-op.
        let mut state = PostFxState::default();
        punch_impact(&mut state, f32::NAN, f32::NAN);
        assert!((state.ev100 - BASE_EV100).abs() < 1e-5);
        assert!((state.bloom - BASE_BLOOM).abs() < 1e-5);
        assert_eq!(state.kick, 0.0);
    }

    #[test]
    fn test_kick_decay_is_monotonic_and_terminates() {
        // Curva do flash do level-up: 0.6 EV a decair a 60 fps.
        let dt = 1.0 / 60.0;
        let mut kick = 0.6;
        let mut prev = kick;
        let mut frames = 0;
        while kick > 0.0 {
            kick = decay_kick(kick, dt);
            assert!(kick <= prev, "decay monotónico: {prev} → {kick}");
            prev = kick;
            frames += 1;
            assert!(
                frames < 600,
                "kick tem de terminar (~1 s), não eternizar-se"
            );
        }
        // ~1 s de decay visível: 3 τ ≈ 0.96 s.
        assert!(
            (120..=240).contains(&frames),
            "decay do kick termina em ~3τ: {frames} frames"
        );
        // Zero fica zero; lixo finito/não finito não fabrica kick.
        assert_eq!(decay_kick(0.0, dt), 0.0);
        assert_eq!(decay_kick(-0.5, dt), 0.0);
        assert_eq!(decay_kick(f32::NAN, dt), 0.0);
        assert_eq!(decay_kick(0.6, 0.0), 0.6, "dt=0 não decai");
        assert_eq!(decay_kick(0.6, -1.0), 0.6, "dt negativo é ignorado");
    }

    #[test]
    fn test_kick_accumulates_with_cap_and_exposure_brightens() {
        let mut state = PostFxState::default();
        assert_eq!(state.kick, 0.0);
        state.kick_exposure(crate::vitals::LEVELUP_KICK_EV);
        assert!(
            (state.kick - 0.6).abs() < 1e-5,
            "kick do level-up: {}",
            state.kick
        );
        // Um segundo level-up a meio soma (não reinicia) — até ao teto.
        state.kick_exposure(crate::vitals::LEVELUP_KICK_EV);
        assert!(
            (state.kick - MAX_KICK_EV).abs() < 1e-5,
            "teto: {}",
            state.kick
        );
        state.kick_exposure(MAX_KICK_EV);
        assert!((state.kick - MAX_KICK_EV).abs() < 1e-5, "não passa do teto");
        // Lixo não mexe.
        state.kick_exposure(f32::NAN);
        assert!((state.kick - MAX_KICK_EV).abs() < 1e-5);
        // Kick positivo CLAREIA: EV efetivo desce.
        assert!(
            ev_with_kick(state.ev100, state.kick) < state.ev100,
            "kick clareia (menos EV)"
        );
    }
}
