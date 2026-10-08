//! Builds the UI entity tree from a world's `<UiRoot>` subtree.
//!
//! Tags are intentionally few and each maps to one obvious bevy_ui shape:
//!
//! | tag | renders as | notes |
//! |---|---|---|
//! | `UiRoot` | full-screen absolute layer | one per world, holds everything |
//! | `UiPanel` / `UiRow` / `UiColumn` | a box | `Row`/`Column` preset the direction |
//! | `UiGrid` | a CSS-like grid | `cols="repeat(4, 1fr)"` / `rows="40 40"` |
//! | `UiText` | text | content is the element's text or `text="…"` |
//! | `UiIcon` | image | `src="/assets/…"` |
//! | `UiBar` | track + fill | `value="0..1"`, fill gets `.fill` + `fill-class` |
//! | `UiCooldown` | box + wipe veil | `value` is the *remaining* fraction |
//! | `UiButton` | clickable box | reports presses to `viber.ui.clicked(id)` |
//! | `UiCheck` | toggle | `value="1"` starts on; classes `checked`/`unchecked` |
//! | `UiSlider` | draggable range | `min`/`max`/`step`, `.fill` + `.handle` children |
//! | `UiInput` | single-line text field | click focuses; Enter/Escape blurs |
//! | `UiSpacer` | flexible gap | `flex-grow: 1` by default |
//!
//! Every tag takes `id`, `class` and inline `style`, so the stylesheet does
//! all the visual work and the XML stays a pure structure description. Any
//! element can also carry `anim="spin|pulse|bob|shake"` (motion, `ui::anim`)
//! and `tooltip="…"` (hover hint, `ui::widgets`).

use bevy::prelude::*;
// Bevy 0.20: `Interaction`/`Button` do prelude são aliases de tipos privados.
use crate::ui::interaction::{Button, Interaction};
use bevy::ui::FocusPolicy;
use bevy::ui::widget::ImageNode;

use super::anim::UiAnim;
use super::list::{UiList, UiLists, is_empty_tag, is_template_tag};
use super::modal::{UiModal, UiScroll, UiTabButton, UiTabPage, parse_key};
use super::runtime::{
    UiBar, UiClasses, UiCooldown, UiDisabled, UiId, UiInlineStyle, UiRegistry, UiStyleDirty, UiTag,
};
use super::style::Measure;
use super::style::parse_declarations;
use super::widgets::{UiCheck, UiInput, UiSlider, UiTooltipText, normalized_range};
use crate::xml::XmlNode;

/// Tags this module knows how to build (lowercased).
pub const UI_TAGS: &[&str] = &[
    "uiroot",
    "uipanel",
    "uirow",
    "uicolumn",
    "uigrid",
    "uitext",
    "uiicon",
    "uibar",
    "uicooldown",
    "uibutton",
    "uicheck",
    "uislider",
    "uiinput",
    "uispacer",
    "uimodal",
    "uilist",
];

/// True when `tag` (any case) is one of the declarative UI elements.
pub fn is_ui_tag(tag: &str) -> bool {
    let lowered = tag.to_ascii_lowercase();
    UI_TAGS.contains(&lowered.as_str())
}

fn attr<'a>(node: &'a XmlNode, name: &str) -> Option<&'a str> {
    node.attrs
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case(name))
        .map(|(_, v)| v.as_str())
}

fn attr_f32(node: &XmlNode, name: &str) -> Option<f32> {
    attr(node, name).and_then(|v| v.trim().parse().ok())
}

/// Text content of an element: the `text` attribute, else the concatenated
/// text of the XML node.
fn element_text(node: &XmlNode) -> String {
    if let Some(text) = attr(node, "text") {
        return text.to_string();
    }
    node.text.trim().to_string()
}

/// Spawns `node` and its children under `parent`, returning the root entity.
///
/// `font` is the HUD display font handed to every `UiText`; the stylesheet
/// controls its size and colour.
pub fn build_ui_tree(
    world: &mut World,
    node: &XmlNode,
    parent: Option<Entity>,
    font: &Handle<Font>,
    assets: &AssetServer,
) -> Option<Entity> {
    let tag = node.tag.to_ascii_lowercase();
    if !UI_TAGS.contains(&tag.as_str()) {
        warn!(
            "ui: unknown element `{}` — skipped with its subtree",
            node.tag
        );
        return None;
    }

    // The `AssetServer` is passed in rather than pulled from the world: the
    // world spawn takes it out while it builds, so `world.resource()` here
    // panicked on the first `<UiIcon>`.
    let image: Option<Handle<Image>> = (tag == "uiicon").then(|| {
        let src = attr(node, "src")
            .unwrap_or_default()
            .trim_start_matches('/');
        assets.load(src.to_string())
    });

    // Every element is a `Node` first; the stylesheet fills in the rest.
    let mut entity = world.spawn((
        Node::default(),
        UiTag(tag.clone()),
        UiStyleDirty,
        BackgroundColor(Color::NONE),
        BorderColor::all(Color::NONE),
        ZIndex(0),
        UiTransform::default(),
    ));

    if let Some(id) = attr(node, "id") {
        entity.insert(UiId(id.to_string()));
    }
    // Presets that are really "a class the tag implies", so a stylesheet can
    // still override them: the row/column direction and the spacer's growth.
    let mut classes = UiClasses::parse(attr(node, "class").unwrap_or_default());
    classes.0.insert(0, tag.clone());
    entity.insert(classes);
    let mut inline = attr(node, "style")
        .map(|text| parse_declarations(text, &node.tag))
        .unwrap_or_default();
    match tag.as_str() {
        "uirow" => inline.flex_direction = inline.flex_direction.or(Some(FlexDirection::Row)),
        "uicolumn" => inline.flex_direction = inline.flex_direction.or(Some(FlexDirection::Column)),
        "uigrid" => {
            // A grelha também é "uma classe que a tag implica", com as pistas
            // como atributos de autor (`cols="repeat(4, 1fr)"`).
            inline.display = inline.display.or(Some(Display::Grid));
            if let Some(cols) = attr(node, "cols").and_then(super::style::parse_tracks) {
                inline.grid_template_columns = Some(cols);
            }
            if let Some(rows) = attr(node, "rows").and_then(super::style::parse_tracks) {
                inline.grid_template_rows = Some(rows);
            }
            if let Some(flow) = attr(node, "flow").and_then(super::style::parse_auto_flow) {
                inline.grid_auto_flow = Some(flow);
            }
        }
        "uispacer" => inline.flex_grow = inline.flex_grow.or(Some(1.0)),
        "uiroot" => {
            inline.position = inline.position.or(Some(PositionType::Absolute));
            inline.width = inline.width.or(Some(Measure::plain(Val::Percent(100.0))));
            inline.height = inline.height.or(Some(Measure::plain(Val::Percent(100.0))));
        }
        _ => {}
    }
    // Roots, labels and other decoration must not intercept siblings/parents.
    // Apply inline pointer-events now as well as in the later style pass.
    let blocks_pointer = matches!(
        tag.as_str(),
        "uibutton" | "uicheck" | "uislider" | "uiinput" | "uimodal"
    ) || attr(node, "scroll").is_some();
    let pointer_none = inline.pointer_none.unwrap_or(false);
    entity.insert(super::runtime::ui_focus_policy(
        blocks_pointer,
        pointer_none,
    ));
    if pointer_none {
        entity.insert(super::runtime::UiPointerNone);
    }
    entity.insert(UiInlineStyle(inline));

    if let Some(bind) = attr(node, "bind") {
        entity.insert(super::runtime::UiBind(bind.trim().to_ascii_lowercase()));
    }
    // `fade="0.2 0.6"` turns a flag binding from a hard show/hide into a
    // dissolve, and the alpha is inherited by the whole widget.
    if let Some(spec) = attr(node, "fade") {
        entity.insert(super::fade::UiFade::parse(spec));
    }
    // Tab wiring: a button selects, a page is shown. The same attribute pair
    // on either kind of element, so a tab bar reads as a tab bar in the XML.
    if let Some(group) = attr(node, "tab-group") {
        let tab = attr(node, "tab").unwrap_or_default().to_string();
        if tag == "uibutton" {
            entity.insert(UiTabButton {
                group: group.to_string(),
                tab,
            });
        } else {
            entity.insert(UiTabPage {
                group: group.to_string(),
                tab,
            });
        }
    }
    if let Some(axis) = attr(node, "scroll") {
        let vertical = !axis.eq_ignore_ascii_case("x");
        entity.insert((
            UiScroll {
                vertical,
                ..Default::default()
            },
            ScrollPosition::default(),
            // Scrolling without clipping just draws outside the box, and the
            // wheel handler needs the element to be pickable.
            Interaction::default(),
        ));
    }
    if attr(node, "hidden").is_some_and(is_truthy) {
        entity.insert(Visibility::Hidden);
    }
    if attr(node, "disabled").is_some_and(is_truthy) {
        entity.insert(UiDisabled);
    }
    // Motion (`anim="pulse 1.5"`) and hover hints (`tooltip="…"`), on any
    // element — they are attributes of a widget, not widgets of their own.
    if let Some(spec) = attr(node, "anim") {
        match UiAnim::parse(spec) {
            Some(anim) => {
                entity.insert(anim);
            }
            None => warn!("ui: unknown anim `{spec}` on <{}> — skipped", node.tag),
        }
    }
    if let Some(text) = attr(node, "tooltip") {
        if !text.is_empty() {
            entity.insert(UiTooltipText(text.to_string()));
        }
    }

    match tag.as_str() {
        "uitext" => {
            entity.insert((
                Text::new(element_text(node)),
                TextColor(Color::WHITE),
                TextFont {
                    font: font.clone().into(),
                    ..Default::default()
                },
                TextLayout::default(),
            ));
        }
        "uiicon" => {
            if let Some(handle) = image {
                entity.insert(ImageNode::new(handle));
            }
        }
        "uibutton" => {
            entity.insert((Button, Interaction::default()));
        }
        "uicheck" => {
            entity.insert((
                Button,
                Interaction::default(),
                UiCheck {
                    checked: attr(node, "value").is_some_and(is_truthy),
                },
            ));
        }
        "uislider" => {
            entity.insert((Button, Interaction::default()));
        }
        "uiinput" => {
            entity.insert((Button, Interaction::default()));
        }
        "uimodal" => {
            if attr(node, "id").is_none() {
                // Abre à mesma, mas fica fora do registry: `viber.ui.open`
                // não o alcança e o gameplay não o consegue interrogar.
                warn!(
                    "ui: <{}> sem `id` — não é endereçável por `viber.ui.open`/`is_open`",
                    node.tag
                );
            }
            entity.insert((
                UiModal {
                    key: parse_key(attr(node, "key").unwrap_or("f1")),
                    // A modal starts closed: a menu that greets the player on
                    // load is a bug, not a feature.
                    open: false,
                    escape_closes: attr(node, "escape-closes").is_none_or(is_truthy),
                },
                Visibility::Hidden,
            ));
        }
        _ => {}
    }

    let this = entity.id();
    if let Some(parent) = parent {
        world.entity_mut(this).insert(ChildOf(parent));
    }

    // Composite widgets own their internal nodes; author children still nest
    // inside so a bar can carry a label.
    match tag.as_str() {
        "uibar" => build_bar(world, node, this),
        "uicooldown" => build_cooldown(world, node, this),
        "uislider" => build_slider(world, node, this),
        "uiinput" => build_input(world, node, this, font),
        _ => {}
    }

    if tag == "uilist" {
        build_list(world, node, this, font, assets);
    } else {
        for (index, child) in node.children.iter().enumerate() {
            if let Some(built) = build_ui_tree(world, child, Some(this), font, assets) {
                world
                    .entity_mut(built)
                    .insert(super::runtime::UiOrder(index));
            }
        }
    }

    if let Some(id) = attr(node, "id") {
        let mut registry = world.resource_mut::<UiRegistry>();
        if let Some(previous) = registry.by_id.insert(id.to_string(), this) {
            warn!("ui: duplicate id `{id}` — {previous:?} is no longer addressable");
        }
    }
    Some(this)
}

/// Track + fill. The fill is a real child so the stylesheet can paint it
/// (`.fill`, plus whatever `fill-class` names) while its width stays the
/// widget's business.
fn build_bar(world: &mut World, node: &XmlNode, track: Entity) {
    let value = attr_f32(node, "value").unwrap_or(1.0);
    let vertical = attr(node, "direction").is_some_and(|d| d.eq_ignore_ascii_case("vertical"));
    let mut fill_classes = UiClasses::parse(attr(node, "fill-class").unwrap_or_default());
    fill_classes.0.insert(0, "fill".to_string());
    // A vertical bar grows from the bottom, a horizontal one from the left.
    let fill_inline = if vertical {
        super::style::StyleProps {
            width: Some(Measure::plain(Val::Percent(100.0))),
            height: Some(Measure::plain(super::runtime::bar_fill_size(value))),
            align_self: Some(AlignSelf::FlexEnd),
            ..Default::default()
        }
    } else {
        super::style::StyleProps {
            height: Some(Measure::plain(Val::Percent(100.0))),
            width: Some(Measure::plain(super::runtime::bar_fill_size(value))),
            ..Default::default()
        }
    };
    let fill = world
        .spawn((
            Node::default(),
            FocusPolicy::Pass,
            UiTag("uifill".to_string()),
            fill_classes,
            UiInlineStyle(fill_inline),
            UiStyleDirty,
            BackgroundColor(Color::WHITE),
            ChildOf(track),
        ))
        .id();
    if let Some(id) = attr(node, "id") {
        let mut registry = world.resource_mut::<UiRegistry>();
        registry.by_id.insert(format!("{id}.fill"), fill);
    }
    world.entity_mut(track).insert(UiBar {
        value,
        fill,
        vertical,
    });
}

/// Icon slot + a veil that wipes downward as the ability recharges.
fn build_cooldown(world: &mut World, node: &XmlNode, slot: Entity) {
    let value = attr_f32(node, "value").unwrap_or(0.0);
    let mut veil_classes = UiClasses::parse(attr(node, "veil-class").unwrap_or_default());
    veil_classes.0.insert(0, "veil".to_string());
    let veil_inline = super::style::StyleProps {
        position: Some(PositionType::Absolute),
        left: Some(Measure::plain(Val::Px(0.0))),
        top: Some(Measure::plain(Val::Px(0.0))),
        width: Some(Measure::plain(Val::Percent(100.0))),
        height: Some(Measure::plain(Val::Percent(value.clamp(0.0, 1.0) * 100.0))),
        ..Default::default()
    };
    let veil = world
        .spawn((
            Node::default(),
            FocusPolicy::Pass,
            UiTag("uiveil".to_string()),
            veil_classes,
            UiInlineStyle(veil_inline),
            UiStyleDirty,
            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.62)),
            ChildOf(slot),
        ))
        .id();
    if let Some(id) = attr(node, "id") {
        let mut registry = world.resource_mut::<UiRegistry>();
        registry.by_id.insert(format!("{id}.veil"), veil);
    }
    world.entity_mut(slot).insert(UiCooldown { value, veil });
}

/// Track + fill + handle. The fill mirrors the value like a bar's; the handle
/// slides along the edge. Both are real children, so the stylesheet paints
/// them (`.fill` / `.handle`, plus `fill-class`/`handle-class`).
fn build_slider(world: &mut World, node: &XmlNode, track: Entity) {
    let min = attr_f32(node, "min").unwrap_or(0.0);
    let max = attr_f32(node, "max").unwrap_or(1.0);
    // `f32::clamp` asserta `min <= max`: um intervalo invertido
    // (`min="100" max="10"`) ou não finito rebentava a engine no load.
    // Avisa e normaliza — o autor vê o erro no log e o mundo abre na mesma.
    if !min.is_finite() || !max.is_finite() {
        warn!(
            "ui: <{}> slider com min/max não finito — a usar o omissão 0..1",
            node.tag
        );
    } else if max < min {
        warn!(
            "ui: <{}> slider com min {min} > max {max} — intervalo invertido, extremos trocados",
            node.tag
        );
    }
    let (min, max) = normalized_range(min, max);
    let value = match attr_f32(node, "value") {
        Some(v) if v.is_finite() => v.clamp(min, max),
        Some(_) => {
            warn!(
                "ui: <{}> slider com value não finito — a usar o máximo {max}",
                node.tag
            );
            max
        }
        None => max,
    };
    let vertical = attr(node, "direction").is_some_and(|d| d.eq_ignore_ascii_case("vertical"));
    let mut fill_classes = UiClasses::parse(attr(node, "fill-class").unwrap_or_default());
    fill_classes.0.insert(0, "fill".to_string());
    let mut handle_classes = UiClasses::parse(attr(node, "handle-class").unwrap_or_default());
    handle_classes.0.insert(0, "handle".to_string());
    // Fill grows from the left (or the bottom, vertically) like a bar fill.
    let fill_inline = if vertical {
        super::style::StyleProps {
            width: Some(Measure::plain(Val::Percent(100.0))),
            height: Some(Measure::plain(super::runtime::bar_fill_size(
                value.clamp(min, max),
            ))),
            align_self: Some(AlignSelf::FlexEnd),
            ..Default::default()
        }
    } else {
        super::style::StyleProps {
            height: Some(Measure::plain(Val::Percent(100.0))),
            width: Some(Measure::plain(super::runtime::bar_fill_size(
                value.clamp(min, max),
            ))),
            ..Default::default()
        }
    };
    let fill = world
        .spawn((
            Node::default(),
            FocusPolicy::Pass,
            UiTag("uifill".to_string()),
            fill_classes,
            UiInlineStyle(fill_inline),
            UiStyleDirty,
            BackgroundColor(Color::WHITE),
            ChildOf(track),
        ))
        .id();
    // The handle rides the fill edge; the drive system keeps it positioned by
    // percentage. Centered on the edge by the stylesheet's typical negative
    // margins — here it just starts at the right spot.
    let fraction = super::runtime::bar_fill_size((value - min) / (max - min).max(f32::EPSILON));
    let handle_inline = super::style::StyleProps {
        position: Some(PositionType::Absolute),
        top: Some(Measure::plain(Val::Px(0.0))),
        left: if vertical {
            None
        } else {
            Some(Measure::Plain(fraction))
        },
        bottom: if vertical {
            Some(Measure::plain(Val::Percent(0.0)))
        } else {
            None
        },
        ..Default::default()
    };
    let handle = world
        .spawn((
            Node::default(),
            FocusPolicy::Pass,
            UiTag("uihandle".to_string()),
            handle_classes,
            UiInlineStyle(handle_inline),
            UiStyleDirty,
            ChildOf(track),
        ))
        .id();
    if let Some(id) = attr(node, "id") {
        let mut registry = world.resource_mut::<UiRegistry>();
        registry.by_id.insert(format!("{id}.fill"), fill);
        registry.by_id.insert(format!("{id}.handle"), handle);
    }
    world.entity_mut(track).insert(UiSlider {
        value,
        min,
        max,
        step: attr_f32(node, "step").unwrap_or(0.0),
        vertical,
        fill,
        handle,
    });
}

/// Field + its `<UiText>` child. The text child is authored-invisible: this
/// widget owns its content (`text` / `placeholder`) and `ui::widgets` keeps it
/// in sync.
fn build_input(world: &mut World, node: &XmlNode, field: Entity, font: &Handle<Font>) {
    let text = world
        .spawn((
            Node::default(),
            FocusPolicy::Pass,
            UiTag("uitext".to_string()),
            UiClasses::parse("input-text"),
            UiInlineStyle(super::style::StyleProps {
                width: Some(Measure::plain(Val::Percent(100.0))),
                ..Default::default()
            }),
            UiStyleDirty,
            Text::new(element_text(node)),
            TextColor(Color::WHITE),
            TextFont {
                font: font.clone().into(),
                ..Default::default()
            },
            TextLayout::default(),
            ChildOf(field),
        ))
        .id();
    if let Some(id) = attr(node, "id") {
        let mut registry = world.resource_mut::<UiRegistry>();
        registry.by_id.insert(format!("{id}.text"), text);
    }
    world.entity_mut(field).insert(UiInput {
        // The authored content is the *value*; the mirror pass overwrites the
        // child with it (or the placeholder) on the first frame.
        text: element_text(node),
        child: text,
        placeholder: attr(node, "placeholder").map(String::from),
        max_len: attr_f32(node, "max-length").map(|n| n.max(0.0) as usize),
    });
}

/// A repeater: `<UiTemplate>` children are stored for cloning, `<UiEmpty>` is
/// built once as the empty-state label, and anything else is built normally.
fn build_list(
    world: &mut World,
    node: &XmlNode,
    this: Entity,
    font: &Handle<Font>,
    assets: &AssetServer,
) {
    let mut template = Vec::new();
    let mut empty_label = None;
    for child in &node.children {
        if is_template_tag(&child.tag) {
            template.extend(child.children.iter().cloned());
        } else if is_empty_tag(&child.tag) {
            // Built from the template's own element vocabulary; the rebuild
            // toggles its `display` once it knows whether the list is empty.
            for grandchild in &child.children {
                if let Some(entity) = build_ui_tree(world, grandchild, Some(this), font, assets) {
                    empty_label = Some(entity);
                    world.entity_mut(entity).insert(Visibility::Hidden);
                }
            }
        } else if let Some(built) = build_ui_tree(world, child, Some(this), font, assets) {
            world.entity_mut(built).insert(super::runtime::UiOrder(0));
        }
    }
    let source = attr(node, "bind").unwrap_or_default().to_ascii_lowercase();
    if source.is_empty() {
        warn!("ui: <UiList> without `bind` never fills — check the world XML");
    }
    // A list drives its own rows; the generic `bind` handling must not also
    // treat the source name as a scalar binding.
    world.entity_mut(this).remove::<super::runtime::UiBind>();
    world.entity_mut(this).insert(UiList {
        source,
        template,
        built_version: None,
        empty_label,
    });
    // Touch the resource so the rebuild system exists even in a world whose
    // lists are only ever fed by scripts.
    world.init_resource::<UiLists>();
}

fn is_truthy(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_node(tag: &str, id: &str) -> XmlNode {
        XmlNode {
            tag: tag.into(),
            attrs: vec![("id".into(), id.into())],
            text: String::new(),
            children: Vec::new(),
        }
    }

    fn tree_app() -> App {
        let mut app = App::new();
        app.add_plugins((MinimalPlugins, bevy::asset::AssetPlugin::default()))
            .init_asset::<Image>()
            .init_resource::<UiRegistry>();
        app
    }

    fn build_test_node(app: &mut App, node: &XmlNode) -> Entity {
        let assets = app.world().resource::<AssetServer>().clone();
        build_ui_tree(app.world_mut(), node, None, &Handle::default(), &assets).unwrap()
    }

    #[test]
    fn test_decorative_nodes_and_composite_children_pass_focus() {
        let mut app = tree_app();
        for tag in UI_TAGS {
            let mut node = test_node(tag, tag);
            if *tag == "uiicon" {
                node.attrs.push(("src".into(), "test-icon.ktx2".into()));
            }
            if *tag == "uilist" {
                node.attrs.push(("bind".into(), "quests".into()));
            }
            if *tag == "uibutton" {
                node.children.push(test_node("uitext", "button-label"));
            }
            let entity = build_test_node(&mut app, &node);
            let blocks = matches!(
                *tag,
                "uibutton" | "uicheck" | "uislider" | "uiinput" | "uimodal"
            );
            assert_eq!(
                app.world().get::<FocusPolicy>(entity),
                Some(&super::super::runtime::ui_focus_policy(blocks, false)),
                "{tag}",
            );
            if let Some(children) = app.world().get::<Children>(entity) {
                for child in children.iter() {
                    assert_eq!(
                        app.world().get::<FocusPolicy>(child),
                        Some(&FocusPolicy::Pass),
                        "child of {tag}"
                    );
                }
            }
        }
    }

    #[test]
    fn test_scroll_blocks_and_inline_pointer_none_passes_at_spawn() {
        let mut app = tree_app();
        let mut scroll = test_node("uipanel", "scroll");
        scroll.attrs.push(("scroll".into(), "y".into()));
        let entity = build_test_node(&mut app, &scroll);
        assert_eq!(
            app.world().get::<FocusPolicy>(entity),
            Some(&FocusPolicy::Block)
        );
        for tag in ["uibutton", "uimodal", "uipanel"] {
            let mut node = test_node(tag, tag);
            node.attrs.push(("scroll".into(), "y".into()));
            node.attrs
                .push(("style".into(), "pointer-events: none".into()));
            let entity = build_test_node(&mut app, &node);
            assert_eq!(
                app.world().get::<FocusPolicy>(entity),
                Some(&FocusPolicy::Pass)
            );
            assert!(
                app.world()
                    .get::<super::super::runtime::UiPointerNone>(entity)
                    .is_some()
            );
        }
    }

    #[test]
    fn test_focus_reaches_journal_through_label_and_menu_root_but_not_open_modal() {
        use super::super::runtime::{UiClicks, collect_ui_clicks};
        use bevy::app::HierarchyPropagatePlugin;
        use bevy::camera::RenderTarget;
        use bevy::input::touch::Touches;
        use bevy::math::Affine2;
        use crate::ui::interaction::ui_focus_system;
        use bevy::ui::{ComputedUiTargetCamera, UiStack};
        use bevy::window::PrimaryWindow;

        let mut app = tree_app();
        app.init_resource::<UiScale>()
            .init_resource::<ButtonInput<MouseButton>>()
            .init_resource::<Touches>()
            .init_resource::<UiClicks>()
            .add_plugins(HierarchyPropagatePlugin::<ComputedUiTargetCamera>::new(
                PostUpdate,
            ))
            .add_systems(Update, bevy::ui::update::propagate_ui_target_cameras)
            .add_systems(Last, (ui_focus_system, collect_ui_clicks).chain());
        let mut window = Window::default();
        window.set_cursor_position(Some(Vec2::splat(50.0)));
        app.world_mut().spawn((window, PrimaryWindow));
        let camera = app
            .world_mut()
            .spawn((Camera::default(), RenderTarget::default()))
            .id();

        let mut hud = test_node("uiroot", "hud");
        hud.children.push(test_node("uibutton", "behind-journal"));
        let mut journal = test_node("uibutton", "open-journal");
        journal.children.push(test_node("uitext", "journal-label"));
        hud.children.push(journal);
        let hud = build_test_node(&mut app, &hud);
        let mut menu = test_node("uiroot", "menu-layer");
        menu.children.push(test_node("uimodal", "menu"));
        let menu = build_test_node(&mut app, &menu);
        app.world_mut()
            .entity_mut(hud)
            .insert(UiTargetCamera(camera));
        app.world_mut()
            .entity_mut(menu)
            .insert(UiTargetCamera(camera));
        let registry = app.world().resource::<UiRegistry>();
        let behind = registry.get("behind-journal").unwrap();
        let journal = registry.get("open-journal").unwrap();
        let label = registry.get("journal-label").unwrap();
        let modal = registry.get("menu").unwrap();
        // Headless fixture: all rectangles overlap the pointer; actual focus
        // traversal is Bevy's, with the same back-to-front order as the HUD.
        let stack = vec![hud, behind, journal, label, menu, modal];
        for entity in &stack {
            app.world_mut().entity_mut(*entity).insert((
                ComputedNode {
                    size: Vec2::splat(100.0),
                    ..Default::default()
                },
                UiGlobalTransform::from(Affine2::from_translation(Vec2::splat(50.0))),
                if *entity == modal {
                    InheritedVisibility::HIDDEN
                } else {
                    InheritedVisibility::VISIBLE
                },
            ));
        }
        app.world_mut().insert_resource(UiStack {
            partition: vec![0..stack.len()],
            uinodes: stack,
        });
        app.world_mut()
            .resource_mut::<ButtonInput<MouseButton>>()
            .press(MouseButton::Left);
        app.update();
        assert_eq!(
            app.world().get::<Interaction>(journal),
            Some(&Interaction::Pressed)
        );
        assert_eq!(
            app.world().get::<Interaction>(behind),
            Some(&Interaction::None)
        );
        assert_eq!(app.world().resource::<UiClicks>().0, ["open-journal"]);

        app.world_mut()
            .resource_mut::<ButtonInput<MouseButton>>()
            .reset_all();
        // Os cliques acumulam DURANTE o frame (o luau_update pode correr
        // antes ou depois da UI) e a limpeza é do clear de fim-de-frame —
        // aqui, entre as duas fases, faz-se à mão.
        app.world_mut().resource_mut::<UiClicks>().0.clear();
        app.world_mut()
            .entity_mut(journal)
            .insert(Interaction::None);
        app.world_mut()
            .entity_mut(modal)
            .insert((Visibility::Inherited, InheritedVisibility::VISIBLE));
        app.world_mut()
            .resource_mut::<ButtonInput<MouseButton>>()
            .press(MouseButton::Left);
        app.update();
        assert_eq!(
            app.world().get::<Interaction>(journal),
            Some(&Interaction::None)
        );
        assert!(
            app.world().resource::<UiClicks>().0.is_empty(),
            "open modal blocks the HUD behind it"
        );
    }

    #[test]
    fn test_is_ui_tag_is_case_insensitive() {
        assert!(is_ui_tag("UiPanel"));
        assert!(is_ui_tag("uibar"));
        assert!(!is_ui_tag("HealthBar"));
    }

    #[test]
    fn test_slider_with_inverted_or_non_finite_range_normalizes_instead_of_panicking() {
        // `f32::clamp` asserta `min <= max`: `<UiSlider min="100" max="10">`
        // panica no load do mundo. O parse avisa e ordena os extremos.
        let mut app = tree_app();
        let mut inverted = test_node("uislider", "inverted");
        inverted.attrs.push(("min".into(), "100".into()));
        inverted.attrs.push(("max".into(), "10".into()));
        inverted.attrs.push(("value".into(), "120".into()));
        let entity = build_test_node(&mut app, &inverted);
        let slider = app.world().get::<UiSlider>(entity).unwrap();
        assert_eq!((slider.min, slider.max), (10.0, 100.0));
        assert_eq!(slider.value, 100.0, "o valor autorado cai no intervalo");

        // Não finito cai nos omissões (0..1) em vez de contagiar o intervalo.
        let mut nan = test_node("uislider", "nan");
        nan.attrs.push(("min".into(), "NaN".into()));
        nan.attrs.push(("value".into(), "0.5".into()));
        let entity = build_test_node(&mut app, &nan);
        let slider = app.world().get::<UiSlider>(entity).unwrap();
        assert_eq!((slider.min, slider.max), (0.0, 1.0));
        assert!((slider.value - 0.5).abs() < 1e-6);
    }

    /// Every element in a shipped world file, flattened.
    fn walk(node: &XmlNode, out: &mut Vec<XmlNode>) {
        out.push(node.clone());
        for child in &node.children {
            walk(child, out);
        }
    }

    fn shipped(path: &str) -> Vec<XmlNode> {
        let doc = crate::xml::parse_file(std::path::Path::new(path)).expect("world file parses");
        let mut out = Vec::new();
        for child in &doc.children {
            walk(child, &mut out);
        }
        out
    }

    #[test]
    fn test_shipped_worlds_only_use_known_ui_tags() {
        // The builder skips an unknown tag with its whole subtree, so a typo in
        // a world file silently deletes a panel. This catches it in CI instead.
        let structural = ["uistyle", "uitemplate", "uiempty", "world"];
        for path in [
            "examples/simple-rpg/world/hud.xml",
            "examples/simple-rpg/world/menu.xml",
        ] {
            for node in shipped(path) {
                let tag = node.tag.to_ascii_lowercase();
                if !tag.starts_with("ui") || structural.contains(&tag.as_str()) {
                    continue;
                }
                assert!(is_ui_tag(&tag), "{path}: unknown element <{}>", node.tag);
            }
        }
    }

    #[test]
    fn test_shipped_worlds_bind_only_to_known_names() {
        // A `bind` the engine does not know leaves the element frozen at its
        // authored placeholder — silent, and easy to miss by eye.
        let data = crate::ui::bind::UiData::default();
        let lists = ["quests", "bag", "skills", "shop", "controls", "system"];
        // Binds de SCRIPT: os nomes que os `.lua` do mundo alimentam com
        // `viber.ui.set("nome", …)` literal (barra de chefe, cooldowns das
        // habilidades Lua). Um nome que nenhum script escreve continua a
        // ser apanhado aqui.
        let mut script_binds = std::collections::HashSet::new();
        fn scan(dir: &std::path::Path, out: &mut std::collections::HashSet<String>) {
            let Ok(entries) = std::fs::read_dir(dir) else {
                return;
            };
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    scan(&path, out);
                } else if path.extension().is_some_and(|e| e == "lua") {
                    let source = std::fs::read_to_string(&path).unwrap_or_default();
                    for chunk in source.split("viber.ui.set(\"").skip(1) {
                        if let Some(name) = chunk.split('"').next() {
                            out.insert(name.to_ascii_lowercase());
                        }
                    }
                }
            }
        }
        scan(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("examples/simple-rpg/scripts"),
            &mut script_binds,
        );
        for path in [
            "examples/simple-rpg/world/hud.xml",
            "examples/simple-rpg/world/menu.xml",
        ] {
            for node in shipped(path) {
                let Some(bind) = attr(&node, "bind") else {
                    continue;
                };
                let bind = bind.to_ascii_lowercase();
                // `bind="nome:classe"` é um toggle de classe — o binding
                // escalar é o nome antes de `:` (mesma regra do bind.rs).
                let bind = crate::ui::bind::split_class_bind(&bind)
                    .map(|(name, _)| name.to_string())
                    .unwrap_or(bind);
                // A `<UiList bind>` names a list source, not a scalar binding.
                if node.tag.eq_ignore_ascii_case("uilist") {
                    assert!(
                        lists.contains(&bind.as_str()),
                        "{path}: unknown list `{bind}`"
                    );
                    continue;
                }
                assert!(
                    data.get(&bind).is_some() || script_binds.contains(&bind),
                    "{path}: <{}> binds to unknown `{bind}`",
                    node.tag
                );
            }
        }
    }

    #[test]
    fn test_shipped_menu_pairs_every_tab_button_with_a_page() {
        let nodes = shipped("examples/simple-rpg/world/menu.xml");
        let mut buttons = Vec::new();
        let mut pages = Vec::new();
        for node in &nodes {
            let Some(group) = attr(node, "tab-group") else {
                continue;
            };
            let tab = attr(node, "tab").unwrap_or_default().to_string();
            assert!(!tab.is_empty(), "tab-group without a tab name");
            if node.tag.eq_ignore_ascii_case("uibutton") {
                buttons.push((group.to_string(), tab));
            } else {
                pages.push((group.to_string(), tab));
            }
        }
        assert!(!buttons.is_empty(), "the menu has tabs");
        // A button with no page shows an empty menu; a page with no button is
        // unreachable. Both are authoring mistakes worth failing on.
        for entry in &buttons {
            assert!(pages.contains(entry), "tab {entry:?} has no page");
        }
        for entry in &pages {
            assert!(buttons.contains(entry), "page {entry:?} has no tab button");
        }
    }

    #[test]
    fn test_shipped_lists_carry_a_template() {
        for node in shipped("examples/simple-rpg/world/menu.xml") {
            if !node.tag.eq_ignore_ascii_case("uilist") {
                continue;
            }
            assert!(
                node.children.iter().any(|c| is_template_tag(&c.tag)),
                "<UiList bind=\"{}\"> has no <UiTemplate>",
                attr(&node, "bind").unwrap_or_default()
            );
        }
    }

    #[test]
    fn test_slider_normalizes_a_garbled_range_instead_of_panicking() {
        let mut app = tree_app();
        let mut node = test_node("uislider", "inverted");
        node.attrs.push(("min".into(), "100".into()));
        node.attrs.push(("max".into(), "0".into()));
        node.attrs.push(("value".into(), "50".into()));
        let entity = build_test_node(&mut app, &node);
        let slider = app
            .world()
            .get::<UiSlider>(entity)
            .expect("slider built with an inverted range");
        assert!(slider.min <= slider.max);
        assert!((slider.min - 0.0).abs() < 1e-6);
        assert!((slider.max - 100.0).abs() < 1e-6);
        assert!((slider.value - 50.0).abs() < 1e-6);
        // NaN bounds fall back to the default 0..1 instead of poisoning clamp.
        let mut node = test_node("uislider", "nan-range");
        node.attrs.push(("min".into(), "nan".into()));
        let entity = build_test_node(&mut app, &node);
        let slider = app.world().get::<UiSlider>(entity).expect("slider built");
        assert!(slider.min.is_finite() && slider.max.is_finite());
        assert!(slider.min <= slider.max);
    }

    #[test]
    fn test_is_truthy_accepts_the_usual_spellings() {
        for yes in ["1", "true", "TRUE", "yes", "on"] {
            assert!(is_truthy(yes), "{yes}");
        }
        for no in ["0", "false", "no", ""] {
            assert!(!is_truthy(no), "{no}");
        }
    }
}
