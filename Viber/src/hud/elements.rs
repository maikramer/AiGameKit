//! HUD element builders: everything spawned from world tags
//! (`HealthBar`, `XpBar`, `Minimap`, `Compass`, `ResourceChip`, …) plus the
//! DOM-parity widgets (help bar, action slots, name-tag pool).

use bevy::prelude::*;
use bevy::ui::BoxShadow;
use bevy::ui::widget::ImageNode;

use super::assets::{
    HudAssets, centered_at, gradient_overlay, label, panel_base, panel_edge, panel_shadow,
};
use super::compass::{CompassDistance, CompassLetter, CompassTick};
use super::interact::{BALLOON_DURATION, HudBalloon, HudPrompt};
use super::minimap::{MinimapAnchor, MinimapArrow, MinimapDot, MinimapRange};
use super::vitals::xp_label_text;
use super::vitals::{HudHealthFill, HudHealthLabel, HudXpFill, HudXpLabel};

fn attr<'a>(attrs: &'a [(String, String)], name: &str) -> Option<&'a str> {
    attrs
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

/// Sombra quente do tema Tinta Quente (#0c0a0988): cai para baixo e
/// espalha — a mesma assinatura dos cards declarativos.
fn warm_shadow() -> BoxShadow {
    BoxShadow::new(
        Color::srgba(0.047, 0.039, 0.035, 0.53),
        Val::Px(0.0),
        Val::Px(4.0),
        Val::ZERO,
        Val::Px(10.0),
    )
}

/// Build every deferred HUD element. `tag` is the lowercased original tag.
pub fn spawn_hud(world: &mut World, tag: &str, attrs: &[(String, String)]) {
    let hud = HudAssets::get(world);
    match tag {
        "hudscreenlayer" => {
            // Root layer + the two widgets the original renders from the DOM
            // (help pill and action slots) that have no dedicated tag.
            world.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    top: Val::Px(0.0),
                    left: Val::Px(0.0),
                    width: Val::Percent(100.0),
                    height: Val::Percent(100.0),
                    ..Default::default()
                },
                bevy::ui::FocusPolicy::Pass,
                Name::new("hud:layer"),
            ));
            // Os antigos slots de acção saíram daqui: a barra de habilidades
            // é agora `<UiCooldown>` no HUD declarativo (`src/ui`), onde a
            // veladura de recarga e o aro de "pronta" são folha de estilo.
        }
        "healthbar" => {
            // Tag legada: mantém-se a funcionar (mundos antigos não partem),
            // mas o caminho suportado é o HUD declarativo. Warn 1× por spawn
            // (o spawn acontece uma vez por mundo, não por frame).
            bevy::log::warn!(
                "hud: tag legada `HealthBar` — usa `<UiBar bind=\"health\">` num UiRoot (HUD declarativo)"
            );
            // Rounded gradient panel: glossy heart icon + green bar with
            // "100/100" inside.
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(12.0),
                        left: Val::Px(12.0),
                        flex_direction: FlexDirection::Row,
                        align_items: AlignItems::Center,
                        column_gap: Val::Px(10.0),
                        padding: UiRect::axes(Val::Px(10.0), Val::Px(8.0)),
                        border: UiRect::all(Val::Px(1.5)),
                        border_radius: BorderRadius::all(Val::Px(16.0)),
                        ..Default::default()
                    },
                    panel_base(),
                    panel_edge(),
                    panel_shadow(),
                    Name::new("hud:health"),
                ))
                .with_children(|p| {
                    p.spawn(gradient_overlay(&hud, 16.0));
                })
                .with_children(|panel| {
                    // Glossy heart icon: back diamond, front diamond, lobes,
                    // glint.
                    panel
                        .spawn(Node {
                            width: Val::Px(30.0),
                            height: Val::Px(26.0),
                            ..Default::default()
                        })
                        .with_children(|heart| {
                            heart.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(8.0),
                                    top: Val::Px(7.0),
                                    width: Val::Px(14.0),
                                    height: Val::Px(14.0),
                                    ..Default::default()
                                },
                                UiTransform::from_rotation(Rot2::radians(
                                    std::f32::consts::FRAC_PI_4,
                                )),
                                BackgroundColor(Color::srgb(0.52, 0.05, 0.08)),
                            ));
                            heart.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(8.0),
                                    top: Val::Px(6.0),
                                    width: Val::Px(14.0),
                                    height: Val::Px(14.0),
                                    ..Default::default()
                                },
                                UiTransform::from_rotation(Rot2::radians(
                                    std::f32::consts::FRAC_PI_4,
                                )),
                                BackgroundColor(Color::srgb(0.80, 0.10, 0.13)),
                            ));
                            for x in [2.0_f32, 14.0] {
                                heart.spawn((
                                    Node {
                                        position_type: PositionType::Absolute,
                                        left: Val::Px(x),
                                        top: Val::Px(1.0),
                                        width: Val::Px(14.0),
                                        height: Val::Px(14.0),
                                        border_radius: BorderRadius::all(Val::Px(7.0)),
                                        ..Default::default()
                                    },
                                    BackgroundColor(Color::srgb(0.80, 0.10, 0.13)),
                                ));
                            }
                            heart.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(6.0),
                                    top: Val::Px(4.0),
                                    width: Val::Px(6.0),
                                    height: Val::Px(6.0),
                                    border_radius: BorderRadius::all(Val::Px(3.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(1.0, 0.6, 0.6, 0.9)),
                            ));
                        });
                    // Bar track with the green fill + centered label.
                    panel
                        .spawn((
                            Node {
                                width: Val::Px(170.0),
                                height: Val::Px(20.0),
                                padding: UiRect::all(Val::Px(2.0)),
                                border_radius: BorderRadius::all(Val::Px(9.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.6)),
                            BorderColor::all(Color::srgba(0.0, 0.0, 0.0, 0.7)),
                        ))
                        .with_children(|track| {
                            track
                                .spawn((
                                    Node {
                                        width: Val::Percent(100.0),
                                        height: Val::Percent(100.0),
                                        border_radius: BorderRadius::all(Val::Px(7.0)),
                                        ..Default::default()
                                    },
                                    BackgroundColor(Color::srgb(0.30, 0.74, 0.22)),
                                    HudHealthFill,
                                ))
                                .with_children(|fill| {
                                    fill.spawn((
                                        Node {
                                            justify_content: JustifyContent::Center,
                                            align_items: AlignItems::Center,
                                            width: Val::Percent(100.0),
                                            height: Val::Percent(100.0),
                                            ..Default::default()
                                        },
                                        label(&hud, "100/100", 14.0, Color::srgb(0.97, 1.0, 0.96)),
                                        HudHealthLabel,
                                    ));
                                });
                        });
                });
        }
        "xpbar" => {
            bevy::log::warn!(
                "hud: tag legada `XpBar` — usa `<UiBar bind=\"xp\">` num UiRoot (HUD declarativo)"
            );
            // Level badge (gold coin) + slim dark bar with a gold fill.
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(66.0),
                        left: Val::Px(12.0),
                        flex_direction: FlexDirection::Row,
                        align_items: AlignItems::Center,
                        column_gap: Val::Px(10.0),
                        padding: UiRect::axes(Val::Px(8.0), Val::Px(5.0)),
                        border_radius: BorderRadius::all(Val::Px(13.0)),
                        ..Default::default()
                    },
                    panel_base(),
                    panel_shadow(),
                    Name::new("hud:xp"),
                ))
                .with_children(|p| {
                    p.spawn(gradient_overlay(&hud, 13.0));
                })
                .with_children(|panel| {
                    panel
                        .spawn((
                            Node {
                                width: Val::Px(26.0),
                                height: Val::Px(26.0),
                                justify_content: JustifyContent::Center,
                                align_items: AlignItems::Center,
                                border_radius: BorderRadius::all(Val::Px(13.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.86, 0.65, 0.12)),
                            BorderColor::all(Color::srgb(0.42, 0.29, 0.04)),
                        ))
                        .with_children(|coin| {
                            coin.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(3.0),
                                    top: Val::Px(3.0),
                                    width: Val::Px(20.0),
                                    height: Val::Px(20.0),
                                    border_radius: BorderRadius::all(Val::Px(10.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(1.0, 0.86, 0.35, 0.55)),
                            ));
                            coin.spawn(label(&hud, "1", 14.0, Color::srgb(0.32, 0.21, 0.02)));
                        });
                    panel
                        .spawn((
                            Node {
                                width: Val::Px(170.0),
                                height: Val::Px(12.0),
                                padding: UiRect::all(Val::Px(2.0)),
                                border_radius: BorderRadius::all(Val::Px(6.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.6)),
                            BorderColor::all(Color::srgba(0.0, 0.0, 0.0, 0.7)),
                        ))
                        .with_children(|track| {
                            track.spawn((
                                Node {
                                    width: Val::Percent(0.0),
                                    height: Val::Percent(100.0),
                                    border_radius: BorderRadius::all(Val::Px(4.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgb(0.94, 0.71, 0.15)),
                                HudXpFill,
                            ));
                        });
                    // Dim numeric readout parked right of the bar.
                    panel.spawn((
                        label(
                            &hud,
                            xp_label_text(0, 100),
                            10.0,
                            Color::srgba(1.0, 0.95, 0.8, 0.65),
                        ),
                        HudXpLabel,
                    ));
                });
        }
        "minimap" => {
            let range = attr(attrs, "range")
                .and_then(|v| v.parse::<f32>().ok())
                .unwrap_or(60.0);
            use super::minimap::{MINIMAP_H, MINIMAP_W, MinimapThreat};
            // Canto inferior esquerdo, como em qualquer jogo de mundo aberto
            // que se leia: o olhar do jogador vive no centro-baixo e o mapa é
            // a única coisa permanente que ele consulta a meio da corrida.
            //
            // Rectângulo de cantos redondos (não disco): é o que deixa o
            // `Overflow::clip` funcionar — e é o clip que permite o mapa
            // DESLIZAR por baixo da moldura em vez de ser um brasão estático.
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        bottom: Val::Px(18.0),
                        left: Val::Px(18.0),
                        width: Val::Px(MINIMAP_W),
                        height: Val::Px(MINIMAP_H),
                        border: UiRect::all(Val::Px(1.5)),
                        border_radius: BorderRadius::all(Val::Px(7.0)),
                        overflow: Overflow::clip(),
                        ..Default::default()
                    },
                    // Vidro umber: escuro o bastante para o painel se
                    // distinguir do mundo, translúcido o bastante para não
                    // ser um bloco morto no canto.
                    BackgroundColor(Color::srgba(0.11, 0.075, 0.045, 0.78)),
                    BorderColor::all(Color::srgba(0.957, 0.925, 0.847, 0.4)),
                    Name::new("hud:minimap"),
                    MinimapRange(range),
                    panel_shadow(),
                ))
                .with_children(|map| {
                    // Rosa dos ventos: só o N ganha letra (é o Norte que se
                    // procura num relance); os outros pontos são traços.
                    map.spawn((
                        centered_at(Val::Percent(50.0), Val::Px(3.0)),
                        UiTransform::from_translation(Val2::new(Val::Percent(-50.0), Val::ZERO)),
                        label(&hud, "N", 11.0, Color::srgba(0.98, 0.88, 0.62, 0.95)),
                    ));
                    for (left, top, w, h) in [
                        (Val::Percent(97.0), Val::Percent(50.0), 6.0, 2.0),
                        (Val::Percent(50.0), Val::Percent(97.0), 2.0, 6.0),
                        (Val::Percent(3.0), Val::Percent(50.0), 6.0, 2.0),
                    ] {
                        map.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left,
                                top,
                                width: Val::Px(w),
                                height: Val::Px(h),
                                border_radius: BorderRadius::all(Val::Px(1.0)),
                                ..Default::default()
                            },
                            UiTransform::from_translation(Val2::new(
                                Val::Percent(-50.0),
                                Val::Percent(-50.0),
                            )),
                            BackgroundColor(Color::srgba(0.957, 0.925, 0.847, 0.5)),
                        ));
                    }
                    // Hostis: aros vermelhos, POR BAIXO dos pontos de quest
                    // na ordem de spawn (desenham primeiro). Doze chegam para
                    // qualquer enquadramento honesto.
                    for _ in 0..12 {
                        map.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Percent(50.0),
                                top: Val::Percent(50.0),
                                margin: UiRect::px(-3.5, 0.0, -3.5, 0.0),
                                width: Val::Px(7.0),
                                height: Val::Px(7.0),
                                border_radius: BorderRadius::MAX,
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.788, 0.294, 0.259)),
                            Outline::new(
                                Val::Px(1.0),
                                Val::ZERO,
                                Color::srgba(0.07, 0.05, 0.03, 0.75),
                            ),
                            UiTransform::IDENTITY,
                            Visibility::Hidden,
                            MinimapThreat,
                        ));
                    }
                    // Quest markers: losango dourado com "!" de tinta
                    // (gerado em `assets.rs`) — o vocabulário universal de
                    // "objectivo aqui", legível sobre terreno claro e
                    // escuro; a contagem vive no tracker.
                    for _ in 1..=6 {
                        map.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Percent(50.0),
                                top: Val::Percent(50.0),
                                margin: UiRect::px(-6.0, 0.0, -6.0, 0.0),
                                width: Val::Px(12.0),
                                height: Val::Px(12.0),
                                ..Default::default()
                            },
                            ImageNode {
                                image: hud.quest_marker.clone(),
                                ..Default::default()
                            },
                            UiTransform::IDENTITY,
                            Visibility::Hidden,
                            MinimapDot,
                        ));
                    }
                    // Âncora do marco assinado (A Nota): pin de mapa dourado
                    // — par de vocabulário com o "!" ("quest = exclamação,
                    // destino = pin"), ambos ícones e não pontos crus.
                    map.spawn((
                        Node {
                            position_type: PositionType::Absolute,
                            left: Val::Percent(50.0),
                            top: Val::Percent(50.0),
                            margin: UiRect::px(-6.0, 0.0, -6.0, 0.0),
                            width: Val::Px(12.0),
                            height: Val::Px(12.0),
                            ..Default::default()
                        },
                        ImageNode {
                            image: hud.map_pin.clone(),
                            ..Default::default()
                        },
                        UiTransform::IDENTITY,
                        Visibility::Hidden,
                        MinimapAnchor,
                    ));
                    // Player arrow: puck escuro com o triângulo por cima —
                    // desenhado POR ÚLTIMO, portanto sempre por cima dos
                    // blips. Fica sempre no centro; é o mapa que desliza.
                    map.spawn((
                        Node {
                            position_type: PositionType::Absolute,
                            left: Val::Percent(50.0),
                            top: Val::Percent(50.0),
                            margin: UiRect::px(-11.0, 0.0, -11.0, 0.0),
                            width: Val::Px(22.0),
                            height: Val::Px(22.0),
                            justify_content: JustifyContent::Center,
                            align_items: AlignItems::Center,
                            border: UiRect::all(Val::Px(1.5)),
                            border_radius: BorderRadius::MAX,
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.07, 0.05, 0.03, 0.86)),
                        BorderColor::all(Color::srgba(0.957, 0.925, 0.847, 0.75)),
                        UiTransform::IDENTITY,
                        MinimapArrow,
                    ))
                    .with_children(|puck| {
                        puck.spawn((
                            Node {
                                width: Val::Px(15.0),
                                height: Val::Px(15.0),
                                ..Default::default()
                            },
                            ImageNode {
                                image: hud.arrow.clone(),
                                ..Default::default()
                            },
                        ));
                    });
                });
        }
        "compass" => {
            // Régua de compasso no topo-centro: letras + réguas deslizam com
            // o rumo da câmara (`hud::compass::hud_compass_update`) e cada
            // sector de 45° mostra a distância ao NPC mais próximo. Removida
            // no passe de polimento, RESTAURADA a pedido do autor — a faixa
            // de céu em cima é dela.
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(10.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        ..Default::default()
                    },
                    Name::new("hud:compass"),
                ))
                .with_children(|wrap| {
                    const DIRECTIONS: [(&str, f32); 8] = [
                        ("N", 0.0),
                        ("NE", 45.0),
                        ("E", 90.0),
                        ("SE", 135.0),
                        ("S", 180.0),
                        ("SW", 225.0),
                        ("W", 270.0),
                        ("NW", 315.0),
                    ];
                    // Letters live INSIDE the strip so their absolute left is
                    // relative to it (wrap is full-width, the strip is not).
                    wrap.spawn((
                        Node {
                            width: Val::Px(460.0),
                            height: Val::Px(34.0),
                            border_radius: BorderRadius::all(Val::Px(17.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.03, 0.03, 0.025, 0.62)),
                        ImageNode {
                            image: hud.panel_gradient.clone(),
                            color: Color::srgba(0.10, 0.095, 0.085, 0.55),
                            ..Default::default()
                        },
                        BorderColor::all(Color::srgba(1.0, 0.96, 0.85, 0.12)),
                        panel_shadow(),
                    ))
                    .with_children(|strip| {
                        // Center caret (static): the heading marker.
                        strip.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Percent(50.0),
                                top: Val::Px(2.0),
                                width: Val::Px(2.0),
                                height: Val::Px(8.0),
                                border_radius: BorderRadius::all(Val::Px(1.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.95, 0.78, 0.25)),
                        ));
                        for (name, bearing) in DIRECTIONS {
                            let color = if name == "N" {
                                Color::srgb(0.95, 0.78, 0.25)
                            } else {
                                Color::srgb(0.92, 0.92, 0.88)
                            };
                            strip.spawn((
                                centered_at(Val::Px(230.0), Val::Px(4.0)),
                                UiTransform::from_translation(Val2::new(
                                    Val::Percent(-50.0),
                                    Val::ZERO,
                                )),
                                label(&hud, name, 13.0, color),
                                CompassLetter {
                                    bearing_deg: bearing,
                                },
                            ));
                            strip.spawn((
                                centered_at(Val::Px(230.0), Val::Px(19.0)),
                                UiTransform::from_translation(Val2::new(
                                    Val::Percent(-50.0),
                                    Val::ZERO,
                                )),
                                label(&hud, "", 9.0, Color::srgba(1.0, 0.92, 0.7, 0.85)),
                                CompassDistance {
                                    bearing_deg: bearing,
                                },
                            ));
                        }
                        // Tick marks every 22.5°.
                        for i in 0..16 {
                            let bearing = i as f32 * 22.5;
                            let tall = i % 2 == 0;
                            strip.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(230.0),
                                    top: Val::Px(0.0),
                                    width: Val::Px(1.5),
                                    height: Val::Px(if tall { 5.0 } else { 3.0 }),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(
                                    1.0,
                                    1.0,
                                    0.95,
                                    if tall { 0.4 } else { 0.22 },
                                )),
                                CompassTick {
                                    bearing_deg: bearing,
                                },
                            ));
                        }
                    });
                });
        }
        "interactionprompt" => {
            let key = attr(attrs, "key").unwrap_or("E").to_string();
            let range = attr(attrs, "range")
                .and_then(|v| v.parse::<f32>().ok())
                .unwrap_or(3.5);
            // Tema TINTA QUENTE (valores equivalentes ao theme.css/hud.css,
            // que a engine não lê aqui): card stone-900 translúcido, borda
            // dourada da casa, texto papel, keycap âmbar, sombra quente.
            // Cinzel (HudAssets) para o selo ler como jogo, não como debug.
            let card = Color::srgba(0.110, 0.098, 0.090, 0.92); // #1c1917eb
            let gold_edge = Color::srgba(0.718, 0.643, 0.467, 0.44); // #b7a47770
            let paper = Color::srgb(0.933, 0.914, 0.863); // #eee9dc
            let keycap = Color::srgb(0.867, 0.780, 0.596); // #ddc798
            let ink = Color::srgb(0.110, 0.098, 0.090); // #1c1917
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        bottom: Val::Px(130.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        ..Default::default()
                    },
                    Visibility::Hidden,
                    Name::new("hud:prompt"),
                ))
                .with_children(|wrap| {
                    wrap.spawn((
                        Node {
                            flex_direction: FlexDirection::Row,
                            align_items: AlignItems::Center,
                            column_gap: Val::Px(9.0),
                            padding: UiRect::axes(Val::Px(12.0), Val::Px(7.0)),
                            border: UiRect::all(Val::Px(1.0)),
                            border_radius: BorderRadius::all(Val::Px(9.0)),
                            ..Default::default()
                        },
                        BackgroundColor(card),
                        BorderColor::all(gold_edge),
                        warm_shadow(),
                        HudPrompt { range },
                    ))
                    .with_children(|card| {
                        card.spawn((
                            Node {
                                padding: UiRect::axes(Val::Px(7.0), Val::Px(2.0)),
                                border_radius: BorderRadius::all(Val::Px(4.0)),
                                justify_content: JustifyContent::Center,
                                align_items: AlignItems::Center,
                                ..Default::default()
                            },
                            BackgroundColor(keycap),
                            Text::new(key),
                            TextColor(ink),
                            TextFont {
                                font: hud.font.clone().into(),
                                font_size: 15.0.into(),
                                ..Default::default()
                            },
                        ));
                        card.spawn(label(&hud, "Interagir", 15.0, paper));
                    });
                });
        }
        "dialogueballoon" => {
            // Mesma voz do prompt: tinta escura translúcida + fio dourado +
            // papel. O TEXTO tem de ficar no PRIMEIRO filho (o fluxo de
            // diálogo e o countdown escrevem nele directamente).
            let card = Color::srgba(0.110, 0.098, 0.090, 0.94); // #1c1917f0
            let gold_edge = Color::srgba(0.718, 0.643, 0.467, 0.50);
            let paper = Color::srgb(0.933, 0.914, 0.863); // #eee9dc
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        bottom: Val::Px(180.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        ..Default::default()
                    },
                    Visibility::Hidden,
                    HudBalloon {
                        timer: BALLOON_DURATION,
                    },
                    Name::new("hud:balloon"),
                ))
                .with_children(|wrap| {
                    wrap.spawn((
                        Node {
                            max_width: Val::Px(520.0),
                            padding: UiRect::axes(Val::Px(16.0), Val::Px(10.0)),
                            border: UiRect::all(Val::Px(1.0)),
                            border_radius: BorderRadius::all(Val::Px(9.0)),
                            ..Default::default()
                        },
                        BackgroundColor(card),
                        BorderColor::all(gold_edge),
                        warm_shadow(),
                        Text::new("…"),
                        TextColor(paper),
                        TextFont {
                            font: hud.font.clone().into(),
                            font_size: 15.0.into(),
                            ..Default::default()
                        },
                    ));
                });
        }
        "tabbedmodal" => {
            // Menu com abas: Controles (a antiga help bar) + Sobre. A tecla
            // de toggle vem do attr autoral `key` (Q fica para o modal da
            // engine — cai em F1).
            let key = super::menu::toggle_key_from_attr(attr(attrs, "key"));
            super::menu::build_menu(world, &hud, key);
        }
        other => {
            bevy::log::warn!("hud: unhandled element `{other}` — skipped");
        }
    }
}

/// Resource chip slot styled like the original: rounded dark slot with an
/// authored mini icon (coin / log / stone) and a count. `index` is 1-based.
pub fn spawn_resource_chip(world: &mut World, index: usize, resource: &str) {
    let hud = HudAssets::get(world);
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(110.0),
                left: Val::Px(12.0 + 64.0 * (index - 1) as f32),
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: Val::Px(7.0),
                padding: UiRect::axes(Val::Px(9.0), Val::Px(6.0)),
                border_radius: BorderRadius::all(Val::Px(12.0)),
                ..Default::default()
            },
            panel_base(),
            panel_edge(),
            panel_shadow(),
            Name::new(format!("chip:{resource}")),
        ))
        .with_children(|slot| {
            match resource {
                // Gold coin: dark ring + gold disc + glint.
                "gold" => {
                    slot.spawn((
                        Node {
                            width: Val::Px(15.0),
                            height: Val::Px(15.0),
                            border_radius: BorderRadius::all(Val::Px(7.5)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgb(0.42, 0.29, 0.04)),
                    ))
                    .with_children(|coin| {
                        coin.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(2.0),
                                top: Val::Px(2.0),
                                width: Val::Px(11.0),
                                height: Val::Px(11.0),
                                border_radius: BorderRadius::all(Val::Px(5.5)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.95, 0.73, 0.16)),
                        ));
                        coin.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(3.0),
                                top: Val::Px(3.0),
                                width: Val::Px(4.0),
                                height: Val::Px(4.0),
                                border_radius: BorderRadius::all(Val::Px(2.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgba(1.0, 0.95, 0.75, 0.9)),
                        ));
                    });
                }
                // Wood log: rounded plank + end-grain disc + core dot.
                "wood" => {
                    slot.spawn((
                        Node {
                            width: Val::Px(17.0),
                            height: Val::Px(11.0),
                            border_radius: BorderRadius::px(5.5, 1.5, 1.5, 5.5),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgb(0.45, 0.28, 0.12)),
                    ))
                    .with_children(|log| {
                        log.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(0.0),
                                top: Val::Px(0.0),
                                width: Val::Px(9.0),
                                height: Val::Px(11.0),
                                border_radius: BorderRadius::all(Val::Px(5.5)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.66, 0.45, 0.22)),
                        ));
                        log.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(2.5),
                                top: Val::Px(2.5),
                                width: Val::Px(4.0),
                                height: Val::Px(6.0),
                                border_radius: BorderRadius::all(Val::Px(2.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.45, 0.28, 0.12)),
                        ));
                    });
                }
                // Stone: pebble disc + lighter top face.
                _ => {
                    slot.spawn((
                        Node {
                            width: Val::Px(15.0),
                            height: Val::Px(12.0),
                            border_radius: BorderRadius::all(Val::Px(6.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgb(0.38, 0.40, 0.44)),
                    ))
                    .with_children(|stone| {
                        stone.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(1.5),
                                top: Val::Px(0.5),
                                width: Val::Px(12.0),
                                height: Val::Px(8.0),
                                border_radius: BorderRadius::all(Val::Px(5.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.63, 0.66, 0.71)),
                        ));
                    });
                }
            }
            slot.spawn(label(&hud, "0", 15.0, Color::srgb(0.96, 0.94, 0.86)));
        });
}
