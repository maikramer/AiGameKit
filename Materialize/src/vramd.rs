//! Cliente mínimo do vramd (Unified Model Server) — só o que o materialize
//! precisa: delegar uma decomposição intrínseca ao backend `intrinsic`.
//!
//! Protocolo: JSONL sobre o Unix socket `~/.cache/vramd/vramd.sock`
//! (`VRAMD_CLIENT_SOCKET` para override). Envelope de request
//! {"cmd":"generate","backend":…,"priority":…}; resposta final é o dict do
//! worker ({"status":"ok","output":…} ou {"status":"error","error":…}).
//! O protocolo completo vive em `aigamekit_shared.worker_protocol` / vramd.

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{Value, json};

/// Saídas do backend `intrinsic`.
#[derive(Debug, Clone)]
pub struct IntrinsicPaths {
    pub albedo: PathBuf,
    pub shading: PathBuf,
    pub specular: PathBuf,
}

#[derive(Debug)]
pub enum VramdError {
    /// vramd não está acessível (socket ausente / timeout de ligação).
    Unavailable(String),
    /// vramd respondeu com erro (backend em falta, GPU ocupada, …).
    Backend(String),
}

impl std::fmt::Display for VramdError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VramdError::Unavailable(m) => write!(f, "vramd indisponível: {m}"),
            VramdError::Backend(m) => write!(f, "backend intrinsic: {m}"),
        }
    }
}

pub fn vramd_socket_path() -> PathBuf {
    if let Ok(p) = std::env::var("VRAMD_CLIENT_SOCKET") {
        return PathBuf::from(p);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join(".cache/vramd/vramd.sock")
}

fn parse_response(line: &str) -> Result<IntrinsicPaths, VramdError> {
    let v: Value = serde_json::from_str(line)
        .map_err(|e| VramdError::Backend(format!("resposta não-JSON: {e}")))?;
    match v.get("status").and_then(|s| s.as_str()) {
        Some("ok") => {
            let out = |key: &str| -> Result<PathBuf, VramdError> {
                v.get(key)
                    .and_then(|o| o.as_str())
                    .map(PathBuf::from)
                    .ok_or_else(|| VramdError::Backend(format!("resposta sem '{key}'")))
            };
            Ok(IntrinsicPaths {
                albedo: out("output")?,
                shading: out("output_shading")?,
                specular: out("output_specular")?,
            })
        }
        _ => {
            let msg = v
                .get("error")
                .and_then(|e| e.as_str())
                .unwrap_or("erro desconhecido");
            Err(VramdError::Backend(msg.to_string()))
        }
    }
}

/// Delega a decomposição intrínseca ao backend `intrinsic` do vramd.
///
/// Bloqueante (uma linha de resposta); `timeout` cobre a leitura inteira.
pub fn decompose_via_vramd(
    image_path: &Path,
    output_dir: &Path,
    timeout: Duration,
) -> Result<IntrinsicPaths, VramdError> {
    let sock = vramd_socket_path();
    if !sock.exists() {
        return Err(VramdError::Unavailable(format!(
            "socket {} ausente — arranca com 'vramd start'",
            sock.display()
        )));
    }
    let image_abs = std::fs::canonicalize(image_path)
        .map_err(|e| VramdError::Unavailable(format!("input: {e}")))?;
    let out_abs = if output_dir.is_absolute() {
        output_dir.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_default().join(output_dir)
    };

    let mut stream =
        UnixStream::connect(&sock).map_err(|e| VramdError::Unavailable(format!("connect: {e}")))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|e| VramdError::Unavailable(format!("set_read_timeout: {e}")))?;

    let request = json!({
        "cmd": "generate",
        "backend": "intrinsic",
        "priority": "interactive",
        "image_path": image_abs.to_string_lossy(),
        "output_dir": out_abs.to_string_lossy(),
    });
    stream
        .write_all(format!("{request}\n").as_bytes())
        .map_err(|e| VramdError::Unavailable(format!("write: {e}")))?;
    stream
        .flush()
        .map_err(|e| VramdError::Unavailable(format!("flush: {e}")))?;

    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|e| VramdError::Unavailable(format!("read: {e}")))?;
    if line.trim().is_empty() {
        return Err(VramdError::Unavailable(
            "vramd fechou o socket sem resposta".to_string(),
        ));
    }
    parse_response(line.trim())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn resp(s: &str) -> String {
        s.to_string()
    }

    #[test]
    fn test_parse_response_ok() {
        let paths = parse_response(
            r#"{"status":"ok","output":"/o/a_albedo.png","output_shading":"/o/a_shading.png","output_specular":"/o/a_specular.png"}"#,
        )
        .unwrap();
        assert_eq!(paths.albedo, Path::new("/o/a_albedo.png"));
        assert_eq!(paths.shading, Path::new("/o/a_shading.png"));
        assert_eq!(paths.specular, Path::new("/o/a_specular.png"));
    }

    #[test]
    fn test_parse_response_backend_error() {
        let err = parse_response(
            r#"{"status":"error","error":"venv da tool 'intrinsic' não encontrado"}"#,
        )
        .unwrap_err();
        match err {
            VramdError::Backend(m) => assert!(m.contains("venv")),
            other => panic!("expected Backend, got {other:?}"),
        }
    }

    #[test]
    fn test_parse_response_missing_fields() {
        let err = parse_response(r#"{"status":"ok","output":"/o/a.png"}"#).unwrap_err();
        assert!(matches!(err, VramdError::Backend(_)));
    }

    #[test]
    fn test_parse_response_non_json() {
        assert!(matches!(
            parse_response("not json"),
            Err(VramdError::Backend(_))
        ));
    }

    #[test]
    fn test_error_display_messages() {
        let e = VramdError::Unavailable("socket ausente".into());
        assert!(e.to_string().contains("indisponível"));
        let e = VramdError::Backend("GPU ocupada".into());
        assert!(e.to_string().contains("GPU ocupada"));
    }

    #[test]
    fn test_socket_path_env_override() {
        // Sem env: caminho canónico sob $HOME.
        let p = vramd_socket_path();
        assert!(
            p.ends_with(".cache/vramd/vramd.sock") || std::env::var("VRAMD_CLIENT_SOCKET").is_ok()
        );
        let _ = resp("x"); // silencia unused quando env está definido
    }

    #[test]
    fn test_unavailable_when_socket_missing() {
        // Aponta para um socket que com certeza não existe.
        // Safety: os testes deste crate correm single-threaded por processo.
        unsafe {
            std::env::set_var("VRAMD_CLIENT_SOCKET", "/nonexistent/vramd-test.sock");
        }
        let err = decompose_via_vramd(
            Path::new("/etc/hostname"),
            Path::new("/tmp"),
            Duration::from_secs(1),
        )
        .unwrap_err();
        unsafe {
            std::env::remove_var("VRAMD_CLIENT_SOCKET");
        }
        assert!(matches!(err, VramdError::Unavailable(_)));
    }
}
