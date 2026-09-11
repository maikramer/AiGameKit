//! Combate corpo-a-corpo — port do pipeline de melee do VibeGame
//! (`examples/simple-rpg/src/game/melee.ts` + `combat-mechanics.ts`).
//!
//! O press de ataque ([J]/clique esquerdo) NÃO acerta nada: agenda um
//! [`PendingSwing`] com o impacto a ~35 % do clip de ataque (a 1.4×), com
//! soft-lock no inimigo vivo mais próximo num círculo de [`LOCK_RANGE`] e
//! lunge em direção ao alvo durante o windup. No instante do impacto
//! (`swing_track_system`) o golpe CLEAVE tudo num arco à frente (hemisfério
//! frontal; finisher alarga para ~140°), aplica chain (×1/×1.15/×1.7 no 3.º),
//! bónus de combo (+2 %/hit, cap 30 %), backstab ×2, crítico ×2 e execute
//! (<15 % HP cai na hora), com knockback, hit-stop, camera shake, partículas
//! e números de dano.
//!
//! Duas contagens distintas (igual ao VibeGame):
//! - **Chain** ([`AttackChain`]): avança POR PRESS, janela curta
//!   ([`CHAIN_WINDOW`]); o passo 2 é o finisher.
//! - **Combo counter** ([`crate::skills::ComboState`]): conta HITS ACERTADOS
//!   (janela [`COMBO_WINDOW_SECS`]) e dá bónus de dano; reseta quando o herói
//!   leva dano.
//!
//! O golpe e a morte entram como ACÇÕES one-shot em
//! [`crate::animation::play_action`]: tomam o rig pelo tempo exacto do clip e
//! devolvem-no ao driver de locomoção sozinhas.

use bevy::input::mouse::MouseButton;
use bevy::prelude::*;
use bevy::time::{Real, Virtual};

use crate::animation::{
    ACTION_BLEND, ACTION_INTERRUPT_BLEND, CharacterAnimator, PlayerQuery, play_action,
    play_action_scaled,
};
use crate::luau::{LuaScriptRef, ScriptInteraction, ScriptToast};
use crate::player::Player;
use crate::profiler::{Group, timed};
use crate::vitals::{Health, Xp, apply_damage};

/// Alcance do golpe corpo-a-corpo (m) — VibeGame `MELEE_RANGE` 3.0.
pub const MELEE_RANGE: f32 = 3.0;
/// Alcance do finisher (m) — +0.5 de cleave.
pub const FINISHER_RANGE: f32 = 3.5;
/// Círculo de soft-lock no press (m) — `LOCK_RANGE` = range + 0.6.
pub const LOCK_RANGE: f32 = 3.6;
/// Dano base do golpe do herói — VibeGame `BASE_MELEE_DAMAGE` 16 sobre HPs de
/// Viber (25 de histórico do port); mantém o TTK do mundo.
pub const MELEE_DAMAGE: f32 = 25.0;
/// Cadência de swings (s) — VibeGame `SWING_COOLDOWN` 0.36.
pub const MELEE_COOLDOWN: f32 = 0.36;
/// Diferença de altura aceitável (m) — `MELEE_VERTICAL` (sem hits através de
/// pisos).
pub const MELEE_VERTICAL: f32 = 2.5;
/// Cone frontal do golpe: dot ≥ 0 = hemisfério frontal (~90° cada lado).
pub const ATTACK_ARC_DOT: f32 = 0.0;
/// Finisher: dot ≥ cos(70°) ≈ 0.342 — arco de ~140° de cleave.
pub const FINISHER_ARC_DOT: f32 = 0.342;
/// Fração do clip em que o corte aterra — `SWING_IMPACT_FRACTION` 0.35.
/// É o FALLBACK de [`impact_fraction_for`] (clip sem entrada na tabela).
pub const SWING_IMPACT_FRACTION: f32 = 0.35;
/// Tabela de timing de impacto por clip: (substring, fração do clip). Clipes
/// diferentes têm windups diferentes; o match é por substring case-insensitive
/// e entradas mais específicas vêm primeiro (`sworda` antes de `sword`).
pub const IMPACT_FRACTIONS: [(&str, f32); 5] = [
    ("sworda", 0.30), // 1.º corte: windup curto, aterra cedo
    ("swordb", 0.38), // 2.º corte: mais peso
    ("swordc", 0.42), // 3.º corte: mais pesado da sequência
    ("sword", 0.34),  // corte genérico da espada/machado
    ("spear", 0.40),  // estocada: o braço recua antes do golpe
];
/// Impacto sem duração de clip conhecida (s).
pub const FALLBACK_IMPACT_DELAY: f32 = 0.22;
/// Whoosh toca isto antes do impacto (s).
pub const WHOOSH_LEAD: f32 = 0.12;
/// Velocidade de playback do clip de ataque (±jitter 8 %).
pub const ATTACK_TIME_SCALE: f32 = 1.4;
/// Lunge: distância de paragem em relação ao alvo (m).
pub const LUNGE_STANDOFF: f32 = 1.2;
/// Lunge: começa a avançar a partir desta distância (m).
pub const STRIKE_DISTANCE: f32 = 1.9;
/// Lunge: avanço máximo por swing (m).
pub const MAX_LUNGE: f32 = 1.6;
/// Cap de velocidade do lunge (m/s).
pub const LUNGE_SPEED_CAP: f32 = 9.0;
/// Viragem para o alvo durante o windup (rad/s — VibeGame VISUAL_TURN_RATE).
/// 14: o herói aponta ao alvo num quarto de segundo em vez de meio —
/// "apontar para ele" tem de ler no frame do press.
pub const FACE_TURN_RATE: f32 = 14.0;
/// Buffer de press durante o cooldown (s): mash [J] dispara o golpe em
/// atraso assim que a cadência deixa, em vez de engolir o press.
pub const ATTACK_BUFFER: f32 = 0.25;

/// Chain de golpes por press — `CHAIN_WINDOW` 1.1 s, mults [1, 1.15, 1.7].
pub const CHAIN_WINDOW: f32 = 1.1;
pub const CHAIN_MULTS: [f32; 3] = [1.0, 1.15, 1.7];
/// Multiplicador do combo counter por hit — 2 %/hit.
pub const COMBO_BONUS_PER_HIT: f32 = 0.02;
/// Cap do bónus de combo (+30 %).
pub const COMBO_BONUS_CAP: f32 = 0.3;
/// Janela do combo counter (s) — VibeGame 2.5.
pub const COMBO_WINDOW_SECS: f32 = 2.5;
/// Chance base de crítico (VibeGame 0.15) + passivas de precisão.
pub const CRIT_CHANCE: f32 = 0.15;
/// Multiplicador de crítico.
pub const CRIT_MULTIPLIER: f32 = 2.0;
/// Execute: alvo abaixo desta fração de HP cai na hora.
pub const EXECUTE_HP_FRAC: f32 = 0.15;
/// XP por abate.
pub const KILL_XP: u32 = 15;
/// Segundos que o cadáver dura (animação de morte) antes de sumir.
pub const CORPSE_LIFETIME: f32 = 1.4;
/// Hit-stop por classe de golpe (s @ escala [`HIT_STOP_SCALE`]) — amplificado
/// (0.07/0.11/0.12 não lia): a pausa É a sensação de impacto.
pub const HIT_STOP_NORMAL: f32 = 0.09;
pub const HIT_STOP_HEAVY: f32 = 0.13;
pub const HIT_STOP_KILL: f32 = 0.16;
/// Escala do mundo durante o hit-stop — 4 %: congelado a olhar.
pub const HIT_STOP_SCALE: f32 = 0.04;
/// Pesos de camera shake (trauma 0..1): golpe / finisher / crítico / abate.
/// Amplificados (os antigos 0.22–0.5 tremiam pouco: amplitude = trauma²).
pub const SHAKE_HIT: f32 = 0.30;
pub const SHAKE_FINISHER: f32 = 0.48;
pub const SHAKE_CRIT: f32 = 0.52;
pub const SHAKE_KILL: f32 = 0.68;
/// Escala de shake pelo COMBO: +trauma por hit na janela (cap) — o combo
/// acende. Soma a [`SHAKE_HIT`] etc.
pub const COMBO_SHAKE_PER_HIT: f32 = 0.02;
pub const COMBO_SHAKE_CAP: f32 = 0.08;
/// Knockback base em metros de deslocamento (crit 1.5, finisher 1.3) — um
/// pouco acima do antigo 0.8/1.3/1.1: "recua levemente" tem de se ver.
pub const KNOCKBACK_METERS: f32 = 1.0;
pub const KNOCKBACK_CRIT_METERS: f32 = 1.5;
pub const KNOCKBACK_FINISHER_METERS: f32 = 1.3;

// ── solavanco direcional + punch de pós-processo por classe de golpe ──
/// Impulso do kick de câmara na direção da mira (m/s; mola em `camera.rs`).
pub const KICK_HIT: f32 = 1.2;
pub const KICK_CRIT: f32 = 2.0;
pub const KICK_KILL: f32 = 2.6;
pub const KICK_FINISHER: f32 = 2.2;
/// Componente vertical do kick (a imagem "salta" com o golpe).
pub const KICK_UP: f32 = 0.3;
/// Punch de pós-processo (stops de clareza + bloom): hit normal.
pub const PUNCH_HIT_STOPS: f32 = 0.25;
pub const PUNCH_HIT_BLOOM: f32 = 0.10;
/// Punch do crítico/finisher.
pub const PUNCH_HEAVY_STOPS: f32 = 0.5;
pub const PUNCH_HEAVY_BLOOM: f32 = 0.2;
/// Punch do abate — o frame do KO clareia e floresce.
pub const PUNCH_KILL_STOPS: f32 = 0.7;
pub const PUNCH_KILL_BLOOM: f32 = 0.28;
/// FOV kick do finisher/abate (graus; o hit normal não precisa).
pub const FOV_KICK_HEAVY: f32 = 3.0;
/// Raio do anel de choque no ABATE (m) — o KO marca o lugar no chão.
pub const KILL_RING_RADIUS: f32 = 2.2;
/// Raio do anel de choque do finisher aterra (m) — o slam da chain.
pub const FINISHER_RING_RADIUS: f32 = 3.2;

/// Pool de clips de ataque por arma (índice em [`WEAPON_TABLE`]) — o herói
/// tem os clips do pack Quaternius; espada e machado partilham o pool de
/// 4 cortes (igual ao VibeGame `ATTACK_POOLS`), a lança tem o seu.
pub const ATTACK_POOLS: [&[&str]; 3] = [
    &["sword", "sworda", "swordb", "swordc"],
    &["sword", "sworda", "swordb", "swordc"],
    &["spear"],
];

/// Combate morto: anima `death` e é removido após [`CORPSE_LIFETIME`].
#[derive(Debug, Clone, Component)]
pub struct Corpse {
    pub timer: f32,
}

/// Materiais CLONADOS do cadáver + Y de base para o sink. Os GLBs partilham
/// materiais entre instâncias — o fade toca só nos clones (mesmo padrão
/// clone-and-keep do hit-flash, com o mesmo cap
/// [`crate::feedback::MAX_FLASH_MATS`]).
#[derive(Debug, Component)]
pub struct CorpseFade {
    pub materials: Vec<Handle<StandardMaterial>>,
    pub base_y: f32,
}

/// Sink do cadáver ao longo do TTL (m) — desaparece PARA O SOLO em vez de um
/// pop de despawn a seco.
pub const CORPSE_SINK_METERS: f32 = 0.3;

/// Alpha do cadáver para um progresso 0..1 do TTL — linear de 1 a 0
/// (monotónico decrescente; clampado fora do intervalo).
pub fn corpse_fade_alpha(progress: f32) -> f32 {
    1.0 - progress.clamp(0.0, 1.0)
}

/// Offset vertical do cadáver para um progresso 0..1 do TTL — linear de 0 a
/// [`CORPSE_SINK_METERS`] (negativo: afunda).
pub fn corpse_sink_offset(progress: f32) -> f32 {
    -CORPSE_SINK_METERS * progress.clamp(0.0, 1.0)
}

/// Relógio do último swing (timestamp do golpe mais recente do herói).
///
/// A animação do golpe já NÃO depende disto — é uma acção one-shot que se
/// devolve sozinha (`crate::animation::play_action`). Fica como sinal de
/// gameplay (feedback/telemetria) para quem precise do instante do swing.
#[derive(Debug, Default, Resource)]
pub struct SwingClock(pub Option<f64>);

/// Chain de ataque por press: passo 0..2 com janela [`CHAIN_WINDOW`].
#[derive(Debug, Clone, Resource, Default)]
pub struct AttackChain {
    pub step: u32,
    pub timer: f32,
    /// Índice do último clip tocado no pool (para não repetir).
    pub last_clip: Option<usize>,
    /// Timestamp do último swing (elapsed da engine, s) — a cadência.
    pub last_swing: f64,
    /// Até quando o clip de ataque atual "possui" o rig (elapsed, s) — um
    /// press dentro disto interrompe um swing e pede blend mais longo.
    pub anim_until: f64,
}

/// Swing agendado: o press cria, `swing_track_system` conduz até ao impacto.
#[derive(Debug, Clone, Resource, Default)]
pub struct PendingSwing {
    /// Swing em curso.
    pub active: bool,
    /// Wall-clock virtual até ao golpe (s).
    pub delay: f32,
    /// Whoosh toca quando chega a 0 (s).
    pub sound_in: f32,
    pub sound_fired: bool,
    /// Direção de mira XZ (re-apontada ao alvo vivo por frame).
    pub aim: Vec3,
    pub target: Option<Entity>,
    /// Passo da chain deste swing (0..2) — fixado no press.
    pub chain: u32,
    /// Avanço pendente (m).
    pub lunge_left: f32,
    /// Finisher (passo 2): range/arc alargados + shockwave.
    pub finisher: bool,
}

/// Press guardado durante o cooldown ([`ATTACK_BUFFER`]) — mash fluido.
#[derive(Debug, Clone, Resource, Default)]
pub struct AttackBuffer {
    pub timer: f32,
}

/// Hit-stop em curso (tempo REAL; o resto do mundo corre a
/// [`HIT_STOP_SCALE`] da velocidade).
#[derive(Debug, Clone, Resource, Default)]
pub struct HitStop {
    pub timer: f32,
}

/// Velocidade base do tempo virtual, definida FORA do hit-stop (ex.: o
/// slow-mo do debug bridge via `viber.debug.set_time_scale`). O
/// [`hit_stop_system`] compõe `relative_speed = base × hit_stop_scale`
/// enquanto há hit-stop e não escreve nada fora dele — assim a base escolhida
/// por outra via persiste (antes, o hit-stop repunha 1.0 todos os frames e
/// estompava-a).
#[derive(Debug, Clone, Resource)]
pub struct BaseTimeScale(pub f32);

impl Default for BaseTimeScale {
    fn default() -> Self {
        Self(1.0)
    }
}

/// Agenda um hit-stop; pedidos sobrepostos mantêm o mais longo.
pub fn request_hit_stop(stop: &mut HitStop, duration: f32) {
    if duration > 0.0 {
        stop.timer = stop.timer.max(duration);
    }
}

/// Escala do tempo virtual durante o hit-stop (0 = tempo normal).
pub fn hit_stop_scale(timer: f32) -> f32 {
    if timer > 0.0 { HIT_STOP_SCALE } else { 1.0 }
}

/// Multiplicador da chain para um passo (0..2, clampeado).
pub fn chain_multiplier(step: u32) -> f32 {
    CHAIN_MULTS[(step as usize).min(CHAIN_MULTS.len() - 1)]
}

/// Fração do clip em que o impacto aterra para o clip dado — match por
/// substring case-insensitive sobre [`IMPACT_FRACTIONS`]; desconhecidos caem
/// no fallback [`SWING_IMPACT_FRACTION`].
pub fn impact_fraction_for(clip_name: &str) -> f32 {
    let name = clip_name.to_ascii_lowercase();
    IMPACT_FRACTIONS
        .iter()
        .find(|(needle, _)| name.contains(needle))
        .map_or(SWING_IMPACT_FRACTION, |(_, fraction)| *fraction)
}

/// Bónus do combo counter para `hits` acertados (+2 %/hit, cap 30 %).
pub fn combo_bonus(hits: u32) -> f32 {
    1.0 + (hits as f32 * COMBO_BONUS_PER_HIT).min(COMBO_BONUS_CAP)
}

/// Escolhe o clip do pool evitando repetir o último (modo `random` do
/// VibeGame: uniforme sem repetição imediata). `roll` ∈ [0,1).
pub fn pick_combo_clip(pool: &[&str], last: Option<usize>, roll: f32) -> usize {
    if pool.is_empty() {
        return 0;
    }
    let roll = roll.clamp(0.0, 0.999_999);
    let mut idx = (roll * pool.len() as f32) as usize % pool.len();
    if Some(idx) == last {
        idx = (idx + 1) % pool.len();
    }
    idx
}

/// Caminho para a frente do herói — CONVENÇÃO DA PIPELINE: o modelo olha
/// +Z (`player::facing_rotation`), não o −Z do `GlobalTransform::forward()`.
/// O melee antigo comparava o cone contra o −Z (as COSTAS do modelo) — golpes
/// acertavam só com o inimigo atrás.
pub fn hero_forward(transform: &GlobalTransform) -> Vec3 {
    (transform.rotation() * Vec3::Z)
        .with_y(0.0)
        .normalize_or_zero()
}

pub struct CombatPlugin;

impl bevy::app::Plugin for CombatPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<ButtonInput<MouseButton>>();
        app.init_resource::<SwingClock>();
        app.init_resource::<HeldWeapon>();
        app.init_resource::<AttackChain>();
        app.init_resource::<AttackBuffer>();
        app.init_resource::<PendingSwing>();
        app.init_resource::<HitStop>();
        app.init_resource::<BaseTimeScale>();
        // O melee e a fireball consultam `MenusOpen` (nasce no MenusPlugin na
        // app completa; aqui, idempotente, para apps mínimas — o teste
        // headless deixava de ter de o inserir à mão).
        app.init_resource::<crate::menus::MenusOpen>();
        app.init_resource::<crate::trail::TrailWindow>();
        // swing_track (shake no impacto) precisa do trauma em qualquer app.
        app.init_resource::<crate::camera::CameraShake>();
        // FOV kick (land/kill do melee, dash): o recurso e o sistema vivem em
        // `camera.rs`; registados aqui porque o CombatPlugin já bootstrap-a o
        // FX de câmara para apps mínimas.
        app.init_resource::<crate::camera::CameraFx>();
        app.add_systems(Update, crate::camera::fov_kick_system);
        // Combo counter do melee — nasce no SkillsPlugin na app completa;
        // aqui (idempotente) para apps mínimas.
        app.init_resource::<crate::skills::ComboState>();
        // Números de dano que o melee/fireball emitem (idempotente com o
        // FeedbackPlugin) + soft-lock do alvo + toasts (kill_creature) —
        // auto-suficiente em apps mínimas (testes headless).
        app.add_message::<crate::feedback::DamageNumberEvent>();
        app.add_message::<ScriptToast>();
        app.add_message::<crate::feedback::AttackAlert>();
        app.add_message::<crate::ambient::SfxEvent>();
        // Contexto da colheita nativa (o real nasce no HarvestPlugin; o
        // default aqui mantém o melee válido em apps mínimas).
        app.init_resource::<crate::harvest::HarvestContext>();
        app.init_resource::<crate::feedback::CombatTarget>();
        app.add_systems(
            Update,
            (ensure_player_vitals, ensure_creature_vitals, cycle_weapon),
        );
        app.add_systems(Update, timed(Group::Combat, player_melee_attack));
        // R2-G9: ambos escrevem o Transform do herói — o doc do estádio 2
        // promete DEPOIS de `player_movement` (o lunge não disputa a
        // locomoção) e ANTES da câmara third-person (que segue o herói).
        // Sem a aresta, a ordem real ficava à sorte do agendador.
        app.add_systems(
            Update,
            swing_track_system
                .after(crate::player::player_movement)
                .before(crate::camera::third_person_camera),
        );
        app.add_systems(Update, timed(Group::Combat, hit_stop_system));
        app.add_systems(
            Update,
            (
                timed(Group::Combat, cast_fireball),
                timed(Group::Combat, fireball_step),
                play_death_animation,
                tick_corpses,
                corpse_fade_setup,
                corpse_fade_tick,
            ),
        );
    }
}

/// Garante vitals no herói (o HUD e o dano dos scripts dependem disto).
#[allow(clippy::type_complexity)]
pub fn ensure_player_vitals(
    players: Query<(Entity, Option<&Health>, Option<&Xp>), With<Player>>,
    mut commands: Commands,
) {
    for (entity, health, xp) in &players {
        let mut entity = commands.entity(entity);
        if health.is_none() {
            entity.insert(Health::default());
        }
        if xp.is_none() {
            entity.insert(Xp::default());
        }
    }
}

/// HP autoral dos chefes (`bosses/…`): com o default de 100 morriam num
/// chain de melee (~2 s). Valores por script path, cohordados com a
/// progression (herói ~25-40 de dano por golpe): o ogre final é o muro.
pub const BOSS_HP_OGRE: f32 = 1200.0;
pub const BOSS_HP_BOG_WARDEN: f32 = 600.0;
pub const BOSS_HP_SAND_WORM: f32 = 500.0;
pub const BOSS_HP_WITCH: f32 = 400.0;

/// HP de nascença para um scriptado hostil: HP autoral dos bosses
/// ([`BOSS_HP_OGRE`]…), [`crate::vitals::DEFAULT_HEALTH`] para o resto.
/// Aceita `\` ou `/` como separador (mesmo contrato de [`is_hostile_script`]).
pub fn authored_max_hp(script_path: &str) -> f32 {
    let tail = script_path.replace('\\', "/");
    let Some(file) = tail.strip_prefix("bosses/") else {
        return crate::vitals::DEFAULT_HEALTH;
    };
    match file {
        "boss.lua" => BOSS_HP_OGRE,
        "bog-warden.lua" => BOSS_HP_BOG_WARDEN,
        "sand-worm.lua" => BOSS_HP_SAND_WORM,
        "witch.lua" => BOSS_HP_WITCH,
        _ => crate::vitals::DEFAULT_HEALTH,
    }
}

/// Hostil = script em `enemies/`/`bosses/` (mesmo predicado de
/// `quests.rs`). Só hostis recebem Vitals: todas as vias de dano (melee,
/// strike, bomba, fireball) exigem `Health`, logo townsfolk/colhíveis/POIs
/// ficam imunes — antes, os 3 townsfolk da praça eram mortáveis a 4 golpes
/// e despawnavam para sempre com +15 XP.
pub fn is_hostile_script(path: &str) -> bool {
    // Igual a `path.replace('\\', "/")` (os separadores aceites) sem alocar
    // por entidade por frame: cada prefixo testado nos dois separadores.
    path.starts_with("enemies/")
        || path.starts_with("enemies\\")
        || path.starts_with("bosses/")
        || path.starts_with("bosses\\")
}

#[allow(clippy::type_complexity)]
pub fn ensure_creature_vitals(
    creatures: Query<
        (&LuaScriptRef, Entity),
        (
            With<LuaScriptRef>,
            Without<ScriptInteraction>,
            Without<Health>,
            Without<Player>,
        ),
    >,
    mut commands: Commands,
) {
    for (script, entity) in &creatures {
        if is_hostile_script(&script.path) {
            let hp = authored_max_hp(&script.path);
            commands.entity(entity).insert(Health {
                current: hp,
                max: hp,
            });
        }
    }
}

/// Parâmetros partilhados dos dois estádios do melee (o sistema passa de 16
/// parâmetros).
#[derive(bevy::ecs::system::SystemParam)]
pub struct MeleeFx<'w, 's> {
    commands: Commands<'w, 's>,
    toasts: bevy::ecs::message::MessageWriter<'w, ScriptToast>,
    numbers: bevy::ecs::message::MessageWriter<'w, crate::feedback::DamageNumberEvent>,
    alerts: bevy::ecs::message::MessageWriter<'w, crate::feedback::AttackAlert>,
    sfx: bevy::ecs::message::MessageWriter<'w, crate::ambient::SfxEvent>,
    combat_target: ResMut<'w, crate::feedback::CombatTarget>,
    // Música de combate: cada golpe no hostil renova a layer battle/boss.
    combat_music: ResMut<'w, crate::music::CombatMusicState>,
    time: Res<'w, Time>,
    quests_log: Option<ResMut<'w, crate::quests::QuestLog>>,
    combo: ResMut<'w, crate::skills::ComboState>,
    stats: Res<'w, crate::skills::PlayerStatsResource>,
    // Estado do swing (o Bevy limita sistemas a 16 params; estes vivem aqui).
    chain: ResMut<'w, AttackChain>,
    pending: ResMut<'w, PendingSwing>,
    swing_clock: ResMut<'w, SwingClock>,
    trail_window: ResMut<'w, crate::trail::TrailWindow>,
}

/// ESTÁDIO 1 do melee (por press): agenda o swing. NADA de detecção de acerto
/// aqui — o impacto aterra a [`SWING_IMPACT_FRACTION`] do clip (a 1.4×), com
/// re-aim/lunge por frame em [`swing_track_system`] (o `pendingMelee` do
/// VibeGame). O soft-lock procura o inimigo vivo mais próximo num CÍRCULO
/// completo de [`LOCK_RANGE`] — o windup vira o herói para ele, por isso o
/// press não precisa de mirar.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn player_melee_attack(
    keys: Res<ButtonInput<KeyCode>>,
    mouse: Res<ButtonInput<MouseButton>>,
    menus: Res<crate::menus::MenusOpen>,
    // Contexto da colheita nativa (harvest.rs) — suprime o melee com alvo.
    harvest: Res<crate::harvest::HarvestContext>,
    harvest_targets: Query<(&GlobalTransform, &ScriptInteraction), Without<Player>>,
    time: Res<Time>,
    mut last_hp: Local<Option<f32>>,
    mut buffer: ResMut<AttackBuffer>,
    mut fx: MeleeFx,
    players: Query<(Entity, &GlobalTransform, &mut Transform, Option<&Health>), With<Player>>,
    mut hero_animator: Query<&mut CharacterAnimator, With<Player>>,
    mut animation_players: PlayerQuery,
    held: Res<HeldWeapon>,
    enemies: Query<
        (Entity, &GlobalTransform, &Health, Option<&Corpse>),
        (Without<Player>, With<LuaScriptRef>),
    >,
) {
    // Janelas correm SEMPRE (mesmo com menu aberto, o combo morre à mesma).
    if buffer.timer > 0.0 {
        buffer.timer = (buffer.timer - time.delta_secs()).max(0.0);
    }
    fx.combo.window -= time.delta_secs();
    if fx.combo.window <= 0.0 && fx.combo.hits > 0 {
        fx.combo.hits = 0;
    }
    fx.chain.timer -= time.delta_secs();
    if fx.chain.timer <= 0.0 && fx.chain.step != 0 {
        fx.chain.step = 0;
    }
    // Combo counter reseta quando o herói leva dano (notifyPlayerDamaged).
    if let Ok((_, _, _, Some(health))) = players.single() {
        if last_hp.is_some_and(|prev| health.current < prev) {
            fx.combo.hits = 0;
            fx.combo.window = 0.0;
        }
        *last_hp = Some(health.current);
    }
    // Menu aberto consome [J]: confirmava compra/teleporte E desencadeava
    // o melee por trás.
    if menus.any() {
        return;
    }
    let j_pressed = keys.just_pressed(KeyCode::KeyJ);
    // [R] é o golpe forte radial (skills.rs) — o básico fica em [J] + clique
    // esquerdo. Um press guardado no buffer durante o cooldown também conta
    // como triggered — sem isto o mash [J] morria no buffer sem nunca
    // disparar (o timer expirava e o input perdia-se).
    let triggered = mouse.just_pressed(MouseButton::Left) || j_pressed || buffer.timer > 0.0;
    if !triggered {
        return;
    }
    // Supressão colheita-mele: com alvo de colheita NATIVO (harvest.rs) o
    // press inteiro (J/clique) pertence à colheita — antes de qualquer
    // buffer/chain, o melee nunca co-dispara com a picareta/machadinha.
    if harvest.target.is_some() {
        return;
    }
    // J contextual: se há alvo de colheita (ScriptInteraction com tecla J) no
    // alcance, o golpe vai para a coleta — o script do alvo cuida do resto.
    if j_pressed {
        if let Ok((_, player, _, _)) = players.single() {
            let origin = player.translation();
            let near_harvest = harvest_targets.iter().any(|(t, interaction)| {
                interaction.key == KeyCode::KeyJ
                    && t.translation().distance(origin) <= interaction.range.min(3.5)
            });
            if near_harvest {
                return;
            }
        }
    }
    let elapsed = time.elapsed_secs_f64();
    let can_fire =
        fx.chain.last_swing <= 0.0 || elapsed - fx.chain.last_swing >= MELEE_COOLDOWN as f64;
    if !can_fire {
        // Mash: guarda o press — dispara sozinho quando a cadência deixa.
        buffer.timer = ATTACK_BUFFER;
        return;
    }
    buffer.timer = 0.0;
    fx.chain.last_swing = elapsed;
    let Ok((_, player, _, _)) = players.single() else {
        return;
    };
    let origin = player.translation();
    let forward = hero_forward(player);

    // Soft-lock: inimigo vivo mais próximo num círculo COMPLETO (sem cone —
    // o windup re-aponta). Igual ao `LOCK_RANGE` do melee.ts.
    let mut best: Option<(Entity, f32)> = None;
    for (entity, transform, health, corpse) in &enemies {
        if corpse.is_some() || health.current <= 0.0 {
            continue;
        }
        let to_target = transform.translation() - origin;
        let dist = to_target.length();
        if dist > LOCK_RANGE || dist < 1e-3 {
            continue;
        }
        if (transform.translation().y - origin.y).abs() > MELEE_VERTICAL {
            continue;
        }
        if best.map(|(_, d)| dist < d).unwrap_or(true) {
            best = Some((entity, dist));
        }
    }
    let target = best.map(|(e, _)| e);

    // ── chain por press ──
    fx.chain.step = if fx.chain.timer > 0.0 {
        (fx.chain.step + 1).min((CHAIN_MULTS.len() - 1) as u32)
    } else {
        0
    };
    fx.chain.timer = CHAIN_WINDOW;
    let finisher = fx.chain.step >= 2;

    // ── clip do pool (random sem repetir) + playback a 1.4× ±8 % ──
    fx.swing_clock.0 = Some(elapsed);
    let pool = ATTACK_POOLS[held.idx.min(ATTACK_POOLS.len() - 1)];
    let clip_idx = pick_combo_clip(pool, fx.chain.last_clip, pseudo_roll(&time));
    fx.chain.last_clip = Some(clip_idx);
    let clip_name = pool.get(clip_idx).copied().unwrap_or("attack");
    let attack_speed = ATTACK_TIME_SCALE * (0.92 + 0.16 * pseudo_roll(&time));
    // Mash: um press DENTRO do clip anterior interrompe o swing — crossfade
    // mais longo para os dois cortes interpolarem (leitura fluida).
    let blend = if elapsed < fx.chain.anim_until {
        ACTION_INTERRUPT_BLEND
    } else {
        ACTION_BLEND
    };
    let mut clip_duration = 0.0_f32;
    if let Ok(mut animator) = hero_animator.single_mut() {
        let node = animator
            .node_matching(|n| n == clip_name)
            .or_else(|| animator.node_matching(|n| n == "attack"));
        if let Some(node) = node {
            clip_duration = animator.duration_of(node);
            play_action_scaled(
                &mut animator,
                &mut animation_players,
                node,
                blend,
                false,
                attack_speed,
            );
        }
    }
    fx.chain.anim_until = elapsed + clip_duration as f64 / attack_speed as f64;
    let delay = if clip_duration > 1e-3 {
        (clip_duration * impact_fraction_for(clip_name) / attack_speed).clamp(0.12, 0.45)
    } else {
        FALLBACK_IMPACT_DELAY
    };
    let aim = target
        .and_then(|e| enemies.get(e).ok())
        .map(|(_, t, _, _)| (t.translation() - origin).with_y(0.0).normalize_or_zero())
        .filter(|aim| aim.length_squared() > 1e-6)
        .unwrap_or(forward);
    *fx.pending = PendingSwing {
        active: true,
        delay,
        sound_in: (delay - WHOOSH_LEAD).max(0.02),
        sound_fired: false,
        aim,
        target,
        chain: fx.chain.step,
        lunge_left: best
            .map(|(_, dist)| (dist - STRIKE_DISTANCE).clamp(0.0, MAX_LUNGE))
            .unwrap_or(0.0),
        finisher,
    };
    fx.trail_window.left = delay + 0.35;
    fx.trail_window.boost = if finisher { 1.3 } else { 1.0 };
}

/// ESTÁDIO 2 do melee: conduz o swing pendente frame a frame — re-aponta a
/// mira ao alvo vivo, vira o corpo para ela (damping ~0.1 s), lunge com passo
/// limitado, whoosh a [`WHOOSH_LEAD`] do impacto e, quando `delay` chega a 0,
/// o golpe em si: CLEAVE de tudo num arco à frente com chain × combo ×
/// backstab × crítico × execute, knockback, hit-stop, shake, partículas e
/// números.
///
/// Corre DEPOIS de `player_movement` (o lunge não disputa a locomoção).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn swing_track_system(
    time: Res<Time>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut hit_stop: ResMut<HitStop>,
    mut shake: ResMut<crate::camera::CameraShake>,
    mut camera_fx: ResMut<crate::camera::CameraFx>,
    mut kick: ResMut<crate::camera::CameraKick>,
    mut postfx: ResMut<crate::postfx::PostFxState>,
    mut fx: MeleeFx,
    mut players: Query<(&GlobalTransform, &mut Transform), With<Player>>,
    mut hero_xp: Query<&mut Xp, With<Player>>,
    mut enemies: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Health,
            Option<&Corpse>,
            Option<&LuaScriptRef>,
            Option<&crate::impact::HitRecoil>,
        ),
        (Without<Player>, With<LuaScriptRef>),
    >,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    if !fx.pending.active {
        return;
    }
    let dt = time.delta_secs();
    fx.pending.delay -= dt;
    if !fx.pending.sound_fired {
        fx.pending.sound_in -= dt;
        if fx.pending.sound_in <= 0.0 {
            fx.pending.sound_fired = true;
            let position = players.single().ok().map(|(g, _)| g.translation());
            fx.sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Whoosh,
                position,
            });
        }
    }
    let Ok((player_global, mut player_transform)) = players.single_mut() else {
        fx.pending.active = false;
        return;
    };
    let origin = player_global.translation();
    // Re-aim: alvo vivo → mira no XZ dele; alvo morto a meio → mantém a última.
    if let Some(entity) = fx.pending.target {
        if let Ok((_, t, health, corpse, _, _)) = enemies.get(entity) {
            if corpse.is_none() && health.current > 0.0 {
                let aim = (t.translation() - origin).with_y(0.0).normalize_or_zero();
                if aim.length_squared() > 1e-6 {
                    fx.pending.aim = aim;
                }
            }
        }
    }
    // setPlayerFaceTarget: o corpo vira para a mira durante o windup.
    if fx.pending.aim.length_squared() > 1e-6 {
        let wanted = crate::player::facing_rotation(fx.pending.aim);
        let factor = crate::player::facing_slerp_factor(
            player_transform.rotation,
            wanted,
            FACE_TURN_RATE,
            dt,
        );
        player_transform.rotation = player_transform.rotation.slerp(wanted, factor);
    }
    // Lunge: passo capado por velocidade e pela distância de paragem.
    if fx.pending.lunge_left > 0.0 {
        let speed = LUNGE_SPEED_CAP.min(fx.pending.lunge_left / fx.pending.delay.max(1.0 / 60.0));
        let mut step = (speed * dt).min(fx.pending.lunge_left);
        if let Some(entity) = fx.pending.target {
            if let Ok((_, t, _, _, _, _)) = enemies.get(entity) {
                let gap = (t.translation() - origin).with_y(0.0).length();
                step = step.min((gap - LUNGE_STANDOFF).max(0.0));
            }
        }
        if step > 0.0 {
            let (x, z) = (
                origin.x + fx.pending.aim.x * step,
                origin.z + fx.pending.aim.z * step,
            );
            // SUPERFÍCIE RENDERIZADA (paridade com spawners/knockback): o
            // sample analítico flutua acima das cordas do mesh nas cristas.
            let y = terrain
                .as_ref()
                .map(|t| t.sample_mesh_surface(x, z))
                .unwrap_or(origin.y);
            player_transform.translation = Vec3::new(x, y, z);
            fx.pending.lunge_left -= step;
        }
    }
    if fx.pending.delay > 0.0 {
        return;
    }
    // ── LAND (landSwing) ──
    let finisher = fx.pending.finisher;
    let chain_step = fx.pending.chain;
    let aim = fx.pending.aim;
    fx.pending.active = false;
    fx.pending.target = None;
    let range = if finisher {
        FINISHER_RANGE
    } else {
        MELEE_RANGE
    };
    let arc_dot = if finisher {
        FINISHER_ARC_DOT
    } else {
        ATTACK_ARC_DOT
    };
    let origin = player_transform.translation;

    // Bónus de combo dos hits ANTERIORES (o counter incrementa por hit em baixo).
    let combo_mult = combo_bonus(fx.combo.hits);
    let mut hit_any = false;
    let mut crit_any = false;
    let mut kill_any = false;
    let mut first_hit_pos: Option<Vec3> = None;
    let mut kills: Vec<(Entity, Option<&LuaScriptRef>, Vec3)> = Vec::new();
    for (entity, transform, mut health, corpse, script, recoil) in &mut enemies {
        if corpse.is_some() || health.current <= 0.0 {
            continue;
        }
        let to_target = transform.translation() - origin;
        let flat = to_target.with_y(0.0);
        let dist = flat.length();
        if dist > range || dist < 1e-3 {
            continue;
        }
        if to_target.y.abs() > MELEE_VERTICAL {
            continue;
        }
        if aim.dot(flat / dist) < arc_dot {
            continue;
        }
        hit_any = true;
        if first_hit_pos.is_none() {
            first_hit_pos = Some(transform.translation() + Vec3::Y * 1.4);
        }
        // Soft-lock: acertar fixa o alvo da TargetBar (TTL 8 s).
        fx.combat_target.entity = Some(entity);
        fx.combat_target.timer = crate::feedback::TARGET_TTL;
        // Backstab: o alvo está virado para LONGE do herói (cos 110° ≈ -0.34;
        // o melee.ts usa dot < -0.1 sobre o forward do modelo +Z).
        let target_forward = hero_forward(transform);
        let backstab = target_forward.dot(-flat / dist) < -0.1;
        let crit = crate::skills::is_crit(CRIT_CHANCE + fx.stats.0.crit_bonus, pseudo_roll(&time));
        // Stack de dano do VibeGame: base × chain × combo × backstab × crit.
        let mut damage =
            (MELEE_DAMAGE + fx.stats.0.bonus_damage) * chain_multiplier(chain_step) * combo_mult;
        if backstab {
            damage *= 2.0;
        }
        if crit {
            damage *= CRIT_MULTIPLIER;
            crit_any = true;
            // Crítico intensifica o rasto do swing em curso.
            fx.trail_window.boost = fx.trail_window.boost.max(1.3);
        }
        // Execute: alvo abaixo de 15 % cai na hora (Viber: execute = morte).
        let was_execute = health.current < health.max * EXECUTE_HP_FRAC;
        apply_damage(&mut health, damage);
        if was_execute {
            health.current = 0.0;
        }
        let killed = health.current <= 0.0;
        kill_any |= killed;
        // Combo counter: hit landed (+janela).
        fx.combo.hits = fx.combo.hits.saturating_add(1);
        fx.combo.window = COMBO_WINDOW_SECS;
        let target_pos = transform.translation();
        fx.numbers.write(crate::feedback::DamageNumberEvent {
            position: target_pos + Vec3::Y * 1.8,
            text: format!(
                "-{}{}{}",
                damage as i32,
                if crit { " CRIT!" } else { "" },
                if backstab { " x2" } else { "" }
            ),
            color: if crit {
                Color::srgb(1.0, 0.3, 0.1)
            } else {
                Color::srgb(1.0, 0.96, 0.85)
            },
        });
        if killed {
            fx.numbers.write(crate::feedback::DamageNumberEvent {
                position: target_pos + Vec3::Y * 2.4,
                text: "EXECUTADO!".into(),
                color: Color::srgb(1.0, 0.29, 0.29),
            });
        }
        // Hit-flash: o inimigo acende em branco-quente 0,18 s (a leitura de
        // impacto mais legível — o asset é clonado, não o partilhado).
        fx.commands
            .entity(entity)
            .insert(crate::feedback::HitFlash {
                timer: crate::feedback::HIT_FLASH_SECS,
            });
        // Hit-react: a vítima flincha (one-shot com guard 0,4 s em
        // `animation::hit_react_system`; corpses locked e swings em curso
        // ignoram-no).
        fx.commands
            .entity(entity)
            .insert(crate::animation::HitReact);
        // Stagger FÍSICO: squash-and-stretch na escala da raiz — lê à
        // distância onde o flinch de animação é sub-pixel. Re-hit a meio
        // reutiliza a base guardada (senão a deformação compunha).
        if !killed {
            fx.commands
                .entity(entity)
                .insert(crate::impact::HitRecoil::new(
                    recoil.map(|r| r.base_scale).unwrap_or(transform.scale()),
                ));
            // Reacção da criatura ao golpe (o Hit é o impacto da arma; este
            // é o "ugh" do alvo — o web-original tinha os dois separados).
            fx.sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::EnemyHurt,
                position: Some(target_pos),
            });
        }
        // Partículas: pop branco + sparks radiais (crítico dobra, o COMBO
        // soma +2/hit) + streaks de slash. No ponto do toque, peito/cabeça.
        let combo_sparks = (fx.combo.hits.min(4) * 2) as usize;
        crate::particles::spawn_burst(
            &mut fx.commands,
            &hit_flash_spec(),
            target_pos + Vec3::Y * 1.25,
            if crit { 24 } else { 12 },
        );
        crate::particles::spawn_burst(
            &mut fx.commands,
            &hit_sparks_spec(),
            target_pos + Vec3::Y * 1.0,
            if crit { 40 } else { 22 } + combo_sparks,
        );
        crate::particles::spawn_burst(
            &mut fx.commands,
            &impact_spec("slash", (0.6, 1.1), (0.14, 0.26), (0.4, 1.2), None),
            target_pos + Vec3::Y * 1.2,
            if crit { 6 } else { 4 },
        );
        // Knockback na direção do golpe; bosses têm poise (não empurra).
        let is_boss = script
            .map(|s| s.path.replace('\\', "/").starts_with("bosses/"))
            .unwrap_or(false);
        // Combate vivo: renova a layer battle/boss no driver de BGM.
        fx.combat_music.engage(fx.time.elapsed_secs_f64(), is_boss);
        if !is_boss {
            let meters = if crit {
                KNOCKBACK_CRIT_METERS
            } else if finisher {
                KNOCKBACK_FINISHER_METERS
            } else {
                KNOCKBACK_METERS
            };
            fx.commands
                .entity(entity)
                .insert(crate::physics_fx::knockback_after(
                    flat,
                    meters * crate::physics_fx::KNOCKBACK_DECAY,
                ));
        }
        if killed {
            // Explosão de morte: fogo + fumo escuro (contraste contra o céu)
            // — o abate tem de fechar com um ponto fulgurante, não sumir-se.
            crate::particles::spawn_burst(
                &mut fx.commands,
                &impact_spec("explosion", (0.55, 1.2), (0.3, 0.6), (3.0, 7.0), None),
                target_pos + Vec3::Y * 1.0,
                20,
            );
            crate::particles::spawn_burst(
                &mut fx.commands,
                &impact_spec("smoke", (0.6, 1.3), (0.8, 1.6), (0.8, 1.8), None),
                target_pos + Vec3::Y * 1.3,
                9,
            );
            // Onda de choque no chão do abate — o KO marca o lugar.
            crate::impact::spawn_impact_ring(
                &mut fx.commands,
                &mut meshes,
                &mut materials,
                target_pos.with_y(target_pos.y + 0.08),
                KILL_RING_RADIUS,
                Color::srgb(1.0, 0.8, 0.55),
            );
            kills.push((entity, script, target_pos));
        } else {
            info!(target: "viber::combat", "hit {entity:?}");
        }
    }
    if hit_any {
        // Hit-stop + shake pela classe mais forte do swing.
        let stop = if kill_any {
            HIT_STOP_KILL
        } else if crit_any || finisher {
            HIT_STOP_HEAVY
        } else {
            HIT_STOP_NORMAL
        };
        request_hit_stop(&mut hit_stop, stop);
        // O combo ACENDE o shake: cada hit na janela soma trauma (cap).
        let combo_shake = (fx.combo.hits as f32 * COMBO_SHAKE_PER_HIT).min(COMBO_SHAKE_CAP);
        let (shake_weight, kick_impulse, punch, fov) = if kill_any {
            (SHAKE_KILL, KICK_KILL, PUNCH_KILL_STOPS, FOV_KICK_HEAVY)
        } else if crit_any {
            (SHAKE_CRIT, KICK_CRIT, PUNCH_HEAVY_STOPS, FOV_KICK_HEAVY)
        } else if finisher {
            (
                SHAKE_FINISHER,
                KICK_FINISHER,
                PUNCH_HEAVY_STOPS,
                FOV_KICK_HEAVY,
            )
        } else {
            (SHAKE_HIT, KICK_HIT, PUNCH_HIT_STOPS, 0.0)
        };
        crate::camera::add_camera_shake(&mut shake, shake_weight + combo_shake);
        // Solavanco DIRECIONAL: a câmara é empurrada PARA o golpe (na mira)
        // e a mola devolve-a — o "empurrão" que o ruído do shake não dá.
        crate::camera::add_camera_kick(&mut kick, aim * kick_impulse + Vec3::Y * KICK_UP);
        // Pulso de pós-processo: o frame do golpe clareia e floresce, o
        // `drive_postfx` devolve ao bioma sozinho.
        crate::postfx::punch_impact(
            &mut postfx,
            punch,
            if kill_any {
                PUNCH_KILL_BLOOM
            } else if crit_any || finisher {
                PUNCH_HEAVY_BLOOM
            } else {
                PUNCH_HIT_BLOOM
            },
        );
        // FOV kick do impacto (land/kill): punch de +5° que decai em 0,3 s;
        // finisher/abate somam o peso extra pesado.
        crate::camera::fov_kick(&mut camera_fx, crate::camera::FOV_KICK_IMPACT + fov);
        // Aggro-chain: aliados a 15 m do alvo batido acordam.
        if let Some(position) = first_hit_pos {
            fx.alerts.write(crate::feedback::AttackAlert { position });
            fx.sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Hit,
                position: Some(position),
            });
        }
        for (target, script, position) in kills {
            crate::skills::kill_creature(
                &mut fx.commands,
                target,
                script,
                position,
                &mut hero_xp,
                &mut fx.numbers,
                &mut fx.toasts,
                &mut fx.quests_log,
                &mut fx.sfx,
            );
        }
    }
    if finisher {
        // Shockwave do finisher + floating text (sempre que o finisher aterra,
        // acerte ou não — é o "GOLPE FINAL!" do combat-mechanics). Explosão
        // grande + poeira levantada do chão para peso + anel de choque a
        // ondular o chão.
        let position = origin + aim * 1.2 + Vec3::Y * 0.3;
        crate::particles::spawn_burst(
            &mut fx.commands,
            &impact_spec("explosion", (0.55, 1.15), (0.35, 0.65), (3.5, 8.0), None),
            position,
            20,
        );
        crate::particles::spawn_burst(
            &mut fx.commands,
            &impact_spec("ground-dust", (0.5, 1.0), (0.6, 1.2), (1.0, 2.4), None),
            position.with_y(origin.y + 0.1),
            8,
        );
        crate::impact::spawn_impact_ring(
            &mut fx.commands,
            &mut meshes,
            &mut materials,
            origin.with_y(origin.y + 0.08),
            FINISHER_RING_RADIUS,
            Color::srgb(1.0, 0.78, 0.45),
        );
        fx.numbers.write(crate::feedback::DamageNumberEvent {
            position: position + Vec3::Y * 1.3,
            text: "GOLPE FINAL!".into(),
            color: Color::srgb(1.0, 0.54, 0.16),
        });
    }
}

/// Hit-stop: o mundo corre a [`HIT_STOP_SCALE`] da velocidade (composta com a
/// [`BaseTimeScale`]) enquanto o timer (que corre em tempo REAL) não esgota —
/// slow-motion do impacto; a câmara treme através da pausa (o seu decay usa
/// `Time<Real>`).
///
/// Escreve o `relative_speed` APENAS na janela do hit-stop (incluindo o frame
/// em que expira, para restaurar a base) — fora dela o `relative_speed`
/// pertence a quem definiu a [`BaseTimeScale`] (ex.: o debug bridge); escrever
/// incondicionalmente estompava o slow-mo externo 1 frame depois de aplicado.
pub fn hit_stop_system(
    real: Res<Time<Real>>,
    mut stop: ResMut<HitStop>,
    base: Res<BaseTimeScale>,
    mut virtual_time: ResMut<Time<Virtual>>,
    mut was_active: Local<bool>,
) {
    let active = stop.timer > 0.0;
    if active {
        stop.timer -= real.delta_secs();
    }
    if active || *was_active {
        virtual_time.set_relative_speed(base.0 * hit_stop_scale(stop.timer));
    }
    *was_active = active;
}

/// Spec de burst de combate AFINADO por cima do preset — os defaults de
/// slash/sparks são sub-pixel no frame do impacto (o pop do BOTW domina o
/// frame: núcleo branco-quente + sparks laranja a radiar do ponto de toque).
pub fn impact_spec(
    preset: &str,
    size: (f32, f32),
    life: (f32, f32),
    speed: (f32, f32),
    color: Option<[f32; 3]>,
) -> crate::recipes::ParticleSpec {
    crate::recipes::ParticleSpec {
        preset: preset.to_string(),
        emission_rate: None,
        life: Some(life),
        speed: Some(speed),
        size: Some(size),
        color,
        shape_radius: None,
        looping: false,
        world_space: false,
    }
}

/// Núcleo branco-quente do impacto (o "pop" que lê a distância) — R2: o
/// pop domina o enquadramento fechado, partículas grandes e densas.
pub fn hit_flash_spec() -> crate::recipes::ParticleSpec {
    impact_spec(
        "sparkle",
        (0.45, 0.95),
        (0.14, 0.26),
        (0.4, 1.4),
        Some([1.0, 0.97, 0.85]),
    )
}

/// Sparks do impacto: densos e vivos (primeiro plano do frame).
pub fn hit_sparks_spec() -> crate::recipes::ParticleSpec {
    impact_spec("sparks", (0.12, 0.28), (0.3, 0.6), (3.0, 7.0), None)
}

/// Vira o MODELO para `dir` (XZ) de uma vez (slerp com fator fixo) — snap de
/// cast das skills ([R]/fireball/bomba): mais forte que o windup contínuo do
/// melee (`FACE_TURN_RATE`), sem teleporte de rotação.
pub fn snap_facing(transform: &mut Transform, dir: Vec3, factor: f32) {
    if dir.length_squared() < 1e-6 {
        return;
    }
    let wanted = crate::player::facing_rotation(dir);
    transform.rotation = transform.rotation.slerp(wanted, factor.clamp(0.0, 1.0));
}

/// Roll pseudo-aleatório determinístico (0..1) para críticos, derivado do
/// tempo da engine — sem depender de crate de RNG.
pub fn pseudo_roll(time: &Time) -> f32 {
    (time.elapsed_secs_f64() * 7919.0).fract() as f32
}

/// Tipo de criatura a partir do path do script (`"enemies/wolf.lua"` →
/// `"wolf"`) — é o alvo dos objetivos `kill` das quests. Também usado pelo
/// kill-parity do golpe forte/bomba em `skills.rs`.
pub fn script_kind(path: &str) -> String {
    path.rsplit('/')
        .next()
        .unwrap_or(path)
        .trim_end_matches(".lua")
        .to_string()
}

/// Toca o clip de morte assim que a entidade vira cadáver — por NOME (o
/// `AnimState` da engine não tem variante Death; os rigs spellam "death" /
/// "Animator3D_Death").
///
/// É uma acção TERMINAL: toca uma vez e segura a última pose. Antes tocava em
/// `.repeat()` e o cadáver morria em loop até desaparecer.
pub fn play_death_animation(
    mut dead: Query<&mut CharacterAnimator, Added<Corpse>>,
    mut players: PlayerQuery,
) {
    for mut animator in &mut dead {
        let node = animator.node_matching(|n| n == "death" || n.ends_with("death"));
        let Some(node) = node else { continue };
        play_action(&mut animator, &mut players, node, ACTION_BLEND, true);
    }
}

/// Cadáveres somem após a animação de morte.
pub fn tick_corpses(
    mut corpses: Query<(Entity, &mut Corpse, &CorpseFade)>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut commands: Commands,
    time: Res<Time>,
) {
    let dt = time.delta_secs();
    for (entity, mut corpse, fade) in &mut corpses {
        corpse.timer -= dt;
        if corpse.timer <= 0.0 {
            // Os clones do fade morrem COM o cadáver: sem isto cada abate
            // deixava até MAX_FLASH_MATS materiais residentes para sempre (o
            // Bevy não faz GC de assets) e o store crescia sem limite.
            for handle in fade.materials.iter() {
                materials.remove(handle);
            }
            commands.entity(entity).despawn();
        }
    }
}

/// No spawn de `Corpse`: clona os materiais da subárvore em
/// `AlphaMode::Blend` (cap [`crate::feedback::MAX_FLASH_MATS`]) para o fade
/// poder descer o alpha sem tocar no material partilhado entre instâncias.
/// Recorda o Y de base para o sink.
#[allow(clippy::type_complexity)]
pub fn corpse_fade_setup(
    mut commands: Commands,
    mut added: Query<(Entity, Option<&Transform>, &Corpse), Added<Corpse>>,
    children: Query<&Children>,
    mut mesh_materials: Query<&mut MeshMaterial3d<StandardMaterial>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    for (entity, transform, _corpse) in &mut added {
        let mut subtree = Vec::new();
        crate::feedback::collect_subtree(entity, &children, &mut subtree);
        let mut cloned: Vec<Handle<StandardMaterial>> = Vec::new();
        for node in subtree {
            if cloned.len() >= crate::feedback::MAX_FLASH_MATS {
                break;
            }
            let Ok(mut slot) = mesh_materials.get_mut(node) else {
                continue;
            };
            let Some(original) = materials.get(&slot.0) else {
                continue;
            };
            let mut copy = original.clone();
            copy.alpha_mode = AlphaMode::Blend;
            let handle = materials.add(copy);
            slot.0 = handle.clone();
            cloned.push(handle);
        }
        commands.entity(entity).insert(CorpseFade {
            materials: cloned,
            base_y: transform.map_or(0.0, |t| t.translation.y),
        });
    }
}

/// Fade + sink do cadáver: alpha cai e o corpo afunda
/// [`CORPSE_SINK_METERS`] ao longo do TTL ([`CORPSE_LIFETIME`]) — o despawn
/// de [`tick_corpses`] chega com o corpo já transparente e meio enterrado.
#[allow(clippy::type_complexity)]
pub fn corpse_fade_tick(
    mut corpses: Query<(&Corpse, &CorpseFade, Option<&mut Transform>)>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    for (corpse, fade, transform) in &mut corpses {
        let progress = 1.0 - corpse.timer / CORPSE_LIFETIME;
        let alpha = corpse_fade_alpha(progress);
        for handle in fade.materials.iter() {
            if let Some(mut material) = materials.get_mut(handle) {
                material.base_color.set_alpha(alpha);
            }
        }
        if let Some(mut transform) = transform {
            transform.translation.y = fade.base_y + corpse_sink_offset(progress);
        }
    }
}

// ── Arma na mão (grips copiados do VibeGame `data/held-items.json`) ────
// Bone: `hand_r` (rig Mixamo do herói; candidates do held-item.ts).
// Ordem de busca espelha HAND_BONE_CANDIDATES + fuzzy "righthand".

// ── Troca de armas ([V]: espada → machado → lança) ─────────────────────
// Grips copiados do VibeGame `dist/data/held-items.json`.

#[derive(Debug, Clone, Resource, Default)]
pub struct HeldWeapon {
    /// Índice em [`WEAPON_TABLE`].
    pub idx: usize,
    /// Osso da mão do herói (preenchido na primeira detecção).
    pub bone: Option<Entity>,
    /// Entidade da arma atual (filha do osso) — trocada no [V].
    pub current: Option<Entity>,
    /// Nomes de nó já varridos (evita re-busca por frame).
    pub searched: bool,
}

/// (url, pos, rot XYZ rad, scale, rótulo)
#[allow(clippy::type_complexity)]
pub const WEAPON_TABLE: [(&str, [f32; 3], [f32; 3], f32, &str); 3] = [
    (
        "assets/meshes/props/sword_hero_lod0.glb",
        [0.12, 0.04, 0.04],
        [-1.33, 12.71, 0.96],
        1.0,
        "espada",
    ),
    (
        "assets/meshes/props/axe_lod0.glb",
        [0.23, 0.11, 0.01],
        [2.98, 12.71, std::f32::consts::FRAC_PI_2],
        1.0,
        "machado",
    ),
    (
        "assets/meshes/props/spear_lod0.glb",
        [0.2, 0.01, 0.04],
        [-1.33, 12.71, 0.96],
        1.0,
        "lança",
    ),
];

/// Anexa a arma inicial (espada) e troca no [V].
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn cycle_weapon(
    mut commands: Commands,
    keys: Res<ButtonInput<KeyCode>>,
    asset_server: Res<AssetServer>,
    heroes: Query<Entity, With<Player>>,
    names: Query<&Name>,
    children: Query<&Children>,
    mut held: ResMut<HeldWeapon>,
    // Colheita nativa com alvo: o [V] fica bloqueado — a mão pertence à
    // ferramenta de colheita (harvest.rs restaura a arma ao sair do alcance).
    harvest: Res<crate::harvest::HarvestContext>,
) {
    let cycle = keys.just_pressed(KeyCode::KeyV);
    if held.bone.is_none() && !held.searched {
        let Ok(hero) = heroes.single() else { return };
        held.bone = find_hand_bone(hero, &children, &names);
        // Só para de procurar quando a cena do herói JÁ carregou (tem
        // filhos): no primeiro frame o GLB ainda está a chegar e uma busca
        // única enterrava a espada — a arma nunca anexava, em mundo nenhum.
        held.searched = held.bone.is_some() || children.get(hero).is_ok_and(|c| !c.is_empty());
    }
    let Some(bone) = held.bone else { return };
    // Guard do [V] DEPOIS da descoberta do osso (a colheita precisa dele
    // para anexar as ferramentas) e ANTES de qualquer troca.
    if harvest.target.is_some() {
        return;
    }
    let first_attach = held.current.is_none();
    if !first_attach && !cycle {
        return;
    }
    if cycle {
        held.idx = (held.idx + 1) % WEAPON_TABLE.len();
    }
    if let Some(old) = held.current.take() {
        commands.entity(old).despawn();
    }
    let (url, pos, rot, scale, _label) = WEAPON_TABLE[held.idx];
    let handle = crate::meshopt::load_gltf(&asset_server, url.to_owned());
    let mut transform = Transform::from_translation(Vec3::new(pos[0], pos[1], pos[2]));
    transform.rotation = Quat::from_euler(EulerRot::XYZ, rot[0], rot[1], rot[2]);
    transform.scale = Vec3::splat(scale);
    let spawned = commands
        .spawn((
            transform,
            Visibility::Inherited,
            crate::recipes::spawn::GltfScenePending { handle },
        ))
        .id();
    commands.entity(bone).add_child(spawned);
    held.current = Some(spawned);
}

/// Procura o osso da mão por subárvore (mesmos candidates do held-item.ts).
/// `pub(crate)`: o harvest.rs anexa as ferramentas de colheita ao mesmo osso.
pub(crate) fn find_hand_bone(
    root: Entity,
    children: &Query<&Children>,
    names: &Query<&Name>,
) -> Option<Entity> {
    let name = names.get(root).ok()?.to_ascii_lowercase();
    let is_hand = matches!(
        name.as_str(),
        "hand_r" | "righthand" | "right_hand" | "right hand"
    ) || (name.contains("hand_r") && !name.contains("finger"))
        || (name.contains("righthand") && !name.contains("finger"));
    if is_hand {
        return Some(root);
    }
    for child in children.get(root).ok()?.iter() {
        if let Some(found) = find_hand_bone(child, children, names) {
            return Some(found);
        }
    }
    None
}

// ── Skill: bola de fogo (botão direito) ────────────────────────────────
// Projétil em frente ao herói; explode no primeiro inimigo (com Health)
// num raio de 2.5 m (40 de dano em área) ou ao fim da vida.

const FIREBALL_SPEED: f32 = 18.0;
const FIREBALL_LIFE: f32 = 2.0;
const FIREBALL_DAMAGE: f32 = 40.0;
const FIREBALL_RADIUS: f32 = 2.5;
const FIREBALL_COOLDOWN: f32 = 1.2;
/// Snap de mira do cast: raio de procura do alvo (m) — projétil viaja, o
/// lock é mais generoso que o melee.
const FIREBALL_LOCK_RANGE: f32 = 14.0;
/// Cone do lock (dot ≥ 0 = hemisfério frontal, generoso de propósito).
const FIREBALL_LOCK_ARC_DOT: f32 = 0.0;
/// Fator do snap de facing no cast.
const FIREBALL_FACE_SNAP: f32 = 0.5;

#[derive(Debug, Component)]
pub struct Fireball {
    pub vel: Vec3,
    pub life: f32,
}

#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn cast_fireball(
    mut commands: Commands,
    mouse: Res<ButtonInput<MouseButton>>,
    menus: Res<crate::menus::MenusOpen>,
    time: Res<Time>,
    mut last: Local<Option<f64>>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform), With<Player>>,
    enemies: Query<
        (&GlobalTransform, &Health, Option<&Corpse>),
        (Without<Player>, With<LuaScriptRef>),
    >,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    // Menu aberto consome o clique direito: lançava a fireball por trás do
    // modal (o clique podia ser um click de UI).
    if menus.any() {
        return;
    }
    if !mouse.just_pressed(MouseButton::Right) {
        return;
    }
    if let Some(last) = *last {
        if time.elapsed_secs_f64() - last < FIREBALL_COOLDOWN as f64 {
            return;
        }
    }
    let Ok((_, player, mut transform)) = players.single_mut() else {
        return;
    };
    *last = Some(time.elapsed_secs_f64());
    // O modelo olha +Z (convenção da pipeline) — o `forward()` (-Z) atirava a
    // bola de fogo PARA TRÁS do herói.
    let mut aim = hero_forward(player);
    let origin_pos = player.translation();
    // Snap de mira: o inimigo vivo mais próximo no cone frontal vira o CAST —
    // o MODELO aponta para ele e o projétil sai na direção dele (o herói
    // "pede" o alvo em vez de atirar onde os pés apontam).
    let mut best: Option<(f32, Vec3)> = None;
    for (t, health, corpse) in &enemies {
        if corpse.is_some() || health.current <= 0.0 {
            continue;
        }
        let to = t.translation() - origin_pos;
        let flat = to.with_y(0.0);
        let dist = flat.length();
        if dist < 1e-3 || dist > FIREBALL_LOCK_RANGE {
            continue;
        }
        if to.y.abs() > MELEE_VERTICAL {
            continue;
        }
        if aim.dot(flat / dist) < FIREBALL_LOCK_ARC_DOT {
            continue;
        }
        if best.map(|(d, _)| dist < d).unwrap_or(true) {
            best = Some((dist, flat / dist));
        }
    }
    if let Some((_, dir)) = best {
        aim = dir;
        snap_facing(&mut transform, aim, FIREBALL_FACE_SNAP);
    }
    let origin = origin_pos + aim * 0.8 + Vec3::Y * 1.2;
    let mesh = meshes.add(Sphere::new(0.16));
    let material = materials.add(StandardMaterial {
        base_color: Color::srgb(1.0, 0.45, 0.1),
        emissive: LinearRgba::rgb(2.5, 0.9, 0.15),
        unlit: true,
        ..StandardMaterial::default()
    });
    commands.spawn((
        Transform::from_translation(origin),
        Visibility::Inherited,
        Mesh3d(mesh),
        MeshMaterial3d(material),
        Fireball {
            vel: aim * FIREBALL_SPEED,
            life: FIREBALL_LIFE,
        },
    ));
}

/// Paridade com o melee (`MeleeFx`): o impacto da fireball também fixa o
/// alvo da TargetBar ([`crate::feedback::CombatTarget`]) e partilha os pesos
/// globais de impacto (hit-stop/shake/kick/pós-processo).
#[derive(bevy::ecs::system::SystemParam)]
struct FireballFx<'w> {
    hit_stop: ResMut<'w, HitStop>,
    shake: ResMut<'w, crate::camera::CameraShake>,
    kick: ResMut<'w, crate::camera::CameraKick>,
    postfx: ResMut<'w, crate::postfx::PostFxState>,
    combat_target: ResMut<'w, crate::feedback::CombatTarget>,
}

/// Move a bola de fogo, detecta impacto e aplica dano em área. O impacto tem
/// a paridade do melee: hit-stop + shake + solavanco + punch de pós-processo
/// globais, flash + recoil por inimigo (o projétil é um golpe, não um número).
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
fn fireball_step(
    mut commands: Commands,
    time: Res<Time>,
    mut fx: FireballFx,
    mut balls: Query<(Entity, &mut Transform, &mut Fireball)>,
    mut enemies: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Health,
            Option<&LuaScriptRef>,
            Option<&crate::impact::HitRecoil>,
        ),
        (Without<Player>, With<LuaScriptRef>, Without<Corpse>),
    >,
    mut hero_xp: Query<&mut Xp, With<Player>>,
    mut numbers: MessageWriter<crate::feedback::DamageNumberEvent>,
    mut alerts: MessageWriter<crate::feedback::AttackAlert>,
    mut toasts: bevy::ecs::message::MessageWriter<ScriptToast>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
    mut quests: Option<ResMut<crate::quests::QuestLog>>,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut ball) in &mut balls {
        ball.life -= dt;
        if ball.life <= 0.0 {
            commands.entity(entity).despawn();
            continue;
        }
        transform.translation += ball.vel * dt;
        // Impacto: qualquer inimigo num raio de contato.
        let mut impact: Option<Vec3> = None;
        for (_, t, _, _, _) in &enemies {
            if t.translation().distance(transform.translation) < 1.2 {
                impact = Some(t.translation());
                break;
            }
        }
        if let Some(center) = impact {
            // FX do impacto: bola de fogo explode (antes só o número saía —
            // o projétil somia sem um único pixel de fogo).
            crate::particles::spawn_burst(
                &mut commands,
                &impact_spec("explosion", (0.5, 1.1), (0.3, 0.65), (3.0, 7.5), None),
                center + Vec3::Y * 1.0,
                16,
            );
            crate::particles::spawn_burst(
                &mut commands,
                &hit_sparks_spec(),
                center + Vec3::Y * 1.0,
                12,
            );
            crate::particles::spawn_burst(
                &mut commands,
                &hit_flash_spec(),
                center + Vec3::Y * 1.2,
                8,
            );
            // Peso de impacto global (classe hit normal).
            request_hit_stop(&mut fx.hit_stop, HIT_STOP_NORMAL);
            crate::camera::add_camera_shake(&mut fx.shake, SHAKE_HIT);
            let ball_dir = ball.vel.with_y(0.0).normalize_or_zero();
            crate::camera::add_camera_kick(&mut fx.kick, ball_dir * KICK_HIT + Vec3::Y * KICK_UP);
            crate::postfx::punch_impact(&mut fx.postfx, PUNCH_HIT_STOPS, PUNCH_HIT_BLOOM);
            // Dano em área; abates via kill_creature (paridade melee/strike/
            // bomba: XP, quests, alerta de aggro — antes a fireball não
            // reportava kills nem acordava aliados).
            let mut kills: Vec<(Entity, Option<&LuaScriptRef>, Vec3)> = Vec::new();
            let mut hit_any = false;
            for (target, t, mut health, script, recoil) in &mut enemies {
                if t.translation().distance(center) <= FIREBALL_RADIUS {
                    hit_any = true;
                    // Paridade do melee: acertar fixa o alvo da TargetBar.
                    fx.combat_target.entity = Some(target);
                    fx.combat_target.timer = crate::feedback::TARGET_TTL;
                    apply_damage(&mut health, FIREBALL_DAMAGE);
                    commands.entity(target).insert(crate::feedback::HitFlash {
                        timer: crate::feedback::HIT_FLASH_SECS,
                    });
                    if health.current > 0.0 {
                        commands
                            .entity(target)
                            .insert(crate::impact::HitRecoil::new(
                                recoil.map(|r| r.base_scale).unwrap_or(t.scale()),
                            ));
                    }
                    numbers.write(crate::feedback::DamageNumberEvent {
                        position: t.translation() + Vec3::Y * 1.8,
                        text: format!("-{}", FIREBALL_DAMAGE as i32),
                        color: Color::srgb(1.0, 0.6, 0.1),
                    });
                    if health.current <= 0.0 {
                        kills.push((target, script, t.translation()));
                    }
                }
            }
            // Paridade do melee: o alerta de aggro sai em QUALQUER hit (o
            // contrato `on_player_attack` é "quando o herói acerta"), não só
            // quando o hit mata.
            if hit_any {
                alerts.write(crate::feedback::AttackAlert { position: center });
            }
            for (target, script, position) in kills {
                crate::skills::kill_creature(
                    &mut commands,
                    target,
                    script,
                    position,
                    &mut hero_xp,
                    &mut numbers,
                    &mut toasts,
                    &mut quests,
                    &mut sfx,
                );
            }
            commands.entity(entity).despawn();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_chain_multiplier_matches_vibegame() {
        // mults [1, 1.15, 1.7]; passo clampeado (4.º+ press segue finisher).
        assert!((chain_multiplier(0) - 1.0).abs() < 1e-5);
        assert!((chain_multiplier(1) - 1.15).abs() < 1e-5);
        assert!((chain_multiplier(2) - 1.7).abs() < 1e-5);
        assert!((chain_multiplier(9) - 1.7).abs() < 1e-5, "clamp");
    }

    #[test]
    fn test_combo_bonus_caps_at_30_percent() {
        assert!((combo_bonus(0) - 1.0).abs() < 1e-5);
        assert!((combo_bonus(1) - 1.02).abs() < 1e-5);
        assert!((combo_bonus(10) - 1.2).abs() < 1e-5);
        assert!((combo_bonus(30) - 1.3).abs() < 1e-5);
        assert!((combo_bonus(100) - 1.3).abs() < 1e-5, "cap +30 %");
    }

    #[test]
    fn test_pick_combo_clip_never_repeats_last() {
        let pool = ["sword", "sworda", "swordb", "swordc"];
        // Sem histórico: devolve o sorteio puro.
        assert_eq!(pick_combo_clip(&pool, None, 0.0), 0);
        assert_eq!(pick_combo_clip(&pool, None, 0.9), 3);
        // Repetição imediata é empurrada para o próximo slot.
        assert_eq!(pick_combo_clip(&pool, Some(2), 0.5), 3);
        // Wrap-around: último → primeiro.
        assert_eq!(pick_combo_clip(&pool, Some(3), 0.9), 0);
        // Pool de 1 (lança) nunca varia nem pânica.
        assert_eq!(pick_combo_clip(&["spear"], Some(0), 0.3), 0);
        assert_eq!(pick_combo_clip(&[], None, 0.3), 0);
    }

    #[test]
    fn test_impact_fraction_for_covers_every_pool_clip() {
        // Cada clip dos pools tem entrada própria na tabela.
        assert!((impact_fraction_for("sword") - 0.34).abs() < 1e-5);
        assert!((impact_fraction_for("sworda") - 0.30).abs() < 1e-5);
        assert!((impact_fraction_for("swordb") - 0.38).abs() < 1e-5);
        assert!((impact_fraction_for("swordc") - 0.42).abs() < 1e-5);
        assert!((impact_fraction_for("spear") - 0.40).abs() < 1e-5);
        for pool in ATTACK_POOLS.iter() {
            for clip in pool.iter() {
                let fraction = impact_fraction_for(clip);
                assert!(
                    (0.12..=0.45).contains(&fraction),
                    "clip {clip} → {fraction} fora do range do clamp de delay"
                );
            }
        }
    }

    #[test]
    fn test_impact_fraction_matching_is_substring_case_insensitive() {
        // Específico ganha ao genérico ("sworda" antes de "sword").
        assert!(impact_fraction_for("sworda") < impact_fraction_for("sword"));
        // Case/spacing dos GLBs reais.
        assert!((impact_fraction_for("SwordB") - 0.38).abs() < 1e-5);
        assert!((impact_fraction_for("hero_spear_swing") - 0.40).abs() < 1e-5);
        // Desconhecido → fallback histórico.
        assert!((impact_fraction_for("attack") - SWING_IMPACT_FRACTION).abs() < 1e-5);
        assert!((impact_fraction_for("") - SWING_IMPACT_FRACTION).abs() < 1e-5);
    }

    #[test]
    fn test_corpse_fade_alpha_monotonic_and_sink_linear() {
        // Alpha: 1 → 0, monotónico decrescente.
        assert!((corpse_fade_alpha(0.0) - 1.0).abs() < 1e-5);
        let mut previous = 1.0;
        for step in 1..=20 {
            let alpha = corpse_fade_alpha(step as f32 / 20.0);
            assert!(alpha <= previous + 1e-6, "não-monotónico em {step}");
            previous = alpha;
        }
        assert!((corpse_fade_alpha(1.0)).abs() < 1e-5, "transparente no fim");
        assert_eq!(corpse_fade_alpha(-0.5), 1.0, "clamp em baixo");
        assert_eq!(corpse_fade_alpha(1.5), 0.0, "clamp em cima");
        // Sink: linear 0 → −CORPSE_SINK_METERS.
        assert!((corpse_sink_offset(0.0)).abs() < 1e-5);
        assert!((corpse_sink_offset(0.5) - (-CORPSE_SINK_METERS * 0.5)).abs() < 1e-5);
        assert!((corpse_sink_offset(1.0) - (-CORPSE_SINK_METERS)).abs() < 1e-5);
        assert_eq!(corpse_sink_offset(2.0), -CORPSE_SINK_METERS, "clamp");
    }

    #[test]
    fn test_hit_stop_scale_and_request() {
        let mut stop = HitStop::default();
        assert!((hit_stop_scale(stop.timer) - 1.0).abs() < 1e-5, "sem stop");
        request_hit_stop(&mut stop, HIT_STOP_KILL);
        assert!((hit_stop_scale(stop.timer) - HIT_STOP_SCALE).abs() < 1e-5);
        // Pedidos sobrepostos mantêm o mais longo.
        request_hit_stop(&mut stop, HIT_STOP_NORMAL);
        assert!((stop.timer - HIT_STOP_KILL).abs() < 1e-5);
    }

    #[test]
    fn test_hero_forward_is_plus_z_not_bezier_minus_z() {
        // facing_rotation orienta o MODELO para +Z: o forward de gameplay do
        // herói virado "norte" (+Z) tem de ser +Z, não o −Z do GlobalTransform.
        let rotation = crate::player::facing_rotation(Vec3::Z);
        let global = GlobalTransform::from_rotation(rotation);
        let fwd = hero_forward(&global);
        assert!(fwd.z > 0.99, "forward {fwd:?}");
        // Flat: sem componente vertical mesmo com pitch no rig.
        let tilted = GlobalTransform::from(Transform::from_rotation(Quat::from_euler(
            EulerRot::XZY,
            0.3,
            0.0,
            0.0,
        )));
        assert!(hero_forward(&tilted).y.abs() < 1e-5);
    }

    #[test]
    fn test_execute_fraction_matches_vibegame() {
        // EXECUTE_HP_FRAC 0.15: 14/100 executa, 16/100 não.
        let hp = Health {
            current: 14.0,
            max: 100.0,
        };
        assert!(hp.current < hp.max * EXECUTE_HP_FRAC);
        let hp = Health {
            current: 16.0,
            max: 100.0,
        };
        assert!(hp.current >= hp.max * EXECUTE_HP_FRAC);
    }

    #[test]
    fn test_authored_max_hp_covers_bosses_and_defaults_the_rest() {
        // Os 4 chefes do simple-rpg (paths como nos XML de bosses).
        assert!((authored_max_hp("bosses/boss.lua") - BOSS_HP_OGRE).abs() < 1e-4);
        assert!((authored_max_hp("bosses/bog-warden.lua") - BOSS_HP_BOG_WARDEN).abs() < 1e-4);
        assert!((authored_max_hp("bosses/sand-worm.lua") - BOSS_HP_SAND_WORM).abs() < 1e-4);
        assert!((authored_max_hp("bosses/witch.lua") - BOSS_HP_WITCH).abs() < 1e-4);
        // Separador Windows normaliza.
        assert_eq!(
            authored_max_hp("bosses\\boss.lua"),
            authored_max_hp("bosses/boss.lua")
        );
        // Inimigos comuns e desconhecidos ficam no default de 100.
        assert!((authored_max_hp("enemies/wolf.lua") - crate::vitals::DEFAULT_HEALTH).abs() < 1e-4);
        assert!(
            (authored_max_hp("bosses/desconhecido.lua") - crate::vitals::DEFAULT_HEALTH).abs()
                < 1e-4
        );
        assert!((authored_max_hp("townsfolk.lua") - crate::vitals::DEFAULT_HEALTH).abs() < 1e-4);
    }

    /// O cadáver leva os clones do fade COM ele: sem isto cada abate deixava
    /// até `MAX_FLASH_MATS` materiais residentes para sempre (o Bevy não faz
    /// GC de assets) e o store crescia sem limite numa sessão de combate.
    #[test]
    fn test_corpse_leva_os_clones() {
        let mut app = App::new();
        app.init_resource::<Time>()
            .init_resource::<Assets<StandardMaterial>>()
            .add_systems(Update, tick_corpses);

        let corpse = {
            let world = app.world_mut();
            let clone = world
                .resource_mut::<Assets<StandardMaterial>>()
                .add(StandardMaterial {
                    alpha_mode: AlphaMode::Blend,
                    ..StandardMaterial::default()
                });
            world
                .spawn((
                    Corpse { timer: 0.0 },
                    CorpseFade {
                        materials: vec![clone],
                        base_y: 0.0,
                    },
                ))
                .id()
        };
        app.update();

        assert_eq!(
            app.world().resource::<Assets<StandardMaterial>>().len(),
            0,
            "o clone do fade foi removido com o cadáver"
        );
        assert!(
            app.world().get_entity(corpse).is_err(),
            "o cadáver despawnou"
        );
    }
}
