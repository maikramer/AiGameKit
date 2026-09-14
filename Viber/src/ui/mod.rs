//! Declarative UI: XML structure + CSS-like stylesheet + Luau behaviour.
//!
//! The engine's original HUD was a few thousand lines of Rust node builders —
//! every panel, colour and corner radius compiled in. This module replaces
//! that with the three layers a UI actually wants:
//!
//! * **structure** — `<UiRoot>` / `<UiPanel>` / `<UiText>` / `<UiBar>` … in the
//!   world XML ([`tree`]);
//! * **presentation** — a `<UiStyle>` stylesheet with classes, ids, `:hover`
//!   and a real cascade ([`style`]);
//! * **data & behaviour** — named bindings fed from gameplay ([`bind`],
//!   [`collect`]) and a `viber.ui.*` Luau API for anything bindings cannot
//!   express ([`script`]).
//!
//! A world author changes the HUD by editing XML and CSS. Nothing here needs a
//! recompile, and nothing in the HUD needs to know Rust.

pub mod actions;
pub mod anim;
pub mod bind;
pub mod collect;
pub mod events;
pub mod fade;
pub mod list;
pub mod menu_data;
pub mod modal;
pub mod palette;
pub mod runtime;
pub mod scale;
pub mod script;
pub mod style;
pub mod tree;
pub mod tween;
pub mod widgets;

pub use bind::{UiBindWarnings, UiData};
pub use collect::{UiPrompt, UiToast};
pub use events::{UiEvent, UiEvents};
pub use fade::UiFade;
pub use list::{ListRow, UiList, UiLists};
pub use modal::{UiModal, UiModalsOpen, UiScroll, UiTabButton, UiTabPage, UiTabs};
pub use runtime::{
    UiBar, UiBind, UiClasses, UiClicks, UiCooldown, UiDisabled, UiId, UiInlineStyle, UiRegistry,
    UiStyleDirty, UiTag,
};
pub use scale::hud_scale_for_height;
pub use script::{UiCommandQueue, UiScriptState};
pub use style::{StyleProps, StyleSheet, StyleState};
pub use tree::{build_ui_tree, is_ui_tag};
pub use tween::{UiAnimation, UiTransitions};

use bevy::prelude::*;

/// System ordering inside one frame: gather → bind → script → style.
///
/// Explicit sets, because the order is the contract: a script has to see this
/// frame's bindings, and the restyle has to run after whatever the script
/// changed, or every class toggle would land a frame late.
#[derive(SystemSet, Debug, Clone, PartialEq, Eq, Hash)]
pub enum UiSet {
    /// Snapshot gameplay state into [`UiData`] and the list sources.
    Collect,
    /// Rebuild data-driven repeaters whose source changed.
    Build,
    /// Push bindings into elements and publish the script view.
    Bind,
    /// Apply the mutations scripts queued.
    Script,
    /// Recompute styles and mirror widget values into their child nodes.
    Style,
    /// A segunda metade do estilo (syncs de widgets e motion); existe porque o
    /// tuplo de sistemas de uma cadeia tem tecto no Bevy e o estilo ficou
    /// grande de mais para uma cadeia só.
    StyleLate,
}

/// Registers the declarative UI.
#[derive(Default)]
pub struct UiPlugin;

impl bevy::app::Plugin for UiPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<StyleSheet>()
            .init_resource::<UiRegistry>()
            .init_resource::<UiData>()
            .init_resource::<UiBindWarnings>()
            .init_resource::<UiClicks>()
            .init_resource::<UiToast>()
            .init_resource::<UiPrompt>()
            .init_resource::<UiScriptState>()
            .init_resource::<UiLists>()
            .init_resource::<UiModalsOpen>()
            .init_resource::<UiTabs>()
            .init_resource::<widgets::UiFocusedInput>()
            .init_resource::<widgets::UiTooltipLayer>()
            .init_resource::<actions::UiActionOwners>()
            .init_resource::<UiEvents>()
            .init_resource::<script::UiScriptBinds>()
            .add_message::<actions::UiAction>()
            .configure_sets(
                Update,
                (
                    UiSet::Collect,
                    UiSet::Build,
                    UiSet::Bind,
                    UiSet::Script,
                    UiSet::Style,
                    UiSet::StyleLate,
                )
                    .chain(),
            )
            .add_systems(
                Update,
                (
                    collect::retire_legacy_toast_layer,
                    collect::collect_ui_toasts,
                    collect::collect_ui_prompt,
                    collect::collect_ui_data,
                    collect::collect_ui_reveals,
                    menu_data::collect_menu_lists,
                )
                    .chain()
                    .in_set(UiSet::Collect),
            )
            .add_systems(Update, list::rebuild_ui_lists_system.in_set(UiSet::Build))
            // Scripts run first so a `viber.ui.open(…)` lands this frame, then
            // the modal/tab drivers reconcile the world with that state.
            .add_systems(
                Update,
                (
                    modal::drive_ui_modals,
                    modal::seed_ui_tabs,
                    modal::drive_ui_tab_keys,
                    modal::drive_ui_tabs,
                    modal::drive_ui_scroll,
                )
                    .chain()
                    .after(script::apply_ui_commands)
                    .in_set(UiSet::Script),
            )
            .add_systems(
                Update,
                (
                    runtime::collect_ui_clicks,
                    // Emissores de eventos de widget (value/text/check/focus/tab).
                    events::collect_ui_widget_events,
                    bind::apply_ui_bindings,
                    // Class binds (`bind="nome:classe"`) — o toggle de estado
                    // engine-driven que antes vivia em Luau.
                    bind::apply_bind_classes,
                    fade::drive_ui_fades,
                    script::publish_ui_script_view,
                )
                    .chain()
                    .in_set(UiSet::Bind),
            )
            .add_systems(
                Update,
                (
                    script::apply_ui_commands,
                    // Criação de elementos: EXCLUSIVA (o construtor da árvore
                    // lê recursos do World e spawna diretamente).
                    script::apply_ui_creates,
                    script::apply_ui_script_binds,
                    actions::apply_shop_actions,
                    actions::apply_skill_actions,
                    // Ações → fila de eventos dos scripts (`viber.events()`).
                    actions::forward_ui_actions,
                )
                    .chain()
                    .in_set(UiSet::Script),
            )
            .add_systems(
                Update,
                (
                    scale::sync_ui_scale,
                    runtime::mark_resize_dirty,
                    runtime::mark_interaction_dirty,
                    runtime::mark_sheet_dirty,
                    runtime::propagate_style_dirty,
                    runtime::apply_ui_styles,
                    // text-transform escreve no `Text` lendo o `UiComputed`
                    // que o re-estilo acabou de publicar (sync point entre
                    // sistemas em cadeia aplica-o primeiro).
                    runtime::sync_text_transforms,
                    // text-overflow: ellipsis parte do texto já transformado
                    // (corre depois do espelho do transform) e usa o layout do
                    // frame anterior — circuito de retorno, estável quando
                    // nada muda.
                    runtime::sync_text_overflow,
                    // Motion CSS: o sync liga o componente `UiAnimation` ao
                    // `animation` do computed; os drivers escrevem valores
                    // interpolados no `UiComputed` + dirty — o fan-out para os
                    // componentes é do re-estilo do frame seguinte (1 frame de
                    // latência, invisível a 60 fps; o valor DE PARTIDA já é
                    // escrito pelo re-estilo que iniciou o tween).
                    tween::sync_ui_animations,
                    tween::drive_ui_transitions,
                    tween::drive_ui_animations,
                )
                    .chain()
                    .in_set(UiSet::Style),
            )
            .add_systems(
                Update,
                (
                    runtime::sync_ui_bars,
                    runtime::sync_ui_cooldowns,
                    // Tab pages are owned by the selection, not by the
                    // cascade: the re-style resets `display` (apply_fresh),
                    // so their sync runs after it — same reason the check
                    // ticks and slider fills live below.
                    modal::sync_tab_pages,
                    // Interactive widgets run after the style pass: they read
                    // fresh Interaction state and write Node data (slider fill,
                    // input mirror) the cascade must not immediately overwrite.
                    widgets::drive_ui_checks,
                    widgets::sync_check_ticks,
                    widgets::drive_ui_sliders,
                    widgets::sync_ui_input_focus,
                    widgets::sync_ui_input_text,
                    widgets::drive_ui_input,
                    widgets::drive_ui_tooltip,
                    widgets::drive_ui_cursors,
                    // Motion gets the last word on rotate/scale/translate.
                    anim::drive_ui_anims,
                )
                    .chain()
                    .in_set(UiSet::StyleLate),
            );
        // Cliques sintéticos e reais acumulam DURANTE o frame (o luau_update
        // pode correr antes ou depois da UI) e limpa no FIM — ver
        // collect::clear_ui_clicks. O mesmo contrato para a fila de eventos.
        app.add_systems(Last, (runtime::clear_ui_clicks, events::clear_ui_events));
    }
}

/// Installs `viber.ui` on the script host once it exists.
///
/// Runs every frame but returns immediately after the first success. The host
/// is created unconditionally by `LuauScriptPlugin::build`, but plugin build
/// order leaves no single moment where both plugins are up — and a minimal
/// test app may not have the host at all (hence the `Option`). `main.rs` pins
/// this system `.before(luau_on_add)`/`.before(luau_update)`: a lost race
/// meant scripts activated with `viber.ui` still nil, killing the script
/// (`Added` fires once) or burning its warn-once on frame 1.
pub fn install_ui_script_api(
    mut state: ResMut<UiScriptState>,
    host: Option<ResMut<crate::luau::LuaScriptHost>>,
) {
    if state.installed {
        return;
    }
    let Some(host) = host else { return };
    let snapshot = state.clone();
    match script::install_ui_api(&host.lua, &snapshot) {
        Ok(()) => {
            state.installed = true;
            debug!("ui: viber.ui installed on the script host");
        }
        Err(error) => {
            // The host seeds `viber` in its constructor, so this only fires if
            // that contract changes — worth one loud line, not a per-frame spam.
            state.installed = true;
            warn!("ui: could not install viber.ui ({error}) — HUD scripts disabled");
        }
    }
}
