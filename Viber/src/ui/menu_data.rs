//! List sources for the menus: quests, bag, skills, shop, controls, system.
//!
//! Each function turns a gameplay resource into rows of plain strings, which is
//! all a `<UiList>` template consumes. Formatting decisions (how a status is
//! spelled, what a price looks like) live here rather than in the XML, so the
//! menu file stays structure and the stylesheet stays presentation.
//!
//! Rows carry a `status`-style field wherever the stylesheet wants to colour a
//! row differently — `class="row {status}"` in the template is the whole
//! mechanism.

use bevy::prelude::*;

use super::list::{ListRow, UiLists};
use crate::economy::Vault;
use crate::quests::{QuestLog, QuestStatus};
use crate::skills::{SKILLS, SkillTree};

/// Human label for a quest status, plus the class the stylesheet keys on.
pub fn quest_status(status: QuestStatus) -> (&'static str, &'static str) {
    match status {
        QuestStatus::NotTaken => ("por aceitar", "pending"),
        QuestStatus::Active => ("em curso", "active"),
        QuestStatus::Ready => ("pronta a entregar", "ready"),
        QuestStatus::Done => ("concluída", "done"),
    }
}

/// Objective phrased for a player: `"caçar 5 wolf"`, not `{kind, target}`.
pub fn objective_text(kind: &str, target: &str, count: u32) -> String {
    match kind {
        "kill" => format!("derrotar {count}× {target}"),
        "collect" => format!("reunir {count}× {target}"),
        "visit" => format!("visitar {count} local(is)"),
        other => format!("{other} {count}× {target}"),
    }
}

/// Item labels; anything unknown falls back to its own id so a new item shows
/// up in the bag instead of vanishing.
pub fn item_label(id: &str) -> String {
    match id {
        "potion" => "Poção".into(),
        "antidote" => "Antídoto".into(),
        "bomb" => "Bomba".into(),
        "gold" => "Ouro".into(),
        "wood" => "Madeira".into(),
        "stone" => "Pedra".into(),
        other => {
            let mut chars = other.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        }
    }
}

/// The controls reference — the list that used to sit on top of the world.
pub const CONTROLS: [(&str, &str); 14] = [
    ("W A S D", "mover"),
    ("SHIFT", "correr"),
    ("ESPAÇO", "saltar"),
    ("RATO ESQ / J", "atacar"),
    ("C", "esquiva (dash)"),
    ("E", "curar"),
    ("R", "golpe forte"),
    ("L", "guarda / aparar"),
    ("1 / 2", "poção / antídoto"),
    ("E", "interagir e falar"),
    ("F", "assinar marco da Nota"),
    ("Q / ESC", "abrir e fechar este diário"),
    ("1 - 6", "separador do diário (com ele aberto)"),
    ("P", "profiler"),
];

/// Refreshes every menu list. Cheap: [`UiLists::set`] only bumps a version
/// when the rows actually differ, and only a bumped version rebuilds elements.
///
/// Gated by [`UiModalsOpen`]: com o diário fechado não há consumidores, e
/// reconstruir 6 listas por frame era trabalho morto. Corre quando algum
/// modal está aberto ou no frame seguinte a qualquer toggle (`is_changed`),
/// que cobre a abertura por tecla — o handler exclusivo alterna o recurso
/// depois de `Last`, por isso o frame em que o modal fica visível já apanha
/// as listas frescas. Abertura por script (`viber.ui.open`, `UiSet::Script`)
/// mostra dados 1 frame velhos; aceitável — o frame seguinte refresca.
#[allow(clippy::type_complexity)]
pub fn collect_menu_lists(
    mut lists: ResMut<UiLists>,
    modals: Res<super::modal::UiModalsOpen>,
    quests: Option<Res<QuestLog>>,
    vault: Option<Res<Vault>>,
    // `SkillTree` é um RESOURCE. Na Bevy 0.19 o derive `Resource` também
    // implementa `Component`, por isso um `Query<&SkillTree>` compila — e não
    // casa nunca, deixando todos os talentos "bloqueados" em silêncio.
    skills: Option<Res<SkillTree>>,
    data: Res<super::bind::UiData>,
) {
    if !modals.any() && !modals.is_changed() {
        return;
    }
    let vault_ref = vault.as_deref();
    lists.set_engine("quests", quest_rows(quests.as_deref(), vault_ref));
    lists.set_engine("bag", bag_rows(vault_ref));
    lists.set_engine("skills", skill_rows(skills.as_deref()));
    lists.set_engine("shop", shop_rows(vault_ref));
    lists.set_engine("controls", control_rows());
    lists.set_engine("system", system_rows(&data));
}

/// Active quests first, then the ones still to accept, then the finished ones —
/// a player opening the log wants the thing they are doing at the top.
pub fn quest_rows(quests: Option<&QuestLog>, vault: Option<&Vault>) -> Vec<ListRow> {
    let Some(quests) = quests else {
        return Vec::new();
    };
    let mut rows: Vec<(u8, ListRow)> = quests
        .defs
        .iter()
        .map(|def| {
            let status = quests.status(&def.id, vault);
            let (label, class) = quest_status(status);
            let progress_text = quests.progress_text(&def.id, vault);
            let order = match status {
                QuestStatus::Ready => 0,
                QuestStatus::Active => 1,
                QuestStatus::NotTaken => 2,
                QuestStatus::Done => 3,
            };
            let progress = match status {
                QuestStatus::Done => 1.0,
                _ => super::collect::progress_fraction(&progress_text),
            };
            let row = UiLists::row([
                ("id", def.id.clone()),
                ("title", def.title.clone()),
                ("npc", def.npc.clone()),
                ("biome", def.biome.clone()),
                ("status", class.to_string()),
                ("status_text", label.to_string()),
                (
                    "objective",
                    objective_text(
                        &def.objective.kind,
                        &def.objective.target,
                        def.objective.count,
                    ),
                ),
                ("progress", format!("{progress:.4}")),
                ("progress_text", progress_text),
            ]);
            (order, row)
        })
        .collect();
    rows.sort_by_key(|(order, _)| *order);
    rows.into_iter().map(|(_, row)| row).collect()
}

/// Resources first (they always exist), then whatever stacks the vault holds.
pub fn bag_rows(vault: Option<&Vault>) -> Vec<ListRow> {
    let Some(vault) = vault else {
        return Vec::new();
    };
    let mut rows: Vec<ListRow> = [
        ("gold", vault.gold),
        ("wood", vault.wood),
        ("stone", vault.stone),
    ]
    .into_iter()
    .map(|(id, count)| bag_row(id, count, "resource"))
    .collect();
    // Deterministic order: a bag that reshuffles itself every frame is unusable
    // (and would rebuild the list on every version bump).
    let mut items: Vec<(&String, &u32)> = vault.items.iter().collect();
    items.sort_by(|a, b| a.0.cmp(b.0));
    rows.extend(
        items
            .into_iter()
            .filter(|(_, count)| **count > 0)
            .map(|(id, count)| bag_row(id, *count, "item")),
    );
    rows
}

fn bag_row(id: &str, count: u32, kind: &str) -> ListRow {
    UiLists::row([
        ("id", id.to_string()),
        ("label", item_label(id)),
        ("count", count.to_string()),
        ("kind", kind.to_string()),
        (
            "status",
            if count == 0 {
                "empty".into()
            } else {
                String::new()
            },
        ),
    ])
}

/// The passive tree, flagged learned / available / locked.
pub fn skill_rows(tree: Option<&SkillTree>) -> Vec<ListRow> {
    SKILLS
        .iter()
        .map(|def| {
            let (status, status_text) = match tree {
                Some(tree) if tree.learned.iter().any(|l| l == def.id) => ("done", "aprendida"),
                Some(tree) if tree.can_learn(def.id) => ("ready", "disponível"),
                _ => ("locked", "bloqueada"),
            };
            UiLists::row([
                ("id", def.id.to_string()),
                ("label", def.label.to_string()),
                ("status", status.to_string()),
                ("status_text", status_text.to_string()),
                (
                    "requires",
                    if def.requires.is_empty() {
                        "sem requisitos".to_string()
                    } else {
                        def.requires.join(", ")
                    },
                ),
            ])
        })
        .collect()
}

/// The merchant's catalogue, with affordability precomputed so the stylesheet
/// can grey out what the player cannot buy.
pub fn shop_rows(vault: Option<&Vault>) -> Vec<ListRow> {
    let gold = vault.map(|v| v.gold).unwrap_or(0);
    crate::menus::shop_catalog()
        .into_iter()
        .map(|(label, price, item, qty)| {
            let selling = price < 0;
            let affordable = selling || gold as i32 >= price;
            UiLists::row([
                ("id", item.to_string()),
                ("label", label.to_string()),
                ("qty", qty.to_string()),
                (
                    "price",
                    if selling {
                        format!("+{}", -price)
                    } else {
                        price.to_string()
                    },
                ),
                (
                    "status",
                    if affordable {
                        "ready".into()
                    } else {
                        "locked".to_string()
                    },
                ),
            ])
        })
        .collect()
}

pub fn control_rows() -> Vec<ListRow> {
    CONTROLS
        .iter()
        .map(|(key, action)| {
            UiLists::row([("key", key.to_string()), ("action", action.to_string())])
        })
        .collect()
}

/// The "system" tab: what build this is and what the world is doing.
pub fn system_rows(data: &super::bind::UiData) -> Vec<ListRow> {
    [
        ("Engine", format!("Viber {}", env!("CARGO_PKG_VERSION"))),
        ("Render", "Bevy 0.19 · Rapier 3D".to_string()),
        ("Mundo", "simple-rpg".to_string()),
        ("Hora", data.clock.clone()),
        ("Nível", data.level.to_string()),
        (
            "Vida",
            format!(
                "{}/{}",
                data.health.max(0.0).round(),
                data.health_max.round()
            ),
        ),
    ]
    .into_iter()
    .map(|(key, value)| UiLists::row([("key", key.to_string()), ("value", value)]))
    .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_objective_text_reads_like_a_sentence() {
        assert_eq!(objective_text("kill", "wolf", 5), "derrotar 5× wolf");
        assert_eq!(objective_text("collect", "wood", 3), "reunir 3× wood");
        assert_eq!(
            objective_text("visit", "shrine ruins", 2),
            "visitar 2 local(is)"
        );
        // An objective kind nobody taught us still renders something useful.
        assert_eq!(objective_text("escort", "npc", 1), "escort 1× npc");
    }

    #[test]
    fn test_item_label_falls_back_to_a_capitalised_id() {
        assert_eq!(item_label("potion"), "Poção");
        assert_eq!(item_label("mithril"), "Mithril");
        assert_eq!(item_label(""), "");
    }

    #[test]
    fn test_bag_rows_always_list_resources_and_hide_empty_stacks() {
        let mut vault = Vault {
            gold: 12,
            wood: 0,
            ..Default::default()
        };
        vault.items.insert("potion".into(), 2);
        vault.items.insert("bomb".into(), 0);
        let rows = bag_rows(Some(&vault));
        let ids: Vec<&str> = rows.iter().map(|r| r["id"].as_str()).collect();
        // Resources are always present, even at zero, because they are the
        // player's economy; an item stack at zero is just gone.
        assert_eq!(ids, vec!["gold", "wood", "stone", "potion"]);
        let wood = rows.iter().find(|r| r["id"] == "wood").unwrap();
        assert_eq!(wood["status"], "empty");
        assert_eq!(rows[0]["count"], "12");
    }

    #[test]
    fn test_bag_rows_are_stable_across_calls() {
        // A HashMap iteration order leaking into the UI would rebuild the list
        // every frame and make the bag flicker.
        let mut vault = Vault::default();
        for item in ["potion", "antidote", "bomb", "amber", "rope"] {
            vault.items.insert(item.into(), 1);
        }
        let first = bag_rows(Some(&vault));
        for _ in 0..8 {
            assert_eq!(bag_rows(Some(&vault)), first);
        }
    }

    #[test]
    fn test_skill_rows_flag_learned_available_and_locked() {
        let tree = SkillTree {
            learned: vec![SKILLS[0].id.to_string()],
            points: 1,
        };
        let rows = skill_rows(Some(&tree));
        assert_eq!(rows.len(), SKILLS.len());
        let learned = rows.iter().find(|r| r["id"] == SKILLS[0].id).unwrap();
        assert_eq!(learned["status"], "done");
        // With a point in hand, a requirement-free skill reads as available.
        let free = rows
            .iter()
            .find(|r| r["id"] != SKILLS[0].id && r["requires"] == "sem requisitos")
            .expect("a second free skill");
        assert_eq!(free["status"], "ready");
        // No tree at all: nothing is learnable.
        assert!(skill_rows(None).iter().all(|r| r["status"] == "locked"));
    }

    #[test]
    fn test_shop_rows_mark_what_the_player_cannot_afford() {
        let broke = shop_rows(Some(&Vault::default()));
        let potion = broke.iter().find(|r| r["id"] == "potion").unwrap();
        assert_eq!(potion["status"], "locked");
        // Selling is always possible, price shown as a gain.
        let wood = broke.iter().find(|r| r["id"] == "wood").unwrap();
        assert_eq!(wood["status"], "ready");
        assert!(wood["price"].starts_with('+'));
        let rich = shop_rows(Some(&Vault {
            gold: 999,
            ..Default::default()
        }));
        assert_eq!(
            rich.iter().find(|r| r["id"] == "potion").unwrap()["status"],
            "ready"
        );
    }

    #[test]
    fn test_quest_rows_put_the_actionable_ones_first() {
        let quests = QuestLog::with_dir(&crate::quests::example_quests_dir());
        let rows = quest_rows(Some(&quests), None);
        assert!(!rows.is_empty(), "the example ships quests");
        // Nothing accepted yet, so everything is pending — and every row has
        // the fields the template substitutes.
        for row in &rows {
            for field in [
                "id",
                "title",
                "status",
                "objective",
                "progress",
                "progress_text",
            ] {
                assert!(row.contains_key(field), "row missing `{field}`");
            }
        }
        assert!(rows.iter().all(|r| r["status"] == "pending"));
        assert!(quest_rows(None, None).is_empty());
    }

    #[test]
    fn test_control_rows_cover_the_whole_reference() {
        let rows = control_rows();
        assert_eq!(rows.len(), CONTROLS.len());
        assert!(rows.iter().any(|r| r["action"] == "mover"));
    }
}
