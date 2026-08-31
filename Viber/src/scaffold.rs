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

/// Cria `<target_dir>/world.xml` a partir do template.
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
    Ok(world_path)
}
