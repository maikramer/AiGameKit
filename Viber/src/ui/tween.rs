//! Motion: `transition` e `@keyframes` para o dialeto CSS, e `viber.ui.tween`
//! para a Luau — todos sobre o MESMO motor de interpolação.
//!
//! Arquitetura (porquê não interpolar componentes diretamente): o re-estilo
//! escreve o estilo FINAL em componentes espalhados (`Node`, `BackgroundColor`,
//! `TextColor`, `UiTransform`…) e a opacidade de grupo é cozida NAS CORES no
//! momento da escrita. Um interpolador que escrevesse componentes teria de
//! reproduzir essa fan-out inteiro. Em vez disso o motor só escreve no
//! [`UiComputed`] (o "computed style") e marca o elemento dirty — o
//! `apply_ui_styles` do frame seguinte faz o fan-out inteiro de graça, com a
//! opacidade e a herança certas.
//!
//! ```text
//! re-estilo (dirty): diff de alvos → inicia tweens → escreve logo o valor DE
//!                    PARTIDA (sem salto para o destino)
//! driver (por frame): avança tweens → UiComputed atual + UiStyleDirty
//!                     (o re-estilo do frame seguinte fan-out)
//! ```
//!
//! Um elemento em transição é re-estilizado por frame ENQUANTO anima (uns 20
//! frames de 0.3 s) — o mesmo custo de um hover, e parado custa zero.

use std::collections::HashMap;

use bevy::prelude::*;

use super::events::{UiEvent, UiEvents};
use super::runtime::{UiComputed, UiStyleDirty};
use super::style::{AnimationSpec, Iterations, Keyframe, StyleProps, StyleSheet};

// ── valores interpoláveis ───────────────────────────────────────────────

/// Unidade de um valor numérico interpolável (pós-`resolve_viewport` só há
/// estas duas; `auto`/expressões não transicionam).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum NumUnit {
    Px,
    Percent,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TweenValue {
    Num(NumUnit, f32),
    Color(bevy::color::Color),
}

impl TweenValue {
    /// Interpola; unidades diferentes NÃO transicionam (salto seco, documentado).
    pub fn lerp(self, other: Self, t: f32) -> Option<Self> {
        match (self, other) {
            (Self::Num(unit_a, a), Self::Num(unit_b, b)) if unit_a == unit_b => {
                Some(Self::Num(unit_a, a + (b - a) * t))
            }
            (Self::Color(a), Self::Color(b)) => Some(Self::Color(lerp_color(a, b, t))),
            _ => None,
        }
    }

    /// O número por baixo (testes e asserts).
    pub fn num_value(&self) -> f32 {
        match self {
            Self::Num(_, v) => *v,
            Self::Color(_) => 0.0,
        }
    }
}

/// Lerp em sRGB — previsível para o autor (o oklab da Bevy escurece os meios).
pub fn lerp_color(from: bevy::color::Color, to: bevy::color::Color, t: f32) -> bevy::color::Color {
    let (a, b) = (from.to_srgba(), to.to_srgba());
    bevy::color::Color::Srgba(bevy::color::Srgba {
        red: a.red + (b.red - a.red) * t,
        green: a.green + (b.green - a.green) * t,
        blue: a.blue + (b.blue - a.blue) * t,
        alpha: a.alpha + (b.alpha - a.alpha) * t,
    })
}

/// Que campo de estilo um tween anima — o subconjunto "animável" do dialeto.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum StyleField {
    Opacity,
    Background,
    BorderColor,
    Color,
    Width,
    Height,
    Top,
    Right,
    Bottom,
    Left,
    Rotate,
    Scale,
    FontSize,
}

/// Os campos que o motor sabe ler/escrever — a mesma lista em todos os
/// circuitos (get/set, keyframes).
pub const TWEENABLE_FIELDS: [StyleField; 13] = [
    StyleField::Opacity,
    StyleField::Background,
    StyleField::BorderColor,
    StyleField::Color,
    StyleField::Width,
    StyleField::Height,
    StyleField::Top,
    StyleField::Right,
    StyleField::Bottom,
    StyleField::Left,
    StyleField::Rotate,
    StyleField::Scale,
    StyleField::FontSize,
];

impl StyleField {
    /// O nome CSS (`transition: width 0.3s`, `property = "width"`).
    pub fn parse(name: &str) -> Option<Self> {
        Some(match name.trim().to_ascii_lowercase().as_str() {
            "opacity" => Self::Opacity,
            "background" | "background-color" => Self::Background,
            "border-color" => Self::BorderColor,
            "color" => Self::Color,
            "width" => Self::Width,
            "height" => Self::Height,
            "top" => Self::Top,
            "right" => Self::Right,
            "bottom" => Self::Bottom,
            "left" => Self::Left,
            "rotate" => Self::Rotate,
            "scale" => Self::Scale,
            "font-size" => Self::FontSize,
            _ => return None,
        })
    }

    /// Nome CSS de volta (eventos, debug).
    pub fn name(self) -> &'static str {
        match self {
            Self::Opacity => "opacity",
            Self::Background => "background",
            Self::BorderColor => "border-color",
            Self::Color => "color",
            Self::Width => "width",
            Self::Height => "height",
            Self::Top => "top",
            Self::Right => "right",
            Self::Bottom => "bottom",
            Self::Left => "left",
            Self::Rotate => "rotate",
            Self::Scale => "scale",
            Self::FontSize => "font-size",
        }
    }

    /// Lê o campo de props já RESOLVIDAS (`resolve_viewport` corrido).
    pub fn get(self, props: &StyleProps) -> Option<TweenValue> {
        let num = |measure: Option<&super::style::Measure>| -> Option<TweenValue> {
            match measure {
                Some(super::style::Measure::Plain(Val::Px(px))) => {
                    Some(TweenValue::Num(NumUnit::Px, *px))
                }
                Some(super::style::Measure::Plain(Val::Percent(p))) => {
                    Some(TweenValue::Num(NumUnit::Percent, *p))
                }
                _ => None,
            }
        };
        Some(match self {
            Self::Opacity => TweenValue::Num(NumUnit::Px, props.opacity.unwrap_or(1.0)),
            Self::Background => TweenValue::Color(props.background?),
            Self::BorderColor => TweenValue::Color(props.border_color?),
            Self::Color => TweenValue::Color(props.color?),
            Self::Width => num(props.width.as_ref())?,
            Self::Height => num(props.height.as_ref())?,
            Self::Top => num(props.top.as_ref())?,
            Self::Right => num(props.right.as_ref())?,
            Self::Bottom => num(props.bottom.as_ref())?,
            Self::Left => num(props.left.as_ref())?,
            Self::Rotate => TweenValue::Num(NumUnit::Px, props.rotate.unwrap_or(0.0)),
            Self::Scale => TweenValue::Num(NumUnit::Px, props.scale.unwrap_or(1.0)),
            Self::FontSize => num(props.font_size.as_ref())?,
        })
    }

    /// Escreve o valor de volta em props (o mesmo campo que [`Self::get`]).
    pub fn set(self, props: &mut StyleProps, value: TweenValue) {
        let set_num = |measure: &mut Option<super::style::Measure>, value: TweenValue| {
            if let TweenValue::Num(unit, v) = value {
                *measure = Some(super::style::Measure::plain(match unit {
                    NumUnit::Px => Val::Px(v),
                    NumUnit::Percent => Val::Percent(v),
                }));
            }
        };
        match self {
            Self::Opacity => {
                if let TweenValue::Num(_, v) = value {
                    props.opacity = Some(v);
                }
            }
            Self::Background => {
                if let TweenValue::Color(c) = value {
                    props.background = Some(c);
                }
            }
            Self::BorderColor => {
                if let TweenValue::Color(c) = value {
                    props.border_color = Some(c);
                }
            }
            Self::Color => {
                if let TweenValue::Color(c) = value {
                    props.color = Some(c);
                }
            }
            Self::Width => set_num(&mut props.width, value),
            Self::Height => set_num(&mut props.height, value),
            Self::Top => set_num(&mut props.top, value),
            Self::Right => set_num(&mut props.right, value),
            Self::Bottom => set_num(&mut props.bottom, value),
            Self::Left => set_num(&mut props.left, value),
            Self::Rotate => {
                if let TweenValue::Num(_, v) = value {
                    props.rotate = Some(v);
                }
            }
            Self::Scale => {
                if let TweenValue::Num(_, v) = value {
                    props.scale = Some(v);
                }
            }
            Self::FontSize => set_num(&mut props.font_size, value),
        }
    }
}

// ── easings ─────────────────────────────────────────────────────────────

/// Curva de tempo — os nomes do CSS mais `cubic-bezier` e `steps`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Easing {
    Linear,
    Ease,
    EaseIn,
    EaseOut,
    EaseInOut,
    /// `cubic-bezier(x1, y1, x2, y2)` com x em 0..1 (a restrição do CSS).
    CubicBezier(f32, f32, f32, f32),
    /// `steps(n)` — escada com n degraus.
    Steps(u32),
}

impl Easing {
    pub fn parse(text: &str) -> Option<Self> {
        let text = text.trim().to_ascii_lowercase();
        Some(match text.as_str() {
            "linear" => Self::Linear,
            "ease" => Self::Ease,
            "ease-in" => Self::EaseIn,
            "ease-out" => Self::EaseOut,
            "ease-in-out" => Self::EaseInOut,
            _ => {
                if let Some(rest) = text.strip_prefix("cubic-bezier(") {
                    let rest = rest.strip_suffix(')')?;
                    let args: Vec<f32> = rest
                        .split(',')
                        .filter_map(|p| p.trim().parse().ok())
                        .collect();
                    if args.len() != 4 {
                        return None;
                    }
                    return Some(Self::CubicBezier(args[0], args[1], args[2], args[3]));
                }
                if let Some(rest) = text.strip_prefix("steps(") {
                    let rest = rest.strip_suffix(')')?;
                    return rest
                        .trim()
                        .parse()
                        .ok()
                        .filter(|n| *n > 0)
                        .map(Self::Steps);
                }
                return None;
            }
        })
    }

    /// progress 0..1 → eased 0..1.
    pub fn eval(self, t: f32) -> f32 {
        let t = t.clamp(0.0, 1.0);
        match self {
            Self::Linear => t,
            Self::Ease => cubic_bezier(0.25, 0.1, 0.25, 1.0, t),
            Self::EaseIn => cubic_bezier(0.42, 0.0, 1.0, 1.0, t),
            Self::EaseOut => cubic_bezier(0.0, 0.0, 0.58, 1.0, t),
            Self::EaseInOut => cubic_bezier(0.42, 0.0, 0.58, 1.0, t),
            Self::CubicBezier(x1, y1, x2, y2) => cubic_bezier(x1, y1, x2, y2, t),
            Self::Steps(n) => (t * n as f32).floor() / n as f32,
        }
    }
}

/// Bézier cúbica CSS: x é tempo, y é progresso. Resolve x→t por bisseção
/// (x é monotónico com a restrição x∈0..1) e devolve y(t).
fn cubic_bezier(x1: f32, y1: f32, x2: f32, y2: f32, x: f32) -> f32 {
    let at = |t: f32, p1: f32, p2: f32| -> f32 {
        3.0 * (1.0 - t) * (1.0 - t) * t * p1 + 3.0 * (1.0 - t) * t * t * p2 + t * t * t
    };
    if x <= 0.0 {
        return 0.0;
    }
    if x >= 1.0 {
        return 1.0;
    }
    // Bisseção em t para bezier_x(t) = x — 24 iterações dão precisão sub-pixel.
    let mut low = 0.0_f32;
    let mut high = 1.0_f32;
    let mut t = x;
    for _ in 0..24 {
        let value = at(t, x1, x2);
        if (value - x).abs() < 1e-6 {
            break;
        }
        if value < x {
            low = t;
        } else {
            high = t;
        }
        t = (low + high) * 0.5;
    }
    at(t, y1, y2)
}

// ── estado por elemento ─────────────────────────────────────────────────

/// Um tween a correr num elemento.
#[derive(Debug, Clone)]
pub struct TweenRun {
    pub field: StyleField,
    pub from: TweenValue,
    pub to: TweenValue,
    pub easing: Easing,
    /// Segundos decorridos (o delay ainda não consumido trava o progresso).
    pub elapsed: f32,
    pub duration: f32,
    pub delay: f32,
    /// `true` = pedido explícito (`viber.ui.tween`), termina com evento
    /// `tween_done` etiquetado com o id do elemento.
    pub explicit: bool,
    /// O id autoral do elemento (só para tweens explícitos — vai no evento).
    pub label: String,
}

impl TweenRun {
    /// Valor atual; `None` quando o par de valores não transiciona.
    pub fn current(&self) -> Option<TweenValue> {
        let active = (self.elapsed - self.delay).clamp(0.0, self.duration);
        let t = if self.duration <= f32::EPSILON {
            1.0
        } else {
            active / self.duration
        };
        self.from.lerp(self.to, self.easing.eval(t))
    }

    pub fn finished(&self) -> bool {
        self.elapsed >= self.delay + self.duration
    }
}

/// Estado de transição de um elemento: a spec da cascata, os ÚLTIMOS valores
/// alvo (o diff corre contra ESTES, não contra o `UiComputed` — que contém
/// valores interpolados) e os tweens em curso.
#[derive(Debug, Clone, Default, Component)]
pub struct UiTransitions {
    pub specs: Vec<super::style::TransitionSpec>,
    pub targets: HashMap<StyleField, TweenValue>,
    pub tweens: Vec<TweenRun>,
    /// Id autoral do elemento (para o evento `tween_done` dos explícitos).
    pub label: String,
}

impl UiTransitions {
    /// Valor exibido AGORA para um campo: o tween em curso se houver, senão o
    /// alvo — é o que um novo tween usa como `from` (sem salto a meio).
    pub fn shown(&self, field: StyleField) -> Option<TweenValue> {
        if let Some(run) = self
            .tweens
            .iter()
            .find(|run| run.field == field && !run.finished())
        {
            return run.current();
        }
        self.targets.get(&field).copied()
    }

    /// Inicia (ou re-alvo) o tween de um campo a partir do valor exibido.
    /// `false` = o par de valores não transiciona (unidades mistas).
    pub fn start(
        &mut self,
        field: StyleField,
        to: TweenValue,
        easing: Easing,
        duration: f32,
        delay: f32,
        explicit: bool,
    ) -> bool {
        let Some(from) = self.shown(field) else {
            return false;
        };
        // Re-alvo no MEIO de um tween: parte do valor exibido (sem salto).
        self.tweens.retain(|run| run.field != field || run.explicit);
        self.tweens.push(TweenRun {
            field,
            from,
            to,
            easing,
            elapsed: 0.0,
            duration: duration.max(0.0),
            delay: delay.max(0.0),
            explicit,
            label: String::new(),
        });
        self.targets.insert(field, to);
        true
    }
}

/// O avança-tweens. Escreve o valor interpolado no [`UiComputed`] e marca o
/// elemento dirty — o re-estilo do frame seguinte faz o fan-out (o driver
/// corre DEPOIS do `apply_ui_styles` na cadeia do `UiSet::Style`; a latência
/// de 1 frame é invisível a 60 fps e o valor DE PARTIDA já foi escrito pelo
/// re-estilo que iniciou o tween, por isso não há salto).
#[allow(clippy::type_complexity)]
pub fn drive_ui_transitions(
    time: Res<Time>,
    mut events: ResMut<UiEvents>,
    mut query: Query<(Entity, &mut UiTransitions, &mut UiComputed)>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut transitions, mut computed) in &mut query {
        if transitions.tweens.is_empty() {
            continue;
        }
        let label = transitions.label.clone();
        let mut dirty = false;
        transitions.tweens.retain_mut(|run| {
            run.elapsed += dt;
            if let Some(value) = run.current() {
                run.field.set(&mut computed.0, value);
                dirty = true;
            }
            let done = run.finished();
            if done && run.explicit {
                events.push(UiEvent::TweenDone {
                    id: if label.is_empty() {
                        format!("{entity:?}")
                    } else {
                        label.clone()
                    },
                    property: run.field.name().to_string(),
                });
            }
            !done
        });
        if dirty {
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
}

// ── @keyframes ──────────────────────────────────────────────────────────

/// Animação nomeada em curso num elemento (a spec vem do `UiComputed`).
#[derive(Debug, Clone, Component)]
pub struct UiAnimation {
    pub spec: AnimationSpec,
    /// Segundos desde o arranque da animação (delay incluído).
    pub elapsed: f32,
}

/// Resolve o offset 0..1 atual de uma animação, com loops/alternate/reverse.
/// `None` = ainda no delay ou terminada (sem fill).
pub fn animation_offset(spec: &AnimationSpec, elapsed: f32) -> Option<f32> {
    let active = elapsed - spec.delay;
    if active < 0.0 {
        return None;
    }
    if spec.duration <= f32::EPSILON {
        return Some(1.0);
    }
    let progress = active / spec.duration;
    let (cycle, phase) = match spec.iterations {
        // Infinite também conta ciclos: o `alternate` inverte nos ímpares.
        Iterations::Infinite => (progress.floor(), progress.fract()),
        Iterations::Count(n) => {
            if progress >= n {
                // Terminada: `forwards` segura o último frame (o driver deixa
                // o UiComputed como está e remove o componente).
                return None;
            }
            (progress.floor(), progress.fract())
        }
    };
    let mut offset = phase;
    if spec.alternate && (cycle as i64) % 2 == 1 {
        offset = 1.0 - offset;
    }
    if spec.reverse {
        offset = 1.0 - offset;
    }
    Some(offset)
}

/// Liga/desliga o componente `UiAnimation` ao `animation` do `UiComputed`.
///
/// O driver anima o `UiComputed` a cada frame, que o marca `Changed` — por
/// isso a (re)inserção SÓ acontece quando a spec mudou face ao componente
/// existente, senão o `elapsed` zerava a cada frame e a animação não andava.
#[allow(clippy::type_complexity)]
pub fn sync_ui_animations(
    mut commands: Commands,
    sheet: Res<StyleSheet>,
    mut warned: Local<HashMap<String, ()>>,
    changed: Query<(Entity, &UiComputed, Option<&UiAnimation>), Changed<UiComputed>>,
) {
    for (entity, computed, existing) in &changed {
        match &computed.0.animation {
            Some(spec) => {
                if !sheet.has_keyframes(&spec.name) {
                    if warned.insert(spec.name.clone(), ()).is_none() {
                        warn!(
                            "ui style: @keyframes `{}` não existe — animation ignorada",
                            spec.name
                        );
                    }
                    commands.entity(entity).remove::<UiAnimation>();
                    continue;
                }
                let same = existing.is_some_and(|animation| animation.spec == *spec);
                if !same {
                    commands.entity(entity).insert(UiAnimation {
                        spec: spec.clone(),
                        elapsed: 0.0,
                    });
                }
            }
            None => {
                if existing.is_some() {
                    commands.entity(entity).remove::<UiAnimation>();
                }
            }
        }
    }
}

/// O driver das `animation`: escreve o overlay dos keyframes no `UiComputed`
/// e marca dirty — o mesmo caminho das transitions. `forwards` segura o
/// último frame sem continuar a re-estilizar (o componente sai e o
/// `UiComputed` fica com o overlay final até à próxima alteração real).
#[allow(clippy::type_complexity)]
pub fn drive_ui_animations(
    sheet: Res<StyleSheet>,
    time: Res<Time>,
    mut animations: Query<(Entity, &mut UiAnimation, &mut UiComputed)>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut animation, mut computed) in &mut animations {
        let Some(keyframes) = sheet.keyframes(&animation.spec.name) else {
            continue;
        };
        animation.elapsed += dt;
        let Some(offset) = animation_offset(&animation.spec, animation.elapsed) else {
            if !animation.spec.fill_forwards {
                commands.entity(entity).remove::<UiAnimation>();
                commands.entity(entity).insert(UiStyleDirty);
            }
            continue;
        };
        if let Some(overlay) = sample_keyframes(keyframes, offset, &animation.spec) {
            computed.0.merge(&overlay);
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
}

/// Interpola os frames vizinhos de `offset`. Os campos interpoláveis presentes
/// nos DOIS frames interpolam com a easing da spec; os restantes ficam do
/// frame mais próximo (a troca discreta do CSS).
fn sample_keyframes(
    keyframes: &[Keyframe],
    offset: f32,
    spec: &AnimationSpec,
) -> Option<StyleProps> {
    let first = keyframes.first()?;
    let last = keyframes.last()?;
    let (before, after) = if offset <= first.offset {
        (first, first)
    } else if offset >= last.offset {
        (last, last)
    } else {
        let mut before = first;
        let mut after = last;
        for pair in keyframes.windows(2) {
            if pair[0].offset <= offset && offset <= pair[1].offset {
                before = &pair[0];
                after = &pair[1];
                break;
            }
        }
        (before, after)
    };
    if before == after {
        let mut out = StyleProps::default();
        out.merge(&before.props);
        return Some(out);
    }
    // Frame-base: o mais próximo fornece os campos discretos e os
    // unilateralmente declarados; a interpolação sobrepõe os animados.
    let local_raw = (offset - before.offset) / (after.offset - before.offset).max(f32::EPSILON);
    let local = spec.easing.eval(local_raw);
    let mut out = if local >= 0.5 {
        after.props.clone()
    } else {
        before.props.clone()
    };
    for field in TWEENABLE_FIELDS {
        if let (Some(from), Some(to)) = (field.get(&before.props), field.get(&after.props)) {
            if let Some(value) = from.lerp(to, local) {
                field.set(&mut out, value);
            }
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_easings_endpoints_and_shapes() {
        for easing in [
            Easing::Linear,
            Easing::Ease,
            Easing::EaseIn,
            Easing::EaseOut,
            Easing::EaseInOut,
            Easing::CubicBezier(0.3, 0.9, 0.4, 1.0),
            Easing::Steps(4),
        ] {
            assert!((easing.eval(0.0) - 0.0).abs() < 1e-4, "{easing:?} em 0");
            assert!((easing.eval(1.0) - 1.0).abs() < 1e-4, "{easing:?} em 1");
        }
        // ease-out chega ao meio mais rápido que linear; ease-in ao contrário.
        assert!(Easing::EaseOut.eval(0.25) > 0.25);
        assert!(Easing::EaseIn.eval(0.25) < 0.25);
        assert_eq!(Easing::Steps(4).eval(0.5), 0.5);
        assert_eq!(Easing::Steps(4).eval(0.26), 0.25);
    }

    #[test]
    fn test_easing_parse() {
        assert_eq!(Easing::parse("linear"), Some(Easing::Linear));
        assert_eq!(Easing::parse("ease-out"), Some(Easing::EaseOut));
        assert_eq!(
            Easing::parse("cubic-bezier(0.2, 0.9, 0.3, 1)"),
            Some(Easing::CubicBezier(0.2, 0.9, 0.3, 1.0))
        );
        assert_eq!(Easing::parse("steps(6)"), Some(Easing::Steps(6)));
        assert_eq!(Easing::parse("bounce"), None);
        assert_eq!(Easing::parse("cubic-bezier(0, 0, 0)"), None);
    }

    #[test]
    fn test_tween_run_current_and_finish() {
        let mut run = TweenRun {
            field: StyleField::Opacity,
            from: TweenValue::Num(NumUnit::Px, 0.0),
            to: TweenValue::Num(NumUnit::Px, 1.0),
            easing: Easing::Linear,
            elapsed: 0.0,
            duration: 1.0,
            delay: 0.5,
            explicit: false,
            label: String::new(),
        };
        assert_eq!(
            run.current(),
            Some(TweenValue::Num(NumUnit::Px, 0.0)),
            "ainda no delay"
        );
        run.elapsed = 1.0; // 0.5s de animação efetiva
        assert!((run.current().unwrap().num_value() - 0.5).abs() < 1e-5);
        run.elapsed = 2.0;
        assert!(run.finished());
        assert_eq!(run.current(), Some(TweenValue::Num(NumUnit::Px, 1.0)));
        // Unidades diferentes não transicionam.
        let mixed = TweenRun {
            from: TweenValue::Num(NumUnit::Px, 0.0),
            to: TweenValue::Num(NumUnit::Percent, 100.0),
            ..run
        };
        assert_eq!(mixed.current(), None);
    }

    #[test]
    fn test_ui_transitions_start_from_shown_value() {
        let mut transitions = UiTransitions::default();
        // Sem alvo conhecido não há "from" — o diff da cascata semeia os
        // alvos ANTES do primeiro start (o estilo inicial não transita).
        assert!(!transitions.start(
            StyleField::Width,
            TweenValue::Num(NumUnit::Px, 100.0),
            Easing::Linear,
            1.0,
            0.0,
            false
        ));
        let target = TweenValue::Num(NumUnit::Px, 100.0);
        transitions.targets.insert(StyleField::Width, target);
        let new_target = TweenValue::Num(NumUnit::Px, 160.0);
        assert!(transitions.start(StyleField::Width, new_target, Easing::Linear, 1.0, 0.0, false));
        // O alvo avança para o novo valor (o diff da cascata compara com ele).
        assert_eq!(transitions.targets.get(&StyleField::Width), Some(&new_target));
        // Re-alvo a meio (elapsed 0.5 de 100→160): o novo from é o valor
        // EXIBIDO (130), não o alvo antigo — sem salto no meio do caminho.
        transitions.tweens[0].elapsed = 0.5;
        let new_target2 = TweenValue::Num(NumUnit::Px, 220.0);
        assert!(transitions.start(StyleField::Width, new_target2, Easing::Linear, 1.0, 0.0, false));
        assert_eq!(transitions.tweens.len(), 1, "o tween antigo foi substituído");
        let run = &transitions.tweens[0];
        assert!((run.from.num_value() - 130.0).abs() < 1e-5);
        // Unidades mistas: recusa e mantém o alvo antigo.
        let bad = TweenValue::Num(NumUnit::Percent, 50.0);
        assert!(!transitions.start(StyleField::Height, bad, Easing::Linear, 1.0, 0.0, false));
    }

    #[test]
    fn test_animation_offset_loops_and_alternates() {
        let spec = |iterations: Iterations, alternate: bool| AnimationSpec {
            name: "t".into(),
            duration: 1.0,
            easing: Easing::Linear,
            delay: 0.0,
            iterations,
            alternate,
            reverse: false,
            fill_forwards: false,
        };
        // Infinite: fract; alternate inverte nos ciclos ímpares.
        assert!(
            (animation_offset(&spec(Iterations::Infinite, false), 1.5).unwrap() - 0.5).abs()
                < 1e-6
        );
        assert!(
            (animation_offset(&spec(Iterations::Infinite, true), 1.25).unwrap() - 0.75).abs()
                < 1e-6
        );
        // Count: 3 iterações terminam a 3.0s.
        assert!(animation_offset(&spec(Iterations::Count(3.0), false), 2.5).is_some());
        assert!(animation_offset(&spec(Iterations::Count(3.0), false), 3.5).is_none());
        // Delay: nada antes do arranque.
        let mut delayed = spec(Iterations::Infinite, false);
        delayed.delay = 0.4;
        assert!(animation_offset(&delayed, 0.2).is_none());
        assert!(animation_offset(&delayed, 0.5).is_some());
    }

    #[test]
    fn test_sample_keyframes_interpolates_and_discrete_switches() {
        let frames = vec![
            Keyframe {
                offset: 0.0,
                props: super::super::style::parse_declarations(
                    "background: #000000; display: none",
                    "t",
                ),
            },
            Keyframe {
                offset: 1.0,
                props: super::super::style::parse_declarations(
                    "background: #ffffff; display: flex",
                    "t",
                ),
            },
        ];
        let spec = AnimationSpec {
            name: "t".into(),
            duration: 1.0,
            easing: Easing::Linear,
            delay: 0.0,
            iterations: Iterations::Count(1.0),
            alternate: false,
            reverse: false,
            fill_forwards: false,
        };
        let mid = sample_keyframes(&frames, 0.5, &spec).expect("meio");
        let mid_gray = mid.background.expect("background interpolado").to_srgba();
        assert!((mid_gray.red - 0.5).abs() < 1e-5);
        // Discreto troca ao meio — a 0.5 o frame "after" vence.
        assert_eq!(mid.display, Some(Display::Flex));
        let start = sample_keyframes(&frames, 0.0, &spec).expect("início");
        assert_eq!(
            start.background,
            super::super::style::parse_color("#000000"),
            "offset 0 = primeiro frame"
        );
    }
}
