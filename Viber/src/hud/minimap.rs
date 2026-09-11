//! Circular minimap: the player arrow rotates with the camera heading and
//! numbered quest dots plot nearby NPCs north-up.

use bevy::asset::RenderAssetUsages;
use bevy::math::Rot2;
use bevy::prelude::*;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat};

use crate::player::Player;
use crate::recipes::spawn::{DialogueNpc, OrbitCamera};
use crate::terrain::runtime::TerrainRuntime;

/// The minimap player arrow (rotation mirrors the camera heading).
#[derive(Component)]
pub struct MinimapArrow;

/// A quest dot on the minimap (positioned at a nearby NPC's world spot).
#[derive(Component)]
pub struct MinimapDot;

/// The signed landmark ("A Nota", `travel::Waypoint`): an anchor pinned to
/// the map (clamped to the rim when the landmark is out of range).
#[derive(Component)]
pub struct MinimapAnchor;

/// The baked terrain underlay (one `ImageNode` child of the map panel).
#[derive(Component)]
pub struct MinimapTerrain;

/// Um hostil próximo no mapa.
#[derive(Component)]
pub struct MinimapThreat;

/// Largura e altura do painel do mapa (px, referência 720p).
///
/// Rectângulo de cantos redondos, não disco: o clip do bevy_ui 0.19 é um
/// `Rect` (o `border-radius` não entra na conta), e é o clip que permite o
/// mapa **deslizar** por baixo da moldura. Um disco obrigava o mapa a ser uma
/// imagem estática do mundo inteiro — que foi exactamente o que o crítico
/// leu como "um disco onde só se distingue um relógio".
pub const MINIMAP_W: f32 = 208.0;
pub const MINIMAP_H: f32 = 148.0;

/// Metros de mundo que cabem na LARGURA do painel.
///
/// 420 m: a aldeia inteira cabe com folga, e um marco a 200 m ainda entra no
/// enquadramento em vez de ficar preso à borda.
pub const MINIMAP_VIEW_M: f32 = 420.0;

/// Píxeis por metro do mapa.
pub fn minimap_scale() -> f32 {
    MINIMAP_W / MINIMAP_VIEW_M
}

/// Resolution of the baked terrain underlay (pixels per side). O(side²)
/// heightfield samples, paid ONCE, no carregamento.
///
/// Subiu de 264 para 640 quando o mapa passou a deslizar: a 264 cada texel
/// cobria 15 m de mundo e, ampliado para a escala local, o relevo virava
/// manchas de 8 px. A 640 o texel são 6,25 m — a orla de um lago e o traço de
/// uma estrada já se lêem.
const TERRAIN_BAKE_SIDE: usize = 640;

/// Bakes the whole world north-up: água em ardósia, terra numa rampa de papel
/// dessaturada, estradas em ocre. A imagem é MAIOR do que o painel e desliza
/// por baixo dele — o recorte é o `Overflow::clip()` do painel, não um alpha
/// cozido (que era o que obrigava o mapa a ser estático).
fn bake_terrain_image(runtime: &TerrainRuntime) -> Image {
    let side = TERRAIN_BAKE_SIDE;
    let world = runtime.spec.world_size;
    let half = world * 0.5;
    let step = world / side as f32;

    // Pass 1: alturas (normalização min/max do próprio mundo).
    let mut heights = vec![0.0_f32; side * side];
    let mut min = f32::MAX;
    let mut max = f32::MIN;
    for iy in 0..side {
        for ix in 0..side {
            let x = -half + (ix as f32 + 0.5) * step;
            let z = -half + (iy as f32 + 0.5) * step;
            let h = runtime.sample(x, z);
            heights[iy * side + ix] = h;
            min = min.min(h);
            max = max.max(h);
        }
    }

    // Pass 2: cores. Papel/tinta dessaturado — o mapa é um instrumento.
    // Escurecido face ao mundo: o mapa é um instrumento, e um rectângulo
    // creme no canto de uma cena nocturna era a coisa mais brilhante do
    // ecrã. Os blips (ouro, vermelho) precisam de fundo que os deixe falar.
    const WATER: [u8; 3] = [46, 72, 92];
    const VALLEY: [u8; 3] = [80, 98, 64];
    const GRASS: [u8; 3] = [96, 108, 72];
    const HIGH: [u8; 3] = [114, 111, 86];
    const STONE: [u8; 3] = [140, 138, 126];
    const ROAD: [u8; 3] = [166, 150, 110];

    let mut data = Vec::with_capacity(side * side * 4);
    for iy in 0..side {
        for ix in 0..side {
            let x = -half + (ix as f32 + 0.5) * step;
            let z = -half + (iy as f32 + 0.5) * step;
            let h = heights[iy * side + ix];
            let t = ((h - min) / (max - min).max(1e-3)).clamp(0.0, 1.0);
            let rgb = if runtime.in_water(x, z) {
                WATER
            } else if runtime.on_road(x, z) {
                ROAD
            } else if t >= 0.82 {
                STONE
            } else if t >= 0.60 {
                HIGH
            } else if t >= 0.30 {
                GRASS
            } else {
                VALLEY
            };
            // Relevo: uma sombra de encosta barata (diferença com o vizinho
            // a norte) dá silhueta ao terreno — sem ela o mapa é um mosaico
            // de manchas planas.
            let north = if iy > 0 {
                heights[(iy - 1) * side + ix]
            } else {
                h
            };
            let slope = ((h - north) / (max - min).max(1e-3) * 14.0).clamp(-0.32, 0.32);
            let shade = |c: u8| (c as f32 * (1.0 + slope)).clamp(0.0, 255.0) as u8;
            data.extend_from_slice(&[shade(rgb[0]), shade(rgb[1]), shade(rgb[2]), 224]);
        }
    }

    Image::new(
        Extent3d {
            width: side as u32,
            height: side as u32,
            depth_or_array_layers: 1,
        },
        TextureDimension::D2,
        data,
        TextureFormat::Rgba8UnormSrgb,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

/// Radius of the minimap in world meters (authored `range` attribute).
#[derive(Component)]
pub struct MinimapRange(pub f32);

/// Minimap position (UI px offsets from the map center) for a world delta
/// `(dx, dz)` from the player, north-up: camera yaw rotates the plot so the
/// character's facing feels stable. Clamped to the map radius.
pub fn minimap_xy(dx: f32, dz: f32, yaw_deg: f32, range_m: f32, radius_px: f32) -> (f32, f32) {
    let yaw = yaw_deg.to_radians();
    let (sin, cos) = yaw.sin_cos();
    // Camera forward (fx, fz), right (rx, rz) — matches player::process_input.
    let (fx, fz) = (-sin, -cos);
    let (rx, rz) = (cos, -sin);
    let scale = radius_px / range_m.max(1.0);
    let x = (dx * rx + dz * rz) * scale;
    let y_up = (dx * fx + dz * fz) * scale;
    let len = (x * x + y_up * y_up).sqrt();
    let clamp = radius_px * 0.92;
    let (x, y_up) = if len > clamp {
        (x / len * clamp, y_up / len * clamp)
    } else {
        (x, y_up)
    };
    // UI y grows downward.
    (x, -y_up)
}

/// Minimap arrow rotation (radians, clockwise) for a camera yaw — the arrow
/// points where the camera faces, on a north-up map.
pub fn arrow_rotation_rad(yaw_deg: f32) -> f32 {
    -yaw_deg.to_radians()
}

/// Prende um offset em px ao interior do painel, com uma margem para o blip
/// não ficar meio cortado pela moldura. Alvos fora do enquadramento colam-se
/// à borda a apontar a direcção — que é a única coisa útil a dizer sobre eles.
fn clamp_to_panel(x: f32, z: f32) -> (f32, f32) {
    let (max_x, max_y) = (MINIMAP_W * 0.5 - 7.0, MINIMAP_H * 0.5 - 7.0);
    (x.clamp(-max_x, max_x), z.clamp(-max_y, max_y))
}

/// True quando o ponto ainda está dentro do enquadramento (sem clamp).
fn inside_panel(x: f32, z: f32) -> bool {
    x.abs() <= MINIMAP_W * 0.5 && z.abs() <= MINIMAP_H * 0.5
}

/// Posiciona um blip (ou esconde-o) só quando o valor MUDA.
///
/// O Bevy marca `Changed` na escrita mesmo com o valor igual: escrever
/// `*transform`/`*visibility` incondicionalmente punha o Taffy a refazer o
/// layout do painel do HUD em todos os frames, mesmo com o herói parado e
/// nenhum dot a mexer-se. `None` = sem alvo, blip escondido.
fn set_blip(transform: &mut UiTransform, visibility: &mut Visibility, wanted: Option<(f32, f32)>) {
    match wanted {
        Some((tx, tz)) => {
            let wanted = UiTransform::from_translation(Val2::new(Val::Px(tx), Val::Px(tz)));
            if *transform != wanted {
                *transform = wanted;
            }
            if *visibility != Visibility::Visible {
                *visibility = Visibility::Visible;
            }
        }
        None => {
            if *visibility != Visibility::Hidden {
                *visibility = Visibility::Hidden;
            }
        }
    }
}

/// Animate the minimap: o mapa DESLIZA sob o painel (o herói fica sempre no
/// centro, virado para onde a câmara olha), com blips de NPC, de hostis e a
/// âncora do marco assinado por cima.
///
/// A versão anterior desenhava o mundo inteiro estático num disco de 132 px:
/// 4 km em 132 px são 30 m por píxel, e o resultado era um brasão bonito onde
/// andar 50 m não movia nada. Um mini-mapa que não se mexe não é um mapa.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn hud_minimap_update(
    mut commands: Commands,
    mut images: ResMut<Assets<Image>>,
    cameras: Query<&OrbitCamera>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<&GlobalTransform, With<DialogueNpc>>,
    threats: Query<&GlobalTransform, With<crate::ai::EnemyCreature>>,
    waypoint: Option<Res<crate::travel::Waypoint>>,
    runtime: Option<Res<TerrainRuntime>>,
    maps: Query<
        Entity,
        (
            // O painel é o único nó com `MinimapRange` — sem este filtro a
            // query casava TODOS os nós de UI e `single()` falhava em
            // silêncio todos os frames (regressão apanhada no re-shoot r5).
            With<MinimapRange>,
            Without<MinimapArrow>,
            Without<MinimapDot>,
            Without<MinimapTerrain>,
        ),
    >,
    mut terrain: Query<
        &mut UiTransform,
        (
            With<MinimapTerrain>,
            Without<MinimapArrow>,
            Without<MinimapDot>,
            Without<MinimapAnchor>,
            Without<MinimapThreat>,
        ),
    >,
    mut arrow: Query<
        &mut UiTransform,
        (
            With<MinimapArrow>,
            Without<MinimapDot>,
            Without<MinimapAnchor>,
            Without<MinimapThreat>,
        ),
    >,
    mut anchor: Query<
        (&mut UiTransform, &mut Visibility),
        (
            With<MinimapAnchor>,
            Without<MinimapDot>,
            Without<MinimapArrow>,
            Without<MinimapThreat>,
        ),
    >,
    mut dots: Query<
        (&mut UiTransform, &mut Visibility),
        (
            With<MinimapDot>,
            Without<MinimapAnchor>,
            Without<MinimapArrow>,
            Without<MinimapThreat>,
        ),
    >,
    mut blips: Query<
        (&mut UiTransform, &mut Visibility),
        (
            With<MinimapThreat>,
            Without<MinimapDot>,
            Without<MinimapAnchor>,
            Without<MinimapArrow>,
        ),
    >,
    mut baked: Local<Option<Handle<Image>>>,
) {
    let Some(cam) = cameras.iter().next() else {
        return;
    };
    let Some(mapper) = players.iter().next() else {
        return;
    };
    let player_pos = mapper.translation();
    let Ok(map_entity) = maps.single() else {
        return;
    };
    let scale = minimap_scale();

    // Bake one-shot: no primeiro frame em que o terreno já existe. O custo
    // é de carregamento, nunca de gameplay.
    if baked.is_none() {
        if let Some(runtime) = runtime.as_deref() {
            let handle = images.add(bake_terrain_image(runtime));
            let span_px = runtime.spec.world_size * scale;
            commands.entity(map_entity).with_children(|map| {
                map.spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        left: Val::Percent(50.0),
                        top: Val::Percent(50.0),
                        margin: UiRect::px(-span_px * 0.5, 0.0, -span_px * 0.5, 0.0),
                        width: Val::Px(span_px),
                        height: Val::Px(span_px),
                        ..Default::default()
                    },
                    ImageNode {
                        image: handle.clone(),
                        ..Default::default()
                    },
                    // O with_children dos commands APENDA o filho no fim da
                    // lista — sem Z negativo ele desenha POR CIMA da seta e
                    // dos pontos (o clip não reordena nada).
                    ZIndex(-1),
                    UiTransform::IDENTITY,
                    Name::new("hud:minimap:terrain"),
                    MinimapTerrain,
                ));
            });
            *baked = Some(handle);
        }
    }
    // Sem terreno ainda (mundo a carregar): o resto do HUD do mapa espera —
    // pontos sobre vidro escuro sem contexto era o erro que o crítico apanhou.
    if baked.is_none() {
        return;
    };

    // O mapa desliza ao contrário do herói: o herói é o centro do painel.
    if let Ok(mut transform) = terrain.single_mut() {
        let wanted = UiTransform::from_translation(Val2::new(
            Val::Px(-player_pos.x * scale),
            Val::Px(-player_pos.z * scale),
        ));
        if *transform != wanted {
            *transform = wanted;
        }
    }

    // Seta do jogador: fixa no centro, orientada pela câmara.
    if let Ok(mut transform) = arrow.single_mut() {
        let wanted = UiTransform {
            translation: Val2::ZERO,
            rotation: Rot2::radians(arrow_rotation_rad(cam.yaw_deg)),
            scale: Vec2::ONE,
        };
        if *transform != wanted {
            *transform = wanted;
        }
    }

    // Blips relativos ao herói (o mapa é north-up, sem rotação).
    let offset = |x: f32, z: f32| ((x - player_pos.x) * scale, (z - player_pos.z) * scale);

    // Pontos de quest: NPCs, os mais próximos primeiro.
    let mut near: Vec<(f32, f32)> = npcs
        .iter()
        .map(|t| offset(t.translation().x, t.translation().z))
        .filter(|(x, z)| inside_panel(*x, *z))
        .collect();
    // Distância AO QUADRADO: `hypot` faz um `sqrt` por comparação (O(n log n)
    // raízes por frame) e a ordem relativa é a mesma.
    near.sort_by(|a, b| {
        (a.0 * a.0 + a.1 * a.1)
            .partial_cmp(&(b.0 * b.0 + b.1 * b.1))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    for (index, (mut transform, mut visibility)) in dots.iter_mut().enumerate() {
        set_blip(
            &mut transform,
            &mut visibility,
            near.get(index).map(|&(x, z)| clamp_to_panel(x, z)),
        );
    }

    // Hostis: o que um mapa de mundo aberto tem de dizer antes de tudo o
    // resto é "há alguém ali". Só os que cabem no enquadramento.
    let mut hostiles: Vec<(f32, f32)> = threats
        .iter()
        .map(|t| offset(t.translation().x, t.translation().z))
        .filter(|(x, z)| inside_panel(*x, *z))
        .collect();
    hostiles.sort_by(|a, b| {
        (a.0 * a.0 + a.1 * a.1)
            .partial_cmp(&(b.0 * b.0 + b.1 * b.1))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    for (index, (mut transform, mut visibility)) in blips.iter_mut().enumerate() {
        set_blip(
            &mut transform,
            &mut visibility,
            hostiles.get(index).map(|&(x, z)| clamp_to_panel(x, z)),
        );
    }

    // Âncora do marco assinado: presa à borda quando fica fora do
    // enquadramento — continua a dizer a direcção.
    if let Ok((mut transform, mut visibility)) = anchor.single_mut() {
        let wanted = waypoint
            .as_deref()
            .and_then(|w| w.position)
            .map(|position| {
                let (x, z) = offset(position.x, position.z);
                clamp_to_panel(x, z)
            });
        set_blip(&mut transform, &mut visibility, wanted);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_clamp_to_panel_pins_offscreen_targets_to_the_frame() {
        // Dentro do enquadramento: passa intacto.
        let (x, y) = clamp_to_panel(20.0, -15.0);
        assert!(approx(x, 20.0) && approx(y, -15.0));
        assert!(inside_panel(20.0, -15.0));
        // Muito a leste: cola-se à borda direita, mantendo o Y.
        let (x, y) = clamp_to_panel(9000.0, 10.0);
        assert!(approx(x, MINIMAP_W * 0.5 - 7.0), "x={x}");
        assert!(approx(y, 10.0));
        assert!(!inside_panel(9000.0, 10.0));
    }

    #[test]
    fn test_minimap_scale_puts_the_authored_span_across_the_panel() {
        let scale = minimap_scale();
        // A largura do painel cobre exactamente MINIMAP_VIEW_M metros.
        assert!(approx(MINIMAP_VIEW_M * scale, MINIMAP_W));
        // E um alvo a meio caminho cai a meio do raio.
        assert!(approx(MINIMAP_VIEW_M * 0.5 * scale, MINIMAP_W * 0.5));
    }

    #[test]
    fn test_minimap_xy_north_up() {
        let radius = 68.0;
        // NPC directly north (−Z) of the player, camera facing north:
        // dot sits straight up on the map (UI y negative).
        let (x, y) = minimap_xy(0.0, -30.0, 0.0, 60.0, radius);
        assert!(approx(x, 0.0) && y < 0.0 && approx(y, -34.0), "({x},{y})");
        // NPC to the east: dot to the right regardless of camera yaw.
        let (x, _) = minimap_xy(30.0, 0.0, 0.0, 60.0, radius);
        assert!(x > 0.0);
        // Camera swung 180°: the same north NPC now plots below center.
        let (_, y) = minimap_xy(0.0, -30.0, 180.0, 60.0, radius);
        assert!(y > 0.0);
        // Beyond range clamps inside the circle.
        let (x, y) = minimap_xy(0.0, -500.0, 0.0, 60.0, radius);
        assert!((x * x + y * y).sqrt() <= radius * 0.93);
    }

    #[test]
    fn test_arrow_rotation_points_facing() {
        // Facing north → arrow up; facing east (yaw −90) → rotated +90°.
        assert!(approx(arrow_rotation_rad(0.0), 0.0));
        assert!(approx(
            arrow_rotation_rad(-90.0),
            std::f32::consts::FRAC_PI_2
        ));
    }
}
