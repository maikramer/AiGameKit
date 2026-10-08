//! Reveal/conceal animation for bound elements.
//!
//! A HUD that shows everything all the time is a HUD nobody reads. The
//! discipline this module buys is: **health and the map are permanent, the rest
//! appears when it means something and leaves on its own.** An element written
//! as
//!
//! ```xml
//! <UiRow id="purse" bind="purse.recent" fade="0.18 0.5"> … </UiRow>
//! ```
//!
//! is invisible until `purse.recent` goes true, dissolves in over 0.18 s, and
//! dissolves out over 0.5 s when the reveal window closes.
//!
//! Without this, a flag binding drives [`Visibility`] and the element pops in
//! and out — which reads as a bug, not as a decision. The alpha is *inherited*:
//! [`crate::ui::runtime::apply_ui_styles`] already walks the ancestor chain for
//! the cascade, so multiplying each ancestor's [`UiFade::alpha`] into the
//! resolved `opacity` fades a whole widget — panel, border, icon and label — as
//! one object.
//!
//! A third number in the spec is a **floor**: `fade="0.25 1.8 0.7"` never lets
//! the alpha sink below 0.7 when the binding is false. Elements whose absence
//! the player reads as a broken HUD (hearts, purse, belt) take a floor — the
//! contextual state becomes a dim/bright *emphasis*, not a vanishing act. Only
//! genuinely transient widgets (XP, readouts) dissolve all the way out.

use bevy::prelude::*;

use super::bind::{UiData, resolve_binding};
use super::script::UiScriptBinds;
use super::runtime::{UiBind, UiStyleDirty};

/// Fade state of one element (and, through inheritance, its subtree).
#[derive(Debug, Clone, Component)]
pub struct UiFade {
    /// Seconds to go from invisible to fully drawn.
    pub in_secs: f32,
    /// Seconds to dissolve out. Slower than the fade in: an arrival should be
    /// quick, a departure should not be noticed.
    pub out_secs: f32,
    /// Current alpha, 0..1.
    pub alpha: f32,
    /// Where the alpha is heading (the binding's truth this frame).
    pub shown: bool,
    /// Minimum alpha while the binding is false. Zero means the element may
    /// vanish outright; ~0.7 keeps a permanently-relevant widget on screen at
    /// a reading dimness and lets the binding brighten it to full.
    pub floor: f32,
}

impl Default for UiFade {
    fn default() -> Self {
        Self {
            in_secs: DEFAULT_IN_SECS,
            out_secs: DEFAULT_OUT_SECS,
            alpha: 0.0,
            shown: false,
            floor: 0.0,
        }
    }
}

/// Default fade-in when `fade` is written without numbers.
pub const DEFAULT_IN_SECS: f32 = 0.18;
/// Default fade-out; deliberately ~3× the fade-in.
pub const DEFAULT_OUT_SECS: f32 = 0.55;

impl UiFade {
    /// Parses the `fade="…"` attribute: empty → defaults, one number → fade-in
    /// (out is 3×), two numbers → in and out, a third is the alpha floor.
    pub fn parse(spec: &str) -> Self {
        let mut numbers = spec
            .split_whitespace()
            .filter_map(|token| token.parse::<f32>().ok());
        let first = numbers.next();
        let in_secs = first.unwrap_or(DEFAULT_IN_SECS).max(0.0);
        let out_secs = match numbers.next() {
            Some(out) => out.max(0.0),
            // Um número: a saída é 3× a entrada. Nenhum: os defaults — que
            // são o mesmo par do `Default`, não "in × 3" (0.18 × 3 = 0.54
            // desviava um milésimo do constante e o teste apanhou-o).
            None if first.is_some() => in_secs * 3.0,
            None => DEFAULT_OUT_SECS,
        };
        // O piso nunca chega a 1 — acima disso nem é piso, é opacidade fixa.
        let floor = numbers.next().unwrap_or(0.0).clamp(0.0, 0.99);
        Self {
            in_secs,
            out_secs,
            // Nasce assente no piso: um widget com piso está no ecrã DESDE o
            // primeiro frame — "vejo sempre o meu estado" não pode esperar
            // por um dissolve de arranque.
            alpha: floor,
            shown: false,
            floor,
        }
    }

    /// Advances the alpha one frame. Returns the new alpha.
    pub fn step(&mut self, dt: f32) -> f32 {
        let target = if self.shown { 1.0 } else { self.floor };
        let seconds = if self.shown {
            self.in_secs
        } else {
            self.out_secs
        };
        if seconds <= 1e-4 {
            self.alpha = target;
            return self.alpha;
        }
        let step = dt / seconds;
        if self.alpha < target {
            self.alpha = (self.alpha + step).min(target);
        } else if self.alpha > target {
            self.alpha = (self.alpha - step).max(target);
        }
        self.alpha
    }

    /// Below this the element is hidden outright, so a faded-out widget costs
    /// no layout and swallows no clicks. A floored element never goes away —
    /// that is the point of the floor.
    pub fn is_gone(&self) -> bool {
        self.floor <= 0.0 && self.alpha <= 1e-3 && !self.shown
    }
}

/// Drives every [`UiFade`] from its binding and marks the element dirty while
/// the alpha is still moving, so the cascade repaints it.
#[allow(clippy::type_complexity)]
pub fn drive_ui_fades(
    mut commands: Commands,
    time: Res<Time>,
    data: Res<UiData>,
    // Binds de script (`viber.ui.set`) — sem isto um `fade=` num bind de
    // script ficava apagado para sempre ("unknown binding"). Option: apps
    // mínimas de teste não registam os binds de script.
    script_binds: Option<Res<UiScriptBinds>>,
    // Warn 1× por binding desconhecido (padrão do `UiBindWarnings`): um
    // elemento com fade está EXCLUÍDO da query do `apply_ui_bindings` —
    // togglar a `Visibility` dele em duro partiria o dissolve — pelo que o
    // aviso de binding errado só pode viver aqui. Sem isto, um typo no `bind`
    // deixava o elemento no sítio para sempre e calado.
    mut warned: Local<std::collections::HashSet<String>>,
    mut fades: Query<(
        Entity,
        Option<&UiBind>,
        &mut UiFade,
        &mut Visibility,
        Has<UiStyleDirty>,
    )>,
) {
    let dt = time.delta_secs();
    let script_binds = script_binds.as_deref();
    for (entity, bind, mut fade, mut visibility, already_dirty) in &mut fades {
        if let Some(bind) = bind {
            match resolve_binding(&data, script_binds, &bind.0) {
                Some(value) => fade.shown = value.truthy,
                None => {
                    if warned.insert(bind.0.clone()) {
                        warn!("ui: unknown binding `{}` — fade left untouched", bind.0);
                    }
                }
            }
        }
        let before = fade.alpha;
        let alpha = fade.step(dt);
        let wanted = if fade.is_gone() {
            Visibility::Hidden
        } else {
            Visibility::Inherited
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
        if (alpha - before).abs() > 1e-4 && !already_dirty {
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_reads_one_or_two_numbers() {
        let default = UiFade::parse("");
        assert_eq!(default.in_secs, DEFAULT_IN_SECS);
        assert_eq!(default.out_secs, DEFAULT_OUT_SECS);
        assert_eq!(default.floor, 0.0);
        // One number: the fade out is the slow one, three times the arrival.
        let one = UiFade::parse("0.2");
        assert!((one.in_secs - 0.2).abs() < 1e-6);
        assert!((one.out_secs - 0.6).abs() < 1e-6);
        let two = UiFade::parse("0.1 0.9");
        assert!((two.in_secs - 0.1).abs() < 1e-6);
        assert!((two.out_secs - 0.9).abs() < 1e-6);
        // A third number is the floor, clamped out of nonsense territory.
        let floored = UiFade::parse("0.25 1.8 0.7");
        assert!((floored.floor - 0.7).abs() < 1e-6);
        assert!((floored.alpha - 0.7).abs() < 1e-6, "born at the floor");
        assert_eq!(UiFade::parse("0.2 0.6 5").floor, 0.99);
        assert_eq!(UiFade::parse("0.2 0.6 -1").floor, 0.0);
        // Garbage never yields a negative or NaN duration.
        let bad = UiFade::parse("wat");
        assert!(bad.in_secs >= 0.0 && bad.out_secs >= 0.0);
    }

    /// Um `fade=` ligado a um bind de SCRIPT (`viber.ui.set`) acende como
    /// um bind da engine — antes o fade só lia o `UiData` e a barra de chefe
    /// do simple-rpg (bind `boss.active`) nunca aparecia.
    #[test]
    fn test_fade_follows_a_script_bind() {
        let mut app = App::new();
        let mut binds = UiScriptBinds::default();
        binds
            .0
            .insert("boss.active".into(), super::super::script::ScriptBindValue::Flag(true));
        app.init_resource::<Time>()
            .init_resource::<UiData>()
            .insert_resource(binds)
            .add_systems(Update, drive_ui_fades);
        let element = app
            .world_mut()
            .spawn((
                UiBind("boss.active".into()),
                UiFade::parse("0.2 0.6"),
                Visibility::Hidden,
            ))
            .id();
        app.update();
        assert!(
            app.world().get::<UiFade>(element).unwrap().shown,
            "o bind de script tem de ligar o fade"
        );
    }

    #[test]
    fn test_step_walks_towards_the_binding_and_stops() {
        let mut fade = UiFade::parse("0.5 0.5");
        fade.shown = true;
        for _ in 0..10 {
            fade.step(0.1);
        }
        assert!((fade.alpha - 1.0).abs() < 1e-4, "alpha={}", fade.alpha);
        assert!(!fade.is_gone());
        fade.shown = false;
        for _ in 0..4 {
            fade.step(0.1);
        }
        // Still dissolving after 0.4 s of a 0.5 s fade out.
        assert!(fade.alpha > 0.0 && fade.alpha < 0.5, "alpha={}", fade.alpha);
        for _ in 0..4 {
            fade.step(0.1);
        }
        assert_eq!(fade.alpha, 0.0);
        assert!(fade.is_gone());
    }

    #[test]
    fn test_floor_never_lets_a_widget_leave_the_screen() {
        // The hearts rule: brighten fast in combat, melt back to a readable
        // floor, never vanish.
        let mut hearts = UiFade::parse("0.25 1.8 0.7");
        hearts.shown = true;
        for _ in 0..60 {
            hearts.step(0.1);
        }
        assert!((hearts.alpha - 1.0).abs() < 1e-4, "combat is full alpha");
        hearts.shown = false;
        for _ in 0..600 {
            hearts.step(0.1);
        }
        assert!((hearts.alpha - 0.7).abs() < 1e-4, "alpha={}", hearts.alpha);
        assert!(!hearts.is_gone(), "floored widgets are never gone");
        // A floor also survives the zero-duration snap.
        let mut snap = UiFade::parse("0 0 0.7");
        snap.shown = false;
        assert_eq!(snap.step(0.016), 0.7);
        // No floor: the old all-the-way-out behaviour is untouched.
        let mut xp = UiFade::parse("0.25 0.6");
        xp.shown = false;
        for _ in 0..60 {
            xp.step(0.1);
        }
        assert_eq!(xp.alpha, 0.0);
        assert!(xp.is_gone());
    }

    #[test]
    fn test_zero_duration_snaps() {
        let mut fade = UiFade::parse("0 0");
        fade.shown = true;
        assert_eq!(fade.step(0.016), 1.0);
        fade.shown = false;
        assert_eq!(fade.step(0.016), 0.0);
    }
}
