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

use bevy::ecs::system::SystemParam;
use bevy::prelude::*;
use mlua::{Lua, Table, Value};

use super::bind::UiData;
use super::events::{UiEvent, UiEvents};
use super::list::ListRow;
use super::runtime::{
    UiBar, UiClasses, UiClicks, UiCooldown, UiComputed, UiDisabled, UiInlineStyle, UiRegistry,
    UiStyleDirty, UiTag,
};
use super::style::{StyleState, parse_declarations};
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
        /// `true` = SUBSTITUI o inline inteiro (em vez de merge). O par do
        /// `viber.ui.set_style(id, decls, true)`.
        replace: bool,
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
    /// Remove um elemento (e a subárvore) da UI em runtime.
    Destroy {
        id: String,
    },
    /// Tween explícito (`viber.ui.tween`) sobre o motor de motion.
    Tween {
        id: String,
        field: super::tween::StyleField,
        from: Option<super::tween::TweenValue>,
        to: super::tween::TweenValue,
        duration: f32,
        easing: super::tween::Easing,
        delay: f32,
    },
    /// Remove o estilo inline do elemento (`viber.ui.clear_style`).
    ClearStyle {
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

/// Fila de CRIAÇÃO de elementos (`viber.ui.create`) — separada dos comandos
/// porque só um sistema EXCLUSIVO pode construir a subárvore (o construtor
/// da árvore declarativa lê recursos do `World` e spawna diretamente).
#[derive(Clone, Default, Resource)]
pub struct UiCreateQueue(pub Arc<Mutex<Vec<UiCreateSpec>>>);

/// Um pedido de criação: um `XmlNode` sintético (mesmo caminho do XML!) e o
/// id do pai onde entra (None = topo da cena UI).
#[derive(Debug, Clone)]
pub struct UiCreateSpec {
    pub node: crate::xml::XmlNode,
    pub parent: Option<String>,
}

/// Fila de binds de script (`viber.ui.set`) — escrita no resource
/// [`UiScriptBinds`] pelo apply, leitura pelos class-binds e por
/// `get`/`number`.
#[derive(Clone, Default, Resource)]
pub struct UiBindQueue(pub Arc<Mutex<Vec<(String, ScriptBindValue)>>>);

impl UiBindQueue {
    fn push(&self, pair: (String, ScriptBindValue)) {
        if let Ok(mut queue) = self.0.lock() {
            queue.push(pair);
        }
    }
}

/// Um valor de bind vindo de Lua — guarda as três faces que os consumidores
/// leem (texto para `bind=`/`get`, número para barras/`number`, flag para
/// visibilidade/class-binds).
#[derive(Debug, Clone)]
pub enum ScriptBindValue {
    Text(String),
    Number(f64),
    Flag(bool),
}

impl ScriptBindValue {
    pub fn text(&self) -> String {
        match self {
            Self::Text(t) => t.clone(),
            Self::Number(n) => format!("{n}"),
            Self::Flag(true) => "1".to_string(),
            Self::Flag(false) => String::new(),
        }
    }

    pub fn number(&self) -> f64 {
        match self {
            Self::Text(t) => t.parse().unwrap_or(0.0),
            Self::Number(n) => *n,
            Self::Flag(flag) => {
                if *flag {
                    1.0
                } else {
                    0.0
                }
            }
        }
    }

    pub fn flag(&self) -> bool {
        match self {
            Self::Text(t) => !t.is_empty() && t != "0",
            Self::Number(n) => *n != 0.0,
            Self::Flag(flag) => *flag,
        }
    }
}

/// Binds definidos por script — consultados DEPOIS do [`UiData`] (os nomes da
/// engine vencem; colisão = warn 1× e recusa no `viber.ui.set`).
#[derive(Debug, Default, Resource)]
pub struct UiScriptBinds(pub HashMap<String, ScriptBindValue>);

impl UiScriptBinds {
    /// Resolve um nome no mapa de script.
    pub fn get(&self, name: &str) -> Option<&ScriptBindValue> {
        self.0.get(name)
    }
}

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

impl UiCreateQueue {
    fn push(&self, spec: UiCreateSpec) {
        if let Ok(mut queue) = self.0.lock() {
            queue.push(spec);
        }
    }

    /// Takes every create request queued so far.
    pub fn drain_creates(&self) -> Vec<UiCreateSpec> {
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
    /// Eventos acumulados desde a última drenagem (`viber.ui.events()`).
    pub ui_events: Arc<Mutex<Vec<UiEvent>>>,
    /// Estrutura (tag/classes/pai/filhos) por id — para `query`/`classes`/
    /// `children`/`parent`.
    pub structure: Arc<Mutex<HashMap<String, UiElementNode>>>,
    /// Binds de script publicados (`viber.ui.set`) — leitura de fallback.
    pub script_binds: Arc<Mutex<HashMap<String, ScriptBindValue>>>,
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
    /// Retângulo computado no espaço autoral: `[x, y, w, h]` (pós-layout;
    /// 1 frame de latência como o resto da publicação).
    pub rect: [f32; 4],
    /// O ponteiro está sobre o elemento ( alimenta `:hover` no `query`).
    pub hovered: bool,
}

/// A estrutura de um elemento endereçável — tag, classes, pai e filhos por
/// id. Reconstruída SÓ quando o registry muda (build + create/destroy); as
/// classes actualizam-se por elemento quando `UiClasses` muda.
#[derive(Debug, Clone, Default)]
pub struct UiElementNode {
    pub tag: String,
    pub classes: Vec<String>,
    pub parent: Option<String>,
    pub children: Vec<String>,
}

/// Resource wrapper so the snapshot can be refreshed from a system.
#[derive(Clone, Default, Resource)]
pub struct UiScriptState {
    pub view: UiScriptView,
    pub queue: UiCommandQueue,
    /// Criações de elementos (`viber.ui.create`) — consumidas pelo sistema
    /// exclusivo `apply_ui_creates`.
    pub creates: UiCreateQueue,
    /// Binds de script (`viber.ui.set`).
    pub binds: UiBindQueue,
    /// Contador para ids gerados (`ui-gen-N`) de elementos sem `id`.
    pub generated_ids: Arc<std::sync::atomic::AtomicU64>,
    /// True once the `viber.ui` table exists on the host.
    pub installed: bool,
}

/// Traduz uma tabela Lua (`{tag=…, class=…, text=…, children={…}}`) num
/// `XmlNode` — recursivo: a chave `children` cria a SUBÁRVORE inteira numa
/// só chamada de `viber.ui.create`. Ids em falta geram `ui-gen-N` (cada nível
/// fica endereçável).
fn lua_table_to_node(spec: &Table, generated: &std::sync::atomic::AtomicU64) -> mlua::Result<XmlNode> {
    let tag: String = spec.get("tag")?;
    let mut attrs: Vec<(String, String)> = Vec::new();
    let mut children: Vec<XmlNode> = Vec::new();
    for pair in spec.pairs::<String, Value>() {
        let (key, value) = pair?;
        let key = key.to_ascii_lowercase();
        match key.as_str() {
            "tag" | "parent" => continue,
            "children" => {
                if let Value::Table(rows) = value {
                    for entry in rows.sequence_values::<Table>() {
                        children.push(lua_table_to_node(&entry?, generated)?);
                    }
                }
            }
            _ => {
                let text = match value {
                    Value::String(text) => text.to_string_lossy().to_string(),
                    Value::Integer(n) => n.to_string(),
                    Value::Number(n) => format!("{n}"),
                    Value::Boolean(true) => "1".to_string(),
                    _ => continue,
                };
                attrs.push((key, text));
            }
        }
    }
    if !attrs.iter().any(|(k, _)| k == "id") {
        let generated_id = format!(
            "ui-gen-{}",
            generated.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        );
        attrs.push(("id".into(), generated_id));
    }
    Ok(XmlNode {
        tag: tag.to_ascii_lowercase(),
        attrs,
        text: String::new(),
        children,
    })
}

use crate::xml::XmlNode;

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
        UiCommand::SetStyle {
            id,
            declarations,
            replace: false,
        }
    });
    command!("clear_style", (String, ()), |(id, ())| UiCommand::ClearStyle {
        id
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

    // viber.ui.get(name) -> string — the formatted binding value (engine
    // primeiro; senão um bind de script de `viber.ui.set`).
    {
        let view = state.view.clone();
        ui.set(
            "get",
            lua.create_function(move |_, name: String| {
                let mut text = view
                    .data
                    .lock()
                    .ok()
                    .and_then(|data| data.get(&name))
                    .map(|value| value.text);
                if text.is_none()
                    && let Ok(binds) = view.script_binds.lock()
                    && let Some(value) = binds.get(&name)
                {
                    text = Some(value.text());
                }
                Ok(text.unwrap_or_default())
            })?,
        )?;
    }
    // viber.ui.number(name) -> number — the 0..1 fraction (or raw count).
    {
        let view = state.view.clone();
        ui.set(
            "number",
            lua.create_function(move |_, name: String| {
                let mut value = view
                    .data
                    .lock()
                    .ok()
                    .and_then(|data| data.get(&name))
                    .map(|value| value.fraction);
                if value.is_none()
                    && let Ok(binds) = view.script_binds.lock()
                    && let Some(bind) = binds.get(&name)
                {
                    value = Some(bind.number() as f32);
                }
                Ok(value.unwrap_or(0.0))
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

    // ── UI v2: eventos, criação/remoção, tween, leitura, binds ──────────

    // viber.ui.events([prefixo]) -> {{type="click", id="btn-save"}, …}
    //
    // DRENA a fila (os eventos voltam UMA vez) — o mesmo contrato one-shot do
    // `clicked`, mas sem poder perder um evento por polling a menos. Com
    // prefixo, só o que casa sai da fila — o resto fica para outro script
    // (cada prefixo é um canal; sem prefixo, drena tudo).
    {
        let view = state.view.clone();
        ui.set(
            "events",
            lua.create_function(move |lua, prefix: Option<String>| {
                let drained = view
                    .ui_events
                    .lock()
                    .map(|mut queue| std::mem::take(&mut *queue))
                    .unwrap_or_default();
                // Com prefixo, só o que casa sai da fila — o RESTO VOLTA para
                // o outro script o reclamar (dois scripts com prefixos
                // diferentes convivem; sem prefixo, drena tudo).
                let (matched, kept): (Vec<UiEvent>, Vec<UiEvent>) = match &prefix {
                    Some(p) if !p.is_empty() => drained
                        .into_iter()
                        .partition(|event| event.id().starts_with(p.as_str())),
                    _ => (drained, Vec::new()),
                };
                if !kept.is_empty()
                    && let Ok(mut queue) = view.ui_events.lock()
                {
                    // Devolve à FRENTE (FIFO: os mantidos são os mais antigos).
                    let mut restored = kept;
                    restored.extend(queue.drain(..));
                    *queue = restored;
                }
                let table = lua.create_table()?;
                let mut index = 0usize;
                for event in matched {
                    index += 1;
                    let entry = lua.create_table()?;
                    entry.set("type", event.kind())?;
                    match &event {
                        UiEvent::Click { id } => {
                            entry.set("id", id.clone())?;
                        }
                        UiEvent::ValueChanged { id, value } => {
                            entry.set("id", id.clone())?;
                            entry.set("value", *value)?;
                        }
                        UiEvent::TextChanged { id, text } => {
                            entry.set("id", id.clone())?;
                            entry.set("text", text.clone())?;
                        }
                        UiEvent::CheckedChanged { id, checked } => {
                            entry.set("id", id.clone())?;
                            entry.set("checked", *checked)?;
                        }
                        UiEvent::FocusChanged { id, focused } => {
                            entry.set("id", id.clone())?;
                            entry.set("focused", *focused)?;
                        }
                        UiEvent::TabChanged { group, tab } => {
                            entry.set("group", group.clone())?;
                            entry.set("tab", tab.clone())?;
                        }
                        UiEvent::TweenDone { id, property } => {
                            entry.set("id", id.clone())?;
                            entry.set("property", property.clone())?;
                        }
                        UiEvent::HoverEnter { id } | UiEvent::HoverLeave { id } => {
                            entry.set("id", id.clone())?;
                        }
                    }
                    table.set(index, entry)?;
                }
                table.set("n", index)?;
                Ok(table)
            })?,
        )?;
    }

    // viber.ui.create{ tag="uibutton", id=…, parent=…, class=…, text=…,
    //                  style=…, … } -> id
    //
    // Constrói um XmlNode sintético e passa pelo MESMO construtor da árvore
    // declarativa (aplicado num sistema exclusivo por causa do World). Sem
    // `id`, um único é gerado. Devolve o id — os comandos seguintes (set_text,
    // add_class, …) funcionam nele NO MESMO frame (o registry já o tem), mas
    // o elemento só APARECE no frame seguinte, como qualquer comando em fila.
    {
        let queue = state.creates.clone();
        let next_generated = state.generated_ids.clone();
        ui.set(
            "create",
            lua.create_function(move |_, spec: Table| -> mlua::Result<String> {
                let node = lua_table_to_node(&spec, &next_generated)?;
                let id = node
                    .attrs
                    .iter()
                    .find(|(k, _)| k == "id")
                    .map(|(_, v)| v.clone())
                    .unwrap_or_default();
                let parent = spec.get::<String>("parent").ok();
                queue.push(UiCreateSpec { node, parent });
                Ok(id)
            })?,
        )?;
    }

    // viber.ui.destroy(id) — remove o elemento e a subárvore (registry incluído).
    command!("destroy", (String, ()), |(id, ())| UiCommand::Destroy { id });

    // viber.ui.tween(id, {property="opacity", to=1, from=0, duration=0.3,
    //                     easing="ease-out", delay=0})
    //
    // Tween explícito sobre o motor das transitions; termina com um evento
    // `tween_done` na fila (`viber.ui.events`). `to` aceita número OU cor
    // ("#ff0000"/"red") conforme o campo.
    {
        let queue = state.queue.clone();
        ui.set(
            "tween",
            lua.create_function(
                move |lua, (id, spec): (String, Table)| -> mlua::Result<()> {
                    let tween_value = |lua: &Lua, value: mlua::Value| -> Option<super::tween::TweenValue> {
                        match value {
                            Value::Number(n) => {
                                Some(super::tween::TweenValue::Num(super::tween::NumUnit::Px, n as f32))
                            }
                            Value::Integer(n) => Some(super::tween::TweenValue::Num(
                                super::tween::NumUnit::Px,
                                n as f32,
                            )),
                            Value::String(text) => {
                                let text = text.to_string_lossy();
                                super::style::parse_color(text.trim())
                                    .map(super::tween::TweenValue::Color)
                            }
                            Value::Table(table) => {
                                // aceita também {r,g,b} / {r,g,b,a} 0..1
                                let channel = |key: &str| -> Option<f32> {
                                    table.get::<f64>(key).ok().map(|v| v as f32)
                                };
                                let (r, g, b) = (channel("r")?, channel("g")?, channel("b")?);
                                let a = channel("a").unwrap_or(1.0);
                                Some(super::tween::TweenValue::Color(
                                    bevy::color::Color::srgba(r, g, b, a),
                                ))
                            }
                            _ => {
                                let _ = lua;
                                None
                            }
                        }
                    };
                    let property: String = spec.get("property")?;
                    let Some(field) = super::tween::StyleField::parse(&property) else {
                        warn!("ui: tween `{property}` não é um campo animável — pedido ignorado");
                        return Ok(());
                    };
                    let Some(to) = tween_value(lua, spec.get("to")?) else {
                        warn!("ui: tween sem `to` legível — pedido ignorado");
                        return Ok(());
                    };
                    let from = match spec.get::<mlua::Value>("from") {
                        Ok(value) => tween_value(lua, value),
                        Err(_) => None,
                    };
                    let duration = spec.get::<f64>("duration").unwrap_or(0.3) as f32;
                    let easing = spec
                        .get::<String>("easing")
                        .ok()
                        .and_then(|text| super::tween::Easing::parse(&text))
                        .unwrap_or(super::tween::Easing::EaseOut);
                    let delay = spec.get::<f64>("delay").unwrap_or(0.0) as f32;
                    queue.push(UiCommand::Tween {
                        id,
                        field,
                        from,
                        to,
                        duration,
                        easing,
                        delay,
                    });
                    Ok(())
                },
            )?,
        )?;
    }

    // viber.ui.set("nome", valor) — bind de script: alimenta class-binds
    // (`bind="nome:classe"`), `bind="nome"`, `get`/`number`. Os nomes da
    // engine têm prioridade (colisão = warn e recusa).
    {
        let queue = state.binds.clone();
        ui.set(
            "set",
            lua.create_function(move |_, (name, value): (String, mlua::Value)| {
                let value = match value {
                    Value::Boolean(flag) => ScriptBindValue::Flag(flag),
                    Value::Integer(n) => ScriptBindValue::Number(n as f64),
                    Value::Number(n) => ScriptBindValue::Number(n),
                    Value::String(text) => ScriptBindValue::Text(text.to_string_lossy().to_string()),
                    other => {
                        warn!("ui: viber.ui.set só aceita texto/número/booleano (recebeu {other:?})");
                        return Ok(());
                    }
                };
                queue.push((name, value));
                Ok(())
            })?,
        )?;
    }

    // viber.ui.rect(id) -> {x=…, y=…, w=…, h=…} | nil — pós-layout, espaço
    // autoral (píxeis do CSS).
    {
        let view = state.view.clone();
        ui.set(
            "rect",
            lua.create_function(move |lua, id: String| {
                let rect = view
                    .elements
                    .lock()
                    .ok()
                    .and_then(|elements| elements.get(&id).map(|element| element.rect));
                match rect {
                    Some([x, y, w, h]) => {
                        let table = lua.create_table()?;
                        table.set("x", x)?;
                        table.set("y", y)?;
                        table.set("w", w)?;
                        table.set("h", h)?;
                        Ok(mlua::Value::Table(table))
                    }
                    None => Ok(mlua::Value::Nil),
                }
            })?,
        )?;
    }

    // viber.ui.classes(id) -> {"chip", "gold"}
    {
        let view = state.view.clone();
        ui.set(
            "classes",
            lua.create_function(move |lua, id: String| {
                let table = lua.create_table()?;
                if let Ok(structure) = view.structure.lock() {
                    if let Some(node) = structure.get(&id) {
                        for (index, class) in node.classes.iter().enumerate() {
                            table.set(index + 1, class.as_str())?;
                        }
                    }
                }
                Ok(table)
            })?,
        )?;
    }

    // viber.ui.children(id) -> {…ids…}; viber.ui.parent(id) -> string | nil
    {
        let view = state.view.clone();
        ui.set(
            "children",
            lua.create_function(move |lua, id: String| {
                let table = lua.create_table()?;
                if let Ok(structure) = view.structure.lock() {
                    if let Some(node) = structure.get(&id) {
                        for (index, child) in node.children.iter().enumerate() {
                            table.set(index + 1, child.as_str())?;
                        }
                    }
                }
                Ok(table)
            })?,
        )?;
    }
    {
        let view = state.view.clone();
        ui.set(
            "parent",
            lua.create_function(move |_, id: String| {
                Ok(view
                    .structure
                    .lock()
                    .ok()
                    .and_then(|structure| structure.get(&id).and_then(|node| node.parent.clone())))
            })?,
        )?;
    }

    // viber.ui.query(seletor) -> {…ids…} — seletores do dialeto (tag, .classe,
    // #id, descendente, `>`, pseudos) sobre a estrutura publicada. Regras
    // @media avaliam como se a janela cabesse (o query não conhece a janela).
    {
        let view = state.view.clone();
        ui.set(
            "query",
            lua.create_function(move |lua, selector_text: String| {
                let table = lua.create_table()?;
                let Some(selector) = super::style::parse_selector(&selector_text) else {
                    warn!("ui: query com seletor ilegível `{selector_text}`");
                    return Ok(table);
                };
                let structure = view.structure.lock().ok();
                let Some(structure) = structure else {
                    return Ok(table);
                };
                // Chain por id, raiz primeiro — igual à resolução da engine.
                fn chain_of<'a>(
                    id: &str,
                    structure: &'a HashMap<String, UiElementNode>,
                ) -> Option<Vec<&'a UiElementNode>> {
                    let mut chain = Vec::new();
                    let mut current = structure.get(id)?;
                    chain.push(current);
                    while let Some(parent) = current.parent.as_deref() {
                        current = structure.get(parent)?;
                        chain.push(current);
                        if chain.len() > 32 {
                            return None;
                        }
                    }
                    chain.reverse();
                    Some(chain)
                }
                let elements = view.elements.lock().ok();
                let mut index = 0usize;
                for (id, node) in structure.iter() {
                    let Some(chain) = chain_of(id, &structure) else {
                        continue;
                    };
                    // `:hover` no query lê o estado publicado (a estrutura não
                    // tem Interaction).
                    let hovered = elements
                        .as_ref()
                        .and_then(|elements| elements.get(id))
                        .is_some_and(|element| element.hovered);
                    let state = if hovered {
                        StyleState::Hover
                    } else {
                        StyleState::Normal
                    };
                    let sibling = node.parent.as_deref().and_then(|parent| {
                        structure
                            .get(parent)
                            .and_then(|parent| parent.children.iter().position(|kid| kid == id))
                    });
                    let last = chain.len().saturating_sub(1);
                    let refs: Vec<super::style::ElementRef<'_>> = chain
                        .iter()
                        .enumerate()
                        .map(|(depth, node)| super::style::ElementRef {
                            tag: &node.tag,
                            id: None,
                            classes: &node.classes,
                            // `:hover` só vale para o PRÓPRIO elemento (o
                            // último da chain); os ancestrais ficam neutros.
                            state: if depth == last {
                                state
                            } else {
                                StyleState::Normal
                            },
                            focused: node.classes.iter().any(|c| c == "focused"),
                            checked: node.classes.iter().any(|c| c == "checked"),
                            empty: node.children.is_empty(),
                            sibling_index: sibling.unwrap_or(0),
                            sibling_count: node.parent.as_deref().and_then(|parent| {
                                structure
                                    .get(parent)
                                    .map(|parent| parent.children.len())
                            }).unwrap_or(1),
                        })
                        .collect();
                    if selector.matches(&refs) {
                        index += 1;
                        table.set(index, id.as_str()).ok();
                    }
                }
                Ok(table)
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

/// Queries extra do apply — num SystemParam para não estourar o tecto de 16
/// parâmetros de sistema do Bevy.
#[derive(SystemParam)]
pub struct UiCommandInfo<'w, 's> {
    /// Subárvores (destroy/remove de ids).
    pub children: Query<'w, 's, &'static Children>,
    /// Pais (dirty ao destruir — um descendente pode deixar de casar regra).
    pub parents: Query<'w, 's, &'static ChildOf>,
    /// Estado de transição (tweens explícitos).
    pub tweens: Query<'w, 's, &'static mut super::tween::UiTransitions>,
    /// Estilo computado (alvo inicial do tween).
    pub computed: Query<'w, 's, &'static UiComputed>,
}

/// Applies the queued script mutations to the live UI.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn apply_ui_commands(
    mut commands: Commands,
    state: Res<UiScriptState>,
    mut registry: ResMut<UiRegistry>,
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
    mut info: UiCommandInfo,
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
            | UiCommand::Focus { id }
            | UiCommand::Destroy { id }
            | UiCommand::Tween { id, .. }
            | UiCommand::ClearStyle { id } => id,
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
            UiCommand::SetStyle {
                declarations,
                replace,
                ..
            } => {
                let props = parse_declarations(&declarations, "viber.ui.set_style");
                if replace {
                    // Substituição completa: o inline anterior sai inteiro
                    // (o merge nunca "desfaz" uma declaração; isto sim).
                    commands.entity(entity).insert(UiInlineStyle(props));
                } else if let Ok(mut inline) = inline.get_mut(entity) {
                    inline.0.merge(&props);
                } else {
                    commands.entity(entity).insert(UiInlineStyle(props));
                }
                commands.entity(entity).insert(UiStyleDirty);
            }
            UiCommand::ClearStyle { .. } => {
                // Sem inline, o cascade volta a mandar — o "unset" que o
                // merge não consegue.
                commands.entity(entity).remove::<UiInlineStyle>();
                commands.entity(entity).insert(UiStyleDirty);
            }
            UiCommand::Destroy { .. } => {
                // Recolhe a subárvore para tirar TODOS os ids do registry
                // (os sub-ids `x.fill`/`x.text` morrem com o pai).
                let mut stack = vec![entity];
                let mut doomed = Vec::new();
                while let Some(next) = stack.pop() {
                    doomed.push(next);
                    if let Ok(kids) = info.children.get(next) {
                        for kid in kids.iter() {
                            stack.push(kid);
                        }
                    }
                }
                registry
                    .by_id
                    .retain(|_, registered| !doomed.contains(registered));
                if let Ok(parent) = info.parents.get(entity) {
                    commands.entity(parent.parent()).insert(UiStyleDirty);
                }
                commands.entity(entity).despawn();
            }
            UiCommand::Tween {
                id,
                field,
                from,
                to,
                duration,
                easing,
                delay,
            } => {
                if let Ok(mut transitions) = info.tweens.get_mut(entity) {
                    // Regista o alvo corrente para o diff da cascata não
                    // "re-saltar" o campo no próximo re-estilo.
                    if !transitions.targets.contains_key(&field) {
                        if let Some(current) = info
                            .computed
                            .get(entity)
                            .ok()
                            .and_then(|computed| field.get(&computed.0))
                        {
                            transitions.targets.insert(field, current);
                        }
                    }
                    if transitions.start(field, to, easing, duration, delay, true) {
                        transitions.label = id.clone();
                        // O `from` explícito vence o valor exibido.
                        if let Some(from) = from {
                            if let Some(run) = transitions.tweens.last_mut() {
                                run.from = from;
                            }
                        }
                    }
                } else {
                    // Sem `UiTransitions` (nenhuma `transition` na cascata):
                    // cria e volta a enfileirar — o componente existe no
                    // frame seguinte (commands) e o tween arranca lá.
                    let mut created = super::tween::UiTransitions::default();
                    created.label = id.clone();
                    commands.entity(entity).insert(created);
                    state.queue.push(UiCommand::Tween {
                        id: id.clone(),
                        field,
                        from,
                        to,
                        duration,
                        easing,
                        delay,
                    });
                }
            }
        }
    }
}

/// Aplica as criações de `viber.ui.create` — sistema EXCLUSIVO porque o
/// construtor da árvore declarativa lê recursos (fonte, AssetServer) e spawna
/// diretamente no `World`. O mesmo construtor do XML: um `XmlNode` sintético
/// com os attrs da tabela Lua.
pub fn apply_ui_creates(world: &mut World) {
    // A fila vive no `UiScriptState` — o MESMO Arc que as closures Lua
    // clonaram (o recurso standalone `UiCreateQueue` nunca recebe nada).
    let pending = world.resource::<UiScriptState>().creates.drain_creates();
    if pending.is_empty() {
        return;
    }
    for spec in pending {
        let parent = spec
            .parent
            .as_deref()
            .and_then(|id| world.resource::<UiRegistry>().get(id));
        if spec.parent.is_some() && parent.is_none() {
            warn!(
                "ui: create com parent `{}` inexistente — elemento no topo da cena UI",
                spec.parent.as_deref().unwrap_or_default()
            );
        }
        let font = crate::hud::HudAssets::get(world).font.clone();
        let assets = world.resource::<AssetServer>().clone();
        let Some(entity) = super::tree::build_ui_tree(world, &spec.node, parent, &font, &assets)
        else {
            continue;
        };
        // Um elemento criado fica no FIM dos irmãos (o Bevy não insere no
        // meio do `Children`); dá-lhe o `UiOrder` de último para a navegação
        // por teclado/tabs o lerem depois dos autorados.
        let order = parent
            .and_then(|parent| world.get::<Children>(parent))
            .map(|kids| kids.len().saturating_sub(1))
            .unwrap_or(0);
        world.entity_mut(entity).insert(super::runtime::UiOrder(order));
        // Novos descendentes podem casar regras `descendant` — o pai re-estila.
        if let Some(parent) = parent {
            world.entity_mut(parent).insert(UiStyleDirty);
        }
    }
}

/// Aplica os binds de script (`viber.ui.set`) no recurso [`UiScriptBinds`] —
/// nomes da engine têm prioridade (colisão = warn 1× e recusa).
pub fn apply_ui_script_binds(
    state: Res<UiScriptState>,
    data: Res<UiData>,
    mut binds: ResMut<UiScriptBinds>,
) {
    let Ok(mut queue) = state.binds.0.lock() else {
        return;
    };
    let pending = std::mem::take(&mut *queue);
    drop(queue);
    for (name, value) in pending {
        if data.get(&name).is_some() {
            warn!("ui: viber.ui.set(`{name}`) colide com um bind da engine — recusado");
            continue;
        }
        binds.0.insert(name, value);
    }
}

/// Queries do publish — agrupadas num SystemParam (tecto de 16 parâmetros).
#[derive(SystemParam)]
pub struct UiPublishInfo<'w, 's> {
    pub texts: Query<'w, 's, &'static Text>,
    pub widgets: Query<
        'w,
        's,
        AnyOf<(
            &'static UiBar,
            &'static UiCooldown,
            &'static super::widgets::UiSlider,
            &'static UiCheck,
            &'static UiInput,
        )>,
    >,
    pub visibility: Query<'w, 's, &'static Visibility>,
    pub disabled: Query<'w, 's, Has<UiDisabled>>,
    /// Geometria computada — rect pós-layout no espaço autoral.
    pub computed_nodes: Query<'w, 's, &'static ComputedNode>,
    /// Posição global do nó (o `UiGlobalTransform` acompanha o `UiTransform`).
    pub transforms: Query<'w, 's, &'static UiGlobalTransform>,
    pub tags: Query<'w, 's, &'static UiTag>,
    pub classes: Query<'w, 's, &'static UiClasses>,
    pub children: Query<'w, 's, &'static Children>,
    pub parents: Query<'w, 's, &'static ChildOf>,
    /// Quem trocou de classes neste frame (o mapa de estrutura actualiza só
    /// essas entradas, sem clones por frame).
    pub classes_changed: Query<
        'w,
        's,
        (Entity, &'static UiClasses),
        bevy::ecs::query::Changed<UiClasses>,
    >,
    /// Estado de hover para `read().hovered` e `:hover` no `query`.
    pub interactions: Query<'w, 's, &'static Interaction>,
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
    mut events: ResMut<UiEvents>,
    binds: Res<UiScriptBinds>,
    scale: Res<UiScale>,
    info: UiPublishInfo,
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
            let (bar, cooldown, slider, check, input) = info
                .widgets
                .get(entity)
                .unwrap_or((None, None, None, None, None));
            // Empréstimos, não clones: a `String` só nasce na inserção de um
            // elemento novo.
            let text: &str = input
                .map(|input| input.text.as_str())
                .or_else(|| info.texts.get(entity).map(|text| text.0.as_str()).ok())
                .unwrap_or_default();
            let value = slider
                .map(|slider| slider.value)
                .or_else(|| bar.map(|bar| bar.value))
                .or_else(|| cooldown.map(|cd| cd.value))
                .unwrap_or(0.0);
            let visible = info
                .visibility
                .get(entity)
                .is_ok_and(|v| *v != Visibility::Hidden);
            let checked = check.is_some_and(|check| check.checked);
            let disabled = info.disabled.get(entity).unwrap_or(false);
            let hovered = info
                .interactions
                .get(entity)
                .is_ok_and(|interaction| {
                    matches!(interaction, Interaction::Hovered | Interaction::Pressed)
                });
            // Rect pós-layout (posição global + tamanho), repartido pela
            // escala para o espaço autoral.
            let rect: [f32; 4] = {
                let size = info
                    .computed_nodes
                    .get(entity)
                    .map(|node| node.size)
                    .unwrap_or(Vec2::ZERO);
                let position = info
                    .transforms
                    .get(entity)
                    .ok()
                    .map(|transform| transform.to_scale_angle_translation().2)
                    .unwrap_or(Vec2::ZERO);
                [
                    position.x / scale.0,
                    position.y / scale.0,
                    size.x / scale.0,
                    size.y / scale.0,
                ]
            };
            if elements.contains_key(id.as_str()) {
                let entry = elements
                    .get_mut(id.as_str())
                    .expect("contains_key acabou de dizer que existe");
                if entry.text != text
                    || entry.value != value
                    || entry.visible != visible
                    || entry.checked != checked
                    || entry.disabled != disabled
                    || entry.rect != rect
                    || entry.hovered != hovered
                {
                    entry.text.clear();
                    entry.text.push_str(text);
                    entry.value = value;
                    entry.visible = visible;
                    entry.checked = checked;
                    entry.disabled = disabled;
                    entry.rect = rect;
                    entry.hovered = hovered;
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
                        rect,
                        hovered,
                    },
                );
            }
        }
    }
    // Estrutura (tag/classes/pai/filhos) para `query`/`classes`/`children`.
    // Reconstrói SÓ quando a estrutura mudou — registry (build/create/destroy)
    // ou alguém trocou classes (class-binds vivem a isso); o passe é de
    // centenas de entradas e não corre em frames parados.
    if registry.is_changed() || info.classes_changed.iter().next().is_some() {
        if let Ok(mut structure) = state.view.structure.lock() {
            structure.retain(|id, _| registry.by_id.contains_key(id.as_str()));
            let reverse: HashMap<Entity, &str> = registry
                .by_id
                .iter()
                .map(|(id, entity)| (*entity, id.as_str()))
                .collect();
            for (id, &entity) in &registry.by_id {
                let Ok(tag) = info.tags.get(entity) else {
                    continue;
                };
                let classes = info
                    .classes
                    .get(entity)
                    .map(|classes| classes.0.clone())
                    .unwrap_or_default();
                let parent = info
                    .parents
                    .get(entity)
                    .ok()
                    .and_then(|parent| reverse.get(&parent.parent()).map(|s| s.to_string()));
                let children: Vec<String> = info
                    .children
                    .get(entity)
                    .map(|kids| {
                        kids.iter()
                            .filter_map(|kid| reverse.get(&kid).map(|s| s.to_string()))
                            .collect()
                    })
                    .unwrap_or_default();
                match structure.get_mut(id.as_str()) {
                    Some(node) => {
                        node.classes = classes;
                        node.parent = parent;
                        node.children = children;
                    }
                    None => {
                        structure.insert(
                            id.clone(),
                            UiElementNode {
                                tag: tag.0.clone(),
                                classes,
                                parent,
                                children,
                            },
                        );
                    }
                }
            }
        }
    }
    // Eventos: a fila do frame MUDOU para a vista — o `events()` em Luau
    // drena a partir daí (um frame de latência, como tudo o que publica).
    if !events.0.is_empty()
        && let Ok(mut queue) = state.view.ui_events.lock()
    {
        queue.extend(events.0.drain(..));
        // Cap: um script que nunca drena não pode fazer a fila crescer sem fim.
        if queue.len() > 512 {
            let excess = queue.len() - 512;
            queue.drain(0..excess);
        }
    }
    // Binds de script publicados (leitura de fallback de get/number).
    if binds.is_changed()
        && let Ok(mut snapshot) = state.view.script_binds.lock()
    {
        snapshot.clone_from(&binds.0);
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
    fn test_lua_table_to_node_builds_addressable_subtree() {
        let lua = Lua::new();
        let table: Table = lua
            .load(
                r#"{ tag = "uipanel", class = "linha",
                     children = {
                       { tag = "uitext", text = "olá" },
                       { tag = "uipanel", class = "inner" },
                     } }"#,
            )
            .eval()
            .unwrap();
        let generated = std::sync::atomic::AtomicU64::default();
        let node = lua_table_to_node(&table, &generated).expect("árvore");
        assert_eq!(node.tag, "uipanel");
        assert!(node.attrs.iter().any(|(k, v)| k == "class" && v == "linha"));
        // ids gerados em TODOS os níveis — cada elemento fica endereçável.
        assert!(node.attrs.iter().any(|(k, v)| k == "id" && v.starts_with("ui-gen-")));
        assert_eq!(node.children.len(), 2);
        assert_eq!(node.children[0].tag, "uitext");
        assert!(node.children[0]
            .attrs
            .iter()
            .any(|(k, v)| k == "text" && v == "olá"));
        assert!(node.children[1].attrs.iter().any(|(k, _)| k == "id"));
    }

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
