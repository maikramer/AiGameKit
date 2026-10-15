//! Shared HUD art and drawing helpers: the display font, generated
//! textures (panel gradient, minimap arrow), the AAA panel palette and the
//! text/positioning primitives every HUD module builds on.

use bevy::asset::RenderAssetUsages;
use bevy::image::Image;
use bevy::prelude::*;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat};
use bevy::ui::BoxShadow;

/// Display font for the whole HUD (Cinzel Bold, OFL, latin subset) — a
/// monospace engine default reads as "programmer demo"; a display serif
/// reads as a shipped game.
pub static DISPLAY_FONT: &[u8] = include_bytes!("../../assets/fonts/cinzel-700.ttf");

/// Lazily-created HUD art: display font + generated panel gradient + minimap
/// arrow + minimap markers. Created once per process (single world per run).
#[derive(Resource, Clone)]
pub struct HudAssets {
    pub font: Handle<Font>,
    pub panel_gradient: Handle<Image>,
    pub arrow: Handle<Image>,
    /// Marcador de quest no minimapa: losango dourado com "!" de tinta.
    pub quest_marker: Handle<Image>,
    /// Âncora do marco assinado no minimapa: pin de mapa dourado furado.
    pub map_pin: Handle<Image>,
}

impl HudAssets {
    pub fn get(world: &mut World) -> HudAssets {
        if let Some(existing) = world.get_resource::<HudAssets>() {
            return existing.clone();
        }
        let font = world
            .resource_mut::<Assets<Font>>()
            .add(Font::from_bytes(DISPLAY_FONT.to_vec()));
        let panel_gradient = world
            .resource_mut::<Assets<Image>>()
            .add(panel_gradient_image());
        let arrow = world.resource_mut::<Assets<Image>>().add(arrow_image());
        let quest_marker = world
            .resource_mut::<Assets<Image>>()
            .add(quest_marker_image());
        let map_pin = world.resource_mut::<Assets<Image>>().add(map_pin_image());
        let assets = HudAssets {
            font,
            panel_gradient,
            arrow,
            quest_marker,
            map_pin,
        };
        world.insert_resource(assets.clone());
        assets
    }
}

/// 2×48 white ramp (alpha 255 → ~140): tinted by an `ImageNode` it turns any
/// flat panel into a vertical gradient — the cheap glossy tell.
fn panel_gradient_image() -> Image {
    let (w, h) = (2usize, 48usize);
    let mut data = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        let t = y as f32 / (h - 1) as f32;
        let alpha = (255.0 * (1.0 - 0.45 * t)) as u8;
        for _ in 0..w {
            data.extend_from_slice(&[255, 255, 255, alpha]);
        }
    }
    Image::new(
        Extent3d {
            width: w as u32,
            height: h as u32,
            depth_or_array_layers: 1,
        },
        TextureDimension::D2,
        data,
        TextureFormat::Rgba8UnormSrgb,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

/// 36×36 upward triangle with a dark outline and soft AA — the minimap
/// player arrow (bevy_ui has no polygon nodes).
pub(crate) fn arrow_image() -> Image {
    let s = 36usize;
    let (tip, left, right): ((f32, f32), (f32, f32), (f32, f32)) =
        ((17.5, 2.0), (4.0, 31.0), (32.0, 31.0));
    // Signed half-planes; inside when all three are ≥ 0 (clockwise order).
    let edges = [(tip, right), (right, left), (left, tip)];
    let mut data = Vec::with_capacity(s * s * 4);
    for y in 0..s {
        for x in 0..s {
            let p = (x as f32 + 0.5, y as f32 + 0.5);
            let mut min_dist = f32::INFINITY;
            let mut inside = true;
            for (a, b) in edges {
                let ab = (b.0 - a.0, b.1 - a.1);
                let len = (ab.0 * ab.0 + ab.1 * ab.1).sqrt().max(1e-5_f32);
                let side = (p.0 - a.0) * ab.1 - (p.1 - a.1) * ab.0;
                let dist = -side / len; // signed, pixels (y-down flip)
                if side > 0.0 {
                    inside = false;
                }
                min_dist = min_dist.min(dist.abs());
            }
            let coverage = (min_dist + 0.5).clamp(0.0, 1.0);
            let (r, g, b, a) = if inside && min_dist > 1.5 {
                (248u8, 250u8, 255u8, 255u8) // white core
            } else if inside {
                (56u8, 62u8, 74u8, 255u8) // dark outline just inside the edge
            } else {
                (56u8, 62u8, 74u8, (coverage * 200.0) as u8) // AA fringe
            };
            data.extend_from_slice(&[r, g, b, a]);
        }
    }
    Image::new(
        Extent3d {
            width: s as u32,
            height: s as u32,
            depth_or_array_layers: 1,
        },
        TextureDimension::D2,
        data,
        TextureFormat::Rgba8UnormSrgb,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

// ------------------------------------------------------------- palette

/// Renderer de ícones com anti-aliasing de caixa: desenha a `ss`× a
/// resolução final e faz a média dos sub-píxeis. O `shade` devolve a cor
/// RGBA por ponto em coordenadas FINAIS — é tudo o que um glifo precisa.
pub(crate) fn supersampled_image(
    side: usize,
    ss: usize,
    shade: impl Fn(f32, f32) -> [u8; 4],
) -> Image {
    let big = side * ss;
    let mut sum = vec![[0u32; 4]; side * side];
    for sy in 0..big {
        let fy = (sy / ss) as f32 + (sy % ss) as f32 / ss as f32 + 0.5 / ss as f32;
        for sx in 0..big {
            let fx = (sx / ss) as f32 + (sx % ss) as f32 / ss as f32 + 0.5 / ss as f32;
            let c = shade(fx, fy);
            let cell = &mut sum[(sy / ss) * side + (sx / ss)];
            for (ch, value) in c.iter().enumerate() {
                cell[ch] += *value as u32;
            }
        }
    }
    let data = sum
        .into_iter()
        .flat_map(|cell| cell.map(|v| (v / (ss * ss) as u32) as u8))
        .collect();
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

/// Marcador de quest do minimapa: losango dourado (placa que se lê sobre
/// terreno claro E escuro) com um "!" de tinta — o vocabulário universal
/// de "objectivo aqui", em vez de um ponto cru.
///
/// Gerado (não asset) por decisão: o HUD é da engine, corre em QUALQUER
/// mundo, e um asset ausente deixaria os pontos de quest invisíveis em
/// silêncio — exactamente a falha que o `arrow_image` já evitou.
pub(crate) fn quest_marker_image() -> Image {
    const GOLD: [u8; 4] = [216, 180, 106, 255];
    const INK: [u8; 4] = [43, 29, 16, 255];
    supersampled_image(48, 4, |x, y| {
        let c = 24.0;
        let (dx, dy) = (x - c, y - c);
        // Superelipse n≈3.2: losango de cantos amaciados.
        let d = (dx.abs().powf(3.2) + dy.abs().powf(3.2)).powf(1.0 / 3.2);
        let edge = 17.5;
        if d > edge + 1.0 {
            return [0, 0, 0, 0];
        }
        // "!": haste + ponto, em tinta sobre o ouro.
        let bar = dx.abs() < 2.6 && dy > -10.0 && dy < 2.0;
        let dot = dx * dx + (dy - 8.0) * (dy - 8.0) < 3.2 * 3.2;
        if bar || dot {
            return INK;
        }
        if d > edge - 1.6 {
            return INK; // contorno de tinta
        }
        GOLD
    })
}

/// Âncora do marco assinado (A Nota) no minimapa: pin de mapa dourado
/// furado — a forma que qualquer jogador lê como "destino", e o par
/// "quest = !, destino = pin" distingue os dois à primeira.
pub(crate) fn map_pin_image() -> Image {
    const GOLD: [u8; 4] = [232, 196, 118, 255];
    const INK: [u8; 4] = [43, 29, 16, 255];
    supersampled_image(48, 4, |x, y| {
        let c = 24.0;
        let (dx, dy) = (x - c, y - c);
        let head_r = 13.5;
        let head = dx * dx + (dy + 5.0) * (dy + 5.0) < head_r * head_r;
        // Cauda: triângulo do fundo da cabeça à ponta (24, 42).
        let tail = (-1.0..=18.0).contains(&dy) && {
            let t = ((dy + 1.0) / 19.0).clamp(0.0, 1.0);
            dx.abs() < head_r * (1.0 - t) * 0.9
        };
        if !(head || tail) {
            return [0, 0, 0, 0];
        }
        // Buraco do pin: tinta.
        if dx * dx + (dy + 5.0) * (dy + 5.0) < 5.2 * 5.2 {
            return INK;
        }
        // Contorno: borda exterior a tinta (a cauda afina para a ponta).
        let head_edge = dx * dx + (dy + 5.0) * (dy + 5.0) < (head_r - 1.6) * (head_r - 1.6);
        let t = ((dy + 1.0) / 19.0).clamp(0.0, 1.0);
        let tail_edge = (-1.0..=16.5).contains(&dy) && dx.abs() < head_r * (1.0 - t) * 0.9 - 1.4;
        if !(head_edge || tail_edge) {
            return INK;
        }
        GOLD
    })
}

// ------------------------------------------------------------- palette

/// Warm dark base under the gradient of every AAA panel.
pub(crate) fn panel_base() -> BackgroundColor {
    BackgroundColor(Color::srgba(0.055, 0.05, 0.04, 0.9))
}

/// Panel tint carried by the gradient overlay (slightly lighter on top).
pub(crate) fn panel_tint() -> Color {
    Color::srgba(0.13, 0.12, 0.105, 0.82)
}

/// Light inner border (top bevel highlight).
pub(crate) fn panel_edge() -> BorderColor {
    BorderColor::all(Color::srgba(1.0, 0.96, 0.85, 0.16))
}

/// Soft drop shadow under every panel — the #1 AAA tell.
pub(crate) fn panel_shadow() -> BoxShadow {
    BoxShadow::new(
        Color::srgba(0.0, 0.0, 0.0, 0.5),
        Val::Px(2.0),
        Val::Px(4.0),
        Val::ZERO,
        Val::Px(8.0),
    )
}

/// Panel gradient overlay CHILD bundle (spans the whole panel, tinted).
/// Spawn as the panel's FIRST child — content children render above it.
pub(crate) fn gradient_overlay(hud: &HudAssets, radius: f32) -> impl Bundle {
    (
        Node {
            position_type: PositionType::Absolute,
            left: Val::Px(0.0),
            top: Val::Px(0.0),
            right: Val::Px(0.0),
            bottom: Val::Px(0.0),
            border_radius: BorderRadius::all(Val::Px(radius)),
            ..Default::default()
        },
        ImageNode {
            image: hud.panel_gradient.clone(),
            color: panel_tint(),
            ..Default::default()
        },
    )
}

/// Styled text with the HUD display font.
pub(crate) fn label(
    hud: &HudAssets,
    text: impl Into<String>,
    size: f32,
    color: Color,
) -> impl Bundle {
    (
        Text::new(text.into()),
        TextColor(color),
        TextFont {
            font: hud.font.clone().into(),
            font_size: size.into(),
            ..Default::default()
        },
    )
}

/// An absolutely-positioned child centered on its parent anchor point via a
/// −50 % self translation (compass letters, minimap dots).
pub(crate) fn centered_at(left: Val, top: Val) -> Node {
    Node {
        position_type: PositionType::Absolute,
        left,
        top,
        ..Default::default()
    }
}
