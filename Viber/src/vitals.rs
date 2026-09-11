//! Player vitals: `Health` and `Xp` components plus the debug key driver
//! (`H` −10 HP, `N` full heal, `K` +10 XP) that feeds the dynamic HUD bars.
//!
//! `J` was retired to the hero's melee attack (`combat`); `H`/`K` are free in
//! the native build (the Luau `interacted()` keys are e/j/f/q/r/space only).
//!
//! There is no real combat yet — this is the dynamic-UI pipeline: the keys
//! mutate the vitals, [`crate::hud::hud_health_sync`] /
//! [`crate::hud::hud_xp_sync`] mirror them into the `healthbar`/`xpbar`
//! fills and labels.
//!
//! The player spawn recipe (`recipes::spawn`) does not attach vitals, so
//! [`debug_damage`] *inserts* `Health`/`Xp` on the hero on the first relevant
//! key press (query with `Option` + `Commands::insert`). Until then the HUD
//! sync systems treat a missing component as the default 100/100 / 0/100.
//!
//! WIRED-BY-ORCHESTRATOR: `vitals::debug_damage` must be registered in
//! `src/main.rs` in the `Update` schedule (add `vitals::debug_damage,` to the
//! existing `app.add_systems(bevy::app::Update, (…))` tuple). This module
//! intentionally does not touch `main.rs`.
//!
//! Passe de juice r1: [`VitalsPlugin`] monta a deteção de level-up
//! ([`level_up_detector`], robusta — compara `Xp.next` com o último visto, por
//! isso apanha TODAS as fontes: kills, quests, colheita, debug, scripts) e a
//! fanfarra ([`level_up_fx`]: bursts `magic`+`sparkle` no herói, toast
//! "NÍVEL X", kick de exposição via `PostFxState` e `SfxEvent::LevelUp`).

use bevy::prelude::*;

use crate::player::Player;
use crate::profiler::{Group, timed};

/// Default HP pool (also the HUD fallback when no `Health` exists yet).
pub const DEFAULT_HEALTH: f32 = 100.0;
/// Damage per `H` press (debug driver).
pub const DEBUG_DAMAGE: f32 = 10.0;
/// XP needed for the first level (HUD fallback uses the same).
pub const DEFAULT_XP_NEXT: u32 = 100;
/// XP gain per `K` press (debug driver).
pub const DEBUG_XP_GAIN: u32 = 10;

/// Player HP pool, clamped to `0..=max` by [`apply_damage`] / [`heal_full`].
#[derive(Debug, Clone, Copy, PartialEq, Component)]
pub struct Health {
    pub current: f32,
    pub max: f32,
}

impl Default for Health {
    fn default() -> Self {
        Self {
            current: DEFAULT_HEALTH,
            max: DEFAULT_HEALTH,
        }
    }
}

/// XP progress toward the next tier; on level-up the remainder carries over
/// and `next` grows by [`xp_ramp`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Component)]
pub struct Xp {
    pub current: u32,
    pub next: u32,
}

impl Default for Xp {
    fn default() -> Self {
        Self {
            current: 0,
            next: DEFAULT_XP_NEXT,
        }
    }
}

/// Health bar fraction in `0..=1` for the UI fill (`0.0` when `max <= 0`).
pub fn health_fraction(current: f32, max: f32) -> f32 {
    if max <= 0.0 {
        return 0.0;
    }
    (current / max).clamp(0.0, 1.0)
}

/// XP bar fraction in `0..=1` for the UI fill (`0.0` when `next == 0`).
pub fn xp_fraction(current: u32, next: u32) -> f32 {
    if next == 0 {
        return 0.0;
    }
    (current as f32 / next as f32).clamp(0.0, 1.0)
}

/// Applies damage clamped to `0..=max`; negative `amount` (a heal) clamps at
/// `max` as well, so the pool never leaves its range.
///
/// `NaN`/`±inf` são ignorados: um dano não finito (via script) propagava-se ao
/// HP e o save gravava `null` — ilegível para sempre.
pub fn apply_damage(health: &mut Health, amount: f32) {
    if !amount.is_finite() {
        return;
    }
    health.current = (health.current - amount).clamp(0.0, health.max);
}

/// Full restore (debug `N`).
pub fn heal_full(health: &mut Health) {
    health.current = health.max;
}

/// Next-tier XP requirement after levelling: +50 % rounded up, never 0 —
/// the "ramp" that makes each level cost more than the last.
pub fn xp_ramp(next: u32) -> u32 {
    next.saturating_mul(3).div_ceil(2).max(1)
}

/// Adds XP, overflowing into a ramped next tier while at/over `next`
/// (carrying the remainder, like classic level systems).
pub fn gain_xp(xp: &mut Xp, gain: u32) {
    xp.current = xp.current.saturating_add(gain);
    while xp.next > 0 && xp.current >= xp.next {
        xp.current -= xp.next;
        xp.next = xp_ramp(xp.next);
    }
}

// ── level-up (passe de juice r1) ────────────────────────────────────────

/// Nível corrente do herói + o último `Xp.next` observado. Componente
/// auxiliar da deteção: `Xp` em si não conta níveis (só current/next), e o
/// `next` SÓ muda quando se sobe de nível — é esse sinais que o detector
/// compara, apanhando qualquer fonte de XP sem tocar nos chamadores.
#[derive(Debug, Clone, Copy, PartialEq, Component)]
pub struct XpLevel {
    pub level: u32,
    pub last_next: u32,
}

/// Um level-up aconteceu (uma vez por transição, com multi-levels de uma
/// só concessão colapsados num único evento com o nível final).
#[derive(Debug, Clone, Copy, bevy::ecs::message::Message)]
pub struct LevelUpEvent {
    pub new_level: u32,
}

/// Kick de exposição do level-up (EV — clareia; ver `postfx::decay_kick`).
pub const LEVELUP_KICK_EV: f32 = 0.6;

/// Quantos níveis separam dois patamares de `Xp.next`: conta aplicações de
/// [`xp_ramp`] até `new_next` BATER EXATO. Cadeias que não batem (save antigo
/// carregado por cima) devolvem 0 — o detector ressincroniza sem fanfarra
/// em vez de festejar um load.
pub fn levels_between(prev_next: u32, new_next: u32) -> u32 {
    let mut cursor = prev_next;
    let mut levels = 0;
    while cursor < new_next && levels < 64 {
        cursor = xp_ramp(cursor);
        levels += 1;
        if cursor == new_next {
            return levels;
        }
    }
    0
}

/// Deteção ROBUSTA de level-up: compara o `Xp.next` atual com o último visto
/// em [`XpLevel`]. Como `gain_xp` só mexe em `next` quando se sobe de nível,
/// esta única porta apanha TODAS as fontes (kills do melee, turn-ins de
/// quest, colheita, tecla [K], `viber.debug.xp`, scripts). A 1.ª vez que o
/// herói é visto insere o baseline em silêncio (sem fanfarra ao arrancar).
#[allow(clippy::type_complexity)]
pub fn level_up_detector(
    mut heroes: Query<(Entity, &Xp, Option<&mut XpLevel>), (With<Player>, Changed<Xp>)>,
    mut events: MessageWriter<LevelUpEvent>,
    mut commands: Commands,
) {
    for (entity, xp, level) in &mut heroes {
        match level {
            Some(mut lvl) => {
                if xp.next == lvl.last_next {
                    continue;
                }
                let gained = levels_between(lvl.last_next, xp.next);
                lvl.last_next = xp.next;
                if gained > 0 {
                    lvl.level = lvl.level.saturating_add(gained);
                    events.write(LevelUpEvent {
                        new_level: lvl.level,
                    });
                }
            }
            None => {
                commands.entity(entity).insert(XpLevel {
                    level: 1,
                    last_next: xp.next,
                });
            }
        }
    }
}

/// Spec de burst de juice sobre um preset — a mesma forma do `impact_spec`
/// do combat (privado), publicada aqui para os módulos do passe de juice
/// (`vitals`/`quests`/`travel`) não duplicarem o builder.
pub fn juice_spec(
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

/// Fanfarra do level-up: bursts `magic`+`sparkle` no herói, toast "NÍVEL X",
/// kick de exposição (+0.6 EV com decay ~1 s) e `SfxEvent::LevelUp`. Corre
/// uma vez por [`LevelUpEvent`]; sem herói visível, só o toast/SFX/kick
/// perdem a posição — os bursts são os únicos que exigem a âncora.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn level_up_fx(
    mut events: MessageReader<LevelUpEvent>,
    players: Query<&GlobalTransform, With<Player>>,
    mut postfx: Option<ResMut<crate::postfx::PostFxState>>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
    mut toasts: MessageWriter<crate::luau::ScriptToast>,
    mut commands: Commands,
) {
    for event in events.read() {
        // SFX e toast são de interface (sem posição, volume cheio).
        sfx.write(crate::ambient::SfxEvent {
            clip: crate::ambient::SfxClip::LevelUp,
            position: None,
        });
        toasts.write(crate::luau::ScriptToast(format!(
            "NÍVEL {}",
            event.new_level
        )));
        if let Some(fx) = postfx.as_deref_mut() {
            fx.kick_exposure(LEVELUP_KICK_EV);
        }
        let Some(anchor) = players.iter().next().map(|t| t.translation()) else {
            continue;
        };
        // Aura mágica a subir + faíscas douradas — o herói é a âncora.
        crate::particles::spawn_burst(
            &mut commands,
            &juice_spec(
                "magic",
                (0.35, 0.85),
                (0.5, 1.0),
                (1.2, 3.0),
                Some([0.65, 0.5, 1.0]),
            ),
            anchor + Vec3::Y * 1.0,
            12,
        );
        crate::particles::spawn_burst(
            &mut commands,
            &juice_spec(
                "sparkle",
                (0.15, 0.45),
                (0.4, 0.9),
                (2.0, 5.0),
                Some([1.0, 0.85, 0.4]),
            ),
            anchor + Vec3::Y * 1.2,
            22,
        );
    }
}

/// Plugin do passe de juice nos vitals (level-up). Registo de mensagens
/// idempotente com o Ambient/Combat/… — apps mínimas de teste ficam
/// auto-suficientes (mesmo padrão do `CombatPlugin`).
pub struct VitalsPlugin;

impl Plugin for VitalsPlugin {
    fn build(&self, app: &mut App) {
        app.add_message::<LevelUpEvent>()
            .add_message::<crate::ambient::SfxEvent>()
            .add_message::<crate::luau::ScriptToast>()
            .add_systems(
                bevy::app::Update,
                (timed(Group::World, level_up_detector), level_up_fx),
            );
    }
}

/// Debug vitals driver for the hero: `H` deals [`DEBUG_DAMAGE`], `N` fully
/// heals, `K` gains [`DEBUG_XP_GAIN`]. Inserts missing `Health`/`Xp` on the
/// first relevant press (spawn recipes are intentionally left untouched).
/// `J` belongs to the melee attack (`combat::player_melee_attack`).
///
/// O dano de `H` segue o path único do feedback (`PlayerHurt`: i-frames,
/// vinheta, número flutuante, morte/respawn) — por isso escreve a mensagem
/// em vez de aplicar directo. Necessita `Health` já presente (o melee e os
/// scripts inserem via `ensure_player_vitals`).
///
/// WIRED-BY-ORCHESTRATOR: registered in `src/main.rs` (`vitals::debug_damage`
/// in the `Update` schedule).
#[allow(clippy::type_complexity)]
pub fn debug_damage(
    keys: Res<ButtonInput<KeyCode>>,
    menus: Res<crate::menus::MenusOpen>,
    mut commands: Commands,
    mut players: Query<(Entity, Option<&mut Health>, Option<&mut Xp>), With<Player>>,
    mut hurts: bevy::ecs::message::MessageWriter<crate::feedback::PlayerHurt>,
) {
    // [K] abre a loja no shop_system; com o modal aberto não dá +10 XP
    // (tecla de debug da Fase 0 a entrar em conflito com o jogo real).
    if menus.any() {
        return;
    }
    let Ok((entity, mut health, mut xp)) = players.single_mut() else {
        return;
    };

    if keys.just_pressed(KeyCode::KeyH) {
        if health.is_none() {
            commands.entity(entity).insert(Health::default());
        }
        hurts.write(crate::feedback::PlayerHurt {
            amount: DEBUG_DAMAGE,
            status: false,
            from: None,
        });
    }
    if keys.just_pressed(KeyCode::KeyN) {
        match health.as_mut() {
            Some(hp) => heal_full(hp),
            None => {
                commands.entity(entity).insert(Health::default());
            }
        }
    }
    if keys.just_pressed(KeyCode::KeyK) {
        match xp.as_mut() {
            Some(x) => gain_xp(x, DEBUG_XP_GAIN),
            None => {
                let mut fresh = Xp::default();
                gain_xp(&mut fresh, DEBUG_XP_GAIN);
                commands.entity(entity).insert(fresh);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_health_fraction_basic() {
        assert!(approx(health_fraction(75.0, 100.0), 0.75));
        assert!(approx(health_fraction(0.0, 100.0), 0.0));
        assert!(approx(health_fraction(100.0, 100.0), 1.0));
    }

    #[test]
    fn test_health_fraction_guards() {
        // max <= 0 must not produce NaN/inf (bar would vanish or explode)
        assert!(approx(health_fraction(50.0, 0.0), 0.0));
        assert!(approx(health_fraction(50.0, -10.0), 0.0));
        // current outside the pool clamps into 0..=1
        assert!(approx(health_fraction(120.0, 100.0), 1.0));
        assert!(approx(health_fraction(-5.0, 100.0), 0.0));
    }

    #[test]
    fn test_apply_damage_clamps_at_zero() {
        let mut hp = Health {
            current: 25.0,
            max: 100.0,
        };
        for _ in 0..4 {
            apply_damage(&mut hp, DEBUG_DAMAGE);
        }
        assert!(approx(hp.current, 0.0), "clamps at 0, got {}", hp.current);
        // extra hits stay at 0 — never negative
        apply_damage(&mut hp, DEBUG_DAMAGE);
        assert!(approx(hp.current, 0.0));
    }

    #[test]
    fn test_heal_full_and_overheal_clamp_at_max() {
        let mut hp = Health {
            current: 30.0,
            max: 100.0,
        };
        heal_full(&mut hp);
        assert!(approx(hp.current, 100.0));
        // negative damage = heal, also clamped at max
        apply_damage(&mut hp, -50.0);
        assert!(approx(hp.current, 100.0));
    }

    #[test]
    fn test_gain_xp_accumulates() {
        let mut xp = Xp::default();
        for _ in 0..3 {
            gain_xp(&mut xp, DEBUG_XP_GAIN);
        }
        assert_eq!(
            xp,
            Xp {
                current: 30,
                next: 100
            }
        );
    }

    #[test]
    fn test_gain_xp_ramps_next_tier() {
        // 95/100 + 10 → level up: remainder 5 carries, next ramps +50 % → 150
        let mut xp = Xp {
            current: 95,
            next: 100,
        };
        gain_xp(&mut xp, 10);
        assert_eq!(
            xp,
            Xp {
                current: 5,
                next: 150
            }
        );
        // keep going to the second ramp: 5 + 145 → current 0 at tier 150,
        // next ramps again to 225
        gain_xp(&mut xp, 145);
        assert_eq!(
            xp,
            Xp {
                current: 0,
                next: 225
            }
        );
    }

    #[test]
    fn test_xp_fraction_guards() {
        assert!(approx(xp_fraction(30, 100), 0.3));
        // division guard: next == 0 → 0.0, never NaN/inf
        assert!(approx(xp_fraction(10, 0), 0.0));
        // clamped into 0..=1
        assert!(approx(xp_fraction(200, 100), 1.0));
    }

    #[test]
    fn test_xp_ramp_never_zero() {
        assert_eq!(xp_ramp(100), 150);
        assert_eq!(xp_ramp(150), 225);
        assert_eq!(xp_ramp(1), 2); // +50 % rounds up, min 1
    }

    // ── level-up (passe de juice r1) ────────────────────────────────────

    #[derive(Resource, Default)]
    struct SeenLevelUps(Vec<u32>);

    fn record_level_ups(mut reader: MessageReader<LevelUpEvent>, mut seen: ResMut<SeenLevelUps>) {
        for event in reader.read() {
            seen.0.push(event.new_level);
        }
    }

    fn detector_app() -> (App, Entity) {
        let mut app = App::default();
        app.add_message::<LevelUpEvent>();
        app.init_resource::<SeenLevelUps>();
        app.add_systems(Update, (level_up_detector, record_level_ups).chain());
        let hero = app
            .world_mut()
            .spawn((Player::default(), Xp::default()))
            .id();
        (app, hero)
    }

    fn grant(app: &mut App, hero: Entity, amount: u32) {
        let mut xp = app.world_mut().get_mut::<Xp>(hero).unwrap();
        gain_xp(&mut xp, amount);
    }

    #[test]
    fn test_level_up_detector_fires_once_per_transition() {
        let (mut app, hero) = detector_app();
        // 1.ª passagem: baseline silencioso (sem fanfarra ao arrancar).
        app.update();
        assert!(app.world().resource::<SeenLevelUps>().0.is_empty());

        // XP sem cruzar o patamar: nada.
        grant(&mut app, hero, 30); // 30/100
        app.update();
        assert!(
            app.world().resource::<SeenLevelUps>().0.is_empty(),
            "XP sem transição não fanfarra"
        );

        // Cruza o patamar: exatamente UMA mensagem, nível 2.
        grant(&mut app, hero, 95); // 25/150
        app.update();
        assert_eq!(app.world().resource::<SeenLevelUps>().0, vec![2]);

        // Re-update sem nova concessão: NÃO re-dispara.
        app.update();
        assert_eq!(
            app.world().resource::<SeenLevelUps>().0.len(),
            1,
            "1 vez por transição"
        );
    }

    #[test]
    fn test_level_up_detector_collapses_multi_level_and_catches_all_sources() {
        let (mut app, hero) = detector_app();
        app.update(); // baseline
        // Concessão enorme (500 XP a 5/150) sobe DOIS níveis de uma vez —
        // colapsa num único evento com o nível FINAL.
        grant(&mut app, hero, 500); // 130/337, níveis 2→4
        app.update();
        assert_eq!(app.world().resource::<SeenLevelUps>().0, vec![4]);
        // E a tecla de debug/quests/etc. usam o MESMO caminho (mutação em Xp)
        // — a deteção é pela mudança de `next`, não por fonte.
        grant(&mut app, hero, 1000); // vários níveis outra vez
        app.update();
        let seen = &app.world().resource::<SeenLevelUps>().0;
        assert_eq!(seen.len(), 2, "um evento por frame de transição");
        assert!(seen[1] > seen[0], "nível é monotónico: {seen:?}");
    }

    #[test]
    fn test_levels_between_counts_exact_ramp_chains_only() {
        assert_eq!(levels_between(100, 150), 1);
        assert_eq!(levels_between(100, 225), 2);
        assert_eq!(levels_between(150, 225), 1);
        assert_eq!(levels_between(100, 338), 3); // 100→150→225→338
        // Mesmo patamar / regressão (save antigo) / cadeia que não bate
        // (load arbitrária): 0 — o detector ressincroniza sem fanfarra.
        assert_eq!(levels_between(150, 150), 0);
        assert_eq!(levels_between(225, 150), 0);
        assert_eq!(levels_between(100, 999), 0);
        assert_eq!(levels_between(0, 0), 0);
    }
}
