//! UI & menus (loop 5 do port simple-rpg) — o análogo nativo do
//! TabbedModal + loja do mercador + toasts + loading screen do VibeGame:
//!
//! - **Toasts visuais**: pilha no topo-centro (5 máx.), fade e despawn —
//!   continuam a ir para o log da bridge.
//! - **Modal [Q]** com tabs reais: Quests (ativas/feitas do [`QuestLog`]),
//!   Inventário (vault) e Ajuda (controlos + opções). ←/→ ou TAB troca de
//!   tab, [Q]/Esc fecha.
//! - **Loja [K]**: perto do `name="merchant"` — comprar poção/antídoto/
//!   bomba, vender madeira/pedra, com seleção ←/→ e confirmação [J].
//! - **Loading screen**: overlay "a forjar o mundo" que se levanta quando a
//!   engine arranca (representativo — GLBs continuam a streamar).
//!
//! Enquanto o modal ou a loja estão abertos, a hotbar ([1]/[2]) não consome
//! ([`MenusOpen`]).

use bevy::prelude::*;

use crate::economy::Vault;
use crate::luau::ScriptToast;
use crate::player::Player;
use crate::quests::QuestLog;

/// Toasts visíveis em simultâneo.
pub const TOAST_CAP: usize = 5;
/// Vida de um toast (s).
pub const TOAST_LIFETIME: f32 = 3.0;
/// Alcance da loja ao mercador (m).
pub const SHOP_RANGE_M: f32 = 5.0;

/// Catálogo da loja: (rótulo, preço em ouro — negativo = vende, item/outra
/// chave de vault, quantidade).
pub fn shop_catalog() -> Vec<(&'static str, i32, &'static str, u32)> {
    vec![
        ("Comprar poção", 25, "potion", 1),
        ("Comprar antídoto", 20, "antidote", 1),
        ("Comprar bomba", 40, "bomb", 1),
        ("Vender madeira", -3, "wood", 1),
        ("Vender pedra", -5, "stone", 1),
    ]
}

/// Abre/fecha o estado dos menus que roubam input à hotbar.
#[derive(Debug, Clone, Resource, Default)]
pub struct MenusOpen {
    pub modal: bool,
    pub shop: bool,
}

impl MenusOpen {
    pub fn any(&self) -> bool {
        self.modal || self.shop
    }
}

/// Tab do modal atual (0=Quests, 1=Inventário, 2=Ajuda).
#[derive(Debug, Clone, Copy, Resource, Default, PartialEq)]
pub struct ModalTab(pub usize);

pub const TAB_COUNT: usize = 3;

/// Próxima tab (cíclica); pura para os testes.
pub fn next_tab(current: usize, delta: i32) -> usize {
    let count = TAB_COUNT as i32;
    ((current as i32 + delta).rem_euclid(count)) as usize
}

// ── componentes ─────────────────────────────────────────────────────────

#[derive(Component)]
struct ModalRoot;

#[derive(Component)]
struct ModalContent;

#[derive(Component)]
struct ShopRoot;

#[derive(Component)]
struct ShopContent;

#[derive(Component)]
struct LoadingScreen;

#[derive(Component)]
struct ToastPill {
    timer: f32,
}

#[derive(Component)]
struct ToastContainer;

/// Empilha o container de toasts no topo-centro (uma única vez).
fn spawn_toast_container(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            top: Val::Px(44.0),
            left: Val::Px(0.0),
            right: Val::Px(0.0),
            flex_direction: FlexDirection::Column,
            align_items: AlignItems::Center,
            row_gap: Val::Px(4.0),
            ..Default::default()
        },
        Name::new("ui:toasts"),
        ToastContainer,
    ));
}

#[derive(Component)]
struct CampfireBanner;

// ── plugin ──────────────────────────────────────────────────────────────

pub struct MenusPlugin;

impl Plugin for MenusPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<MenusOpen>()
            .init_resource::<ModalTab>()
            .add_message::<ToastSpawned>()
            .add_systems(
                Startup,
                (
                    spawn_loading_screen,
                    spawn_campfire_banner,
                    spawn_toast_container,
                    spawn_modal,
                    spawn_shop,
                ),
            )
            .add_systems(
                Update,
                (
                    toast_display_system,
                    toast_fade_system,
                    modal_toggle_system,
                    modal_content_system,
                    shop_system,
                    campfire_banner_system,
                    loading_hide_system,
                ),
            );
    }
}

/// Evento interno: toast a mostrar (o HUD/log espelham).
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct ToastSpawned {
    pub text: String,
}

// ── toasts ──────────────────────────────────────────────────────────────

/// Lê `ScriptToast`, espelha no log e spawna a pílula visual (cap
/// [`TOAST_CAP`]: a mais antiga é removida).
fn toast_display_system(
    mut toasts: MessageReader<ScriptToast>,
    mut spawned: MessageWriter<ToastSpawned>,
    active: Query<(), With<ToastPill>>,
    container: Query<Entity, With<ToastContainer>>,
    mut commands: Commands,
) {
    for toast in toasts.read() {
        info!(target: "viber::toast", "{}", toast.0);
        spawned.write(ToastSpawned {
            text: toast.0.clone(),
        });
        if active.iter().count() >= TOAST_CAP {
            continue; // pilha cheia: o mais antigo ainda está a desvanecer
        }
        let Ok(container) = container.single() else {
            continue;
        };
        commands.entity(container).with_children(|wrap_parent| {
            wrap_parent
                .spawn((
                    Node {
                        padding: UiRect::axes(Val::Px(12.0), Val::Px(6.0)),
                        border_radius: BorderRadius::all(Val::Px(10.0)),
                        ..Default::default()
                    },
                    BackgroundColor(Color::srgba(0.10, 0.09, 0.07, 0.92)),
                    Name::new("ui:toast"),
                ))
                .with_children(|pill| {
                    pill.spawn((
                        Text::new(toast.0.clone()),
                        TextColor(Color::srgb(0.96, 0.93, 0.85)),
                        TextFont::from_font_size(14.0),
                        ToastPill {
                            timer: TOAST_LIFETIME,
                        },
                    ));
                });
        });
    }
}

/// Vida e fade dos toasts.
fn toast_fade_system(
    mut pills: Query<(Entity, &mut ToastPill, &mut TextColor)>,
    time: Res<Time>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut pill, mut color) in &mut pills {
        pill.timer -= dt;
        if pill.timer <= 0.0 {
            commands.entity(entity).despawn();
            continue;
        }
        let fade = (pill.timer / 0.6).min(1.0);
        color.0.set_alpha(fade);
    }
}

// ── modal [Q] ───────────────────────────────────────────────────────────

fn spawn_modal(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(0.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                bottom: Val::Px(0.0),
                justify_content: JustifyContent::Center,
                align_items: AlignItems::Center,
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.45)),
            Visibility::Hidden,
            Name::new("ui:modal"),
            ModalRoot,
        ))
        .with_children(|root| {
            root.spawn((
                Node {
                    width: Val::Px(560.0),
                    height: Val::Px(380.0),
                    padding: UiRect::all(Val::Px(20.0)),
                    flex_direction: FlexDirection::Column,
                    row_gap: Val::Px(10.0),
                    border_radius: BorderRadius::all(Val::Px(14.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.08, 0.06, 0.04, 0.96)),
                BorderColor::all(Color::srgb(0.85, 0.75, 0.45)),
                Outline::new(Val::Px(2.0), Val::ZERO, Color::srgb(0.85, 0.75, 0.45)),
            ))
            .with_children(|panel| {
                panel.spawn((
                    Text::new("DISCORDIA"),
                    TextColor(Color::srgb(0.95, 0.85, 0.6)),
                    TextFont::from_font_size(22.0),
                ));
                panel.spawn((
                    Text::new(tab_header(0)),
                    TextColor(Color::srgb(0.85, 0.8, 0.7)),
                    TextFont::from_font_size(14.0),
                    ModalContent,
                ));
            });
        });
}

/// Cabeçalho das tabs (marcador > na ativa).
pub fn tab_header(active: usize) -> String {
    let names = ["Quests", "Inventário", "Ajuda"];
    names
        .iter()
        .enumerate()
        .map(|(i, name)| {
            if i == active {
                format!("> {name}")
            } else {
                format!("  {name}")
            }
        })
        .collect::<Vec<_>>()
        .join("    ")
}

/// Corpo de uma tab (puro; texturas dos testes).
pub fn tab_body(tab: usize, quest_lines: &[String], vault_lines: &[String]) -> String {
    match tab {
        0 => {
            if quest_lines.is_empty() {
                "Sem quests ativas — procura os NPCs de quest.".into()
            } else {
                quest_lines.join("\n")
            }
        }
        1 => vault_lines.join("\n"),
        _ => "Controlos:\n\
              WASD mover · Espaço saltar · Shift correr\n\
              [J] atacar/colher · [E] falar/interagir · [K] loja\n\
              [Q] este menu · [F3] profiler · [F10] giver de QA\n\n\
              Opções: volumes e save/load chegam com o loop 7.".into(),
    }
}

/// Toggle [Q]/Esc + navegação de tabs; reescreve o corpo quando aberto.
#[allow(clippy::type_complexity)]
fn modal_toggle_system(
    keys: Res<ButtonInput<KeyCode>>,
    mut open: ResMut<MenusOpen>,
    mut tab: ResMut<ModalTab>,
    mut roots: Query<&mut Visibility, With<ModalRoot>>,
) {
    if keys.just_pressed(KeyCode::KeyQ) {
        open.modal = !open.modal;
    }
    if open.modal && keys.just_pressed(KeyCode::Escape) {
        open.modal = false;
    }
    if open.modal {
        if keys.just_pressed(KeyCode::ArrowRight) || keys.just_pressed(KeyCode::Tab) {
            tab.0 = next_tab(tab.0, 1);
        }
        if keys.just_pressed(KeyCode::ArrowLeft) {
            tab.0 = next_tab(tab.0, -1);
        }
    }
    for mut visibility in roots.iter_mut() {
        let wanted = if open.modal {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
    }
}

/// Conteúdo do modal (throttle 0,25 s; barato e sempre fresco).
#[allow(clippy::type_complexity)]
fn modal_content_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    open: Res<MenusOpen>,
    tab: Res<ModalTab>,
    log: Option<Res<QuestLog>>,
    vault: Option<Res<Vault>>,
    mut q_content: Query<&mut Text, With<ModalContent>>,
) {
    if !open.modal {
        return;
    }
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.25;
    for mut text in q_content.iter_mut() {
        let header = tab_header(tab.0);
        let body = match (log.as_deref(), vault.as_deref()) {
            (Some(log), Some(vault)) => {
                let quest_lines: Vec<String> = log
                    .active_ids(Some(vault))
                    .iter()
                    .map(|id| {
                        log.def(id)
                            .map(|def| {
                                format!(
                                    "• {}  [{}]",
                                    def.title,
                                    log.progress_text(id, Some(vault))
                                )
                            })
                            .unwrap_or_default()
                    })
                    .collect();
                tab_body(tab.0, &quest_lines, &vault_lines(vault))
            }
            _ => tab_body(tab.0, &[], &[]),
        };
        let wanted = format!("{header}\n\n{body}");
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

/// Linhas do inventário a partir do vault.
pub fn vault_lines(vault: &Vault) -> Vec<String> {
    let mut lines = vec![
        format!("Ouro: {}", vault.gold),
        format!("Madeira: {}", vault.wood),
        format!("Pedra: {}", vault.stone),
        String::new(),
    ];
    let mut items: Vec<(&String, &u32)> = vault.items.iter().collect();
    items.sort();
    if items.is_empty() {
        lines.push("(sem itens)".into());
    } else {
        for (id, count) in items {
            lines.push(format!("• {id} ×{count}"));
        }
    }
    lines
}

// ── loja [K] ────────────────────────────────────────────────────────────

fn spawn_shop(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(0.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                bottom: Val::Px(0.0),
                justify_content: JustifyContent::Center,
                align_items: AlignItems::Center,
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.45)),
            Visibility::Hidden,
            Name::new("ui:shop"),
            ShopRoot,
        ))
        .with_children(|root| {
            root.spawn((
                Node {
                    width: Val::Px(520.0),
                    height: Val::Px(300.0),
                    padding: UiRect::all(Val::Px(20.0)),
                    flex_direction: FlexDirection::Column,
                    row_gap: Val::Px(8.0),
                    border_radius: BorderRadius::all(Val::Px(14.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.08, 0.06, 0.04, 0.96)),
                BorderColor::all(Color::srgb(0.85, 0.75, 0.45)),
            ))
            .with_children(|panel| {
                panel.spawn((
                    Text::new("LOJA DO MERCADOR"),
                    TextColor(Color::srgb(0.95, 0.85, 0.6)),
                    TextFont::from_font_size(20.0),
                ));
                panel.spawn((
                    Text::new(""),
                    TextColor(Color::srgb(0.9, 0.88, 0.8)),
                    TextFont::from_font_size(15.0),
                    ShopContent,
                ));
            });
        });
}

/// Estado da loja: seleção e resultado da última ação (puro p/ testes).
#[derive(Debug, Clone, PartialEq)]
pub enum ShopAction {
    Bought { item: String, price: u32 },
    Sold { item: String, earned: u32 },
    OutOfStock { item: String },
    CannotAfford { item: String, price: u32 },
    Nothing,
}

/// Tenta executar a linha `index` do catálogo sobre o vault.
pub fn shop_apply(vault: &mut Vault, index: usize) -> ShopAction {
    let catalog = shop_catalog();
    let Some((label, price, key, amount)) = catalog.get(index) else {
        return ShopAction::Nothing;
    };
    if *price >= 0 {
        if vault.gold < *price as u32 {
            return ShopAction::CannotAfford {
                item: label.to_string(),
                price: *price as u32,
            };
        }
        vault.gold -= *price as u32;
        vault.item_add(key, *amount);
        ShopAction::Bought {
            item: label.to_string(),
            price: *price as u32,
        }
    } else {
        let earned = (-*price) as u32;
        if !vault.take(key, *amount) {
            return ShopAction::OutOfStock {
                item: label.to_string(),
            };
        }
        vault.gold += earned;
        ShopAction::Sold {
            item: label.to_string(),
            earned,
        }
    }
}

/// Abre/fecha a loja perto do mercador, navega e executa compras/vendas.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn shop_system(
    keys: Res<ButtonInput<KeyCode>>,
    players: Query<&GlobalTransform, With<Player>>,
    merchants: Query<&GlobalTransform, Without<Player>>,
    mut vault: ResMut<Vault>,
    mut open: ResMut<MenusOpen>,
    mut selection: Local<usize>,
    mut q_shop: Query<&mut Visibility, With<ShopRoot>>,
    mut q_content: Query<&mut Text, With<ShopContent>>,
    time: Res<Time>,
    mut throttle: Local<f32>,
) {
    let near_merchant = players.iter().next().is_some_and(|player| {
        merchants
            .iter()
            .any(|m| m.translation().distance(player.translation()) < SHOP_RANGE_M)
    });

    if keys.just_pressed(KeyCode::KeyK) && (near_merchant || open.shop) {
        open.shop = !open.shop;
        *selection = 0;
    }
    if open.shop && !near_merchant {
        open.shop = false;
    }
    for mut visibility in q_shop.iter_mut() {
        let wanted = if open.shop {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
    }
    if !open.shop {
        return;
    }

    let catalog_len = shop_catalog().len();
    if keys.just_pressed(KeyCode::ArrowDown) || keys.just_pressed(KeyCode::KeyS) {
        *selection = (*selection + 1) % catalog_len;
    }
    if keys.just_pressed(KeyCode::ArrowUp) || keys.just_pressed(KeyCode::KeyW) {
        *selection = (*selection + catalog_len - 1) % catalog_len;
    }
    let mut action = ShopAction::Nothing;
    if keys.just_pressed(KeyCode::KeyJ) || keys.just_pressed(KeyCode::Enter) {
        action = shop_apply(&mut vault, *selection);
    }

    *throttle += time.delta_secs();
    let refresh = *throttle >= 0.25 || action != ShopAction::Nothing;
    if !refresh {
        return;
    }
    *throttle = 0.0;
    let catalog = shop_catalog();
    let mut lines = vec![format!("Ouro: {}", vault.gold), String::new()];
    for (i, (label, price, _, _amount)) in catalog.iter().enumerate() {
        let marker = if i == *selection { ">" } else { " " };
        let price_text = if *price >= 0 {
            format!("{} ouro", price)
        } else {
            format!("+{} ouro", -price)
        };
        lines.push(format!("{marker} [{label}  {price_text}]"));
    }
    lines.push(String::new());
    lines.push(match &action {
        ShopAction::Bought { item, price } => format!("✓ {item} por {price} ouro"),
        ShopAction::Sold { item, earned } => format!("✓ {item} por {earned} ouro"),
        ShopAction::OutOfStock { item } => format!("✗ {item}: sem stock"),
        ShopAction::CannotAfford { item, price } => {
            format!("✗ {item}: faltam ouro ({price} necessário)")
        }
        ShopAction::Nothing => "[J] confirmar · ↑↓ navegar · [K] sair".into(),
    });
    for mut text in q_content.iter_mut() {
        let wanted = lines.join("\n");
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

// ── loading screen ──────────────────────────────────────────────────────

fn spawn_loading_screen(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(0.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                bottom: Val::Px(0.0),
                justify_content: JustifyContent::Center,
                align_items: AlignItems::Center,
                ..Default::default()
            },
            BackgroundColor(Color::srgb(0.03, 0.03, 0.04)),
            Name::new("ui:loading"),
            LoadingScreen,
        ))
        .with_children(|root| {
            root.spawn((
                Text::new("DISCORDIA\n\na forjar o mundo…"),
                TextColor(Color::srgb(0.95, 0.85, 0.6)),
                TextFont::from_font_size(26.0),
            ));
        });
}

/// Levanta o loading screen quando o mundo arranca (player spawloaded ou
/// timeout de 8 s — os GLBs continuam a streamar em fundo).
fn loading_hide_system(
    time: Res<Time>,
    players: Query<(), With<Player>>,
    mut loading: Query<&mut Visibility, With<LoadingScreen>>,
) {
    let ready = players.iter().next().is_some() && time.elapsed_secs() > 2.0;
    if !ready && time.elapsed_secs() < 8.0 {
        return;
    }
    for mut visibility in loading.iter_mut() {
        if *visibility != Visibility::Hidden {
            *visibility = Visibility::Hidden;
        }
    }
}

// ── banner da fogueira ──────────────────────────────────────────────────

fn spawn_campfire_banner(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            bottom: Val::Px(120.0),
            left: Val::Px(0.0),
            right: Val::Px(0.0),
            justify_content: JustifyContent::Center,
            ..Default::default()
        },
        Visibility::Hidden,
        Name::new("ui:campfire"),
        CampfireBanner,
    ))
    .with_children(|wrap| {
        wrap.spawn((
            Text::new("Fogueira: [E] descansar — o calor restaura vida"),
            TextColor(Color::srgb(0.98, 0.8, 0.5)),
            TextFont::from_font_size(14.0),
        ));
    });
}

fn campfire_banner_system(
    players: Query<&GlobalTransform, With<Player>>,
    camps: Query<&GlobalTransform, Without<Player>>,
    mut banner: Query<&mut Visibility, With<CampfireBanner>>,
) {
    let near = players.iter().next().is_some_and(|player| {
        camps
            .iter()
            .any(|c| c.translation().distance(player.translation()) < 3.5)
    });
    for mut visibility in banner.iter_mut() {
        let wanted = if near {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_next_tab_cycles() {
        assert_eq!(next_tab(0, 1), 1);
        assert_eq!(next_tab(2, 1), 0);
        assert_eq!(next_tab(0, -1), 2);
        assert_eq!(next_tab(1, 3), 1);
    }

    #[test]
    fn test_shop_catalog_shape() {
        let catalog = shop_catalog();
        assert_eq!(catalog.len(), 5);
        assert!(catalog.iter().any(|(l, _, _, _)| l.contains("poção")));
        assert!(catalog.iter().any(|(_, p, _, _)| *p < 0), "vendas incluídas");
    }

    #[test]
    fn test_shop_buy_and_sell() {
        let mut vault = Vault {
            gold: 30,
            ..Vault::default()
        };
        // comprar poção (25)
        match shop_apply(&mut vault, 0) {
            ShopAction::Bought { price, .. } => assert_eq!(price, 25),
            other => panic!("{other:?}"),
        }
        assert_eq!(vault.gold, 5);
        assert_eq!(vault.item_count("potion"), 1);
        // sem ouro para outra
        assert!(matches!(
            shop_apply(&mut vault, 0),
            ShopAction::CannotAfford { .. }
        ));
        // vender madeira sem stock
        assert!(matches!(
            shop_apply(&mut vault, 3),
            ShopAction::OutOfStock { .. }
        ));
        // vender pedra com stock
        vault.add_resource("stone", 2);
        match shop_apply(&mut vault, 4) {
            ShopAction::Sold { earned, .. } => assert_eq!(earned, 5),
            other => panic!("{other:?}"),
        }
        assert_eq!(vault.gold, 10);
        assert_eq!(vault.stone, 1);
    }

    #[test]
    fn test_tab_body_fallbacks() {
        assert!(tab_body(0, &[], &[]).contains("Sem quests"));
        assert!(tab_body(0, &["• a".into()], &[]).contains("• a"));
        assert!(tab_body(2, &[], &[]).contains("Controlos"));
    }

    #[test]
    fn test_toast_constants_sane() {
        assert!((1..=6).contains(&TOAST_CAP));
        assert!((2.0..=5.0).contains(&TOAST_LIFETIME));
    }
}
