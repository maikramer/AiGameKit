//! Tabbed menu (tecla **Q**): título + abas clicáveis — "Controles" guarda
//! a legenda de teclas que antes vivia solta na help bar, "Sobre" identifica
//! a engine e o mundo.

use bevy::prelude::*;

use super::assets::{HudAssets, gradient_overlay, label, panel_base, panel_edge, panel_shadow};
use super::widgets::{controls_row, tab_buttons};

/// Aba activa do menu (0 = Controles, 1 = Sobre).
#[derive(Resource, Default)]
pub struct HudMenuState {
    pub active: usize,
}

#[derive(Component)]
pub struct HudMenuTab {
    tab: usize,
}

#[derive(Component)]
pub struct HudMenuContent {
    tab: usize,
}

/// Constrói o menu com abas. Chamado pela tag `TabbedModal`.
pub fn build_menu(world: &mut World, hud: &HudAssets) {
    if world.get_resource::<HudMenuState>().is_none() {
        world.insert_resource(HudMenuState::default());
    }
    let (w, h) = (560.0_f32, 380.0_f32);
    world
        .spawn((
            // Overlay em ecrã cheio: escurece e bloqueia cliques no mundo.
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
            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.35)),
            Visibility::Hidden,
            super::interact::HudToggle(KeyCode::KeyQ),
            Name::new("hud:menu"),
        ))
        .with_children(|overlay| {
            overlay
                .spawn((
                    Node {
                        width: Val::Px(w),
                        height: Val::Px(h),
                        flex_direction: FlexDirection::Column,
                        padding: UiRect::all(Val::Px(20.0)),
                        border: UiRect::all(Val::Px(2.0)),
                        border_radius: BorderRadius::all(Val::Px(18.0)),
                        ..Default::default()
                    },
                    panel_base(),
                    panel_edge(),
                    panel_shadow(),
                    Name::new("hud:menu:panel"),
                ))
                .with_children(|p| {
                    p.spawn(gradient_overlay(hud, 18.0));
                })
                .with_children(|panel| {
                    // Cabeçalho.
                    panel.spawn((
                        Node {
                            margin: UiRect::bottom(Val::Px(6.0)),
                            ..Default::default()
                        },
                        label(hud, "MENU", 26.0, Color::srgb(0.95, 0.85, 0.6)),
                    ));
                    panel.spawn((
                        Node {
                            margin: UiRect::bottom(Val::Px(10.0)),
                            ..Default::default()
                        },
                        label(
                            hud,
                            "simple-rpg · engine Viber",
                            12.0,
                            Color::srgba(0.85, 0.82, 0.74, 0.75),
                        ),
                    ));
                    // Abas.
                    tab_buttons(panel, hud, &["Controles", "Sobre"]);
                    // Conteúdos (um por aba; visibilidade guiada pelo estado).
                    let contents: [(usize, &str); 2] = [(0, "controles"), (1, "sobre")];
                    for (tab, name) in contents {
                        panel
                            .spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    top: Val::Px(96.0),
                                    left: Val::Px(20.0),
                                    width: Val::Px(w - 40.0),
                                    height: Val::Px(h - 116.0),
                                    flex_direction: FlexDirection::Column,
                                    ..Default::default()
                                },
                                Visibility::Hidden,
                                HudMenuContent { tab },
                                Name::new(format!("hud:menu:{name}")),
                            ))
                            .with_children(|content| match tab {
                                0 => {
                                    for (key, action) in [
                                        ("WASD", "mover"),
                                        ("ESPAÇO", "pular"),
                                        ("SHIFT", "correr"),
                                        ("E", "interagir"),
                                        ("Q", "abrir/fechar este menu"),
                                        ("P", "profiler"),
                                        ("H / J / K", "debug: dano / cura / XP"),
                                    ] {
                                        controls_row(content, hud, key, action, w - 60.0);
                                    }
                                }
                                _ => {
                                    for (k, v) in [
                                        ("Engine", "Viber (Bevy 0.19, Rust)"),
                                        ("Mundo", "simple-rpg"),
                                        ("Render", "câmera 3ª pessoa automática"),
                                        ("Estradas", "ribbon com Chaikin + miter"),
                                    ] {
                                        controls_row(content, hud, k, v, w - 60.0);
                                    }
                                }
                            });
                    }
                });
        });
}

/// Controla a aba activa: clique nos botões + teclas 1/2, e sincroniza
/// cores/visibilidade com [`HudMenuState`].
pub fn hud_menu_system(
    mut state: ResMut<HudMenuState>,
    keys: Res<ButtonInput<KeyCode>>,
    mut tabs: Query<(&Interaction, &HudMenuTab, &mut BackgroundColor)>,
    mut contents: Query<(&mut Visibility, &HudMenuContent)>,
) {
    let tabs_len = MENU_TAB_COUNT;
    // Clique numa aba (activa ao pressionar)…
    for (interaction, tab, bg) in &mut tabs {
        if *interaction == Interaction::Pressed {
            state.active = tab.tab;
        }
        let _ = bg;
    }
    // …ou teclas 1..N.
    for (i, code) in [
        KeyCode::Digit1,
        KeyCode::Digit2,
        KeyCode::Digit3,
        KeyCode::Digit4,
    ]
    .into_iter()
    .enumerate()
    {
        if i < tabs_len && keys.just_pressed(code) {
            state.active = i;
        }
    }
    // Sincroniza visual: aba activa dourada, resto escuro.
    for (interaction, tab, mut bg) in &mut tabs {
        let active = state.active == tab.tab;
        *bg = BackgroundColor(if active {
            Color::srgba(0.9, 0.7, 0.2, 0.85)
        } else if *interaction == Interaction::Hovered {
            Color::srgba(0.24, 0.22, 0.19, 0.9)
        } else {
            Color::srgba(0.16, 0.15, 0.13, 0.85)
        });
    }
    for (mut visibility, content) in &mut contents {
        *visibility = if state.active == content.tab {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
    }
}

const MENU_TAB_COUNT: usize = 2;
