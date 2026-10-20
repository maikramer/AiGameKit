//! Data binding: engine state → declarative UI elements.
//!
//! An element written as `<UiBar bind="health"/>` or `<UiText bind="gold"/>`
//! is fed once per frame from the game's own components and resources, with no
//! Rust needed per HUD. This is what lets the whole simple-rpg interface live
//! in XML: the *values* come from bindings, the *look* from the stylesheet, and
//! only genuinely game-specific behaviour goes into a Luau script.
//!
//! A binding produces one [`BoundValue`]; how the element consumes it depends
//! on what the element is:
//!
//! * `UiBar` / `UiCooldown` → the 0..1 fraction;
//! * `UiText` → the formatted string;
//! * anything else → the truthiness drives `Visibility`.
//!
//! ## Bindings de estado do herói e do mundo
//!
//! Os três binds "derivados" que o HUD lê para estados de jogo:
//!
//! | bind           | tipo  | semântica                                                    |
//! |----------------|-------|--------------------------------------------------------------|
//! | `health.low`   | flag  | vida/máx ≤ 0.30 — o card de vitais fica em alerta (`danger`) |
//! | `status.venom` | flag  | veneno activo no herói (`StatusEffects.venom > 0`)           |
//! | `zone.name`    | texto | nome de exposição da zona (`display-name` do BiomeRegion → tabela → id) |
//! | `weather.rain` | fração | intensidade de chuva do `<Weather>` (0 = seco, 1 = tempestade) |
//! | `weather.wet`  | flag  | rain > 0.05 — liga o chip de chuva no card de ambiente       |
//! | `talent.ready` | flag  | pontos de talento por gastar (`LevelState.points > 0`)       |
//! | `cd.*.ready`   | flag  | cooldown terminado — acende o slot (`cd.dash.ready:ready`)   |
//! | `*.empty`      | flag  | consumível esgotado (`potion.empty:empty` cinzeia o vial)    |
//!
//! ## Bindings de classe: `nome:classe`
//!
//! Um `bind="health.low:danger"` não mostra nem esconde nada — troca uma
//! **classe** no próprio elemento conforme a flag (`apply_bind_classes`,
//! idempotente, padrão apply-fresh: só toca o `UiClasses`/`UiStyleDirty`
//! quando o estado muda). É o caminho engine-driven dos toggles que antes
//! viviam em Luau (`viber.ui.toggle_class`).
//!
//! Unknown binding names are reported once and then ignored, so a typo costs a
//! log line rather than a blank HUD.

use std::collections::HashSet;

use bevy::prelude::*;

use super::runtime::{UiBar, UiBind, UiClasses, UiCooldown, UiStyleDirty};
use super::script::UiScriptBinds;

/// One frame's snapshot of everything bindings can read.
///
/// Collected once per frame so a hundred bound elements cost one pass over the
/// game state instead of a hundred queries.
#[derive(Debug, Clone, Default, Resource)]
pub struct UiData {
    pub health: f32,
    pub health_max: f32,
    pub xp: f32,
    pub xp_next: f32,
    pub level: u32,
    pub gold: u32,
    pub wood: u32,
    pub stone: u32,
    /// Ability cooldowns as *remaining fractions* (1 = just fired, 0 = ready).
    pub cd_dash: f32,
    pub cd_heal: f32,
    pub cd_strike: f32,
    /// Soft-locked combat target, when one is live.
    pub target_name: String,
    pub target_health: f32,
    pub target_health_max: f32,
    /// In-world clock, as `HH:MM`.
    pub clock: String,
    /// Fraction of the day elapsed (0 = midnight) — drives the day/night dial.
    pub day_fraction: f32,
    /// Nearest interactable's prompt, empty when there is nothing to press.
    pub prompt_key: String,
    pub prompt_label: String,
    /// Currently tracked quest.
    pub quest_title: String,
    pub quest_progress: f32,
    pub quest_progress_text: String,
    /// Newest toast and how long it still has to live.
    pub toast: String,
    pub toast_time: f32,
    /// Consumable stacks on the hotbar.
    pub potions: u32,
    pub antidotes: u32,
    pub bombs: u32,
    /// Sementes de canção coletadas (colecionáveis) — `korok-seed`.
    pub korok_seeds: u32,
    /// Combo counter do melee: hits acertados na janela (0 = escondido).
    pub combo_hits: u32,
    /// Veneno activo no herói (`status.venom`) — segundos restantes > 0.
    pub status_venom: bool,
    /// Chuva contínua do `<Weather>` (0 = seco, 1 = tempestade) — `weather.rain`.
    pub weather_rain: f32,
    /// Pontos de talento por gastar (`LevelState.points`) — `talent.points`.
    pub talent_points: u32,
    // ── janelas de revelação ────────────────────────────────────────────
    // Segundos que ainda faltam a um widget contextual antes de se apagar.
    // A regra do HUD é: **permanente = vida + mapa**; tudo o resto entra
    // quando muda e sai sozinho. Estes contadores são a memória disso.
    /// Ouro/madeira/pedra mudaram há pouco.
    pub purse_reveal: f32,
    /// Poções/antídotos/bombas mudaram há pouco.
    pub belt_reveal: f32,
    /// XP subiu há pouco (ou o nível).
    pub xp_reveal: f32,
    /// A missão activa mudou de título ou de progresso há pouco.
    pub quest_reveal: f32,
    /// Houve combate (alvo vivo, dano recebido) há pouco.
    pub combat_reveal: f32,
    /// Nome legível da zona em que o herói acabou de entrar.
    pub zone_name: String,
    /// Quanto tempo o cartão de descoberta ainda tem de vida.
    pub zone_reveal: f32,
}

impl UiData {
    /// Resolves a binding name. `None` means the name is unknown.
    pub fn get(&self, name: &str) -> Option<BoundValue> {
        let value = match name {
            // Vida, ouro, quests, cooldowns e alvo são do jogo: o script
            // publica-os com `viber.ui.set`. A engine só conhece o mundo.
            "weather.rain" => BoundValue::fraction(self.weather_rain.clamp(0.0, 1.0)),
            "weather.wet" => BoundValue::flag(self.weather_rain > 0.05),
            "clock" => BoundValue::text(self.clock.clone()),
            "day" => BoundValue::fraction(self.day_fraction),
            "prompt.key" => BoundValue::text(self.prompt_key.clone()),
            "prompt.label" => BoundValue::text(self.prompt_label.clone()),
            "prompt.active" => BoundValue::flag(!self.prompt_label.is_empty()),
            "toast" => BoundValue::text(self.toast.clone()),
            "toast.active" => BoundValue::flag(self.toast_time > 0.0 && !self.toast.is_empty()),
            "zone.name" => BoundValue::text(self.zone_name.clone()),
            "zone.active" => BoundValue::flag(self.zone_reveal > 0.0 && !self.zone_name.is_empty()),
            _ => return None,
        };
        Some(value)
    }
}

/// A resolved binding: every consumer picks the projection it needs.
#[derive(Debug, Clone)]
pub struct BoundValue {
    /// 0..1 for bars and cooldown veils.
    pub fraction: f32,
    /// Formatted text for `UiText`.
    pub text: String,
    /// Drives visibility for plain elements.
    pub truthy: bool,
}

impl BoundValue {
    fn fraction(value: f32) -> Self {
        Self {
            fraction: value,
            text: format!("{:.0}%", value * 100.0),
            truthy: value > 0.0,
        }
    }

    fn text(value: String) -> Self {
        Self {
            fraction: 0.0,
            truthy: !value.is_empty(),
            text: value,
        }
    }

    fn flag(value: bool) -> Self {
        Self {
            fraction: if value { 1.0 } else { 0.0 },
            text: if value { "1".into() } else { String::new() },
            truthy: value,
        }
    }
}

/// Names already reported as unknown, so a typo logs once and not every frame.
#[derive(Debug, Default, Resource)]
pub struct UiBindWarnings(HashSet<String>);

/// Segundos de jogo antes de um bind em falta ser aviso: os binds de script
/// (`viber.ui.set`) só existem depois do 1.º `on_update` — avisar no frame 0
/// enchia o arranque de falsos "unknown binding" (`boss`, `ab.*`, …).
pub const BIND_WARN_GRACE_SECS: f32 = 3.0;

/// `true` se `key` deve ser avisado AGORA (fora da graça e ainda não dito).
pub fn should_warn_unknown(seen: &mut HashSet<String>, key: &str, elapsed: f32) -> bool {
    elapsed >= BIND_WARN_GRACE_SECS && seen.insert(key.to_owned())
}

/// Splits a class binding `nome:classe` into `(nome, classe)`.
///
/// Devolve `None` para binds normais (nenhum nome de binding contém `:` —
/// os nomes usam pontos). A classe sai sem espaços; o `UiClasses` faz o
/// lowercase quando a guarda.
pub fn split_class_bind(bind: &str) -> Option<(&str, &str)> {
    let (name, class) = bind.split_once(':')?;
    let class = class.trim();
    (!name.is_empty() && !class.is_empty()).then_some((name, class))
}

/// Applies class bindings (`bind="nome:classe"`): the bound flag toggles the
/// class on the element itself.
///
/// Idempotente por construção — [`UiClasses::set_class`] só devolve `true`
/// quando a lista mudou, e só nesse caso o elemento é marcado
/// [`UiStyleDirty`] para a cascata repintar (padrão apply-fresh: o estado
/// novo é recalculado do zero a cada frame, a escrita é que é rara).
#[allow(clippy::type_complexity)]
/// Resolve um nome: ENGINE primeiro, binds de script (`viber.ui.set`)
/// depois. `None` = desconhecido nos dois. Partilhado com o fade
/// (`ui::fade::drive_ui_fades`) — um `fade=` ligado a um bind de script
/// tem de acender como qualquer outro elemento.
pub(crate) fn resolve_binding(
    data: &UiData,
    script_binds: Option<&UiScriptBinds>,
    name: &str,
) -> Option<BoundValue> {
    data.get(name).or_else(|| {
        script_binds
            .and_then(|binds| binds.get(name))
            .map(|value| BoundValue {
                fraction: value.number() as f32,
                text: value.text(),
                truthy: value.flag(),
            })
    })
}

pub fn apply_bind_classes(
    mut commands: Commands,
    data: Res<UiData>,
    // Option: apps mínimas de teste não registam os binds de script.
    script_binds: Option<Res<UiScriptBinds>>,
    mut warned: ResMut<UiBindWarnings>,
    time: Option<Res<Time>>,
    mut binds: Query<(Entity, &UiBind, &mut UiClasses)>,
) {
    let elapsed = time.map_or(f32::MAX, |t| t.elapsed_secs());
    let script_binds = script_binds.as_deref();
    for (entity, bind, mut classes) in &mut binds {
        let Some((name, class)) = split_class_bind(&bind.0) else {
            continue;
        };
        let Some(value) = resolve_binding(&data, script_binds, name) else {
            if should_warn_unknown(&mut warned.0, &bind.0, elapsed) {
                warn!("ui: unknown binding `{name}` — class `{class}` left untouched");
            }
            continue;
        };
        if classes.set_class(class, value.truthy) {
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
}

/// Pushes [`UiData`] into every bound element.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn apply_ui_bindings(
    data: Res<UiData>,
    script_binds: Option<Res<UiScriptBinds>>,
    mut warned: ResMut<UiBindWarnings>,
    time: Option<Res<Time>>,
    mut bars: Query<(&UiBind, &mut UiBar)>,
    mut cooldowns: Query<(&UiBind, &mut UiCooldown)>,
    mut texts: Query<(&UiBind, &mut Text)>,
    // Faded elements are driven by `ui::fade::drive_ui_fades` instead: hard
    // toggling their `Visibility` here would pop them in before the dissolve.
    mut others: Query<
        (&UiBind, &mut Visibility),
        (
            Without<UiBar>,
            Without<UiCooldown>,
            Without<Text>,
            Without<super::fade::UiFade>,
        ),
    >,
) {
    let script_binds = script_binds.as_deref();
    let elapsed = time.map_or(f32::MAX, |t| t.elapsed_secs());
    let mut resolve = |name: &str| -> Option<BoundValue> {
        // Class binds (`nome:classe`) são do `apply_bind_classes`, não de
        // valor — nunca chegam a "unknown binding".
        if split_class_bind(name).is_some() {
            return None;
        }
        match resolve_binding(&data, script_binds, name) {
            Some(value) => Some(value),
            None => {
                if should_warn_unknown(&mut warned.0, name, elapsed) {
                    warn!("ui: unknown binding `{name}` — element left untouched");
                }
                None
            }
        }
    };
    for (bind, mut bar) in &mut bars {
        if let Some(value) = resolve(&bind.0) {
            // Change detection drives the fill sync, so only write on change.
            if (bar.value - value.fraction).abs() > 1e-4 {
                bar.value = value.fraction;
            }
        }
    }
    for (bind, mut cooldown) in &mut cooldowns {
        if let Some(value) = resolve(&bind.0)
            && (cooldown.value - value.fraction).abs() > 1e-4
        {
            cooldown.value = value.fraction;
        }
    }
    for (bind, mut text) in &mut texts {
        if let Some(value) = resolve(&bind.0)
            && text.0 != value.text
        {
            text.0 = value.text;
        }
    }
    for (bind, mut visibility) in &mut others {
        if let Some(value) = resolve(&bind.0) {
            let wanted = if value.truthy {
                Visibility::Inherited
            } else {
                Visibility::Hidden
            };
            if *visibility != wanted {
                *visibility = wanted;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> UiData {
        UiData {
            health: 45.0,
            health_max: 100.0,
            xp: 30.0,
            xp_next: 120.0,
            level: 3,
            gold: 12,
            cd_dash: 0.5,
            target_name: "Lobo".into(),
            target_health: 20.0,
            target_health_max: 80.0,
            clock: "07:15".into(),
            ..Default::default()
        }
    }

    #[test]
    fn test_game_bindings_are_not_engine_bindings() {
        let data = sample();
        for name in [
            "health",
            "gold",
            "xp",
            "quest.title",
            "target.name",
            "status.venom",
        ] {
            assert!(data.get(name).is_none(), "{name} é do script");
        }
    }

    #[test]
    fn test_readiness_empty_and_weather_bindings() {
        let mut data = sample();
        // Weather: seco = wet falso; acima do limiar liga; fração satura.
        assert!(!data.get("weather.wet").unwrap().truthy);
        assert_eq!(data.get("weather.rain").unwrap().fraction, 0.0);
        data.weather_rain = 0.4;
        assert!(data.get("weather.wet").unwrap().truthy);
        assert!((data.get("weather.rain").unwrap().fraction - 0.4).abs() < 1e-6);
        data.weather_rain = 2.5;
        assert_eq!(data.get("weather.rain").unwrap().fraction, 1.0);
    }

    #[test]
    fn test_text_bindings_format_for_a_hud() {
        let data = sample();
        assert_eq!(data.get("clock").unwrap().text, "07:15");
        assert!(data.get("gold").is_none());
    }

    #[test]
    fn test_flag_bindings_gate_visibility() {
        let data = sample();
        assert!(!data.get("prompt.active").unwrap().truthy);
        assert!(data.get("health.low").is_none());
    }

    #[test]
    fn test_split_class_bind_only_matches_name_class_pairs() {
        assert_eq!(
            split_class_bind("health.low:danger"),
            Some(("health.low", "danger"))
        );
        assert_eq!(
            split_class_bind("combat.active:shown "),
            Some(("combat.active", "shown"))
        );
        // Binds normais nunca têm `:`.
        assert_eq!(split_class_bind("health"), None);
        assert_eq!(split_class_bind("zone.name"), None);
        // Meio par vazio também não é class bind.
        assert_eq!(split_class_bind(":danger"), None);
        assert_eq!(split_class_bind("health:"), None);
    }

    #[test]
    fn test_class_bind_toggles_the_class_without_popping_visibility() {
        let mut app = App::new();
        app.init_resource::<UiData>()
            .init_resource::<UiBindWarnings>()
            .add_systems(Update, (apply_ui_bindings, apply_bind_classes).chain());
        let card = app
            .world_mut()
            .spawn((
                UiBind("weather.wet:danger".into()),
                UiClasses::parse("hud-card vitals"),
                Visibility::Inherited,
            ))
            .id();
        // Seco: sem classe, e o bind de classe NÃO é reportado como
        // desconhecido nem esconde o elemento.
        app.world_mut().resource_mut::<UiData>().weather_rain = 0.0;
        app.update();
        assert!(
            app.world()
                .get::<UiClasses>(card)
                .unwrap()
                .0
                .iter()
                .all(|c| c != "danger")
        );
        assert_eq!(
            *app.world().get::<Visibility>(card).unwrap(),
            Visibility::Inherited
        );
        assert!(app.world().resource::<UiBindWarnings>().0.is_empty());

        // Chuva: a classe entra (idempotente no segundo frame).
        app.world_mut().resource_mut::<UiData>().weather_rain = 0.4;
        app.update();
        app.update();
        assert!(app.world().get::<UiClasses>(card).unwrap().has("danger"));

        // Secou: a classe sai.
        app.world_mut().resource_mut::<UiData>().weather_rain = 0.0;
        app.update();
        assert!(!app.world().get::<UiClasses>(card).unwrap().has("danger"));
    }

    #[test]
    fn test_unknown_binding_is_reported_not_guessed() {
        assert!(sample().get("helth").is_none());
    }

    #[test]
    fn test_every_documented_binding_resolves() {
        // Guards against a binding being renamed in `get` but left in a world.
        let data = sample();
        for name in [
            "clock",
            "day",
            "prompt.key",
            "prompt.label",
            "prompt.active",
            "toast",
            "toast.active",
            "zone.name",
            "zone.active",
            "weather.rain",
            "weather.wet",
        ] {
            assert!(data.get(name).is_some(), "binding `{name}` disappeared");
        }
    }
}
