//! The Game dock (controlar o jogo a correr): clock (slider 0–1440), weather
//! (rain/clouds/wind sliders) — pushed live through the bridge
//! (`viber.debug.set_clock/set_weather`) — plus a RUNTIME line for the
//! selected node's live entity (name, hp, AI state via `viber.debug.info`).
//! One combined poll per second fills the readouts; slider drags push
//! commands immediately and are not overwritten by the next poll.

use bevy::feathers::controls::FeathersSlider;
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::tasks::{Task, block_on, poll_once};
use bevy::ui_widgets::ValueChange;
use serde_json::Value;

use crate::bridge::{BrpClientError, EngineStatus, ViberBridge, methods};
use crate::scene::Selection;
use crate::ui::icons::{self, fa, icon_caption};
use crate::ui::strings::{Lang, StrKey, t};
use crate::ui::theme;

/// The dock body.
/// Poll interval for the readouts.
const POLL: f32 = 1.0;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct GameBody;

/// The readout lines (clock/weather summary + runtime info).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct GameLabels;

/// Markers de slider (unit structs — o bsn! não constrói variantes-tupla).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ClockSlider;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct RainSlider;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct CloudsSlider;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct WindSlider;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SunYawSlider;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SunPitchSlider;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SunIlluminanceSlider;

/// O slider que o utilizador está a arrastar.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Dragging {
    Clock,
    Rain,
    Clouds,
    Wind,
}

#[derive(Resource, Debug, Default)]
struct GameState {
    /// Comandos pendentes dos sliders / leitura combinada.
    task: Option<Task<Result<Value, BrpClientError>>>,
    /// Task do runtime info (mais pesada, corre em separado).
    info_task: Option<Task<Result<Value, BrpClientError>>>,
    readout: String,
    runtime: Vec<String>,
    dirty: bool,
    timer: f32,
    /// O slider em arrasto neste instante (o poll não o sobrescreve).
    dragging: Option<Dragging>,
}

pub struct GamePanelPlugin;

impl Plugin for GamePanelPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<GameState>()
            .add_observer(push_slider)
            .add_systems(Update, (poll_game, render_game).chain());
    }
}

/// The dock body, as a scene.
pub fn game_body() -> impl Scene {
    bsn! {
        @theme::pane()
        Children [
            @theme::pane_body()
            Node {
                flex_direction: FlexDirection::Row,
                flex_wrap: FlexWrap::Wrap,
                align_items: AlignItems::FlexStart,
                column_gap: px(8),
                row_gap: px(8),
                overflow: Overflow::scroll_y(),
            }
            GameBody
            Children [
                // AMBIENTE: relógio + tempo
                @game_card()
                Children [
                    @card_title(fa::CLOCK, StrKey::GameClock)
                    --
                    @clock_slider()
                    --
                    @card_title(fa::CLOUD_RAIN, StrKey::GameWeather)
                    --
                    @rain_slider()
                    --
                    @clouds_slider()
                    --
                    @wind_slider()
                ]
                --
                // SOL
                @game_card()
                Children [
                    @card_title(fa::SUN, StrKey::GameSun)
                    --
                    @sun_yaw_slider()
                    --
                    @sun_pitch_slider()
                    --
                    @sun_light_slider()
                ]
                --
                // RUNTIME (entidade selecionada, enquanto joga)
                @game_card()
                Children [
                    @card_title(fa::GEARS, StrKey::GameRuntime)
                    --
                    Node {
                        flex_direction: FlexDirection::Column,
                        row_gap: px(1),
                    }
                    GameLabels
                    Children []
                ]
            ]
        ]
    }
}

/// A card in the Game tab: a titled column of slider rows.
fn game_card() -> impl Scene {
    bsn! {
        Node {
            flex_grow: 1.0,
            flex_basis: px(200),
            min_width: px(200),
            max_width: px(340),
            flex_direction: FlexDirection::Column,
            row_gap: px(5),
            padding: px(8),
            border_radius: px(6),
        }
        BackgroundColor(theme::BG_HEADER)
    }
}

fn card_title(glyph: char, key: StrKey) -> impl Scene {
    bsn! {
        @icon_caption(glyph, t(Lang::default(), key), icons::ICON_DIM)
        Pickable::IGNORE
    }
}

fn clock_slider() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
        }
        Children [
            @FeathersSlider {
                @min: 0.0f32,
                @max: 1440.0f32,
            }
            Node { flex_grow: 1.0, min_width: px(90) }
            ClockSlider
        ]
    }
}

fn rain_slider() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
        }
        Children [
            @caption(t(Lang::default(), StrKey::GameRain)) Node { width: px(76), flex_shrink: 0.0 } Pickable::IGNORE
            --
            @FeathersSlider {
                @min: 0.0f32,
                @max: 1.0f32,
            }
            Node { flex_grow: 1.0, min_width: px(90) }
            RainSlider
        ]
    }
}

fn clouds_slider() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
        }
        Children [
            @caption(t(Lang::default(), StrKey::GameClouds)) Node { width: px(76), flex_shrink: 0.0 } Pickable::IGNORE
            --
            @FeathersSlider {
                @min: 0.0f32,
                @max: 1.0f32,
            }
            Node { flex_grow: 1.0, min_width: px(90) }
            CloudsSlider
        ]
    }
}

fn sun_yaw_slider() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
        }
        Children [
            @caption(t(Lang::default(), StrKey::GameSunYaw)) Node { width: px(76), flex_shrink: 0.0 } Pickable::IGNORE
            --
            @FeathersSlider {
                @min: 0.0f32,
                @max: 360.0f32,
            }
            Node { flex_grow: 1.0, min_width: px(90) }
            SunYawSlider
        ]
    }
}

fn sun_pitch_slider() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
        }
        Children [
            @caption(t(Lang::default(), StrKey::GameSunPitch)) Node { width: px(76), flex_shrink: 0.0 } Pickable::IGNORE
            --
            @FeathersSlider {
                @min: 0.0f32,
                @max: 90.0f32,
            }
            Node { flex_grow: 1.0, min_width: px(90) }
            SunPitchSlider
        ]
    }
}

fn sun_light_slider() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
        }
        Children [
            @caption(t(Lang::default(), StrKey::GameSunLight)) Node { width: px(76), flex_shrink: 0.0 } Pickable::IGNORE
            --
            @FeathersSlider {
                @min: 0.0f32,
                @max: 20000.0f32,
            }
            Node { flex_grow: 1.0, min_width: px(90) }
            SunIlluminanceSlider
        ]
    }
}

fn wind_slider() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
        }
        Children [
            @caption(t(Lang::default(), StrKey::GameWind)) Node { width: px(76), flex_shrink: 0.0 } Pickable::IGNORE
            --
            @FeathersSlider {
                @min: 0.0f32,
                @max: 10.0f32,
            }
            Node { flex_grow: 1.0, min_width: px(90) }
            WindSlider
        ]
    }
}

/// Slider drag → comando imediato para o jogo (e o poll não pisa o valor).
#[allow(clippy::too_many_arguments)]
fn push_slider(
    event: On<ValueChange<f32>>,
    clock: Query<(), With<ClockSlider>>,
    rain: Query<(), With<RainSlider>>,
    clouds: Query<(), With<CloudsSlider>>,
    wind: Query<(), With<WindSlider>>,
    sun_yaw: Query<(), With<SunYawSlider>>,
    sun_pitch: Query<(), With<SunPitchSlider>>,
    sun_light: Query<(), With<SunIlluminanceSlider>>,
    bridge: Res<ViberBridge>,
    mut state: ResMut<GameState>,
) {
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        return;
    }
    let (code, dragging) = if clock.contains(event.source) {
        (
            format!("viber.debug.set_clock({:.0})", event.value),
            Dragging::Clock,
        )
    } else if rain.contains(event.source) {
        (
            format!("viber.debug.set_weather{{rain = {:#}}}", event.value),
            Dragging::Rain,
        )
    } else if clouds.contains(event.source) {
        (
            format!("viber.debug.set_weather{{clouds = {:#}}}", event.value),
            Dragging::Clouds,
        )
    } else if wind.contains(event.source) {
        (
            format!("viber.debug.set_weather{{wind = {:#}}}", event.value),
            Dragging::Wind,
        )
    } else if sun_yaw.contains(event.source) {
        (
            format!("viber.debug.sun{{yaw = {:#}}}", event.value),
            Dragging::Wind,
        )
    } else if sun_pitch.contains(event.source) {
        (
            format!("viber.debug.sun{{pitch = {:#}}}", event.value),
            Dragging::Wind,
        )
    } else if sun_light.contains(event.source) {
        (
            format!(
                "viber.debug.sun{{illuminance = {:#}}}",
                event.value.max(100.0)
            ),
            Dragging::Wind,
        )
    } else {
        return;
    };
    state.dragging = Some(dragging);
    state.timer = POLL; // dá um segundo de respiro ao arrasto
    if state.task.is_none() {
        state.task = Some(methods::lua_eval(&bridge.client, &code));
    }
}

/// Exclusive poll: drains the command task, then at 1 Hz reads clock+weather
/// in ONE call, and the selected entity's live info in another.
fn poll_game(world: &mut World) {
    if !matches!(
        world.resource::<ViberBridge>().status,
        EngineStatus::Connected { .. }
    ) {
        return;
    }
    // 1. drena o task de comandos/leitura combinada
    let mut task = world.resource_mut::<GameState>().task.take();
    if let Some(current) = task.as_mut() {
        match block_on(poll_once(current)) {
            Some(Ok(value)) => {
                let minute = value.get("minute").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let rain = value.get("rain").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let clouds = value.get("clouds").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let wind = value.get("wind").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let (h, m) = (
                    (minute / 60.0).floor() as u32,
                    minute.rem_euclid(60.0) as u32,
                );
                world.resource_mut::<GameState>().readout =
                    format!("{h:02}:{m:02} · chuva {rain:#} · nuvens {clouds:#} · vento {wind:#}");
                world.resource_mut::<GameState>().dirty = true;
                task = None;
            }
            Some(Err(error)) => {
                warn!("game panel: {error}");
                task = None;
            }
            None => {}
        }
    }
    if let Some(current) = task {
        world.resource_mut::<GameState>().task = Some(current);
    }
    // 2. drena o task do runtime info
    let mut info = world.resource_mut::<GameState>().info_task.take();
    if let Some(current) = info.as_mut() {
        match block_on(poll_once(current)) {
            Some(Ok(value)) => {
                world.resource_mut::<GameState>().runtime = runtime_lines(&value);
                world.resource_mut::<GameState>().dirty = true;
                info = None;
            }
            Some(Err(error)) => {
                warn!("game panel: info: {error}");
                info = None;
            }
            None => {}
        }
    }
    if let Some(current) = info {
        world.resource_mut::<GameState>().info_task = Some(current);
    }
    // 3. a cada POLL: relê clock/weather e pede o info da seleção
    let delta = world.resource::<Time>().delta_secs();
    world.resource_mut::<GameState>().timer -= delta;
    if world.resource::<GameState>().timer > 0.0 {
        return;
    }
    world.resource_mut::<GameState>().timer = POLL;
    let bridge = world.resource::<ViberBridge>().client;
    if world.resource::<GameState>().task.is_none() {
        world.resource_mut::<GameState>().task = Some(methods::lua_eval(
            &bridge,
            "local c = viber.debug.clock() local w = viber.debug.weather_full() return {minute = c.minute, rain = w.rain, clouds = w.clouds, wind = w.wind_strength}",
        ));
    }
    let bits = {
        let selection = world.resource::<Selection>().clone();
        let doc = world.resource::<viber::scene::doc::SceneDoc>().clone();
        selection.primary().and_then(|node| {
            let origin = doc.origin_of(node);
            let mut query = world.query_filtered::<(Entity, &viber::scene::SceneSource), ()>();
            query
                .iter(world)
                .find(|(_, source)| *source.0 == origin)
                .map(|(entity, _)| entity.to_bits())
        })
    };
    if let Some(bits) = bits
        && world.resource::<GameState>().info_task.is_none()
    {
        world.resource_mut::<GameState>().info_task = Some(methods::lua_eval(
            &bridge,
            &format!("return viber.debug.info({bits})"),
        ));
    }
}

/// The runtime lines from an EntityInfo value.
fn runtime_lines(value: &Value) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(name) = value.get("name").and_then(|v| v.as_str()) {
        out.push(format!("• {name}"));
    }
    if let (Some(hp), Some(max)) = (
        value.get("hp").and_then(|v| v.as_f64()),
        value.get("max_hp").and_then(|v| v.as_f64()),
    ) {
        out.push(format!("• vida {hp:.0}/{max:.0}"));
    }
    if let Some(components) = value.get("components").and_then(|v| v.as_array()) {
        out.push(format!("• {} componente(s)", components.len()));
    }
    if let Some(ai) = value.get("ai")
        && !ai.is_null()
        && let Some(ai_state) = ai.get("state").and_then(|v| v.as_str())
    {
        out.push(format!("• IA: {ai_state}"));
    }
    if let Some(script) = value.get("script").and_then(|v| v.as_str()) {
        out.push(format!("• script: {script}"));
    }
    out
}

/// Reflects the polled readout into the labels.
fn render_game(
    mut commands: Commands,
    mut state: ResMut<GameState>,
    bodies: Query<Entity, With<GameLabels>>,
) {
    if !state.dirty {
        return;
    }
    state.dirty = false;
    let Ok(body) = bodies.single() else {
        return;
    };
    commands.entity(body).despawn_related::<Children>();
    let muted = Color::srgb(0.62, 0.66, 0.72);
    commands.spawn((
        Text::new(state.readout.clone()),
        TextFont::from_font_size(12.0),
        TextColor(Color::srgb(0.82, 0.86, 0.9)),
        Pickable::IGNORE,
        ChildOf(body),
    ));
    for line in &state.runtime {
        commands.spawn((
            Text::new(line.clone()),
            TextFont::from_font_size(11.0),
            TextColor(muted),
            Pickable::IGNORE,
            ChildOf(body),
        ));
    }
}
