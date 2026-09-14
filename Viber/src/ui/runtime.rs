//! Runtime for the declarative UI: the components an element carries, the
//! registry that maps author ids to entities, and the systems that keep the
//! rendered node in sync with the stylesheet.
//!
//! Styling is recomputed on demand, not every frame: an element is restyled
//! when it is spawned, when its class list or inline style changes, and when
//! its interaction state flips. A HUD of a few hundred nodes therefore costs
//! nothing while nobody is touching it.

use std::collections::HashMap;

use bevy::ecs::system::SystemParam;
use bevy::prelude::*;
use bevy::text::{
    FontWeight, LetterSpacing, LineBreak, Strikethrough, StrikethroughColor, Underline,
    UnderlineColor,
};
use bevy::ui::widget::ImageNode;
use bevy::ui::{BoxShadow, FocusPolicy, Val2};
use bevy::window::PrimaryWindow;

use super::style::{
    Decoration, GradientSpec, GradientStop, LetterSpacingSpec, StyleProps, StyleSheet, StyleState,
    fade,
};

/// Author-facing id (`id="hp-fill"`), unique per world.
#[derive(Debug, Clone, Component)]
pub struct UiId(pub String);

/// The element's XML tag, lowercased — what tag selectors match against.
#[derive(Debug, Clone, Component)]
pub struct UiTag(pub String);

/// Class list (`class="chip gold"`), lowercased.
#[derive(Debug, Clone, Default, Component)]
pub struct UiClasses(pub Vec<String>);

impl UiClasses {
    pub fn parse(text: &str) -> Self {
        Self(
            text.split_whitespace()
                .map(|c| c.to_ascii_lowercase())
                .collect(),
        )
    }

    pub fn has(&self, class: &str) -> bool {
        self.0.iter().any(|c| c == class)
    }

    /// Adds a class; `true` when it was not already present.
    pub fn add(&mut self, class: &str) -> bool {
        let class = class.to_ascii_lowercase();
        if self.has(&class) {
            return false;
        }
        self.0.push(class);
        true
    }

    /// Removes a class; `true` when it was present.
    pub fn remove(&mut self, class: &str) -> bool {
        let class = class.to_ascii_lowercase();
        let before = self.0.len();
        self.0.retain(|c| *c != class);
        before != self.0.len()
    }

    /// Adds or removes a class; `true` when the list changed. The one call
    /// every widget sync wants (`set_class("dragging", dragging)`).
    pub fn set_class(&mut self, class: &str, on: bool) -> bool {
        if on {
            self.add(class)
        } else {
            self.remove(class)
        }
    }
}

/// Declarations from the element's own `style="…"` attribute, or pushed at
/// runtime by a script. Highest precedence in the cascade.
#[derive(Debug, Clone, Default, Component)]
pub struct UiInlineStyle(pub StyleProps);

/// Position of an element among its siblings, as authored.
///
/// Entity ids do **not** follow document order — Bevy's allocator recycles
/// them, so a world that spawns and despawns while building hands them out in
/// an arbitrary sequence. Anything that needs "the order the author wrote
/// these in" (tab bars, keyboard navigation) reads this instead.
#[derive(Debug, Clone, Copy, Component)]
pub struct UiOrder(pub usize);

/// Binds an element to a named engine value (`bind="health"`), fed each frame
/// by [`crate::ui::bind`]. Bars take the 0..1 fraction, texts the formatted
/// string, and any element can bind to a boolean to drive its visibility.
#[derive(Debug, Clone, Component)]
pub struct UiBind(pub String);

/// Marks an element whose style has to be recomputed on the next pass.
#[derive(Debug, Component)]
pub struct UiStyleDirty;

/// O estilo FINAL de um elemento — cascata + inline + viewport + herança já
/// fechados, mais a opacidade EFETIVA (a própria × ancestrais CSS × fades).
/// É o "computed style" da engine: os descendentes é que o leem para herdar
/// (via [`StyleProps::apply_inherited`]), na passada de aplicação — e o motor
/// de motion (`ui::tween`) escreve aqui os valores interpolados, deixando o
/// fan-out para os componentes por conta do re-estilo.
#[derive(Debug, Clone, Component)]
pub struct UiComputed(pub super::style::StyleProps, pub f32);

/// Folhas de estilo que alimentam ESTA raiz (índices [`StyleSheet::parse_into`]).
///
/// Shadow-DOM-lite: uma regra só combina com elementos de raízes que carregam
/// a folha que a produziu — o `.empty` do menu nunca volta a estilizar um slot
/// do HUD. Raízes sem este componente veem TUDO (compatibilidade).
#[derive(Debug, Clone, Component, Default)]
pub struct UiRootSheets(pub Vec<usize>);

/// Fonte computada em px, se houver (`Measure::Plain(Val::Px)` pós-resolução).
fn font_px(val: Option<&super::style::Measure>) -> Option<f32> {
    match val {
        Some(super::style::Measure::Plain(Val::Px(px))) => Some(*px),
        _ => None,
    }
}

/// Fecha as unidades RELATIVAS de `font-size` contra a base herdada —
/// `120%` e `1.2em` multiplicam a fonte do PAI, `0.9rem` a da RAIZ. Sem pai
/// conhecido (elemento isolado, fora de qualquer `uiroot` estilizado), ambas
/// caem no omissão do browser ([`super::style::DEFAULT_FONT_PX`]).
fn resolve_relative_font(
    props: &mut super::style::StyleProps,
    parent_font: Option<f32>,
    root_font: Option<f32>,
) {
    let parent = parent_font.unwrap_or(super::style::DEFAULT_FONT_PX);
    let root = root_font.unwrap_or(super::style::DEFAULT_FONT_PX);
    if let Some(n) = props.font_size_rem.take() {
        props.font_size = Some(super::style::Measure::plain(Val::Px(root * n)));
    }
    if let Some(n) = props.font_size_em.take() {
        props.font_size = Some(super::style::Measure::plain(Val::Px(parent * n)));
    }
    if let Some(super::style::Measure::Plain(Val::Percent(p))) = props.font_size {
        props.font_size = Some(super::style::Measure::plain(Val::Px(parent * p / 100.0)));
    }
}

/// Marks an element scripts have flagged as disabled (`:disabled` rules, and
/// clicks are swallowed).
#[derive(Debug, Component)]
pub struct UiDisabled;

/// Marks an element with `pointer-events: none`: no `:hover` state, no clicks,
/// no tooltips — the pointer goes through it.
#[derive(Debug, Component)]
pub struct UiPointerNone;

/// Decorative nodes must explicitly pass focus: Bevy treats an absent policy
/// as Block, even without Interaction (labels would swallow their own button).
/// Controls, modal backdrops and scroll viewports capture unless CSS opts out.
pub(super) fn ui_focus_policy(blocks_pointer: bool, pointer_none: bool) -> FocusPolicy {
    if blocks_pointer && !pointer_none {
        FocusPolicy::Block
    } else {
        FocusPolicy::Pass
    }
}

/// A progress bar: `value` in 0..1 drives the width of the `fill` child.
#[derive(Debug, Component)]
pub struct UiBar {
    pub value: f32,
    pub fill: Entity,
    /// Fill grows along Y instead of X.
    pub vertical: bool,
}

/// A radial-style cooldown veil: `value` 0..1 is the *remaining* fraction and
/// drives the height of an overlay that wipes downward.
#[derive(Debug, Component)]
pub struct UiCooldown {
    pub value: f32,
    pub veil: Entity,
}

/// Clicks observed on an element this frame, keyed by id — drained by scripts
/// through `viber.ui.clicked(id)`.
#[derive(Debug, Default, Resource)]
pub struct UiClicks(pub Vec<String>);

/// Maps author ids to entities so scripts (and engine systems) can address
/// elements by name.
#[derive(Debug, Default, Resource)]
pub struct UiRegistry {
    pub by_id: HashMap<String, Entity>,
}

impl UiRegistry {
    pub fn get(&self, id: &str) -> Option<Entity> {
        self.by_id.get(id).copied()
    }
}

/// Interaction state of an element, from its `Interaction` and `UiDisabled`.
///
/// `pointer_none` flattens everything to `Normal`: a `pointer-events: none`
/// element must not light up `:hover` rules either.
pub fn state_of(
    interaction: Option<&Interaction>,
    disabled: bool,
    pointer_none: bool,
) -> StyleState {
    if disabled {
        return StyleState::Disabled;
    }
    if pointer_none {
        return StyleState::Normal;
    }
    match interaction {
        Some(Interaction::Pressed) => StyleState::Active,
        Some(Interaction::Hovered) => StyleState::Hover,
        _ => StyleState::Normal,
    }
}

/// The pieces of an element the cascade reads. Fetched for the styled element
/// and for each of its ancestors, so descendant selectors can be resolved.
type StyleLookup<'w, 's> = Query<
    'w,
    's,
    (
        &'static UiTag,
        Option<&'static UiId>,
        Option<&'static UiClasses>,
        Option<&'static Interaction>,
        Has<UiDisabled>,
        Has<UiPointerNone>,
        Option<&'static ChildOf>,
        Option<&'static super::widgets::UiCheck>,
    ),
>;

/// The text-side components a style writes into.
type TextQuery<'w, 's> = Query<
    'w,
    's,
    (
        &'static mut TextColor,
        &'static mut TextFont,
        Option<&'static mut TextLayout>,
    ),
>;

/// The paint-side components a style writes into. Every one is optional: a
/// text node has no border, an icon has no background of its own.
type PaintQuery<'w, 's> = Query<
    'w,
    's,
    (
        &'static mut Node,
        Option<&'static mut BackgroundColor>,
        Option<&'static mut BorderColor>,
        Option<&'static mut ZIndex>,
        Option<&'static mut UiTransform>,
    ),
>;

/// O texto ORIGINAL de um elemento com `text-transform` — o espelho escreve a
/// forma transformada no `Text` e guarda aqui o que o autor (ou script)
/// escreveu. `written` é a última coisa que O ESPELHO escreveu: qualquer
/// divergência entre `Text` e `written` é escrita externa (novo original).
#[derive(Debug, Clone, Component)]
pub struct UiTextMirror {
    pub original: String,
    pub written: String,
}

/// Aplica o `text-transform` computado aos textos, idempotente.
///
/// Corre DEPOIS do re-estilo (o `UiComputed` via commands já aplicou). Um
/// `set_text`/XML que muda o texto sob um transform é detectado pela
/// divergência com `written` — o novo texto torna-se o original e re-transforma.
#[allow(clippy::type_complexity)]
pub fn sync_text_transforms(
    mut query: Query<(Entity, &mut Text, &UiComputed, Option<&mut UiTextMirror>)>,
    mut commands: Commands,
) {
    for (entity, mut text, computed, mut mirror) in &mut query {
        let Some(transform) = computed.0.text_transform else {
            // A declaração saiu (classe/media) — restaura o original e desarma.
            if let Some(mirror) = mirror.as_deref_mut() {
                if **text != mirror.original {
                    **text = mirror.original.clone();
                }
            }
            if mirror.is_some() {
                commands.entity(entity).remove::<UiTextMirror>();
            }
            continue;
        };
        if let Some(mirror) = mirror.as_deref_mut() {
            if **text != mirror.written {
                mirror.original = (**text).clone();
            }
            let desired = transform.apply(&mirror.original);
            if **text != desired {
                **text = desired.clone();
            }
            mirror.written = desired;
        } else {
            let original = (**text).clone();
            let desired = transform.apply(&original);
            commands.entity(entity).insert(UiTextMirror {
                original,
                written: desired.clone(),
            });
            if **text != desired {
                **text = desired;
            }
        }
    }
}

/// Depth at which a HUD stops being a HUD; guards against a cycle in the
/// hierarchy turning the walk into a hang.
const MAX_ANCESTRY: usize = 32;

/// Builds the root-first ancestor chain for `entity`, self last.
fn ancestry(entity: Entity, lookup: &StyleLookup) -> Vec<Entity> {
    let mut chain = vec![entity];
    let mut current = entity;
    while chain.len() < MAX_ANCESTRY {
        // (…, ChildOf, UiCheck) — o último campo é o check; o pai é o penúltimo.
        let Ok((.., Some(parent), _)) = lookup.get(current) else {
            break;
        };
        current = parent.parent();
        if lookup.get(current).is_err() {
            break; // reached a non-UI parent; the chain ends here
        }
        chain.push(current);
    }
    chain.reverse();
    chain
}

/// Queries auxiliares do re-estilo, agrupadas num `SystemParam` — a função
/// principal chegou ao tecto de 16 parâmetros de sistema do Bevy.
#[derive(SystemParam)]
pub struct UiStyleInfo<'w, 's> {
    /// Filhos de cada elemento — índice de irmão (`:nth-child`) e `:empty`.
    pub children: Query<'w, 's, &'static Children>,
    /// Texto do próprio elemento (um `uitext` com texto não é `:empty`).
    pub texts: Query<'w, 's, &'static Text>,
    /// Tamanho computado do PAI (frame anterior) — `%` dentro de `calc()`.
    pub computed_nodes: Query<'w, 's, &'static ComputedNode>,
    /// `letter-spacing` anterior, para o reset honesto a 0.
    pub letters: Query<'w, 's, &'static mut LetterSpacing>,
    /// Estado de transição por elemento — diff de alvos e valores exibidos.
    pub tweens: Query<'w, 's, &'static mut super::tween::UiTransitions>,
}

/// Recomputes and writes the style of every dirty element.
///
/// Media conditions (`@media`) evaluate against the primary window, so a
/// resize rearranges the layout with nothing but the stylesheet's say-so.
#[allow(clippy::too_many_arguments)]
///
/// Splitting the write across `Node` / `BackgroundColor` / `TextFont` / … is
/// what makes the stylesheet feel like CSS while the ECS keeps its narrow
/// components: one resolved [`StyleProps`] fans out into whichever of them the
/// element actually has.
#[allow(clippy::type_complexity)]
pub fn apply_ui_styles(
    mut commands: Commands,
    sheet: Res<StyleSheet>,
    dirty: Query<Entity, (With<UiStyleDirty>, With<UiTag>)>,
    lookup: StyleLookup,
    mut nodes: PaintQuery,
    inlines: Query<&UiInlineStyle>,
    fades: Query<&super::fade::UiFade>,
    mut text: TextQuery,
    mut images: Query<&mut ImageNode>,
    blockers: Query<
        (),
        Or<(
            With<Button>,
            With<super::modal::UiModal>,
            With<super::modal::UiScroll>,
        )>,
    >,
    windows: Query<(&Window, Has<PrimaryWindow>)>,
    scale: Res<UiScale>,
    computed: Query<&UiComputed>,
    root_sheets: Query<&UiRootSheets>,
    mut info: UiStyleInfo,
) {
    // Tudo no espaço autoral (ver `scale::ui_viewport`): a janela dividida
    // pela escala do HUD. É o espaço em que os píxeis do CSS vivem — e o
    // ÚNICO: media queries e unidades de viewport avaliam nele, como num
    // browser, onde `max-width` e `px` falam o mesmo metro.
    let viewport = windows
        .iter()
        .find(|(_, primary)| *primary)
        .or_else(|| windows.iter().next())
        .map(|(w, _)| (w.resolution.width(), w.resolution.height()))
        .unwrap_or((1280.0, 720.0));
    let viewport = super::scale::ui_viewport(viewport.0, viewport.1, scale.0);

    // ── passada A — resolver sem herança ────────────────────────────────
    // O dirty set não tem ordem de documento, por isso PRIMEIRO resolvo
    // tudo (cascata + inline + viewport + fade), DEPOIS aplico dos pais
    // para os filhos, com o estilo computado do pai já fechado à mão.
    let mut resolved: Vec<(Entity, StyleProps, Vec<Entity>)> = Vec::new();
    for entity in &dirty {
        let chain = ancestry(entity, &lookup);
        // Shadow-DOM-lite: a folha visível é a que a RAIZ do ramo carrega.
        let allowed = chain
            .first()
            .and_then(|root| root_sheets.get(*root).ok())
            .map(|s| s.0.as_slice());
        // The borrow checker needs the parts before the refs: each `ElementRef`
        // points into the query's data, so collect the tuples first.
        let parts: Vec<(
            String,
            Option<String>,
            Vec<String>,
            StyleState,
            bool,
            bool,
            bool,
            usize,
            usize,
        )> = chain
            .iter()
            .filter_map(|e| lookup.get(*e).ok().map(|item| (*e, item)))
            .map(
                |(element, (tag, id, classes, interaction, disabled, pointer_none, parent, check))| {
                    // Posição entre irmãos: índice no Children do PAI, na
                    // ordem do DOM (a que `:nth-child` fala).
                    let (sibling_index, sibling_count) = parent
                        .and_then(|p| info.children.get(p.parent()).ok())
                        .and_then(|kids| {
                            kids.iter()
                                .position(|kid| kid == element)
                                .map(|index| (index, kids.len()))
                        })
                        .unwrap_or((0, 1));
                    let focused = classes.is_some_and(|c| c.has("focused"));
                    let checked = check.is_some_and(|c| c.checked);
                    let empty = info
                        .children
                        .get(element)
                        .map(|kids| kids.is_empty())
                        .unwrap_or(true)
                        && info
                            .texts
                            .get(element)
                            .map(|t| t.0.trim().is_empty())
                            .unwrap_or(true);
                    (
                        tag.0.clone(),
                        id.map(|i| i.0.clone()),
                        classes.map(|c| c.0.clone()).unwrap_or_default(),
                        state_of(interaction, disabled, pointer_none),
                        focused,
                        checked,
                        empty,
                        sibling_index,
                        sibling_count,
                    )
                },
            )
            .collect();
        let refs: Vec<super::style::ElementRef<'_>> = parts
            .iter()
            .map(
                |(tag, id, classes, state, focused, checked, empty, sibling_index, sibling_count)| {
                    super::style::ElementRef {
                        tag,
                        id: id.as_deref(),
                        classes,
                        state: *state,
                        focused: *focused,
                        checked: *checked,
                        empty: *empty,
                        sibling_index: *sibling_index,
                        sibling_count: *sibling_count,
                    }
                },
            )
            .collect();
        let mut props = sheet.resolve(&refs, viewport, allowed);
        if let Ok(inline) = inlines.get(entity) {
            props.merge(&inline.0);
        }
        // Viewport units já resolvem contra o espaço autoral (o próprio
        // viewport chegou dividido pela escala); píxeis autorais e %
        // passam intactos e o caminho normal do Taffy aplica-lhes a escala.
        // O `%` dentro de calc() fecha contra a LARGURA do pai — o
        // `ComputedNode` do frame anterior, repartido pela escala de volta ao
        // espaço autoral (um frame de atraso, como num browser a re-layout).
        let parent_size = (chain.len() > 1)
            .then(|| chain[chain.len() - 2])
            .and_then(|parent| info.computed_nodes.get(parent).ok())
            .map(|node| node.size / scale.0);
        props.resolve_viewport(viewport, parent_size);
        resolved.push((entity, props, chain));
    }
    // `chain` inclui o próprio elemento, por isso é também a profundidade —
    // ascendentes primeiro é o que a herança precisa.
    resolved.sort_by_key(|(.., chain)| chain.len());

    // ── passada B — herdar e escrever ───────────────────────────────────
    let mut fresh: HashMap<Entity, StyleProps> = HashMap::new();
    for (entity, mut props, chain) in resolved {
        // Base herdada: o computed do ancestral MAIS PRÓXIMO que o tenha. O
        // computed de um pai já contém o que ELE herdou (semântica CSS), por
        // isso basta o primeiro; o `fresh` cobre pais re-estilizados NESTE
        // frame, a cache cobre os que já foram estilizados noutro.
        let mut base = super::style::StyleProps::default();
        for ancestor in chain.iter().rev().skip(1) {
            let parent = fresh
                .get(ancestor)
                .or_else(|| computed.get(*ancestor).ok().map(|c| &c.0));
            if let Some(parent) = parent {
                base.apply_inherited(parent);
                break;
            }
        }
        let parent_font = font_px(base.font_size.as_ref());
        // `rem` usa a fonte da raiz do ramo (o topo da chain), não a do pai.
        let root_font = if chain.len() > 1 {
            let root = chain[0];
            fresh
                .get(&root)
                .and_then(|p| font_px(p.font_size.as_ref()))
                .or_else(|| {
                    computed
                        .get(root)
                        .ok()
                        .and_then(|c| font_px(c.0.font_size.as_ref()))
                })
        } else {
            None
        };
        props.apply_inherited(&base);
        resolve_relative_font(&mut props, parent_font, root_font);
        // Opacidade efetiva = a PRÓPRIA × a CSS dos ancestrais (GRUPO — uma
        // `opacity: 0.4` num pai esbate tudo lá dentro, como no browser) × os
        // fades de toda a cadeia. A fade is inherited: an ancestor dissolving
        // takes its whole widget with it instead of leaving the labels at
        // full strength.
        let mut opacity = props.opacity.unwrap_or(1.0).clamp(0.0, 1.0);
        for (index, ancestor) in chain.iter().enumerate() {
            if index + 1 < chain.len() {
                let ancestor_css = fresh
                    .get(ancestor)
                    .and_then(|props| props.opacity)
                    .or_else(|| {
                        computed
                            .get(*ancestor)
                            .ok()
                            .and_then(|computed| computed.0.opacity)
                    })
                    .unwrap_or(1.0);
                opacity *= ancestor_css.clamp(0.0, 1.0);
            }
            if let Ok(fade) = fades.get(*ancestor) {
                opacity *= fade.alpha.clamp(0.0, 1.0);
            }
        }
        // ── transitions: diff de alvos + inject do valor exibido ──────────
        // O diff corre contra os ALVOS guardados (não contra o `UiComputed`,
        // que contém valores interpolados a meio de um tween). Um tween
        // recém-iniciado escreve já o valor DE PARTIDA — o frame do re-estilo
        // mostra o estado antigo, e o driver anima a partir dele (sem salto).
        if let Ok(mut transitions) = info.tweens.get_mut(entity) {
            transitions.specs = props.transition.clone().unwrap_or_default();
            let specs = transitions.specs.clone();
            for spec in &specs {
                let Some(new_value) = spec.property.get(&props) else {
                    continue;
                };
                match transitions.targets.get(&spec.property) {
                    None => {
                        transitions.targets.insert(spec.property, new_value);
                    }
                    Some(old) if *old != new_value => {
                        transitions.start(
                            spec.property,
                            new_value,
                            spec.easing,
                            spec.duration,
                            spec.delay,
                            false,
                        );
                    }
                    Some(_) => {}
                }
            }
            let runs: Vec<super::tween::TweenRun> = transitions.tweens.clone();
            for run in &runs {
                if let Some(value) = run.current() {
                    run.field.set(&mut props, value);
                }
            }
        } else if let Some(specs) = props
            .transition
            .clone()
            .filter(|specs| !specs.is_empty())
        {
            // Primeiro estilo com `transition`: registra os alvos sem tween —
            // o estilo inicial NÃO transita (semântica do browser).
            let mut created = super::tween::UiTransitions::default();
            created.specs = specs;
            for spec in &created.specs {
                if let Some(value) = spec.property.get(&props) {
                    created.targets.insert(spec.property, value);
                }
            }
            commands.entity(entity).insert(created);
        }
        commands.entity(entity).insert(UiComputed(props.clone(), opacity));
        fresh.insert(entity, props.clone());

        if let Ok((mut node, background, border_color, z_index, transform)) = nodes.get_mut(entity)
        {
            // Recalcula TUDO a partir do omissão (semântica CSS de re-estilo:
            // um `@media` que deixa de valer desfaz o que escreveu).
            props.apply_fresh(&mut node);
            if let (Some(mut background), Some(color)) = (background, props.background) {
                *background = BackgroundColor(fade(color, opacity));
            }
            // Gradiente: `Some(Some)` pinta (por cima da cor); qualquer outra
            // coisa REMOVE — um @media que deixou de valer não pode deixar o
            // gradiente preso, e quem nunca o declarou não sente o no-op.
            match &props.background_gradient {
                Some(Some(spec)) => {
                    commands
                        .entity(entity)
                        .insert(BackgroundGradient(vec![to_bevy_gradient(spec, opacity)]));
                }
                _ => {
                    commands.entity(entity).remove::<BackgroundGradient>();
                }
            }
            // Cor da borda por lado: cada lado aceita o longhand próprio ou o
            // uniforme; um lado sem NENHUM dos dois mantém o que já lá estava
            // (compondo com a nova opacidade do frame).
            if let Some(mut border) = border_color {
                let uniform = props.border_color;
                let declared = [
                    props.border_top_color.or(uniform),
                    props.border_right_color.or(uniform),
                    props.border_bottom_color.or(uniform),
                    props.border_left_color.or(uniform),
                ];
                if declared.iter().any(|side| side.is_some()) {
                    let current: &BorderColor = border.as_ref();
                    let pick =
                        |side: Option<Color>, fallback: Color| fade(side.unwrap_or(fallback), opacity);
                    *border = BorderColor {
                        top: pick(declared[0], current.top),
                        right: pick(declared[1], current.right),
                        bottom: pick(declared[2], current.bottom),
                        left: pick(declared[3], current.left),
                    };
                }
            }
            // Contorno escuro garantido (regra do crítico, r5): via commands
            // porque `Outline` nem sempre existe no entity (mesmo padrão do
            // TextShadow abaixo).
            if let Some(spec) = props.outline {
                commands.entity(entity).insert(Outline::new(
                    Val::Px(spec.width),
                    Val::ZERO,
                    fade(spec.color, opacity),
                ));
            }
            // Sombras: lista vazia = `box-shadow: none` explícito (remove).
            match &props.box_shadow {
                Some(shadows) if shadows.is_empty() => {
                    commands.entity(entity).remove::<BoxShadow>();
                }
                Some(shadows) => {
                    let mut owned = shadows.clone();
                    for shadow in &mut owned {
                        shadow.color = fade(shadow.color, opacity);
                    }
                    commands.entity(entity).insert(BoxShadow(owned));
                }
                None => {}
            }
            // Recompute even when the declaration disappears (class/media
            // changes): auto restores each widget's policy, not always Block.
            let pointer_none = props.pointer_none.unwrap_or(false);
            let mut entity_commands = commands.entity(entity);
            entity_commands.insert(ui_focus_policy(blockers.contains(entity), pointer_none));
            if pointer_none {
                entity_commands.insert(UiPointerNone);
            } else {
                entity_commands.remove::<UiPointerNone>();
            }
            match props.cursor.clone() {
                Some(icon) => {
                    commands
                        .entity(entity)
                        .insert(super::widgets::UiCursorIcon(icon));
                }
                None => {
                    // A declaração desapareceu (classe/media) — o cursor
                    // também sai, em vez de ficar preso para sempre.
                    commands
                        .entity(entity)
                        .remove::<super::widgets::UiCursorIcon>();
                }
            }
            if let (Some(mut z), Some(value)) = (z_index, props.z_index) {
                *z = ZIndex(value);
            }
            if let Some(mut transform) = transform {
                // Reset+apply: sem `anim` e sem rotate/scale/translate, o
                // transform volta ao neutro (um bloco de media pode ter
                // escrito valores que já não valem).
                transform.rotation = Rot2::IDENTITY;
                transform.scale = Vec2::ONE;
                transform.translation = Val2::ZERO;
                if let Some(degrees) = props.rotate {
                    transform.rotation = Rot2::degrees(degrees);
                }
                if let Some(scale) = props.scale {
                    transform.scale = Vec2::splat(scale);
                }
                // Offset sem peso no layout; `anim` sobrepõe-no (corre depois).
                if let Some(translate) = props.translate {
                    transform.translation = Val2::px(translate.x, translate.y);
                }
            }
        }
        if let Ok((mut color, mut font, layout)) = text.get_mut(entity) {
            if let Some(value) = props.color {
                *color = TextColor(fade(value, opacity));
            }
            if let Some(size) = &props.font_size {
                // Pós-resolução é sempre Plain; exprs já fecharam em px.
                font.font_size = size.to_val().into();
            }
            if let Some(weight) = props.font_weight {
                font.weight = FontWeight(weight.clamp(1.0, 1000.0) as u16);
            }
            // letter-spacing: `em` fecha contra a fonte computada do próprio
            // elemento; sem declaração, o espaçamento volta a 0 (um class-swap
            // que o tira tem de o desfazer — semântica apply-fresh).
            match props.letter_spacing {
                Some(LetterSpacingSpec::Px(px)) => {
                    commands.entity(entity).insert(LetterSpacing::Px(px));
                }
                Some(LetterSpacingSpec::Em(em)) => {
                    let size = font_px(props.font_size.as_ref())
                        .unwrap_or(super::style::DEFAULT_FONT_PX);
                    commands.entity(entity).insert(LetterSpacing::Px(size * em));
                }
                None => {
                    if let Ok(mut spacing) = info.letters.get_mut(entity) {
                        if !matches!(*spacing, LetterSpacing::Px(0.0)) {
                            *spacing = LetterSpacing::Px(0.0);
                        }
                    }
                }
            }
            // `LineHeight` é componente próprio no Bevy 0.19 (não campo do
            // `TextFont`) — entra via commands, como as decorações.
            if let Some(line_height) = props.line_height {
                commands.entity(entity).insert(line_height);
            }
            if let Some(mut layout) = layout {
                if let Some(justify) = props.text_align {
                    layout.justify = justify;
                }
                if let Some(linebreak) = props.linebreak {
                    layout.linebreak = linebreak;
                }
            }
            // Decorações via commands: `Underline`/`Strikethrough` nem sempre
            // existem no entity, e `Off` desliga explicitamente uma herdada.
            match props.text_underline {
                Some(Decoration::On(color)) => {
                    commands.entity(entity).insert(Underline);
                    if let Some(color) = color {
                        commands
                            .entity(entity)
                            .insert(UnderlineColor(fade(color, opacity)));
                    }
                }
                Some(Decoration::Off) => {
                    commands
                        .entity(entity)
                        .remove::<Underline>()
                        .remove::<UnderlineColor>();
                }
                None => {}
            }
            match props.text_strikethrough {
                Some(Decoration::On(color)) => {
                    commands.entity(entity).insert(Strikethrough);
                    if let Some(color) = color {
                        commands
                            .entity(entity)
                            .insert(StrikethroughColor(fade(color, opacity)));
                    }
                }
                Some(Decoration::Off) => {
                    commands
                        .entity(entity)
                        .remove::<Strikethrough>()
                        .remove::<StrikethroughColor>();
                }
                None => {}
            }
            // The shadow carries the same opacity as the glyphs, or a fading
            // label would leave its own shadow behind.
            match props.text_shadow {
                Some(Some(spec)) => {
                    commands
                        .entity(entity)
                        .insert(bevy::ui::widget::TextShadow {
                            offset: spec.offset,
                            color: super::style::fade(spec.color, opacity),
                        });
                }
                Some(None) => {
                    commands
                        .entity(entity)
                        .remove::<bevy::ui::widget::TextShadow>();
                }
                None => {}
            }
        }
        if let Ok(mut image) = images.get_mut(entity) {
            if let Some(tint) = props.image_tint {
                image.color = fade(tint, opacity);
            } else {
                image.color = fade(Color::WHITE, opacity);
            }
            if let Some(mode) = props.image_mode.clone() {
                image.image_mode = mode;
            }
        }
        commands.entity(entity).remove::<UiStyleDirty>();
    }
}

/// Converte o spec autoral no gradiente da Bevy, já com a opacidade efetiva
/// nos stops (a mesma que baña as cores sólidas). O ângulo fala a convenção
/// CSS (0° = para cima, crescente no sentido horário) — a mesma da Bevy.
fn to_bevy_gradient(spec: &GradientSpec, opacity: f32) -> bevy::ui::Gradient {
    let stops = |list: &[GradientStop]| -> Vec<bevy::ui::ColorStop> {
        list.iter()
            .map(|stop| {
                let color = fade(stop.color, opacity);
                match stop.point {
                    Some(point) => bevy::ui::ColorStop::new(color, point),
                    None => bevy::ui::ColorStop::auto(color),
                }
            })
            .collect()
    };
    match spec {
        GradientSpec::Linear {
            angle_deg,
            stops: list,
        } => bevy::ui::Gradient::Linear(bevy::ui::LinearGradient::new(
            angle_deg.to_radians(),
            stops(list),
        )),
        GradientSpec::Radial {
            farthest,
            stops: list,
        } => {
            let shape = if *farthest {
                bevy::ui::RadialGradientShape::FarthestCorner
            } else {
                bevy::ui::RadialGradientShape::ClosestSide
            };
            bevy::ui::Gradient::Radial(bevy::ui::RadialGradient::new(
                bevy::ui::UiPosition::CENTER,
                shape,
                stops(list),
            ))
        }
    }
}

/// O texto completo de um elemento com `text-overflow: ellipsis`, a truncagem
/// em curso e a largura de conteúdo medida do texto COMPLETO (para saber
/// restaurar quando a caixa cresce).
#[derive(Debug, Clone, Component)]
pub struct UiTextOverflowMirror {
    pub full: String,
    /// A última coisa que ESTE sistema escreveu — divergência = escrita
    /// externa (set_text/transform/…), que recomeça a medição.
    pub written: String,
    /// Chars mantidos do `full` (`keep == chars` = texto inteiro à mostra).
    pub keep: usize,
    /// Largura de conteúdo medida com o full visível (0 = desconhecida).
    pub full_w: f32,
}

/// Aplica `text-overflow: ellipsis` — pós-LAYOUT, num circuito de retorno:
/// o `ComputedNode` lido aqui é do frame anterior; a truncagem estima pela
/// razão largura-caixa ÷ largura-conteúdo e o frame seguinte verifica. Estável
/// (sem transbordo em curso = zero escritas), como o resto da cadeia.
///
/// Só atua em texto de UMA linha (`line-break: none` no estilo) — texto que
/// quebra em linhas transborda na vertical e não é cortado. Corre DEPOIS do
/// `sync_text_transforms` (a truncagem parte do texto já transformado).
#[allow(clippy::type_complexity)]
pub fn sync_text_overflow(
    mut query: Query<(
        Entity,
        &mut Text,
        &UiComputed,
        &ComputedNode,
        Option<&mut UiTextOverflowMirror>,
    )>,
    mut commands: Commands,
) {
    const SLACK: f32 = 0.5;
    const MARGIN: f32 = 0.9; // margem para o `…` e para variação de glifos
    for (entity, mut text, computed, node, mut mirror) in &mut query {
        let Some(super::style::TextOverflowKind::Ellipsis) = computed.0.text_overflow else {
            // A declaração saiu — restaura o texto completo e desarma.
            if let Some(m) = mirror.as_deref_mut() {
                let full = m.full.clone();
                if **text != full {
                    **text = full;
                }
            }
            if mirror.is_some() {
                commands.entity(entity).remove::<UiTextOverflowMirror>();
            }
            continue;
        };
        // Uma linha só: com quebra, o texto transborda na vertical.
        if computed.0.linebreak != Some(LineBreak::NoWrap) {
            continue;
        }
        let node_w = node.size.x;
        if node_w <= SLACK {
            continue; // ainda sem layout (ou largura nula — nada a decidir)
        }
        let content_w = node.content_size.x;

        // Espelho: primeira passagem apenas regista o estado; a decisão de
        // truncagem começa no frame seguinte, com o `written` a valer o
        // texto atual (senão a detecção de escrita externa disparava à toa).
        if mirror.is_none() {
            let full = (**text).clone();
            commands.entity(entity).insert(UiTextOverflowMirror {
                written: full.clone(),
                keep: full.chars().count(),
                full,
                full_w: 0.0,
            });
            continue;
        }
        let m = mirror.as_deref_mut().expect("is_some acima");
        if **text != m.written {
            // Escrita externa (set_text/transform/…): novo original.
            m.full = (**text).clone();
            m.keep = m.full.chars().count();
            m.full_w = 0.0;
        }
        let chars = m.full.chars().count();
        let mut full_w = m.full_w;

        // Decisão do frame.
        let overflow = content_w > node_w + SLACK;
        let mut desired: Option<(String, usize)> = None;
        if overflow {
            if m.keep == chars {
                full_w = content_w; // primeira medição do texto completo
            }
            let reference = if m.keep == chars { content_w } else { full_w };
            if reference > 0.0 {
                let ratio = ((node_w / reference) * MARGIN).clamp(0.0, 1.0);
                let new_keep = ((chars as f32 * ratio) as usize).clamp(1, chars.saturating_sub(1));
                let cut: String = m.full.chars().take(new_keep).collect();
                desired = Some((format!("{cut}…"), new_keep));
            }
        } else if m.keep < chars {
            // Cabe truncado. Restaura o full SÓ quando a caixa já tem espaço
            // para ele na última medida — sem isto oscilava (cortar faz caber,
            // caber restaura, o full volta a transborda…).
            if full_w > 0.0 && node_w >= full_w - SLACK {
                desired = Some((m.full.clone(), chars));
            }
        } else {
            // Full à mostra e a caber: mede e guarda.
            full_w = content_w;
        }

        if let Some((written, keep)) = desired {
            if **text != written {
                **text = written.clone();
            }
            m.written = written;
            m.keep = keep;
        }
        m.full_w = full_w;
    }
}

/// Spreads a dirty mark down the subtree.
///
/// A rule like `.track.danger .fill` means a class toggled on the *track*
/// changes the *fill*'s style — so whenever an element is restyled, everything
/// under it has to be reconsidered too.
#[allow(clippy::type_complexity)]
pub fn propagate_style_dirty(
    mut commands: Commands,
    dirty: Query<Entity, Added<UiStyleDirty>>,
    children: Query<&Children>,
    elements: Query<(), (With<UiTag>, Without<UiStyleDirty>)>,
) {
    let mut stack: Vec<Entity> = dirty.iter().collect();
    let mut depth = 0;
    while let Some(entity) = stack.pop() {
        depth += 1;
        if depth > 4096 {
            break; // pathological tree; styling the rest next frame is fine
        }
        let Ok(kids) = children.get(entity) else {
            continue;
        };
        for child in kids.iter() {
            if elements.get(child).is_ok() {
                commands.entity(child).insert(UiStyleDirty);
            }
            stack.push(child);
        }
    }
}

/// Re-dirties elements whose interaction state changed, so `:hover` / `:active`
/// rules take effect without polling every element every frame.
#[allow(clippy::type_complexity)]
pub fn mark_interaction_dirty(
    mut commands: Commands,
    changed: Query<Entity, (Changed<Interaction>, With<UiTag>, Without<UiStyleDirty>)>,
) {
    for entity in &changed {
        commands.entity(entity).insert(UiStyleDirty);
    }
}

/// Dirties everything when the stylesheet itself is replaced (hot-reload).
pub fn mark_sheet_dirty(
    mut commands: Commands,
    sheet: Res<StyleSheet>,
    elements: Query<Entity, With<UiTag>>,
) {
    if !sheet.is_changed() || sheet.is_added() {
        return;
    }
    for entity in &elements {
        commands.entity(entity).insert(UiStyleDirty);
    }
}

/// Dirties everything when the WINDOW is resized, so `@media` blocks swap in
/// and viewport units re-resolve — the resize is what makes a layout
/// responsive instead of merely scalable.
///
/// `WindowResized` fires only on real changes, so this costs nothing on the
/// frames where nothing moved.
pub fn mark_resize_dirty(
    mut commands: Commands,
    mut resized: bevy::ecs::message::MessageReader<bevy::window::WindowResized>,
    elements: Query<Entity, With<UiTag>>,
) {
    if resized.read().next().is_none() {
        return;
    }
    for entity in &elements {
        commands.entity(entity).insert(UiStyleDirty);
    }
}

/// Width (or height) a bar fill gets for a 0..1 value.
pub fn bar_fill_size(value: f32) -> Val {
    Val::Percent((value.clamp(0.0, 1.0) * 100.0).max(0.0))
}

/// Mirrors [`UiBar::value`] onto the fill child's size.
///
/// Corre TODOS os frames (escrita idempotente): o re-estilo pode ter limpado
/// o `width` do fill — um resize com `@media` não pode congelar uma barra no
/// valor inicial.
#[allow(clippy::type_complexity)]
pub fn sync_ui_bars(bars: Query<&UiBar>, mut nodes: Query<&mut Node>) {
    for bar in &bars {
        let Ok(mut fill) = nodes.get_mut(bar.fill) else {
            continue;
        };
        let size = bar_fill_size(bar.value);
        if bar.vertical {
            if fill.height != size {
                fill.height = size;
            }
        } else if fill.width != size {
            fill.width = size;
        }
    }
}

/// Mirrors [`UiCooldown::value`] onto the veil child: full veil at 1 (just
/// fired), gone at 0 (ready). Todos os frames, idempotente — mesmo motivo.
#[allow(clippy::type_complexity)]
pub fn sync_ui_cooldowns(cooldowns: Query<&UiCooldown>, mut nodes: Query<&mut Node>) {
    for cooldown in &cooldowns {
        let Ok(mut veil) = nodes.get_mut(cooldown.veil) else {
            continue;
        };
        let remaining = cooldown.value.clamp(0.0, 1.0);
        let height = Val::Percent(remaining * 100.0);
        let display = if remaining <= 0.001 {
            Display::None
        } else {
            Display::Flex
        };
        if veil.height != height {
            veil.height = height;
        }
        if veil.display != display {
            veil.display = display;
        }
    }
}

/// Collects this frame's presses into [`UiClicks`] for scripts to read.
#[allow(clippy::type_complexity)]
pub fn collect_ui_clicks(
    mut clicks: ResMut<UiClicks>,
    // Option: apps mínimas de teste não registam o recurso de eventos.
    mut events: Option<ResMut<super::events::UiEvents>>,
    pressed: Query<
        (&Interaction, &UiId),
        (
            Changed<Interaction>,
            Without<UiDisabled>,
            Without<UiPointerNone>,
        ),
    >,
) {
    for (interaction, id) in &pressed {
        if *interaction == Interaction::Pressed && !clicks.0.contains(&id.0) {
            clicks.0.push(id.0.clone());
            // O mesmo clique entra na fila de eventos — o `viber.ui.events()`
            // drena o lote (o `clicked()` de 1 frame perdia cliques com
            // polling lento; aqui nada se perde enquanto o script ler).
            if let Some(ref mut events) = events {
                events.push_click(&id.0);
            }
        }
    }
}

/// Fim do frame: os cliques já foram publicados para os scripts (o
/// `luau_update` pode correr antes OU depois da UI no schedule) — limpa
/// para o frame seguinte.
pub fn clear_ui_clicks(mut clicks: ResMut<UiClicks>) {
    clicks.0.clear();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_scaled_px(value: Val, scale: f32, expected: f32) {
        let Val::Px(px) = value else {
            panic!("expected resolved pixels, got {value:?}");
        };
        assert!(
            (px * scale - expected).abs() < 1e-3,
            "{px}px at scale {scale} should occupy {expected} logical pixels"
        );
    }

    #[test]
    fn test_styles_evaluate_media_and_viewport_in_the_authored_space() {
        // Um browser só tem UM espaço de píxeis; aqui também. Media queries e
        // unidades de viewport avaliam em janela÷UiScale — o mesmo espaço dos
        // píxeis autorais — por isso `max-width: 850` lê-se "UI estreita" mesmo
        // numa janela fisicamente larga com o HUD ampliado (1000×1600 @1.5).
        for (width, height, scale, narrow) in [
            (1280, 720, 1.5, false),  // espaço autoral 853×480 — acima do corte
            (1920, 1080, 1.5, false), // 1280×720
            (600, 1000, 1.0, true),   // 600×1000
            (1000, 1600, 1.5, true),  // 667×1067 — janela larga, UI estreita
        ] {
            let mut app = style_app();
            app.world_mut().resource_mut::<UiScale>().0 = scale;
            app.world_mut().resource_mut::<StyleSheet>().parse_into(
                "uipanel { width: 10; height: 100vh; padding: 24; }
                 @media (max-width: 850) { uipanel { width: 50vw; } }",
            );
            // A secondary window is deliberately spawned first.
            app.world_mut().spawn(Window {
                resolution: (320, 240).into(),
                ..Default::default()
            });
            app.world_mut().spawn((
                Window {
                    resolution: (width, height).into(),
                    ..Default::default()
                },
                PrimaryWindow,
            ));
            let panel = app
                .world_mut()
                .spawn((Node::default(), UiTag("uipanel".into()), UiStyleDirty))
                .id();
            app.update();
            let ui = super::super::scale::ui_viewport(width as f32, height as f32, scale);
            let node = app.world().get::<Node>(panel).unwrap();
            // `100vh` preenche a janela REAL (o valor autoral ui_h é ampliado
            // pela escala de volta ao tamanho do ecrã).
            assert_scaled_px(node.height, scale, height as f32);
            if narrow {
                assert_eq!(node.width, Val::Px(ui.0 * 0.5), "{width}x{height}@{scale}");
            } else {
                assert_eq!(node.width, Val::Px(10.0), "{width}x{height}@{scale}");
            }
            assert_eq!(node.padding, UiRect::all(Val::Px(24.0)));
        }
    }

    fn style_app() -> App {
        let mut app = App::new();
        app.init_resource::<StyleSheet>()
            .init_resource::<UiScale>()
            .add_systems(Update, apply_ui_styles);
        app
    }

    #[test]
    fn test_text_properties_inherit_from_the_parent_computed_style() {
        let mut app = style_app();
        app.world_mut().resource_mut::<StyleSheet>().parse_into(
            "uiroot { color: #ff0000; font-size: 20; }
             uitext.gold { color: #00ff00 }",
        );
        let root = app
            .world_mut()
            .spawn((Node::default(), UiTag("uiroot".into()), UiStyleDirty))
            .id();
        let spawn_text = |app: &mut App, parent: Entity, gold: bool| {
            let mut e = app.world_mut().spawn((
                Text::new("a"),
                TextColor::default(),
                TextFont::default(),
                UiTag("uitext".into()),
                UiStyleDirty,
                ChildOf(parent),
            ));
            if gold {
                e.insert(UiClasses::parse("gold"));
            }
            e.id()
        };
        let plain = spawn_text(&mut app, root, false);
        let gold = spawn_text(&mut app, root, true);
        let deep = spawn_text(&mut app, gold, false);
        app.update();
        let is_red = |c: &TextColor| {
            let c = c.0.to_srgba();
            c.red > 0.9 && c.green < 0.1
        };
        let is_green = |c: &TextColor| {
            let c = c.0.to_srgba();
            c.green > 0.9 && c.red < 0.1
        };
        // plain herda do root; gold sobrepõe a COR mas mantém a fonte;
        // deep herda de gold (o computed do pai já dobrou a herança dele).
        let px = |app: &App, e: Entity| match app.world().get::<TextFont>(e).unwrap().font_size {
            bevy::text::FontSize::Px(v) => v,
            other => panic!("expected authored px, got {other:?}"),
        };
        assert!(is_red(app.world().get::<TextColor>(plain).unwrap()));
        assert_eq!(px(&app, plain), 20.0);
        assert!(is_green(app.world().get::<TextColor>(gold).unwrap()));
        assert_eq!(px(&app, gold), 20.0);
        assert!(is_green(app.world().get::<TextColor>(deep).unwrap()));
        assert_eq!(px(&app, deep), 20.0);
        // Filho que nasce DEPOIS: o pai já não está dirty — herda da cache.
        let late = spawn_text(&mut app, root, false);
        app.update();
        assert!(is_red(app.world().get::<TextColor>(late).unwrap()));
        assert_eq!(px(&app, late), 20.0);
    }

    #[test]
    fn test_relative_font_sizes_resolve_against_the_inherited_base() {
        let mut app = style_app();
        app.world_mut().resource_mut::<StyleSheet>().parse_into(
            "uiroot { font-size: 20 }
             uitext.pct { font-size: 150% }
             uitext.em { font-size: 0.5em }
             uitext.rem { font-size: 2rem }
             uitext.orphan { font-size: 1.5em }",
        );
        let font = |app: &App, e: Entity| match app.world().get::<TextFont>(e).unwrap().font_size {
            bevy::text::FontSize::Px(v) => v,
            other => panic!("expected authored px, got {other:?}"),
        };
        let root = app
            .world_mut()
            .spawn((Node::default(), UiTag("uiroot".into()), UiStyleDirty))
            .id();
        let spawn = |app: &mut App, class: &str, parent: Option<Entity>| {
            let mut e = app.world_mut().spawn((
                Text::new("a"),
                TextColor::default(),
                TextFont::default(),
                UiTag("uitext".into()),
                UiClasses::parse(class),
                UiStyleDirty,
            ));
            if let Some(parent) = parent {
                e.insert(ChildOf(parent));
            }
            e.id()
        };
        let pct = spawn(&mut app, "pct", Some(root));
        let em = spawn(&mut app, "em", Some(root));
        let rem = spawn(&mut app, "rem", Some(root));
        let orphan = spawn(&mut app, "orphan", None);
        app.update();
        // % e em leem o PAI (20); rem lê a RAIZ (20); o órfão não tem pai
        // estilizado — cai no omissão do browser (16 × 1.5).
        assert_eq!(font(&app, pct), 30.0);
        assert_eq!(font(&app, em), 10.0);
        assert_eq!(font(&app, rem), 40.0);
        assert_eq!(font(&app, orphan), 24.0);
    }

    #[test]
    fn test_pointer_events_restores_control_and_decorative_focus_defaults() {
        let mut app = style_app();
        let button = app
            .world_mut()
            .spawn((Node::default(), UiTag("uibutton".into()), Button))
            .id();
        let modal = app
            .world_mut()
            .spawn((
                Node::default(),
                UiTag("uimodal".into()),
                super::super::modal::UiModal {
                    key: KeyCode::KeyQ,
                    open: true,
                    escape_closes: true,
                },
            ))
            .id();
        let scroll = app
            .world_mut()
            .spawn((
                Node::default(),
                UiTag("uipanel".into()),
                super::super::modal::UiScroll::default(),
            ))
            .id();
        let root = app
            .world_mut()
            .spawn((Node::default(), UiTag("uiroot".into())))
            .id();
        let text = app
            .world_mut()
            .spawn((Text::new("Diário"), UiTag("uitext".into()), ChildOf(button)))
            .id();
        for declaration in [
            "pointer-events: none",
            "pointer-events: auto",
            "pointer-events: none",
            "",
        ] {
            let none = declaration.ends_with("none");
            for entity in [button, modal, scroll, root, text] {
                app.world_mut().entity_mut(entity).insert((
                    UiInlineStyle(super::super::style::parse_declarations(declaration, "test")),
                    UiStyleDirty,
                ));
            }
            app.update();
            for (entity, blocks) in [
                (button, true),
                (modal, true),
                (scroll, true),
                (root, false),
                (text, false),
            ] {
                assert_eq!(
                    app.world().get::<FocusPolicy>(entity),
                    Some(&ui_focus_policy(blocks, none))
                );
                assert_eq!(app.world().get::<UiPointerNone>(entity).is_some(), none);
            }
        }
    }

    #[test]
    fn test_styles_use_primary_window_in_one_pixel_space() {
        for (width, height) in [(1280, 720), (1920, 1080), (600, 1000)] {
            let mut app = style_app();
            let scale = 1.5;
            app.world_mut().resource_mut::<UiScale>().0 = scale;
            app.world_mut().resource_mut::<StyleSheet>().parse_into(
                "uipanel { width: 10; height: 100vh; padding: 24; }
                 @media (min-width: 1000) { uipanel { width: 100vw; } }
                 @media (portrait) and (min-height: 900) { uipanel { width: 100vw; } }",
            );
            // A secondary window is deliberately spawned first.
            app.world_mut().spawn(Window {
                resolution: (320, 240).into(),
                ..Default::default()
            });
            app.world_mut().spawn((
                Window {
                    resolution: (width, height).into(),
                    ..Default::default()
                },
                PrimaryWindow,
            ));
            let panel = app
                .world_mut()
                .spawn((Node::default(), UiTag("uipanel".into()), UiStyleDirty))
                .id();
            app.update();
            let node = app.world().get::<Node>(panel).unwrap();
            assert_scaled_px(node.height, scale, height as f32);
            // O media avalia no espaço autoral (janela÷escala): só 1920×1080
            // e 600×1000 (com escala 1) o cumprem a partir do espaço estreito.
            let ui = super::super::scale::ui_viewport(width as f32, height as f32, scale);
            let wide = ui.0 >= 1000.0 || (ui.0 < ui.1 && ui.1 >= 900.0);
            if wide {
                assert_scaled_px(node.width, scale, width as f32);
            } else {
                assert_eq!(node.width, Val::Px(10.0));
            }
            assert_eq!(node.padding, UiRect::all(Val::Px(24.0)));
        }
    }

    #[test]
    fn test_image_opacity_restores_white_at_full_strength() {
        let mut app = style_app();
        let icon = app
            .world_mut()
            .spawn((
                ImageNode::default(),
                UiTag("uiicon".into()),
                UiInlineStyle(StyleProps::default()),
            ))
            .id();
        for opacity in [0.0, 0.4, 1.0] {
            app.world_mut()
                .get_mut::<UiInlineStyle>(icon)
                .unwrap()
                .0
                .opacity = Some(opacity);
            app.world_mut().entity_mut(icon).insert(UiStyleDirty);
            app.update();
            assert_eq!(
                app.world().get::<ImageNode>(icon).unwrap().color,
                fade(Color::WHITE, opacity),
            );
        }
    }

    #[test]
    fn test_image_recovers_after_inherited_fade() {
        let mut app = style_app();
        let parent = app
            .world_mut()
            .spawn((
                UiTag("uipanel".into()),
                super::super::fade::UiFade::default(),
            ))
            .id();
        let icon = app
            .world_mut()
            .spawn((
                ImageNode::default(),
                UiTag("uiicon".into()),
                UiStyleDirty,
                ChildOf(parent),
            ))
            .id();
        app.update();
        assert_eq!(
            app.world().get::<ImageNode>(icon).unwrap().color,
            fade(Color::WHITE, 0.0)
        );
        app.world_mut()
            .get_mut::<super::super::fade::UiFade>(parent)
            .unwrap()
            .alpha = 1.0;
        app.world_mut().entity_mut(icon).insert(UiStyleDirty);
        app.update();
        let color = app.world().get::<ImageNode>(icon).unwrap().color.to_srgba();
        for channel in [color.red, color.green, color.blue, color.alpha] {
            assert!(
                (channel - 1.0).abs() < 1e-6,
                "fully restored white: {color:?}"
            );
        }
    }

    #[test]
    fn test_a_root_only_sees_the_sheets_it_loaded() {
        // Shadow-DOM-lite: a folha do HUD não pode vazar para o menu
        // (o incidente `.empty` do menu.css vs. slots do HUD).
        let mut app = style_app();
        let mut sheet = StyleSheet::default();
        sheet.parse_into("uipanel { width: 10 }"); // folha 0 — "tema"
        sheet.parse_into("uipanel { width: 40 }"); // folha 1 — "hud"
        app.world_mut().insert_resource(sheet);
        let hud_root = app
            .world_mut()
            .spawn((
                Node::default(),
                UiTag("uiroot".into()),
                UiRootSheets(vec![0, 1]),
            ))
            .id();
        let menu_root = app
            .world_mut()
            .spawn((
                Node::default(),
                UiTag("uiroot".into()),
                UiRootSheets(vec![0]),
            ))
            .id();
        let hud_panel = app
            .world_mut()
            .spawn((
                Node::default(),
                UiTag("uipanel".into()),
                UiStyleDirty,
                ChildOf(hud_root),
            ))
            .id();
        let menu_panel = app
            .world_mut()
            .spawn((
                Node::default(),
                UiTag("uipanel".into()),
                UiStyleDirty,
                ChildOf(menu_root),
            ))
            .id();
        app.update();
        assert_eq!(
            app.world().get::<Node>(hud_panel).unwrap().width,
            Val::Px(40.0)
        );
        assert_eq!(
            app.world().get::<Node>(menu_panel).unwrap().width,
            Val::Px(10.0),
            "a folha do hud não pode tocar a raiz do menu"
        );
    }

    #[test]
    fn test_cursor_is_removed_when_the_declaration_disappears() {
        let mut app = style_app();
        let panel = app
            .world_mut()
            .spawn((Node::default(), UiTag("uipanel".into()), UiStyleDirty))
            .id();
        app.world_mut().entity_mut(panel).insert((
            UiInlineStyle(super::super::style::parse_declarations(
                "cursor: pointer",
                "t",
            )),
            UiStyleDirty,
        ));
        app.update();
        assert!(
            app.world()
                .get::<super::super::widgets::UiCursorIcon>(panel)
                .is_some()
        );
        // A declaração desaparece (media/class swap) — o cursor também sai.
        app.world_mut()
            .entity_mut(panel)
            .insert(UiInlineStyle(super::super::style::StyleProps::default()));
        app.world_mut().entity_mut(panel).insert(UiStyleDirty);
        app.update();
        assert!(
            app.world()
                .get::<super::super::widgets::UiCursorIcon>(panel)
                .is_none()
        );
    }

    #[test]
    fn test_classes_parse_add_and_remove() {
        let mut classes = UiClasses::parse("Chip  gold ");
        assert_eq!(classes.0, vec!["chip".to_string(), "gold".to_string()]);
        assert!(classes.has("gold"));
        assert!(!classes.add("GOLD"), "adding a present class is a no-op");
        assert!(classes.add("empty"));
        assert!(classes.remove("chip"));
        assert!(
            !classes.remove("chip"),
            "removing twice reports nothing done"
        );
        assert_eq!(classes.0, vec!["gold".to_string(), "empty".to_string()]);
    }

    #[test]
    fn test_state_of_prefers_disabled_then_pressed_then_hover() {
        assert_eq!(
            state_of(Some(&Interaction::Pressed), true, false),
            StyleState::Disabled
        );
        assert_eq!(
            state_of(Some(&Interaction::Pressed), false, false),
            StyleState::Active
        );
        assert_eq!(
            state_of(Some(&Interaction::Hovered), false, false),
            StyleState::Hover
        );
        assert_eq!(
            state_of(Some(&Interaction::None), false, false),
            StyleState::Normal
        );
        assert_eq!(state_of(None, false, false), StyleState::Normal);
        // `pointer-events: none` aplaina o estado — nem `:hover`.
        assert_eq!(
            state_of(Some(&Interaction::Hovered), false, true),
            StyleState::Normal
        );
        // Mas `:disabled` ganha sempre ao pointer-events.
        assert_eq!(
            state_of(Some(&Interaction::Hovered), true, true),
            StyleState::Disabled
        );
    }

    #[test]
    fn test_bar_fill_size_clamps_to_the_track() {
        assert_eq!(bar_fill_size(0.5), Val::Percent(50.0));
        assert_eq!(bar_fill_size(-2.0), Val::Percent(0.0));
        assert_eq!(bar_fill_size(7.0), Val::Percent(100.0));
    }

    #[test]
    fn test_sync_ui_bars_writes_the_fill_width() {
        let mut world = World::new();
        let fill = world.spawn(Node::default()).id();
        world.spawn(UiBar {
            value: 0.25,
            fill,
            vertical: false,
        });
        #[allow(clippy::type_complexity)]
        let mut state: bevy::ecs::system::SystemState<(Query<&UiBar>, Query<&mut Node>)> =
            bevy::ecs::system::SystemState::new(&mut world);
        let (bars, nodes) = state.get_mut(&mut world).expect("system state");
        sync_ui_bars(bars, nodes);
        assert_eq!(world.get::<Node>(fill).unwrap().width, Val::Percent(25.0));
    }

    #[test]
    fn test_sync_ui_cooldowns_hides_the_veil_when_ready() {
        let mut world = World::new();
        let veil = world.spawn(Node::default()).id();
        world.spawn(UiCooldown { value: 0.0, veil });
        #[allow(clippy::type_complexity)]
        let mut state: bevy::ecs::system::SystemState<(
            Query<&UiCooldown>,
            Query<&mut Node>,
        )> = bevy::ecs::system::SystemState::new(&mut world);
        let (cooldowns, nodes) = state.get_mut(&mut world).expect("system state");
        sync_ui_cooldowns(cooldowns, nodes);
        let node = world.get::<Node>(veil).unwrap();
        assert_eq!(node.display, Display::None, "a ready ability shows no veil");
    }

    #[test]
    fn test_text_overflow_truncates_and_restores() {
        let mut world = World::new();
        let full = "um texto bem comprido para transbordar a caixa".to_string();
        let props = StyleProps {
            text_overflow: Some(super::super::style::TextOverflowKind::Ellipsis),
            linebreak: Some(LineBreak::NoWrap),
            ..Default::default()
        };
        let entity = world
            .spawn((
                Text::new(full.clone()),
                UiComputed(props, 1.0),
                ComputedNode {
                    size: Vec2::new(50.0, 20.0),
                    content_size: Vec2::new(200.0, 20.0),
                    ..Default::default()
                },
            ))
            .id();

        #[allow(clippy::type_complexity)]
        let mut state: bevy::ecs::system::SystemState<(
            Query<
                (
                    Entity,
                    &mut Text,
                    &UiComputed,
                    &ComputedNode,
                    Option<&mut UiTextOverflowMirror>,
                ),
            >,
            Commands,
        )> = bevy::ecs::system::SystemState::new(&mut world);

        // 1.º frame: primeiro contacto — o espelho só nasce (sem escrita).
        {
            let (mut query, mut commands) = state.get_mut(&mut world).expect("system state");
            sync_text_overflow(query.reborrow(), commands.reborrow());
            state.apply(&mut world);
            let mirror = world.get::<UiTextOverflowMirror>(entity).expect("espelho criado");
            assert_eq!(mirror.keep, full.chars().count());
        }

        // 2.º frame: transborda (200 de conteúdo numa caixa de 50) — corta.
        {
            let (mut query, mut commands) = state.get_mut(&mut world).expect("system state");
            sync_text_overflow(query.reborrow(), commands.reborrow());
            state.apply(&mut world);
            let text = world.get::<Text>(entity).unwrap();
            let mirror = world.get::<UiTextOverflowMirror>(entity).unwrap();
            assert!(text.0.ends_with('…'), "cortou: {}", text.0);
            assert!(text.0.chars().count() < full.chars().count());
            assert!((mirror.full_w - 200.0).abs() < 1e-4, "mede o full");
        }

        // 3.º frame: a caixa cresce o suficiente para o full (300 >= 200) — restaura.
        world.get_mut::<ComputedNode>(entity).unwrap().size = Vec2::new(300.0, 20.0);
        {
            let (mut query, mut commands) = state.get_mut(&mut world).expect("system state");
            sync_text_overflow(query.reborrow(), commands.reborrow());
            state.apply(&mut world);
            let text = world.get::<Text>(entity).unwrap();
            assert_eq!(text.0, full, "restaura o texto completo");
            let mirror = world.get::<UiTextOverflowMirror>(entity).unwrap();
            assert_eq!(mirror.keep, full.chars().count());
        }
    }

    #[test]
    fn test_registry_lookup() {
        let mut registry = UiRegistry::default();
        let entity = Entity::from_raw_u32(7).expect("valid entity id");
        registry.by_id.insert("hp".into(), entity);
        assert_eq!(registry.get("hp"), Some(entity));
        assert_eq!(registry.get("nope"), None);
    }
}
