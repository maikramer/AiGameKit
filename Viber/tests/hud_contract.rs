//! The shipped HUD must remain addressable by its script and engine bindings.
use std::{collections::HashSet, path::Path};

use viber::{
    ui::bind::{UiData, split_class_bind},
    xml::{self, XmlNode},
};

fn visit<'a>(nodes: &'a [XmlNode], out: &mut Vec<&'a XmlNode>) {
    for node in nodes {
        out.push(node);
        visit(&node.children, out);
    }
}

#[test]
fn shipped_hud_has_unique_ids_and_valid_bindings() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("examples/simple-rpg");
    let mut ids = HashSet::new();
    for file in ["world/hud.xml", "world/menu.xml", "qa-hud.xml"] {
        let doc = xml::parse_file(&root.join(file)).unwrap();
        let mut nodes = Vec::new();
        visit(&doc.children, &mut nodes);
        for node in nodes {
            if let Some(id) = node.attr("id") {
                assert!(ids.insert(id.to_owned()), "duplicate id {id}");
            }
            if file == "world/hud.xml" {
                if let Some(binding) = node.attr("bind") {
                    // Class-binds (`expr:classe`) resolvem só a parte do
                    // nome — a classe é aplicada, não lida do UiData.
                    let name = match split_class_bind(binding) {
                        Some((name, class)) => {
                            assert!(!class.is_empty(), "class-bind {binding} sem classe");
                            name
                        }
                        None => binding,
                    };
                    assert!(
                        UiData::default().get(name).is_some(),
                        "unknown binding {binding}"
                    );
                }
                if node.tag == "UiIcon" {
                    let src = node.attr("src").expect("icon source");
                    // A mesma resolução da engine (docs/ASSETS.md): a pasta do
                    // mundo primeiro, o pool partilhado como fallback.
                    let rel = src.trim_start_matches('/');
                    let exists = root.join(rel).exists()
                        || viber::meshopt::shared_asset_pool()
                            .is_some_and(|pool| pool.join(rel).exists());
                    assert!(exists, "missing icon {src}");
                }
            }
        }
    }
    for id in [
        "vitals",
        "hp-bar",
        "xp-bar",
        "quest",
        "dock",
        "cd-dash",
        "cd-heal",
        "cd-strike",
        "vial-potion",
        "vial-antidote",
        "vial-bomb",
        "prompt",
        "notification",
        "notification-text",
        "open-journal",
        "menu",
    ] {
        assert!(ids.contains(id), "missing HUD element {id}");
    }
}

#[test]
fn hud_script_opens_journal_and_punches_the_combo() {
    // HUD v3: os toggles de estado são class-binds engine-driven (cobre o
    // `apply_bind_classes` nos testes unitários do bind.rs) — o script só
    // trata do clique do diário e do soco visual do combo.
    let lua = mlua::Lua::new();
    lua.load(
        r#"
        combo = ""
        state = {}
        click = false
        anims = {}
        opened = nil
        viber = {state=function() return state end, ui={
            number=function(key) return 0 end,
            get=function(key) return combo end,
            toggle_class=function() assert(false, "toggles vivem em class-binds engine") end,
            clicked=function(id) return id == "open-journal" and click end,
            open=function(id) opened = id end,
            set_anim=function(id, anim) anims[id] = anim end
        }}
    "#,
    )
    .exec()
    .unwrap();
    lua.load(include_str!("../examples/simple-rpg/scripts/ui/hud.lua"))
        .exec()
        .unwrap();
    lua.load(
        r#"
        on_update(0.016)
        assert(opened == nil)
        assert(anims["combo-text"] == "none")

        click = true
        on_update(0.016)
        assert(opened == "menu")

        combo = "x2"
        on_update(0.016)
        assert(anims["combo-text"] == "shake 0.45 0.5")

        combo = ""
        on_update(0.016)
        assert(anims["combo-text"] == "none")
    "#,
    )
    .exec()
    .unwrap();
}
