//! Mapa regional [M] — o mapa-múndi da revelação (retorno visual das
//! atalaias de agrimensura).
//!
//! O minimapa-radar (140 px) mostra 2–3 losangos; activar uma atalaia tem de
//! ACENDER O MAPA: um ecrã full-screen com a cartografia real do mundo —
//! topografia do heightmap (tinta hipsométrica + sombra de encosta), água e
//! estradas do terreno carvado, marcos da Nota como pins — onde os biomas
//! NÃO revelados ficam sob névoa de mapa até a atalaia do bioma falar.
//!
//! - **Base cartográfica real**: mesma fonte do minimapa ([`TerrainRuntime`],
//!   que já carrega o `.ahgt` + carve de lagos/rios/estradas) a 1024² —
//!   cozida FORA da tecla, em fatias de ~96 linhas/frame (abrir [M] não paga
//!   hitch nenhum; a revelação re-casca só a passagem de cor, com as
//!   máscaras de água/estrada em cache).
//! - **Revelação por bioma**: um píxel fica escurecido quando cai DENTRO de
//!   um `<BiomeRegion>` cujo bioma tem atalaia ([`RevealedBiomes::keyed_biomes`])
//!   e ainda não foi revelado. O vale (fora de todos os polígonos) e o raio
//!   da vila ficam SEMPRE visíveis. Mundos sem atalaias não geram névoa
//!   nenhuma (back-compat da ronda 1).
//! - **Pins**: atalaias + marcos disponíveis ([`travel::LandmarkBlips`] — o
//!   mesmo gating do minimapa/bússola, assinados a ouro via [`travel::NotaLog`]),
//!   faróis de missão ([`quest_guide::QuestGuide`] — tracking que já existe),
//!   a praça da vila ([`feedback::RespawnCatalog`]) e o herói.
//! - **Interação mínima**: cursor sobre o pin → rótulo; [M]/Esc/[L] fecham.
//! - **Persistência**: ZERO estado novo — a revelação é derivada de
//!   [`travel::RevealedBiomes`], que já viaja no save.

use std::collections::HashSet;

use bevy::asset::RenderAssetUsages;
use bevy::prelude::*;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat};
use bevy::window::PrimaryWindow;

use crate::player::Player;
use crate::profiler::{Group, timed};
use crate::travel::{LandmarkBlips, NotaLog, RevealedBiomes};

/// Lado da cozinha cartográfica (px por lado da textura). 1024 sobre um
/// mundo de 4096 m = 4 m/texel — a estrada e a orla de um lago lêem-se
/// full-screen. O dobro do bake do minimapa, pago em fatias.
pub const MAP_BAKE_SIDE: usize = 1024;

/// Linhas cozidas por frame em cada passagem (alturas e cor). 128 divide
/// 1024 à percentagem (8 frames por passagem); ~131 k amostras/passage —
/// um dígito baixo de ms, invisível no frame.
const BAKE_ROWS_PER_FRAME: usize = 128;

/// Lado do painel do mapa (px). Full-screen centrado; a 720p deixa margem
/// para o cabeçalho e o rodapé.
pub const MAP_PANEL_PX: f32 = 600.0;

/// Raio (m) em torno da praça da vila que NUNCA entra em névoa — a zona
/// habitada é o ponto de partida do mapa, atalaia ou não.
pub const VILLAGE_CLEAR_M: f32 = 70.0;

/// Raio de apanha do cursor sobre um pin (px).
pub const PIN_PICK_RADIUS_PX: f32 = 14.0;

/// Expansão (m) dos polígonos de bioma SÓ no teste de névoa: os píxeis
/// exactamente SOBRE a aresta de um `<BiomeRegion>` caem fora do point-
/// in-polygon (ray-cast em aresta é indefinido) e ficavam ACESOS — uma
/// linha diagonal de 1-2 px atravessava a névoa nas fronteiras dos
/// polígonos (as arestas dos wedges do simple-rpg são diagonais x=z).
pub const FOG_EDGE_EXPAND_M: f32 = 12.0;

// ── pura: vocabulário da revelação ──────────────────────────────────────

/// Chave de revelação de uma `<BiomeRegion>`: o mundo autoria ids com
/// HÍFEN (`dark-forest`, `frozen-peaks`) e a Nota/atalaias usam UNDERSCORE
/// (`dark_forest` — `tower-reveals`). A ponte é normalizar o separador; sem
/// isto o mapa nunca casava a região com o bioma revelado.
pub fn reveal_key(region_id: &str) -> String {
    region_id.replace('-', "_")
}

/// Um bioma está sob névoa? Tem de TER atalaia (a chave existe) e não ter
/// sido revelado. Sem atalaias no mundo, `keyed` é vazio e NADA esconde —
/// o mesmo back-compat do gating dos marcos.
pub fn region_hidden(revealed: &RevealedBiomes, keyed: &HashSet<&str>, region_key: &str) -> bool {
    keyed.contains(region_key) && !revealed.0.contains(region_key)
}

/// Um píxel em névoa acende perto da vila? O raio de limpeza da praça ganha
/// sempre ao gating — a zona habitada nunca é desconhecida.
pub fn pixel_hidden(hidden_region: bool, village_dist: f32) -> bool {
    hidden_region && village_dist > VILLAGE_CLEAR_M
}

// ── pura: tinta cartográfica ────────────────────────────────────────────

/// Tinta hipsométrica (papel/tinta do mapa): rampa vale→relva→alto→pedra→
/// neve normalizada 0..1. Mais clara que o minimapa de propósito — o mapa
/// regional é um instrumento de leitura, não um widget de canto.
pub fn map_land_tint(t: f32) -> [u8; 3] {
    const VALLEY: [u8; 3] = [86, 104, 66];
    const GRASS: [u8; 3] = [110, 122, 78];
    const HIGH: [u8; 3] = [146, 134, 94];
    const STONE: [u8; 3] = [168, 162, 144];
    const SNOW: [u8; 3] = [216, 214, 204];
    match t {
        x if x >= 0.88 => SNOW,
        x if x >= 0.70 => STONE,
        x if x >= 0.42 => HIGH,
        x if x >= 0.18 => GRASS,
        _ => VALLEY,
    }
}

/// Água (ardósia) e estrada (ocre) sobre a tinta — o mesmo vocabulário do
/// minimapa, para o jogador ler os dois como o MESMO mapa.
pub const MAP_WATER: [u8; 3] = [54, 78, 98];
pub const MAP_ROAD: [u8; 3] = [178, 158, 112];

/// Sombra de encosta barata: diferença com o vizinho a norte, clampada —
/// sem ela a topografia é um mosaico de manchas planas.
pub fn hillshade(height: f32, north: f32, span: f32) -> f32 {
    ((height - north) / span.max(1e-3) * 14.0).clamp(-0.32, 0.32)
}

/// Aplica a sombra a um triplo RGB.
pub fn shade_pixel(rgb: [u8; 3], slope: f32) -> [u8; 3] {
    [
        (rgb[0] as f32 * (1.0 + slope)).clamp(0.0, 255.0) as u8,
        (rgb[1] as f32 * (1.0 + slope)).clamp(0.0, 255.0) as u8,
        (rgb[2] as f32 * (1.0 + slope)).clamp(0.0, 255.0) as u8,
    ]
}

/// Polígono EXPANDIDO a partir do centróide (`expand_m` para fora) — só
/// para a névoa tapar a aresta do ray-cast; o gating de gameplay usa o
/// polígono cru.
fn expanded_polygon(poly: &[[f32; 2]], expand_m: f32) -> Vec<[f32; 2]> {
    let n = poly.len().max(1) as f32;
    let (mut cx, mut cz) = (0.0_f32, 0.0_f32);
    for p in poly {
        cx += p[0];
        cz += p[1];
    }
    cx /= n;
    cz /= n;
    poly.iter()
        .map(|p| {
            let (dx, dz) = (p[0] - cx, p[1] - cz);
            let len = (dx * dx + dz * dz).sqrt().max(1e-3);
            [p[0] + dx / len * expand_m, p[1] + dz / len * expand_m]
        })
        .collect()
}

/// Um píxel está sob névoa? Testa as regiões cruas E as expandidas (a
/// expansão só LARGA a névoa — nunca acende um píxel de bioma oculto).
fn fogged_at(
    x: f32,
    z: f32,
    region_list: &[crate::worldsys::BiomeRegionData],
    expanded: &[Vec<[f32; 2]>],
    revealed: &RevealedBiomes,
    keyed: &HashSet<&str>,
    village: Option<Vec2>,
) -> bool {
    let mut hidden = false;
    for (r, exp) in region_list.iter().zip(expanded) {
        let key = reveal_key(&r.id);
        let region_hidden = region_hidden(revealed, keyed, &key);
        let inside = crate::ambient::point_in_polygon(x, z, &r.polygon)
            || (region_hidden && crate::ambient::point_in_polygon(x, z, exp));
        if inside {
            if region_hidden {
                hidden = true;
            }
            break;
        }
    }
    if !hidden {
        return false;
    }
    let dist = village
        .map(|v| (Vec2::new(x, z) - v).length())
        .unwrap_or(f32::INFINITY);
    pixel_hidden(true, dist)
}

/// Névoa de mapa: um píxel escondido afunda para quase-preto (fica um resto
/// de textura — lê-se "há terra lá", não "não há dados").
pub fn fogged_pixel(rgb: [u8; 3]) -> [u8; 3] {
    const FOG: [f32; 3] = [13.0, 15.0, 20.0];
    [
        (rgb[0] as f32 * 0.14 + FOG[0]) as u8,
        (rgb[1] as f32 * 0.14 + FOG[1]) as u8,
        (rgb[2] as f32 * 0.14 + FOG[2]) as u8,
    ]
}

// ── pura: mapeamento mundo↔painel e apanha de pins ─────────────────────

/// Mundo (x, z) → px RELATIVOS AO CENTRO do painel (o referencial das
/// âncoras 50%/50% dos pins). Norte é -Z (para cima no ecrã), este é +x
/// (direita).
pub fn world_to_panel(x: f32, z: f32, half: f32, panel: f32) -> (f32, f32) {
    let scale = panel / (half * 2.0);
    (
        (x + half) * scale - panel * 0.5,
        (z + half) * scale - panel * 0.5,
    )
}

/// O pin mais próximo do cursor dentro do raio (o mais perto ganha).
pub fn pick_pin(spots: &[(f32, f32, String)], cx: f32, cy: f32) -> Option<(f32, f32, String)> {
    let mut best: Option<(f32, (f32, f32, String))> = None;
    for &(x, y, ref label) in spots {
        let d2 = (x - cx) * (x - cx) + (y - cy) * (y - cy);
        if d2 > PIN_PICK_RADIUS_PX * PIN_PICK_RADIUS_PX {
            continue;
        }
        if best.as_ref().is_none_or(|(bd, _)| d2 < *bd) {
            best = Some((d2, (x, y, label.clone())));
        }
    }
    best.map(|(_, spot)| spot)
}

// ── ECS: estado e componentes ───────────────────────────────────────────

/// Aberto/fechado do mapa (espelhado em [`crate::menus::MenusOpen::map`],
/// que rouba o input ao movimento/melee/hotbar).
#[derive(Debug, Clone, Copy, Resource, Default)]
pub struct RegionalMapState {
    pub open: bool,
}

/// Cozinha cartográfica em fatias — alturas primeiro, cor depois; a imagem
/// só entra na Assets quando a passagem completa (UM upload por bake).
#[derive(Resource)]
struct MapBake {
    side: usize,
    half: f32,
    step: f32,
    heights: Vec<f32>,
    min: f32,
    max: f32,
    /// Máscaras de água (bit 0) e estrada (bit 1) — preenchidas na PRIMEIRA
    /// passagem de cor e reutilizadas nas re-cores de revelação (as queries
    /// `in_water`/`on_road` não correm outra vez por píxel).
    masks: Vec<u8>,
    masks_done: bool,
    colors: Vec<u8>,
    next_row: usize,
    phase: BakePhase,
    image: Option<Handle<Image>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BakePhase {
    Heights,
    Colors,
    Ready,
}

impl MapBake {
    fn new(world_size: f32) -> Self {
        let side = MAP_BAKE_SIDE;
        Self {
            side,
            half: world_size * 0.5,
            step: world_size / side as f32,
            heights: vec![0.0; side * side],
            min: f32::MAX,
            max: f32::MIN,
            masks: vec![0; side * side],
            masks_done: false,
            colors: vec![0; side * side * 4],
            next_row: 0,
            phase: BakePhase::Heights,
            image: None,
        }
    }
}

#[derive(Component)]
struct RegionalMapRoot;

/// A moldura do mapa (onde a imagem e os pins penduram) — referência para
/// o hover converter cursor→px locais.
#[derive(Component)]
struct MapFrame;

#[derive(Component)]
struct MapImageNode;

#[derive(Component)]
struct MapPlaceholder;

#[derive(Component)]
struct MapHeader;

#[derive(Component)]
struct MapFooter;

/// Rótulo de hover (segue o pin sob o cursor).
#[derive(Component)]
struct MapLabel;

/// Pin da vila (praça) — sempre visível.
#[derive(Component)]
struct MapPinVillage;

/// Marcador do herói.
#[derive(Component)]
struct MapPinPlayer;

/// Pool de pins de marcos da Nota (atalaia ⇒ separado, maior).
#[derive(Component)]
struct MapPinLandmark;

#[derive(Component)]
struct MapPinTower;

/// Faróis de missão (NPC por aceitar/entregar — [`QuestGuide::beacons`]).
#[derive(Component)]
struct MapPinQuest;

/// Spots do frame (px do painel + rótulo) para o hover — reconstruído
/// enquanto o mapa está aberto.
#[derive(Resource, Default)]
struct MapSpots(Vec<(f32, f32, String)>);

pub struct RegionalMapPlugin;

/// Dados de mundo dos pins agrupados num `SystemParam` — o sistema passa do
/// teto de 16 params em tupla (o mesmo padrão do `save::RpgSaveState`).
#[derive(bevy::ecs::system::SystemParam)]
struct MapPinData<'w> {
    blips: Option<Res<'w, LandmarkBlips>>,
    nota: Option<Res<'w, NotaLog>>,
    guide: Option<Res<'w, crate::quest_guide::QuestGuide>>,
    respawn: Option<Res<'w, crate::feedback::RespawnCatalog>>,
    catalog: Option<Res<'w, crate::travel::LandmarkCatalog>>,
    revealed: Option<Res<'w, RevealedBiomes>>,
    regions: Option<Res<'w, crate::worldsys::BiomeRegions>>,
}

impl Plugin for RegionalMapPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<RegionalMapState>()
            .init_resource::<MapSpots>()
            .init_resource::<crate::worldsys::BiomeRegions>()
            .add_message::<MapOpened>()
            .add_systems(Startup, spawn_regional_map)
            .add_systems(
                Update,
                (
                    timed(Group::Hud, map_key_system),
                    timed(Group::Hud, map_bake_system),
                    timed(Group::Hud, map_pins_system),
                    timed(Group::Hud, map_hover_system),
                ),
            );
    }
}

/// Sinal de abertura (o áudio/log podem ouvir; hoje só marca o evento para
/// testes/QA).
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct MapOpened;

// ── UI: spawn uma vez ───────────────────────────────────────────────────

const PIN_SIZE: f32 = 10.0;
const TOWER_SIZE: f32 = 14.0;
const QUEST_SIZE: f32 = 9.0;
const VILLAGE_SIZE: f32 = 13.0;
const PLAYER_SIZE: f32 = 11.0;

/// Pins de marcos no pool (12 da Nota + folga para mundos maiores).
pub const LANDMARK_PIN_POOL: usize = 18;
/// Pins de atalaias no pool (5 do simple-rpg + folga).
pub const TOWER_PIN_POOL: usize = 6;
/// Faróis de missão no pool.
pub const QUEST_PIN_POOL: usize = 6;

fn pin_node(size: f32, bg: Color, border: Color, rotation: f32) -> impl Bundle {
    (
        Node {
            position_type: PositionType::Absolute,
            left: Val::Percent(50.0),
            top: Val::Percent(50.0),
            width: Val::Px(size),
            height: Val::Px(size),
            border_radius: BorderRadius::all(Val::Px(size * 0.5)),
            ..Default::default()
        },
        BackgroundColor(bg),
        BorderColor::all(border),
        UiTransform {
            rotation: Rot2::radians(rotation),
            ..Default::default()
        },
        Visibility::Hidden,
    )
}

fn map_text(text: &str, size: f32, color: Color) -> impl Bundle {
    (
        Text::new(text),
        TextColor(color),
        TextFont::from_font_size(size),
    )
}

fn spawn_regional_map(mut commands: Commands) {
    let gold = Color::srgb(0.95, 0.65, 0.3);
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(0.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                bottom: Val::Px(0.0),
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.55)),
            // ACIMA de todo o HUD (a seta do guia de missão usa
            // GlobalZIndex(40) e o minimapa ficava por cima do mapa —
            // a stack global do bevy_ui ordena por ESTE componente).
            GlobalZIndex(200),
            Visibility::Hidden,
            Name::new("ui:regional-map"),
            RegionalMapRoot,
        ))
        .with_children(|root| {
            // Moldura do mapa: centrada no ecrã via 50% + meia-largura
            // negativa (o mesmo truque do minimapa), com clip para os pins
            // de borda não vazarem.
            root.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    left: Val::Percent(50.0),
                    top: Val::Percent(50.0),
                    margin: UiRect::px(-MAP_PANEL_PX * 0.5, 0.0, -MAP_PANEL_PX * 0.5, 0.0),
                    width: Val::Px(MAP_PANEL_PX),
                    height: Val::Px(MAP_PANEL_PX),
                    border: UiRect::all(Val::Px(2.0)),
                    border_radius: BorderRadius::all(Val::Px(8.0)),
                    overflow: Overflow::clip(),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.05, 0.05, 0.06, 1.0)),
                BorderColor::all(gold),
                Name::new("ui:regional-map:frame"),
                MapFrame,
            ))
            .with_children(|frame| {
                // Cabeçalho e rodapé DENTRO da moldura (faixas escuras):
                // fora, disputavam o painel de vitais e a hotbar.
                frame.spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        top: Val::Px(0.0),
                        height: Val::Px(30.0),
                        justify_content: JustifyContent::Center,
                        align_items: AlignItems::Center,
                        ..Default::default()
                    },
                    BackgroundColor(Color::srgba(0.03, 0.03, 0.04, 0.82)),
                    map_text("VAEL - MAPA REGIONAL   ([M] / Esc / [L] fecha)", 15.0, gold),
                    MapHeader,
                ));
                frame.spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        bottom: Val::Px(0.0),
                        height: Val::Px(46.0),
                        justify_content: JustifyContent::Center,
                        align_items: AlignItems::Center,
                        ..Default::default()
                    },
                    BackgroundColor(Color::srgba(0.03, 0.03, 0.04, 0.82)),
                    map_text("", 12.0, Color::srgb(0.88, 0.85, 0.75)),
                    MapFooter,
                ));
                frame.spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        left: Val::Percent(35.0),
                        top: Val::Percent(48.0),
                        ..Default::default()
                    },
                    map_text(
                        "a desenhar a cartografia…",
                        15.0,
                        Color::srgb(0.75, 0.72, 0.62),
                    ),
                    MapPlaceholder,
                ));
                // Pin da vila (praça).
                frame.spawn((
                    pin_node(
                        VILLAGE_SIZE,
                        Color::srgba(0.93, 0.88, 0.74, 1.0),
                        Color::srgb(0.45, 0.36, 0.2),
                        0.0,
                    ),
                    Name::new("ui:map-pin-village"),
                    MapPinVillage,
                ));
                // Herói.
                frame.spawn((
                    pin_node(
                        PLAYER_SIZE,
                        Color::WHITE,
                        Color::srgb(0.2, 0.2, 0.2),
                        std::f32::consts::FRAC_PI_4,
                    ),
                    MapPinPlayer,
                ));
                // Pools (assignments por ordem; mais perto do centro 1.º).
                for _ in 0..LANDMARK_PIN_POOL {
                    frame.spawn((
                        pin_node(
                            PIN_SIZE,
                            Color::srgba(0.12, 0.14, 0.12, 0.4),
                            Color::srgb(0.35, 0.8, 0.75),
                            0.0,
                        ),
                        MapPinLandmark,
                    ));
                }
                for _ in 0..TOWER_PIN_POOL {
                    frame.spawn((
                        pin_node(
                            TOWER_SIZE,
                            Color::srgba(0.2, 0.85, 0.78, 1.0),
                            Color::srgb(0.95, 0.98, 0.95),
                            std::f32::consts::FRAC_PI_4,
                        ),
                        MapPinTower,
                    ));
                }
                for _ in 0..QUEST_PIN_POOL {
                    frame.spawn((
                        pin_node(
                            QUEST_SIZE,
                            Color::srgba(0.9, 0.35, 0.3, 1.0),
                            Color::srgb(0.98, 0.9, 0.85),
                            0.0,
                        ),
                        MapPinQuest,
                    ));
                }
                // Rótulo de hover (por cima de tudo no painel).
                frame
                    .spawn((
                        Node {
                            position_type: PositionType::Absolute,
                            left: Val::Percent(50.0),
                            top: Val::Percent(50.0),
                            padding: UiRect::axes(Val::Px(8.0), Val::Px(3.0)),
                            border_radius: BorderRadius::all(Val::Px(6.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.04, 0.04, 0.05, 0.92)),
                        BorderColor::all(Color::srgba(0.85, 0.72, 0.45, 0.5)),
                        UiTransform::IDENTITY,
                        Visibility::Hidden,
                        MapLabel,
                    ))
                    .with_children(|label| {
                        label.spawn((
                            Text::new(""),
                            TextColor(Color::srgb(0.97, 0.92, 0.8)),
                            TextFont::from_font_size(13.0),
                            bevy::ui::widget::TextShadow {
                                offset: Vec2::splat(1.0),
                                color: Color::srgba(0.0, 0.0, 0.0, 0.8),
                            },
                            MapLabelText,
                        ));
                    });
            });
        });
}

#[derive(Component)]
struct MapLabelText;

// ── teclas + espelho MenusOpen ──────────────────────────────────────────

/// [M] abre (com nenhum outro menu ativo) e fecha; Esc/[L] fecham. O
/// espelho em `MenusOpen.map` é o que rouba o input ao herói — o mapa é um
/// menu, não um modo.
fn map_key_system(
    keys: Res<ButtonInput<KeyCode>>,
    mut state: ResMut<RegionalMapState>,
    mut menus: ResMut<crate::menus::MenusOpen>,
    mut root: Query<&mut Visibility, With<RegionalMapRoot>>,
    mut opened: MessageWriter<MapOpened>,
) {
    if keys.just_pressed(KeyCode::KeyM) && (state.open || !menus.any()) {
        state.open = !state.open;
        if state.open {
            opened.write(MapOpened);
        }
    }
    if state.open && (keys.just_pressed(KeyCode::Escape) || keys.just_pressed(KeyCode::KeyL)) {
        state.open = false;
    }
    if menus.map != state.open {
        menus.map = state.open;
    }
    let wanted = if state.open {
        Visibility::Visible
    } else {
        Visibility::Hidden
    };
    for mut visibility in root.iter_mut() {
        if *visibility != wanted {
            *visibility = wanted;
        }
    }
}

// ── cozinha cartográfica em fatias ──────────────────────────────────────

/// Nestas linhas corre a cozinha por frame (alturas OU cor). A passagem de
/// alturas faz `sample` por píxel; a de cor faz point-in-polygon das
/// regiões + máscaras (na 1.ª passagem também `in_water`/`on_road`).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn map_bake_system(
    mut commands: Commands,
    mut images: ResMut<Assets<Image>>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    regions: Option<Res<crate::worldsys::BiomeRegions>>,
    revealed: Option<Res<RevealedBiomes>>,
    catalog: Option<Res<crate::travel::LandmarkCatalog>>,
    respawn: Option<Res<crate::feedback::RespawnCatalog>>,
    mut bake: Option<ResMut<MapBake>>,
    frame: Query<Entity, With<MapFrame>>,
    image_node: Query<Entity, (With<MapImageNode>, Without<MapFrame>)>,
    mut placeholder: Query<&mut Visibility, With<MapPlaceholder>>,
) {
    // Mudou a revelação (atalaia activada, save carregado)? Re-cor a
    // passagem de COR (as alturas e as máscaras ficam).
    let re_reveal = revealed.as_ref().is_some_and(|r| r.is_changed())
        || regions.as_ref().is_some_and(|r| r.is_changed());
    let Some(runtime) = runtime else {
        return;
    };
    // O bake nasce por Commands (o world_size vem do runtime no 1.º frame).
    if bake.is_none() {
        commands.insert_resource(MapBake::new(runtime.spec.world_size));
        return;
    }
    let Some(bake) = bake.as_deref_mut() else {
        return;
    };
    if re_reveal && bake.phase == BakePhase::Ready {
        info!(target: "viber::map", "mapa: revelação mudou — re-casca a passagem de cor");
        bake.phase = BakePhase::Colors;
        bake.next_row = 0;
    }
    // Chaves da névoa: biomas com atalaia (catálogo) + praça da vila.
    let keyed: HashSet<&str> = catalog
        .as_deref()
        .map(|c| RevealedBiomes::keyed_biomes(c))
        .unwrap_or_default();
    let village: Option<Vec2> = respawn
        .as_deref()
        .and_then(|r| r.0.first().map(|(p, _)| *p));

    match bake.phase {
        BakePhase::Heights => {
            let end = (bake.next_row + BAKE_ROWS_PER_FRAME).min(bake.side);
            for iy in bake.next_row..end {
                for ix in 0..bake.side {
                    let x = -bake.half + (ix as f32 + 0.5) * bake.step;
                    let z = -bake.half + (iy as f32 + 0.5) * bake.step;
                    let h = runtime.sample(x, z);
                    bake.heights[iy * bake.side + ix] = h;
                    bake.min = bake.min.min(h);
                    bake.max = bake.max.max(h);
                }
            }
            bake.next_row = end;
            if bake.next_row >= bake.side {
                bake.phase = BakePhase::Colors;
                bake.next_row = 0;
            }
        }
        BakePhase::Colors => {
            let span = (bake.max - bake.min).max(1e-3);
            let region_list: &[crate::worldsys::BiomeRegionData] =
                regions.as_deref().map(|r| r.list.as_slice()).unwrap_or(&[]);
            let expanded: Vec<Vec<[f32; 2]>> = region_list
                .iter()
                .map(|r| expanded_polygon(&r.polygon, FOG_EDGE_EXPAND_M))
                .collect();
            let fallback_revealed = RevealedBiomes::default();
            let revealed_ref = revealed.as_deref().unwrap_or(&fallback_revealed);
            let end = (bake.next_row + BAKE_ROWS_PER_FRAME).min(bake.side);
            for iy in bake.next_row..end {
                for ix in 0..bake.side {
                    let idx = iy * bake.side + ix;
                    let x = -bake.half + (ix as f32 + 0.5) * bake.step;
                    let z = -bake.half + (iy as f32 + 0.5) * bake.step;
                    // Máscaras caras (água/estrada) só na PRIMEIRA passagem.
                    let mask = if bake.masks_done {
                        bake.masks[idx]
                    } else {
                        let mut m = 0_u8;
                        if runtime.in_water(x, z) {
                            m |= 1;
                        }
                        if runtime.on_road(x, z) {
                            m |= 2;
                        }
                        bake.masks[idx] = m;
                        m
                    };
                    let t = ((bake.heights[idx] - bake.min) / span).clamp(0.0, 1.0);
                    let mut rgb = if mask & 1 != 0 {
                        MAP_WATER
                    } else if mask & 2 != 0 {
                        MAP_ROAD
                    } else {
                        map_land_tint(t)
                    };
                    let north = if iy > 0 {
                        bake.heights[(iy - 1) * bake.side + ix]
                    } else {
                        bake.heights[idx]
                    };
                    rgb = shade_pixel(rgb, hillshade(bake.heights[idx], north, span));
                    // Névoa de revelação: região com atalaia ainda escura
                    // (arestas alargadas — ver FOG_EDGE_EXPAND_M), salvo o
                    // raio de limpeza da praça.
                    if fogged_at(x, z, region_list, &expanded, revealed_ref, &keyed, village) {
                        rgb = fogged_pixel(rgb);
                    }
                    let out = idx * 4;
                    bake.colors[out] = rgb[0];
                    bake.colors[out + 1] = rgb[1];
                    bake.colors[out + 2] = rgb[2];
                    bake.colors[out + 3] = 255;
                }
            }
            bake.next_row = end;
            bake.masks_done = true;
            if bake.next_row >= bake.side {
                // Upload ÚNICO por cozinha: cria o nó da imagem se for a
                // primeira, ou substitui os bytes (re-revelação).
                match bake.image.clone() {
                    // Re-revelação: troca por uma textura NOVA (handle novo
                    // = import fresco no render; mutar `image.data` in-place
                    // ficou no CPU — o widget de UI mantinha a 1.ª textura).
                    Some(old) => {
                        let image = Image::new(
                            Extent3d {
                                width: bake.side as u32,
                                height: bake.side as u32,
                                depth_or_array_layers: 1,
                            },
                            TextureDimension::D2,
                            bake.colors.clone(),
                            TextureFormat::Rgba8UnormSrgb,
                            RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
                        );
                        let handle = images.add(image);
                        if let Ok(node) = image_node.single() {
                            commands.entity(node).insert(ImageNode {
                                image: handle.clone(),
                                ..Default::default()
                            });
                        }
                        images.remove(old.id());
                        bake.image = Some(handle);
                    }
                    None => {
                        let image = Image::new(
                            Extent3d {
                                width: bake.side as u32,
                                height: bake.side as u32,
                                depth_or_array_layers: 1,
                            },
                            TextureDimension::D2,
                            bake.colors.clone(),
                            TextureFormat::Rgba8UnormSrgb,
                            RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
                        );
                        let handle = images.add(image);
                        if let Ok(frame) = frame.single() {
                            commands.entity(frame).with_children(|map| {
                                map.spawn((
                                    Node {
                                        position_type: PositionType::Absolute,
                                        left: Val::Px(0.0),
                                        top: Val::Px(0.0),
                                        width: Val::Percent(100.0),
                                        height: Val::Percent(100.0),
                                        ..Default::default()
                                    },
                                    ImageNode {
                                        image: handle.clone(),
                                        ..Default::default()
                                    },
                                    // Por baixo dos pins (o clip não
                                    // reordena — o with_children apenda).
                                    ZIndex(-1),
                                    Name::new("ui:regional-map:image"),
                                    MapImageNode,
                                ));
                            });
                        }
                        bake.image = Some(handle.clone());
                        // A imagem chegou: o placeholder sai de cena.
                        for mut visibility in placeholder.iter_mut() {
                            if *visibility != Visibility::Hidden {
                                *visibility = Visibility::Hidden;
                            }
                        }
                    }
                }
                bake.phase = BakePhase::Ready;
            }
        }
        BakePhase::Ready => {}
    }
}

// ── pins + rodapé ───────────────────────────────────────────────────────

/// Posiciona os pools de pins e escreve o rodapé. Corre SÓ com o mapa
/// aberto (zero custo nos outros frames) — as posições são derivadas dos
/// mesmos recursos do minimapa/bússola (gating incluído). 16 params: o teto
/// de `SystemParam` em tupla.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn map_pins_system(
    state: Res<RegionalMapState>,
    data: MapPinData,
    players: Query<&GlobalTransform, With<Player>>,
    bake: Option<Res<MapBake>>,
    mut spots: ResMut<MapSpots>,
    mut landmarks: Query<
        (
            &mut UiTransform,
            &mut Visibility,
            &mut BackgroundColor,
            &mut BorderColor,
        ),
        (With<MapPinLandmark>, Without<MapPinTower>),
    >,
    mut towers: Query<
        (&mut UiTransform, &mut Visibility),
        (With<MapPinTower>, Without<MapPinLandmark>),
    >,
    mut quests: Query<
        (&mut UiTransform, &mut Visibility),
        (
            With<MapPinQuest>,
            Without<MapPinLandmark>,
            Without<MapPinTower>,
        ),
    >,
    mut village: Query<
        (&mut UiTransform, &mut Visibility),
        (
            With<MapPinVillage>,
            Without<MapPinLandmark>,
            Without<MapPinTower>,
            Without<MapPinQuest>,
        ),
    >,
    mut player_pin: Query<
        (&mut UiTransform, &mut Visibility),
        (
            With<MapPinPlayer>,
            Without<MapPinVillage>,
            Without<MapPinLandmark>,
            Without<MapPinTower>,
            Without<MapPinQuest>,
        ),
    >,
    mut footer: Query<&mut Text, (With<MapFooter>, Without<MapLabelText>)>,
) {
    if !state.open {
        spots.0.clear();
        return;
    }
    let Some(bake) = bake.as_deref() else {
        return;
    };
    let (half, panel) = (bake.half, MAP_PANEL_PX);
    spots.0.clear();
    let push = |x: f32, z: f32, label: String, spots: &mut MapSpots| {
        let (px, py) = world_to_panel(x, z, half, panel);
        spots.0.push((px, py, label));
    };

    // Vila (praça) — sempre pin, fora do pool.
    if let Some(first) = data.respawn.as_deref().and_then(|r| r.0.first()) {
        let (px, py) = world_to_panel(first.0.x, first.0.y, half, panel);
        if let Ok((mut transform, mut visibility)) = village.single_mut() {
            set_pin(&mut transform, &mut visibility, px, py);
        }
        spots
            .0
            .push((px, py, format!("Praça da vila ({})", first.1)));
    }
    // Herói.
    if let Some(player) = players.iter().next() {
        let p = player.translation();
        let (px, py) = world_to_panel(p.x, p.z, half, panel);
        if let Ok((mut transform, mut visibility)) = player_pin.single_mut() {
            set_pin(&mut transform, &mut visibility, px, py);
        }
        spots.0.push((px, py, "Tu".to_string()));
    }
    // Atalaias + marcos disponíveis (o mesmo snapshot do minimapa).
    if let Some(blips) = data.blips.as_deref() {
        let mut tower_iter = towers.iter_mut();
        let mut landmark_iter = landmarks.iter_mut();
        for blip in &blips.0 {
            let (px, py) = world_to_panel(blip.position.x, blip.position.z, half, panel);
            let signed = data
                .nota
                .as_deref()
                .is_some_and(|n| n.marked.contains(&blip.name));
            if blip.tower {
                let Some((mut transform, mut visibility)) = tower_iter.next() else {
                    continue;
                };
                set_pin(&mut transform, &mut visibility, px, py);
            } else {
                let Some((mut transform, mut visibility, mut bg, mut border)) =
                    landmark_iter.next()
                else {
                    continue;
                };
                // Assinado = ouro cheio; por assinar = contorno teal.
                let wanted_bg = if signed {
                    Color::srgba(0.93, 0.75, 0.3, 1.0)
                } else {
                    Color::srgba(0.12, 0.14, 0.12, 0.4)
                };
                if bg.0 != wanted_bg {
                    bg.0 = wanted_bg;
                }
                let wanted_border = BorderColor::all(if signed {
                    Color::srgb(0.55, 0.4, 0.12)
                } else {
                    Color::srgb(0.35, 0.8, 0.75)
                });
                if *border != wanted_border {
                    *border = wanted_border;
                }
                set_pin(&mut transform, &mut visibility, px, py);
            }
            let state_word = if signed { "assinado" } else { "por assinar" };
            push(
                blip.position.x,
                blip.position.z,
                format!("{} ({state_word})", blip.label),
                &mut spots,
            );
        }
        for (_, mut visibility, _, _) in landmark_iter {
            if *visibility != Visibility::Hidden {
                *visibility = Visibility::Hidden;
            }
        }
        for (_, mut visibility) in tower_iter {
            if *visibility != Visibility::Hidden {
                *visibility = Visibility::Hidden;
            }
        }
    }
    // Faróis de missão (NPC por aceitar/entregar).
    if let Some(guide) = data.guide.as_deref() {
        let mut quest_iter = quests.iter_mut();
        for beacon in guide.beacons.iter().take(QUEST_PIN_POOL) {
            let Some((mut transform, mut visibility)) = quest_iter.next() else {
                break;
            };
            let (px, py) = world_to_panel(beacon.x, beacon.z, half, panel);
            set_pin(&mut transform, &mut visibility, px, py);
            push(
                beacon.x,
                beacon.z,
                "Missão por aqui".to_string(),
                &mut spots,
            );
        }
        for (_, mut visibility) in quest_iter {
            if *visibility != Visibility::Hidden {
                *visibility = Visibility::Hidden;
            }
        }
    }
    // Rodapé: progresso da Nota + estado dos biomas com atalaia.
    let signed_count = data
        .nota
        .as_deref()
        .map(|n| {
            data.catalog
                .as_deref()
                .map(|c| c.0.iter().filter(|l| n.marked.contains(&l.name)).count())
                .unwrap_or(0)
        })
        .unwrap_or(0);
    let total = data.catalog.as_deref().map(|c| c.0.len()).unwrap_or(0);
    let keyed: Vec<String> = data
        .catalog
        .as_deref()
        .map(|c| {
            let mut keys: Vec<String> = RevealedBiomes::keyed_biomes(c)
                .into_iter()
                .map(str::to_string)
                .collect();
            keys.sort();
            keys
        })
        .unwrap_or_default();
    let region_list: &[crate::worldsys::BiomeRegionData] = data
        .regions
        .as_deref()
        .map(|r| r.list.as_slice())
        .unwrap_or(&[]);
    let mut biome_words = Vec::new();
    let mut revealed_count = 0;
    for key in &keyed {
        let is_revealed = data
            .revealed
            .as_deref()
            .is_some_and(|r| r.0.contains(key.as_str()));
        if is_revealed {
            revealed_count += 1;
        }
        // Nome de exposição: o display-name da região, ou o rótulo do bioma
        // canónico (Biome::label) — sem inventar vocabulário novo. ASCII
        // puro: a fonte default do bevy_ui não tem ✓/✗ (renderizam caixa).
        let display = region_list
            .iter()
            .find(|r| reveal_key(&r.id) == *key && !r.display_name.is_empty())
            .map(|r| r.display_name.clone())
            .unwrap_or_else(|| {
                crate::travel::Biome::from_id(key)
                    .map(|b| b.label().to_string())
                    .unwrap_or_else(|| key.clone())
            });
        biome_words.push(format!(
            "{display} {}",
            if is_revealed { "[OK]" } else { "[??]" }
        ));
    }
    if let Ok(mut text) = footer.single_mut() {
        let mut wanted = format!("Marcos assinados {signed_count}/{total}");
        if !keyed.is_empty() {
            wanted.push_str(&format!(
                "  |  Biomas revelados {revealed_count}/{}  |  {}",
                keyed.len(),
                biome_words.join("  ")
            ));
        }
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

/// Posição de um pin (px do painel → translation do nó absoluto).
fn set_pin(transform: &mut UiTransform, visibility: &mut Visibility, px: f32, py: f32) {
    // O pin nasce ancorado a 50%/50% (o centro do painel); a translation é
    // o delta do centro.
    let wanted = UiTransform::from_translation(Val2::new(Val::Px(px), Val::Px(py)));
    if *transform != wanted {
        *transform = wanted;
    }
    if *visibility != Visibility::Inherited {
        *visibility = Visibility::Inherited;
    }
}

/// Hover: cursor sobre o pin → rótulo com o nome. Sem zoom/pan (fora do
/// âmbito); fechar é [M]/Esc/[L] no sistema de teclas.
#[allow(clippy::type_complexity)]
fn map_hover_system(
    state: Res<RegionalMapState>,
    windows: Query<&Window, With<PrimaryWindow>>,
    spots: Res<MapSpots>,
    mut label: Query<(&mut UiTransform, &mut Visibility), (With<MapLabel>, Without<MapLabelText>)>,
    mut text: Query<&mut Text, With<MapLabelText>>,
) {
    if !state.open {
        return;
    }
    let Ok(window) = windows.single() else {
        return;
    };
    let Some(cursor) = window.cursor_position() else {
        return;
    };
    // A moldura está centrada no ecrã: cursor → px relativos ao CENTRO do
    // painel (o mesmo referencial dos pins, [`world_to_panel`]).
    let local = (
        cursor.x - window.width() * 0.5,
        cursor.y - window.height() * 0.5,
    );
    let hit = pick_pin(&spots.0, local.0, local.1);
    for (mut transform, mut visibility) in label.iter_mut() {
        match &hit {
            Some((px, py, _)) => {
                // Rótulo deslocado ao lado do pin, preso ao interior.
                let tx = (px + 12.0).clamp(-(MAP_PANEL_PX * 0.5 - 90.0), MAP_PANEL_PX * 0.5 - 12.0);
                let ty = (py - 16.0).clamp(-(MAP_PANEL_PX * 0.5 - 10.0), MAP_PANEL_PX * 0.5 - 10.0);
                let wanted = UiTransform::from_translation(Val2::new(Val::Px(tx), Val::Px(ty)));
                if *transform != wanted {
                    *transform = wanted;
                }
                if *visibility != Visibility::Inherited {
                    *visibility = Visibility::Inherited;
                }
            }
            None => {
                if *visibility != Visibility::Hidden {
                    *visibility = Visibility::Hidden;
                }
            }
        }
    }
    if let Some((_, _, label_text)) = hit
        && let Ok(mut t) = text.single_mut()
        && t.0 != label_text
    {
        t.0 = label_text;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_reveal_key_normalizes_region_ids() {
        // O mundo autoria `dark-forest`/`frozen-peaks` (hífen); a Nota e as
        // atalaias usam `dark_forest`/`frozen_peaks`. A ponte é o separador.
        assert_eq!(reveal_key("dark-forest"), "dark_forest");
        assert_eq!(reveal_key("frozen-peaks"), "frozen_peaks");
        assert_eq!(reveal_key("desert"), "desert");
        assert_eq!(reveal_key("swamp"), "swamp");
        // Ids já canónicos passam intactos.
        assert_eq!(reveal_key("dark_forest"), "dark_forest");
    }

    #[test]
    fn test_region_hidden_gating() {
        let mut revealed = RevealedBiomes::default();
        let keyed: HashSet<&str> = ["dark_forest", "desert"].into_iter().collect();
        // Com atalaia e sem revelar: névoa.
        assert!(region_hidden(&revealed, &keyed, "dark_forest"));
        // Revelado: visível.
        revealed.0.insert("dark_forest".into());
        assert!(!region_hidden(&revealed, &keyed, "dark_forest"));
        // Bioma SEM atalaia: nunca esconde.
        assert!(!region_hidden(&revealed, &keyed, "swamp"));
        // Mundo sem atalaias: nada esconde (back-compat).
        let empty: HashSet<&str> = HashSet::new();
        assert!(!region_hidden(&revealed, &empty, "desert"));
    }

    #[test]
    fn test_village_clear_radius_always_lit() {
        // Dentro do raio da praça o píxel acende mesmo em bioma oculto.
        assert!(!pixel_hidden(true, 10.0));
        assert!(!pixel_hidden(true, VILLAGE_CLEAR_M));
        // Fora do raio, a névoa mantém-se.
        assert!(pixel_hidden(true, VILLAGE_CLEAR_M + 1.0));
        // Sem região de bioma: nunca esconde.
        assert!(!pixel_hidden(false, 1.0e6));
    }

    #[test]
    fn test_map_land_tint_rises_with_altitude() {
        // Rampa hipsométrica: a luminância nunca DESCE com a altitude nos
        // degraus (papel claro no alto, vale escuro em baixo).
        let luma = |c: [u8; 3]| c[0] as u32 + c[1] as u32 + c[2] as u32;
        let mut prev = 0;
        for t in [0.05, 0.3, 0.5, 0.8, 0.95] {
            let l = luma(map_land_tint(t));
            assert!(l >= prev, "t={t} escureceu ({l} < {prev})");
            prev = l;
        }
    }

    #[test]
    fn test_hillshade_and_fog_clamp() {
        // Sombra limitada (sem picos de 8-bit) e névoa monotónica.
        assert!((-0.32..=0.32).contains(&hillshade(50.0, 40.0, 20.0)));
        assert!((-0.32..=0.32).contains(&hillshade(40.0, 50.0, 20.0)));
        let lit = shade_pixel([100, 100, 100], 0.32);
        assert!(lit[0] <= 133);
        let dark = shade_pixel([100, 100, 100], -0.32);
        assert!(dark[0] >= 68);
        // Névoa: quase-preto, com um resto de textura.
        let fog = fogged_pixel([200, 190, 170]);
        assert!(fog[0] < 45 && fog[1] < 45 && fog[2] < 45);
    }

    #[test]
    fn test_world_to_panel_north_up() {
        let half = 2048.0;
        let panel = 600.0;
        // Centro do mundo → centro do painel (o referencial é o CENTRO:
        // pins ancorados a 50%/50% com translation = delta).
        let (x, y) = world_to_panel(0.0, 0.0, half, panel);
        assert!(x.abs() < 1e-3 && y.abs() < 1e-3);
        // Este (+x) → direita; norte (−z) → cima (y negativo).
        let (ex, _) = world_to_panel(1024.0, 0.0, half, panel);
        assert!(ex > 0.0);
        let (_, ny) = world_to_panel(0.0, -1024.0, half, panel);
        assert!(ny < 0.0);
    }

    #[test]
    fn test_pick_pin_nearest_within_radius() {
        let spots = vec![
            (10.0, 10.0, "a".to_string()),
            (12.0, 10.0, "b".to_string()),
            (500.0, 500.0, "longe".to_string()),
        ];
        let hit = pick_pin(&spots, 11.5, 10.0).expect("pin à apanha");
        assert_eq!(hit.2, "b", "o mais perto ganha");
        assert!(pick_pin(&spots, 200.0, 200.0).is_none());
    }

    #[test]
    fn test_bake_side_constants_sane() {
        // 1024² em fatias de 96 → ≤ 11 frames por passagem; o painel cabe
        // no ecrã de referência 720p com cabeçalho e rodapé.
        assert!(MAP_BAKE_SIDE % BAKE_ROWS_PER_FRAME == 0);
        assert!(BAKE_ROWS_PER_FRAME <= 128);
        assert!(MAP_PANEL_PX + 80.0 < 720.0);
    }

    #[test]
    fn test_biome_labels_cover_keyed_vocabulary() {
        // As chaves que as atalaias do simple-rpg revelam têm rótulo
        // canónico (o rodapé nunca mostra um id cru para biomas conhecidos).
        for key in ["dark_forest", "desert", "swamp", "frozen_peaks"] {
            assert!(
                crate::travel::Biome::from_id(key).is_some(),
                "{key} sem Biome"
            );
        }
    }

    #[test]
    fn test_expanded_polygon_moves_vertices_outward() {
        // Quadrado 100×100 centrado na origem: a expansão afasta cada
        // vértice do centróide (a névoa tapa a aresta do ray-cast).
        let square = vec![[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]];
        let grown = expanded_polygon(&square, 12.0);
        for (orig, grown) in square.iter().zip(&grown) {
            let d0 = (orig[0] - 50.0).hypot(orig[1] - 50.0);
            let d1 = (grown[0] - 50.0).hypot(grown[1] - 50.0);
            assert!(d1 > d0, "vértice não se afastou ({d0} -> {d1})");
        }
    }
}
