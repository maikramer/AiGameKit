//! Scaffold de projectos de mundo Viber (`viber create <name>`).

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};

/// Mundo inicial gerado por `viber create` — ESTÁTICO desde o primeiro minuto: terreno
/// plano editável (`world.data/terrain.ahgt`, pincéis do editor / `viber terrain brush`),
/// primitivas, luz e câmara. Edita-se com `viber scene`, `viber tool` ou o ViberEditor.
pub const WORLD_XML_TEMPLATE: &str = r##"<?xml version="1.0"?>
<world clear-color="#87ceeb" static="true">
  <AmbientLight brightness="300" />
  <DirectionalLight direction="-0.5 -1 -0.3" illuminance="20000" shadows="true" />
  <Terrain name="ground" world-size="128" max-height="20" heightmap="world.data/terrain.ahgt" base-color="#4a7d3a" />
  <Cuboid name="crate" half-size="0.5 0.5 0.5" translation="0 0.5 0" base-color="#c0764a" metallic="0.1" roughness="0.5" />
  <Sphere name="ball" radius="0.6" translation="2 0.6 -1" base-color="#4a90d9" metallic="0.4" roughness="0.25" />
  <Cylinder name="pillar" radius="0.4" half-height="0.8" translation="-2 0.8 -1" base-color="#7bc043" />
  <PointLight translation="4 6 4" intensity="1500" color="#fff4e0" shadows="true" />
  <OrbitCamera target="crate" distance="10" pitch="22" />
</world>
"##;

/// Lado (amostras) do heightmap plano do mundo inicial.
const STARTER_TERRAIN_SAMPLES: usize = 257;

/// `config.yaml` gerado por `viber create` — o contrato de paths do jogo
/// com a engine (docs/ASSETS.md). Jogo standalone: sem roots extra (a pasta
/// do jogo é a única root); os dirs canónicos do pool partilhado vêm
/// materializados, prontos a apontar quando o jogo adotar assets.
pub const CONFIG_YAML_TEMPLATE: &str = r#"# config.yaml — contrato de paths do jogo com a engine (docs/ASSETS.md).
# Relativos a ESTA pasta. Obrigatório: o run/analyze não arranca sem ele.
title: # opcional — título da janela (vazio = nome do world.xml)

# DLSS (upscaling NVIDIA, requer binário construído com --features dlss):
# off | auto (default) | dlaa | quality | balanced | performance | ultra
# dlss: quality

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

/// Cria `<target_dir>/{world.xml,config.yaml,world.data/terrain.ahgt}` a partir dos
/// templates.
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
    // O terreno plano do mundo inicial (cota 0): dados binários na pasta de dados do mundo.
    let ahgt = crate::bake::data_dir(&world_path).join(crate::bake::BAKE_AHGT);
    fs::create_dir_all(ahgt.parent().expect("data dir"))
        .with_context(|| format!("creating {}", ahgt.display()))?;
    let flat = crate::terrain::heightmap::HeightMapU16::filled(
        STARTER_TERRAIN_SAMPLES,
        STARTER_TERRAIN_SAMPLES,
        0,
    );
    let meta = crate::terrain::heightmap::AhgtMeta {
        world_size: Some(128.0),
        max_height: Some(20.0),
        biomes: Default::default(),
    };
    fs::write(&ahgt, flat.to_ahgt(&meta)?)
        .with_context(|| format!("writing {}", ahgt.display()))?;
    let config_path = target_dir.join(crate::config::CONFIG_FILE);
    fs::write(&config_path, CONFIG_YAML_TEMPLATE)
        .with_context(|| format!("writing {}", config_path.display()))?;
    Ok(world_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// O mundo criado já é estático e carrega: parse limpo + heightmap plano legível.
    #[test]
    fn created_world_is_static_and_loads() {
        let tmp = tempfile::tempdir().unwrap();
        let world = create_world_project(&tmp.path().join("demo")).unwrap();
        let loaded = crate::xml::include::load_world(&world).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        assert!(parsed.static_world);
        assert!(parsed.skipped_tags.is_empty(), "{:?}", parsed.skipped_tags);
        let bytes = fs::read(tmp.path().join("demo/world.data/terrain.ahgt")).unwrap();
        let (map, meta) = crate::terrain::heightmap::HeightMapU16::from_ahgt(&bytes).unwrap();
        assert_eq!(map.width, STARTER_TERRAIN_SAMPLES);
        assert!(map.data.iter().all(|&h| h == 0));
        assert_eq!(meta.world_size, Some(128.0));
        assert!(
            create_world_project(&tmp.path().join("demo")).is_err(),
            "nunca sobrescreve"
        );
    }
}
