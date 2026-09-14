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
    /// Fractions are clamped and guard against a zero maximum, so a bar never
    /// renders NaN-wide during the frame an entity is still initialising.
    fn fraction(current: f32, max: f32) -> f32 {
        if max <= 0.0 {
            return 0.0;
        }
        (current / max).clamp(0.0, 1.0)
    }

    /// Resolves a binding name. `None` means the name is unknown.
    pub fn get(&self, name: &str) -> Option<BoundValue> {
        let value = match name {
            "health" => BoundValue::fraction(Self::fraction(self.health, self.health_max)),
            "health.text" => BoundValue::text(format!(
                "{}/{}",
                self.health.max(0.0).round(),
                self.health_max.round()
            )),
            "health.value" => BoundValue::number(self.health.max(0.0).round()),
            "health.low" => BoundValue::flag(Self::fraction(self.health, self.health_max) <= 0.3),
            "status.venom" => BoundValue::flag(self.status_venom),
            "weather.rain" => BoundValue::fraction(self.weather_rain.clamp(0.0, 1.0)),
            "weather.wet" => BoundValue::flag(self.weather_rain > 0.05),
            "talent.points" => BoundValue::number(self.talent_points as f32),
            "talent.ready" => BoundValue::flag(self.talent_points > 0),
            "xp" => BoundValue::fraction(Self::fraction(self.xp, self.xp_next)),
            "xp.text" => BoundValue::text(format!("{}/{}", self.xp.round(), self.xp_next.round())),
            "level" => BoundValue::number(self.level as f32),
            "level.text" => BoundValue::text(self.level.to_string()),
            "gold" => BoundValue::number(self.gold as f32),
            "wood" => BoundValue::number(self.wood as f32),
            "stone" => BoundValue::number(self.stone as f32),
            "cd.dash" => BoundValue::fraction(self.cd_dash),
            "cd.heal" => BoundValue::fraction(self.cd_heal),
            "cd.strike" => BoundValue::fraction(self.cd_strike),
            // Prontidão — o oposto da fração de cooldown, para o class-bind
            // `cd.dash.ready:ready` acender o slot sem loop de Luau.
            "cd.dash.ready" => BoundValue::flag(self.cd_dash <= 0.0),
            "cd.heal.ready" => BoundValue::flag(self.cd_heal <= 0.0),
            "cd.strike.ready" => BoundValue::flag(self.cd_strike <= 0.0),
            "target" => {
                BoundValue::fraction(Self::fraction(self.target_health, self.target_health_max))
            }
            "target.name" => BoundValue::text(self.target_name.clone()),
            "target.alive" => BoundValue::flag(!self.target_name.is_empty()),
            "clock" => BoundValue::text(self.clock.clone()),
            "day" => BoundValue::fraction(self.day_fraction),
            "prompt.key" => BoundValue::text(self.prompt_key.clone()),
            "prompt.label" => BoundValue::text(self.prompt_label.clone()),
            "prompt.active" => BoundValue::flag(!self.prompt_label.is_empty()),
            "quest.title" => BoundValue::text(self.quest_title.clone()),
            "quest" => BoundValue::fraction(self.quest_progress),
            "quest.text" => BoundValue::text(self.quest_progress_text.clone()),
            "quest.active" => BoundValue::flag(!self.quest_title.is_empty()),
            "potion" => BoundValue::number(self.potions as f32),
            "antidote" => BoundValue::number(self.antidotes as f32),
            "bomb" => BoundValue::number(self.bombs as f32),
            // Esgotado — acende a classe `empty` (slot cinzento) por class-bind.
            "potion.empty" => BoundValue::flag(self.potions < 1),
            "antidote.empty" => BoundValue::flag(self.antidotes < 1),
            "bomb.empty" => BoundValue::flag(self.bombs < 1),
            "toast" => BoundValue::text(self.toast.clone()),
            "toast.active" => BoundValue::flag(self.toast_time > 0.0 && !self.toast.is_empty()),
            // ── revelações contextuais ─────────────────────────────────
            "purse.recent" => BoundValue::flag(self.purse_reveal > 0.0),
            "belt.recent" => BoundValue::flag(self.belt_reveal > 0.0),
            "xp.recent" => BoundValue::flag(self.xp_reveal > 0.0),
            "quest.recent" => {
                BoundValue::flag(self.quest_reveal > 0.0 && !self.quest_title.is_empty())
            }
            "combat.active" => BoundValue::flag(self.combat_reveal > 0.0),
            // Os corações são CONTEXTUAIS (a lição do crítico, r5): vivem no
            // ecrã quando informam — vida por baixo do máximo ou combate
            // recente — e derretem devagar com HP cheio e calma. O gesto
            // BOTW que o A/B premiou quatro rondas seguidas.
            "vitals.active" => {
                BoundValue::flag(self.combat_reveal > 0.0 || self.health < self.health_max - 1e-3)
            }
            // A barra de acções aparece quando serve para alguma coisa: em
            // combate, ou enquanto uma habilidade ainda recarrega.
            "abilities.active" => BoundValue::flag(
                self.combat_reveal > 0.0
                    || self.cd_dash > 0.0
                    || self.cd_heal > 0.0
                    || self.cd_strike > 0.0,
            ),
            "zone.name" => BoundValue::text(self.zone_name.clone()),
            "zone.active" => BoundValue::flag(self.zone_reveal > 0.0 && !self.zone_name.is_empty()),
            "combo" => BoundValue::flag(self.combo_hits >= 2),
            "combo.text" => BoundValue::text(if self.combo_hits >= 2 {
                format!("COMBO \u{d7}{}", self.combo_hits)
            } else {
                String::new()
            }),
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

    fn number(value: f32) -> Self {
        Self {
            fraction: value,
            text: format!("{value:.0}"),
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
/// depois. `None` = desconhecido nos dois.
fn resolve_binding(
    data: &UiData,
    script_binds: Option<&UiScriptBinds>,
    name: &str,
) -> Option<BoundValue> {
    data.get(name).or_else(|| {
        script_binds.and_then(|binds| binds.get(name)).map(|value| BoundValue {
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
    mut binds: Query<(Entity, &UiBind, &mut UiClasses)>,
) {
    let script_binds = script_binds.as_deref();
    for (entity, bind, mut classes) in &mut binds {
        let Some((name, class)) = split_class_bind(&bind.0) else {
            continue;
        };
        let Some(value) = resolve_binding(&data, script_binds, name) else {
            if warned.0.insert(bind.0.clone()) {
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
#[allow(clippy::type_complexity)]
pub fn apply_ui_bindings(
    data: Res<UiData>,
    script_binds: Option<Res<UiScriptBinds>>,
    mut warned: ResMut<UiBindWarnings>,
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
    let mut resolve = |name: &str| -> Option<BoundValue> {
        // Class binds (`nome:classe`) são do `apply_bind_classes`, não de
        // valor — nunca chegam a "unknown binding".
        if split_class_bind(name).is_some() {
            return None;
        }
        match resolve_binding(&data, script_binds, name) {
            Some(value) => Some(value),
            None => {
                if warned.0.insert(name.to_string()) {
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
        if let Some(value) = resolve(&bind.0) {
            if (cooldown.value - value.fraction).abs() > 1e-4 {
                cooldown.value = value.fraction;
            }
        }
    }
    for (bind, mut text) in &mut texts {
        if let Some(value) = resolve(&bind.0) {
            if text.0 != value.text {
                text.0 = value.text;
            }
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
    fn test_fraction_bindings_clamp_and_survive_a_zero_maximum() {
        let mut data = sample();
        assert!((data.get("health").unwrap().fraction - 0.45).abs() < 1e-6);
        data.health_max = 0.0;
        assert_eq!(data.get("health").unwrap().fraction, 0.0);
        data.health_max = 100.0;
        data.health = 500.0;
        assert_eq!(data.get("health").unwrap().fraction, 1.0);
        data.health = -20.0;
        assert_eq!(data.get("health").unwrap().fraction, 0.0);
    }

    #[test]
    fn test_readiness_empty_and_weather_bindings() {
        let mut data = sample();
        // cd_dash = 0.5 no sample → ainda a arrefecer; heal/strike prontos.
        assert!(!data.get("cd.dash.ready").unwrap().truthy);
        assert!(data.get("cd.heal.ready").unwrap().truthy);
        assert!(data.get("cd.strike.ready").unwrap().truthy);
        data.cd_dash = 0.0;
        assert!(data.get("cd.dash.ready").unwrap().truthy);

        // Consumíveis: inventário vazio por omissão → empty; com stock, não.
        assert!(data.get("potion.empty").unwrap().truthy);
        assert!(data.get("bomb.empty").unwrap().truthy);
        data.potions = 2;
        assert!(!data.get("potion.empty").unwrap().truthy);
        assert_eq!(data.get("potion").unwrap().fraction, 2.0);

        // Weather: seco = wet falso; acima do limiar liga; fração satura.
        assert!(!data.get("weather.wet").unwrap().truthy);
        assert_eq!(data.get("weather.rain").unwrap().fraction, 0.0);
        data.weather_rain = 0.4;
        assert!(data.get("weather.wet").unwrap().truthy);
        assert!((data.get("weather.rain").unwrap().fraction - 0.4).abs() < 1e-6);
        data.weather_rain = 2.5;
        assert_eq!(data.get("weather.rain").unwrap().fraction, 1.0);

        // Talento: badge só com pontos pendentes.
        assert!(!data.get("talent.ready").unwrap().truthy);
        data.talent_points = 1;
        assert!(data.get("talent.ready").unwrap().truthy);
        assert_eq!(data.get("talent.points").unwrap().fraction, 1.0);
    }

    #[test]
    fn test_text_bindings_format_for_a_hud() {
        let data = sample();
        assert_eq!(data.get("health.text").unwrap().text, "45/100");
        assert_eq!(data.get("level.text").unwrap().text, "3");
        assert_eq!(data.get("gold").unwrap().text, "12");
        assert_eq!(data.get("clock").unwrap().text, "07:15");
        // A negative health still reads as 0, never "-20/100".
        let hurt = UiData {
            health: -5.0,
            ..sample()
        };
        assert_eq!(hurt.get("health.text").unwrap().text, "0/100");
    }

    #[test]
    fn test_flag_bindings_gate_visibility() {
        let data = sample();
        assert!(data.get("target.alive").unwrap().truthy);
        assert!(!data.get("prompt.active").unwrap().truthy);
        // `health.low` is the hook a stylesheet uses to turn the orb red.
        assert!(!data.get("health.low").unwrap().truthy);
        let dying = UiData {
            health: 12.0,
            ..sample()
        };
        assert!(dying.get("health.low").unwrap().truthy);
    }

    #[test]
    fn test_health_low_threshold_is_exactly_thirty_percent() {
        // No limiar exacto (30/100): já é "vida baixa" — o jogador não pode
        // perder o alerta por um ponto flutuante.
        let edge = UiData {
            health: 30.0,
            health_max: 100.0,
            ..Default::default()
        };
        assert!(edge.get("health.low").unwrap().truthy);
        // Um ponto acima, ainda não.
        let above = UiData {
            health: 31.0,
            health_max: 100.0,
            ..Default::default()
        };
        assert!(!above.get("health.low").unwrap().truthy);
        // E sem vida máxima (entidade a nascer) o fraction é 0 — o alerta
        // acende, mas o collect só publica o estado quando existe `Health`.
        let uninit = UiData {
            health: 0.0,
            health_max: 0.0,
            ..Default::default()
        };
        assert!(uninit.get("health.low").unwrap().truthy);
    }

    #[test]
    fn test_status_venom_mirrors_the_flag() {
        assert!(!sample().get("status.venom").unwrap().truthy);
        let poisoned = UiData {
            status_venom: true,
            ..sample()
        };
        assert!(poisoned.get("status.venom").unwrap().truthy);
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
                UiBind("health.low:danger".into()),
                UiClasses::parse("hud-card vitals"),
                Visibility::Inherited,
            ))
            .id();
        // Vida alta: sem classe, e o bind de classe NÃO é reportado como
        // desconhecido nem esconde o elemento.
        app.world_mut().resource_mut::<UiData>().health = 80.0;
        app.world_mut().resource_mut::<UiData>().health_max = 100.0;
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

        // Vida baixa: a classe entra (idempotente no segundo frame).
        app.world_mut().resource_mut::<UiData>().health = 12.0;
        app.update();
        app.update();
        assert!(app.world().get::<UiClasses>(card).unwrap().has("danger"));

        // Recuperou: a classe sai.
        app.world_mut().resource_mut::<UiData>().health = 90.0;
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
            "health",
            "health.text",
            "health.value",
            "health.low",
            "status.venom",
            "xp",
            "xp.text",
            "level",
            "level.text",
            "gold",
            "wood",
            "stone",
            "cd.dash",
            "cd.heal",
            "cd.strike",
            "target",
            "target.name",
            "target.alive",
            "clock",
            "day",
            "prompt.key",
            "prompt.label",
            "prompt.active",
            "quest.title",
            "quest",
            "quest.text",
            "quest.active",
            "potion",
            "antidote",
            "bomb",
            "toast",
            "toast.active",
            "combo",
            "combo.text",
            "purse.recent",
            "belt.recent",
            "xp.recent",
            "quest.recent",
            "combat.active",
            "abilities.active",
            "vitals.active",
            "zone.name",
            "zone.active",
            "weather.rain",
            "weather.wet",
            "talent.points",
            "talent.ready",
            "cd.dash.ready",
            "cd.heal.ready",
            "cd.strike.ready",
            "potion.empty",
            "antidote.empty",
            "bomb.empty",
        ] {
            assert!(data.get(name).is_some(), "binding `{name}` disappeared");
        }
    }
}
