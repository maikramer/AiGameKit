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
//! `VIBER_NO_POSTFX=1` desliga tudo (comparações A/B e GPUs fracas).

use bevy::anti_alias::contrast_adaptive_sharpening::ContrastAdaptiveSharpening;
use bevy::anti_alias::fxaa::Fxaa;
use bevy::anti_alias::taa::TemporalAntiAliasing;
use bevy::camera::Exposure;
use bevy::core_pipeline::prepass::{DepthPrepass, NormalPrepass};
use bevy::core_pipeline::tonemapping::Tonemapping;
use bevy::light::{FogVolume, VolumetricFog};
use bevy::pbr::{ContactShadows, ScreenSpaceAmbientOcclusion, ScreenSpaceAmbientOcclusionQualityLevel};
use bevy::post_process::auto_exposure::{AutoExposure, AutoExposureCompensationCurve};
use bevy::post_process::bloom::{Bloom, BloomPrefilter};
use bevy::post_process::dof::{DepthOfField, DepthOfFieldMode};
use bevy::post_process::effect_stack::{ChromaticAberration, Vignette};
use bevy::post_process::motion_blur::MotionBlur;
use bevy::prelude::*;
use bevy::render::view::Msaa;

use crate::ambient::point_in_polygon;
use crate::player::Player;
use crate::profiler::{Group, timed};
use crate::worldsys::BiomeRegions;

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
/// O `AutoExposure` expõe para a luminância média da cena. De noite o mundo
/// físico fica ~6 stops abaixo do meio-cinza e o medidor satura no máximo
/// (+6 EV) — mas o céu e a névoa vivem na ESCALA DA PALETA (o domo é um
/// material custom que nunca recebe a exposição física, ver `sky.rs`;
/// `ambient.rs` usa a mesma escala para o fog se fundir com o horizonte do
/// domo). Multiplicar a paleta noturna (azul-escuro ~0.04) por 2^6 põe o
/// frame inteiro a branco — o "chuva à noite = ecrã branco" (repro ao vivo
/// 2026-09-10 no pântano: fog medido em 0.048 de luminância, frame a 236/255;
/// com a exposição neutralizada a mesma cena é uma noite azul legível).
///
/// 0.5 = a noite fica a NOITE (o ganho que resta é o mínimo para as fontes
/// quentes poparem); medido no simples-rpg, pântano com chuva a 0.9, 23:36 —
/// frame 127/255 com 2.0, 83/255 com 0.5, 71/255 com 0.0. O dia, o crepúsculo
/// e os interiores NÃO são tocados: a rampa só começa a apertar em
/// [`NIGHT_LIFT_KNEE_EV`]. `VIBER_NO_AECURVE=1` devolve a curva plana (o
/// comportamento sem teto) para A/B.
pub const NIGHT_LIFT_CAP_EV: f32 = 0.5;

/// Luminância média (log2, a unidade do histograma) a partir da qual o teto
/// começa a apertar. 3.0 = o comportamento de sempre fica INTACTO para cenas
/// até 3 stops abaixo do meio-cinza (crepúsculo, sombra funda, interiores) e o
/// aperto faz-se em rampa até ao dobro (6 stops = noite cheia, onde o medidor
/// satura). Sem esta folga, o teto escurecia também a alvorada/crepúsculo que
/// o user aprovou.
pub const NIGHT_LIFT_KNEE_EV: f32 = 3.0;

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
fn night_capped_curve_points() -> [bevy::math::Vec2; 6] {
    use bevy::math::vec2;
    let knee = NIGHT_LIFT_KNEE_EV;
    let at = |x: f32| vec2(x, auto_exposure_compensation(x));
    [
        // Escuro fundo: o teto já está preso (dois pontos só para a LUT
        // cobrir toda a gama do histograma).
        at(-12.0),
        at(-2.0 * knee),
        at(-1.5 * knee),
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
        app.add_systems(
            bevy::app::Update,
            (
                attach_postfx_to_cameras,
                // O grading lê [`crate::worldsys::AtmosphereState`] do MESMO
                // frame (publicada depois de `sun_drive`; o registo/glue vive
                // no `AmbientPlugin`, que também consome a paleta).
                timed(Group::Fx, drive_postfx).after(crate::worldsys::atmosphere_drive),
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
                // deixa o glow para o que é realmente brilhante: o disco
                // solar e fontes emissivas à noite.
                prefilter: BloomPrefilter {
                    threshold: 700.0,
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
            // por causa do ruído sem acumulação).
            ScreenSpaceAmbientOcclusion {
                quality_level: if taa_enabled() {
                    ScreenSpaceAmbientOcclusionQualityLevel::High
                } else {
                    ScreenSpaceAmbientOcclusionQualityLevel::Medium
                },
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

/// Interpola exposição/bloom na direção do bioma onde o herói está.
#[allow(clippy::type_complexity)]
fn drive_postfx(
    time: Res<Time>,
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    biomes: Option<Res<BiomeRegions>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut state: ResMut<PostFxState>,
    mut cameras: Query<(&mut Bloom, &mut Exposure, Option<&mut bevy::render::view::ColorGrading>), With<Camera3d>>,
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
    // quentes — fogueiras à noite, glare do sol rasante.
    state.target_ev100 = ev100_for_exposure_multiplier(
        BASE_EV100,
        exposure_mult * atmosphere.exposure_scale.max(0.05),
    );
    state.target_bloom = (bloom + atmosphere.bloom_boost).clamp(0.0, 0.5);

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
    // Grading POR HORA (r3): a golden hour aquece e satura, a noite dessatura
    // e arrefece — o "film stock" muda com o dia. ASC CDL compõe com o
    // TonyMcMapface (aplicado pré-tonemap). O grading por BIOMA precisa de
    // attrs no parser (frente fria do worldsys/recipes) — fica para seguir.
    let golden = atmosphere.golden;
    let night = atmosphere.night;
    let temperature = 0.0 + golden * 0.35 - night * 0.25;
    let saturation = 1.0 + golden * 0.18 - night * 0.22;
    for (mut camera_bloom, mut exposure, grading) in &mut cameras {
        if camera_bloom.intensity != bloom {
            camera_bloom.intensity = bloom;
        }
        if exposure.ev100 != ev100 {
            exposure.ev100 = ev100;
        }
        // A câmara pode não ter ColorGrading (inserido pelo attach com o
        // resto do pós; defensivo se alguém a spawnar à mão).
        if let Some(mut grading) = grading {
            let g = &mut grading.global;
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
                                let off = src_off + ((zz * parent + yy) * parent + xx) as usize * 4 + c;
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
        let mut h = (x as u32)
            .wrapping_mul(0x27d4eb2d)
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
        let day_att = FOG_VOLUME_NIGHT_ATTENUATION
            + (1.0 - FOG_VOLUME_NIGHT_ATTENUATION) * atmosphere.day;
        let mut density = FOG_VOLUME_DENSITY * day_att + golden_haze;
        if let (Some(biomes), Ok(player)) = (biomes.as_deref(), players.single()) {
            let pos = player.translation();
            if biomes
                .list
                .iter()
                .any(|b| b.id.contains("swamp") && crate::ambient::point_in_polygon(pos.x, pos.z, &b.polygon))
            {
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
        // Noite cheia (o medidor satura no fundo do histograma): o ganho fica
        // preso no teto — era aqui que a noite ia a branco (+6 EV).
        assert!((auto_exposure_target_lift(-6.0) - NIGHT_LIFT_CAP_EV).abs() < 1e-5);
        assert!((auto_exposure_target_lift(-12.0) - NIGHT_LIFT_CAP_EV).abs() < 1e-5);
        // Crepúsculo/sombra funda/interior: comportamento de sempre (expor
        // para o meio-cinza) — o aperto da noite não lhes toca.
        let knee = -NIGHT_LIFT_KNEE_EV;
        assert!((auto_exposure_target_lift(knee) + knee).abs() < 1e-5);
        assert!((auto_exposure_target_lift(0.0)).abs() < 1e-5);
        assert!((auto_exposure_target_lift(3.0) + 3.0).abs() < 1e-5);
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
