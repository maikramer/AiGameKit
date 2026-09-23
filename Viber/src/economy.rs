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
use crate::profiler::{Group, timed};
use crate::vitals::Health;

/// Cura da poção (HP) — VibeGame potion 50.
pub const POTION_HEAL: f32 = 50.0;
/// Cooldown entre usos da hotbar (s).
pub const HOTBAR_COOLDOWN: f32 = 1.0;
/// Ouro inicial do herói (onboarding — nascia com 0 g, sem conseguir sequer
/// comprar uma poção de 25 g).
pub const STARTING_GOLD: u32 = 30;
/// Poções iniciais do herói: 1 uso imediato da hotbar [1].
pub const STARTING_POTIONS: u32 = 1;

/// Teto de uma pilha de item no inventário.
pub const MAX_ITEM_STACK: u32 = 99;

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

    /// Adiciona `amount` unidades de um item (max stack [`MAX_ITEM_STACK`],
    /// como no TS).
    pub fn item_add(&mut self, id: &str, amount: u32) {
        let entry = self.items.entry(normalize_item(id)).or_default();
        // saturating: um entry alto vindo de save corrompido não pode dar
        // overflow (panic em dev/test, wrap em release) antes do `.min`.
        *entry = entry.saturating_add(amount).min(MAX_ITEM_STACK);
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

// ── plugin ──────────────────────────────────────────────────────────────

/// Kit de arranque do herói: [`STARTING_GOLD`] de ouro + [`STARTING_POTIONS`]
/// poção. Vive num sistema Startup (NÃO em `Vault::default()`) para o save
/// antigo carregado não voltar a receber o kit — o `apply_save` escreve por
/// cima de qualquer valor aqui depositado.
pub fn apply_starting_kit(vault: &mut Vault) {
    vault.add_resource("gold", STARTING_GOLD);
    vault.item_add("potion", STARTING_POTIONS);
}

fn starting_kit_system(mut vault: ResMut<Vault>) {
    apply_starting_kit(&mut vault);
}

pub struct EconomyPlugin;

impl Plugin for EconomyPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<Vault>()
            // Idempotente com o Ambient/Combat (apps mínimas auto-suficientes).
            .add_message::<crate::ambient::SfxEvent>()
            .add_systems(Startup, starting_kit_system)
            .add_systems(
                Update,
                (
                    timed(Group::World, hotbar_use_system),
                    timed(Group::World, vault_chips_system),
                    vault_loot_sfx_system,
                    debug_give_system,
                ),
            );
    }
}

/// SFX de loot (passe de juice r1): o vault CRESCER toca o clip de loot —
/// uma vez por frame de alteração, de QUALQUER fonte (colheita, baús,
/// recompensas, debug, scripts), porque vigia o total do vault e não os
/// caminhos de entrada. A primeira observação (kit de arranque, save
/// carregado) não fanfarra.
fn vault_loot_sfx_system(
    vault: Res<Vault>,
    mut previous: Local<Option<u64>>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
) {
    if !vault.is_changed() {
        return;
    }
    let total: u64 = vault.gold as u64
        + vault.wood as u64
        + vault.stone as u64
        + vault.items.values().map(|&n| n as u64).sum::<u64>();
    match *previous {
        None => *previous = Some(total),
        Some(prev) => {
            if total > prev {
                sfx.write(crate::ambient::SfxEvent {
                    clip: crate::ambient::SfxClip::Loot,
                    position: None,
                });
            }
            *previous = Some(total);
        }
    }
}

// ── hotbar ──────────────────────────────────────────────────────────────
// As linhas de texto ("[1] Poção x0", "[2] Antídoto x0") saíram do ecrã: as
// contagens são agora pips do HUD declarativo, ligados aos bindings `potion`
// e `antidote` (`src/ui/bind.rs`). As teclas continuam a ser [1] e [2] — o
// jogador vê o stock, não a legenda.

/// `[1]/[2]`: usar poção (cura 50) / antídoto (limpa veneno).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn hotbar_use_system(
    keys: Res<ButtonInput<KeyCode>>,
    menus: Option<Res<crate::menus::MenusOpen>>,
    mut cooldown: Local<f32>,
    mut players: Query<(&mut Health, Option<&mut crate::feedback::StatusEffects>), With<Player>>,
    mut vault: ResMut<Vault>,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut toasts: MessageWriter<ScriptToast>,
    transforms: Query<&GlobalTransform, With<Player>>,
    time: Res<Time>,
    owners: Option<Res<crate::luau::ScriptSystemOwners>>,
) {
    // Reclamado por script (`viber.own_system("hotbar")`).
    if owners
        .as_deref()
        .is_some_and(|o| o.owns(crate::luau::ownership::SYSTEM_HOTBAR))
    {
        return;
    }
    *cooldown -= time.delta_secs();
    if *cooldown > 0.0 {
        return;
    }
    let potion = keys.just_pressed(KeyCode::Digit1);
    let antidote = keys.just_pressed(KeyCode::Digit2);
    if !potion && !antidote {
        return;
    }
    // menus abertos roubam as teclas numéricas
    if menus.is_some_and(|m| m.any()) {
        return;
    }
    // Ctrl/Alt+dígito são atalhos (ex. `<PostFxDebugToggle>` em Ctrl+1..6),
    // não uso de item.
    if keys.any_pressed([
        KeyCode::ControlLeft,
        KeyCode::ControlRight,
        KeyCode::AltLeft,
        KeyCode::AltRight,
    ]) {
        return;
    }
    let (item, label) = if potion {
        ("potion", "Poção")
    } else {
        ("antidote", "Antídoto")
    };
    // O player vem PRIMEIRO: sem herói (ausente/Disabled) o item não pode
    // ser consumido sem efeito nenhum.
    let Ok((mut health, effects)) = players.single_mut() else {
        return;
    };
    if !vault.item_take(item) {
        toasts.write(ScriptToast(format!("{label}: sem stock no inventário")));
        *cooldown = HOTBAR_COOLDOWN;
        return;
    }
    *cooldown = HOTBAR_COOLDOWN;
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
        toasts.write(ScriptToast(format!(
            "Poção usada (+{} HP)",
            healed.round() as i32
        )));
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
    vault.item_add("bomb", 1);
    toasts.write(ScriptToast(
        "QA: +10 ouro, +6 madeira, +6 pedra, poção, antídoto, bomba".into(),
    ));
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

    #[test]
    fn test_hotbar_ignores_ctrl_digit_shortcuts() {
        let mut app = App::new();
        app.init_resource::<ButtonInput<KeyCode>>()
            .init_resource::<Time>()
            .init_resource::<Vault>()
            .add_message::<DamageNumberEvent>()
            .add_message::<ScriptToast>()
            .add_systems(Update, hotbar_use_system);
        app.world_mut().resource_mut::<Vault>().item_add("potion", 2);
        app.world_mut().spawn((
            Player::default(),
            Health {
                current: 10.0,
                max: 100.0,
            },
            GlobalTransform::default(),
        ));
        {
            let mut keys = app.world_mut().resource_mut::<ButtonInput<KeyCode>>();
            keys.press(KeyCode::ControlLeft);
            keys.press(KeyCode::Digit1);
        }
        app.update();
        assert_eq!(
            app.world().resource::<Vault>().item_count("potion"),
            2,
            "Ctrl+1 é atalho (postfx), não poção"
        );
        {
            let mut keys = app.world_mut().resource_mut::<ButtonInput<KeyCode>>();
            keys.release(KeyCode::ControlLeft);
            keys.release(KeyCode::Digit1);
            keys.clear();
            keys.press(KeyCode::Digit1);
        }
        app.update();
        assert_eq!(app.world().resource::<Vault>().item_count("potion"), 1);
    }

    #[test]
    fn test_starting_kit_gives_gold_and_potion() {
        // O Vault::default() continua VAZIO (o kit é do boot, não do tipo —
        // senão carregar um save antigo voltava a dar 30 g).
        let mut vault = Vault::default();
        assert_eq!(vault.gold, 0);
        assert_eq!(vault.item_count("potion"), 0);
        apply_starting_kit(&mut vault);
        assert_eq!(vault.gold, STARTING_GOLD);
        assert_eq!(vault.item_count("potion"), STARTING_POTIONS);
    }
}
