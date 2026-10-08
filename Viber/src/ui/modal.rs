//! Modals, tab groups and scrolling — the three things a game menu needs that
//! a plain element tree does not give for free.
//!
//! All three are declarative:
//!
//! ```xml
//! <UiModal id="menu" key="F1" class="overlay">
//!   <UiPanel class="frame">
//!     <UiRow class="tabbar">
//!       <UiButton class="tab" tab-group="menu" tab="quests"><UiText>Missões</UiText></UiButton>
//!       <UiButton class="tab" tab-group="menu" tab="bag"><UiText>Mochila</UiText></UiButton>
//!     </UiRow>
//!     <UiPanel class="page" tab-group="menu" tab="quests" scroll="y"> … </UiPanel>
//!     <UiPanel class="page" tab-group="menu" tab="bag" scroll="y"> … </UiPanel>
//!   </UiPanel>
//! </UiModal>
//! ```
//!
//! The active tab button gets the `active` class, so the stylesheet decides
//! what "selected" looks like; inactive pages are `display: none`, which keeps
//! them out of layout entirely instead of merely invisible.

use bevy::input::mouse::{MouseScrollUnit, MouseWheel};
use bevy::prelude::*;
// Bevy 0.20: `Interaction`/`Button` do prelude são aliases de tipos privados.
use crate::ui::interaction::Interaction;

use super::runtime::{UiClasses, UiId, UiStyleDirty};

/// A full-screen overlay toggled by a key.
#[derive(Debug, Component)]
pub struct UiModal {
    /// Key that opens and closes it.
    pub key: KeyCode,
    /// Open right now?
    pub open: bool,
    /// Escape also closes it (true unless the author says otherwise).
    pub escape_closes: bool,
}

/// Which modals are open — gameplay systems read this to stop stealing input.
#[derive(Debug, Default, Resource)]
pub struct UiModalsOpen {
    pub open: Vec<String>,
    /// Modais SEM `id`: não entram em `open` (não há nome para listar), mas
    /// roubam input ao gameplay na mesma — contam em [`UiModalsOpen::any`].
    pub anonymous: usize,
}

impl UiModalsOpen {
    pub fn any(&self) -> bool {
        !self.open.is_empty() || self.anonymous > 0
    }

    pub fn is_open(&self, id: &str) -> bool {
        self.open.iter().any(|o| o == id)
    }
}

/// A tab button: selects `tab` inside `group`.
#[derive(Debug, Component)]
pub struct UiTabButton {
    pub group: String,
    pub tab: String,
}

/// Next tab in a group, wrapping.
///
/// `tabs` is the group's buttons in document order; `delta` is +1 / -1.
pub fn cycle(tabs: &[String], current: Option<&str>, delta: i32) -> Option<String> {
    if tabs.is_empty() {
        return None;
    }
    let index = current
        .and_then(|c| tabs.iter().position(|t| t == c))
        .unwrap_or(0) as i32;
    let count = tabs.len() as i32;
    Some(tabs[(index + delta).rem_euclid(count) as usize].clone())
}

/// A tab page: visible only while `tab` is the selection of `group`.
#[derive(Debug, Component)]
pub struct UiTabPage {
    pub group: String,
    pub tab: String,
}

/// Selected tab per group; the first page authored in a group wins by default.
#[derive(Debug, Default, Resource)]
pub struct UiTabs {
    pub active: std::collections::HashMap<String, String>,
}

impl UiTabs {
    pub fn selected(&self, group: &str) -> Option<&str> {
        self.active.get(group).map(String::as_str)
    }

    /// Selects a tab; `true` when it actually changed.
    pub fn select(&mut self, group: &str, tab: &str) -> bool {
        if self.selected(group) == Some(tab) {
            return false;
        }
        self.active.insert(group.to_string(), tab.to_string());
        true
    }
}

/// A scrollable viewport; `speed` is pixels per wheel line.
#[derive(Debug, Component)]
pub struct UiScroll {
    pub vertical: bool,
    pub speed: f32,
}

impl Default for UiScroll {
    fn default() -> Self {
        Self {
            vertical: true,
            speed: 26.0,
        }
    }
}

/// Parses a key name from a `key="…"` attribute.
///
/// Aceita o teclado todo em nomes CSS-ish: letras (`"p"`), dígitos (`"1"`),
/// função (`"f5"`), navegação (`"pageup"`, `"home"`, `"arrowleft"`),
/// pontuação (`"`"`/`"backquote"`, `","`, `"["`…). Um nome desconhecido é
/// reportado e o modal cai em F1 em vez de ficar inabritável.
pub fn parse_key(name: &str) -> KeyCode {
    let key = name.trim().to_ascii_lowercase();
    let key = key.as_str();
    match key {
        "escape" | "esc" => KeyCode::Escape,
        "tab" => KeyCode::Tab,
        "space" => KeyCode::Space,
        "enter" | "return" => KeyCode::Enter,
        "backspace" => KeyCode::Backspace,
        "delete" | "del" => KeyCode::Delete,
        "insert" | "ins" => KeyCode::Insert,
        "home" => KeyCode::Home,
        "end" => KeyCode::End,
        "pageup" | "page-up" => KeyCode::PageUp,
        "pagedown" | "page-down" => KeyCode::PageDown,
        "pause" => KeyCode::Pause,
        "arrowup" | "up" => KeyCode::ArrowUp,
        "arrowdown" | "down" => KeyCode::ArrowDown,
        "arrowleft" | "left" => KeyCode::ArrowLeft,
        "arrowright" | "right" => KeyCode::ArrowRight,
        "backquote" | "grave" | "`" => KeyCode::Backquote,
        "minus" | "-" => KeyCode::Minus,
        "equal" | "=" => KeyCode::Equal,
        "comma" | "," => KeyCode::Comma,
        "period" | "." => KeyCode::Period,
        "slash" | "/" => KeyCode::Slash,
        "semicolon" | ";" => KeyCode::Semicolon,
        "quote" | "'" => KeyCode::Quote,
        "bracketleft" | "[" => KeyCode::BracketLeft,
        "bracketright" | "]" => KeyCode::BracketRight,
        "backslash" | "\\" => KeyCode::Backslash,
        _ => {
            // Letras: "p" (ou "KeyP"/"P" — o trim+lowercase já tratou).
            if let Some(rest) = key.strip_prefix("key") {
                if rest.len() == 1 {
                    if let Some(code) = letter_key(rest) {
                        return code;
                    }
                }
            }
            if let Some(code) = letter_key(key) {
                return code;
            }
            // Dígitos: "1"…"0" (ou "Digit1").
            let digit = key.strip_prefix("digit").unwrap_or(key);
            if let Some(code) = match digit {
                "1" => Some(KeyCode::Digit1),
                "2" => Some(KeyCode::Digit2),
                "3" => Some(KeyCode::Digit3),
                "4" => Some(KeyCode::Digit4),
                "5" => Some(KeyCode::Digit5),
                "6" => Some(KeyCode::Digit6),
                "7" => Some(KeyCode::Digit7),
                "8" => Some(KeyCode::Digit8),
                "9" => Some(KeyCode::Digit9),
                "0" => Some(KeyCode::Digit0),
                _ => None,
            } {
                return code;
            }
            // Teclas de função F1–F12.
            if let Some(n) = key.strip_prefix('f') {
                if let Ok(n) = n.parse::<u8>() {
                    let code = match n {
                        1 => KeyCode::F1,
                        2 => KeyCode::F2,
                        3 => KeyCode::F3,
                        4 => KeyCode::F4,
                        5 => KeyCode::F5,
                        6 => KeyCode::F6,
                        7 => KeyCode::F7,
                        8 => KeyCode::F8,
                        9 => KeyCode::F9,
                        10 => KeyCode::F10,
                        11 => KeyCode::F11,
                        12 => KeyCode::F12,
                        _ => KeyCode::F1,
                    };
                    if (1..=12).contains(&n) {
                        return code;
                    }
                }
            }
            if !key.is_empty() {
                warn!("ui: unknown modal key `{key}` — falling back to F1");
            }
            KeyCode::F1
        }
    }
}

/// "a".."z" → `KeyCode::KeyA`.."KeyZ".
fn letter_key(name: &str) -> Option<KeyCode> {
    let bytes = name.as_bytes();
    if bytes.len() == 1 && bytes[0].is_ascii_lowercase() {
        return Some(match bytes[0] {
            b'a' => KeyCode::KeyA,
            b'b' => KeyCode::KeyB,
            b'c' => KeyCode::KeyC,
            b'd' => KeyCode::KeyD,
            b'e' => KeyCode::KeyE,
            b'f' => KeyCode::KeyF,
            b'g' => KeyCode::KeyG,
            b'h' => KeyCode::KeyH,
            b'i' => KeyCode::KeyI,
            b'j' => KeyCode::KeyJ,
            b'k' => KeyCode::KeyK,
            b'l' => KeyCode::KeyL,
            b'm' => KeyCode::KeyM,
            b'n' => KeyCode::KeyN,
            b'o' => KeyCode::KeyO,
            b'p' => KeyCode::KeyP,
            b'q' => KeyCode::KeyQ,
            b'r' => KeyCode::KeyR,
            b's' => KeyCode::KeyS,
            b't' => KeyCode::KeyT,
            b'u' => KeyCode::KeyU,
            b'v' => KeyCode::KeyV,
            b'w' => KeyCode::KeyW,
            b'x' => KeyCode::KeyX,
            b'y' => KeyCode::KeyY,
            b'z' => KeyCode::KeyZ,
            _ => return None,
        });
    }
    None
}

/// Opens and closes modals from the keyboard and mirrors the state into
/// `Visibility` and [`UiModalsOpen`].
///
/// While an `<UiInput>` holds the keyboard, its keys are PROSE, not commands:
/// the toggle keys are ignored (Escape included — it blurs the field instead),
/// so typing "Menu" in a text field never flings the menu open.
pub fn drive_ui_modals(
    keys: Res<ButtonInput<KeyCode>>,
    typing: Res<super::widgets::UiFocusedInput>,
    mut open: ResMut<UiModalsOpen>,
    mut modals: Query<(&mut UiModal, &mut Visibility, Option<&UiId>)>,
) {
    let escape = typing.0.is_none() && keys.just_pressed(KeyCode::Escape);
    let mut anonymous = 0usize;
    for (mut modal, mut visibility, id) in &mut modals {
        let toggled = typing.0.is_none() && keys.just_pressed(modal.key);
        // Escape only ever closes: opening every modal with one key would be a
        // surprise, closing them all with it is the expected thing.
        let closed_by_escape = modal.open && modal.escape_closes && escape;
        if toggled || closed_by_escape {
            modal.open = if closed_by_escape { false } else { !modal.open };
        }
        let wanted = if modal.open {
            Visibility::Inherited
        } else {
            Visibility::Hidden
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
        if id.is_none() && modal.open {
            anonymous += 1;
        }
        if let Some(id) = id {
            let listed = open.open.iter().position(|o| *o == id.0);
            match (modal.open, listed) {
                (true, None) => open.open.push(id.0.clone()),
                (false, Some(index)) => {
                    open.open.remove(index);
                }
                _ => {}
            }
        }
    }
    // Modais sem id não entram em `open`, mas um menu em ecrã inteiro rouba
    // input ao gameplay seja qual for o id — contam em `any`.
    open.anonymous = anonymous;
}

/// Seeds each group's selection from the first page authored in it, so a menu
/// is never opened on a blank body.
pub fn seed_ui_tabs(
    mut tabs: ResMut<UiTabs>,
    pages: Query<(&UiTabPage, Option<&super::runtime::UiOrder>), Added<UiTabPage>>,
) {
    // Lowest sibling index wins, so "the first tab the author wrote" is the
    // default no matter what order the ECS reports the new pages in.
    let mut best: std::collections::HashMap<&str, (usize, &str)> = std::collections::HashMap::new();
    for (page, order) in &pages {
        let order = order.map(|o| o.0).unwrap_or(usize::MAX);
        let entry = best
            .entry(page.group.as_str())
            .or_insert((order, page.tab.as_str()));
        if order < entry.0 {
            *entry = (order, page.tab.as_str());
        }
    }
    for (group, (_, tab)) in best {
        tabs.active
            .entry(group.to_string())
            .or_insert_with(|| tab.to_string());
    }
}

/// Applies clicks to tab groups and mirrors the selection into button classes.
///
/// O `display` das páginas é sincronizado por [`sync_tab_pages`], DEPOIS do
/// re-estilo — ver o doc aí em baixo.
#[allow(clippy::type_complexity)]
pub fn drive_ui_tabs(
    mut commands: Commands,
    mut tabs: ResMut<UiTabs>,
    clicked: Query<(&Interaction, &UiTabButton), Changed<Interaction>>,
    mut buttons: Query<(Entity, &UiTabButton, &mut UiClasses)>,
) {
    for (interaction, button) in &clicked {
        if *interaction == Interaction::Pressed {
            tabs.select(&button.group, &button.tab);
        }
    }
    for (entity, button, mut classes) in &mut buttons {
        let active = tabs.selected(&button.group) == Some(button.tab.as_str());
        let changed = if active {
            classes.add("active")
        } else {
            classes.remove("active")
        };
        if changed {
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
}

/// Keeps every tab page's `display` in step with its group's selection.
///
/// Corre DEPOIS de `apply_ui_styles` (UiSet::Style), no padrão do
/// `sync_check_ticks`: o re-estilo recalcula o `Node` do zero (`apply_fresh`
/// repõe `display: Flex` quando o CSS não declara `display`) e desfaria um
/// `display: none` escrito em UiSet::Script — cada re-estilo global (resize,
/// classe num ancestral) mostrava as páginas inativas durante 1 frame. A
/// escrita é idempotente: só toca no nó quando o valor muda.
///
/// `display: none` rather than `Visibility::Hidden`: an inactive page must
/// not take part in layout, or the panel sizes itself to the tallest tab and
/// every page floats in a too-large box.
#[allow(clippy::type_complexity)]
pub fn sync_tab_pages(tabs: Res<UiTabs>, mut pages: Query<(&UiTabPage, &mut Node)>) {
    for (page, mut node) in &mut pages {
        let wanted = if tabs.selected(&page.group) == Some(page.tab.as_str()) {
            Display::Flex
        } else {
            Display::None
        };
        if node.display != wanted {
            node.display = wanted;
        }
    }
}

/// Keyboard navigation for open modals.
///
/// Two ways in, because a six-tab menu wants both: the **digits 1-9** jump
/// straight to a tab, and **`,` / `.`** step through them.
///
/// Tab and the arrow keys are deliberately *not* used: Bevy's focus navigation
/// consumes them before an `Update` system sees them, so a menu bound to Tab
/// silently does nothing. Digits are free here because the hotbar stops
/// listening while a menu is open.
pub fn drive_ui_tab_keys(
    keys: Res<ButtonInput<KeyCode>>,
    open: Res<UiModalsOpen>,
    mut tabs: ResMut<UiTabs>,
    buttons: Query<(&UiTabButton, Option<&super::runtime::UiOrder>)>,
) {
    if !open.any() {
        return;
    }
    let step = if keys.just_pressed(KeyCode::Period) || keys.just_pressed(KeyCode::BracketRight) {
        Some(1)
    } else if keys.just_pressed(KeyCode::Comma) || keys.just_pressed(KeyCode::BracketLeft) {
        Some(-1)
    } else {
        None
    };
    let jump = DIGIT_KEYS.iter().position(|key| keys.just_pressed(*key));
    if step.is_none() && jump.is_none() {
        return;
    }
    for (group, names) in tabs_by_group(&buttons) {
        if let Some(index) = jump {
            if let Some(tab) = names.get(index) {
                tabs.select(&group, tab);
            }
            continue;
        }
        if let Some(next) = cycle(&names, tabs.selected(&group), step.unwrap_or(0)) {
            tabs.select(&group, &next);
        }
    }
}

/// Digits 1-9, in order.
const DIGIT_KEYS: [KeyCode; 9] = [
    KeyCode::Digit1,
    KeyCode::Digit2,
    KeyCode::Digit3,
    KeyCode::Digit4,
    KeyCode::Digit5,
    KeyCode::Digit6,
    KeyCode::Digit7,
    KeyCode::Digit8,
    KeyCode::Digit9,
];

/// Tab names per group, in the order the author wrote them.
///
/// The order comes from [`crate::ui::runtime::UiOrder`] (the sibling index),
/// never from entity ids: Bevy recycles those, and sorting by them silently
/// reversed the whole tab bar.
fn tabs_by_group(
    buttons: &Query<(&UiTabButton, Option<&super::runtime::UiOrder>)>,
) -> Vec<(String, Vec<String>)> {
    let mut by_group: std::collections::HashMap<&str, Vec<(usize, &str)>> =
        std::collections::HashMap::new();
    for (button, order) in buttons {
        by_group.entry(button.group.as_str()).or_default().push((
            order.map(|o| o.0).unwrap_or(usize::MAX),
            button.tab.as_str(),
        ));
    }
    by_group
        .into_iter()
        .map(|(group, mut entries)| {
            entries.sort_by_key(|(order, _)| *order);
            (
                group.to_string(),
                entries
                    .into_iter()
                    .map(|(_, tab)| tab.to_string())
                    .collect(),
            )
        })
        .collect()
}

/// Scrolls the hovered viewport with the mouse wheel.
///
/// `Interaction` only lands on the element directly under the cursor, so the
/// walk up the hierarchy is what makes scrolling work when the pointer is over
/// a row rather than over the viewport itself.
pub fn drive_ui_scroll(
    mut wheel: bevy::ecs::message::MessageReader<MouseWheel>,
    hovered: Query<(Entity, &Interaction)>,
    parents: Query<&ChildOf>,
    scrolls: Query<&UiScroll>,
    mut positions: Query<&mut ScrollPosition>,
) {
    let mut delta = 0.0;
    for event in wheel.read() {
        delta += match event.unit {
            MouseScrollUnit::Line => event.y,
            MouseScrollUnit::Pixel => event.y / 26.0,
        };
    }
    if delta == 0.0 {
        return;
    }
    let Some((target, _)) = hovered
        .iter()
        .find(|(_, interaction)| **interaction != Interaction::None)
    else {
        return;
    };
    let mut current = target;
    for _ in 0..32 {
        if let Ok(scroll) = scrolls.get(current) {
            if let Ok(mut position) = positions.get_mut(current) {
                let step = delta * scroll.speed;
                if scroll.vertical {
                    position.0.y = (position.0.y - step).max(0.0);
                } else {
                    position.0.x = (position.0.x - step).max(0.0);
                }
            }
            return;
        }
        let Ok(parent) = parents.get(current) else {
            return;
        };
        current = parent.parent();
    }
}

#[cfg(test)]
#[cfg(test)]
mod parse_key_tests {
    use super::*;

    #[test]
    fn test_parse_key_letters_digits_function() {
        assert_eq!(parse_key("p"), KeyCode::KeyP);
        assert_eq!(parse_key("KeyP"), KeyCode::KeyP);
        assert_eq!(parse_key("Q"), KeyCode::KeyQ);
        assert_eq!(parse_key("5"), KeyCode::Digit5);
        assert_eq!(parse_key("digit0"), KeyCode::Digit0);
        assert_eq!(parse_key("f5"), KeyCode::F5);
        assert_eq!(parse_key("F12"), KeyCode::F12);
    }

    #[test]
    fn test_parse_key_navigation_and_punctuation() {
        assert_eq!(parse_key("pageup"), KeyCode::PageUp);
        assert_eq!(parse_key("page-down"), KeyCode::PageDown);
        assert_eq!(parse_key("home"), KeyCode::Home);
        assert_eq!(parse_key("escape"), KeyCode::Escape);
        assert_eq!(parse_key("esc"), KeyCode::Escape);
        assert_eq!(parse_key("`"), KeyCode::Backquote);
        assert_eq!(parse_key("backquote"), KeyCode::Backquote);
        assert_eq!(parse_key(","), KeyCode::Comma);
        assert_eq!(parse_key("["), KeyCode::BracketLeft);
        assert_eq!(parse_key("arrowleft"), KeyCode::ArrowLeft);
        assert_eq!(parse_key("up"), KeyCode::ArrowUp);
        assert_eq!(parse_key("space"), KeyCode::Space);
    }

    #[test]
    fn test_parse_key_unknown_falls_back_to_f1() {
        assert_eq!(parse_key("wat"), KeyCode::F1);
        assert_eq!(parse_key(""), KeyCode::F1);
        assert_eq!(parse_key("f13"), KeyCode::F1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_key_covers_the_menu_keys_and_falls_back_loudly() {
        assert_eq!(parse_key("q"), KeyCode::KeyQ);
        assert_eq!(parse_key("F1"), KeyCode::F1);
        assert_eq!(parse_key("Tab"), KeyCode::Tab);
        assert_eq!(parse_key("esc"), KeyCode::Escape);
        // Unknown or empty never yields an unopenable modal.
        assert_eq!(parse_key("hyperspace"), KeyCode::F1);
        assert_eq!(parse_key(""), KeyCode::F1);
    }

    #[test]
    fn test_tabs_select_reports_real_changes() {
        let mut tabs = UiTabs::default();
        assert_eq!(tabs.selected("menu"), None);
        assert!(tabs.select("menu", "quests"));
        assert_eq!(tabs.selected("menu"), Some("quests"));
        assert!(!tabs.select("menu", "quests"), "re-selecting is a no-op");
        assert!(tabs.select("menu", "bag"));
        // Groups are independent.
        assert!(tabs.select("shop", "buy"));
        assert_eq!(tabs.selected("menu"), Some("bag"));
    }

    #[test]
    fn test_cycle_wraps_in_both_directions() {
        let tabs: Vec<String> = ["quests", "bag", "skills"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(cycle(&tabs, Some("quests"), 1).as_deref(), Some("bag"));
        assert_eq!(cycle(&tabs, Some("skills"), 1).as_deref(), Some("quests"));
        assert_eq!(cycle(&tabs, Some("quests"), -1).as_deref(), Some("skills"));
        // An unknown or absent selection starts from the first tab.
        assert_eq!(cycle(&tabs, None, 1).as_deref(), Some("bag"));
        assert_eq!(cycle(&tabs, Some("nope"), -1).as_deref(), Some("skills"));
        assert_eq!(cycle(&[], Some("quests"), 1), None);
    }

    use super::super::runtime::UiOrder;

    /// World with one open modal and a tab bar authored in the given order.
    fn tabbed_world(tabs_in_order: &[&str]) -> World {
        let mut world = World::new();
        world.init_resource::<UiTabs>();
        world.init_resource::<ButtonInput<KeyCode>>();
        world.insert_resource(UiModalsOpen {
            open: vec!["menu".into()],
            ..Default::default()
        });
        // Spawned in REVERSE author order on purpose: entity ids are recycled
        // by Bevy, so only `UiOrder` may decide what "the next tab" means.
        for (index, tab) in tabs_in_order.iter().enumerate().rev() {
            world.spawn((
                UiTabButton {
                    group: "menu".into(),
                    tab: (*tab).into(),
                },
                UiOrder(index),
            ));
        }
        world
    }

    fn run_tab_keys(world: &mut World) {
        #[allow(clippy::type_complexity)]
        let mut state: bevy::ecs::system::SystemState<(
            Res<ButtonInput<KeyCode>>,
            Res<UiModalsOpen>,
            ResMut<UiTabs>,
            Query<(&UiTabButton, Option<&UiOrder>)>,
        )> = bevy::ecs::system::SystemState::new(world);
        let (keys, open, tabs, buttons) = state.get_mut(world).expect("system state");
        drive_ui_tab_keys(keys, open, tabs, buttons);
    }

    /// One fresh key press. `ButtonInput::press` only records `just_pressed`
    /// for a key that was not already down, so the previous press has to be
    /// released first — exactly what a real frame does.
    fn press(world: &mut World, key: KeyCode) {
        let mut keys = world.resource_mut::<ButtonInput<KeyCode>>();
        keys.release_all();
        keys.clear();
        keys.press(key);
    }

    #[test]
    fn test_digit_keys_jump_straight_to_the_nth_tab() {
        let mut world = tabbed_world(&["quests", "bag", "skills", "shop", "controls", "system"]);
        press(&mut world, KeyCode::Digit3);
        run_tab_keys(&mut world);
        assert_eq!(world.resource::<UiTabs>().selected("menu"), Some("skills"));
        press(&mut world, KeyCode::Digit1);
        run_tab_keys(&mut world);
        assert_eq!(world.resource::<UiTabs>().selected("menu"), Some("quests"));
        press(&mut world, KeyCode::Digit6);
        run_tab_keys(&mut world);
        assert_eq!(world.resource::<UiTabs>().selected("menu"), Some("system"));
        // A digit past the last tab leaves the selection alone.
        press(&mut world, KeyCode::Digit9);
        run_tab_keys(&mut world);
        assert_eq!(world.resource::<UiTabs>().selected("menu"), Some("system"));
    }

    #[test]
    fn test_step_keys_walk_the_tabs_in_author_order() {
        let mut world = tabbed_world(&["quests", "bag", "skills"]);
        press(&mut world, KeyCode::Digit1);
        run_tab_keys(&mut world);
        press(&mut world, KeyCode::Period);
        run_tab_keys(&mut world);
        assert_eq!(
            world.resource::<UiTabs>().selected("menu"),
            Some("bag"),
            "`.` moves forward through the AUTHORED order, not entity order"
        );
        press(&mut world, KeyCode::Comma);
        run_tab_keys(&mut world);
        assert_eq!(world.resource::<UiTabs>().selected("menu"), Some("quests"));
        // Wraps backwards off the first tab.
        press(&mut world, KeyCode::Comma);
        run_tab_keys(&mut world);
        assert_eq!(world.resource::<UiTabs>().selected("menu"), Some("skills"));
    }

    #[test]
    fn test_tab_keys_do_nothing_while_every_modal_is_closed() {
        let mut world = tabbed_world(&["quests", "bag"]);
        world.resource_mut::<UiModalsOpen>().open.clear();
        press(&mut world, KeyCode::Digit2);
        run_tab_keys(&mut world);
        assert_eq!(
            world.resource::<UiTabs>().selected("menu"),
            None,
            "a closed menu must not swallow the player's number keys"
        );
    }

    #[test]
    fn test_modals_open_tracks_ids() {
        let mut open = UiModalsOpen::default();
        assert!(!open.any());
        open.open.push("menu".into());
        assert!(open.any());
        assert!(open.is_open("menu"));
        assert!(!open.is_open("shop"));
    }

    #[test]
    fn test_any_counts_idless_modals() {
        // Um modal sem `id` não entra em `open`, mas rouba input ao gameplay
        // na mesma — `any` é o que o espelho `MenusOpen` consulta.
        let mut open = UiModalsOpen::default();
        open.anonymous = 1;
        assert!(open.any(), "um modal sem id também é um menu aberto");
        open.anonymous = 0;
        assert!(!open.any());
    }

    #[test]
    fn test_seed_ui_tabs_picks_the_first_authored_page() {
        use super::super::runtime::UiOrder;
        let mut world = World::new();
        world.init_resource::<UiTabs>();
        // Spawned out of author order on purpose: entity ids are recycled by
        // Bevy, so the seed must key on `UiOrder`, not on spawn order.
        world.spawn((
            UiTabPage {
                group: "menu".into(),
                tab: "bag".into(),
            },
            UiOrder(3),
        ));
        world.spawn((
            UiTabPage {
                group: "menu".into(),
                tab: "quests".into(),
            },
            UiOrder(1),
        ));
        #[allow(clippy::type_complexity)]
        let mut state: bevy::ecs::system::SystemState<(
            ResMut<UiTabs>,
            Query<(&UiTabPage, Option<&UiOrder>), Added<UiTabPage>>,
        )> = bevy::ecs::system::SystemState::new(&mut world);
        let (tabs, pages) = state.get_mut(&mut world).expect("system state");
        seed_ui_tabs(tabs, pages);
        assert_eq!(
            world.resource::<UiTabs>().selected("menu"),
            Some("quests"),
            "the lowest-ordered page is the default"
        );
    }
}
