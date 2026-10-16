//! Startup error screen (Fase O1) + New World wizard (Fase O2): when the
//! world fails to load (config.yaml missing/invalid, procedural world
//! needing bake), show a minimal error App instead of exiting to stderr —
//! with "create sample config", "new world…" (scaffold + dirs + spawn) and
//! "exit". Everything is built once at Startup from the resources. A
//! `--screenshot`/`--frames` pair captures the screen for QA and exits.

use crate::ui::icons::{fa, icon_caption};
use crate::ui::strings::{Lang, pick};
use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::feathers::dark_theme::create_dark_theme;
use bevy::feathers::theme::UiTheme;
use bevy::prelude::*;
use bevy::render::view::screenshot::{Screenshot, save_to_disk};
use bevy::ui_widgets::Activate;
use std::path::PathBuf;
use viber::scaffold;

/// Por que o mundo não abriu.
#[derive(Resource, Debug, Clone)]
pub struct StartupError {
    pub message: String,
    pub hint: StartupHint,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StartupHint {
    /// Falta/está inválido o config.yaml → oferece criar o exemplo.
    #[default]
    CreateConfig,
    /// Mundo procedural → aponta o `viber bake`.
    NeedsBake,
}

/// O caminho do mundo que falhou.
#[derive(Resource, Debug, Clone)]
pub struct FailedWorld(pub PathBuf);

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct CreateConfigButton;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ExitButton;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct WizardCreateButton;

/// Nome da pasta para o wizard (texto do input).
#[derive(Component, Debug, Default)]
pub struct WizardNameInput;

/// QA: captura a janela no meio dos frames e sai no fim.
#[derive(Resource, Debug)]
pub struct ErrorQa {
    path: PathBuf,
    frames_left: u32,
    taken: bool,
}

pub struct ErrorAppPlugin;

impl Plugin for ErrorAppPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(Update, (error_screenshot_and_exit, exit_on_escape));
    }
}

/// App mínima de erro: UI + a mensagem + as ações. Sem mundo.
pub fn error_app(
    message: String,
    hint: StartupHint,
    world: PathBuf,
    screenshot: Option<PathBuf>,
    frames: u32,
    lang: Lang,
) -> App {
    let mut app = App::new();
    let capture = screenshot.is_some();
    // DefaultPlugins completos: os plugins do Feathers precisam de
    // AssetPlugin/Shader/et al., e o binário já os traz — o erro é transitório.
    app.add_plugins((DefaultPlugins, bevy::feathers::FeathersPlugins))
        .insert_resource(UiTheme(create_dark_theme()))
        .insert_resource(StartupError { message, hint })
        .insert_resource(lang)
        .insert_resource(FailedWorld(world))
        .insert_resource(ErrorQa {
            path: screenshot.unwrap_or_default(),
            // pedir a captura nos primeiros ~30 frames (superfície da janela
            // ainda a configurar) fazia o processo rebentar (segfault) ao gravar
            frames_left: frames.max(60),
            taken: !capture, // sem pedido = nunca captura
        })
        .add_plugins((ErrorAppPlugin, crate::ui::theme::ThemePlugin))
        .add_systems(Startup, error_ui);
    // Os botões usam ícones Font Awesome: a fonte embebida também aqui (como no app.rs).
    bevy::asset::embedded_asset!(app, "assets/fonts/fa-solid-900.ttf");
    app
}

/// Constrói TODA a tela no Startup (lê os resources diretamente).
fn error_ui(mut commands: Commands, error: Res<StartupError>, lang: Res<Lang>) {
    let hint_text = match error.hint {
        StartupHint::CreateConfig => pick(
            *lang,
            "Este mundo precisa de um config.yaml válido ao lado do world.xml (contrato de paths de assets/scripts/save).",
            "This world needs a valid config.yaml next to world.xml (the assets/scripts/save path contract).",
        ),
        StartupHint::NeedsBake => pick(
            *lang,
            "Este mundo é procedural — converte-o com `viber bake <world.xml>` para o editar aqui.",
            "This world is procedural — convert it with `viber bake <world.xml>` to edit it here.",
        ),
    };
    // Sem câmara a UI não tem onde desenhar: a janela ficava preta.
    commands.spawn(Camera2d);
    let root = commands
        .spawn((
            Node {
                width: percent(100),
                height: percent(100),
                align_items: AlignItems::Center,
                justify_content: JustifyContent::Center,
                flex_direction: FlexDirection::Column,
                row_gap: px(14),
                padding: UiRect::all(px(48)),
                ..default()
            },
            BackgroundColor(Color::srgb(0.09, 0.1, 0.12)),
            // a raiz do editor: o ThemePlugin troca a fonte default (sem acentos)
            crate::ui::EditorRoot,
        ))
        .id();
    let title = commands
        .spawn((
            Node {
                flex_direction: FlexDirection::Row,
                column_gap: px(10),
                align_items: AlignItems::Center,
                margin: UiRect::bottom(px(8)),
                ..default()
            },
            ChildOf(root),
        ))
        .id();
    commands.spawn((
        // "!" e não "⚠": esta app mínima não traz a fonte de ícones, e a fonte
        // default não tem o U+26A0 (saía o glifo em falta).
        Text::new("!".to_owned()),
        TextFont::from_font_size(26.0),
        TextColor(Color::srgb(0.95, 0.78, 0.42)),
        ChildOf(title),
    ));
    commands.spawn((
        Text::new(pick(*lang, "O mundo não abriu", "The world did not open").to_owned()),
        TextFont::from_font_size(20.0),
        TextColor(Color::WHITE),
        ChildOf(title),
    ));
    commands.spawn((
        Text::new(error.message.clone()),
        TextFont::from_font_size(13.0),
        TextColor(Color::srgb(0.95, 0.6, 0.55)),
        Node {
            width: percent(80),
            ..default()
        },
        ChildOf(root),
    ));
    commands.spawn((
        Text::new(hint_text.to_owned()),
        TextFont::from_font_size(13.0),
        TextColor(Color::srgb(0.7, 0.74, 0.8)),
        Node {
            width: percent(80),
            ..default()
        },
        ChildOf(root),
    ));

    // ações
    let actions = commands
        .spawn((
            Node {
                flex_direction: FlexDirection::Row,
                column_gap: px(10),
                margin: UiRect::top(px(10)),
                ..default()
            },
            ChildOf(root),
        ))
        .id();
    if error.hint == StartupHint::CreateConfig {
        commands
            .spawn_scene(bsn! {
                @FeathersToolButton {
                    @variant: ButtonVariant::Primary,
                    @caption: bsn! {
                        @icon_caption(
                            fa::WAND_MAGIC_SPARKLES,
                            pick(*lang, "Criar config de exemplo", "Create sample config"),
                            Color::WHITE
                        )
                    },
                }
                CreateConfigButton
            })
            .insert(ChildOf(actions))
            .observe(create_config_clicked);
    }
    commands
        .spawn_scene(bsn! {
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    @icon_caption(fa::PLUS, pick(*lang, "Novo mundo…", "New world…"), Color::WHITE)
                },
            }
            WizardCreateButton
        })
        .insert(ChildOf(actions))
        .observe(wizard_create_clicked);
    commands
        .spawn_scene(bsn! {
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    @icon_caption(fa::CIRCLE_XMARK, pick(*lang, "Sair", "Exit"), Color::WHITE)
                },
            }
            ExitButton
        })
        .insert(ChildOf(actions))
        .observe(exit_clicked);

    // input do nome do wizard
    let wizard = commands
        .spawn((
            Node {
                flex_direction: FlexDirection::Row,
                column_gap: px(8),
                align_items: AlignItems::Center,
                ..default()
            },
            ChildOf(root),
        ))
        .id();
    commands.spawn((
        Text::new(pick(*lang, "novo mundo em ./", "new world in ./").to_owned()),
        TextFont::from_font_size(12.0),
        TextColor(Color::srgb(0.62, 0.66, 0.72)),
        ChildOf(wizard),
    ));
    // Campo a sério (cenas feathers, não componentes soltos): o nome lê-se do
    // `EditableText`; vazio = "meu-jogo".
    let container = commands
        .spawn_scene(bsn! {
            @bevy::feathers::controls::FeathersTextInputContainer
            Node { width: px(220) }
        })
        .insert(ChildOf(wizard))
        .id();
    commands
        .spawn_scene(bsn! { @bevy::feathers::controls::FeathersTextInput })
        .insert((WizardNameInput, ChildOf(container)));
}

fn create_config_clicked(
    event: On<Activate>,
    buttons: Query<(), With<CreateConfigButton>>,
    failed: Res<FailedWorld>,
    mut exit: MessageWriter<bevy::app::AppExit>,
) {
    if !buttons.contains(event.event_target()) {
        return;
    }
    let dir = failed.0.parent().map(PathBuf::from).unwrap_or_default();
    let config = dir.join("config.yaml");
    if let Err(error) = std::fs::write(&config, scaffold::CONFIG_YAML_TEMPLATE) {
        error!("criar config: {error}");
        return;
    }
    info!("config.yaml criado em {} — reabrindo…", config.display());
    if let Some(exe) = std::env::current_exe().ok()
        && std::process::Command::new(exe)
            .arg(&failed.0)
            .spawn()
            .is_ok()
    {
        exit.write(bevy::app::AppExit::Success);
    }
}

fn exit_clicked(
    event: On<Activate>,
    buttons: Query<(), With<ExitButton>>,
    mut exit: MessageWriter<bevy::app::AppExit>,
) {
    if buttons.contains(event.event_target()) {
        exit.write(bevy::app::AppExit::Success);
    }
}

/// "Novo mundo…" — cria o scaffold + dirs ao lado do mundo que falhou e abre.
fn wizard_create_clicked(
    event: On<Activate>,
    buttons: Query<(), With<WizardCreateButton>>,
    inputs: Query<&bevy::text::EditableText, With<WizardNameInput>>,
    failed: Res<FailedWorld>,
    lang: Res<Lang>,
    mut exit: MessageWriter<bevy::app::AppExit>,
) {
    if !buttons.contains(event.event_target()) {
        return;
    }
    let name = inputs
        .iter()
        .map(|text| text.value().to_string().trim().to_owned())
        .find(|name| !name.is_empty())
        .unwrap_or_else(|| pick(*lang, "meu-jogo", "my-game").to_owned());
    let base = failed.0.parent().map(PathBuf::from).unwrap_or_default();
    let target = base.join(&name);
    match scaffold::create_world_project(&target) {
        Ok(world_xml) => {
            for dir in [
                "assets/audio/bgm",
                "assets/audio/sfx",
                "assets/textures",
                "scripts",
                "saves",
            ] {
                let _ = std::fs::create_dir_all(target.join(dir));
            }
            info!("novo mundo: {}", world_xml.display());
            if let Some(exe) = std::env::current_exe().ok()
                && std::process::Command::new(exe)
                    .arg(&world_xml)
                    .spawn()
                    .is_ok()
            {
                exit.write(bevy::app::AppExit::Success);
            }
        }
        Err(error) => error!("novo mundo: {error:#}"),
    }
}

/// QA: captura a janela no meio dos frames e sai no fim.
fn error_screenshot_and_exit(
    qa: Option<Res<ErrorQa>>,
    mut counters: Local<u32>,
    mut commands: Commands,
    mut exit: MessageWriter<bevy::app::AppExit>,
) {
    let Some(qa) = qa else {
        return;
    };
    if qa.taken {
        return;
    }
    *counters += 1;
    if *counters == qa.frames_left {
        info!("screenshot → {}", qa.path.display());
        commands
            .spawn(Screenshot::primary_window())
            .observe(save_to_disk(qa.path.clone()));
        return;
    }
    // Uns frames para a captura ser lida da GPU e gravada (como o app.rs).
    if *counters >= qa.frames_left + 30 {
        exit.write(bevy::app::AppExit::Success);
    }
}

fn exit_on_escape(keys: Res<ButtonInput<KeyCode>>, mut exit: MessageWriter<bevy::app::AppExit>) {
    if keys.just_pressed(KeyCode::Escape) {
        exit.write(bevy::app::AppExit::Success);
    }
}
