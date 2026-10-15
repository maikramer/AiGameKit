//! `<WaypointArrow margin="64">` — a seta de rumo do HUD.
//!
//! Aponta ao alvo do [`crate::quest_guide::QuestGuide`] (entregar → cumprir →
//! missão nova). Com o alvo no ecrã, a seta pousa POR CIMA dele a apontar para
//! baixo; fora do ecrã encosta à borda (inset `margin`) e roda para o lado
//! certo — incluindo alvos nas costas da câmara, que a projeção crua espelharia.
//! A legenda diz o quê e a quantos metros.

use bevy::prelude::*;
use bevy::ui::Val2;

use crate::player::Player;
use crate::quest_guide::{GuideKind, QuestGuide};
use crate::recipes::spawn::OrbitCamera;

/// Raiz da seta (posicionada em % do ecrã).
#[derive(Component, Debug, Clone, Copy)]
pub struct WaypointArrowRoot {
    pub margin: f32,
}

/// O triângulo que roda.
#[derive(Component, Debug, Clone, Copy)]
pub struct WaypointArrowPointer;

/// A legenda ("Entregar · 42 m").
#[derive(Component, Debug, Clone, Copy)]
pub struct WaypointArrowCaption;

/// Margem por omissão (px lógicos).
pub const DEFAULT_MARGIN: f32 = 64.0;
/// Altura acima do alvo onde a seta pousa (m) — por cima da cabeça.
const LIFT_M: f32 = 2.6;
/// Abaixo desta distância a seta cala-se (o jogador já chegou).
const ARRIVED_M: f32 = 4.0;

/// Colocação da seta no ecrã.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Placement {
    /// Centro, em px lógicos do viewport.
    pub x: f32,
    pub y: f32,
    /// Rotação (rad, sentido horário a partir de "para cima").
    pub angle: f32,
    pub on_screen: bool,
}

/// Coloca a seta a partir do ponto em ESPAÇO DE CÂMARA (`local`: x direita,
/// y cima, −z em frente) e da sua projeção (`projected`, quando à frente).
pub fn place(local: Vec3, projected: Option<Vec2>, size: Vec2, margin: f32) -> Placement {
    let margin = margin.min(size.x * 0.45).min(size.y * 0.45);
    let center = size * 0.5;
    if local.z < 0.0
        && let Some(p) = projected
        && p.x >= margin
        && p.x <= size.x - margin
        && p.y >= margin
        && p.y <= size.y - margin
    {
        return Placement {
            x: p.x,
            y: p.y,
            angle: std::f32::consts::PI,
            on_screen: true,
        };
    }
    // Direção no ecrã (y para baixo). Atrás da câmara a projeção espelha —
    // usa-se o lado (x) e empurra-se para baixo ("está nas tuas costas").
    let mut dir = if local.z < 0.0 {
        Vec2::new(local.x, -local.y)
    } else {
        Vec2::new(local.x, local.z.abs().max(0.1))
    };
    if dir.length_squared() < 1e-6 {
        dir = Vec2::Y;
    }
    let half = center - Vec2::splat(margin);
    let scale = (half.x / dir.x.abs().max(1e-6)).min(half.y / dir.y.abs().max(1e-6));
    let at = center + dir * scale;
    Placement {
        x: at.x,
        y: at.y,
        angle: dir.x.atan2(-dir.y),
        on_screen: false,
    }
}

/// Spawn do widget (chamado pelo `elements.rs`).
pub fn spawn_waypoint_arrow(world: &mut World, hud: &super::assets::HudAssets, margin: f32) {
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Percent(50.0),
                top: Val::Percent(50.0),
                flex_direction: FlexDirection::Column,
                align_items: AlignItems::Center,
                row_gap: Val::Px(2.0),
                ..Default::default()
            },
            UiTransform::from_translation(Val2::new(Val::Percent(-50.0), Val::Percent(-50.0))),
            Visibility::Hidden,
            Pickable::IGNORE,
            // Por cima dos painéis do HUD: encostada à borda, passava por
            // baixo do cartão de recursos.
            GlobalZIndex(40),
            Name::new("hud:waypoint-arrow"),
            WaypointArrowRoot { margin },
        ))
        .with_children(|root| {
            root.spawn((
                Node {
                    width: Val::Px(30.0),
                    height: Val::Px(30.0),
                    ..Default::default()
                },
                ImageNode {
                    image: hud.arrow.clone(),
                    color: Color::srgb(1.0, 0.83, 0.45),
                    ..Default::default()
                },
                UiTransform::IDENTITY,
                WaypointArrowPointer,
            ));
            root.spawn((
                Node {
                    padding: UiRect::axes(Val::Px(7.0), Val::Px(1.0)),
                    border_radius: BorderRadius::all(Val::Px(7.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.05, 0.04, 0.03, 0.72)),
            ))
            .with_children(|pill| {
                pill.spawn((
                    super::assets::label(hud, "", 11.0, Color::srgb(1.0, 0.86, 0.55)),
                    WaypointArrowCaption,
                ));
            });
        });
}

/// Coloca a seta a cada frame.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn update_waypoint_arrow(
    guide: Option<Res<QuestGuide>>,
    interior: Option<Res<crate::worldsys::InteriorLighting>>,
    menus: Option<Res<crate::menus::MenusOpen>>,
    cameras: Query<(&Camera, &GlobalTransform), With<OrbitCamera>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut roots: Query<(&mut Node, &mut Visibility, &WaypointArrowRoot)>,
    mut pointers: Query<&mut UiTransform, With<WaypointArrowPointer>>,
    mut captions: Query<&mut Text, With<WaypointArrowCaption>>,
) {
    let Ok((mut node, mut visibility, root)) = roots.single_mut() else {
        return;
    };
    let hidden = interior.as_deref().is_some_and(|state| state.active)
        || menus.as_deref().is_some_and(|m| m.any());
    let target = guide.as_deref().and_then(|g| g.target.as_ref());
    let (Some(target), Some((camera, camera_gt)), Some(player), false) = (
        target,
        cameras.iter().find(|(c, _)| c.is_active),
        players.iter().next(),
        hidden,
    ) else {
        visibility.set_if_neq(Visibility::Hidden);
        return;
    };
    let distance = player.translation().distance(target.position);
    let Some(size) = camera
        .logical_viewport_size()
        .filter(|s| s.x > 1.0 && s.y > 1.0)
    else {
        visibility.set_if_neq(Visibility::Hidden);
        return;
    };
    if distance < ARRIVED_M {
        visibility.set_if_neq(Visibility::Hidden);
        return;
    }
    let point = target.position + Vec3::Y * LIFT_M;
    let local = camera_gt.affine().inverse().transform_point3(point);
    let projected = camera.world_to_viewport(camera_gt, point).ok();
    let at = place(local, projected, size, root.margin);
    let left = Val::Percent(at.x / size.x * 100.0);
    let top = Val::Percent(at.y / size.y * 100.0);
    if node.left != left {
        node.left = left;
    }
    if node.top != top {
        node.top = top;
    }
    visibility.set_if_neq(Visibility::Inherited);
    if let Ok(mut pointer) = pointers.single_mut() {
        let wanted = UiTransform {
            rotation: Rot2::radians(at.angle),
            ..UiTransform::IDENTITY
        };
        if *pointer != wanted {
            *pointer = wanted;
        }
    }
    if let Ok(mut caption) = captions.single_mut() {
        let verb = match target.kind {
            GuideKind::TurnIn => "Entregar",
            GuideKind::Objective => &target.label,
            GuideKind::NewQuest => "Nova missão",
        };
        let text = format!("{verb} · {} m", distance.round() as i32);
        if caption.0 != text {
            caption.0 = text;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f32::consts::{FRAC_PI_2, PI};

    const SIZE: Vec2 = Vec2::new(1600.0, 900.0);

    #[test]
    fn on_screen_target_gets_the_arrow_on_top_pointing_down() {
        let at = place(
            Vec3::new(0.0, 0.0, -10.0),
            Some(Vec2::new(800.0, 300.0)),
            SIZE,
            64.0,
        );
        assert!(at.on_screen);
        assert_eq!((at.x, at.y), (800.0, 300.0));
        assert!((at.angle - PI).abs() < 1e-5);
    }

    #[test]
    fn off_screen_right_clamps_to_the_edge_pointing_right() {
        let at = place(Vec3::new(50.0, 0.0, -5.0), None, SIZE, 64.0);
        assert!(!at.on_screen);
        assert!((at.x - (1600.0 - 64.0)).abs() < 1e-3, "{at:?}");
        assert!((at.y - 450.0).abs() < 1e-3);
        assert!((at.angle - FRAC_PI_2).abs() < 1e-5);
    }

    #[test]
    fn target_behind_goes_to_the_bottom() {
        let at = place(Vec3::new(0.0, 0.0, 20.0), None, SIZE, 64.0);
        assert!(!at.on_screen);
        assert!((at.y - (900.0 - 64.0)).abs() < 1e-3, "{at:?}");
        assert!((at.angle.abs() - PI).abs() < 1e-5);
    }
}
