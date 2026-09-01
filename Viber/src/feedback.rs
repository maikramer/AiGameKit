//! Feedback de combate (loop 2 do port simple-rpg) — o análogo nativo do
//! `CombatFeedbackSystem`/`RespawnSystem`/hurt-vignette do VibeGame:
//!
//! - **Dano flutuante**: pool de textos UI world-anchored (projecção da
//!   câmara) que sobem e desvanecem — `-25` no alvo, dourado para XP.
//! - **Hurt vignette + i-frames**: todo dano ao herói passa por
//!   [`hurt_player`] (via `PlayerHurt`), respeita `Invulnerable` (0,35 s),
//!   acende a vinheta vermelha e não acerta durante `Dying`.
//! - **TargetBar / BossBar reais**: o melee do herói fixa o alvo
//!   ([`CombatTarget`], TTL 8 s); a barra do boss segue a entidade
//!   `name="boss"` (hud cria os nós `hud:bar:*`).
//! - **RespawnSystem**: HP 0 → `Dying` 2 s → volta ao ponto mais próximo
//!   (praça/portões) com HP cheio e i-frames.
//! - **Status effects mínimos**: veneno tickado por segundo; scripts Luau
//!   aplicam com `viber.apply_status("venom", secs)`.
//!
//! Camera shake fica para o dono da câmara (cross-scope).

use bevy::camera::Camera3d;
use bevy::prelude::*;

use crate::luau::ScriptToast;
use crate::player::Player;
use crate::vitals::{Health, apply_damage};

/// Janela de invulnerabilidade após cada golpe sofrido (s) — VibeGame 0,35.
pub const IFRAME_SECS: f32 = 0.35;
/// Espera entre a morte e o respawn (s) — VibeGame RespawnSystem.
pub const RESPAWN_DELAY: f32 = 2.0;
/// Segundos sem combate até o alvo da TargetBar se perder.
pub const TARGET_TTL: f32 = 8.0;
/// Dano por segundo do veneno (tick 1/s).
pub const VENOM_DPS: f32 = 4.0;
/// Vida útil de um número de dano (s).
const NUMBER_LIFETIME: f32 = 0.9;
/// Tamanho do pool de números de dano (slots reutilizados).
const NUMBER_POOL: usize = 14;

/// Nomes dos nós das barras criados pelo hud (`hud:bar:{label}`).
pub const TARGET_BAR_NODE: &str = "hud:bar:sem alvo";
pub const BOSS_BAR_NODE: &str = "hud:bar:BOSS";

// ── eventos ─────────────────────────────────────────────────────────────

/// Dano ao herói — ÚNICO caminho do dano (scripts `damage_player`, veneno).
/// A aplicação real acontece em [`player_hurt_system`] com i-frames/vinheta.
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct PlayerHurt {
    pub amount: f32,
    /// `true` para dano de status (veneno): ignora i-frames, sem número.
    pub status: bool,
}

/// Número flutuante a mostrar no mundo (consumido pelo pool de UI).
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct DamageNumberEvent {
    pub position: Vec3,
    pub text: String,
    pub color: Color,
}

/// Aggro-chain (loop 6): o herói acertou uma criatura nesta posição —
/// aliados num raio de 15 m recebem `on_player_attack(px, pz)` nos scripts.
#[derive(Debug, Clone, Copy, bevy::ecs::message::Message)]
pub struct AttackAlert {
    pub position: Vec3,
}

// ── componentes / recursos ──────────────────────────────────────────────

/// Janela de i-frames do herói (decrementada por frame).
#[derive(Debug, Clone, Component)]
pub struct Invulnerable {
    pub timer: f32,
}

/// Herói morto: espera [`RESPAWN_DELAY`] e renasce no ponto mais próximo.
#[derive(Debug, Clone, Component)]
pub struct Dying {
    pub timer: f32,
}

/// Status effects activos no herói (mínimo: veneno).
#[derive(Debug, Clone, Component, Default)]
pub struct StatusEffects {
    /// Segundos restantes de veneno (0 = sem veneno).
    pub venom: f32,
    /// Acumulador para o tick de 1 s (recomeça em cada aplicação).
    pub venom_tick: f32,
}

/// Alvo de combate actual do herói (soft-lock do VibeGame: fixa ao acertar).
#[derive(Debug, Clone, Resource)]
pub struct CombatTarget {
    pub entity: Option<Entity>,
    pub timer: f32,
}

impl Default for CombatTarget {
    fn default() -> Self {
        Self {
            entity: None,
            timer: 0.0,
        }
    }
}

/// Intensidade actual da vinheta de dano (0..1), decai exponencialmente.
#[derive(Debug, Clone, Resource, Default)]
pub struct HurtFlash(pub f32);

/// Slot do pool de números de dano.
#[derive(Debug, Clone, Component)]
struct DamageNumberSlot {
    world_pos: Vec3,
    age: f32,
    active: bool,
}

// ── lógica pura (testada) ───────────────────────────────────────────────

/// Pontos de respawn: praça + 4 portões cardeais (LOOKOUT_GATES do VibeGame).
pub const RESPAWN_POINTS: [Vec2; 5] = [
    Vec2::ZERO,
    Vec2::new(0.0, -50.0),
    Vec2::new(0.0, 50.0),
    Vec2::new(-50.0, 0.0),
    Vec2::new(50.0, 0.0),
];

/// Ponto de respawn mais próximo da posição de morte (XZ).
pub fn nearest_respawn_point(from: Vec2) -> Vec2 {
    RESPAWN_POINTS
        .iter()
        .copied()
        .min_by(|a, b| a.distance_squared(from).total_cmp(&b.distance_squared(from)))
        .unwrap_or(Vec2::ZERO)
}

/// Rótulo humano do ponto de respawn (toast de retorno).
pub fn respawn_label(point: Vec2) -> &'static str {
    match point {
        Vec2::ZERO => "praça",
        p if p.y < 0.0 => "portão sul",
        p if p.y > 0.0 => "portão norte",
        p if p.x < 0.0 => "portão oeste",
        _ => "portão leste",
    }
}

/// Resultado de tentar ferir o herói.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum HurtOutcome {
    /// Herói morto ou em `Dying`: dano ignorado.
    Ignored,
    /// Bloqueado por i-frames (dano físico apenas).
    Blocked,
    /// Aplicado; `killed` = HP chegou a 0.
    Applied { killed: bool },
}

/// Caminho único de dano ao herói: i-frames (físico, só com timer > 0 —
/// o componente fica, o QUE importa é a janela), `Dying` ignora tudo,
/// clamp no pool e deteção de morte.
pub fn hurt_player(
    health: &mut Health,
    invuln: Option<&Invulnerable>,
    dying: Option<&Dying>,
    amount: f32,
    status: bool,
) -> HurtOutcome {
    if dying.is_some() {
        return HurtOutcome::Ignored;
    }
    if !status && invuln.is_some_and(|frame| frame.timer > 0.0) {
        return HurtOutcome::Blocked;
    }
    apply_damage(health, amount);
    HurtOutcome::Applied {
        killed: health.current <= 0.0,
    }
}

/// Avança o veneno: devolve dano do tick (1/s) ou 0.
pub fn tick_venom(effects: &mut StatusEffects, dt: f32) -> f32 {
    if effects.venom <= 0.0 {
        return 0.0;
    }
    effects.venom = (effects.venom - dt).max(0.0);
    effects.venom_tick += dt;
    if effects.venom_tick >= 1.0 {
        effects.venom_tick %= 1.0;
        VENOM_DPS
    } else {
        0.0
    }
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct FeedbackPlugin;

impl Plugin for FeedbackPlugin {
    fn build(&self, app: &mut App) {
        // auto-suficiente em apps mínimos (registos idempotentes)
        app.add_message::<ScriptToast>()
            .add_message::<PlayerHurt>()
            .add_message::<DamageNumberEvent>()
            .add_message::<AttackAlert>()
            .init_resource::<CombatTarget>()
            .init_resource::<HurtFlash>()
            .add_systems(Startup, (spawn_vignette, spawn_damage_number_pool))
            .add_systems(
                Update,
                (
                    player_hurt_system,
                    tick_status_system,
                    respawn_system,
                    decay_invulnerability,
                    target_expiry_system,
                    vignette_system,
                    damage_numbers_system,
                    bars_sync_system,
                ),
            );
    }
}

// ── spawn de UI ─────────────────────────────────────────────────────────

fn spawn_vignette(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            top: Val::Px(0.0),
            left: Val::Px(0.0),
            width: Val::Percent(100.0),
            height: Val::Percent(100.0),
            border: UiRect::all(Val::Px(72.0)),
            ..Default::default()
        },
        BackgroundColor(Color::NONE),
        BorderColor::all(Color::NONE),
        Name::new("fx:hurt-vignette"),
        HurtVignette,
    ));
}

#[derive(Component)]
struct HurtVignette;

fn spawn_damage_number_pool(mut commands: Commands) {
    for i in 0..NUMBER_POOL {
        commands.spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Px(0.0),
                top: Val::Px(0.0),
                ..Default::default()
            },
            Text::new(""),
            TextColor(Color::NONE),
            TextFont::from_font_size(20.0),
            Name::new(format!("fx:dmg-{i}")),
            Visibility::Hidden,
            DamageNumberSlot {
                world_pos: Vec3::ZERO,
                age: 0.0,
                active: false,
            },
        ));
    }
}

// ── sistemas ────────────────────────────────────────────────────────────

/// Consome `PlayerHurt`: i-frames, HP, número flutuante, vinheta.
#[allow(clippy::type_complexity)]
fn player_hurt_system(
    mut hurts: MessageReader<PlayerHurt>,
    mut players: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Health,
            Option<&mut Invulnerable>,
            Option<&Dying>,
        ),
        With<Player>,
    >,
    mut commands: Commands,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut flash: ResMut<HurtFlash>,
) {
    let Ok((entity, transform, mut health, mut invuln, dying)) = players.single_mut() else {
        return;
    };
    for hurt in hurts.read() {
        match hurt_player(&mut health, invuln.as_deref(), dying, hurt.amount, hurt.status) {
            HurtOutcome::Ignored | HurtOutcome::Blocked => continue,
            HurtOutcome::Applied { .. } => {}
        }
        if !hurt.status {
            // i-frames físicos renovam a cada golpe
            if let Some(frame) = invuln.as_deref_mut() {
                frame.timer = IFRAME_SECS;
            } else {
                commands.entity(entity).insert(Invulnerable {
                    timer: IFRAME_SECS,
                });
            }
            numbers.write(DamageNumberEvent {
                position: transform.translation() + Vec3::Y * 1.9,
                text: format!("-{}", hurt.amount.round() as i32),
                color: Color::srgb(1.0, 0.32, 0.25),
            });
        }
        flash.0 = (flash.0 + if hurt.status { 0.35 } else { 0.9 }).min(1.0);
    }
}

/// Veneno: 1 tick/s enquanto activo — passa pelo mesmo caminho de dano
/// (sem i-frames, sem número), respeitando `Dying`.
#[allow(clippy::type_complexity)]
fn tick_status_system(
    time: Res<Time>,
    mut players: Query<(&mut StatusEffects, &mut Health, Option<&Dying>), With<Player>>,
    mut hurts: MessageWriter<PlayerHurt>,
) {
    let dt = time.delta_secs();
    for (mut effects, mut health, dying) in &mut players {
        let tick = tick_venom(&mut effects, dt);
        if tick > 0.0 {
            hurt_player(&mut health, None, dying, tick, true);
            hurts.write(PlayerHurt {
                amount: tick,
                status: true,
            });
        }
    }
}

/// Morte do herói → espera → respawn no ponto mais próximo, HP cheio.
#[allow(clippy::type_complexity)]
fn respawn_system(
    mut players: Query<(Entity, &mut Health, &mut Transform, Option<&mut Dying>), With<Player>>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    time: Res<Time>,
    mut commands: Commands,
    mut toasts: MessageWriter<ScriptToast>,
) {
    let dt = time.delta_secs();
    for (entity, mut health, mut transform, dying) in &mut players {
        match dying {
            Some(mut state) => {
                state.timer -= dt;
                if state.timer <= 0.0 {
                    let death_xz =
                        Vec2::new(transform.translation.x, transform.translation.z);
                    let point = nearest_respawn_point(death_xz);
                    let y = terrain
                        .as_deref()
                        .map(|t| t.sample(point.x, point.y) + 0.1)
                        .unwrap_or(transform.translation.y);
                    transform.translation = Vec3::new(point.x, y, point.y);
                    health.current = health.max;
                    commands.entity(entity).remove::<Dying>();
                    commands.entity(entity).insert(Invulnerable {
                        timer: RESPAWN_DELAY,
                    });
                    toasts.write(ScriptToast(format!(
                        "De volta à {} — levanta e luta!",
                        respawn_label(point)
                    )));
                    info!(
                        target: "viber::feedback",
                        "respawn na {} ({point:?}) — HP cheio + {RESPAWN_DELAY}s de i-frames",
                        respawn_label(point)
                    );
                }
            }
            None => {
                if health.current <= 0.0 {
                    commands.entity(entity).insert(Dying {
                        timer: RESPAWN_DELAY,
                    });
                    toasts.write(ScriptToast("Caiu em combate…".into()));
                    info!(target: "viber::feedback", "herói caiu — respawn em {RESPAWN_DELAY}s");
                }
            }
        }
    }
}

/// Decrementa a janela de i-frames.
fn decay_invulnerability(time: Res<Time>, mut frames: Query<&mut Invulnerable>) {
    let dt = time.delta_secs();
    for mut frame in &mut frames {
        frame.timer -= dt;
    }
}

/// Expira o alvo de combate (TTL) e limpa alvos mortos.
fn target_expiry_system(
    time: Res<Time>,
    mut target: ResMut<CombatTarget>,
    alive: Query<(Entity, &Health)>,
) {
    if let Some(entity) = target.entity {
        let exists = alive.get(entity).is_ok_and(|(_, hp)| hp.current > 0.0);
        if !exists {
            target.entity = None;
            target.timer = 0.0;
            return;
        }
        target.timer -= time.delta_secs();
        if target.timer <= 0.0 {
            target.entity = None;
        }
    }
}

/// Vinheta: decai e aplica as alphas (fundo + borda).
fn vignette_system(
    mut flash: ResMut<HurtFlash>,
    time: Res<Time>,
    mut q_vignette: Query<(&mut BackgroundColor, &mut BorderColor), With<HurtVignette>>,
) {
    flash.0 = (flash.0 - time.delta_secs() * 2.2).max(0.0);
    let intensity = flash.0;
    let Ok((mut bg, mut border)) = q_vignette.single_mut() else {
        return;
    };
    bg.0 = Color::srgba(0.62, 0.05, 0.05, 0.28 * intensity);
    *border = BorderColor::all(Color::srgba(0.55, 0.03, 0.03, 0.55 * intensity));
}

/// Números de dano: projecta no ecrã, sobe e desvanece.
#[allow(clippy::type_complexity)]
fn damage_numbers_system(
    time: Res<Time>,
    mut incoming: MessageReader<DamageNumberEvent>,
    mut slots: Query<(
        &mut DamageNumberSlot,
        &mut Node,
        &mut Text,
        &mut TextColor,
        &mut Visibility,
    )>,
    camera: Query<(&Camera, &GlobalTransform), With<Camera3d>>,
) {
    for event in incoming.read() {
        for (mut slot, _node, mut text, mut color, mut visibility) in &mut slots {
            if slot.active {
                continue;
            }
            slot.active = true;
            slot.world_pos = event.position;
            slot.age = 0.0;
            *text = Text::new(event.text.clone());
            color.0 = event.color;
            *visibility = Visibility::Inherited;
            info!(target: "viber::feedback", "número '{}' ativado em {:#?}", event.text, event.position);
            break;
        }
    }
    let Ok((camera, camera_transform)) = camera.single() else {
        return;
    };
    let dt = time.delta_secs();
    for (mut slot, mut node, _text, mut color, mut visibility) in &mut slots {
        if !slot.active {
            continue;
        }
        slot.age += dt;
        if slot.age >= NUMBER_LIFETIME {
            slot.active = false;
            *visibility = Visibility::Hidden;
            color.0.set_alpha(0.0);
            continue;
        }
        let projected = camera.world_to_viewport(
            camera_transform,
            slot.world_pos + Vec3::Y * (slot.age * 0.6),
        );
        let Ok(screen) = projected else {
            // pode acontecer em apps sem viewport (headless/tests)
            debug!(target: "viber::feedback", "projeção falhou: {projected:?}");
            *visibility = Visibility::Hidden;
            continue;
        };
        if slot.age - dt < 0.05 {
            info!(target: "viber::feedback", "número projetado em {screen:?}");
        }
        // world_to_viewport devolve o canto superior-esquerdo em px lógicos;
        // o texto fica um pouco acima do ponto e centrado a olho.
        node.left = Val::Px(screen.x - 18.0);
        node.top = Val::Px(screen.y - 34.0);
        let fade = (1.0 - slot.age / NUMBER_LIFETIME).clamp(0.0, 1.0);
        color.0.set_alpha(fade);
    }
}

/// Sincroniza TargetBar (alvo do melee) e BossBar (`name="boss"`).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn bars_sync_system(
    target: Res<CombatTarget>,
    names: Query<&Name>,
    creatures: Query<(Entity, &Health)>,
    bosses: Query<(&Name, &Health), Without<Player>>,
    bars: Query<(Entity, &Name, &Children)>,
    mut fills: Query<&mut Node>,
    mut labels: Query<&mut Text>,
    mut visibilities: Query<&mut Visibility>,
) {
    // TargetBar: preenchida só quando há alvo vivo.
    let target_info: BarInfo = target.entity.and_then(|entity| {
        creatures.get(entity).ok().map(|(_, hp)| {
            (
                hp.current,
                hp.max,
                names
                    .get(entity)
                    .map(|n| n.to_string())
                    .unwrap_or_else(|_| "inimigo".into()),
            )
        })
    });
    sync_bar(
        &bars,
        &mut fills,
        &mut labels,
        &mut visibilities,
        TARGET_BAR_NODE,
        target_info,
        "sem alvo",
    );

    // BossBar: barra do boss `name="boss"` enquanto vivo.
    let boss_info: BarInfo = bosses
        .iter()
        .find(|(name, hp)| name.to_lowercase() == "boss" && hp.current > 0.0)
        .map(|(name, hp)| (hp.current, hp.max, name.to_uppercase()));
    sync_bar(
        &bars,
        &mut fills,
        &mut labels,
        &mut visibilities,
        BOSS_BAR_NODE,
        boss_info,
        "BOSS",
    );
}

type BarInfo = Option<(f32, f32, String)>;

/// Preenche uma barra pelo nome do nó raiz: largura do fill (filho com
/// largura percentual) + texto do label (filho com `Text`).
#[allow(clippy::type_complexity)]
fn sync_bar(
    bars: &Query<(Entity, &Name, &Children)>,
    fills: &mut Query<&mut Node>,
    labels: &mut Query<&mut Text>,
    visibilities: &mut Query<&mut Visibility>,
    root_name: &str,
    info: BarInfo,
    empty_label: &str,
) {
    let Some((root, _name, children)) = bars
        .iter()
        .find(|(_e, name, _)| name.to_string() == root_name)
    else {
        return;
    };
    let wanted_visibility = if info.is_some() {
        Visibility::Inherited
    } else {
        Visibility::Hidden
    };
    if let Ok(mut visibility) = visibilities.get_mut(root) {
        if *visibility != wanted_visibility {
            *visibility = wanted_visibility;
        }
    }
    let Some((current, max, label_text)) = info else {
        for child in children.iter() {
            if let Ok(mut text) = labels.get_mut(child) {
                if text.0 != empty_label {
                    text.0 = empty_label.into();
                }
            }
        }
        return;
    };
    let fraction = if max > 0.0 {
        (current / max).clamp(0.0, 1.0)
    } else {
        0.0
    };
    for child in children.iter() {
        if let Ok(mut node) = fills.get_mut(child) {
            if matches!(node.width, Val::Percent(_)) {
                node.width = Val::Percent(fraction * 100.0);
            }
        }
        if let Ok(mut text) = labels.get_mut(child) {
            let wanted =
                format!("{label_text}  {}/{}", current.round() as i32, max.round() as i32);
            if text.0 != wanted {
                text.0 = wanted;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_nearest_respawn_point() {
        // morto no centro → praça
        assert_eq!(nearest_respawn_point(Vec2::new(3.0, -4.0)), Vec2::ZERO);
        // morto perto do portão leste
        assert_eq!(
            nearest_respawn_point(Vec2::new(48.0, 2.0)),
            Vec2::new(50.0, 0.0)
        );
        // morto longe no deserto → o mais próximo dos 5 ainda é escolhido
        let far = nearest_respawn_point(Vec2::new(-290.0, 12.0));
        assert_eq!(far, Vec2::new(-50.0, 0.0));
    }

    #[test]
    fn test_respawn_labels() {
        assert_eq!(respawn_label(Vec2::ZERO), "praça");
        assert_eq!(respawn_label(Vec2::new(0.0, -50.0)), "portão sul");
        assert_eq!(respawn_label(Vec2::new(0.0, 50.0)), "portão norte");
        assert_eq!(respawn_label(Vec2::new(-50.0, 0.0)), "portão oeste");
        assert_eq!(respawn_label(Vec2::new(50.0, 0.0)), "portão leste");
    }

    #[test]
    fn test_hurt_player_gates() {
        let mut hp = Health::default();
        // i-frames bloqueiam dano físico…
        let invuln = Invulnerable { timer: 0.2 };
        assert_eq!(
            hurt_player(&mut hp, Some(&invuln), None, 25.0, false),
            HurtOutcome::Blocked
        );
        assert!((hp.current - 100.0).abs() < 1e-4, "não perdeu HP");
        // …mas dano de status passa
        assert_eq!(
            hurt_player(&mut hp, Some(&invuln), None, 25.0, true),
            HurtOutcome::Applied { killed: false }
        );
        // morto (Dying) ignora tudo
        let dying = Dying { timer: 1.0 };
        assert_eq!(
            hurt_player(&mut hp, None, Some(&dying), 50.0, true),
            HurtOutcome::Ignored
        );
    }

    #[test]
    fn test_iframes_expire_by_timer() {
        // o componente permanece na entidade; é o TIMER que bloqueia —
        // janela expirada (<= 0) não pode bloquear dano físico
        let mut hp = Health::default();
        let expired = Invulnerable { timer: 0.0 };
        assert_eq!(
            hurt_player(&mut hp, Some(&expired), None, 25.0, false),
            HurtOutcome::Applied { killed: false },
            "i-frame expirado (componente presente, timer 0) não bloqueia"
        );
        assert!((hp.current - 75.0).abs() < 1e-4);
    }

    #[test]
    fn test_hurt_player_kills() {
        let mut hp = Health {
            current: 10.0,
            max: 100.0,
        };
        assert_eq!(
            hurt_player(&mut hp, None, None, 25.0, false),
            HurtOutcome::Applied { killed: true }
        );
        assert!((hp.current - 0.0).abs() < 1e-4);
    }

    #[test]
    fn test_venom_ticks_once_per_second() {
        let mut effects = StatusEffects {
            venom: 2.5,
            venom_tick: 0.0,
        };
        let mut total = 0.0;
        // 2.5 s em passos de 0.25 s (exatos em binário): ticks no 4.º e 8.º
        // passo; o veneno esgota no 10.º
        for _ in 0..10 {
            total += tick_venom(&mut effects, 0.25);
        }
        assert!((total - VENOM_DPS * 2.0).abs() < 1e-6, "total {total}");
        assert!(effects.venom <= 0.0, "veneno expirou: {}", effects.venom);
        // veneno zerado não volta a ticar
        assert_eq!(tick_venom(&mut effects, 0.25), 0.0);
    }

    #[test]
    fn test_venom_inactive_is_free() {
        let mut effects = StatusEffects::default();
        assert_eq!(tick_venom(&mut effects, 0.5), 0.0);
    }

    #[test]
    fn test_damage_number_slot_activates_and_projects() {
        let mut app = App::new();
        app.add_plugins(MinimalPlugins);
        app.add_plugins(FeedbackPlugin);
        app.world_mut().spawn((
            Camera3d::default(),
            Camera::default(),
            Projection::default(),
            GlobalTransform::from(Transform::from_xyz(0.0, 2.0, 6.0)),
        ));
        app.update(); // Startup: pool + vignette
        app.world_mut().write_message(DamageNumberEvent {
            position: Vec3::new(0.0, 1.5, 0.0),
            text: "-10".into(),
            color: Color::srgb(1.0, 0.3, 0.2),
        });
        app.update(); // leitura do evento + projeção

        let world = app.world_mut();
        let mut q = world.query::<(&DamageNumberSlot, &Visibility, &Node, &Text)>();
        let mut active = 0;
        for (slot, _visibility, _node, text) in q.iter(world) {
            if slot.active {
                active += 1;
                assert_eq!(text.0, "-10");
            }
        }
        assert_eq!(active, 1, "exatamente um slot ativado pelo evento");
        // Nota: a projeção (world_to_viewport) precisa de viewport real —
        // headless ela esconde o slot; o render é verificado in-game via
        // screenshots da bridge (validação do loop).
    }
}
