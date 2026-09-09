//! Scaffold de projectos de mundo Viber (`viber create <name>`).

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};

/// Mundo inicial gerado por `viber create` — plano, primitivas, luz e câmara.
pub const WORLD_XML_TEMPLATE: &str = r##"<?xml version="1.0"?>
<!-- Mundo Viber: plano, primitivas, luz e câmara orbital. Edite livremente. -->
<world clear-color="#87ceeb">
  <AmbientLight brightness="300" />
  <Entity name="ground">
    <Plane half-size="12 12" base-color="#4a7d3a" roughness="0.9" />
  </Entity>
  <Cuboid half-size="0.5 0.5 0.5" translation="0 0.5 0" base-color="#c0764a" metallic="0.1" roughness="0.5" />
  <Sphere radius="0.6" translation="2 0.6 -1" base-color="#4a90d9" metallic="0.4" roughness="0.25" />
  <Cylinder radius="0.4" half-height="0.8" translation="-2 0.8 -1" base-color="#7bc043" />
  <PointLight translation="4 6 4" intensity="1500" color="#fff4e0" shadows="true" />
  <OrbitCamera target="ground" distance="10" pitch="22" />
</world>
"##;

/// `config.yaml` gerado por `viber create` — o contrato de paths do jogo
/// com a engine (docs/ASSETS.md). Jogo standalone: sem roots extra (a pasta
/// do jogo é a única root); os dirs canónicos do pool partilhado vêm
/// materializados, prontos a apontar quando o jogo adotar assets.
pub const CONFIG_YAML_TEMPLATE: &str = r#"# config.yaml — contrato de paths do jogo com a engine (docs/ASSETS.md).
# Relativos a ESTA pasta. Obrigatório: o run/analyze não arranca sem ele.
title: # opcional — título da janela (vazio = nome do world.xml)

assets:
  # Roots extra, por ordem, DEPOIS da pasta do jogo (a 1.ª root é sempre a
  # pasta do próprio jogo — overrides e shaders/ especializados vivem lá).
  # Exemplo, partilhando o pool do monorepo:
  #   roots:
  #     - ../shared-assets/public
  roots: []
  bgm_dir: assets/audio/bgm              # <MusicLayer layer="x"> → {bgm_dir}/x.ogg
  sfx_dir: assets/audio/sfx              # clips da engine → {sfx_dir}/…
  terrain_textures_dir: assets/textures  # layers de terreno → {dir}/{alias}/…

game:
  scripts_dir: scripts                   # script="x.lua" + hot-reload

save:
  dir: ~/.local/share/viber              # {dir}/{mundo}.save.json (~ ok)
"#;

/// Cria `<target_dir>/{world.xml,config.yaml}` a partir dos templates.
///
/// Falha se `target_dir` já existir — nunca sobrescreve. Devolve o caminho do
/// `world.xml` criado.
pub fn create_world_project(target_dir: &Path) -> Result<PathBuf> {
    if target_dir.exists() {
        bail!(
            "{} already exists — choose another name or remove the folder",
            target_dir.display()
        );
    }
    fs::create_dir_all(target_dir).with_context(|| format!("creating {}", target_dir.display()))?;
    let world_path = target_dir.join("world.xml");
    fs::write(&world_path, WORLD_XML_TEMPLATE)
        .with_context(|| format!("writing {}", world_path.display()))?;
    let config_path = target_dir.join(crate::config::CONFIG_FILE);
    fs::write(&config_path, CONFIG_YAML_TEMPLATE)
        .with_context(|| format!("writing {}", config_path.display()))?;
    Ok(world_path)
}
