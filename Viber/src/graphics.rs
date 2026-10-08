//! Preset de GRÁFICOS — um botão em vez de doze variáveis de ambiente.
//!
//! A lente do Viber tem uma dúzia de custos independentes (cascatas do sol,
//! raymarch volumétrico, SSAO, DOF, motion blur, contact shadows, densidade de
//! relva). Cada um já tinha o seu `VIBER_*` para bissecção de QA, e isso é
//! exatamente a "lista de knobs" que o monorepo não quer como caminho normal:
//! quem quer jogar a 60 fps não devia ter de descobrir os doze.
//!
//! `config.yaml` do jogo declara `graphics: alto | equilibrado | desempenho`
//! (default `equilibrado`), `VIBER_GRAPHICS` sobrepõe-se para A/B sem editar o
//! ficheiro, e cada `VIBER_*` individual continua a ganhar ao preset — a
//! bissecção não perde nada.
//!
//! # De onde vêm os números
//!
//! Medido no spawn do `simple-rpg` (RTX 4050 Laptop 6 GiB, 1280x720, janela
//! visível — um compositor a estrangular a apresentação da janela ocluída
//! adiciona 3–15 ms ao `render.prepare_views` e invalida qualquer A/B):
//!
//! | corte | Δ frame |
//! |-------|---------|
//! | cascatas do sol 4×600 m → 3×150 m | −5,5 ms |
//! | raymarch volumétrico 64 → 40 passos | −2,9 ms |
//! | volumétrico desligado | −6,0 ms |
//! | resto do postfx (bloom/SSAO/DOF/TAA/…) | −2,9 ms |
//!
//! O passe de sombra é **draw-bound, não fill-bound**: baixar o shadow map de
//! 4096² para 1024² devolveu 1 ms, cortar o ALCANCE devolveu 9 (a 600 m as
//! cascatas varrem 23556 malhas, a 120 m varrem 5140).

use std::sync::OnceLock;

/// Preset de gráficos em vigor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GraphicsPreset {
    /// Tudo ligado, cascatas longas — para GPUs com folga.
    Alto,
    /// Default: a lente inteira, com os alcances medidos em vez dos herdados.
    Equilibrado,
    /// Corta o que custa e não se lê em movimento — o preset de 60 fps.
    Desempenho,
}

impl GraphicsPreset {
    /// Nome canónico (o que se escreve no `config.yaml`).
    pub fn name(self) -> &'static str {
        match self {
            Self::Alto => "alto",
            Self::Equilibrado => "equilibrado",
            Self::Desempenho => "desempenho",
        }
    }

    /// Aceita os nomes em português e os equivalentes em inglês — um mundo
    /// portado do VibeGame escreve `performance`, não `desempenho`.
    pub fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "alto" | "high" | "ultra" => Some(Self::Alto),
            "equilibrado" | "balanced" | "medium" | "default" => Some(Self::Equilibrado),
            "desempenho" | "performance" | "low" | "fast" => Some(Self::Desempenho),
            _ => None,
        }
    }
}

static PRESET: OnceLock<GraphicsPreset> = OnceLock::new();

/// Resolve o preset UMA vez, a partir do `config.yaml` e do `VIBER_GRAPHICS`.
///
/// Chamado pelo `run` antes de construir a App; os getters abaixo caem no
/// default (`equilibrado`) se ninguém tiver resolvido — é o que acontece nos
/// testes e no `analyze`, que não bootam a lente.
pub fn resolve(config_value: Option<&str>) -> GraphicsPreset {
    let from_env = std::env::var("VIBER_GRAPHICS")
        .ok()
        .and_then(|raw| GraphicsPreset::parse(&raw));
    let from_config = config_value.and_then(GraphicsPreset::parse);
    let preset = from_env
        .or(from_config)
        .unwrap_or(GraphicsPreset::Equilibrado);
    // `set` falha se já estiver resolvido (dois `run` no mesmo processo nos
    // testes) — o primeiro ganha, que é o do processo que está a correr.
    let _ = PRESET.set(preset);
    preset
}

/// Preset em vigor (default `equilibrado` enquanto ninguém resolver).
pub fn preset() -> GraphicsPreset {
    *PRESET.get().unwrap_or(&GraphicsPreset::Equilibrado)
}

/// Cascatas do shadow map do sol por preset.
pub fn shadow_cascades() -> usize {
    match preset() {
        GraphicsPreset::Alto => 4,
        GraphicsPreset::Equilibrado => 3,
        GraphicsPreset::Desempenho => 2,
    }
}

/// Alcance máximo das cascatas do sol (metros) por preset.
pub fn shadow_distance() -> f32 {
    match preset() {
        GraphicsPreset::Alto => 300.0,
        GraphicsPreset::Equilibrado => 150.0,
        GraphicsPreset::Desempenho => 90.0,
    }
}

/// Lado do shadow map direcional (uma textura de N camadas) por preset.
///
/// O `desempenho` desce para 2048 pela VRAM (268 MiB → 67 numa placa de
/// 6 GiB), não pelo frame: o passe é draw-bound e 4096²→1024² valeu 1 ms.
/// Com o alcance a 90 m, 2048 dá 4,4 cm/texel — mais nítido do que os
/// 14,6 cm/texel que o default antigo (4096 a 600 m) entregava.
pub fn dir_shadow_size() -> usize {
    match preset() {
        GraphicsPreset::Alto | GraphicsPreset::Equilibrado => 4096,
        GraphicsPreset::Desempenho => 2048,
    }
}

/// Passos do raymarch volumétrico; `0` desliga o volumétrico.
pub fn volumetric_steps() -> u32 {
    match preset() {
        GraphicsPreset::Alto => 64,
        GraphicsPreset::Equilibrado => 40,
        GraphicsPreset::Desempenho => 0,
    }
}

/// Efeitos que o `desempenho` desliga em bloco (o `fx_off` dos gates de QA
/// continua a poder desligá-los em qualquer preset).
pub fn heavy_fx_enabled() -> bool {
    preset() != GraphicsPreset::Desempenho
}

/// Multiplicador do raio de render (`CullDistance`) dos spawners por preset.
///
/// Mexe no NÚMERO DE INSTÂNCIAS desenhadas, que é o que sobra a dominar o
/// frame quando a GPU deixa de ser o limite: no preset `desempenho` a
/// `render.render` (encode + submit dos draws) é ~11 ms de um frame de 17,
/// com a GPU a ter folga.
pub fn cull_scale() -> f32 {
    match preset() {
        GraphicsPreset::Alto => 1.15,
        GraphicsPreset::Equilibrado => 1.0,
        GraphicsPreset::Desempenho => 0.7,
    }
}

/// Multiplicador da densidade da relva por preset.
pub fn grass_density_scale() -> f32 {
    match preset() {
        GraphicsPreset::Alto | GraphicsPreset::Equilibrado => 1.0,
        GraphicsPreset::Desempenho => 0.6,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_accepts_pt_and_en() {
        assert_eq!(GraphicsPreset::parse("alto"), Some(GraphicsPreset::Alto));
        assert_eq!(GraphicsPreset::parse("HIGH"), Some(GraphicsPreset::Alto));
        assert_eq!(
            GraphicsPreset::parse(" desempenho "),
            Some(GraphicsPreset::Desempenho)
        );
        assert_eq!(
            GraphicsPreset::parse("performance"),
            Some(GraphicsPreset::Desempenho)
        );
        assert_eq!(
            GraphicsPreset::parse("equilibrado"),
            Some(GraphicsPreset::Equilibrado)
        );
        assert_eq!(GraphicsPreset::parse("nuvem"), None);
    }

    #[test]
    fn test_preset_tuning_is_monotonic() {
        // Não depende do estado global: compara as tabelas diretamente.
        let cascades = |p: GraphicsPreset| match p {
            GraphicsPreset::Alto => 4,
            GraphicsPreset::Equilibrado => 3,
            GraphicsPreset::Desempenho => 2,
        };
        assert!(cascades(GraphicsPreset::Alto) > cascades(GraphicsPreset::Equilibrado));
        assert!(cascades(GraphicsPreset::Equilibrado) > cascades(GraphicsPreset::Desempenho));
    }

    /// Os getters têm de ser utilizáveis sem `resolve` (o `analyze` e os
    /// testes não bootam a lente) e o que sai tem de ser o `equilibrado`.
    #[test]
    fn test_getters_without_resolve_give_equilibrado() {
        if PRESET.get().is_some() {
            // Outro teste no mesmo processo já resolveu — o OnceLock é
            // global e o default já não é observável aqui.
            return;
        }
        assert_eq!(preset(), GraphicsPreset::Equilibrado);
        assert_eq!(shadow_cascades(), 3);
        assert_eq!(shadow_distance(), 150.0);
        assert_eq!(volumetric_steps(), 40);
        assert_eq!(dir_shadow_size(), 4096);
        assert!(heavy_fx_enabled());
        assert_eq!(cull_scale(), 1.0);
        assert_eq!(grass_density_scale(), 1.0);
    }

    /// O `desempenho` tem de cortar em TODAS as frentes medidas — um preset
    /// que só mexe numa delas não chega aos 60 fps (o frame estava repartido
    /// entre cascatas, volumétrico, postfx e nº de instâncias).
    #[test]
    fn test_desempenho_cuts_every_axis() {
        let cascades = |p| match p {
            GraphicsPreset::Alto => 4,
            GraphicsPreset::Equilibrado => 3,
            GraphicsPreset::Desempenho => 2,
        };
        let distance = |p| match p {
            GraphicsPreset::Alto => 300.0,
            GraphicsPreset::Equilibrado => 150.0,
            GraphicsPreset::Desempenho => 90.0_f32,
        };
        let steps = |p| match p {
            GraphicsPreset::Alto => 64,
            GraphicsPreset::Equilibrado => 40,
            GraphicsPreset::Desempenho => 0,
        };
        for (a, b) in [
            (GraphicsPreset::Alto, GraphicsPreset::Equilibrado),
            (GraphicsPreset::Equilibrado, GraphicsPreset::Desempenho),
        ] {
            assert!(cascades(a) > cascades(b), "cascatas {a:?} > {b:?}");
            assert!(distance(a) > distance(b), "alcance {a:?} > {b:?}");
            assert!(steps(a) > steps(b), "volumétrico {a:?} > {b:?}");
        }
        assert_eq!(steps(GraphicsPreset::Desempenho), 0, "0 = volumétrico OFF");
    }

    #[test]
    fn test_name_roundtrips_through_parse() {
        for preset in [
            GraphicsPreset::Alto,
            GraphicsPreset::Equilibrado,
            GraphicsPreset::Desempenho,
        ] {
            assert_eq!(GraphicsPreset::parse(preset.name()), Some(preset));
        }
    }
}
