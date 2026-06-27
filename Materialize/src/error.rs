//! Typed errors with stable process exit codes.
//!
//! Exit codes (see docs/cli-api.md):
//!   0 success · 1 generic · 2 input not found · 3 unsupported input format
//!   4 GPU error · 5 I/O error · 6 image too large for GPU.

use std::fmt;

#[derive(Debug)]
pub enum MaterializeError {
    NotFound(String),
    UnsupportedFormat(String),
    Gpu(String),
    Io(String),
    TooLarge { width: u32, height: u32, bytes: u64 },
    Other(anyhow::Error),
}

impl MaterializeError {
    pub fn exit_code(&self) -> u8 {
        match self {
            Self::NotFound(_) => 2,
            Self::UnsupportedFormat(_) => 3,
            Self::Gpu(_) => 4,
            Self::Io(_) => 5,
            Self::TooLarge { .. } => 6,
            Self::Other(_) => 1,
        }
    }
}

impl fmt::Display for MaterializeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            // Messages from io::load_image / bail! already include the path; do not re-wrap.
            Self::NotFound(msg) => write!(f, "{msg}"),
            Self::UnsupportedFormat(msg) => write!(f, "{msg}"),
            Self::Gpu(m) => write!(f, "GPU error: {m}"),
            Self::Io(m) => write!(f, "I/O error: {m}"),
            Self::TooLarge {
                width,
                height,
                bytes,
            } => {
                write!(
                    f,
                    "Image too large for GPU: {width}x{height} (~{bytes} bytes)"
                )
            }
            Self::Other(e) => write!(f, "{e:#}"),
        }
    }
}

impl std::error::Error for MaterializeError {}

impl From<anyhow::Error> for MaterializeError {
    fn from(e: anyhow::Error) -> Self {
        let msg = e.to_string();
        let lower = msg.to_lowercase();
        if lower.contains("not found") || msg.contains("No such file") {
            Self::NotFound(msg)
        } else if lower.contains("unsupported format") || lower.contains("image format") {
            Self::UnsupportedFormat(msg)
        } else if lower.contains("no gpu adapter")
            || lower.contains("adapter")
            || lower.contains("device")
            || lower.contains("gpu")
            || lower.contains("vulkan")
            || lower.contains("metal")
            || lower.contains("dx12")
        {
            Self::Gpu(msg)
        } else {
            Self::Other(e)
        }
    }
}

impl From<std::io::Error> for MaterializeError {
    fn from(e: std::io::Error) -> Self {
        use std::io::ErrorKind;
        match e.kind() {
            ErrorKind::NotFound => Self::NotFound(e.to_string()),
            _ => {
                let msg = e.to_string();
                let lower = msg.to_lowercase();
                if lower.contains("not found") || lower.contains("no such file") {
                    Self::NotFound(msg)
                } else {
                    Self::Io(msg)
                }
            }
        }
    }
}

impl From<image::ImageError> for MaterializeError {
    fn from(e: image::ImageError) -> Self {
        use image::ImageError;
        match e {
            ImageError::IoError(io) => Self::from(io),
            ImageError::Decoding(de) => Self::UnsupportedFormat(de.to_string()),
            ImageError::Unsupported(uf) => Self::UnsupportedFormat(uf.to_string()),
            ImageError::Encoding(en) => Self::Io(format!("encode: {en}")),
            ImageError::Parameter(p) => Self::Other(anyhow::anyhow!("{p}")),
            ImageError::Limits(l) => Self::TooLarge {
                width: 0,
                height: 0,
                bytes: l.to_string().len() as u64,
            },
        }
    }
}

pub type Result<T> = std::result::Result<T, MaterializeError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_exit_codes() {
        assert_eq!(MaterializeError::NotFound("x".into()).exit_code(), 2);
        assert_eq!(
            MaterializeError::UnsupportedFormat("x".into()).exit_code(),
            3
        );
        assert_eq!(MaterializeError::Gpu("x".into()).exit_code(), 4);
        assert_eq!(MaterializeError::Io("x".into()).exit_code(), 5);
        assert_eq!(
            MaterializeError::TooLarge {
                width: 1,
                height: 1,
                bytes: 1
            }
            .exit_code(),
            6
        );
        assert_eq!(MaterializeError::Other(anyhow::anyhow!("x")).exit_code(), 1);
    }

    #[test]
    fn test_from_anyhow_classifies() {
        let e: MaterializeError = anyhow::anyhow!("Input file 'foo.png' not found").into();
        assert!(matches!(e, MaterializeError::NotFound(_)));

        let e: MaterializeError = anyhow::anyhow!("No GPU adapter available").into();
        assert!(matches!(e, MaterializeError::Gpu(_)));
    }

    #[test]
    fn test_from_io_classifies() {
        let e: MaterializeError =
            std::io::Error::new(std::io::ErrorKind::NotFound, "missing").into();
        assert!(matches!(e, MaterializeError::NotFound(_)));

        let e: MaterializeError =
            std::io::Error::new(std::io::ErrorKind::PermissionDenied, "nope").into();
        assert!(matches!(e, MaterializeError::Io(_)));
    }
}
