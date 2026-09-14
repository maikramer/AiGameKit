//! Eventos de UI para os scripts — o lado push do `viber.ui`.
//!
//! A API Lua continua a ser de POLLING (o modelo de fila/snapshot da engine;
//! callbacks reentrantes a partir dos sistemas seria outro jogo). O que muda:
//! a engine ACUMULA eventos num ring por frame e `viber.ui.events()` devolve
//! o lote — o script deixa de fazer detecção de mudança à mão e o
//! `clicked()` de 1 frame (que já mordeu o profiler.lua) deixa de poder
//! perder cliques.
//!
//! Emissores: cliques (`collect_ui_clicks`), mudanças de valor de widget
//! (sliders/inputs/checks), focus/blur, troca de aba e fim de um
//! `viber.ui.tween`. A fila é LIMPA no fim do frame (o mesmo contrato do
//! `UiClicks`) — quem não ler, perde; a leitura não é idempotente por
//! desígnio (drenar).

use bevy::prelude::*;

use super::runtime::{UiId, UiRegistry};
use super::tween::StyleField;
use super::widgets::{UiCheck, UiFocusedInput, UiInput};

/// Um evento de UI, já com a forma que `viber.ui.events()` devolve a Luau.
#[derive(Debug, Clone)]
pub enum UiEvent {
    /// Um elemento com `Interaction` foi pressionado.
    Click { id: String },
    /// O valor de um slider/bar/cooldown mudou (valor 0..1 normalizado ao
    /// intervalo do widget).
    ValueChanged { id: String, value: f32 },
    /// O texto de um input mudou (por teclado; escritas de script não contam).
    TextChanged { id: String, text: String },
    /// Um `<UiCheck>` ligou ou desligou.
    CheckedChanged { id: String, checked: bool },
    /// Um input recebeu (`true`) ou perdeu (`false`) o teclado.
    FocusChanged { id: String, focused: bool },
    /// A aba activa de um grupo mudou.
    TabChanged { group: String, tab: String },
    /// Um `viber.ui.tween` explícito terminou.
    TweenDone { id: String, property: String },
    /// O ponteiro entrou no elemento.
    HoverEnter { id: String },
    /// O ponteiro saiu do elemento.
    HoverLeave { id: String },
}

impl UiEvent {
    /// O id do elemento, para o filtro por prefixo da API.
    pub fn id(&self) -> &str {
        match self {
            Self::Click { id }
            | Self::ValueChanged { id, .. }
            | Self::TextChanged { id, .. }
            | Self::CheckedChanged { id, .. }
            | Self::FocusChanged { id, .. }
            | Self::TweenDone { id, .. }
            | Self::HoverEnter { id }
            | Self::HoverLeave { id } => id,
            Self::TabChanged { group, .. } => group,
        }
    }

    /// O nome do tipo, como chega a Luau.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Click { .. } => "click",
            Self::ValueChanged { .. } => "value_changed",
            Self::TextChanged { .. } => "text_changed",
            Self::CheckedChanged { .. } => "checked_changed",
            Self::FocusChanged { .. } => "focus_changed",
            Self::TabChanged { .. } => "tab_changed",
            Self::TweenDone { .. } => "tween_done",
            Self::HoverEnter { .. } => "hover_enter",
            Self::HoverLeave { .. } => "hover_leave",
        }
    }
}

/// Fila de eventos do frame — drenada por `viber.ui.events()`, limpa no fim
/// do frame (`clear_ui_events`).
#[derive(Debug, Default, Resource)]
pub struct UiEvents(pub Vec<UiEvent>);

impl UiEvents {
    pub fn push(&mut self, event: UiEvent) {
        // Cap honesto: um HUD não gera milhares de eventos por frame; se
        // gerar, o mais antigo cai (o ring do `viber debug logs` tem a mesma
        // política).
        if self.0.len() >= 512 {
            self.0.remove(0);
        }
        self.0.push(event);
    }

    pub fn push_click(&mut self, id: &str) {
        self.push(UiEvent::Click { id: id.to_string() });
    }

    pub fn push_tween_done(&mut self, entity: Entity, field: StyleField) {
        self.push(UiEvent::TweenDone {
            id: format!("{entity:?}"),
            property: field.name().to_string(),
        });
    }
}

/// Fim do frame: os eventos já foram publicados para os scripts — limpa.
pub fn clear_ui_events(mut events: ResMut<UiEvents>) {
    events.0.clear();
}

/// Emissores de eventos de WIDGET — mudanças de valor (slider), texto (input),
/// estado (check), foco e abas. Compara com o frame anterior via `Local`s: os
/// drivers escrevem componentes todos os frames (`Changed` dispara sempre), o
/// que interessa é o VALOR ter mudado de facto.
///
/// Barras/cooldowns NÃO emitem `value_changed` — os valores deles mudam a cada
/// frame de jogo (vida, cooldown) e o lote de eventos virava spam; quem quer
/// seguir uma barra lê `viber.ui.number()` como sempre.
#[allow(clippy::type_complexity)]
pub fn collect_ui_widget_events(
    mut events: ResMut<UiEvents>,
    registry: Res<UiRegistry>,
    focus: Res<UiFocusedInput>,
    tabs: Res<super::modal::UiTabs>,
    sliders: Query<
        (Entity, &UiId, &super::widgets::UiSlider),
        bevy::ecs::query::Changed<super::widgets::UiSlider>,
    >,
    inputs: Query<
        (Entity, &UiId, &UiInput),
        bevy::ecs::query::Changed<UiInput>,
    >,
    checks: Query<
        (Entity, &UiId, &UiCheck),
        bevy::ecs::query::Changed<UiCheck>,
    >,
    interactions: Query<
        (Entity, &UiId, &Interaction),
        bevy::ecs::query::Changed<Interaction>,
    >,
    mut last_sliders: Local<std::collections::HashMap<Entity, f32>>,
    mut last_inputs: Local<std::collections::HashMap<Entity, String>>,
    mut last_checks: Local<std::collections::HashMap<Entity, bool>>,
    mut last_hover: Local<std::collections::HashMap<Entity, bool>>,
    mut last_focus: Local<Option<Entity>>,
    mut last_tabs: Local<std::collections::HashMap<String, String>>,
) {
    for (entity, id, slider) in &sliders {
        if last_sliders.get(&entity).copied() != Some(slider.value) {
            events.push(UiEvent::ValueChanged {
                id: id.0.clone(),
                value: slider.value,
            });
            last_sliders.insert(entity, slider.value);
        }
    }
    for (entity, id, input) in &inputs {
        if last_inputs.get(&entity).map(String::as_str) != Some(input.text.as_str()) {
            events.push(UiEvent::TextChanged {
                id: id.0.clone(),
                text: input.text.clone(),
            });
            last_inputs.insert(entity, input.text.clone());
        }
    }
    for (entity, id, check) in &checks {
        if last_checks.get(&entity).copied() != Some(check.checked) {
            events.push(UiEvent::CheckedChanged {
                id: id.0.clone(),
                checked: check.checked,
            });
            last_checks.insert(entity, check.checked);
        }
    }
    // Hover: entrada/saída do ponteiro (Pressed conta como hover — o dedo
    // continua por cima durante o clique).
    for (entity, id, interaction) in &interactions {
        let hovered = matches!(interaction, Interaction::Hovered | Interaction::Pressed);
        if last_hover.get(&entity).copied() != Some(hovered) {
            events.push(if hovered {
                UiEvent::HoverEnter { id: id.0.clone() }
            } else {
                UiEvent::HoverLeave { id: id.0.clone() }
            });
            last_hover.insert(entity, hovered);
        }
    }
    // Foco: o teclado mudou de input — blur no antigo, focus no novo.
    if focus.0 != *last_focus {
        let id_of = |entity: Entity| -> Option<String> {
            registry
                .by_id
                .iter()
                .find(|(_, registered)| **registered == entity)
                .map(|(id, _)| id.clone())
        };
        if let Some(previous) = *last_focus
            && let Some(id) = id_of(previous)
        {
            events.push(UiEvent::FocusChanged {
                id,
                focused: false,
            });
        }
        if let Some(current) = focus.0
            && let Some(id) = id_of(current)
        {
            events.push(UiEvent::FocusChanged { id, focused: true });
        }
        *last_focus = focus.0;
    }
    // Abas: diff de group→tab activo.
    if tabs.is_changed() {
        for (group, tab) in &tabs.active {
            if last_tabs.get(group) != Some(tab) {
                events.push(UiEvent::TabChanged {
                    group: group.clone(),
                    tab: tab.clone(),
                });
            }
        }
        *last_tabs = tabs.active.clone();
    }
}
