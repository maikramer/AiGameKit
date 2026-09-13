//! Fills [`UiData`] once per frame from the game's own components and
//! resources.
//!
//! This is the single place the declarative UI touches gameplay state. Keeping
//! it in one system means a HUD element never queries the world itself: worlds
//! and scripts only ever see the named bindings, which is what makes the
//! interface re-authorable without recompiling the engine.

use bevy::prelude::*;

use super::bind::UiData;
use super::runtime::{UiBind, UiTag};
use crate::vitals::{Health, Xp};

/// Newest toast plus its remaining lifetime, so `toast.active` can fade.
#[derive(Debug, Default, Resource)]
pub struct UiToast {
    pub text: String,
    pub time: f32,
}

/// Seconds a toast stays bound before `toast.active` goes false.
pub const TOAST_LIFETIME: f32 = 3.5;

/// Formats an in-world minute-of-day as `HH:MM`.
pub fn clock_text(minute_of_day: f32) -> String {
    let minutes = minute_of_day.rem_euclid(1440.0) as u32;
    format!("{:02}:{:02}", minutes / 60, minutes % 60)
}

/// Keeps the toast resource fed from the script/gameplay toast channel.
///
/// Avisos de zona ("Entraste em: …", "De volta ao vale.") NÃO chegam aqui: o
/// deles é o cartão de descoberta (`zone.name`/`zone.active`), que tem ritmo,
/// tamanho e SFX próprios — o mesmo texto em duas camadas era o ruído que
/// entulhava o HUD antigo. Filtrado do lado display, conforme o contrato.
pub fn collect_ui_toasts(
    mut toast: ResMut<UiToast>,
    mut incoming: bevy::ecs::message::MessageReader<crate::luau::ScriptToast>,
    time: Res<Time>,
) {
    for message in incoming.read() {
        if crate::menus::is_zone_notice(&message.0) {
            continue;
        }
        toast.text = message.0.clone();
        toast.time = TOAST_LIFETIME;
    }
    if toast.time > 0.0 {
        toast.time = (toast.time - time.delta_secs()).max(0.0);
    }
}

/// Retira a camada legacy de toasts em Rust (`menus::spawn_toast_container`,
/// nome `"ui:toasts"`) — pilha de até 3 pílulas que corria EM PARALELO com o
/// toast declarativo deste módulo. Só retira quando existe um texto
/// declarativo com `bind="toast"`: mundos sem consumidor mantêm o fallback.
///
/// Corre em `Update` (a ordem dentro de `Startup` não é garantida entre
/// plugins) e desiste no primeiro sucesso. Sem consumidor ainda tenta nos
/// frames seguintes, para permitir que a árvore declarativa nasça mais tarde.
/// Se `menus.rs` renomear o contentor, o fallback é ver duas camadas — nunca zero.
#[allow(clippy::type_complexity)]
pub fn retire_legacy_toast_layer(
    mut commands: Commands,
    mut done: Local<bool>,
    names: Query<(Entity, &Name)>,
    consumers: Query<&UiBind, (With<Text>, With<UiTag>)>,
) {
    if *done || !consumers.iter().any(|bind| bind.0 == "toast") {
        return;
    }
    for (entity, name) in &names {
        if name.as_str() == "ui:toasts" {
            commands.entity(entity).despawn();
            *done = true;
            debug!("ui: camada legacy de toasts retirada — o toast é o declarativo");
            return;
        }
    }
}

/// Publishes the nearest interactable into [`UiPrompt`], which is what the
/// HUD's key-cap prompt binds to.
///
/// The label is the verb, not a sentence: the prompt is two glyphs and a word
/// floating over the world, and anything longer reads as a tooltip.
pub fn collect_ui_prompt(
    mut prompt: ResMut<UiPrompt>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    targets: Query<
        (&GlobalTransform, &crate::luau::ScriptInteraction),
        Without<crate::player::Player>,
    >,
    npcs: Query<
        (&GlobalTransform, &crate::recipes::spawn::DialogueNpc),
        Without<crate::player::Player>,
    >,
) {
    let Ok(player) = players.single() else {
        prompt.label.clear();
        return;
    };
    let origin = player.translation();
    // Nearest wins, across both kinds of interactable — standing between a
    // merchant and a berry bush should offer the closer one, not the first the
    // query happens to yield.
    let mut best: Option<(f32, String, String)> = None;
    let mut consider = |distance: f32, key: String, label: String| {
        if best.as_ref().is_none_or(|(d, _, _)| distance < *d) {
            best = Some((distance, key, label));
        }
    };
    for (transform, interaction) in &targets {
        let distance = transform.translation().distance(origin);
        if distance <= interaction.range {
            // The script authored the verb; fall back to a generic one only
            // when it left the label empty.
            let label = if interaction.label.trim().is_empty() {
                "usar".to_string()
            } else {
                interaction.label.clone()
            };
            consider(distance, key_label(interaction.key), label);
        }
    }
    for (transform, _) in &npcs {
        let distance = transform.translation().distance(origin);
        if distance <= crate::interact::default_range() {
            consider(distance, "E".to_string(), "falar".to_string());
        }
    }
    match best {
        Some((_, key, label)) => {
            prompt.key = key;
            prompt.label = label;
        }
        None => {
            prompt.key.clear();
            prompt.label.clear();
        }
    }
}

/// Range at which a `<DialogueNPC>` offers its prompt (m) — valor AUTORADO;
/// o efetivo é `interact::default_range()` (metade, ver `src/interact.rs`).
pub const DIALOGUE_RANGE_M: f32 = crate::interact::BASE_RANGE_M;

/// Single-glyph name for the keys an interaction can be bound to.
///
/// Cobre todas as teclas aceites por `key_code_from_str` (`src/luau.rs`:
/// `"e" "j" "f" "q" "r" "space"`) + K (loja da engine).
pub fn key_label(key: bevy::input::keyboard::KeyCode) -> String {
    use bevy::input::keyboard::KeyCode;
    match key {
        KeyCode::KeyE => "E",
        KeyCode::KeyF => "F",
        KeyCode::KeyJ => "J",
        KeyCode::KeyK => "K",
        KeyCode::KeyQ => "Q",
        KeyCode::KeyR => "R",
        KeyCode::Space => "␣",
        _ => "•",
    }
    .to_string()
}

/// Gathers everything the bindings can read.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn collect_ui_data(
    mut data: ResMut<UiData>,
    hero: Query<
        (
            Option<&Health>,
            Option<&Xp>,
            Option<&crate::feedback::StatusEffects>,
        ),
        With<crate::player::Player>,
    >,
    levels: Query<&crate::skills::LevelState, With<crate::player::Player>>,
    vault: Option<Res<crate::economy::Vault>>,
    cooldowns: Option<Res<crate::skills::AbilityCooldowns>>,
    target: Option<Res<crate::feedback::CombatTarget>>,
    targets: Query<(&Health, Option<&Name>), Without<crate::player::Player>>,
    day: Option<Res<crate::worldsys::DayCycleState>>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    quests: Option<Res<crate::quests::QuestLog>>,
    prompt: Option<Res<UiPrompt>>,
    toast: Res<UiToast>,
    combo: Option<Res<crate::skills::ComboState>>,
) {
    if let Ok((health, xp, effects)) = hero.single() {
        if let Some(health) = health {
            data.health = health.current;
            data.health_max = health.max;
        }
        if let Some(xp) = xp {
            data.xp = xp.current as f32;
            data.xp_next = xp.next as f32;
        }
        // Veneno activo (`status.venom`): leitura do estado que o feedback
        // mantém no herói — o collect nunca o muta, só o publica.
        data.status_venom = effects.is_some_and(|effects| effects.venom > 0.0);
    }
    data.level = levels.single().map(|l| l.level).unwrap_or(1).max(1);
    // Pontos de talento por gastar (`talent.ready` acende o badge do nível).
    data.talent_points = levels.single().map(|l| l.points).unwrap_or(0);
    // Chuva contínua do `<Weather>` (`weather.rain`/`weather.wet` no card de
    // ambiente) — leitura pura; o scheduler do clima é que a escreve.
    data.weather_rain = weather.as_deref().map(|w| w.rain).unwrap_or(0.0);
    if let Some(vault) = vault.as_deref() {
        data.gold = vault.gold;
        data.wood = vault.wood;
        data.stone = vault.stone;
        let stack = |item: &str| vault.items.get(item).copied().unwrap_or(0);
        data.potions = stack("potion");
        data.antidotes = stack("antidote");
        data.bombs = stack("bomb");
    }
    // Combo counter (hits na janela) para o binding `combo.text`.
    if let Some(combo) = combo.as_deref() {
        data.combo_hits = combo.hits;
    }
    if let Some(cd) = cooldowns.as_deref() {
        // Bindings carry the *remaining fraction*, which is what a radial veil
        // wants — the raw seconds mean nothing without the ability's period.
        data.cd_dash = (cd.dash / crate::skills::DASH_COOLDOWN).clamp(0.0, 1.0);
        data.cd_heal = (cd.heal / crate::skills::HEAL_ABILITY_COOLDOWN).clamp(0.0, 1.0);
        data.cd_strike = (cd.strike / crate::skills::STRIKE_COOLDOWN).clamp(0.0, 1.0);
    }
    // Soft-locked target: name + health, cleared as soon as the lock expires.
    data.target_name.clear();
    data.target_health = 0.0;
    data.target_health_max = 0.0;
    if let Some(target) = target.as_deref() {
        if target.timer > 0.0 {
            if let Some(entity) = target.entity {
                if let Ok((health, name)) = targets.get(entity) {
                    data.target_name = name
                        .map(|n| n.as_str().to_string())
                        .unwrap_or_else(|| "Inimigo".to_string());
                    data.target_health = health.current;
                    data.target_health_max = health.max;
                }
            }
        }
    }
    if let Some(day) = day.as_deref() {
        let text = clock_text(day.minute_of_day);
        if data.clock != text {
            data.clock = text;
        }
        let fraction = day.minute_of_day.rem_euclid(1440.0) / 1440.0;
        if data.day_fraction != fraction {
            data.day_fraction = fraction;
        }
    }
    // Padrão "só escrevo se mudou": reescrever as Strings por frame realocava
    // à toa e mantinha `UiData` em `Changed` sempre. O progresso só é
    // recalculado quando o texto muda — é derivado dele e muda a passos
    // discretos.
    let active = quests.as_deref().and_then(|quests| {
        let vault_ref = vault.as_deref();
        let active_ids = quests.active_ids(vault_ref);
        let id = active_ids.first()?.as_str();
        let title = quests.def(id).map(|def| def.title.clone());
        Some((title, quests.progress_text(id, vault_ref)))
    });
    match active {
        Some((title, progress_text)) => {
            let title = title.unwrap_or_default();
            if data.quest_title != title {
                data.quest_title = title;
            }
            if data.quest_progress_text != progress_text {
                data.quest_progress_text = progress_text;
                data.quest_progress = progress_fraction(&data.quest_progress_text);
            }
        }
        None => {
            if !data.quest_title.is_empty() {
                data.quest_title.clear();
            }
            if !data.quest_progress_text.is_empty() {
                data.quest_progress_text.clear();
                data.quest_progress = 0.0;
            }
        }
    }
    match prompt.as_deref() {
        Some(prompt) => {
            if data.prompt_key != prompt.key {
                data.prompt_key = prompt.key.clone();
            }
            if data.prompt_label != prompt.label {
                data.prompt_label = prompt.label.clone();
            }
        }
        None => {
            if !data.prompt_key.is_empty() {
                data.prompt_key.clear();
            }
            if !data.prompt_label.is_empty() {
                data.prompt_label.clear();
            }
        }
    }
    if data.toast != toast.text {
        data.toast = toast.text.clone();
    }
    if data.toast_time != toast.time {
        data.toast_time = toast.time;
    }
}

// ── revelações contextuais ──────────────────────────────────────────────

/// Quanto tempo um widget contextual fica no ecrã depois da mudança que o
/// justificou (s).
pub const REVEAL_SECS: f32 = 3.2;
/// A missão fica um pouco mais — é a única linha de texto longa do HUD e o
/// jogador tem de a conseguir ler.
pub const QUEST_REVEAL_SECS: f32 = 6.0;
/// Depois do último sinal de combate, a barra de acções ainda espera isto
/// antes de sair (evita piscar entre dois golpes).
pub const COMBAT_REVEAL_SECS: f32 = 5.0;
/// Vida do cartão de descoberta de zona.
pub const ZONE_REVEAL_SECS: f32 = 4.5;

/// Último valor visto de cada coisa que dispara uma revelação.
#[derive(Debug, Default)]
pub struct RevealMemory {
    seeded: bool,
    purse: (u32, u32, u32),
    belt: (u32, u32, u32),
    xp: (f32, u32),
    quest: String,
    progress: String,
    health: f32,
    zone: Option<String>,
}

/// Nome de exposição de uma região de bioma.
///
/// Um cartão de descoberta que diz `dark-forest` é uma etiqueta de debug; o
/// que dá peso ao sítio é o nome que ele tem no mundo. A precedência é:
/// **`display-name` autoral** na região activa (XML) → tabela de fallback da
/// engine → título derivado do próprio id, para uma zona nova não precisar
/// nem de Rust nem de editar o mundo existente.
pub fn zone_display_name(display_name: Option<&str>, id: Option<&str>) -> String {
    if let Some(name) = display_name.map(str::trim).filter(|name| !name.is_empty()) {
        return name.to_string();
    }
    match id {
        None => "O Vale".to_string(),
        Some("dark-forest") => "Floresta Sombria".to_string(),
        Some("desert") => "Ermo Rubro".to_string(),
        Some("swamp") => "Pântano da Bruma".to_string(),
        Some("frozen-peaks") => "Picos Gelados".to_string(),
        Some(other) => title_case(other),
    }
}

/// `dark-forest` → `Dark Forest`.
fn title_case(id: &str) -> String {
    id.split(['-', '_'])
        .filter(|word| !word.is_empty())
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Abre e fecha as janelas de revelação de [`UiData`].
///
/// Corre depois de `collect_ui_data`: compara o que mudou face ao frame
/// anterior, reabre a janela do widget correspondente e desconta o tempo das
/// outras. O primeiro frame é só semeadura — sem isto, o HUD abria tudo de uma
/// vez no arranque, que é exactamente o que estamos a tentar não ter.
pub fn collect_ui_reveals(
    mut data: ResMut<UiData>,
    mut memory: Local<RevealMemory>,
    biome: Option<Res<crate::ambient::CurrentBiome>>,
    biomes: Option<Res<crate::worldsys::BiomeRegions>>,
    time: Res<Time>,
) {
    let dt = time.delta_secs();
    let purse = (data.gold, data.wood, data.stone);
    let belt = (data.potions, data.antidotes, data.bombs);
    let xp = (data.xp, data.level);
    let zone = biome.as_deref().and_then(|b| b.id.clone());
    // O `display-name` da região activa vem do XML (Tinta Quente: o mundo é
    // quem nomeia os sítios); sem attr, `zone_display_name` cai na tabela.
    let authored = zone.as_deref().and_then(|id| {
        biomes
            .as_deref()?
            .list
            .iter()
            .find(|r| r.id == id)
            .and_then(|r| {
                let name = r.display_name.trim();
                (!name.is_empty()).then(|| name.to_string())
            })
    });

    if !memory.seeded {
        memory.seeded = true;
        memory.purse = purse;
        memory.belt = belt;
        memory.xp = xp;
        memory.quest = data.quest_title.clone();
        memory.progress = data.quest_progress_text.clone();
        memory.health = data.health;
        memory.zone = zone.clone();
        // A zona inicial não é uma descoberta — o herói já lá está.
        data.zone_name = zone_display_name(authored.as_deref(), zone.as_deref());
        return;
    }

    let open = |window: &mut f32, seconds: f32| {
        if *window < seconds {
            *window = seconds;
        }
    };

    if purse != memory.purse {
        memory.purse = purse;
        open(&mut data.purse_reveal, REVEAL_SECS);
    }
    if belt != memory.belt {
        memory.belt = belt;
        open(&mut data.belt_reveal, REVEAL_SECS);
    }
    if xp != memory.xp {
        memory.xp = xp;
        open(&mut data.xp_reveal, REVEAL_SECS);
    }
    if data.quest_title != memory.quest {
        memory.quest = data.quest_title.clone();
        open(&mut data.quest_reveal, QUEST_REVEAL_SECS);
    }
    // Progresso do objectivo ("1/5" → "2/5") também reabre o tracker: é o
    // feedback da colheita/kill. Sem isto, só uma MISSÃO nova aparecia e o
    // "2/5" atualizava-se às escuras.
    if !data.quest_progress_text.is_empty() && data.quest_progress_text != memory.progress {
        memory.progress = data.quest_progress_text.clone();
        open(&mut data.quest_reveal, QUEST_REVEAL_SECS);
    } else if data.quest_progress_text != memory.progress {
        memory.progress = data.quest_progress_text.clone();
    }
    // Combate: um alvo travado ou uma perda de vida mantêm a janela aberta.
    let hurt = data.health < memory.health - 0.01;
    memory.health = data.health;
    if hurt || !data.target_name.is_empty() {
        open(&mut data.combat_reveal, COMBAT_REVEAL_SECS);
    }
    // O cinto acompanha o combate: é onde a poção está.
    if data.combat_reveal > 0.0 {
        open(&mut data.belt_reveal, 0.6);
    }
    if zone != memory.zone {
        memory.zone = zone.clone();
        data.zone_name = zone_display_name(authored.as_deref(), zone.as_deref());
        data.zone_reveal = ZONE_REVEAL_SECS;
    }

    tick(&mut data.purse_reveal, dt);
    tick(&mut data.belt_reveal, dt);
    tick(&mut data.xp_reveal, dt);
    tick(&mut data.quest_reveal, dt);
    tick(&mut data.combat_reveal, dt);
    tick(&mut data.zone_reveal, dt);
}

/// Desconta um frame de uma janela de revelação (nunca abaixo de zero).
fn tick(window: &mut f32, dt: f32) {
    if *window > 0.0 {
        *window = (*window - dt).max(0.0);
    }
}

/// Nearest interactable, published by the interaction system.
#[derive(Debug, Default, Resource)]
pub struct UiPrompt {
    pub key: String,
    pub label: String,
}

/// Reads `"2/5"` as `0.4`; anything else is 0.
pub fn progress_fraction(text: &str) -> f32 {
    let Some((done, total)) = text.split_once('/') else {
        return 0.0;
    };
    let done: f32 = done.trim().parse().unwrap_or(0.0);
    let total: f32 = total.trim().parse().unwrap_or(0.0);
    if total <= 0.0 {
        return 0.0;
    }
    (done / total).clamp(0.0, 1.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::input::keyboard::KeyCode;

    #[test]
    fn test_dialogue_prompt_uses_e_and_preserves_script_keys() {
        let mut app = App::new();
        app.init_resource::<UiPrompt>()
            .add_systems(Update, collect_ui_prompt);
        app.world_mut()
            .spawn((crate::player::Player::default(), GlobalTransform::default()));
        let npc = app
            .world_mut()
            .spawn((
                crate::recipes::spawn::DialogueNpc {
                    dialogue_id: "test".into(),
                },
                // Dentro do alcance EFETIVO (metade do autorado: 1,75 m).
                GlobalTransform::from_translation(Vec3::new(1.2, 0.0, 0.0)),
            ))
            .id();
        app.update();
        let prompt = app.world().resource::<UiPrompt>();
        assert_eq!(prompt.key, "E");
        assert_eq!(prompt.label, "falar");

        let scripted = app
            .world_mut()
            .spawn((
                crate::luau::ScriptInteraction {
                    label: "assinar".into(),
                    key: KeyCode::KeyF,
                    // `range` aqui é o EFETIVO (o `set_interaction` já
                    // escalou; o teste insere o componente à mão).
                    range: 1.5,
                },
                GlobalTransform::from_translation(Vec3::new(0.6, 0.0, 0.0)),
            ))
            .id();
        app.update();
        let prompt = app.world().resource::<UiPrompt>();
        assert_eq!(prompt.key, "F", "closer script keeps its authored key");
        assert_eq!(prompt.label, "assinar");

        app.world_mut().despawn(scripted);
        app.world_mut()
            .entity_mut(npc)
            .insert(GlobalTransform::from_translation(Vec3::new(
                DIALOGUE_RANGE_M + 1.0,
                0.0,
                0.0,
            )));
        app.update();
        let prompt = app.world().resource::<UiPrompt>();
        assert!(prompt.key.is_empty());
        assert!(prompt.label.is_empty());
    }

    fn toast_app() -> (App, Entity) {
        let mut app = App::new();
        app.add_systems(Update, retire_legacy_toast_layer);
        let legacy = app.world_mut().spawn(Name::new("ui:toasts")).id();
        (app, legacy)
    }

    #[test]
    fn test_legacy_toasts_stay_without_a_declarative_consumer() {
        let (mut app, legacy) = toast_app();
        app.update();
        app.update();
        assert!(app.world().get_entity(legacy).is_ok());
    }

    #[test]
    fn test_legacy_toasts_need_toast_text_on_the_same_declarative_element() {
        let (mut app, legacy) = toast_app();
        // A panel binding plus unrelated text is not a toast text consumer.
        app.world_mut()
            .spawn((UiTag("uipanel".into()), UiBind("toast".into())));
        app.world_mut()
            .spawn((UiTag("uitext".into()), Text::new("unrelated")));
        // Neither a truthiness binding nor a non-declarative text qualifies.
        app.world_mut().spawn((
            UiTag("uitext".into()),
            Text::default(),
            UiBind("toast.active".into()),
        ));
        app.world_mut()
            .spawn((Text::default(), UiBind("toast".into())));
        app.update();
        assert!(app.world().get_entity(legacy).is_ok());
    }

    #[test]
    fn test_legacy_toasts_retire_when_declarative_text_is_present() {
        let (mut app, legacy) = toast_app();
        let child = app
            .world_mut()
            .spawn((Text::new("old toast"), ChildOf(legacy)))
            .id();
        let consumer = app
            .world_mut()
            .spawn((
                super::super::runtime::UiId("notification-text".into()),
                UiTag("uitext".into()),
                Text::default(),
                UiBind("toast".into()),
            ))
            .id();
        let other = app.world_mut().spawn(Name::new("ui:other")).id();
        app.update();
        assert!(app.world().get_entity(legacy).is_err());
        assert!(app.world().get_entity(child).is_err());
        assert!(app.world().get_entity(consumer).is_ok());
        assert!(app.world().get_entity(other).is_ok());
    }

    #[test]
    fn test_legacy_toasts_retry_when_consumer_arrives_later() {
        let (mut app, legacy) = toast_app();
        app.update();
        assert!(app.world().get_entity(legacy).is_ok());
        app.world_mut().spawn((
            UiTag("uitext".into()),
            Text::default(),
            UiBind("toast".into()),
        ));
        app.update();
        assert!(app.world().get_entity(legacy).is_err());
    }

    #[test]
    fn test_key_label_is_one_glyph_and_never_empty() {
        assert_eq!(key_label(KeyCode::KeyE), "E");
        assert_eq!(key_label(KeyCode::KeyQ), "Q");
        assert_eq!(key_label(KeyCode::Space), "␣");
        // An unmapped key still prints something the cap can hold.
        let fallback = key_label(KeyCode::F7);
        assert_eq!(fallback.chars().count(), 1);
        assert!(!fallback.is_empty());
    }

    #[test]
    fn test_clock_text_wraps_the_day() {
        assert_eq!(clock_text(0.0), "00:00");
        assert_eq!(clock_text(435.0), "07:15");
        assert_eq!(clock_text(1439.0), "23:59");
        // Past midnight and before it both wrap into the day.
        assert_eq!(clock_text(1440.0), "00:00");
        assert_eq!(clock_text(-60.0), "23:00");
    }

    #[test]
    fn test_zone_display_name_is_a_place_not_an_id() {
        assert_eq!(zone_display_name(None, None), "O Vale");
        assert_eq!(
            zone_display_name(None, Some("dark-forest")),
            "Floresta Sombria"
        );
        assert_eq!(
            zone_display_name(None, Some("frozen-peaks")),
            "Picos Gelados"
        );
        // Uma região nova no XML já sai legível sem tocar em Rust.
        assert_eq!(zone_display_name(None, Some("sunken-city")), "Sunken City");
        assert_eq!(zone_display_name(None, Some("")), "");
    }

    #[test]
    fn test_zone_display_name_prefers_the_authored_name() {
        // display-name autoral vence a tabela…
        assert_eq!(
            zone_display_name(Some("Vale das Cinzas"), Some("dark-forest")),
            "Vale das Cinzas"
        );
        // …e a tabela vence o id derivado.
        assert_eq!(
            zone_display_name(None, Some("dark-forest")),
            "Floresta Sombria"
        );
        assert_eq!(zone_display_name(None, Some("banana-tree")), "Banana Tree");
        // Um attr em branco não conta como nome — cai ao nível abaixo.
        assert_eq!(zone_display_name(Some("   "), Some("desert")), "Ermo Rubro");
        // Com id desconhecido E sem attr, o id deriva o título.
        assert_eq!(zone_display_name(Some("Nome"), None), "Nome");
    }

    #[test]
    fn test_collect_publishes_the_venom_status_of_the_hero() {
        use crate::feedback::StatusEffects;

        let mut app = App::new();
        app.init_resource::<UiData>()
            .init_resource::<UiToast>()
            .add_systems(Update, collect_ui_data);
        let hero = app
            .world_mut()
            .spawn((
                crate::player::Player::default(),
                Health {
                    current: 100.0,
                    max: 100.0,
                },
                Xp {
                    current: 0,
                    next: 100,
                },
            ))
            .id();
        app.update();
        assert!(!app.world().resource::<UiData>().status_venom);

        // Envenenado: o bind acende sem o collect tocar no estado.
        app.world_mut().entity_mut(hero).insert(StatusEffects {
            venom: 2.5,
            venom_tick: 0.0,
        });
        app.update();
        assert!(app.world().resource::<UiData>().status_venom);

        // Expirou (venom = 0): apaga.
        app.world_mut().entity_mut(hero).insert(StatusEffects {
            venom: 0.0,
            venom_tick: 0.0,
        });
        app.update();
        assert!(!app.world().resource::<UiData>().status_venom);
    }

    #[test]
    fn test_progress_fraction_reads_the_quest_counter() {
        assert_eq!(progress_fraction("2/5"), 0.4);
        assert_eq!(progress_fraction("5/5"), 1.0);
        // Overshoot is clamped, and a finished-but-uncollected quest is 1.
        assert_eq!(progress_fraction("9/5"), 1.0);
        // Garbage never produces NaN or a negative bar.
        assert_eq!(progress_fraction(""), 0.0);
        assert_eq!(progress_fraction("done"), 0.0);
        assert_eq!(progress_fraction("3/0"), 0.0);
    }
}
