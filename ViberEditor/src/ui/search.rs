//! The Search dock (Ctrl+F): full-text search over the world's `.xml` and
//! `.lua` files (binaries in `<stem>.data/` skipped). Enter runs the search;
//! clicking a hit jumps: `.lua` opens in Script mode, `.xml` lines with
//! `name="…"`/`id="…"` select+reveal that node (anything else opens the file
//! externally). Pure disk scan — the same files agents edit.

use bevy::feathers::controls::FeathersTextInput;
use bevy::feathers::controls::FeathersTextInputContainer;
use bevy::prelude::*;

use super::icons::{self, fa, icon_text};
use super::strings::{Lang, LangLabel, StrKey, t};
use super::theme;
use crate::scene::Selection;
use crate::scene::TreeExpansion;
use viber::scene::doc::SceneDoc;

/// One result: file (world-relative), 1-based line, the trimmed line text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SearchHit {
    pub path: String,
    pub line: usize,
    pub text: String,
}

#[derive(Resource, Debug, Default)]
pub struct SearchState {
    results: Vec<SearchHit>,
    dirty: bool,
}

/// The search box.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SearchInput;

/// The results scroll body.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SearchBody;

/// One clickable result row.
#[derive(Component, Debug, Clone)]
pub struct SearchRow(pub SearchHit);

/// Max results kept (the list is a human picker, not grep).
const MAX_RESULTS: usize = 200;
/// Files above this are skipped (binaries/vinst live in `.data/`, but guard anyway).
const MAX_FILE_BYTES: u64 = 1_000_000;

pub struct SearchPlugin;

impl Plugin for SearchPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<SearchState>().add_systems(
            Update,
            (
                search_shortcut,
                apply_search_input,
                render_search,
                focus_search_input,
            ),
        );
    }
}

/// The dock body: search box over the scrollable results.
pub fn search_body() -> impl Scene {
    bsn! {
        @theme::pane()
        Children [
            @theme::pane_body()
            Node { width: percent(100), row_gap: px(4) }
            Children [
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                    flex_shrink: 0.0,
                }
                Children [
                    @caption(t(Lang::default(), StrKey::SearchHint)) LangLabel(StrKey::SearchHint) Pickable::IGNORE
                    --
                    @FeathersTextInputContainer
                    Node { width: px(320), min_width: px(0) }
                    Children [
                        @FeathersTextInput {
                            @visible_width: 8f32,
                        }
                        SearchInput
                    ]
                    Node { flex_grow: 1.0 }
                ]
                --
                Node {
                    flex_grow: 1.0,
                    min_height: px(0),
                    overflow: Overflow::scroll_y(),
                    flex_direction: FlexDirection::Column,
                    width: percent(100),
                }
                SearchBody
                Children []
            ]
        ]
    }
}

fn caption(label: impl Into<String>) -> impl Scene {
    bsn! {
        Text({label.into()})
        TextFont { font_size: {FontSize::Px(12.0)} }
        TextColor(Color::srgb(0.62, 0.66, 0.72))
    }
}

/// Ctrl+F: switches to the Search tab and focuses the box.
fn search_shortcut(
    keys: Res<ButtonInput<KeyCode>>,
    mut active: ResMut<super::layout::ActiveDockTab>,
    mut commands: Commands,
) {
    let ctrl = keys.any_pressed([KeyCode::ControlLeft, KeyCode::ControlRight]);
    if ctrl && keys.just_pressed(KeyCode::KeyF) {
        active.0 = super::layout::TabKind::Search;
        commands.queue(|world: &mut World| {
            focus_input(world);
        });
    }
}

/// Foca a caixa (também usado pelo atalho).
fn focus_input(world: &mut World) {
    let mut inputs = world.query_filtered::<Entity, With<SearchInput>>();
    let Some(entity) = inputs.iter(world).next() else {
        return;
    };
    if let Some(mut focus) = world.get_resource_mut::<bevy::input_focus::InputFocus>() {
        focus.set(entity, bevy::input_focus::FocusCause::Navigated);
    }
}

/// A system that focuses the input when the tab becomes active (one-shot per
/// activation; the Ctrl+F path queues [`focus_input`] directly).
fn focus_search_input(
    active: Res<super::layout::ActiveDockTab>,
    mut was_active: Local<bool>,
    mut commands: Commands,
) {
    let is_active = active.0 == super::layout::TabKind::Search;
    if is_active && !*was_active {
        commands.queue(focus_input);
    }
    *was_active = is_active;
}

/// Enter runs the scan; Esc clears the results.
fn apply_search_input(world: &mut World) {
    let Some(focused) = world
        .get_resource::<bevy::input_focus::InputFocus>()
        .and_then(|focus| focus.get())
    else {
        return;
    };
    if world.get::<SearchInput>(focused).is_none() {
        return;
    }
    let keys = world.resource::<ButtonInput<KeyCode>>();
    let (enter, escape) = (
        keys.just_pressed(KeyCode::Enter),
        keys.just_pressed(KeyCode::Escape),
    );
    if !enter && !escape {
        return;
    }
    let Some(text) = world.get::<Text>(focused).cloned() else {
        return;
    };
    let needle = text.0.trim().to_owned();
    if escape || needle.is_empty() {
        let mut state = world.resource_mut::<SearchState>();
        state.results.clear();
        state.dirty = true;
        return;
    }
    let Some(root) = world
        .resource::<SceneDoc>()
        .world_path()
        .parent()
        .map(|dir| dir.to_path_buf())
    else {
        return;
    };
    let lang = *world.resource::<Lang>();
    let results = scan(&root, &needle);
    let count = results.len();
    {
        let mut state = world.resource_mut::<SearchState>();
        state.results = results;
        state.dirty = true;
    }
    info!(
        "pesquisa `{needle}`: {count} resultado(s) — {}",
        t(lang, StrKey::SearchHint)
    );
}

/// Case-insensitive full-text scan of the world folder.
fn scan(root: &std::path::Path, needle: &str) -> Vec<SearchHit> {
    let mut hits = Vec::new();
    let needle = needle.to_ascii_lowercase();
    let skip_dir = |name: &str| {
        name.starts_with('.')
            || name == "target"
            || name == "node_modules"
            || name.ends_with(".data")
    };
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        let mut children: Vec<_> = entries.filter_map(Result::ok).collect();
        children.sort_by_key(|entry| entry.path());
        for entry in children {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if path.is_dir() {
                if !skip_dir(&name) {
                    stack.push(path);
                }
                continue;
            }
            let is_text = name.to_ascii_lowercase().ends_with(".xml")
                || name.to_ascii_lowercase().ends_with(".lua");
            if !is_text {
                continue;
            }
            if std::fs::metadata(&path)
                .map(|meta| meta.len() > MAX_FILE_BYTES)
                .unwrap_or(true)
            {
                continue;
            }
            let Ok(content) = std::fs::read_to_string(&path) else {
                continue;
            };
            let rel = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .display()
                .to_string();
            for (index, line) in content.lines().enumerate() {
                if line.to_ascii_lowercase().contains(&needle) {
                    hits.push(SearchHit {
                        path: rel.clone(),
                        line: index + 1,
                        text: line.trim().to_owned(),
                    });
                    if hits.len() >= MAX_RESULTS {
                        return hits;
                    }
                }
            }
        }
    }
    hits
}

/// Rebuilds the result rows (and the empty state) when the results change.
fn render_search(
    mut commands: Commands,
    mut state: ResMut<SearchState>,
    lang: Res<Lang>,
    bodies: Query<Entity, With<SearchBody>>,
) {
    if !state.dirty {
        return;
    }
    state.dirty = false;
    let Ok(body) = bodies.single() else {
        return;
    };
    commands.entity(body).despawn_related::<Children>();
    if state.results.is_empty() {
        commands.spawn((
            Text::new(t(*lang, StrKey::SearchNoMatches).to_owned()),
            TextFont::from_font_size(12.0),
            TextColor(Color::srgb(0.55, 0.58, 0.64)),
            Pickable::IGNORE,
            ChildOf(body),
        ));
        return;
    }
    for hit in &state.results {
        commands
            .spawn((
                SearchRow(hit.clone()),
                Node {
                    flex_direction: FlexDirection::Column,
                    row_gap: px(0),
                    padding: UiRect::vertical(px(1)),
                    ..default()
                },
                ChildOf(body),
            ))
            .observe(hit_clicked)
            .with_children(|row| {
                row.spawn((
                    Node {
                        flex_direction: FlexDirection::Row,
                        column_gap: px(6),
                        ..default()
                    },
                    Pickable::IGNORE,
                ))
                .with_children(|head| {
                    head.spawn((
                        icon_text(fa::MAGNIFYING_GLASS, 10.0, icons::ICON_DIM),
                        Node {
                            width: px(13),
                            ..default()
                        },
                        Pickable::IGNORE,
                    ));
                    head.spawn((
                        Text::new(format!("{}:{}", hit.path, hit.line)),
                        TextFont::from_font_size(10.0),
                        TextColor(Color::srgb(0.62, 0.78, 0.95)),
                        Pickable::IGNORE,
                    ));
                });
                row.spawn((
                    Text::new(hit.text.clone()),
                    TextFont::from_font_size(11.0),
                    TextColor(Color::srgb(0.82, 0.86, 0.9)),
                    Pickable::IGNORE,
                ));
            });
    }
}

/// Click a hit: `.lua` → Script mode; `.xml` with `name=`/`id=` → select+reveal.
#[allow(clippy::too_many_arguments)] // Bevy system: um parâmetro por recurso/query.
fn hit_clicked(
    click: On<PointerClick>,
    rows: Query<&SearchRow>,
    doc: Res<SceneDoc>,
    mut selection: ResMut<Selection>,
    mut expansion: ResMut<TreeExpansion>,
    mut mode: ResMut<super::mode_strip::EditorModeState>,
    mut scripts: ResMut<super::mode_strip::ScriptState>,
    mut commands: Commands,
) {
    let Ok(row) = rows.get(click.event_target()) else {
        return;
    };
    let hit = &row.0;
    if hit.path.to_ascii_lowercase().ends_with(".lua") {
        scripts.selected = Some(hit.path.clone());
        mode.0 = super::mode_strip::EditorMode::Script;
        return;
    }
    // XML: procura name=/id= na linha e seleciona o nó (1.º que case)
    if let Some(value) = attr_value(&hit.text, "name").or_else(|| attr_value(&hit.text, "id")) {
        let mut found = None;
        doc.walk(|node, xml| {
            if found.is_some() {
                return;
            }
            if ["name", "id"]
                .iter()
                .any(|key| xml.attr(key) == Some(value.as_str()))
            {
                found = Some(node.clone());
            }
        });
        if let Some(node) = found {
            crate::scene::reveal(&doc, &node, &mut expansion);
            selection.set_one(node);
            return;
        }
    }
    // fallback: abre no editor externo
    if let Some(dir) = doc.world_path().parent() {
        let _ = std::process::Command::new("xdg-open")
            .arg(dir.join(&hit.path))
            .spawn();
    }
    commands.queue(|world: &mut World| {
        let lang = *world.resource::<Lang>();
        world.resource_mut::<crate::scene::EditStatus>().0 =
            t(lang, StrKey::FilesOpened).to_string();
    });
}

/// O valor de `key="value"` numa linha XML (aspas duplas ou simples).
fn attr_value(line: &str, key: &str) -> Option<String> {
    let needle = format!("{key}=\"");
    let start = line.find(&needle)? + needle.len();
    let rest = &line[start..];
    let end = rest.find('"')?;
    if end == 0 {
        return None;
    }
    Some(rest[..end].to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn attr_value_reads_quoted_values() {
        assert_eq!(
            attr_value("<Entity name=\"chapel\" pos=\"0 0 0\">", "name"),
            Some("chapel".to_owned())
        );
        assert_eq!(attr_value("<Entity name='x'>", "name"), None);
        assert_eq!(attr_value("<Entity>", "name"), None);
    }

    #[test]
    fn scan_finds_matches_and_skips_data() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir(dir.path().join("world.data")).unwrap();
        std::fs::write(dir.path().join("world.data/a.xml"), "hidden dragon").unwrap();
        std::fs::write(
            dir.path().join("world.xml"),
            "<world>\n  <Entity name=\"dragon-nest\"/>\n</world>\n",
        )
        .unwrap();
        std::fs::write(dir.path().join("ai.lua"), "-- quest dragon\nreturn 1\n").unwrap();
        let hits = scan(dir.path(), "dragon");
        let paths: Vec<&str> = hits.iter().map(|hit| hit.path.as_str()).collect();
        assert_eq!(paths, ["ai.lua", "world.xml"]);
        assert_eq!(hits[1].line, 2);
    }
}
