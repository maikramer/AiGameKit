//! Attribute value parsers for the Viber world XML.
//!
//! Mirrors the tolerant conventions of the original world format where they are
//! load-bearing (single-component broadcast, hex + named colors, yes/no bools)
//! and is strict everywhere else: vectors accept exactly 1 or N components —
//! 2 components is always an error.

use anyhow::{Result, anyhow, bail};

/// Parse an attribute value as a single finite number (`NaN`/`inf` rejected).
pub fn parse_f32(value: &str, ctx: &str) -> Result<f32> {
    let parsed = value
        .trim()
        .parse::<f32>()
        .map_err(|_| anyhow!("{ctx}: `{value}` is not a number"))?;
    if !parsed.is_finite() {
        bail!("{ctx}: `{value}` is not a finite number");
    }
    Ok(parsed)
}

/// Parse `"x"` (broadcast) or `"x y z"` into a 3-component vector.
/// Exactly 2 components is an error; more than 3 is also an error.
pub fn parse_vec3(value: &str, ctx: &str) -> Result<[f32; 3]> {
    let parts: Vec<&str> = value.split_whitespace().collect();
    match parts.len() {
        1 => Ok([parse_f32(parts[0], ctx)?; 3]),
        3 => Ok([
            parse_f32(parts[0], ctx)?,
            parse_f32(parts[1], ctx)?,
            parse_f32(parts[2], ctx)?,
        ]),
        n => bail!("{ctx}: expected 1 or 3 components, got {n}: `{value}`"),
    }
}

/// Parse `"w"` (broadcast) or `"w d"` into a 2-component size.
pub fn parse_vec2(value: &str, ctx: &str) -> Result<[f32; 2]> {
    let parts: Vec<&str> = value.split_whitespace().collect();
    match parts.len() {
        1 => Ok([parse_f32(parts[0], ctx)?; 2]),
        2 => Ok([parse_f32(parts[0], ctx)?, parse_f32(parts[1], ctx)?]),
        n => bail!("{ctx}: expected 1 or 2 components, got {n}: `{value}`"),
    }
}

/// Parse `"x y z w"` into a quaternion (all 4 components required).
pub fn parse_vec4(value: &str, ctx: &str) -> Result<[f32; 4]> {
    let parts: Vec<&str> = value.split_whitespace().collect();
    if parts.len() != 4 {
        bail!(
            "{ctx}: expected 4 components (x y z w), got {}: `{value}`",
            parts.len()
        );
    }
    Ok([
        parse_f32(parts[0], ctx)?,
        parse_f32(parts[1], ctx)?,
        parse_f32(parts[2], ctx)?,
        parse_f32(parts[3], ctx)?,
    ])
}

/// Parse `"x z x z …"` into a list of 2D points (paths, `via` waypoints).
/// An empty value yields an empty list; an odd component count is an error.
pub fn parse_vec2_list(value: &str, ctx: &str) -> Result<Vec<[f32; 2]>> {
    let parts: Vec<&str> = value.split_whitespace().collect();
    if !parts.len().is_multiple_of(2) {
        bail!(
            "{ctx}: expected an even number of components (x z pairs), got {}: `{value}`",
            parts.len()
        );
    }
    parts
        .chunks(2)
        .map(|pair| Ok([parse_f32(pair[0], ctx)?, parse_f32(pair[1], ctx)?]))
        .collect()
}

/// Parse an attribute value as a `u32` (non-negative, finite integers only).
pub fn parse_u32(value: &str, ctx: &str) -> Result<u32> {
    let v = parse_f32(value, ctx)?;
    if v < 0.0 || v.fract() != 0.0 || v > u32::MAX as f32 {
        bail!("{ctx}: `{value}` is not a non-negative integer");
    }
    Ok(v as u32)
}

/// Parse an attribute value as a `u64` (non-negative, finite integers only).
pub fn parse_u64(value: &str, ctx: &str) -> Result<u64> {
    let v = parse_f32(value, ctx)?;
    if v < 0.0 || v.fract() != 0.0 || v > 18_446_744_073_709_551_615.0 {
        bail!("{ctx}: `{value}` is not a non-negative integer");
    }
    Ok(v as u64)
}

/// Parse a boolean: `true/1/yes/on` vs `false/0/no/off` (case-insensitive).
pub fn parse_bool(value: &str, ctx: &str) -> Result<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "true" | "1" | "yes" | "on" => Ok(true),
        "false" | "0" | "no" | "off" => Ok(false),
        _ => bail!("{ctx}: `{value}` is not a boolean"),
    }
}

/// Parse a color as `#rgb`, `#rrggbb`, `0xrrggbb` or one of the named colors.
/// Returns linear-ish sRGB components in `0.0..=1.0`.
pub fn parse_color(value: &str, ctx: &str) -> Result<[f32; 3]> {
    let v = value.trim();
    let hex = v
        .strip_prefix('#')
        .or_else(|| v.strip_prefix("0x"))
        .or_else(|| v.strip_prefix("0X"));
    if let Some(hex) = hex {
        if !hex.chars().all(|c| c.is_ascii_hexdigit()) {
            bail!("{ctx}: `{value}` is not a valid hex color");
        }
        return match hex.len() {
            3 => Ok(hex
                .chars()
                .map(|c| (c.to_digit(16).unwrap() * 17) as f32 / 255.0)
                .collect::<Vec<f32>>()
                .try_into()
                .unwrap()),
            6 => {
                let rgb = u32::from_str_radix(hex, 16).unwrap();
                Ok([
                    ((rgb >> 16) & 0xff) as f32 / 255.0,
                    ((rgb >> 8) & 0xff) as f32 / 255.0,
                    (rgb & 0xff) as f32 / 255.0,
                ])
            }
            n => bail!("{ctx}: hex color must have 3 or 6 digits, got {n}: `{value}`"),
        };
    }
    named_color(v).ok_or_else(|| anyhow!("{ctx}: `{value}` is not a color"))
}

/// The 13 named colors carried over from the original format.
fn named_color(name: &str) -> Option<[f32; 3]> {
    let [r, g, b] = match name.to_ascii_lowercase().as_str() {
        "red" => [255, 0, 0],
        "green" => [0, 255, 0],
        "blue" => [0, 0, 255],
        "yellow" => [255, 255, 0],
        "purple" => [128, 0, 128],
        "cyan" => [0, 255, 255],
        "white" => [255, 255, 255],
        "black" => [0, 0, 0],
        "gray" => [128, 128, 128],
        "orange" => [255, 165, 0],
        "pink" => [255, 192, 203],
        "lime" => [0, 255, 0],
        "gold" => [255, 215, 0],
        _ => return None,
    };
    Some([r as f32 / 255.0, g as f32 / 255.0, b as f32 / 255.0])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_f32_ok() {
        assert_eq!(parse_f32("1.5", "t").unwrap(), 1.5);
        assert_eq!(parse_f32(" -2 ", "t").unwrap(), -2.0);
    }

    #[test]
    fn test_parse_f32_rejects_text() {
        assert!(parse_f32("abc", "t").is_err());
    }

    #[test]
    fn test_parse_f32_rejects_non_finite() {
        assert!(parse_f32("NaN", "t").is_err());
        assert!(parse_f32("inf", "t").is_err());
        assert!(parse_f32("-infinity", "t").is_err());
    }

    #[test]
    fn test_parse_vec3_broadcasts_single() {
        assert_eq!(parse_vec3("2", "t").unwrap(), [2.0, 2.0, 2.0]);
    }

    #[test]
    fn test_parse_vec3_accepts_three() {
        assert_eq!(parse_vec3("0 1.5 -3", "t").unwrap(), [0.0, 1.5, -3.0]);
    }

    #[test]
    fn test_parse_vec3_rejects_two_components() {
        assert!(parse_vec3("1 2", "t").is_err());
    }

    #[test]
    fn test_parse_vec3_rejects_four_components() {
        assert!(parse_vec3("1 2 3 4", "t").is_err());
    }

    #[test]
    fn test_parse_vec2_broadcasts_and_pairs() {
        assert_eq!(parse_vec2("3", "t").unwrap(), [3.0, 3.0]);
        assert_eq!(parse_vec2("3 4", "t").unwrap(), [3.0, 4.0]);
        assert!(parse_vec2("3 4 5", "t").is_err());
    }

    #[test]
    fn test_parse_vec4_requires_four() {
        assert_eq!(parse_vec4("0 0 0 1", "t").unwrap(), [0.0, 0.0, 0.0, 1.0]);
        assert!(parse_vec4("0 0 1", "t").is_err());
    }

    #[test]
    fn test_parse_vec2_list_pairs_and_errors() {
        assert_eq!(parse_vec2_list("", "t").unwrap(), Vec::<[f32; 2]>::new());
        assert_eq!(
            parse_vec2_list("0 0 10 20", "t").unwrap(),
            vec![[0.0, 0.0], [10.0, 20.0]]
        );
        assert!(parse_vec2_list("1 2 3", "t").is_err());
        assert!(parse_vec2_list("1", "t").is_err());
    }

    #[test]
    fn test_parse_u32_and_u64() {
        assert_eq!(parse_u32("64", "t").unwrap(), 64);
        assert!(parse_u32("-1", "t").is_err());
        assert!(parse_u32("1.5", "t").is_err());
        assert_eq!(parse_u64("42", "t").unwrap(), 42);
        assert!(parse_u64("x", "t").is_err());
    }

    #[test]
    fn test_parse_bool_variants() {
        for v in ["true", "1", "yes", "on", "TRUE", "On"] {
            assert!(parse_bool(v, "t").unwrap());
        }
        for v in ["false", "0", "no", "off", "No", "OFF"] {
            assert!(!parse_bool(v, "t").unwrap());
        }
        assert!(parse_bool("maybe", "t").is_err());
    }

    #[test]
    fn test_parse_color_hex_formats() {
        assert_eq!(parse_color("#ff0000", "t").unwrap(), [1.0, 0.0, 0.0]);
        assert_eq!(parse_color("#f00", "t").unwrap(), [1.0, 0.0, 0.0]);
        assert_eq!(parse_color("0x00ff00", "t").unwrap(), [0.0, 1.0, 0.0]);
    }

    #[test]
    fn test_parse_color_named() {
        assert_eq!(parse_color("Red", "t").unwrap(), [1.0, 0.0, 0.0]);
        assert_eq!(parse_color("gold", "t").unwrap()[0], 1.0);
        assert!(parse_color("chartreuse", "t").is_err());
    }

    #[test]
    fn test_parse_color_rejects_garbage() {
        assert!(parse_color("#12345", "t").is_err());
        assert!(parse_color("#zzzzzz", "t").is_err());
    }
}
