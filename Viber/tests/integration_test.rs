//! CLI integration tests: run the `viber` binary end-to-end (headless).

use std::path::{Path, PathBuf};
use std::process::Command;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}

/// Escreve o config.yaml mínimo (contrato obrigatório do jogo) num tempdir
/// de teste — docs/ASSETS.md.
fn write_config(dir: &Path) {
    std::fs::write(
        dir.join("config.yaml"),
        concat!(
            "assets:\n",
            "  roots: []\n",
            "  bgm_dir: assets/audio/bgm\n",
            "  sfx_dir: assets/audio/sfx\n",
            "  terrain_textures_dir: assets/textures\n",
            "game:\n",
            "  scripts_dir: scripts\n",
            "save:\n",
            "  dir: /tmp\n",
        ),
    )
    .unwrap();
}

fn viber(args: &[&str]) -> (i32, String, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_viber"))
        .args(args)
        .output()
        .expect("viber binary runs");
    (
        output.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&output.stdout).to_string(),
        String::from_utf8_lossy(&output.stderr).to_string(),
    )
}

#[test]
fn test_analyze_hello_fixture_succeeds() {
    let path = fixture("hello.xml");
    let (code, stdout, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 0, "stderr: {stderr}");
    // 2 groups (props + ground) + 4 primitives (cuboid, sphere, cylinder,
    // plane child) + 1 point light + 1 camera = 8; the ambient light is a
    // resource and does not count.
    assert!(stdout.contains("entities: 8"), "stdout: {stdout}");
    assert!(stdout.contains("groups 2"), "stdout: {stdout}");
    assert!(stdout.contains("primitives 4"), "stdout: {stdout}");
    assert!(stdout.contains("cameras 1"), "stdout: {stdout}");
    assert!(stdout.contains("OK"), "stdout: {stdout}");
}

#[test]
fn test_analyze_unknown_tag_is_skipped_with_warning() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bad.xml");
    std::fs::write(&path, "<world><GameObject /></world>").unwrap();
    write_config(dir.path());
    let (code, stdout, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 0, "stderr: {stderr}");
    assert!(
        stdout.contains("not implemented (skipped)"),
        "stdout: {stdout}"
    );
    assert!(stdout.contains("<GameObject>×1"), "stdout: {stdout}");
    assert!(stderr.contains("skipped as no-op"), "stderr: {stderr}");
}

#[test]
fn test_analyze_strict_fails_on_unknown_tag() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bad.xml");
    std::fs::write(&path, "<world><GameObject /></world>").unwrap();
    write_config(dir.path());
    let (code, _, stderr) = viber(&["analyze", "--strict", path.to_str().unwrap()]);
    assert_eq!(code, 1);
    assert!(stderr.contains("strict mode"), "stderr: {stderr}");
}

#[test]
fn test_analyze_include_chain_is_expanded() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("props.xml"), "<world><Cuboid /></world>").unwrap();
    write_config(dir.path());
    let path = dir.path().join("main.xml");
    std::fs::write(&path, "<world><Include src=\"props.xml\" /></world>").unwrap();
    let (code, stdout, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 0, "stderr: {stderr}");
    assert!(stdout.contains("primitives 1"), "stdout: {stdout}");
}

#[test]
fn test_analyze_warns_but_succeeds_on_unknown_attribute() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("warn.xml");
    std::fs::write(&path, "<world><Entity pos-x=\"0 0 0\" /></world>").unwrap();
    write_config(dir.path());
    let (code, _, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 0);
    assert!(
        stderr.contains("ignored attribute `pos-x`"),
        "stderr: {stderr}"
    );
}

#[test]
fn test_analyze_missing_file_fails() {
    let (code, _, stderr) = viber(&["analyze", "/nonexistent/world.xml"]);
    assert_eq!(code, 1);
    assert!(stderr.contains("not found"), "stderr: {stderr}");
}

#[test]
fn test_version_flag_succeeds() {
    let (code, stdout, _) = viber(&["--version"]);
    assert_eq!(code, 0);
    assert!(stdout.contains("viber"), "stdout: {stdout}");
}

/// Como `viber`, mas corre o binário num directório específico (para `create`
/// e a auto-descoberta de `run`/`analyze`, que resolvem contra o cwd).
fn viber_in(cwd: &Path, args: &[&str]) -> (i32, String, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_viber"))
        .args(args)
        .current_dir(cwd)
        .output()
        .expect("viber binary runs");
    (
        output.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&output.stdout).to_string(),
        String::from_utf8_lossy(&output.stderr).to_string(),
    )
}

#[test]
fn test_create_scaffolds_project_and_world_analyzes() {
    let dir = tempfile::tempdir().unwrap();
    let (code, stdout, stderr) = viber_in(dir.path(), &["create", "demo"]);
    assert_eq!(code, 0, "stderr: {stderr}");
    assert!(stdout.contains("world.xml"), "stdout: {stdout}");
    assert!(stdout.contains("viber run"), "stdout: {stdout}");

    let world = dir.path().join("demo").join("world.xml");
    assert!(world.is_file(), "world.xml criado");
    assert!(
        dir.path().join("demo").join("config.yaml").is_file(),
        "config.yaml criado"
    );
    let content = std::fs::read_to_string(&world).unwrap();
    assert!(content.contains("<world"), "conteúdo do template");

    // O mundo gerado tem de validar headless sem warnings.
    let (code, stdout, stderr) = viber(&["analyze", world.to_str().unwrap()]);
    assert_eq!(code, 0, "stderr: {stderr}");
    assert!(stdout.contains("OK"), "stdout: {stdout}");
    assert!(!stderr.contains("warning"), "stderr: {stderr}");
}

#[test]
fn test_create_refuses_existing_directory() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir(dir.path().join("taken")).unwrap();
    let (code, _, stderr) = viber_in(dir.path(), &["create", "taken"]);
    assert_eq!(code, 1);
    assert!(stderr.contains("already exists"), "stderr: {stderr}");
}

#[test]
fn test_run_without_world_fails_with_hint() {
    let dir = tempfile::tempdir().unwrap();
    let (code, _, stderr) = viber_in(dir.path(), &["run"]);
    assert_eq!(code, 1);
    assert!(
        stderr.contains("world.xml") && stderr.contains("viber run"),
        "stderr: {stderr}"
    );
}

#[test]
fn test_no_args_prints_help() {
    let (code, stdout, _) = viber(&[]);
    assert_eq!(code, 0);
    assert!(stdout.contains("Usage:"), "stdout: {stdout}");
    assert!(stdout.contains("create"), "stdout: {stdout}");
}

// ----- terrain features -----

fn world_fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("worlds")
        .join(name)
}

#[test]
fn test_analyze_terrain_world_reports_all_features() {
    let path = world_fixture("terrain.xml");
    let (code, stdout, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 0, "stderr: {stderr}");
    assert!(
        stdout.contains("terrain: heightfield 1"),
        "stdout: {stdout}"
    );
    assert!(stdout.contains("ground features 8"), "stdout: {stdout}");
    assert!(
        stdout.contains(
            "pads 1, lakes 1, rivers 1, cliffs 3, caves 0, arches 0, bridges 0, rock fields 0, roads 1 + \
             networks 1, decals 0"
        ),
        "stdout: {stdout}"
    );
    // The feature tags are implemented — nothing terrain-related is skipped.
    assert!(!stdout.contains("not implemented"), "stdout: {stdout}");
    assert!(
        !stderr.contains("Terrain"),
        "no terrain warnings expected: {stderr}"
    );
}

#[test]
fn test_analyze_terrain_world_is_strict_clean() {
    let path = world_fixture("terrain.xml");
    let (code, stdout, stderr) = viber(&["analyze", "--strict", path.to_str().unwrap()]);
    assert_eq!(code, 0, "stderr: {stderr}");
    assert!(stdout.contains("OK"), "stdout: {stdout}");
}

#[test]
fn test_analyze_terrain_river_needs_two_points() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bad-river.xml");
    std::fs::write(&path, r#"<world><River path="10 20" /></world>"#).unwrap();
    let (code, _, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 1);
    assert!(stderr.contains("at least 2 points"), "stderr: {stderr}");
}

#[test]
fn test_analyze_road_network_rejects_unknown_profile() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bad-net.xml");
    std::fs::write(
        &path,
        r#"<world><RoadNetwork default-profile="railway"><Way id="a" xz="0 0" /><Way id="b" xz="10 0" /><Segment a="a" b="b" /></RoadNetwork></world>"#,
    )
    .unwrap();
    let (code, _, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 1);
    assert!(stderr.contains("unknown profile"), "stderr: {stderr}");
}

#[test]
fn test_analyze_road_network_rejects_foreign_children() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bad-net-child.xml");
    std::fs::write(
        &path,
        r#"<world><RoadNetwork><Cuboid /></RoadNetwork></world>"#,
    )
    .unwrap();
    let (code, _, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 1);
    assert!(
        stderr.contains("only <Way> and <Segment>"),
        "stderr: {stderr}"
    );
}

#[test]
fn test_analyze_composition_and_prototypes_reported() {
    let dir = tempfile::tempdir().unwrap();
    write_config(dir.path());
    let path = dir.path().join("composition.xml");
    std::fs::write(
        &path,
        r##"<world>
  <Prototype id="house">
    <Composition name="house-root">
      <Box translation="0 1 0" half-size="2 1 1.5" base-color="#c8b8a0" />
      <Box translation="0 2.3 0" half-size="2.3 0.3 1.8" base-color="#8a4030" />
      <PointLight translation="0 1.6 0" color="#ffd9a0" intensity="600" />
    </Composition>
  </Prototype>
  <Use prototype="house" pos="0 0 0" name="house.a" />
  <Use prototype="house" pos="12 0 0" euler="0 90 0" name="house.b" />
  <Composition translation="-10 0 0" place="at: -10 0; align-to-terrain: 0">
    <Sphere radius="0.6" base-color="#ffcc77" metallic="0.6" roughness="0.3" />
    <Cylinder radius="0.2" half-height="1" translation="0 1 0" />
    <PointLight translation="0 2.2 0" intensity="900" color="#a0c0ff" />
  </Composition>
</world>"##,
    )
    .unwrap();
    let (code, stdout, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 0, "stderr: {stderr}");
    assert!(stdout.contains("compositions 3"), "stdout: {stdout}");
    assert!(
        stdout.contains("compositions: 3 root(s)"),
        "stdout: {stdout}"
    );
    // 4 partes nas 2 instâncias da casa (2 Box cada) + 2 na composition
    // standalone (Sphere + Cylinder); as PointLight não são partes.
    assert!(stdout.contains("6 part(s)"), "stdout: {stdout}");
    assert!(
        stdout.contains("prototypes: 1 definido(s), 2 instância(s)"),
        "stdout: {stdout}"
    );
    assert!(stdout.contains("house×2"), "stdout: {stdout}");
    assert!(stdout.contains("OK"), "stdout: {stdout}");

    // --strict: um <Use> apontando para um id inexistente falha.
    let bad = dir.path().join("bad-use.xml");
    std::fs::write(
        &bad,
        r##"<world>
  <Use prototype="fantasma" pos="0 0 0" />
</world>"##,
    )
    .unwrap();
    let (code, _, stderr) = viber(&["analyze", "--strict", bad.to_str().unwrap()]);
    assert_eq!(code, 1);
    assert!(
        stderr.contains("protótipo desconhecido"),
        "stderr: {stderr}"
    );
}
