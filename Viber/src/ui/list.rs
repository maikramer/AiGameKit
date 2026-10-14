//! Data-driven repeaters: `<UiList>` + `<UiTemplate>`.
//!
//! A menu is mostly *lists* — quests, inventory slots, skills, option rows —
//! and their length is data, not layout. Authoring them one element at a time
//! would put the game's content back into the interface file, which is exactly
//! what the declarative UI exists to avoid.
//!
//! ```xml
//! <UiList id="quests" bind="quests" class="rows">
//!   <UiTemplate>
//!     <UiRow id="quest-{index}" class="row {status}">
//!       <UiText class="title">{title}</UiText>
//!       <UiBar  class="bar" value="{progress}" />
//!       <UiText class="count">{progress_text}</UiText>
//!     </UiRow>
//!   </UiTemplate>
//! </UiList>
//! ```
//!
//! `{field}` placeholders are substituted in **every attribute value and every
//! text node** of the template before it is built, so a row can drive its own
//! id, classes, bar values and labels from one data row. `{index}` is always
//! available.
//!
//! The list is rebuilt only when its source changes (a version counter), not
//! every frame: a 40-slot inventory costs nothing while nobody picks anything
//! up.

use std::collections::{HashMap, HashSet};

use bevy::prelude::*;

use super::runtime::{UiInlineStyle, UiRegistry, UiStyleDirty};
use crate::xml::XmlNode;

/// One row of a list source: named string fields.
///
/// Everything is a string because that is what a template substitutes; numeric
/// consumers (`value="{progress}"`) parse it back on the spot.
pub type ListRow = HashMap<String, String>;

/// Named list sources, fed by the engine or by scripts.
#[derive(Debug, Default, Resource)]
pub struct UiLists {
    lists: HashMap<String, Vec<ListRow>>,
    /// Bumped whenever a list's content actually changes.
    versions: HashMap<String, u64>,
    /// Sources fed by SCRIPTS: the engine must not clobber them (its own
    /// collectors run every frame and would win by stubbornness).
    script_owned: HashSet<String>,
}

impl UiLists {
    /// Replaces a list, bumping its version only if the content differs.
    ///
    /// The equality check is what keeps a rebuild from happening 60 times a
    /// second for a list nobody touched.
    pub fn set(&mut self, name: &str, rows: Vec<ListRow>) {
        if self.lists.get(name).is_some_and(|current| *current == rows) {
            return;
        }
        self.lists.insert(name.to_string(), rows);
        *self.versions.entry(name.to_string()).or_insert(0) += 1;
    }

    /// The engine's feeding path: like [`set`], but a source a script has
    /// claimed via [`set_script`] is left alone.
    pub fn set_engine(&mut self, name: &str, rows: Vec<ListRow>) {
        if self.script_owned.contains(name) {
            return;
        }
        self.set(name, rows);
    }

    /// The script feeding path (`viber.ui.list`): sets the rows AND claims
    /// ownership, so per-frame engine collectors stop overwriting it.
    pub fn set_script(&mut self, name: &str, rows: Vec<ListRow>) {
        self.script_owned.insert(name.to_string());
        self.set(name, rows);
    }

    /// Releases a script claim (`viber.ui.unlist`) — the engine may feed it
    /// again. Returns `true` when a claim was actually held.
    pub fn release_script(&mut self, name: &str) -> bool {
        self.script_owned.remove(name)
    }

    pub fn is_script_owned(&self, name: &str) -> bool {
        self.script_owned.contains(name)
    }

    pub fn rows(&self, name: &str) -> &[ListRow] {
        self.lists.get(name).map(Vec::as_slice).unwrap_or(&[])
    }

    pub fn version(&self, name: &str) -> u64 {
        self.versions.get(name).copied().unwrap_or(0)
    }

    /// All source names that currently exist (script-fed or engine-fed).
    pub fn names(&self) -> Vec<String> {
        self.lists.keys().cloned().collect()
    }

    /// Convenience for building a row from `(field, value)` pairs.
    pub fn row<'a>(fields: impl IntoIterator<Item = (&'a str, String)>) -> ListRow {
        fields
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect()
    }
}

/// A repeater element: rebuilds its children from `source` whenever the list
/// version changes.
#[derive(Debug, Component)]
pub struct UiList {
    /// Name of the list in [`UiLists`].
    pub source: String,
    /// Template subtree, cloned per row.
    pub template: Vec<XmlNode>,
    /// Last version built.
    pub built_version: Option<u64>,
    /// Shown instead of the rows when the list is empty (already a child of
    /// the list, kept out of the rebuild).
    pub empty_label: Option<Entity>,
}

/// Substitutes `{field}` placeholders in `text` from `row`.
///
/// An unknown placeholder resolves to an empty string rather than being left
/// as literal braces: a missing optional field should disappear, not print
/// `{icon}` in the middle of a menu.
pub fn substitute(text: &str, row: &ListRow, index: usize) -> String {
    if !text.contains('{') {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let after = &rest[open + 1..];
        let Some(close) = after.find('}') else {
            // Unbalanced brace: keep it verbatim so the author sees the typo.
            out.push_str(&rest[open..]);
            return out;
        };
        let key = after[..close].trim();
        match key {
            "index" => out.push_str(&index.to_string()),
            "n" => out.push_str(&(index + 1).to_string()),
            other => out.push_str(row.get(other).map(String::as_str).unwrap_or("")),
        }
        rest = &after[close + 1..];
    }
    out.push_str(rest);
    out
}

/// Clones a template node with every attribute value and text substituted.
pub fn instantiate(node: &XmlNode, row: &ListRow, index: usize) -> XmlNode {
    XmlNode {
        tag: node.tag.clone(),
        attrs: node
            .attrs
            .iter()
            .map(|(key, value)| (key.clone(), substitute(value, row, index)))
            .collect(),
        text: substitute(&node.text, row, index),
        children: node
            .children
            .iter()
            .map(|child| instantiate(child, row, index))
            .collect(),
        origin: None,
    }
}

/// Params the rebuild needs before it takes the world exclusively.
pub type RebuildParams = bevy::ecs::system::SystemState<(
    Query<'static, 'static, (Entity, &'static mut UiList)>,
    Res<'static, UiLists>,
    Query<'static, 'static, &'static Children>,
)>;

/// Rebuilds the lists whose source changed.
#[allow(clippy::type_complexity)]
pub fn rebuild_ui_lists(world: &mut World, params: &mut RebuildParams) {
    // Which lists need work, and with what rows — resolved before touching the
    // world, because the rebuild needs exclusive access.
    let mut jobs: Vec<(Entity, u64, Vec<ListRow>, Vec<XmlNode>, Vec<Entity>)> = Vec::new();
    {
        let Ok((mut lists, sources, children)) = params.get_mut(world) else {
            return;
        };
        for (entity, list) in &mut lists {
            let version = sources.version(&list.source);
            if list.built_version == Some(version) {
                continue;
            }
            let stale: Vec<Entity> = children
                .get(entity)
                .map(|kids| {
                    kids.iter()
                        .filter(|child| Some(*child) != list.empty_label)
                        .collect()
                })
                .unwrap_or_default();
            jobs.push((
                entity,
                version,
                sources.rows(&list.source).to_vec(),
                list.template.clone(),
                stale,
            ));
        }
    }
    if jobs.is_empty() {
        return;
    }
    let font = crate::hud::HudAssets::get(world).font.clone();
    let assets = world.resource::<AssetServer>().clone();
    for (entity, version, rows, template, stale) in jobs {
        for child in stale {
            despawn_ui_subtree(world, child);
        }
        for (index, row) in rows.iter().enumerate() {
            for node in &template {
                let instance = instantiate(node, row, index);
                super::tree::build_ui_tree(world, &instance, Some(entity), &font, &assets);
            }
        }
        // The empty-state label is a sibling of the rows, shown only when there
        // are none — the one piece of a list that is not data-driven.
        if let Some(mut list) = world.get_mut::<UiList>(entity) {
            list.built_version = Some(version);
            let empty_label = list.empty_label;
            if let Some(label) = empty_label {
                // `display`, not `Visibility`: a hidden node still takes part
                // in layout, and the reserved line pushed every row of a full
                // list down by the height of a message nobody could see.
                // INLINE, não directo no `Node`: o re-estilo recalcula o nó do
                // zero (semântica CSS) e o inline é o que sobrevive a isso.
                let display = if rows.is_empty() {
                    Display::Flex
                } else {
                    Display::None
                };
                if let Ok(mut label_entity) = world.get_entity_mut(label) {
                    label_entity.insert(UiInlineStyle(super::style::StyleProps {
                        display: Some(display),
                        ..Default::default()
                    }));
                    label_entity.insert(UiStyleDirty);
                }
            }
        }
        if let Ok(mut entity) = world.get_entity_mut(entity) {
            entity.insert(UiStyleDirty);
        }
    }
    // The cached `SystemState` holds a command queue; without this the engine
    // logs "CommandQueue has un-applied commands being dropped" every rebuild.
    params.apply(world);
}

/// Despawns an element and its descendants, dropping their registry ids.
fn despawn_ui_subtree(world: &mut World, root: Entity) {
    let mut stack = vec![root];
    let mut doomed = Vec::new();
    while let Some(entity) = stack.pop() {
        doomed.push(entity);
        if let Some(children) = world.get::<Children>(entity) {
            stack.extend(children.iter());
        }
    }
    let ids: Vec<String> = doomed
        .iter()
        .filter_map(|e| world.get::<super::runtime::UiId>(*e).map(|id| id.0.clone()))
        .collect();
    if !ids.is_empty() {
        let mut registry = world.resource_mut::<UiRegistry>();
        for id in ids {
            registry.by_id.remove(&id);
        }
    }
    if let Ok(entity) = world.get_entity_mut(root) {
        entity.despawn();
    }
}

/// `rebuild_ui_lists` as an exclusive system with cached state.
pub fn rebuild_ui_lists_system(world: &mut World, params: &mut RebuildParams) {
    // Cheap early-out on the common frame where nothing changed.
    let dirty = {
        let Ok((mut lists, sources, _)) = params.get_mut(world) else {
            return;
        };
        lists
            .iter_mut()
            .any(|(_, list)| list.built_version != Some(sources.version(&list.source)))
    };
    if !dirty {
        return;
    }
    rebuild_ui_lists(world, params);
}

/// True when `tag` is the template marker inside a `<UiList>`.
pub fn is_template_tag(tag: &str) -> bool {
    tag.eq_ignore_ascii_case("uitemplate")
}

/// True when `tag` is the empty-state marker inside a `<UiList>`.
pub fn is_empty_tag(tag: &str) -> bool {
    tag.eq_ignore_ascii_case("uiempty")
}

/// Marker so `UiTag` selectors can still reach the list itself.
pub const LIST_TAG: &str = "uilist";

#[cfg(test)]
mod tests {
    use super::*;

    fn row(pairs: &[(&str, &str)]) -> ListRow {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn test_substitute_fills_fields_and_the_index() {
        let row = row(&[("title", "Lobos"), ("progress", "0.4")]);
        assert_eq!(substitute("{title}", &row, 0), "Lobos");
        assert_eq!(substitute("quest-{index}", &row, 3), "quest-3");
        assert_eq!(substitute("{n}. {title}", &row, 0), "1. Lobos");
        assert_eq!(
            substitute("row {title} ({progress})", &row, 0),
            "row Lobos (0.4)"
        );
        // No braces: returned untouched, and cheaply.
        assert_eq!(substitute("plain", &row, 0), "plain");
    }

    #[test]
    fn test_substitute_drops_unknown_fields_and_keeps_typos_visible() {
        let row = row(&[("title", "Lobos")]);
        // A missing optional field disappears rather than printing braces.
        assert_eq!(substitute("[{icon}]{title}", &row, 0), "[]Lobos");
        // An unbalanced brace stays verbatim so the author notices.
        assert_eq!(substitute("{title", &row, 0), "{title");
    }

    #[test]
    fn test_instantiate_substitutes_attributes_and_text_recursively() {
        let template = XmlNode {
            tag: "UiRow".into(),
            attrs: vec![
                ("id".into(), "quest-{index}".into()),
                ("class".into(), "row {status}".into()),
            ],
            text: String::new(),
            children: vec![XmlNode {
                tag: "UiText".into(),
                attrs: vec![],
                text: "{title}".into(),
                children: vec![],
                origin: None,
            }],
            origin: None,
        };
        let row = row(&[("title", "Lobos"), ("status", "ready")]);
        let built = instantiate(&template, &row, 2);
        assert_eq!(built.attrs[0].1, "quest-2");
        assert_eq!(built.attrs[1].1, "row ready");
        assert_eq!(built.children[0].text, "Lobos");
    }

    #[test]
    fn test_set_bumps_the_version_only_on_a_real_change() {
        let mut lists = UiLists::default();
        assert_eq!(lists.version("quests"), 0);
        lists.set("quests", vec![row(&[("title", "A")])]);
        assert_eq!(lists.version("quests"), 1);
        // Same content again: no rebuild.
        lists.set("quests", vec![row(&[("title", "A")])]);
        assert_eq!(lists.version("quests"), 1);
        lists.set("quests", vec![row(&[("title", "B")])]);
        assert_eq!(lists.version("quests"), 2);
        // Emptying is a change too.
        lists.set("quests", vec![]);
        assert_eq!(lists.version("quests"), 3);
        assert!(lists.rows("quests").is_empty());
    }

    #[test]
    fn test_rows_of_an_unknown_list_is_empty_not_a_panic() {
        let lists = UiLists::default();
        assert!(lists.rows("nope").is_empty());
        assert_eq!(lists.version("nope"), 0);
    }

    #[test]
    fn test_script_owned_sources_beat_the_engine_collector() {
        let mut lists = UiLists::default();
        // A script feeds `bag` (an ENGINE name): from then on, per-frame
        // engine collectors must not clobber it.
        lists.set_script("bag", vec![row(&[("title", "do script")])]);
        lists.set_engine("bag", vec![row(&[("title", "da engine")])]);
        assert_eq!(lists.rows("bag")[0]["title"], "do script");
        // New script content still lands (the owner can update its own list).
        lists.set_script("bag", vec![row(&[("title", "v2")])]);
        assert_eq!(lists.rows("bag")[0]["title"], "v2");
        // Other sources keep flowing normally.
        lists.set_engine("skills", vec![row(&[("title", "engine")])]);
        assert_eq!(lists.rows("skills")[0]["title"], "engine");
        // Releasing the claim hands the source back to the engine.
        assert!(lists.release_script("bag"));
        assert!(!lists.is_script_owned("bag"));
        lists.set_engine("bag", vec![row(&[("title", "da engine")])]);
        assert_eq!(lists.rows("bag")[0]["title"], "da engine");
    }
}
