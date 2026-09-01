//! Economia & inventário (loop 4 do port simple-rpg) — o análogo nativo do
//! RpgVault + hotbar de consumíveis do VibeGame:
//!
//! - **[`Vault`]**: ouro/madeira/pedra + itens com stack (`potion`, `antidote`,
//!   `bomb`, materiais de quest). Alimentado por colheita (`tree.lua`/
//!   `rock.lua` via `viber.report_collect`), baús (`item_add`/`vault_add`),
//!   recompensas de quest e compras do mercador (loop 5).
//! - **ResourceChips vivos**: os chips `chip:gold|wood|stone` do HUD mostram
//!   os valores reais do vault.
//! - **Hotbar** `[1]` poção (cura 50) / `[2]` antídoto (limpa veneno) com
//!   contagens do inventário.
//! - **Quests collect**: o progresso lê o VAULT (entregar consome os itens) —
//!   ver `quests.rs`.
//!
//! Hooks Luau: `vault_get/vault_add/item_add/item_count`.

use std::collections::HashMap;

use bevy::prelude::*;

use crate::feedback::DamageNumberEvent;
use crate::luau::ScriptToast;
use crate::player::Player;
use crate::vitals::Health;

/// Cura da poção (HP) — VibeGame potion 50.
pub const POTION_HEAL: f32 = 50.0;
/// Cooldown entre usos da hotbar (s).
pub const HOTBAR_COOLDOWN: f32 = 1.0;

// ── vault ───────────────────────────────────────────────────────────────

/// Recursos + inventário do herói.
#[derive(Debug, Clone, Resource, Default)]
pub struct Vault {
    pub gold: u32,
    pub wood: u32,
    pub stone: u32,
    /// Itens com stack: `potion`, `antidote`, `bomb`, materiais…
    pub items: HashMap<String, u32>,
}

impl Vault {
    /// Deposita num recurso nomeado (`gold`/`wood`/`stone`).
    pub fn add_resource(&mut self, kind: &str, amount: u32) -> bool {
        match kind {
            "gold" => self.gold = self.gold.saturating_add(amount),
            "wood" => self.wood = self.wood.saturating_add(amount),
            "stone" => self.stone = self.stone.saturating_add(amount),
            _ => return false,
        }
        true
    }

    /// Contagem de um recurso nomeado.
    pub fn resource(&self, kind: &str) -> u32 {
        match kind {
            "gold" => self.gold,
            "wood" => self.wood,
            "stone" => self.stone,
            _ => 0,
        }
    }

    /// Adiciona `amount` unidades de um item (max stack 99, como no TS).
    pub fn item_add(&mut self, id: &str, amount: u32) {
        let entry = self.items.entry(normalize_item(id)).or_default();
        *entry = (*entry + amount).min(99);
    }

    pub fn item_count(&self, id: &str) -> u32 {
        self.items.get(&normalize_item(id)).copied().unwrap_or(0)
    }

    /// Consome 1 unidade do item; `false` sem stock.
    pub fn item_take(&mut self, id: &str) -> bool {
        let key = normalize_item(id);
        match self.items.get_mut(&key) {
            Some(count) if *count > 0 => {
                *count -= 1;
                true
            }
            _ => false,
        }
    }

    /// Contagem unificada: recursos nomeados ou itens.
    pub fn count(&self, kind: &str) -> u32 {
        match kind {
            "gold" | "wood" | "stone" => self.resource(kind),
            other => self.item_count(other),
        }
    }

    /// Consome `amount` unidades de um recurso OU item (unificado para os
    /// objetivos collect das quests); `false` sem stock suficiente.
    pub fn take(&mut self, kind: &str, amount: u32) -> bool {
        if matches!(kind, "gold" | "wood" | "stone") {
            if self.resource(kind) >= amount {
                match kind {
                    "gold" => self.gold -= amount,
                    "wood" => self.wood -= amount,
                    _ => self.stone -= amount,
                }
                return true;
            }
            return false;
        }
        self.item_take_amount(kind, amount)
    }

    /// Consome `amount` unidades; `false` sem stock suficiente.
    pub fn item_take_amount(&mut self, id: &str, amount: u32) -> bool {
        let key = normalize_item(id);
        match self.items.get(&key) {
            Some(count) if *count >= amount => {
                self.items.insert(key, count - amount);
                true
            }
            _ => false,
        }
    }
}

/// Normaliza ids de item (`"Potion"` → `potion`).
pub fn normalize_item(raw: &str) -> String {
    raw.trim().to_lowercase()
}

// ── markers de UI ───────────────────────────────────────────────────────

/// Slot da hotbar ([1] poção / [2] antídoto).
#[derive(Component)]
struct HotbarSlot {
    item: &'static str,
    key_hint: &'static str,
    label: &'static str,
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct EconomyPlugin;

impl Plugin for EconomyPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<Vault>()
            .add_systems(Startup, spawn_hotbar)
            .add_systems(
                Update,
                (
                    hotbar_use_system,
                    hotbar_sync_system,
                    vault_chips_system,
                    debug_give_system,
                ),
            );
    }
}

// ── hotbar ──────────────────────────────────────────────────────────────

fn spawn_hotbar(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                bottom: Val::Px(52.0),
                left: Val::Px(12.0),
                flex_direction: FlexDirection::Column,
                row_gap: Val::Px(4.0),
                ..Default::default()
            },
            Name::new("fx:hotbar"),
        ))
        .with_children(|bar| {
            for slot in [
                HotbarSlot {
                    item: "potion",
                    key_hint: "[1]",
                    label: "Poção",
                },
                HotbarSlot {
                    item: "antidote",
                    key_hint: "[2]",
                    label: "Antídoto",
                },
            ] {
                bar.spawn((
                    Node {
                        flex_direction: FlexDirection::Row,
                        column_gap: Val::Px(6.0),
                        padding: UiRect::axes(Val::Px(8.0), Val::Px(4.0)),
                        border_radius: BorderRadius::all(Val::Px(9.0)),
                        ..Default::default()
                    },
                    BackgroundColor(Color::srgba(0.08, 0.08, 0.07, 0.8)),
                    Name::new(format!("hotbar:{}", slot.item)),
                    slot,
                ))
                .with_children(|slot_node| {
                    slot_node.spawn((
                        Text::new(""),
                        TextColor(Color::srgba(0.95, 0.93, 0.85, 0.9)),
                        TextFont::from_font_size(13.0),
                        HotbarCount,
                    ));
                });
            }
        });
}

#[derive(Component)]
struct HotbarCount;

/// `[1]/[2]`: usar poção (cura 50) / antídoto (limpa veneno).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn hotbar_use_system(
    keys: Res<ButtonInput<KeyCode>>,
    mut cooldown: Local<f32>,
    mut players: Query<(&mut Health, Option<&mut crate::feedback::StatusEffects>), With<Player>>,
    mut vault: ResMut<Vault>,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut toasts: MessageWriter<ScriptToast>,
    transforms: Query<&GlobalTransform, With<Player>>,
    time: Res<Time>,
) {
    *cooldown -= time.delta_secs();
    if *cooldown > 0.0 {
        return;
    }
    let potion = keys.just_pressed(KeyCode::Digit1);
    let antidote = keys.just_pressed(KeyCode::Digit2);
    if !potion && !antidote {
        return;
    }
    let (item, label) = if potion {
        ("potion", "Poção")
    } else {
        ("antidote", "Antídoto")
    };
    if !vault.item_take(item) {
        toasts.write(ScriptToast(format!("{label}: sem stock no inventário")));
        *cooldown = HOTBAR_COOLDOWN;
        return;
    }
    *cooldown = HOTBAR_COOLDOWN;
    let Ok((mut health, effects)) = players.single_mut() else {
        return;
    };
    if potion {
        let healed = POTION_HEAL.min(health.max - health.current);
        health.current = (health.current + POTION_HEAL).min(health.max);
        if let Ok(transform) = transforms.single() {
            numbers.write(DamageNumberEvent {
                position: transform.translation() + Vec3::Y * 1.9,
                text: format!("+{}", healed.round() as i32),
                color: Color::srgb(0.4, 1.0, 0.45),
            });
        }
        toasts.write(ScriptToast(format!("Poção usada (+{} HP)", healed.round() as i32)));
    } else if let Some(mut effects) = effects {
        if effects.venom > 0.0 {
            effects.venom = 0.0;
            toasts.write(ScriptToast("Antídoto: veneno neutralizado".into()));
        } else {
            toasts.write(ScriptToast("Antídoto: nada a neutralizar".into()));
        }
    }
}

/// Debug de QA (**F10**): dá recursos/itens (análogo do debug action
/// `give`/`gold` do VibeGame) — 10 ouro, 6 madeira, 6 pedra, poção e antídoto.
fn debug_give_system(
    keys: Res<ButtonInput<KeyCode>>,
    mut vault: ResMut<Vault>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    if !keys.just_pressed(KeyCode::F10) {
        return;
    }
    vault.add_resource("gold", 10);
    vault.add_resource("wood", 6);
    vault.add_resource("stone", 6);
    vault.item_add("potion", 1);
    vault.item_add("antidote", 1);
    toasts.write(ScriptToast("QA: +10 ouro, +6 madeira, +6 pedra, poção, antídoto".into()));
}

/// Contagens da hotbar em tempo real.
fn hotbar_sync_system(
    vault: Res<Vault>,
    slots: Query<(&HotbarSlot, &Children)>,
    mut counts: Query<&mut Text, With<HotbarCount>>,
) {
    if !vault.is_changed() {
        return;
    }
    for (slot, children) in &slots {
        for child in children.iter() {
            if let Ok(mut text) = counts.get_mut(child) {
                let wanted = format!(
                    "{} {} x{}",
                    slot.key_hint,
                    slot.label,
                    vault.item_count(slot.item)
                );
                if text.0 != wanted {
                    text.0 = wanted;
                }
            }
        }
    }
}

/// Chips `chip:gold|wood|stone` do HUD mostram o vault real.
fn vault_chips_system(
    vault: Res<Vault>,
    chips: Query<(&Name, &Children)>,
    mut texts: Query<&mut Text>,
) {
    if !vault.is_changed() {
        return;
    }
    for (name, children) in &chips {
        let name_str = name.to_string();
        let Some(resource) = name_str.strip_prefix("chip:") else {
            continue;
        };
        let wanted = vault.resource(resource).to_string();
        for child in children.iter() {
            if let Ok(mut text) = texts.get_mut(child) {
                if text.0 != wanted {
                    text.0 = wanted.clone();
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vault_resources() {
        let mut vault = Vault::default();
        assert!(vault.add_resource("gold", 80));
        assert!(vault.add_resource("wood", 6));
        assert!(vault.add_resource("wood", 1));
        assert!(!vault.add_resource("diamond", 1));
        assert_eq!(vault.gold, 80);
        assert_eq!(vault.wood, 7);
        assert_eq!(vault.resource("stone"), 0);
    }

    #[test]
    fn test_items_stack_and_take() {
        let mut vault = Vault::default();
        vault.item_add("Potion", 2);
        vault.item_add("potion", 1);
        assert_eq!(vault.item_count("POTION"), 3, "ids normalizam");
        assert!(vault.item_take("potion"));
        assert_eq!(vault.item_count("potion"), 2);
        assert!(vault.item_take_amount("potion", 2));
        assert_eq!(vault.item_count("potion"), 0);
        assert!(!vault.item_take("potion"), "sem stock");
    }

    #[test]
    fn test_item_stack_cap_99() {
        let mut vault = Vault::default();
        vault.item_add("bomb", 150);
        assert_eq!(vault.item_count("bomb"), 99);
    }

    #[test]
    fn test_chips_names_covered() {
        let mut vault = Vault::default();
        for kind in ["gold", "wood", "stone"] {
            vault.add_resource(kind, 1);
            assert_eq!(vault.resource(kind), 1);
        }
    }
}
