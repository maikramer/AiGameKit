//! `<PostFxDebugToggle bindings="…">` — teclas que ligam/desligam efeitos de
//! pós-processo AO VIVO (port do plugin `postfx-toggle` do VibeGame).
//!
//! ```xml
//! <PostFxDebugToggle />                                  <!-- defaults Ctrl+1..6 -->
//! <PostFxDebugToggle bindings="Digit1:bloom, shift+f4:ssao, ctrl+l:lens" />
//! ```
//!
//! Cada tecla alterna o mesmo gate que o `viber.debug.postfx{…}` do bridge
//! força ([`crate::postfx::fx_runtime_toggle`]), e o `sync_postfx_gates` do
//! `postfx.rs` tira/repõe o componente na câmara no frame seguinte. Os
//! defaults usam Ctrl porque os dígitos nus já são a hotbar do preset RPG.

use bevy::prelude::*;

use crate::worldsys::EngineConfigs;

/// Bindings por omissão — a ordem do VibeGame (bloom, CA, vinheta…), com os
/// dois efeitos que o Viber não comuta ao vivo (AA, tonemapping) trocados por
/// SSAO/DoF, e Ctrl para não roubar a hotbar.
pub const DEFAULT_BINDINGS: &str =
    "ctrl+1:bloom, ctrl+2:ca, ctrl+3:vignette, ctrl+4:ssao, ctrl+5:dof, ctrl+6:volumetrics";

/// Uma tecla (com modificadores exatos) → um gate de [`crate::postfx::fx_off`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FxBinding {
    pub key: KeyCode,
    pub ctrl: bool,
    pub shift: bool,
    pub alt: bool,
    /// Key do gate (`BLOOM`, `SSAO`, `LENS`…).
    pub fx: &'static str,
    /// Texto da binding como o autor o escreveu (para o log).
    pub label: String,
}

/// Bindings ativas. O estado ON/OFF NÃO vive aqui: é o corte runtime
/// partilhado com o bridge ([`crate::postfx::fx_forced_off`]) — uma cópia
/// local ficava dessincronizada e a 1.ª tecla depois de um
/// `viber.debug.postfx{…}` não fazia nada.
#[derive(Debug, Clone, Default, Resource)]
pub struct PostFxToggleBindings {
    pub bindings: Vec<FxBinding>,
}

/// Alias do autor → key do gate. `Err` explica aliases reconhecidos mas que
/// não se comutam ao vivo.
pub fn fx_key_for_alias(alias: &str) -> Result<&'static str, String> {
    let norm: String = alias
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| c.to_ascii_lowercase())
        .collect();
    Ok(match norm.as_str() {
        "bloom" => "BLOOM",
        "ca" | "chromatic" | "chromaticaberration" => "CHROMATIC",
        "vignette" => "VIGNETTE",
        "ssao" | "ao" => "SSAO",
        "dof" | "depthoffield" => "DOF",
        "autoexposure" | "exposure" => "AUTOEXPOSURE",
        "contactshadows" => "CONTACT_SHADOWS",
        "cas" | "sharpen" => "CAS",
        "splittone" => "SPLITTONE",
        "aerial" | "aerialperspective" => "AERIAL",
        "volumetrics" | "volumetric" | "godrays" | "fog" => "VOLUMETRICS",
        "motionblur" => "MOTION_BLUR",
        "lens" => "LENS",
        "aa" | "taa" | "fxaa" | "tonemapping" => {
            return Err(format!(
                "'{alias}' não se comuta ao vivo no Viber (TAA/tonemapping são de arranque: VIBER_NO_TAA)"
            ));
        }
        _ => return Err(format!("efeito desconhecido '{alias}'")),
    })
}

/// `"ctrl+shift+Digit1"` → tecla + modificadores. Aceita os códigos do
/// VibeGame (`Digit1`, `KeyB`) e os nomes do `viber.key_pressed` (`1`, `b`,
/// `f4`, `numpad3`…).
pub fn parse_key_spec(spec: &str) -> Option<(KeyCode, bool, bool, bool)> {
    let mut parts: Vec<&str> = spec.split('+').map(str::trim).collect();
    // `ctrl++` → a própria tecla `+`.
    let key = if spec.trim_end().ends_with("++") {
        parts.truncate(parts.len().saturating_sub(2));
        "+"
    } else {
        parts.pop()?
    };
    let (mut ctrl, mut shift, mut alt) = (false, false, false);
    for modifier in parts {
        match modifier.to_ascii_lowercase().as_str() {
            "ctrl" | "control" => ctrl = true,
            "shift" => shift = true,
            "alt" | "option" => alt = true,
            _ => return None,
        }
    }
    let lower = key.to_ascii_lowercase();
    let bare = lower
        .strip_prefix("digit")
        .filter(|d| d.len() == 1)
        .or_else(|| lower.strip_prefix("key").filter(|k| k.len() == 1))
        .unwrap_or(key);
    let code = crate::luau::input::key_code_from_str(bare)?;
    Some((code, ctrl, shift, alt))
}

/// Faz o parse de `bindings="tecla:efeito, …"`. Entradas inválidas viram
/// warnings e as válidas ficam — uma gralha não custa as restantes teclas.
pub fn parse_bindings(text: &str) -> (Vec<FxBinding>, Vec<String>) {
    let mut bindings = Vec::new();
    let mut warnings = Vec::new();
    for entry in text.split(',').map(str::trim).filter(|e| !e.is_empty()) {
        let Some((key, alias)) = entry.rsplit_once(':') else {
            warnings.push(format!("binding '{entry}' sem ':' (esperado tecla:efeito)"));
            continue;
        };
        let Some((code, ctrl, shift, alt)) = parse_key_spec(key) else {
            warnings.push(format!("tecla desconhecida '{}' em '{entry}'", key.trim()));
            continue;
        };
        match fx_key_for_alias(alias.trim()) {
            Ok(fx) => bindings.push(FxBinding {
                key: code,
                ctrl,
                shift,
                alt,
                fx,
                label: entry.to_string(),
            }),
            Err(warning) => warnings.push(warning),
        }
    }
    (bindings, warnings)
}

/// Lê a tag do mundo (depois do spawn) e instala as bindings.
fn install_bindings(mut commands: Commands, configs: Option<Res<EngineConfigs>>) {
    let Some(tag) = configs.as_ref().and_then(|c| c.first("postfxdebugtoggle")) else {
        return;
    };
    if matches!(
        tag.attr("enabled").map(str::trim),
        Some("0" | "false" | "off")
    ) {
        return;
    }
    let text = tag.attr("bindings").unwrap_or(DEFAULT_BINDINGS);
    let (bindings, warnings) = parse_bindings(text);
    for warning in &warnings {
        warn!("postfx-toggle: {warning}");
    }
    info!(
        "postfx-toggle: {} tecla(s) — {}",
        bindings.len(),
        bindings
            .iter()
            .map(|b| b.label.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    );
    commands.insert_resource(PostFxToggleBindings { bindings });
}

fn toggle_on_keys(
    keys: Option<Res<ButtonInput<KeyCode>>>,
    menus: Option<Res<crate::menus::MenusOpen>>,
    toggles: Option<Res<PostFxToggleBindings>>,
) {
    let (Some(keys), Some(toggles)) = (keys, toggles) else {
        return;
    };
    if menus.is_some_and(|m| m.any()) {
        return;
    }
    let ctrl = keys.any_pressed([KeyCode::ControlLeft, KeyCode::ControlRight]);
    let shift = keys.any_pressed([KeyCode::ShiftLeft, KeyCode::ShiftRight]);
    let alt = keys.any_pressed([KeyCode::AltLeft, KeyCode::AltRight]);
    let pressed: Vec<&'static str> = toggles
        .bindings
        .iter()
        .filter(|b| b.ctrl == ctrl && b.shift == shift && b.alt == alt)
        .filter(|b| keys.just_pressed(b.key))
        .map(|b| b.fx)
        .collect();
    for fx in pressed {
        let now_off = !crate::postfx::fx_forced_off(fx);
        crate::postfx::fx_runtime_toggle(fx, !now_off);
        let state = if now_off {
            "OFF"
        } else if crate::postfx::fx_off(fx) {
            "ON — mas continua cortado (VIBER_NO_* ou tier de qualidade)"
        } else {
            "ON"
        };
        info!("postfx-toggle: {fx} {state}");
    }
}

/// Liga o `<PostFxDebugToggle>`; inerte em mundos sem a tag.
pub struct PostFxDebugTogglePlugin;

impl Plugin for PostFxDebugTogglePlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(
            Startup,
            install_bindings.after(crate::recipes::spawn::startup),
        )
        .add_systems(Update, toggle_on_keys);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_bindings_all_parse() {
        let (bindings, warnings) = parse_bindings(DEFAULT_BINDINGS);
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(bindings.len(), 6);
        assert!(bindings.iter().all(|b| b.ctrl && !b.shift && !b.alt));
        assert_eq!(bindings[0].key, KeyCode::Digit1);
        assert_eq!(bindings[0].fx, "BLOOM");
        assert_eq!(bindings[1].fx, "CHROMATIC");
    }

    #[test]
    fn test_vibegame_codes_are_accepted() {
        let (bindings, warnings) = parse_bindings("Digit1:bloom,KeyB:ssao,Numpad3:lens");
        assert!(warnings.is_empty(), "{warnings:?}");
        let keys: Vec<_> = bindings.iter().map(|b| b.key).collect();
        assert_eq!(keys, [KeyCode::Digit1, KeyCode::KeyB, KeyCode::Numpad3]);
        assert!(bindings.iter().all(|b| !b.ctrl));
    }

    #[test]
    fn test_modifiers_parse() {
        assert_eq!(
            parse_key_spec("Shift+Alt+F4"),
            Some((KeyCode::F4, false, true, true))
        );
        assert_eq!(
            parse_key_spec("ctrl++"),
            Some((KeyCode::Equal, true, false, false))
        );
        assert_eq!(parse_key_spec("hyper+1"), None);
    }

    #[test]
    fn test_bad_entries_warn_and_keep_the_rest() {
        let (bindings, warnings) =
            parse_bindings("Digit1:bloom, Digit4:aa, Digit5:tonemapping, wat:bloom, 2:sparkles, 3");
        assert_eq!(bindings.len(), 1);
        assert_eq!(warnings.len(), 5, "{warnings:?}");
        assert!(warnings[0].contains("ao vivo"));
    }

    #[test]
    fn test_aliases_are_case_and_dash_insensitive() {
        assert_eq!(fx_key_for_alias("Contact-Shadows"), Ok("CONTACT_SHADOWS"));
        assert_eq!(fx_key_for_alias("motion_blur"), Ok("MOTION_BLUR"));
        assert_eq!(fx_key_for_alias("ChromaticAberration"), Ok("CHROMATIC"));
    }

    #[test]
    fn test_key_press_toggles_gate_off_then_back() {
        let mut app = App::new();
        app.init_resource::<ButtonInput<KeyCode>>()
            .insert_resource(PostFxToggleBindings {
                bindings: parse_bindings("f4:splittone").0,
            })
            .add_systems(Update, toggle_on_keys);
        let press = |app: &mut App| {
            let mut keys = app.world_mut().resource_mut::<ButtonInput<KeyCode>>();
            keys.release(KeyCode::F4);
            keys.clear();
            keys.press(KeyCode::F4);
            app.update();
        };
        press(&mut app);
        assert!(crate::postfx::fx_off("SPLITTONE"), "1.º toque desliga");
        press(&mut app);
        assert!(
            std::env::var_os("VIBER_NO_SPLITTONE").is_some() || !crate::postfx::fx_off("SPLITTONE"),
            "2.º toque repõe"
        );
    }

    /// O bridge desligou o efeito: a 1.ª tecla tem de o LIGAR (antes o toggle
    /// só via o seu próprio registo e o 1.º toque não fazia nada).
    #[test]
    fn test_key_press_follows_the_bridge_state() {
        let mut app = App::new();
        app.init_resource::<ButtonInput<KeyCode>>()
            .insert_resource(PostFxToggleBindings {
                bindings: parse_bindings("f7:aerial").0,
            })
            .add_systems(Update, toggle_on_keys);
        crate::postfx::fx_runtime_toggle("AERIAL", false);
        app.world_mut()
            .resource_mut::<ButtonInput<KeyCode>>()
            .press(KeyCode::F7);
        app.update();
        assert!(!crate::postfx::fx_forced_off("AERIAL"), "1.º toque repõe");
    }
}
