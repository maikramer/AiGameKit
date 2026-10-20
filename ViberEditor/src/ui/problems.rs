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

use super::icons::{self, fa, icon_text};
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
    was_dirty: bool,
    initial_done: bool,
    /// Um save chegou com um lint já em voo. O pedido seguinte era deitado
    /// fora e o painel ficava com o relatório de antes da gravação.
    lint_again: bool,
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
            .add_systems(
                Update,
                (poll_problems, render_problems, update_badge).chain(),
            );
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
    let mut again = false;
    if let Some(current) = task.as_mut() {
        match block_on(poll_once(current)) {
            Some(Ok(report)) => {
                let mut state = world.resource_mut::<ProblemsState>();
                state.report = Some(report);
                state.dirty = true;
                again = state.lint_again;
                state.lint_again = false;
                task = None;
            }
            Some(Err(error)) => {
                warn!("problems: lint falhou: {error}");
                let mut state = world.resource_mut::<ProblemsState>();
                again = state.lint_again;
                state.lint_again = false;
                task = None;
            }
            None => {}
        }
    }
    if let Some(current) = task {
        world.resource_mut::<ProblemsTask>().0 = Some(current);
    }
    if again {
        run_lint(world);
    }
    // 2. transição dirty→clean = gravou → revalida (escreve SÓ na transição:
    //    um `resource_mut` incondicional marcava o estado como changed todos
    //    os frames e o badge nunca entrava em curto-circuito)
    let dirty = world.resource::<viber::scene::doc::SceneDoc>().is_dirty();
    let was_dirty = world.resource::<ProblemsState>().was_dirty;
    if was_dirty && !dirty {
        world.resource_mut::<ProblemsState>().was_dirty = false;
        if world.resource::<ProblemsTask>().0.is_some() {
            world.resource_mut::<ProblemsState>().lint_again = true;
        } else {
            run_lint(world);
        }
        return;
    }
    if was_dirty != dirty {
        world.resource_mut::<ProblemsState>().was_dirty = dirty;
    }
    // 3. lint inicial (uma vez)
    if !world.resource::<ProblemsState>().initial_done {
        world.resource_mut::<ProblemsState>().initial_done = true;
        run_lint(world);
    }
}

/// Lança o lint (sobre o ESTADO DO DISCO) em background task —
/// compila todos os Luau, custa ~1 s.
fn run_lint(world: &mut World) {
    if world.resource::<ProblemsTask>().0.is_some() {
        return; // já há um lint em voo
    }
    // O lint lê o DISCO e não grava nada: gravar aqui escrevia as edições do
    // utilizador sem ele pedir (e no arranque deixava "guardar: nada para
    // escrever" no estado). Edições por gravar entram no próximo Ctrl+S.
    let path = world
        .resource::<viber::scene::doc::SceneDoc>()
        .world_path()
        .to_path_buf();
    let task = bevy::tasks::AsyncComputeTaskPool::get()
        .spawn(async move { viber::lint::lint_world(&path).map_err(|error| format!("{error:#}")) });
    world.resource_mut::<ProblemsTask>().0 = Some(task);
}

/// The Problems tab badge: errors (red) or, without errors, warnings (amber).
fn update_badge(
    state: Res<ProblemsState>,
    mut badges: Query<(
        &super::layout::TabBadge,
        &mut Node,
        &mut BackgroundColor,
        &Children,
    )>,
    mut texts: Query<&mut Text, With<super::layout::TabBadgeText>>,
) {
    if !state.is_changed() {
        return;
    }
    let (errors, warnings) = state.counts();
    for (badge, mut node, mut color, children) in &mut badges {
        if badge.0 != super::layout::TabKind::Problems {
            continue;
        }
        let (count, tint) = if errors > 0 {
            (errors, Color::srgb(0.85, 0.3, 0.28))
        } else {
            (warnings, Color::srgb(0.82, 0.6, 0.2))
        };
        node.display = if count > 0 {
            Display::Flex
        } else {
            Display::None
        };
        color.0 = tint;
        for child in children.iter() {
            if let Ok(mut text) = texts.get_mut(child) {
                let label = if count > 99 {
                    "99+".to_owned()
                } else {
                    count.to_string()
                };
                if text.0 != label {
                    text.0 = label;
                }
            }
        }
    }
}

/// Rebuilds the list from the report.
fn render_problems(
    mut commands: Commands,
    mut state: ResMut<ProblemsState>,
    lang: Res<Lang>,
    bodies: Query<Entity, With<ProblemsBody>>,
    headers: Query<Entity, With<ProblemsHeader>>,
) {
    if !state.dirty && !lang.is_changed() {
        return;
    }
    state.dirty = false;
    let Ok(body) = bodies.single() else {
        return;
    };
    let (errors, warnings) = state.counts();
    if let Ok(header) = headers.single() {
        commands.entity(header).despawn_related::<Children>();
        let counts = match *lang {
            Lang::Pt => format!("{errors} erro(s) · {warnings} aviso(s)"),
            Lang::En => format!("{errors} error(s) · {warnings} warning(s)"),
        };
        commands
            .spawn_scene(bsn! {
                @caption(counts)
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
        // findings de script são CLICÁVEIS: abrem o .lua no modo Script
        let clickable =
            finding.kind.starts_with("script-") && extract_script_path(&finding.message).is_some();
        let mut row = commands.spawn((
            FindingRow {
                kind: finding.kind,
                message: finding.message.clone(),
            },
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
                padding: UiRect::vertical(px(1)),
                ..default()
            },
            ChildOf(body),
        ));
        if clickable {
            row.observe(finding_clicked)
                .insert(bevy::picking::cursor::EntityCursor::System(
                    bevy::window::SystemCursorIcon::Pointer,
                ));
        }
        row.with_children(|row| {
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
                TextColor(if clickable {
                    Color::srgb(0.62, 0.78, 0.95)
                } else {
                    Color::srgb(0.82, 0.86, 0.9)
                }),
                Pickable::IGNORE,
            ));
        });
    }
}

/// Uma linha de achado (kind + mensagem) — as de script abrem o `.lua`.
#[derive(Component, Debug, Clone)]
pub struct FindingRow {
    kind: &'static str,
    message: String,
}

/// Extrai o primeiro caminho `.lua` da mensagem (ex. `scripts/quests.lua:12`).
fn extract_script_path(message: &str) -> Option<String> {
    let start = message.split_whitespace().find_map(|token| {
        token
            .split(':')
            .next()
            .filter(|candidate| candidate.to_ascii_lowercase().ends_with(".lua"))
    })?;
    Some(start.trim_start_matches("./").to_owned())
}

fn finding_clicked(
    click: On<bevy::picking::events::PointerClick>,
    rows: Query<&FindingRow>,
    mut mode: ResMut<crate::ui::mode_strip::EditorModeState>,
    mut scripts: ResMut<crate::ui::mode_strip::ScriptState>,
) {
    let Ok(row) = rows.get(click.event_target()) else {
        return;
    };
    if !row.kind.starts_with("script-") {
        return;
    }
    let Some(path) = extract_script_path(&row.message) else {
        return;
    };
    scripts.selected = Some(path);
    mode.0 = crate::ui::mode_strip::EditorMode::Script;
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
                // a contagem (reescrita a cada relatório): um Node, para os filhos
                // aparecerem (pendurados num não-Node nunca eram desenhados)
                Node { align_items: AlignItems::Center }
                ProblemsHeader
                Pickable::IGNORE
                --
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icons::icon_caption_key(
                            fa::ROTATE_RIGHT,
                            StrKey::ProblemsRevalidate,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_script_paths_from_messages() {
        assert_eq!(
            extract_script_path("script-syntax: scripts/quests.lua:12: unexpected 'end'"),
            Some("scripts/quests.lua".to_owned())
        );
        assert_eq!(
            extract_script_path("script-ref: npc 'vael' refers to missing ./enemy.lua"),
            Some("enemy.lua".to_owned())
        );
        assert_eq!(
            extract_script_path("duplicate-name: two nodes 'chapel'"),
            None
        );
    }
}
