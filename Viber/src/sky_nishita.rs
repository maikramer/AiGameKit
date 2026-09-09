//! Modelo de atmosfera Nishita — scattering físico Rayleigh + Mie.
//!
//! Portado de `bevy_atmosphere` v0.13.0, © JonahPlusPlus et al., Apache-2.0
//! (https://github.com/JonahPlusPlus/bevy_atmosphere, ficheiros
//! `src/shaders/nishita.wgsl` + `src/collection/nishita.rs`), para Rust puro.
//! Os defaults dos coeficientes são os do crate; os multiplicadores do mundo
//! (`<Sky rayleigh … nishita-mie … sun-intensity …>` + ganho de normalização)
//! entram por [`NishitaModel`].
//!
//! Dois consumidores:
//! 1. O IBL CPU ([`crate::ibl`]) — pinta o cubemap de ambiente com o MESMO
//!    modelo que o domo desenha, para os reflexos PBR baterem com o céu.
//! 2. A afinação das consts do WGSL (`src/sky.wgsl`, ramo `CFG_SKY_MODEL`):
//!    o shader porta o mesmo algoritmo 1:1, e os testes deste módulo medem a
//!    escala da radiância para calibrar `NISHITA_GAIN` sem arrancar o jogo.

use bevy::math::Vec3;

/// Raio do planeta (m) — o "chão" do mundo plano é tratado como a superfície
/// de uma esfera gigante: a linha do horizonte em `dir.y == 0` coincide com o
/// horizonte real do mundo, e os raios abaixo dela batem no planeta de
/// imediato (aí o domo usa o fallback de horizonte, como no ramo analítico).
pub const PLANET_RADIUS: f32 = 6_371_000.0;
/// Raio da atmosfera (m) — topo a 100 km, default do crate.
pub const ATMOSPHERE_RADIUS: f32 = 6_471_000.0;
/// Scale height do espalhamento Rayleigh (m).
pub const RAYLEIGH_SCALE_HEIGHT: f32 = 8_000.0;
/// Scale height do espalhamento Mie (m).
pub const MIE_SCALE_HEIGHT: f32 = 1_200.0;
/// Coeficiente Rayleigh base (1/m, RGB) — default do crate.
pub const RAYLEIGH_BASE: [f32; 3] = [5.5e-6, 13.0e-6, 22.4e-6];
/// Coeficiente Mie base (1/m) — default do crate.
pub const MIE_BASE: f32 = 21e-6;
/// Intensidade solar base do modelo (o attr `sun-intensity` do XML é um
/// multiplicador sobre este valor, igual ao ramo analítico).
pub const SUN_INTENSITY_BASE: f32 = 22.0;
/// Passos do raio primário (amostras ao longo da vista).
pub const ISTEPS: usize = 16;
/// Passos do raio secundário (luz para o sol, por amostra).
pub const JSTEPS: usize = 8;

/// Ganho que mapeia a radiância física para a escala da paleta do domo
/// (a mesma do `AtmosphereState`/fog). Calibrado pelo teste
/// `imprime_escalas_para_afinar_gain`: com 1.0 o zénite do meio-dia saía
/// ~6× acima da paleta analítica; 0.2 deixa-o em ~[0.35, 0.40, 0.44] —
/// mesma ordem do céu atual. Não é uma constante do mundo: vive no template
/// WGSL e aqui, para o IBL bater com o domo (o `render_world_shader` emite
/// este valor, não uma cópia independente).
pub const NISHITA_GAIN: f32 = 0.2;
/// Piso de airglow noturno sobre o raymarch físico (que é ~negro de noite):
/// `paleta(horizonte→zénite) × night × NIGHT_FLOOR`. Partilhado entre o
/// WGSL (`CFG_NISHITA_NIGHT_FLOOR`) e o IBL CPU para os dois baterem.
pub const NIGHT_FLOOR: f32 = 0.06;

/// Instância do modelo com os coeficientes já escalados pelo mundo.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NishitaModel {
    /// Coeficiente Rayleigh (1/m, RGB), já multiplicado pelo attr `rayleigh`.
    pub rayleigh: [f32; 3],
    /// Coeficiente Mie (1/m), já multiplicado pelo attr `nishita-mie`.
    pub mie: f32,
    /// Anisotropia da fase Mie (attr `mie-directional-g`).
    pub g: f32,
    /// Multiplicador de intensidade do sol (attr `sun-intensity`).
    pub sun_intensity: f32,
    /// Ganho de normalização (constante [`NISHITA_GAIN`]).
    pub gain: f32,
}

impl Default for NishitaModel {
    fn default() -> Self {
        Self {
            rayleigh: RAYLEIGH_BASE,
            mie: MIE_BASE,
            g: 0.758,
            sun_intensity: 2.6,
            gain: NISHITA_GAIN,
        }
    }
}

impl NishitaModel {
    /// Modelo a partir dos multiplicadores do `<Sky>`.
    pub fn from_multipliers(rayleigh: f32, mie: f32, g: f32, sun_intensity: f32) -> Self {
        Self {
            rayleigh: [
                RAYLEIGH_BASE[0] * rayleigh,
                RAYLEIGH_BASE[1] * rayleigh,
                RAYLEIGH_BASE[2] * rayleigh,
            ],
            mie: MIE_BASE * mie,
            g: g.clamp(-0.99, 0.99),
            sun_intensity,
            gain: NISHITA_GAIN,
        }
    }
}

/// Interseção raio-esfera com a esfera centrada na origem; devolve (perto,
/// longe). Sem interseção real → `(1e5, -1e5)` (contrato do shader original).
/// Valores negativos significam "atrás da origem do raio" — o raymarch usa
/// SÓ o SPAN `far - near`, que fica correto mesmo quando as duas raízes são
/// negativas (raio a apontar para fora da atmosfera).
fn ray_sphere_intersection(rd: Vec3, r0: Vec3, sr: f32) -> (f32, f32) {
    let a = rd.dot(rd);
    let b = 2.0 * rd.dot(r0);
    let c = r0.dot(r0) - (sr * sr);
    let d = (b * b) - (4.0 * a * c);
    if d < 0.0 {
        (1e5, -1e5)
    } else {
        ((-b - d.sqrt()) / (2.0 * a), (-b + d.sqrt()) / (2.0 * a))
    }
}

/// Radiância do céu numa direção (mundo, partindo da câmara), linear e na
/// escala do domo — port 1:1 de `render_nishita` do crate, em f32, com a
/// MESMA ordem de somas do WGSL (o IBL CPU e o domo GPU batem).
pub fn radiance(dir: Vec3, sun: Vec3, cam_height: f32, model: &NishitaModel) -> [f32; 3] {
    let r = dir.normalize_or_zero();
    if r == Vec3::ZERO {
        return [0.0; 3];
    }
    // Raios ao nível do horizonte ou abaixo param no planeta — o domo e o
    // IBL usam aí o fallback de horizonte (mesmo contrato do ramo
    // analítico). O early-out também mantém o raymarch FORA do subsolo
    // profundo, onde exp() estoura para inf e inf·0 = NaN (o skybox do
    // crate nunca vê esses raios — a origem dele está a 1 km de altitude;
    // a nossa câmara anda a ~1 m do chão).
    if r.y <= 0.0 {
        return [0.0; 3];
    }
    let p_sun = sun.normalize_or_zero();
    if p_sun == Vec3::ZERO {
        return [0.0; 3];
    }
    let r0 = Vec3::new(0.0, PLANET_RADIUS + cam_height, 0.0);
    let k_rlh = Vec3::from_array(model.rayleigh);
    let k_mie = model.mie;
    let g = model.g.clamp(0.0, 0.96);
    let i_sun = SUN_INTENSITY_BASE * model.sun_intensity.max(0.0) * model.gain;

    let mut p = ray_sphere_intersection(r, r0, ATMOSPHERE_RADIUS);
    if p.0 > p.1 {
        return [0.0; 3];
    }
    // O raio primário pára no planeta (chão); para raios acima do horizonte
    // a raiz do planeta fica atrás da câmara e o span continua correto.
    p.1 = p.1.min(ray_sphere_intersection(r, r0, PLANET_RADIUS).0);
    let i_step_size = (p.1 - p.0) / ISTEPS as f32;

    let mut i_depth = 0.0f32;
    let mut total_rlh = Vec3::ZERO;
    let mut total_mie = Vec3::ZERO;
    let mut i_od_rlh = 0.0f32;
    let mut i_od_mie = 0.0f32;

    let mu = r.dot(p_sun);
    let mumu = mu * mu;
    let gg = g * g;
    let p_rlh = 3.0 / (16.0 * std::f32::consts::PI) * (1.0 + mumu);
    let p_mie = 3.0 / (8.0 * std::f32::consts::PI) * ((1.0 - gg) * (mumu + 1.0))
        / ((1.0 + gg - 2.0 * mu * g).powf(1.5) * (2.0 + gg));

    for _ in 0..ISTEPS {
        let i_pos = r0 + r * (i_depth + i_step_size * 0.5);
        let i_height = i_pos.length() - PLANET_RADIUS;
        let od_step_rlh = (-i_height / RAYLEIGH_SCALE_HEIGHT).exp() * i_step_size;
        let od_step_mie = (-i_height / MIE_SCALE_HEIGHT).exp() * i_step_size;
        i_od_rlh += od_step_rlh;
        i_od_mie += od_step_mie;

        let j_step_size = ray_sphere_intersection(p_sun, i_pos, ATMOSPHERE_RADIUS).1
            / JSTEPS as f32;
        let mut j_depth = 0.0f32;
        let mut j_od_rlh = 0.0f32;
        let mut j_od_mie = 0.0f32;
        for _ in 0..JSTEPS {
            let j_pos = i_pos + p_sun * (j_depth + j_step_size * 0.5);
            let j_height = j_pos.length() - PLANET_RADIUS;
            j_od_rlh += (-j_height / RAYLEIGH_SCALE_HEIGHT).exp() * j_step_size;
            j_od_mie += (-j_height / MIE_SCALE_HEIGHT).exp() * j_step_size;
            j_depth += j_step_size;
        }

        // WGSL: attn = exp(-(k_mie * (i_od_mie + j_od_mie) + k_rlh * (i_od_rlh + j_od_rlh)))
        // — k_rlh é vec3, o produto com o escalar é componente a componente e
        // o termo escalar soma-se a cada componente: attn é um VEC3.
        let od_rlh = i_od_rlh + j_od_rlh;
        let od_mie = i_od_mie + j_od_mie;
        let attn = (-(k_mie * od_mie + k_rlh * od_rlh)).exp();

        total_rlh += od_step_rlh * attn;
        total_mie += od_step_mie * attn;
        i_depth += i_step_size;
    }

    let color = i_sun * (p_rlh * k_rlh * total_rlh + p_mie * k_mie * total_mie);
    color.to_array()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn noon_sun() -> Vec3 {
        Vec3::new(0.0, 1.0, 0.0)
    }

    #[test]
    fn zenite_do_meio_dia_e_azulado_e_finito() {
        let model = NishitaModel::default();
        let color = radiance(Vec3::Y, noon_sun(), 1.0, &model);
        for v in color {
            assert!(v.is_finite(), "radiância não finita: {color:?}");
        }
        assert!(color[2] > color[0], "zénite do meio-dia deve ser azul: {color:?}");
    }

    #[test]
    fn direcao_do_sol_domina_sobre_direcoes_afastadas() {
        // Com o sol no zénite, a direção do sol É o zénite (mu = 1): a fase
        // Rayleigh/Mie pica aí. Uma direção ~28° afastada (mu ≈ 0.83) tem de
        // ficar bem abaixo — a mesma física que põe o glow à volta do disco.
        let model = NishitaModel::default();
        let at_sun = radiance(Vec3::Y, noon_sun(), 1.0, &model);
        let off_axis = radiance(Vec3::new(0.2, 0.8, 0.5).normalize(), noon_sun(), 1.0, &model);
        let sun_sum: f32 = at_sun.iter().sum();
        let off_sum: f32 = off_axis.iter().sum();
        assert!(
            sun_sum > off_sum * 2.0,
            "a direção do sol domina: sol={sun_sum} afastado={off_sum}"
        );
    }

    #[test]
    fn sol_abaixo_do_horizonte_escurece_o_ceu() {
        let model = NishitaModel::default();
        let color = radiance(Vec3::Y, Vec3::new(0.0, -1.0, 0.0), 1.0, &model);
        assert!(
            color.iter().all(|v| *v < 1e-4),
            "sol abaixo do horizonte = céu quase negro: {color:?}"
        );
    }

    #[test]
    fn por_do_sol_esquenta_o_horizonte_na_direcao_do_sol() {
        let model = NishitaModel::default();
        let sun = Vec3::new(0.2, 0.02, 0.98).normalize();
        let toward = Vec3::new(0.2, 0.06, 0.98).normalize();
        let away = Vec3::new(-0.2, 0.06, -0.98).normalize();
        let warm = radiance(toward, sun, 1.0, &model);
        let cool = radiance(away, sun, 1.0, &model);
        assert!(
            warm[0] > warm[2],
            "horizonte na direção do sol poente deve puxar ao vermelho: {warm:?}"
        );
        assert!(
            warm[0] + warm[1] + warm[2] > cool[0] + cool[1] + cool[2],
            "o lado do sol poente é o mais luminoso: warm={warm:?} cool={cool:?}"
        );
    }

    #[test]
    fn abaixo_do_horizonte_bate_no_planeta_e_nao_vaza() {
        let model = NishitaModel::default();
        let down = Vec3::new(0.0, -0.5, 0.0).normalize();
        let color = radiance(down, noon_sun(), 1.0, &model);
        assert!(
            color.iter().all(|v| *v < 1e-3),
            "raios abaixo do horizonte param no planeta: {color:?}"
        );
    }

    #[test]
    fn determinismo_bit_a_bit() {
        let model = NishitaModel::default();
        let dir = Vec3::new(0.31, 0.72, -0.55).normalize();
        let a = radiance(dir, noon_sun(), 1.0, &model);
        let b = radiance(dir, noon_sun(), 1.0, &model);
        assert_eq!(a, b);
    }

    /// Mede a escala da radiância para calibrar [`NISHITA_GAIN`] sem arrancar
    /// o jogo. Correr com `--ignored --nocapture` depois de mexer nos
    /// coeficientes e fixar o ganho.
    #[test]
    #[ignore = "ferramenta de calibração manual do ganho"]
    fn imprime_escalas_para_afinar_gain() {
        let model = NishitaModel {
            gain: 1.0,
            ..Default::default()
        };
        let print = |label: &str, dir: Vec3, sun: Vec3| {
            let c = radiance(dir, sun, 1.0, &model);
            println!("{label}: {c:?} (soma {})", c.iter().sum::<f32>());
        };
        print("zénite meio-dia", Vec3::Y, noon_sun());
        print(
            "horizonte contra-sol meio-dia",
            Vec3::new(0.0, 0.0, 1.0),
            noon_sun(),
        );
        let sunset = Vec3::new(0.2, 0.02, 0.98).normalize();
        print(
            "horizonte na direção do poente",
            Vec3::new(0.2, 0.04, 0.98).normalize(),
            sunset,
        );
        print("zénite ao pôr do sol", Vec3::Y, sunset);
    }
}
