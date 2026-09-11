//! Mundo vivo (loop 9 do port simple-rpg) — o análogo nativo dos sistemas
//! de ambiente do VibeGame:
//!
//! - **Perspetiva aérea**: a câmara tem SEMPRE [`DistanceFog`], com a cor da
//!   hora ([`crate::worldsys::AtmosphereState`]) e inscattering do sol; a
//!   `<BiomeRegion>` só modula densidade e tinta.
//! - **Orçamento de PointLights**: só as 12 luzes mais próximas da câmara
//!   ficam acesas (o mundo tem 69 tochas/lanternas).
//! - **Gestos idle de NPC**: os NPCs de quest (sem script) tocam um clip de
//!   gesto (`talk`/`wave`/`call`) de vez em quando, se o rig tiver.
//! - **SFX espaciais mínimos**: eventos [`SfxEvent`] tocam WAVs curtos com
//!   volume por distância (hit/whoosh/harvest/ui) — `assets/audio/sfx/`.
//!
//! BGM por bioma adiado: as BiomeRegions do XML trazem todas
//! `bgm-layer="1"` (a mesma camada) — nada a trocar.

use std::collections::HashMap;

use bevy::prelude::*;
use bevy_kira_audio::AudioControl;

use crate::animation::CharacterAnimator;
use crate::luau::{LuaScriptRef, ScriptToast};
use crate::player::Player;

/// Número máximo de PointLights acesas em simultâneo.
pub const LIGHT_BUDGET: usize = 12;
/// Intervalo de refrescamento do orçamento de luzes (s).
pub const LIGHT_BUDGET_INTERVAL: f32 = 1.0;
/// Intervalo entre gestos idle de NPC (s, ±jitter).
pub const GESTURE_MIN_INTERVAL: f32 = 7.0;

// ── SFX ─────────────────────────────────────────────────────────────────

/// Clip de SFX curto.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SfxClip {
    Hit,
    Whoosh,
    Harvest,
    Ui,
    /// Golpe de machado em árvore (colheita nativa, `harvest.rs`).
    ChopHit,
    /// Árvore a cair (break `fall`).
    ChopBreak,
    /// Picareta na rocha.
    MineHit,
    /// Rocha a estilhaçar (break `shatter`/`burst`).
    MineBreak,
    // ── Passe de Juice (WS-A): clips partilhados com o WS-D ──────────────
    /// Subida de nível (jingle).
    LevelUp,
    /// Missão concluída (jingle).
    QuestDone,
    /// Viagem rápida (whoosh longo).
    Travel,
    /// Passo do herói em terra/erva (seco, ~0.15 s).
    Footstep,
    /// Passo do herói em água rasa (salpico, ~0.2 s).
    FootstepWater,
    /// Loot apanhado / baú aberto.
    Loot,
    // ── Passe de som completo (pool partilhada) ──────────────────────────
    /// Herói sofre dano (grunt de impacto).
    Hurt,
    /// Cura (brilho de recuperação).
    Heal,
    /// Herói morre (sting descendente).
    GameOver,
    /// Missão aceite (chime curto de confirmação).
    QuestAccept,
    /// Notificação suave (toast de sistema).
    Notification,
    /// Moedas a cair / ouro apanhado.
    Coin,
    /// Compra feita (transação do mercador).
    Buy,
    /// Acção recusada (sem ouro, inventário cheio…).
    Error,
    /// Jogo guardado.
    Save,
    /// Jogo carregado.
    Load,
    /// Loja aberta (sino acolhedor).
    ShopOpen,
    /// Criatura atingida (impacto de carne).
    EnemyHurt,
    /// Criatura morre (último suspiro).
    EnemyDeath,
    /// Lobo a agredir (growl).
    WolfGrowl,
    /// Slime (squish).
    SlimeSquish,
    /// Boss (roar).
    BossRoar,
    /// Guarda absorveu o golpe (metal na madeira).
    ShieldBlock,
    /// Porta a abrir.
    DoorOpen,
    /// Porta a fechar.
    DoorClose,
    /// Bomba lançada (fuso a arder).
    BombDrop,
    /// Herói salta.
    Jump,
    /// Dash do herói (rajada de ar).
    Dash,
}

/// Todos os clips, pela ordem do enum — alimenta o preload dos handles, o
/// audit (`analyze` valida a existência de cada ficheiro) e o test que
/// garante o registry Lua completo.
pub const SFX_CLIPS_ALL: &[SfxClip] = &[
    SfxClip::Hit,
    SfxClip::Whoosh,
    SfxClip::Harvest,
    SfxClip::Ui,
    SfxClip::ChopHit,
    SfxClip::ChopBreak,
    SfxClip::MineHit,
    SfxClip::MineBreak,
    SfxClip::LevelUp,
    SfxClip::QuestDone,
    SfxClip::Travel,
    SfxClip::Footstep,
    SfxClip::FootstepWater,
    SfxClip::Loot,
    SfxClip::Hurt,
    SfxClip::Heal,
    SfxClip::GameOver,
    SfxClip::QuestAccept,
    SfxClip::Notification,
    SfxClip::Coin,
    SfxClip::Buy,
    SfxClip::Error,
    SfxClip::Save,
    SfxClip::Load,
    SfxClip::ShopOpen,
    SfxClip::EnemyHurt,
    SfxClip::EnemyDeath,
    SfxClip::WolfGrowl,
    SfxClip::SlimeSquish,
    SfxClip::BossRoar,
    SfxClip::ShieldBlock,
    SfxClip::DoorOpen,
    SfxClip::DoorClose,
    SfxClip::BombDrop,
    SfxClip::Jump,
    SfxClip::Dash,
];

/// Loops ambientes carregados por path fixo (`setup_water_ambience`),
/// RELATIVOS ao `sfx_dir` do config.yaml do jogo — não são clips
/// `SfxEvent`; o audit valida a existência deles a partir daqui.
pub const AMBIENT_LOOP_FILES: &[&str] = &[
    "world/water_lake.ogg",
    "world/water_flow.ogg",
    "world/water_waterfall.ogg",
];

impl SfxClip {
    /// Clip RELATIVO ao `sfx_dir` do config.yaml do jogo
    /// (`config.sfx_path(clip.file())` dá o path AssetServer).
    pub fn file(self) -> &'static str {
        match self {
            SfxClip::Hit => "hit.ogg",
            SfxClip::Whoosh => "whoosh.ogg",
            SfxClip::Harvest => "harvest.ogg",
            SfxClip::Ui => "ui.ogg",
            SfxClip::ChopHit => "combat/chop_hit.ogg",
            SfxClip::ChopBreak => "combat/chop_break.ogg",
            SfxClip::MineHit => "combat/mine_hit.ogg",
            SfxClip::MineBreak => "combat/mine_break.ogg",
            SfxClip::LevelUp => "ui/levelup.ogg",
            SfxClip::QuestDone => "ui/quest_complete.ogg",
            SfxClip::Travel => "combat/swing.ogg",
            SfxClip::Footstep => "ambient/footstep.ogg",
            SfxClip::FootstepWater => "ambient/footstep_water.ogg",
            SfxClip::Loot => "world/chest_open.ogg",
            SfxClip::Hurt => "player/hurt.ogg",
            SfxClip::Heal => "player/heal.ogg",
            SfxClip::GameOver => "ui/game_over.ogg",
            SfxClip::QuestAccept => "ui/quest_accept.ogg",
            SfxClip::Notification => "ui/notification.ogg",
            SfxClip::Coin => "ui/coin.ogg",
            SfxClip::Buy => "ui/buy.ogg",
            SfxClip::Error => "ui/error.ogg",
            SfxClip::Save => "ui/save.ogg",
            SfxClip::Load => "ui/load.ogg",
            SfxClip::ShopOpen => "ui/shop_open.ogg",
            SfxClip::EnemyHurt => "creatures/enemy_hurt.ogg",
            SfxClip::EnemyDeath => "creatures/enemy_death.ogg",
            SfxClip::WolfGrowl => "creatures/wolf_growl.ogg",
            SfxClip::SlimeSquish => "creatures/slime_squish.ogg",
            SfxClip::BossRoar => "creatures/boss_roar.ogg",
            SfxClip::ShieldBlock => "combat/shield_block.ogg",
            SfxClip::DoorOpen => "world/door_open.ogg",
            SfxClip::DoorClose => "world/door_close.ogg",
            SfxClip::BombDrop => "world/bomb_drop.ogg",
            SfxClip::Jump => "player/jump.ogg",
            SfxClip::Dash => "player/dash.ogg",
        }
    }
}

/// Toca um SFX (volume cai com a distância ao oyente — a câmara).
#[derive(Debug, Clone, Copy, bevy::ecs::message::Message)]
pub struct SfxEvent {
    pub clip: SfxClip,
    /// Posição no mundo; `None` = som de interface (volume cheio).
    pub position: Option<Vec3>,
}

// ── ponto-em-polígono ───────────────────────────────────────────────────

/// Ray casting clássico: o ponto (x, z) está dentro do polígono?
pub fn point_in_polygon(x: f32, z: f32, polygon: &[[f32; 2]]) -> bool {
    let mut inside = false;
    let mut j = polygon.len().saturating_sub(1);
    for i in 0..polygon.len() {
        let (xi, zi) = (polygon[i][0], polygon[i][1]);
        let (xj, zj) = (polygon[j][0], polygon[j][1]);
        let crosses = (zi > z) != (zj > z);
        if crosses {
            let intersect_x = (xj - xi) * (z - zi) / (zj - zi + f32::EPSILON) + xi;
            if x < intersect_x {
                inside = !inside;
            }
        }
        j = i;
    }
    inside
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct AmbientPlugin;

impl Plugin for AmbientPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<CurrentBiome>()
            // GLUE da atmosfera (peça céu): [`crate::worldsys::AtmosphereState`]
            // é a paleta da hora partilhada por céu/fog/grading. Era escrita
            // por `atmosphere_drive` e lida por três sistemas — mas NINGUÉM a
            // inseria como recurso nem registava os sistemas, pelo que o fog,
            // o grading e o storage buffer do domo falhavam a validação de
            // params todos os frames (céu/fog congelados no default de dia).
            .init_resource::<crate::worldsys::AtmosphereState>()
            .add_message::<SfxEvent>()
            .add_systems(PostStartup, load_sfx_assets)
            .add_systems(
                Update,
                (
                    // A névoa lê a paleta publicada NO MESMO frame.
                    biome_fog_system.after(crate::worldsys::atmosphere_drive),
                    light_budget_system,
                    npc_gesture_system,
                    sfx_player_system,
                    setup_water_ambience,
                    water_ambience_driver,
                    setup_waterfall_ambience,
                    waterfall_ambience_driver,
                    // Chuva + lanterna (WS-A): a chuva lê a intensidade já
                    // rodada pelo scheduler. O MOVER+compensação corre antes
                    // do step dos emissores (main.rs) — as gotas compensam no
                    // mesmo frame em que a âncora anda.
                    rain_emitter_driver
                        .after(crate::worldsys::weather_drive)
                        .before(crate::particles::particle_emitter_update),
                    rain_ripple_spawner,
                    setup_rain_ambience,
                    rain_ambience_driver,
                    // A lanterna REESCREVE a Visibility por frame — tem de
                    // ganhar ao orçamento de luzes (que corre 1×/s).
                    lantern_driver.after(light_budget_system),
                ),
            )
            .add_systems(
                Update,
                (
                    // Scheduler do `<Weather cycle>` (WS-A): a intensidade
                    // contínua de chuva entra na paleta NO MESMO frame.
                    crate::worldsys::weather_drive.before(crate::worldsys::atmosphere_drive),
                    // Publica a paleta a partir da posição do sol já
                    // resolvida por `sun_drive` (main.rs)…
                    crate::worldsys::atmosphere_drive.after(crate::worldsys::sun_drive),
                    // …e empurra-a para o storage buffer do domo (`SkyUniform`)
                    // — é esta escrita por frame que faz o céu mudar com o
                    // `set_clock` (o relógio do mundo não é `globals.time`).
                    crate::sky::sky_material_drive.after(crate::worldsys::atmosphere_drive),
                    // O tint dia/noite do TERRENO (r5, bissecção da banda
                    // branca noturna) e o chão molhado (r3, canal `walls_b.w`)
                    // NÃO se registam aqui: vivem no `TerrainFeaturesPlugin`
                    // (src/terrain/runtime.rs), embrulhados em `timed` para
                    // aparecerem no profiler. Registá-los também aqui era uma
                    // DUPLICAÇÃO silenciosa — o `Timed` devolve o
                    // `system_type()` do sistema INTERIOR, portanto o Bevy vê o
                    // wrapper e a função crua como o mesmo sistema e o registo
                    // posterior sobrepõe o anterior: a versão que sobrevivia era
                    // a SEM `timed` e o sistema desaparecia do profiler.
                ),
            );
    }
}

// ── Perspetiva aérea: névoa de distância sempre ligada ─────────────────
//
// A `DistanceFog` só existia DENTRO de uma `<BiomeRegion>` e usava o tint do
// bioma como cor — ou seja: no vale (a vila, metade do jogo) não havia névoa
// nenhuma, e na floresta a névoa era verde-escura, o que ESCURECE o longe em
// vez de o afastar. Resultado: tudo lia à mesma distância, como maquete.
//
// Agora a névoa está sempre lá e a cor vem de [`AtmosphereState`] — a mesma
// paleta do céu. O que está longe funde-se com o horizonte, exatamente o que
// separa camadas de serra nas referências. O bioma deixa de ser a fonte da
// cor e passa a ser um MODULADOR: densidade (o pântano fecha, o deserto abre)
// e um empurrão leve de tinta (só 25% — mais do que isso e volta o
// verde-escuro).
//
// `directional_light_color` liga o inscattering do sol: a metade do mundo
// virada ao sol fica dourada ao pôr-do-sol, que é a razão pela qual as
// paredes deixam de ser cinzentas às 19:00.

/// Distância (m) a que a névoa atinge ~63% com densidade base. Abaixo disto
/// a imagem fica intacta — a legibilidade de jogo não paga a atmosfera.
pub const FOG_REFERENCE_M: f32 = 340.0;
/// Densidade base (falloff quadrático): imperceptível a 100 m, véu claro a
/// 200 m, camadas separadas a 400 m, horizonte fundido a 800 m.
pub const FOG_BASE_DENSITY: f32 = 1.0 / FOG_REFERENCE_M;
/// Peso do tint do bioma na cor da névoa.
const BIOME_TINT_WEIGHT: f32 = 0.25;

/// Bioma atual do herói (id da região, ou `None` = vale central).
#[derive(Debug, Clone, Resource, Default)]
pub struct CurrentBiome {
    pub id: Option<String>,
}

/// Converte a `fog-density` exponencial do XML num multiplicador sobre a
/// densidade base.
///
/// Os mundos autoraram 0.0014–0.0046 para `FogFalloff::Exponential`; o
/// falloff quadrático usa outra escala, por isso o valor do XML entra como
/// razão face à mediana autoral (0.0028) e é limitado — um bioma nunca pode
/// cegar o jogador nem apagar a atmosfera por completo.
pub fn fog_density_multiplier(xml_density: f32) -> f32 {
    if !xml_density.is_finite() || xml_density <= 0.0 {
        return 1.0;
    }
    (xml_density / 0.0028).clamp(0.55, 1.9)
}

/// Mistura linear entre duas cores RGB.
fn mix_rgb(a: [f32; 3], b: [f32; 3], t: f32) -> [f32; 3] {
    let t = t.clamp(0.0, 1.0);
    [
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    ]
}

#[allow(clippy::too_many_arguments)]
fn biome_fog_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    players: Query<&GlobalTransform, With<Player>>,
    biomes: Option<Res<crate::worldsys::BiomeRegions>>,
    scene: Option<Res<crate::worldsys::InteriorSceneConfig>>,
    mut current: ResMut<CurrentBiome>,
    mut cameras: Query<Entity, With<Camera3d>>,
    mut commands: Commands,
    mut toasts: MessageWriter<ScriptToast>,
    // Estado suavizado da transição de bioma (mult, tint, peso do tint).
    mut biome_blend: Local<Option<(f32, [f32; 3], f32)>>,
) {
    // 4×/s: a paleta muda devagar (o dia inteiro dura 20 min reais) e a
    // névoa é um componente inserido, não um uniform barato.
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.25;

    // Dentro da bolsa de interior não há bioma: a cena está declaradamente
    // FORA do mundo (a 2 km do mapa) e a única razão para a `BiomeRegion` do
    // vale se aplicar lá era a posição no plano. Sem isto a sala herda a
    // névoa, a tinta e a exposição de um bioma onde não está — o exterior
    // pinta-se por cima do interior.
    let inside_scene = scene
        .as_deref()
        .zip(players.iter().next())
        .is_some_and(|(scene, player)| {
            let pos = player.translation();
            scene.contains(pos.x, pos.z)
        });
    let region = if inside_scene {
        None
    } else {
        match (biomes, players.iter().next()) {
            (Some(biomes), Some(player)) => {
                let pos = player.translation();
                biomes
                    .list
                    .iter()
                    .find(|b| point_in_polygon(pos.x, pos.z, &b.polygon))
                    .cloned()
            }
            _ => None,
        }
    };

    // Toast só na TRANSIÇÃO (o sistema agora corre sempre, não só ao entrar).
    let next_id = region.as_ref().map(|r| r.id.clone());
    if next_id != current.id {
        match &next_id {
            Some(id) => {
                toasts.write(ScriptToast(format!("Entraste em: {id}")));
            }
            None => {
                if current.id.is_some() {
                    toasts.write(ScriptToast("De volta ao vale.".into()));
                }
            }
        }
        current.id = next_id;
    }

    let (density_mult, tint) = match &region {
        Some(r) => (fog_density_multiplier(r.fog_density), r.tint),
        None => (1.0, None),
    };

    // ── Transição SUAVE de bioma ──────────────────────────────────────────
    // A paleta por região aplicava-se de GOLPE ao cruzar o polígono — a
    // "linha diagonal" que cortava o ecrã de um lado ao outro. O alvo agora
    // persegue-se por lerp (~0.9 s a 4 Hz): o toast anuncia, o olho não vê
    // o corte. Primeira chamada: snap ao alvo.
    let target_mult = density_mult;
    let target_tint = tint.unwrap_or([1.0, 1.0, 1.0]);
    let target_weight = if tint.is_some() { 1.0 } else { 0.0 };
    let blend = biome_blend
        .get_or_insert((target_mult, target_tint, target_weight));
    const BLEND_K: f32 = 0.35;
    blend.0 += (target_mult - blend.0) * BLEND_K;
    for c in 0..3 {
        blend.1[c] += (target_tint[c] - blend.1[c]) * BLEND_K;
    }
    blend.2 += (target_weight - blend.2) * BLEND_K;
    let (smooth_mult, smooth_tint, smooth_weight) = *blend;

    // Cor: horizonte da hora + um toque do bioma, na MESMA escala do domo
    // (paleta raw — o r7 multiplicava por SKY_RADIANCE 400 e o fog ficava
    // branco estourado, r8 limitava a luminância). O cap 0.8×horizonte
    // mantém-se: silhuetas de serra lêem-se contra um fundo mais claro.
    let mut color = [atmosphere.fog[0], atmosphere.fog[1], atmosphere.fog[2]];
    if smooth_weight > 0.01 {
        color = mix_rgb(color, smooth_tint, BIOME_TINT_WEIGHT * smooth_weight);
    }
    let horizon_sky = [
        atmosphere.horizon[0],
        atmosphere.horizon[1],
        atmosphere.horizon[2],
    ];
    let lum = |c: [f32; 3]| 0.25 * c[0] + 0.5 * c[1] + 0.25 * c[2];
    let cap = lum(horizon_sky) * 0.8;
    let fog_lum = lum(color);
    if fog_lum > cap && fog_lum > 0.0 {
        let k = cap / fog_lum;
        color = [color[0] * k, color[1] * k, color[2] * k];
    }
    // À noite a névoa não pode "acender" o mundo: é o azul profundo que
    // engole o longe e deixa as fogueiras a valer ouro. Mas a névoa é
    // VISIBILIDADE: os multiplicadores de noite/chuva ficaram SUTIS de
    // propósito — eram +35 %/+60 %, que no pântano à chuva apagavam a
    // paisagem a ~90 m ("chuva com noite não se vê nada, fica tudo cinza
    // branco", repro do user 2026-09-11). A base
    // ([`FOG_BASE_DENSITY`]) e o multiplicador do bioma continuam a mandar;
    // noite e chuva só dão um toque.
    let rain = weather.map(|w| w.rain.clamp(0.0, 1.0)).unwrap_or(0.0);
    let density =
        FOG_BASE_DENSITY * smooth_mult * (1.0 + 0.08 * atmosphere.night) * (1.0 + 0.12 * rain);

    // Inscattering direcional — a escala NÃO é arbitrária, e errá-la lava o
    // frame inteiro de branco (r1/r2).
    //
    // O `pbr_functions.wgsl` calcula
    //   `scattering = pow(dot(view, sun), exponent) * light.color * view.exposure`
    // e `light.color` de uma direcional é `cor × ILLUMINANCE` — 10 000 lux no
    // default do Bevy. Com `view.exposure ≈ 1/(2^EV100 × 1.2)` (≈ 1e-3 a
    // EV100 9.7) o pico de `scattering` anda por ~10 unidades. Depois
    //   `fog_color = base_color + scattering × directional_light_color`
    // — ou seja, um `directional_light_color` de 0.5 somava **5.0** a uma cor
    // de névoa que vive em 0..1. Era isso que punha o mundo inteiro branco
    // no meio-dia.
    //
    // Aqui pede-se o EFEITO (quanto o glow soma à cor da névoa) e converte-se
    // para o valor do componente, compensando a exposição da hora.
    const SCATTER_AT_PEAK: f32 = 10.0;
    let inscatter = atmosphere.sun_tint;
    let target_add = (0.10 + 0.34 * atmosphere.golden) * atmosphere.day;
    let inscatter_strength =
        (target_add / (SCATTER_AT_PEAK * atmosphere.exposure_scale.max(0.1))).clamp(0.0, 0.08);

    let fog = DistanceFog {
        color: Color::LinearRgba(bevy::color::LinearRgba::rgb(color[0], color[1], color[2])),
        directional_light_color: Color::LinearRgba(bevy::color::LinearRgba::rgb(
            inscatter[0] * inscatter_strength,
            inscatter[1] * inscatter_strength,
            inscatter[2] * inscatter_strength,
        )),
        // Lobe de ~45°: largo o bastante para a metade do mundo virada ao
        // sol aquecer na hora dourada, estreito o bastante para não ser um
        // véu global (que é indistinguível de "subir a exposição").
        directional_light_exponent: 14.0,
        // Quadrático: a imagem perto fica intacta e o longe funde-se rápido —
        // é o perfil que dá "camadas" em vez de um véu uniforme.
        falloff: FogFalloff::ExponentialSquared { density },
    };
    for camera in &mut cameras {
        commands.entity(camera).insert(fog.clone());
    }
}

use bevy::camera::Camera3d;
use bevy::pbr::{DistanceFog, FogFalloff};

// ── orçamento de PointLights ────────────────────────────────────────────

/// Só as [`LIGHT_BUDGET`] luzes mais próximas da câmara ficam visíveis.
#[allow(clippy::type_complexity)]
fn light_budget_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    cameras: Query<&GlobalTransform, With<Camera3d>>,
    lights: Query<(Entity, &GlobalTransform), (With<PointLight>, Without<Camera3d>)>,
    mut visibilities: Query<&mut Visibility, With<PointLight>>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = LIGHT_BUDGET_INTERVAL;
    // `iter().next()` e não `single()`: com ≥2 câmaras o `single()` falhava
    // (mesma semântica do 1.º player do `music_driver`).
    let Some(cam) = cameras.iter().next() else {
        return;
    };
    let cam_pos = cam.translation();
    let mut by_distance: Vec<(Entity, f32)> = lights
        .iter()
        .map(|(entity, t)| (entity, t.translation().distance_squared(cam_pos)))
        .collect();
    by_distance.sort_by(|a, b| a.1.total_cmp(&b.1));
    for (i, (entity, _)) in by_distance.iter().enumerate() {
        if let Ok(mut visibility) = visibilities.get_mut(*entity) {
            let wanted = if i < LIGHT_BUDGET {
                Visibility::Visible
            } else {
                Visibility::Hidden
            };
            if *visibility != wanted {
                *visibility = wanted;
            }
        }
    }
}

// ── gestos idle de NPC ──────────────────────────────────────────────────

/// NPCs (sem script, sem player) tocam um clip de gesto periodicamente.
///
/// O gesto é uma ACÇÃO one-shot: toca uma vez, tranca o driver de locomoção
/// enquanto dura e devolve o rig no fim. Antes chamava `transitions.play` sem
/// `repeat` e sem qualquer regresso ao idle — o NPC congelava na última pose do
/// aceno para sempre.
#[allow(clippy::type_complexity)]
fn npc_gesture_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    mut npcs: Query<
        (Entity, &mut CharacterAnimator),
        (With<Name>, Without<Player>, Without<LuaScriptRef>),
    >,
    mut animation_players: crate::animation::PlayerQuery,
    mut timers: Local<HashMap<Entity, f32>>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    // Tempo REAL desde a última passada (1 s + overshoot do frame): o `-1.0`
    // fixo contava um segundo por passada mesmo com frames longos.
    let elapsed = 1.0 - *throttle;
    *throttle = 1.0;
    // Timers de NPCs despawnados saem do map — o Local crescia para sempre
    // (cada respawn de criatura/NPC leakava uma entrada).
    timers.retain(|entity, _| npcs.contains(*entity));
    for (entity, mut animator) in &mut npcs {
        let entry = timers.entry(entity).or_insert_with(|| {
            // primeiro gesto entre 5 e 12 s (pseudo-aleatório por entidade)
            5.0 + (entity.to_bits() % 7000) as f32 / 1000.0
        });
        *entry -= elapsed;
        if *entry > 0.0 {
            continue;
        }
        *entry = GESTURE_MIN_INTERVAL + (entity.to_bits() % 5000) as f32 / 1000.0;
        // Um NPC a andar (ou a meio de outra acção) não é interrompido — o
        // gesto é decoração de quem está parado.
        if animator.is_busy() || animator.state != Some(crate::animation::AnimState::Idle) {
            continue;
        }
        let Some(node) = animator
            .node_matching(|n| n.contains("talk") || n.contains("wave") || n.contains("call"))
        else {
            continue;
        };
        crate::animation::play_action(
            &mut animator,
            &mut animation_players,
            node,
            std::time::Duration::from_millis(250),
            false,
        );
    }
}

// ── SFX ─────────────────────────────────────────────────────────────────

/// Handles pré-carregados dos clips — um map `SfxClip → handle` construído
/// de [`SFX_CLIPS_ALL`]; um clip novo no enum entra no preload sozinho. O
/// asset type é o [`bevy_kira_audio::AudioSource`] (decode symphonia), NÃO
/// o `bevy_audio::AudioSource` do backend nativo.
#[derive(Debug, Clone, Resource, Default)]
pub struct SfxHandles {
    clips: HashMap<SfxClip, Handle<bevy_kira_audio::AudioSource>>,
}

impl SfxHandles {
    /// Pré-carrega TODOS os clips (PostStartup) — o `.ogg` falhado só
    /// produz warn de load e o play é no-op silencioso. Os paths vêm do
    /// `sfx_dir` do config.yaml do jogo.
    pub fn load(server: &AssetServer, config: &crate::config::GameConfig) -> Self {
        Self {
            clips: SFX_CLIPS_ALL
                .iter()
                .map(|clip| (*clip, server.load(config.sfx_path(clip.file()))))
                .collect(),
        }
    }

    pub fn get(&self, clip: SfxClip) -> Option<Handle<bevy_kira_audio::AudioSource>> {
        self.clips.get(&clip).cloned()
    }
}

fn load_sfx_assets(mut commands: Commands, server: Res<AssetServer>, config: Res<crate::config::GameConfig>) {
    commands.insert_resource(SfxHandles::load(&server, &config));
}

/// Toca o clip com volume por distância (Ativos de áudio são globais; a
/// atenuação é calculada no momento do evento). Os buses `sfx` e `master`
/// do `AudioMixer` multiplicam o resultado — sem eles os sliders eram
/// no-op audível para os efeitos.
/// Clips tocados em rajada (cada golpe/step) levam um jitter de pitch
/// ±8 % — mata a monotonia da repetição sem samples extra. LCG local
/// (numerical recipes): barato, sem depender de crate de RNG.
fn pitch_jitter(clip: SfxClip, rng: &mut u32) -> f32 {
    const JITTERED: &[SfxClip] = &[
        SfxClip::Hit,
        SfxClip::Whoosh,
        SfxClip::EnemyHurt,
        SfxClip::Footstep,
        SfxClip::FootstepWater,
        SfxClip::Coin,
    ];
    if !JITTERED.contains(&clip) {
        return 1.0;
    }
    *rng = rng.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
    0.92 + ((*rng >> 16) as f32 / u16::MAX as f32) * 0.16
}

fn sfx_player_system(
    mut events: MessageReader<SfxEvent>,
    handles: Option<Res<SfxHandles>>,
    listeners: Query<&GlobalTransform, With<Camera3d>>,
    mixer: Option<Res<crate::music::AudioMixerSettings>>,
    sfx: Option<Res<bevy_kira_audio::AudioChannel<crate::music::SfxBus>>>,
    mut rng: Local<u32>,
) {
    let (Some(handles), Some(sfx)) = (handles, sfx) else {
        return;
    };
    // Mudo total nem toca (o bus estaria a −60 dB na mesma — poupança de CPU).
    if let Some(mixer) = mixer {
        if (mixer.sfx * mixer.master) <= 0.0 {
            return;
        }
    }
    for event in events.read() {
        let Some(handle) = handles.get(event.clip) else {
            continue;
        };
        // Atenuação por distância no momento do evento; os buses sfx×master
        // são aplicados pelo kira (crate::music::mixer_sync) — um som a meio
        // responde a mudanças de volume, coisa que o modelo antigo (bus
        // multiplicado no spawn) não fazia.
        let volume = match event.position {
            Some(pos) => {
                let distance = listeners
                    .iter()
                    .next()
                    .map(|cam| cam.translation().distance(pos))
                    .unwrap_or(0.0);
                (1.0 - distance / 40.0).clamp(0.05, 1.0)
            }
            None => 0.6,
        };
        sfx.play(handle)
            .with_volume(crate::music::linear_to_db(volume))
            .with_playback_rate(pitch_jitter(event.clip, &mut rng) as f64);
    }
}

// ── Água ambiente ───────────────────────────────────────────────────────
//
// Os clips `water_lake.ogg` / `water_flow.ogg` existiam no pool e não tinham
// consumidor: a margem era cega e surda. Dois loops permanentes (um por tipo
// de corpo) nascem quando o terreno publica `TerrainRuntime`; o driver move
// o volume deles para a distância À LINHA DE ÁGUA (a mesma métrica do
// splatter/spawner) — fade out a `WATER_AUDIBLE_RADIUS` m.

/// Loop de água ativo (lago OU rio).
#[derive(Debug, Component)]
pub struct WaterAmbienceLoop {
    /// `true` = o loop de rio (water_flow); `false` = lago (water_lake).
    pub river: bool,
}

/// Distância (m) além da qual o loop está em silêncio.
const WATER_AUDIBLE_RADIUS: f32 = 26.0;
/// Ganho máximo junto à água (antes dos buses do mixer).
const WATER_MAX_GAIN: f32 = 0.5;

/// Spawna UMA vez os dois loops quando o runtime do terreno existe. Os
/// paths resolvem contra o `sfx_dir` do config.yaml do jogo.
fn setup_water_ambience(
    mut commands: Commands,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    spawned: Query<(), With<WaterAmbienceLoop>>,
    config: Option<Res<crate::config::GameConfig>>,
) {
    if runtime.is_none() || !spawned.is_empty() {
        return;
    }
    let Some(config) = config else {
        return;
    };
    for (river, clip) in [
        (false, "world/water_lake.ogg"),
        (true, "world/water_flow.ogg"),
    ] {
        commands.spawn((
            WaterAmbienceLoop { river },
            crate::music::AudioLoopPending {
                url: config.sfx_path(clip),
                music: false,
            },
        ));
    }
}

/// Move o volume dos loops para o alvo da distância à linha de água.
fn water_ambience_driver(
    time: Res<Time>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    mut loops: Query<
        (&WaterAmbienceLoop, &crate::music::LoopInstance, &mut crate::music::LoopVolume),
    >,
    mut instances: ResMut<Assets<bevy_kira_audio::AudioInstance>>,
) {
    // `iter().next()` e não `single()`: ≥2 players não pode matar o áudio.
    let (Some(runtime), Some(player)) = (runtime, players.iter().next()) else {
        return;
    };
    let pos = player.translation();
    let mut best = [f32::MAX; 2]; // [lake, river]
    for body in &runtime.water {
        let d = body
            .distance_to_waterline(bevy::math::Vec2::new(pos.x, pos.z))
            .max(0.0);
        let slot = match body.kind {
            crate::terrain::WaterKind::Lake => 0,
            crate::terrain::WaterKind::River => 1,
        };
        best[slot] = best[slot].min(d);
    }
    let dt = time.delta_secs().clamp(0.0, 0.2);
    for (tag, instance, mut volume) in &mut loops {
        let d = if tag.river { best[1] } else { best[0] };
        let falloff = (1.0 - d / WATER_AUDIBLE_RADIUS).clamp(0.0, 1.0);
        // Curva quadrática — perto da água o som cresce depressa. O bus
        // (sfx×master) é do canal kira, não entra aqui.
        let target = WATER_MAX_GAIN * falloff * falloff;
        let next = crate::music::fade_step(volume.0, target, dt, 0.5);
        if next != volume.0 {
            let Some(mut snd) = instances.get_mut(&instance.0) else {
                continue;
            };
            snd.set_decibels(
                crate::music::linear_to_db(next),
                bevy_kira_audio::AudioTween::linear(std::time::Duration::from_millis(60)),
            );
            volume.0 = next;
        }
    }
}

// ── Cachoeiras (loops posicionais) ──────────────────────────────────────
//
// Diferente dos loops globais de água: UMA entidade por queda grande
// (`WaterBody.cascades` com `waterfall`), volume por distância ao
// caldeirão, raio audível e ganho crescendo com a queda — água grande
// ouve-se de longe.

/// Loop posicional de cachoeira (uma entidade por queda).
#[derive(Debug, Component)]
pub struct WaterfallLoop {
    /// Base da queda (world XZ) — o caldeirão.
    pub at: bevy::math::Vec2,
    /// Queda (m) — escala o raio audível e o ganho.
    pub drop: f32,
}

/// Distância (m) base além da qual a cachoeira está em silêncio; cresce
/// `3 m` por metro de queda.
const WATERFALL_AUDIBLE_RADIUS: f32 = 34.0;
/// Ganho junto a uma queda de 3 m, antes dos buses do mixer (cresce com a
/// queda, teto de segurança).
const WATERFALL_MAX_GAIN: f32 = 0.55;

/// Spawna UMA vez um loop por cachoeira registada no runtime do terreno.
fn setup_waterfall_ambience(
    mut commands: Commands,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    spawned: Query<(), With<WaterfallLoop>>,
    config: Option<Res<crate::config::GameConfig>>,
) {
    let Some(runtime) = runtime else {
        return;
    };
    if !spawned.is_empty() {
        return;
    }
    for body in &runtime.water {
        if body.kind != crate::terrain::WaterKind::River {
            continue;
        }
        for c in &body.cascades {
            if !c.waterfall {
                continue;
            }
            let Some(st) = body.stations.get(c.base) else {
                continue;
            };
            commands.spawn((
                WaterfallLoop {
                    at: *st,
                    drop: c.drop,
                },
                crate::music::AudioLoopPending {
                    url: config
                        .as_deref()
                        .map(|c| c.sfx_path("world/water_waterfall.ogg"))
                        .unwrap_or_else(|| "world/water_waterfall.ogg".to_string()),
                    music: false,
                },
            ));
        }
    }
}

/// Move o volume de cada loop de cachoeira para o alvo da distância ao
/// caldeirão (mesma curva quadrática dos loops globais de água).
fn waterfall_ambience_driver(
    time: Res<Time>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    mut loops: Query<(&WaterfallLoop, &crate::music::LoopInstance, &mut crate::music::LoopVolume)>,
    mut instances: ResMut<Assets<bevy_kira_audio::AudioInstance>>,
) {
    if loops.is_empty() {
        return;
    }
    // `iter().next()` e não `single()`: ≥2 players não pode matar o áudio.
    let Some(player) = players.iter().next() else {
        return;
    };
    let pos = player.translation();
    let dt = time.delta_secs().clamp(0.0, 0.2);
    for (tag, instance, mut volume) in &mut loops {
        let d = (bevy::math::Vec2::new(pos.x, pos.z) - tag.at).length();
        let radius = WATERFALL_AUDIBLE_RADIUS + tag.drop * 3.0;
        let falloff = (1.0 - d / radius).clamp(0.0, 1.0);
        let gain = (WATERFALL_MAX_GAIN * (0.5 + tag.drop * 0.12)).min(0.95);
        // Curva quadrática — perto da queda o som cresce depressa. O bus
        // (sfx×master) é do canal kira, não entra aqui.
        let target = gain * falloff * falloff;
        let next = crate::music::fade_step(volume.0, target, dt, 0.5);
        if next != volume.0 {
            let Some(mut snd) = instances.get_mut(&instance.0) else {
                continue;
            };
            snd.set_decibels(
                crate::music::linear_to_db(next),
                bevy_kira_audio::AudioTween::linear(std::time::Duration::from_millis(60)),
            );
            volume.0 = next;
        }
    }
}

// ── Chuva viva (WS-A) ───────────────────────────────────────────────────
// Três peças que partilham a intensidade CONTÍNUA do `<Weather>`:
//
// 1. **Emissor âncora** — UM emissor do preset `rain` (+12 m acima do herói)
//    que o segue frame a frame; cobre sempre o sítio certo num mundo de
//    4 km sem encher o ECS de emissores. As gotas vivem em espaço LOCAL do
//    emissor, pelo que cada movimento do herói é COMPENSADO (as gotas ficam
//    fixas ao mundo enquanto ele anda). Rate ∝ intensidade; 0 = escondido.
// 2. **Ondinhas** — bursts `rain_ripple` no chão à volta do herói, rate ∝
//    intensidade (o chão molhado lê-se).
// 3. **Loop SFX** — `ambient/rain_loop.ogg` (8 s costura-sem-clique, gerado
//    por `scripts/synth_rain.py`) com volume ∝ intensidade.

/// Altura do emissor de chuva acima da cabeça do herói (m).
pub const RAIN_EMITTER_HEIGHT: f32 = 12.0;
/// Rate do emissor de chuva com intensidade 1 (partículas/s).
pub const RAIN_MAX_RATE: f32 = 500.0;
/// Ondinhas por segundo no chão com intensidade 1.
const RAIN_RIPPLES_PER_SEC: f32 = 14.0;
/// Raio (m) dentro do qual as ondinhas nascem.
const RAIN_RIPPLE_RADIUS: f32 = 8.0;
/// Ganho máximo do loop de chuva (antes dos buses do mixer).
const RAIN_MAX_GAIN: f32 = 0.4;

/// Marker do emissor de chuva âncora do herói (uma instância por mundo).
/// A posição âncora vive no próprio `Transform` (a compensação do espaço
/// local das gotas compara com ela a cada frame).
#[derive(Debug, Component)]
pub struct RainEmitter;

/// Marker do loop de áudio da chuva.
#[derive(Debug, Component)]
pub struct RainAmbienceLoop;

fn rain_spec() -> crate::recipes::ParticleSpec {
    crate::recipes::ParticleSpec {
        preset: "rain".into(),
        emission_rate: None,
        life: None,
        speed: None,
        size: None,
        color: None,
        shape_radius: None,
        looping: true,
        world_space: false,
    }
}

fn ripple_spec() -> crate::recipes::ParticleSpec {
    crate::recipes::ParticleSpec {
        preset: "rain_ripple".into(),
        emission_rate: None,
        life: None,
        speed: None,
        size: None,
        color: None,
        shape_radius: None,
        looping: false,
        world_space: false,
    }
}

/// Spawna (uma vez) e conduz o emissor de chuva âncora do herói.
#[allow(clippy::type_complexity)]
fn rain_emitter_driver(
    mut commands: Commands,
    scene: Option<Res<crate::worldsys::InteriorSceneConfig>>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut emitters: Query<
        (
            &mut Transform,
            &mut crate::particles::ParticleEmitter,
            &mut Visibility,
        ),
        With<RainEmitter>,
    >,
) {
    let Some(weather) = weather else {
        return;
    };
    // `iter().next()` e não `single()`: ≥2 players não pode matar a chuva.
    let Some(player) = players.iter().next() else {
        return;
    };
    let anchor = player.translation() + Vec3::Y * RAIN_EMITTER_HEIGHT;
    // Telhado implícito: dentro da bolsa de interior não chove — o emissor
    // segue o player e seguiria para dentro da sala.
    let intensity = if scene
        .as_deref()
        .is_some_and(|s| s.contains(anchor.x, anchor.z))
    {
        0.0
    } else {
        weather.rain.clamp(0.0, 1.0)
    };
    let Ok((mut transform, mut emitter, mut visibility)) = emitters.single_mut() else {
        // Ainda não existe: spawna UM. O `Commands::queue` só dá `&mut World`
        // — daí o `spawn_looping_in_world`.
        commands.queue(move |world: &mut World| {
            let entity = crate::particles::spawn_looping_in_world(world, &rain_spec(), anchor);
            world.entity_mut(entity).insert(RainEmitter);
        });
        return;
    };
    // Compensa o espaço LOCAL: gotas já a meio da queda ficam fixas ao mundo.
    let delta = anchor - transform.translation;
    if delta != Vec3::ZERO {
        for particle in &mut emitter.sim.particles {
            particle.pos -= delta;
        }
    }
    transform.translation = anchor;
    // Rate ∝ intensidade; sem chuva o emissor desliga E esconde-se.
    emitter.sim.resolved.emission_rate = RAIN_MAX_RATE * intensity;
    let wanted = if intensity <= 0.01 {
        Visibility::Hidden
    } else {
        Visibility::Inherited
    };
    if *visibility != wanted {
        *visibility = wanted;
    }
}

/// Estado acumulado do spawner de ondinhas (rng próprio = sem lockstep com
/// os emissores).
struct RainRippleState {
    accumulator: f32,
    rng: crate::spawner::Rng,
}

impl Default for RainRippleState {
    fn default() -> Self {
        Self {
            accumulator: 0.0,
            rng: crate::spawner::Rng::new(0x51A1_BEEF),
        }
    }
}

/// Ondinhas `rain_ripple` no chão à volta do herói enquanto chove.
#[allow(clippy::needless_pass_by_value, clippy::too_many_arguments)]
fn rain_ripple_spawner(
    time: Res<Time>,
    mut state: Local<RainRippleState>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    players: Query<&GlobalTransform, With<Player>>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut commands: Commands,
) {
    let Some(weather) = weather else {
        return;
    };
    let intensity = weather.rain.clamp(0.0, 1.0);
    if intensity <= 0.05 {
        return;
    }
    let Some(player) = players.iter().next() else {
        return;
    };
    let origin = player.translation();
    state.accumulator += RAIN_RIPPLES_PER_SEC * intensity * time.delta_secs();
    if state.accumulator < 1.0 {
        return;
    }
    state.accumulator -= state.accumulator.floor();
    for _ in 0..2 {
        let disc = state.rng.unit_disc() * RAIN_RIPPLE_RADIUS;
        let x = origin.x + disc.x;
        let z = origin.z + disc.y;
        let y = runtime
            .as_ref()
            .map(|r| r.sample(x, z))
            .unwrap_or(origin.y - RAIN_EMITTER_HEIGHT);
        crate::particles::spawn_burst(
            &mut commands,
            &ripple_spec(),
            Vec3::new(x, y + 0.03, z),
            2,
        );
    }
}

/// Spawna UMA vez o loop de áudio da chuva (volume 0 até o driver o levantar).
fn setup_rain_ambience(
    mut commands: Commands,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    spawned: Query<(), With<RainAmbienceLoop>>,
    config: Option<Res<crate::config::GameConfig>>,
) {
    if weather.is_none() || !spawned.is_empty() {
        return;
    }
    commands.spawn((
        RainAmbienceLoop,
        crate::music::AudioLoopPending {
            url: config
                .as_deref()
                .map(|c| c.sfx_path("ambient/rain_loop.ogg"))
                .unwrap_or_else(|| "ambient/rain_loop.ogg".to_string()),
            music: false,
        },
    ));
}

/// Volume do loop de chuva ∝ intensidade (fade lento — a chuva não liga/desliga).
fn rain_ambience_driver(
    time: Res<Time>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    mut loops: Query<
        (&crate::music::LoopInstance, &mut crate::music::LoopVolume),
        With<RainAmbienceLoop>,
    >,
    mut instances: ResMut<Assets<bevy_kira_audio::AudioInstance>>,
) {
    let Some(weather) = weather else {
        return;
    };
    let intensity = weather.rain.clamp(0.0, 1.0);
    // O bus (sfx×master) é do canal kira, não entra aqui.
    let target = RAIN_MAX_GAIN * intensity;
    let dt = time.delta_secs().clamp(0.0, 0.2);
    for (instance, mut volume) in &mut loops {
        let next = crate::music::fade_step(volume.0, target, dt, 0.35);
        if next != volume.0 {
            let Some(mut snd) = instances.get_mut(&instance.0) else {
                continue;
            };
            snd.set_decibels(
                crate::music::linear_to_db(next),
                bevy_kira_audio::AudioTween::linear(std::time::Duration::from_millis(60)),
            );
            volume.0 = next;
        }
    }
}

// ── Lanterna do viajante (WS-A) ─────────────────────────────────────────
//
// Uma PointLight QUENTE presa ao herói que só existe à noite: é o que mantém
// o caminho de casa legível quando o sol desce. SEM sombras — o orçamento de
// luzes com sombras não é tocado; como está sempre a <2 m do herói, ocupa um
// único lugar no orçamento das 12 mais próximas.

/// Intensidade da lanterna à noite (lm — ~uma tocha do mundo).
pub const LANTERN_MAX_INTENSITY: f32 = 1200.0;
/// Alcance da lanterna (m).
pub const LANTERN_RANGE: f32 = 9.0;
/// Altura da chama acima do chão do herói (m).
const LANTERN_HEIGHT: f32 = 2.2;

/// Marker da lanterna do viajante (uma instância por mundo).
#[derive(Debug, Component)]
pub struct TravellerLantern;

/// Spawna (uma vez) e conduz a lanterna: segue o herói, intensidade ∝ noite
/// com fade exponencial suave (~0.4 s de constante — sem piscar).
#[allow(clippy::type_complexity)]
fn lantern_driver(
    time: Res<Time>,
    atmosphere: Option<Res<crate::worldsys::AtmosphereState>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut lanterns: Query<(&mut Transform, &mut PointLight, &mut Visibility), With<TravellerLantern>>,
    mut commands: Commands,
) {
    // `iter().next()` e não `single()`: ≥2 players não pode matar a lanterna.
    let Some(player) = players.iter().next() else {
        return;
    };
    let anchor = player.translation() + Vec3::Y * LANTERN_HEIGHT;
    let night = atmosphere.map(|a| a.night.clamp(0.0, 1.0)).unwrap_or(0.0);
    let target = LANTERN_MAX_INTENSITY * night;
    let Ok((mut transform, mut light, mut visibility)) = lanterns.single_mut() else {
        commands.spawn((
            TravellerLantern,
            PointLight {
                color: Color::srgb(1.0, 0.69, 0.40), // #ffb066 quente
                intensity: 0.0,
                range: LANTERN_RANGE,
                ..Default::default()
            },
            Transform::from_translation(anchor),
            Visibility::Inherited,
            Name::new("fx:lanterna-viajante"),
        ));
        return;
    };
    transform.translation = anchor;
    // Fade exponencial: constante de tempo ~0.4 s em ambos os sentidos.
    let dt = time.delta_secs().clamp(0.0, 0.2);
    let blend = 1.0 - (-dt * 2.5).exp();
    light.intensity += (target - light.intensity) * blend;
    let wanted = if light.intensity < LANTERN_MAX_INTENSITY * 0.02 {
        Visibility::Hidden
    } else {
        Visibility::Inherited
    };
    if *visibility != wanted {
        *visibility = wanted;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_point_in_polygon_square() {
        let square = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]];
        assert!(point_in_polygon(5.0, 5.0, &square));
        assert!(!point_in_polygon(15.0, 5.0, &square));
        assert!(!point_in_polygon(-1.0, -1.0, &square));
    }

    #[test]
    fn test_point_in_polygon_wedge() {
        // cunha do bioma norte (estilo environment.xml)
        let wedge = [
            [-56.0, 56.0],
            [56.0, 56.0],
            [4040.0, 4040.0],
            [-4040.0, 4040.0],
        ];
        assert!(point_in_polygon(0.0, 300.0, &wedge), "norte profundo");
        assert!(!point_in_polygon(0.0, 0.0, &wedge), "praça fora");
    }

    #[test]
    fn test_sfx_clip_files() {
        assert!(SfxClip::Hit.file().starts_with(""));
        // core 4: os stubs .wav sintéticos foram substituídos por clips
        // Text2Sound (.ogg) — nada na engine pode apontar a .wav.
        assert!(SfxClip::Ui.file().ends_with("ui.ogg"));
        for clip in SFX_CLIPS_ALL {
            assert!(clip.file().ends_with(".ogg"), "{} não é .ogg", clip.file());
        }
        // colheita nativa: 4 clips .ogg partilhados com a referência
        for clip in [
            SfxClip::ChopHit,
            SfxClip::ChopBreak,
            SfxClip::MineHit,
            SfxClip::MineBreak,
        ] {
            assert!(clip.file().contains("combat/"), "{}", clip.file());
        }
        // passe de juice (WS-A): os clips novos têm de ser .ogg e apontar a
        // pastas reais da árvore de assets (ui/, combat/, world/, ambient/).
        let juice = [
            (SfxClip::LevelUp, "ui/levelup.ogg"),
            (SfxClip::QuestDone, "ui/quest_complete.ogg"),
            (SfxClip::Travel, "combat/swing.ogg"),
            (SfxClip::Footstep, "ambient/footstep.ogg"),
            (SfxClip::FootstepWater, "ambient/footstep_water.ogg"),
            (SfxClip::Loot, "world/chest_open.ogg"),
        ];
        for (clip, suffix) in juice {
            assert!(clip.file().ends_with(suffix), "{}", clip.file());
        }
    }

    /// O registry Lua tem de cobrir TODAS as variantes — um clip sem nome
    /// seria inalcançável por script e invisível para o audit.
    #[test]
    fn test_lua_registry_covers_all_clips() {
        for clip in SFX_CLIPS_ALL {
            assert!(
                crate::luau::SFX_NAME_REGISTRY
                    .iter()
                    .any(|(_, c)| c == clip),
                "clip {:?} sem nome no SFX_NAME_REGISTRY",
                clip
            );
        }
        // e o mapeamento inverso resolve (case-insensitive).
        for (name, clip) in crate::luau::SFX_NAME_REGISTRY {
            assert_eq!(crate::luau::sfx_clip_from_str(name), Some(*clip));
        }
    }

    /// Os OGGs do passe de juice existem no POOL partilhado — a única árvore
    /// de assets que a engine carrega (docs/ASSETS.md; sem espelho por
    /// exemplo, o teste deixa de exigir cópias redundantes).
    #[test]
    fn test_juice_audio_assets_exist() {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        for rel in [
            "examples/shared-assets/public/assets/audio/sfx/ambient/rain_loop.ogg",
            "examples/shared-assets/public/assets/audio/sfx/ambient/footstep.ogg",
            "examples/shared-assets/public/assets/audio/sfx/ambient/footstep_water.ogg",
            "examples/shared-assets/public/assets/audio/sfx/ui/levelup.ogg",
            "examples/shared-assets/public/assets/audio/sfx/ui/quest_complete.ogg",
            "examples/shared-assets/public/assets/audio/sfx/combat/swing.ogg",
            "examples/shared-assets/public/assets/audio/sfx/world/chest_open.ogg",
        ] {
            let path = manifest.join(rel);
            assert!(path.is_file(), "falta {}", path.display());
        }
    }

    /// A chuva fecha o horizonte: densidade ×(1+0.6·rain) na névoa da câmara.
    #[test]
    fn test_biome_fog_thickens_with_rain() {
        fn fog_density(rain: f32, night: f32) -> f32 {
            let mut app = bevy::app::App::new();
            app.add_plugins(bevy::MinimalPlugins);
            app.add_message::<ScriptToast>();
            app.insert_resource(CurrentBiome::default());
            app.insert_resource(crate::worldsys::AtmosphereState {
                night,
                ..Default::default()
            });
            app.insert_resource(crate::worldsys::WeatherState {
                wind: [0.0, 0.0],
                wind_strength: 0.0,
                clouds: 0.0,
                rain,
                cycle: false,
            });
            app.add_systems(bevy::app::Update, biome_fog_system);
            app.world_mut()
                .spawn((Player::default(), GlobalTransform::from_xyz(0.0, 0.0, 0.0)));
            app.world_mut().spawn((Camera3d::default(),));
            app.update();
            let camera = app
                .world_mut()
                .query_filtered::<&DistanceFog, With<Camera3d>>()
                .single(app.world())
                .expect("a névoa foi inserida na câmara")
                .clone();
            match camera.falloff {
                FogFalloff::ExponentialSquared { density } => density,
                other => panic!("falloff inesperado: {other:?}"),
            }
        }
        // A névoa é VISIBILIDADE: noite e chuva dão só um toque (eram +35 % e
        // +60 %, que apagavam a paisagem a ~90 m no pântano à chuva).
        let dry = fog_density(0.0, 0.0);
        let wet = fog_density(1.0, 0.0);
        assert!(
            (wet / dry - 1.12).abs() < 1e-4,
            "chuva cheia engrossa a névoa ×1.12 (subtil): {wet} vs {dry}"
        );
        let night = fog_density(0.0, 1.0);
        assert!(
            (night / dry - 1.08).abs() < 1e-4,
            "noite engrossa a névoa ×1.08 (subtil): {night} vs {dry}"
        );
    }

    #[test]
    fn test_region_format() {
        // REGIONS do travel expostas em travel.rs — aqui só smoke do catálogo
        assert_eq!(crate::travel::LANDMARKS.len(), 12);
    }
}
