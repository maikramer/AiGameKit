use std::process::Command;

#[test]
fn test_cli_help() {
    let output = Command::new("cargo")
        .args(["run", "--", "--help"])
        .output()
        .expect("Failed to execute command");

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("Generate PBR maps"));
    assert!(stdout.contains("--output"));
    assert!(stdout.contains("--format"));
}

#[test]
fn test_cli_version() {
    let output = Command::new("cargo")
        .args(["run", "--", "--version"])
        .output()
        .expect("Failed to execute command");

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(env!("CARGO_PKG_VERSION")));
}

#[test]
fn test_file_not_found() {
    let output = Command::new("cargo")
        .args(["run", "--", "nonexistent.png", "-o", "/tmp/out"])
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("not found"));
}

#[test]
fn test_list_presets_exits_zero() {
    let output = Command::new("cargo")
        .args(["run", "--", "--list-presets"])
        .output()
        .expect("Failed to execute command");

    assert!(output.status.success(), "--list-presets should exit 0");
    let stdout = String::from_utf8_lossy(&output.stdout);
    for name in ["default", "skin", "metal", "auto"] {
        assert!(
            stdout.contains(name),
            "stdout should list preset '{name}':\n{stdout}"
        );
    }
}

#[test]
fn test_invalid_preset_rejected() {
    // clap rejects the bad value via the Preset FromStr value parser before any
    // GPU or file work happens, so this needs no adapter.
    let output = Command::new("cargo")
        .args(["run", "--", "x.png", "-p", "bogus"])
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success(), "invalid preset should fail");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("invalid value") && stderr.contains("bogus"),
        "stderr should mention the invalid value 'bogus':\n{stderr}"
    );
}

#[test]
fn test_only_and_skip_conflict_rejected() {
    // --only and --skip declare a mutual `conflicts_with` in clap, so the parse
    // fails before the pipeline starts (no GPU needed).
    let output = Command::new("cargo")
        .args(["run", "--", "x.png", "--only", "height", "--skip", "normal"])
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success(), "--only + --skip should conflict");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("cannot be used with"),
        "stderr should mention the conflict:\n{stderr}"
    );
}
