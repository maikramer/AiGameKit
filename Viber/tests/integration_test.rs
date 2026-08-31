//! CLI integration tests: run the `viber` binary end-to-end (headless).

use std::path::{Path, PathBuf};
use std::process::Command;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
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
fn test_analyze_unknown_tag_fails_with_hint() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bad.xml");
    std::fs::write(&path, "<world><GameObject /></world>").unwrap();
    let (code, _, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 1);
    assert!(stderr.contains("unknown element"), "stderr: {stderr}");
    assert!(stderr.contains("entity"), "stderr: {stderr}");
}

#[test]
fn test_analyze_include_chain_is_expanded() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("props.xml"), "<world><Cuboid /></world>").unwrap();
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
    std::fs::write(&path, "<world><Entity pos=\"0 0 0\" /></world>").unwrap();
    let (code, _, stderr) = viber(&["analyze", path.to_str().unwrap()]);
    assert_eq!(code, 0);
    assert!(
        stderr.contains("ignored attribute `pos`"),
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
