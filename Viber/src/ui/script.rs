//! `viber.ui.*` — the Luau surface of the declarative UI.
//!
//! Installed onto the existing script host rather than baked into
//! `src/luau.rs`: the UI owns its own API, and the scripting module stays the
//! sandbox/VM owner. Calls queue [`UiCommand`]s that are applied after all
//! scripts have run, exactly like the movement commands — a script never holds
//! a `World`.
//!
//! ```lua
//! function on_update(dt)
//!   viber.ui.set_text("clock", viber.ui.get("clock"))
//!   viber.ui.toggle_class("hp-orb", "danger", viber.ui.number("health") < 0.3)
//!   if viber.ui.clicked("btn-save") then viber.log("saving") end
//! end
//! ```

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use bevy::prelude::*;
use mlua::{Lua, Table, Value};

use super::bind::UiData;
use super::list::ListRow;
use super::runtime::{
    UiBar, UiClasses, UiClicks, UiCooldown, UiDisabled, UiInlineStyle, UiRegistry, UiStyleDirty,
};
use super::style::parse_declarations;
use super::widgets::{UiCheck, UiFocusedInput, UiInput, normalized_range};

/// A UI mutation queued by a script, applied once per frame.
#[derive(Debug, Clone)]
pub enum UiCommand {
    SetText {
        id: String,
        text: String,
    },
    SetValue {
        id: String,
        value: f32,
    },
    SetVisible {
        id: String,
        visible: bool,
    },
    SetDisabled {
        id: String,
        disabled: bool,
    },
    AddClass {
        id: String,
        class: String,
    },
    RemoveClass {
        id: String,
        class: String,
    },
    SetStyle {
        id: String,
        declarations: String,
    },
    /// Open / close a `<UiModal>` by id.
    SetModal {
        id: String,
        open: bool,
    },
    /// Select a tab inside a group.
    SelectTab {
        group: String,
        tab: String,
    },
    /// Raise a gameplay action (`learn`, `buy`, `sell`, `save`, `load`).
    Action {
        name: String,
        arg: String,
    },
    /// Flip a `<UiCheck>`.
    SetChecked {
        id: String,
        checked: bool,
    },
    /// Replace (or clear, with `none`) the motion on an element.
    SetAnim {
        id: String,
        spec: String,
    },
    /// Feed a `<UiList>` source from script data.
    SetList {
        name: String,
        rows: Vec<ListRow>,
    },
    /// Release a script-fed list source so the engine may feed it again.
    Unlist {
        name: String,
    },
    /// Give the keyboard to an `<UiInput>`.
    Focus {
        id: String,
    },
}

/// Queue shared between the Lua closures and the apply system.
///
/// `Arc<Mutex<…>>` rather than Lua app data: the UI API is installed onto a
/// host owned by another module, so it cannot extend that module's context
/// struct — and the lock is uncontended (scripts run on one thread).
#[derive(Clone, Default, Resource)]
pub struct UiCommandQueue(pub Arc<Mutex<Vec<UiCommand>>>);

impl UiCommandQueue {
    fn push(&self, command: UiCommand) {
        if let Ok(mut queue) = self.0.lock() {
            queue.push(command);
        }
    }

    /// Takes everything queued so far.
    pub fn drain(&self) -> Vec<UiCommand> {
        self.0
            .lock()
            .map(|mut queue| std::mem::take(&mut *queue))
            .unwrap_or_default()
    }
}

/// Read-side snapshot the Lua closures see: bindings, this frame's clicks, and
/// per-element state by id.
#[derive(Clone, Default)]
pub struct UiScriptView {
    pub data: Arc<Mutex<UiData>>,
    pub clicks: Arc<Mutex<Vec<String>>>,
    pub modals: Arc<Mutex<Vec<String>>>,
    pub tabs: Arc<Mutex<std::collections::HashMap<String, String>>>,
    /// Everything addressable by id, refreshed each frame from the registry.
    pub elements: Arc<Mutex<HashMap<String, UiElementRead>>>,
    /// Script-fed and engine list sources, refreshed on version change.
    pub lists: Arc<Mutex<HashMap<String, Vec<ListRow>>>>,
    /// Id of the `<UiInput>` holding the keyboard, if any.
    pub focused: Arc<Mutex<Option<String>>>,
}

/// What `viber.ui.read(id)` returns about one element.
#[derive(Debug, Clone, Default)]
pub struct UiElementRead {
    /// Current text (an input reports its typed value, not the placeholder).
    pub text: String,
    /// Bar / cooldown / slider value.
    pub value: f32,
    pub visible: bool,
    pub checked: bool,
    pub disabled: bool,
}

/// Resource wrapper so the snapshot can be refreshed from a system.
#[derive(Clone, Default, Resource)]
pub struct UiScriptState {
    pub view: UiScriptView,
    pub queue: UiCommandQueue,
    /// True once the `viber.ui` table exists on the host.
    pub installed: bool,
}

/// Installs `viber.ui` onto an existing Lua VM.
///
/// Returns an error only when the host has no `viber` table yet — every other
/// failure would be a bug in this function.
pub fn install_ui_api(lua: &Lua, state: &UiScriptState) -> mlua::Result<()> {
    let viber: Table = lua.globals().get("viber")?;
    let ui = lua.create_table()?;

    /// Every setter is the same shape: take args, queue one command.
    macro_rules! command {
        ($name:literal, $args:ty, $build:expr) => {{
            let queue = state.queue.clone();
            let build: fn($args) -> UiCommand = $build;
            ui.set(
                $name,
                lua.create_function(move |_, args: $args| {
                    queue.push(build(args));
                    Ok(())
                })?,
            )?;
        }};
    }

    command!("set_text", (String, String), |(id, text)| {
        UiCommand::SetText { id, text }
    });
    command!("set_value", (String, f32), |(id, value)| {
        UiCommand::SetValue { id, value }
    });
    command!("set_visible", (String, bool), |(id, visible)| {
        UiCommand::SetVisible { id, visible }
    });
    command!("set_disabled", (String, bool), |(id, disabled)| {
        UiCommand::SetDisabled { id, disabled }
    });
    command!("add_class", (String, String), |(id, class)| {
        UiCommand::AddClass { id, class }
    });
    command!("remove_class", (String, String), |(id, class)| {
        UiCommand::RemoveClass { id, class }
    });
    command!("set_style", (String, String), |(id, declarations)| {
        UiCommand::SetStyle { id, declarations }
    });
    command!("open", (String, bool), |(id, open)| UiCommand::SetModal {
        id,
        open
    });
    command!("select_tab", (String, String), |(group, tab)| {
        UiCommand::SelectTab { group, tab }
    });
    command!("action", (String, String), |(name, arg)| {
        UiCommand::Action { name, arg }
    });
    command!("set_checked", (String, bool), |(id, checked)| {
        UiCommand::SetChecked { id, checked }
    });
    command!("set_anim", (String, String), |(id, spec)| {
        UiCommand::SetAnim { id, spec }
    });
    command!("focus", (String, ()), |(id, ())| UiCommand::Focus { id });

    // viber.ui.list(name, { {campo = valor, …}, … }) — feeds a <UiList>
    // source from script data; numbers and booleans stringify so a row is
    // always what a template substitution expects.
    {
        let queue = state.queue.clone();
        ui.set(
            "list",
            lua.create_function(move |_, (name, rows): (String, Table)| {
                let mut converted: Vec<ListRow> = Vec::new();
                for entry in rows.sequence_values::<Table>() {
                    let table = entry?;
                    let mut row = ListRow::new();
                    for pair in table.pairs::<String, Value>() {
                        let (key, value) = pair?;
                        row.insert(key, lua_value_to_string(&value));
                    }
                    converted.push(row);
                }
                queue.push(UiCommand::SetList {
                    name,
                    rows: converted,
                });
                Ok(())
            })?,
        )?;
    }

    // toggle_class(id, class, on) — the one call a HUD script makes most.
    {
        let queue = state.queue.clone();
        ui.set(
            "toggle_class",
            lua.create_function(move |_, (id, class, on): (String, String, bool)| {
                queue.push(if on {
                    UiCommand::AddClass { id, class }
                } else {
                    UiCommand::RemoveClass { id, class }
                });
                Ok(())
            })?,
        )?;
    }

    // viber.ui.get(name) -> string — the formatted binding value.
    {
        let view = state.view.clone();
        ui.set(
            "get",
            lua.create_function(move |_, name: String| {
                let text = view
                    .data
                    .lock()
                    .ok()
                    .and_then(|data| data.get(&name))
                    .map(|value| value.text)
                    .unwrap_or_default();
                Ok(text)
            })?,
        )?;
    }
    // viber.ui.number(name) -> number — the 0..1 fraction (or raw count).
    {
        let view = state.view.clone();
        ui.set(
            "number",
            lua.create_function(move |_, name: String| {
                let value = view
                    .data
                    .lock()
                    .ok()
                    .and_then(|data| data.get(&name))
                    .map(|value| value.fraction)
                    .unwrap_or(0.0);
                Ok(value)
            })?,
        )?;
    }
    // viber.ui.is_open(id) -> bool — is that modal showing?
    {
        let view = state.view.clone();
        ui.set(
            "is_open",
            lua.create_function(move |_, id: String| {
                let open = view
                    .modals
                    .lock()
                    .map(|open| open.contains(&id))
                    .unwrap_or(false);
                Ok(open)
            })?,
        )?;
    }
    // viber.ui.tab(group) -> string — the selected tab.
    {
        let view = state.view.clone();
        ui.set(
            "tab",
            lua.create_function(move |_, group: String| {
                let tab = view
                    .tabs
                    .lock()
                    .ok()
                    .and_then(|tabs| tabs.get(&group).cloned())
                    .unwrap_or_default();
                Ok(tab)
            })?,
        )?;
    }
    // viber.ui.clicked(id) -> bool — true on the frame the element was pressed.
    {
        let view = state.view.clone();
        ui.set(
            "clicked",
            lua.create_function(move |_, id: String| {
                let hit = view
                    .clicks
                    .lock()
                    .map(|clicks| clicks.contains(&id))
                    .unwrap_or(false);
                Ok(hit)
            })?,
        )?;
    }

    // viber.ui.unlist(name) — devolve a fonte à engine (o script deixa de a
    // possuir; menu_data volta a alimentá-la no frame seguinte).
    {
        let queue = state.queue.clone();
        ui.set(
            "unlist",
            lua.create_function(move |_, name: String| {
                queue.push(UiCommand::Unlist { name });
                Ok(())
            })?,
        )?;
    }

    // viber.ui.read(id) -> {text, value, visible, checked, disabled} | nil.
    {
        let view = state.view.clone();
        ui.set(
            "read",
            lua.create_function(move |lua, id: String| {
                let read = view
                    .elements
                    .lock()
                    .ok()
                    .and_then(|elements| elements.get(&id).cloned());
                match read {
                    Some(element) => {
                        let table = lua.create_table()?;
                        table.set("text", element.text)?;
                        table.set("value", element.value)?;
                        table.set("visible", element.visible)?;
                        table.set("checked", element.checked)?;
                        table.set("disabled", element.disabled)?;
                        Ok(mlua::Value::Table(table))
                    }
                    None => Ok(mlua::Value::Nil),
                }
            })?,
        )?;
    }
    // viber.ui.exists(id) -> bool — is that id addressable right now?
    {
        let view = state.view.clone();
        ui.set(
            "exists",
            lua.create_function(move |_, id: String| {
                let exists = view
                    .elements
                    .lock()
                    .map(|elements| elements.contains_key(&id))
                    .unwrap_or(false);
                Ok(exists)
            })?,
        )?;
    }
    // viber.ui.list_count(name) -> number — rows currently in a list source.
    {
        let view = state.view.clone();
        ui.set(
            "list_count",
            lua.create_function(move |_, name: String| {
                let count = view
                    .lists
                    .lock()
                    .map(|lists| lists.get(&name).map(Vec::len).unwrap_or(0))
                    .unwrap_or(0);
                Ok(count)
            })?,
        )?;
    }
    // viber.ui.rows(name) -> {{campo = valor, …}, …} — a copy of a source.
    {
        let view = state.view.clone();
        ui.set(
            "rows",
            lua.create_function(move |lua, name: String| {
                let table = lua.create_table()?;
                if let Ok(lists) = view.lists.lock() {
                    for (index, row) in lists.get(&name).into_iter().flatten().enumerate() {
                        let entry = lua.create_table()?;
                        for (key, value) in row {
                            entry.set(key.as_str(), value.as_str())?;
                        }
                        table.set(index + 1, entry)?;
                    }
                }
                Ok(table)
            })?,
        )?;
    }
    // viber.ui.focused() -> string | nil — the input holding the keyboard.
    {
        let view = state.view.clone();
        ui.set(
            "focused",
            lua.create_function(move |_, ()| {
                Ok(view.focused.lock().ok().and_then(|focused| focused.clone()))
            })?,
        )?;
    }

    viber.set("ui", ui)?;
    Ok(())
}

/// Stringifies a Lua value for template rows — a row is always strings.
fn lua_value_to_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.to_string_lossy().to_string(),
        Value::Integer(n) => n.to_string(),
        Value::Number(n) => format!("{n}"),
        Value::Boolean(true) => "1".to_string(),
        _ => String::new(),
    }
}

/// Applies the queued script mutations to the live UI.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn apply_ui_commands(
    mut commands: Commands,
    state: Res<UiScriptState>,
    registry: Res<UiRegistry>,
    // Warn 1× por id inexistente (mesmo padrão do `UiBindWarnings`): os
    // setters correm por frame e um id errado virava spam no log.
    mut warned: Local<std::collections::HashSet<String>>,
    mut classes: Query<&mut UiClasses>,
    mut inline: Query<&mut UiInlineStyle>,
    mut text: Query<&mut Text>,
    mut visibility: Query<&mut Visibility>,
    mut fades: Query<&mut super::fade::UiFade>,
    mut widgets: Query<
        AnyOf<(
            &'static mut UiBar,
            &'static mut UiCooldown,
            &'static mut UiCheck,
            &'static mut UiInput,
            &'static mut super::widgets::UiSlider,
        )>,
    >,
    mut modals: Query<&mut super::modal::UiModal>,
    mut tabs: ResMut<super::modal::UiTabs>,
    mut lists: ResMut<super::list::UiLists>,
    mut focus: ResMut<UiFocusedInput>,
    mut actions: bevy::ecs::message::MessageWriter<super::actions::UiAction>,
) {
    for command in state.queue.drain() {
        let id = match &command {
            UiCommand::SetText { id, .. }
            | UiCommand::SetValue { id, .. }
            | UiCommand::SetVisible { id, .. }
            | UiCommand::SetDisabled { id, .. }
            | UiCommand::AddClass { id, .. }
            | UiCommand::RemoveClass { id, .. }
            | UiCommand::SetModal { id, .. }
            | UiCommand::SetStyle { id, .. }
            | UiCommand::SetChecked { id, .. }
            | UiCommand::SetAnim { id, .. }
            | UiCommand::Focus { id } => id,
            // These address a group / the game / a data source, not an element.
            UiCommand::SelectTab { group, tab } => {
                tabs.select(group, tab);
                continue;
            }
            UiCommand::Action { name, arg } => {
                actions.write(super::actions::UiAction {
                    name: name.clone(),
                    arg: arg.clone(),
                });
                continue;
            }
            UiCommand::SetList { name, rows } => {
                lists.set_script(name, rows.clone());
                continue;
            }
            // These address a data source / group / the game, not an element.
            UiCommand::Unlist { name } => {
                lists.release_script(name);
                continue;
            }
        };
        let Some(entity) = registry.get(id) else {
            if warned.insert(id.clone()) {
                warn!("ui: script addressed unknown element `{id}`");
            }
            continue;
        };
        // The widget value-holders, whichever ones this entity carries.
        let (bar, cooldown, check, input, slider) = widgets
            .get_mut(entity)
            .unwrap_or((None, None, None, None, None));
        match command {
            UiCommand::SetText { text: value, .. } => {
                // An input owns its text: writing it must update the flag the
                // widget syncs from, not just a stale `Text` on the root.
                if let Some(mut input) = input {
                    input.text = value;
                } else if let Ok(mut text) = text.get_mut(entity) {
                    if text.0 != value {
                        text.0 = value;
                    }
                }
            }
            UiCommand::SetValue { value, .. } => {
                if let Some(mut bar) = bar {
                    bar.value = value;
                }
                if let Some(mut cooldown) = cooldown {
                    cooldown.value = value;
                }
                // A slider clamps the raw number to its own min/max.
                if let Some(mut slider) = slider {
                    // Defesa em profundidade: o parse já normalizou, mas
                    // `clamp` asserta com um intervalo cru.
                    let (min, max) = normalized_range(slider.min, slider.max);
                    slider.value = value.clamp(min, max);
                }
            }
            UiCommand::SetVisible { visible, .. } => {
                // Um elemento com fade não tem `Visibility` própria estável: o
                // `drive_ui_fades` repõe Hidden no frame seguinte (um fade sem
                // bind nasce `shown=false` para sempre), por isso escrever por
                // cima nunca o faria APARECER. O caminho do fade é o mesmo
                // interruptor — `shown` — e o alpha caminha sozinho. Com bind,
                // o binding continua a mandar: o fade é dele.
                if let Ok(mut fade) = fades.get_mut(entity) {
                    fade.shown = visible;
                } else if let Ok(mut visibility) = visibility.get_mut(entity) {
                    *visibility = if visible {
                        Visibility::Inherited
                    } else {
                        Visibility::Hidden
                    };
                }
            }
            UiCommand::SetDisabled { disabled, .. } => {
                let mut entity_commands = commands.entity(entity);
                if disabled {
                    entity_commands.insert(UiDisabled);
                } else {
                    entity_commands.remove::<UiDisabled>();
                }
                commands.entity(entity).insert(UiStyleDirty);
            }
            UiCommand::AddClass { class, .. } => {
                if let Ok(mut classes) = classes.get_mut(entity) {
                    if classes.add(&class) {
                        commands.entity(entity).insert(UiStyleDirty);
                    }
                }
            }
            UiCommand::RemoveClass { class, .. } => {
                if let Ok(mut classes) = classes.get_mut(entity) {
                    if classes.remove(&class) {
                        commands.entity(entity).insert(UiStyleDirty);
                    }
                }
            }
            UiCommand::SetModal { open, .. } => {
                if let Ok(mut modal) = modals.get_mut(entity) {
                    modal.open = open;
                }
            }
            UiCommand::SetChecked { checked, .. } => {
                if let Some(mut check) = check {
                    check.checked = checked;
                }
            }
            UiCommand::SetAnim { spec, .. } => {
                let mut entity_commands = commands.entity(entity);
                // `none`/`""` removes; an unknown spec also reads as "stop" —
                // kinder than warning on every frame a script re-arms it.
                if let Some(anim) = super::anim::UiAnim::parse(&spec) {
                    entity_commands.insert(anim);
                } else {
                    entity_commands.remove::<super::anim::UiAnim>();
                }
            }
            UiCommand::Focus { .. } => {
                // Blur whoever had it, then hand the keyboard over. Only an
                // input can actually take it.
                let takes = input.is_some();
                if let Some(previous) = focus.0 {
                    if previous != entity {
                        if let Ok(mut classes) = classes.get_mut(previous) {
                            if classes.remove("focused") {
                                commands.entity(previous).insert(UiStyleDirty);
                            }
                        }
                    }
                }
                if takes {
                    focus.0 = Some(entity);
                    if let Ok(mut classes) = classes.get_mut(entity) {
                        if classes.add("focused") {
                            commands.entity(entity).insert(UiStyleDirty);
                        }
                    }
                }
            }
            UiCommand::SelectTab { .. }
            | UiCommand::Action { .. }
            | UiCommand::SetList { .. }
            | UiCommand::Unlist { .. } => {
                unreachable!("handled above")
            }
            UiCommand::SetStyle { declarations, .. } => {
                let props = parse_declarations(&declarations, "viber.ui.set_style");
                if let Ok(mut inline) = inline.get_mut(entity) {
                    inline.0.merge(&props);
                } else {
                    commands.entity(entity).insert(UiInlineStyle(props));
                }
                commands.entity(entity).insert(UiStyleDirty);
            }
        }
    }
}

/// Republishes the frame's bindings, element state and clicks into the
/// script-visible view.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn publish_ui_script_view(
    state: Res<UiScriptState>,
    data: Res<UiData>,
    clicks: Res<UiClicks>,
    modals: Res<super::modal::UiModalsOpen>,
    tabs: Res<super::modal::UiTabs>,
    registry: Res<UiRegistry>,
    lists: Res<super::list::UiLists>,
    focus: Res<UiFocusedInput>,
    mut last_list_versions: Local<HashMap<String, u64>>,
    texts: Query<&Text>,
    widgets: Query<
        AnyOf<(
            &'static UiBar,
            &'static UiCooldown,
            &'static super::widgets::UiSlider,
            &'static UiCheck,
            &'static UiInput,
        )>,
    >,
    visibility: Query<&Visibility>,
    disabled: Query<Has<UiDisabled>>,
) {
    // Cada bloco republica só o que MUDOU. Sem estes gates o sistema clonava
    // o `UiData` inteiro (10+ `String`) e copiava clicks/modais/tabs em TODOS
    // os frames, mesmo com o mundo parado e sem um único script de UI a
    // escrever. Um frame em falta não muda nada: o script lê o mesmo valor que
    // já lá estava, e no frame seguinte a mudança entra por `is_changed`.
    if data.is_changed()
        && let Ok(mut snapshot) = state.view.data.lock()
    {
        *snapshot = data.clone();
    }
    if clicks.is_changed()
        && let Ok(mut snapshot) = state.view.clicks.lock()
    {
        snapshot.clone_from(&clicks.0);
    }
    if modals.is_changed()
        && let Ok(mut snapshot) = state.view.modals.lock()
    {
        snapshot.clone_from(&modals.open);
    }
    if tabs.is_changed()
        && let Ok(mut snapshot) = state.view.tabs.lock()
    {
        snapshot.clone_from(&tabs.active);
    }
    // Per-element state by id. The registry is a few hundred entries, so the
    // walk is cheap; keeping it on the view means a script can ask for ANY
    // element, not just the ones it wrote.
    //
    // O mapa actualiza-se NO SÍTIO, sem `clear()` + reinserção: o `id.clone()`
    // e o `text.clone()` por elemento registado eram centenas de alocações de
    // `String` em cada frame (HUD + menu + profiler), com o valor quase sempre
    // igual ao da véspera. Só as entradas cujo elemento saiu do registry caem.
    if let Ok(mut elements) = state.view.elements.lock() {
        elements.retain(|id, _| registry.by_id.contains_key(id.as_str()));
        for (id, &entity) in &registry.by_id {
            // (bar, cooldown, slider, check, input) — whichever this entity has.
            let (bar, cooldown, slider, check, input) = widgets
                .get(entity)
                .unwrap_or((None, None, None, None, None));
            // Empréstimos, não clones: a `String` só nasce na inserção de um
            // elemento novo.
            let text: &str = input
                .map(|input| input.text.as_str())
                .or_else(|| texts.get(entity).map(|text| text.0.as_str()).ok())
                .unwrap_or_default();
            let value = slider
                .map(|slider| slider.value)
                .or_else(|| bar.map(|bar| bar.value))
                .or_else(|| cooldown.map(|cd| cd.value))
                .unwrap_or(0.0);
            let visible = visibility.get(entity).is_ok_and(|v| *v != Visibility::Hidden);
            let checked = check.is_some_and(|check| check.checked);
            let disabled = disabled.get(entity).unwrap_or(false);
            if elements.contains_key(id.as_str()) {
                let entry = elements
                    .get_mut(id.as_str())
                    .expect("contains_key acabou de dizer que existe");
                if entry.text != text
                    || entry.value != value
                    || entry.visible != visible
                    || entry.checked != checked
                    || entry.disabled != disabled
                {
                    entry.text.clear();
                    entry.text.push_str(text);
                    entry.value = value;
                    entry.visible = visible;
                    entry.checked = checked;
                    entry.disabled = disabled;
                }
            } else {
                elements.insert(
                    id.clone(),
                    UiElementRead {
                        text: text.to_owned(),
                        value,
                        visible,
                        checked,
                        disabled,
                    },
                );
            }
        }
    }
    // Focused input id, for `viber.ui.focused()`.
    if let Ok(mut snapshot) = state.view.focused.lock() {
        *snapshot = focus.0.and_then(|entity| {
            registry
                .by_id
                .iter()
                .find(|(_, e)| **e == entity)
                .map(|(id, _)| id.clone())
        });
    }
    // Lists: copy a source only when its version moved, so a 40-slot bag
    // costs nothing while nobody picks anything up.
    if let Ok(mut snapshot) = state.view.lists.lock() {
        for name in lists.names() {
            let version = lists.version(&name);
            if last_list_versions.get(&name) != Some(&version) {
                snapshot.insert(name.clone(), lists.rows(&name).to_vec());
                last_list_versions.insert(name, version);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_queue_drains_once() {
        let queue = UiCommandQueue::default();
        queue.push(UiCommand::SetText {
            id: "a".into(),
            text: "x".into(),
        });
        assert_eq!(queue.drain().len(), 1);
        assert!(queue.drain().is_empty(), "a drained queue stays empty");
    }

    /// Builds a VM with a bare `viber` table, the way the real host starts.
    fn host() -> (Lua, UiScriptState) {
        let lua = Lua::new();
        let viber = lua.create_table().unwrap();
        lua.globals().set("viber", viber).unwrap();
        let state = UiScriptState::default();
        install_ui_api(&lua, &state).expect("install");
        (lua, state)
    }

    #[test]
    fn test_script_calls_queue_commands() {
        let (lua, state) = host();
        lua.load(
            r#"
            viber.ui.set_text("hp", "42/100")
            viber.ui.set_value("hp-bar", 0.42)
            viber.ui.toggle_class("orb", "danger", true)
            viber.ui.toggle_class("orb", "danger", false)
            viber.ui.set_style("orb", "background: #ff0000")
            "#,
        )
        .exec()
        .expect("script runs");
        let queued = state.queue.drain();
        assert_eq!(queued.len(), 5);
        assert!(
            matches!(&queued[0], UiCommand::SetText { id, text } if id == "hp" && text == "42/100")
        );
        assert!(
            matches!(&queued[1], UiCommand::SetValue { value, .. } if (*value - 0.42).abs() < 1e-6)
        );
        assert!(matches!(&queued[2], UiCommand::AddClass { class, .. } if class == "danger"));
        assert!(matches!(&queued[3], UiCommand::RemoveClass { .. }));
        assert!(matches!(&queued[4], UiCommand::SetStyle { .. }));
    }

    #[test]
    fn test_scripts_read_bindings_and_clicks() {
        let (lua, state) = host();
        {
            let mut data = state.view.data.lock().unwrap();
            data.health = 30.0;
            data.health_max = 100.0;
            data.clock = "12:00".into();
        }
        state.view.clicks.lock().unwrap().push("btn-save".into());
        let clock: String = lua.load(r#"return viber.ui.get("clock")"#).eval().unwrap();
        assert_eq!(clock, "12:00");
        let health: f32 = lua
            .load(r#"return viber.ui.number("health")"#)
            .eval()
            .unwrap();
        assert!((health - 0.3).abs() < 1e-6);
        let clicked: bool = lua
            .load(r#"return viber.ui.clicked("btn-save")"#)
            .eval()
            .unwrap();
        assert!(clicked);
        let other: bool = lua
            .load(r#"return viber.ui.clicked("btn-load")"#)
            .eval()
            .unwrap();
        assert!(!other);
    }

    #[test]
    fn test_unknown_binding_reads_as_empty_not_an_error() {
        let (lua, _state) = host();
        let text: String = lua.load(r#"return viber.ui.get("nope")"#).eval().unwrap();
        assert!(text.is_empty());
        let value: f32 = lua
            .load(r#"return viber.ui.number("nope")"#)
            .eval()
            .unwrap();
        assert_eq!(value, 0.0);
    }
}
