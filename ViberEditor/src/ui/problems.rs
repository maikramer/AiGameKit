//! The Problems dock (Fase N): runs the engine's `lint::lint_world` on the
//! SAVED world (Ctrl+S or the Revalidate button lints the disk state) and
//! lists the findings — errors in red, warnings in amber, with the stable
//! `kind` chip and the message. The badge on the tab mirrors the error count.

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::tasks::{block_on, poll_once};
use bevy::ui_widgets::Activate;
use viber::lint::{LintReport, Severity};

use super::icons::{self, fa, icon_caption, icon_text};
use super::strings::{Lang, StrKey, t};
use super::tooltip::Tooltip;

/// The dock body (rebuilt per report).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ProblemsBody;

/// The "N erros · M avisos" header line.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ProblemsHeader;

/// The revalidate button.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ProblemsRevalidate;

#[derive(Resource, Debug, Default)]
pub struct ProblemsState {
    report: Option<LintReport>,
    dirty: bool,
    running: bool,
    was_dirty: bool,
    initial_done: bool,
}

impl ProblemsState {
    fn counts(&self) -> (usize, usize) {
        let Some(report) = &self.report else {
            return (0, 0);
        };
        let errors = report
            .findings
            .iter()
            .filter(|f| f.severity == Severity::Error)
            .count();
        let warnings = report.findings.len() - errors;
        (errors, warnings)
    }
}

pub struct ProblemsPlugin;

impl Plugin for ProblemsPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<ProblemsState>()
            .init_resource::<ProblemsTask>()
            .add_observer(revalidate_clicked)
            .add_systems(Update, (poll_problems, render_problems).chain());
    }
}

/// O task em voo.
#[derive(Resource, Debug, Default)]
pub struct ProblemsTask(pub Option<bevy::tasks::Task<Result<LintReport, String>>>);

/// Sistema exclusivo: drena o task, deteta "gravação" (transição dirty→clean)
/// e corre o lint nesses momentos + uma vez no arranque.
fn poll_problems(world: &mut World) {
    // 1. drena o task — devolve ao recurso SÓ se ainda estiver pendente
    //    (recolocar uma task completada é "polled after completion")
    let mut task = world.resource_mut::<ProblemsTask>().0.take();
    if let Some(current) = task.as_mut() {
        match block_on(poll_once(current)) {
            Some(Ok(report)) => {
                let mut state = world.resource_mut::<ProblemsState>();
                state.report = Some(report);
                state.dirty = true;
                state.running = false;
                task = None;
            }
            Some(Err(error)) => {
                warn!("problems: lint falhou: {error}");
                task = None;
            }
            None => {}
        }
    }
    if let Some(current) = task {
        world.resource_mut::<ProblemsTask>().0 = Some(current);
    }
    // 2. transição dirty→clean = gravou → revalida
    let dirty = world.resource::<viber::scene::doc::SceneDoc>().is_dirty();
    if world.resource::<ProblemsState>().was_dirty && !dirty {
        world.resource_mut::<ProblemsState>().was_dirty = dirty;
        run_lint(world);
        return;
    }
    world.resource_mut::<ProblemsState>().was_dirty = dirty;
    // 3. lint inicial (uma vez)
    if !world.resource::<ProblemsState>().initial_done {
        world.resource_mut::<ProblemsState>().initial_done = true;
        run_lint(world);
    }
}

/// Grava (o lint lê o ESTADO DO DISCO) e lança o lint em background task —
/// compila todos os Luau, custa ~1 s.
fn run_lint(world: &mut World) {
    if world.resource::<ProblemsTask>().0.is_some() {
        return; // já há um lint em voo
    }
    crate::scene::save_world(world);
    let path = world
        .resource::<viber::scene::doc::SceneDoc>()
        .world_path()
        .to_path_buf();
    world.resource_mut::<ProblemsState>().running = true;
    let task = bevy::tasks::AsyncComputeTaskPool::get()
        .spawn(async move { viber::lint::lint_world(&path).map_err(|error| format!("{error:#}")) });
    world.resource_mut::<ProblemsTask>().0 = Some(task);
}

/// Rebuilds the list from the report.
fn render_problems(
    mut commands: Commands,
    mut state: ResMut<ProblemsState>,
    lang: Res<Lang>,
    bodies: Query<Entity, With<ProblemsBody>>,
    headers: Query<Entity, With<ProblemsHeader>>,
) {
    if !state.dirty {
        return;
    }
    state.dirty = false;
    let Ok(body) = bodies.single() else {
        return;
    };
    let (errors, warnings) = state.counts();
    if let Ok(header) = headers.single() {
        commands
            .spawn_scene(bsn! {
                @caption({format!(
                    "{} erro(s) · {} aviso(s)",
                    errors, warnings
                )})
            })
            .insert((
                TextFont::from_font_size(11.0),
                TextColor(if errors > 0 {
                    Color::srgb(0.95, 0.55, 0.45)
                } else {
                    Color::srgb(0.62, 0.66, 0.72)
                }),
                Pickable::IGNORE,
                ChildOf(header),
            ));
    }
    commands.entity(body).despawn_related::<Children>();
    let Some(report) = &state.report else {
        return;
    };
    if report.findings.is_empty() {
        commands.spawn((
            Text::new(t(*lang, StrKey::ProblemsClean).to_owned()),
            TextFont::from_font_size(12.0),
            TextColor(Color::srgb(0.55, 0.8, 0.55)),
            Pickable::IGNORE,
            ChildOf(body),
        ));
        return;
    }
    for finding in &report.findings {
        let (color, glyph) = match finding.severity {
            Severity::Error => (Color::srgb(0.95, 0.5, 0.42), fa::CIRCLE_XMARK),
            Severity::Warning => (Color::srgb(0.95, 0.78, 0.42), fa::TRIANGLE_EXCLAMATION),
        };
        commands
            .spawn((
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                    padding: UiRect::vertical(px(1)),
                    ..default()
                },
                ChildOf(body),
            ))
            .with_children(|row| {
                row.spawn((
                    icon_text(glyph, 11.0, color),
                    Node {
                        width: px(14),
                        ..default()
                    },
                    Pickable::IGNORE,
                ));
                row.spawn((
                    Text::new(finding.kind.to_owned()),
                    TextFont::from_font_size(10.0),
                    TextColor(icons::ICON_DIM),
                    Node {
                        width: px(120),
                        ..default()
                    },
                    Pickable::IGNORE,
                ));
                row.spawn((
                    Text::new(finding.message.clone()),
                    TextFont::from_font_size(11.0),
                    TextColor(Color::srgb(0.82, 0.86, 0.9)),
                    Pickable::IGNORE,
                ));
            });
    }
}

fn revalidate_clicked(
    event: On<Activate>,
    buttons: Query<(), With<ProblemsRevalidate>>,
    mut commands: Commands,
) {
    if !buttons.contains(event.event_target()) {
        return;
    }
    commands.queue(run_lint);
}

/// O corpo do tab Problemas: header + revalidar + lista.
pub fn problems_body() -> impl Scene {
    bsn! {
        @super::theme::pane()
        Children [
            Node {
                width: percent(100),
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(10),
                padding: px(6),
            }
            Children [
                ProblemsHeader
                Pickable::IGNORE
                --
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icon_caption(
                            fa::ROTATE_RIGHT,
                            t(Lang::default(), StrKey::ProblemsRevalidate),
                            icons::ICON_DIM
                        )
                    },
                }
                ProblemsRevalidate
                Tooltip(t(Lang::default(), StrKey::ProblemsRevalidateTip))
                on(revalidate_clicked)
            ]
            --
            @super::theme::pane_body()
            Node {
                width: percent(100),
                overflow: Overflow::scroll_y(),
                flex_direction: FlexDirection::Column,
            }
            ProblemsBody
            Children []
        ]
    }
}
