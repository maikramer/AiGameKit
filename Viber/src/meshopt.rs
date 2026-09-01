//! `EXT_meshopt_compression` — decoding glTF buffer views at load time.
//!
//! The asset pipeline ships its GLBs meshopt-compressed (with
//! `KHR_mesh_quantization`, and KTX2 textures via `KHR_texture_basisu`). Bevy
//! reads the other two but has no meshopt decoder, so Viber previously needed
//! a build step that decompressed every GLB into the example tree — a second,
//! larger copy of the whole asset pool.
//!
//! This module decodes the extension in-process, so the engine reads the
//! shared pool as authored.
//!
//! # How the extension works
//!
//! A compressed buffer view carries an `EXT_meshopt_compression` block naming
//! the byte range of its compressed data plus how to expand it: `mode`
//! (`ATTRIBUTES` for vertex streams, `TRIANGLES`/`INDICES` for index streams),
//! `count` × `byteStride` for the decoded size, and an optional `filter` that
//! is undone afterwards (octahedral normals, quaternion rotations, shared
//! exponents).
//!
//! [`decode_glb`] expands every such view into one plain buffer and rewrites
//! the JSON to point at it, producing a GLB that any glTF reader accepts.

use std::collections::HashMap;

use anyhow::{Context, Result, bail};

/// GLB container magic (`glTF`).
const GLB_MAGIC: u32 = 0x4654_6C67;
/// Chunk type tag for the JSON chunk.
const CHUNK_JSON: u32 = 0x4E4F_534A;
/// Chunk type tag for the binary chunk.
const CHUNK_BIN: u32 = 0x004E_4942;
/// glTF extension name.
const EXT: &str = "EXT_meshopt_compression";
/// Quantized attribute extension; see [`decode_glb`].
const QUANTIZATION: &str = "KHR_mesh_quantization";
/// KTX2/BasisU texture extension; see [`lift_basisu_sources`].
const BASISU: &str = "KHR_texture_basisu";

/// Does this GLB use any extension Bevy needs help with?
///
/// Cheap enough to run on every asset read: it parses only the JSON chunk and
/// looks at `extensionsUsed`. Covers meshopt (decoded), quantization and KTX2
/// textures (both normalized) — see [`decode_glb`].
pub fn needs_rewrite(bytes: &[u8]) -> bool {
    uses_any(bytes, &[EXT, QUANTIZATION, BASISU])
}

/// Does this GLB carry meshopt-compressed buffer views?
pub fn needs_decode(bytes: &[u8]) -> bool {
    uses_any(bytes, &[EXT])
}

/// True when `extensionsUsed` names any of `names`.
fn uses_any(bytes: &[u8], names: &[&str]) -> bool {
    parse_glb(bytes)
        .ok()
        .and_then(|(json, _)| {
            serde_json::from_slice::<serde_json::Value>(json)
                .ok()
                .map(|doc| {
                    doc.get("extensionsUsed")
                        .and_then(|v| v.as_array())
                        .is_some_and(|used| {
                            used.iter()
                                .filter_map(serde_json::Value::as_str)
                                .any(|e| names.contains(&e))
                        })
                })
        })
        .unwrap_or(false)
}

/// Splits a GLB into its JSON and BIN chunks.
fn parse_glb(bytes: &[u8]) -> Result<(&[u8], &[u8])> {
    if bytes.len() < 12 {
        bail!("not a GLB: {} bytes", bytes.len());
    }
    let magic = u32::from_le_bytes(bytes[0..4].try_into()?);
    if magic != GLB_MAGIC {
        bail!("not a GLB: bad magic {magic:#x}");
    }
    let mut json: Option<&[u8]> = None;
    let mut bin: &[u8] = &[];
    let mut cursor = 12;
    while cursor + 8 <= bytes.len() {
        let len = u32::from_le_bytes(bytes[cursor..cursor + 4].try_into()?) as usize;
        let kind = u32::from_le_bytes(bytes[cursor + 4..cursor + 8].try_into()?);
        let start = cursor + 8;
        let end = start
            .checked_add(len)
            .filter(|end| *end <= bytes.len())
            .context("GLB chunk runs past the end of the file")?;
        match kind {
            CHUNK_JSON => json = Some(&bytes[start..end]),
            CHUNK_BIN => bin = &bytes[start..end],
            _ => {} // unknown chunk types are skipped per spec
        }
        cursor = end;
    }
    Ok((json.context("GLB has no JSON chunk")?, bin))
}

/// What a compressed buffer view expands to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Interleaved vertex attributes.
    Attributes,
    /// Triangle index buffer.
    Triangles,
    /// Index sequence (points/lines, or non-triangle topologies).
    Indices,
}

impl Mode {
    fn parse(value: &str) -> Result<Self> {
        match value {
            "ATTRIBUTES" => Ok(Mode::Attributes),
            "TRIANGLES" => Ok(Mode::Triangles),
            "INDICES" => Ok(Mode::Indices),
            other => bail!("unknown EXT_meshopt_compression mode `{other}`"),
        }
    }
}

/// Post-decode transform to undo.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Filter {
    None,
    Octahedral,
    Quaternion,
    Exponential,
}

impl Filter {
    fn parse(value: &str) -> Result<Self> {
        match value {
            "NONE" => Ok(Filter::None),
            "OCTAHEDRAL" => Ok(Filter::Octahedral),
            "QUATERNION" => Ok(Filter::Quaternion),
            "EXPONENTIAL" => Ok(Filter::Exponential),
            other => bail!("unknown EXT_meshopt_compression filter `{other}`"),
        }
    }
}

/// Expands one compressed stream into raw bytes.
///
/// `count` elements of `stride` bytes each; `encoded` is the compressed slice
/// taken from the buffer the extension names.
pub fn decode_stream(
    encoded: &[u8],
    count: usize,
    stride: usize,
    mode: Mode,
    filter: Filter,
) -> Result<Vec<u8>> {
    if count == 0 || stride == 0 {
        return Ok(Vec::new());
    }
    let mut out = vec![0u8; count * stride];

    // The safe wrappers in `meshopt` are generic over a compile-time sized
    // element type; the extension's stride is only known at runtime, so the
    // C entry points are called directly.
    let code = match mode {
        Mode::Attributes => unsafe {
            meshopt::ffi::meshopt_decodeVertexBuffer(
                out.as_mut_ptr().cast(),
                count,
                stride,
                encoded.as_ptr(),
                encoded.len(),
            )
        },
        Mode::Triangles => unsafe {
            meshopt::ffi::meshopt_decodeIndexBuffer(
                out.as_mut_ptr().cast(),
                count,
                stride,
                encoded.as_ptr(),
                encoded.len(),
            )
        },
        Mode::Indices => unsafe {
            meshopt::ffi::meshopt_decodeIndexSequence(
                out.as_mut_ptr().cast(),
                count,
                stride,
                encoded.as_ptr(),
                encoded.len(),
            )
        },
    };
    if code != 0 {
        bail!("meshopt decode failed with code {code} (mode {mode:?}, {count}x{stride})");
    }

    // Filters are applied in place over the decoded bytes.
    match filter {
        Filter::None => {}
        Filter::Octahedral => unsafe {
            meshopt::ffi::meshopt_decodeFilterOct(out.as_mut_ptr().cast(), count, stride);
        },
        Filter::Quaternion => unsafe {
            meshopt::ffi::meshopt_decodeFilterQuat(out.as_mut_ptr().cast(), count, stride);
        },
        Filter::Exponential => unsafe {
            meshopt::ffi::meshopt_decodeFilterExp(out.as_mut_ptr().cast(), count, stride);
        },
    }
    Ok(out)
}

/// Decodes every compressed buffer view in a GLB, returning a plain GLB.
///
/// Returns the input untouched when the extension is not present, so this is
/// safe to call on any asset.
pub fn decode_glb(bytes: &[u8]) -> Result<Vec<u8>> {
    if !needs_rewrite(bytes) {
        return Ok(bytes.to_vec());
    }
    let (json_chunk, bin) = parse_glb(bytes)?;
    let mut doc: serde_json::Value =
        serde_json::from_slice(json_chunk).context("parsing the GLB JSON chunk")?;

    // Buffers other than the embedded BIN chunk are not supported: the
    // pipeline emits self-contained GLBs, and a URI buffer would need the
    // asset reader to resolve a sibling file.
    let buffers = doc
        .get("buffers")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    for (i, buffer) in buffers.iter().enumerate() {
        if buffer.get("uri").is_some() {
            bail!("buffer {i} is external (`uri`); only self-contained GLBs are decoded");
        }
    }

    let views = doc
        .get("bufferViews")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let mut out_bin: Vec<u8> = Vec::with_capacity(bin.len() * 3);
    // view index -> (offset, length) in the new buffer
    let mut relocated: HashMap<usize, (usize, usize)> = HashMap::new();

    for (index, view) in views.iter().enumerate() {
        let Some(ext) = view.get("extensions").and_then(|e| e.get(EXT)) else {
            continue;
        };
        let get_usize = |key: &str| -> Result<usize> {
            ext.get(key)
                .and_then(serde_json::Value::as_u64)
                .map(|v| v as usize)
                .with_context(|| format!("bufferView {index}: `{key}` missing from {EXT}"))
        };
        let byte_offset = ext
            .get("byteOffset")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0) as usize;
        let byte_length = get_usize("byteLength")?;
        let count = get_usize("count")?;
        let stride = get_usize("byteStride")?;
        let mode = Mode::parse(
            ext.get("mode")
                .and_then(serde_json::Value::as_str)
                .context("EXT_meshopt_compression: `mode` missing")?,
        )?;
        let filter = match ext.get("filter").and_then(serde_json::Value::as_str) {
            Some(name) => Filter::parse(name)?,
            None => Filter::None,
        };

        let end = byte_offset
            .checked_add(byte_length)
            .filter(|end| *end <= bin.len())
            .with_context(|| {
                format!("bufferView {index}: compressed range outside the BIN chunk")
            })?;
        let decoded = decode_stream(&bin[byte_offset..end], count, stride, mode, filter)
            .with_context(|| format!("decoding bufferView {index}"))?;

        // glTF requires 4-byte alignment for buffer views.
        while !out_bin.len().is_multiple_of(4) {
            out_bin.push(0);
        }
        relocated.insert(index, (out_bin.len(), decoded.len()));
        out_bin.extend_from_slice(&decoded);
    }

    // A file can need the KTX2 fix without carrying a single compressed view;
    // in that case the binary chunk is reused as-is and only the JSON is
    // rewritten.
    if relocated.is_empty() {
        let mut doc = doc;
        let mut out_bin = bin.to_vec();
        lift_basisu_sources(&mut doc);
        if dequantize_vertex_attributes(&mut doc, &mut out_bin)? {
            strip_extension(&mut doc, "extensionsUsed", QUANTIZATION);
        }
        strip_extension(&mut doc, "extensionsRequired", QUANTIZATION);
        return write_glb(&doc, &out_bin);
    }

    // Uncompressed views are copied across unchanged so their data survives.
    let mut plain: HashMap<usize, (usize, usize)> = HashMap::new();
    for (index, view) in views.iter().enumerate() {
        if relocated.contains_key(&index) {
            continue;
        }
        let offset = view
            .get("byteOffset")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0) as usize;
        let length = view
            .get("byteLength")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0) as usize;
        let end = offset
            .checked_add(length)
            .filter(|end| *end <= bin.len())
            .with_context(|| format!("bufferView {index}: range outside the BIN chunk"))?;
        while !out_bin.len().is_multiple_of(4) {
            out_bin.push(0);
        }
        plain.insert(index, (out_bin.len(), length));
        out_bin.extend_from_slice(&bin[offset..end]);
    }

    // Rewrite the views: every one now points into the single new buffer and
    // carries no compression extension.
    if let Some(array) = doc.get_mut("bufferViews").and_then(|v| v.as_array_mut()) {
        for (index, view) in array.iter_mut().enumerate() {
            let (offset, length) = relocated
                .get(&index)
                .or_else(|| plain.get(&index))
                .copied()
                .context("every buffer view is accounted for")?;
            let object = view
                .as_object_mut()
                .context("bufferView is not a JSON object")?;
            object.insert("buffer".into(), serde_json::json!(0));
            object.insert("byteOffset".into(), serde_json::json!(offset));
            object.insert("byteLength".into(), serde_json::json!(length));
            if let Some(extensions) = object.get_mut("extensions").and_then(|e| e.as_object_mut()) {
                extensions.remove(EXT);
                if extensions.is_empty() {
                    object.remove("extensions");
                }
            }
        }
    }

    // One buffer, sized to the decoded payload.
    doc["buffers"] = serde_json::json!([{ "byteLength": out_bin.len() }]);
    strip_extension(&mut doc, "extensionsUsed", EXT);
    strip_extension(&mut doc, "extensionsRequired", EXT);
    // `KHR_mesh_quantization` only tells a reader to expect normalized integer
    // attributes, which core glTF accessors already describe — the flag gates
    // readers rather than changing the data. Bevy refuses any file that
    // *requires* an extension it does not name, so the requirement is dropped
    // while `extensionsUsed` keeps the honest record of how the file was
    // authored.
    lift_basisu_sources(&mut doc);
    // After the buffer views are rewritten: the de-quantizer reads decoded
    // data and appends its packed output to the same binary chunk.
    if dequantize_vertex_attributes(&mut doc, &mut out_bin)? {
        strip_extension(&mut doc, "extensionsUsed", QUANTIZATION);
    }
    strip_extension(&mut doc, "extensionsRequired", QUANTIZATION);
    doc["buffers"] = serde_json::json!([{ "byteLength": out_bin.len() }]);

    write_glb(&doc, &out_bin)
}

/// Moves `KHR_texture_basisu`'s image index into the texture's core `source`.
///
/// A KTX2 texture puts its image index inside the extension block and leaves
/// the core `source` unset. `gltf::Texture::source()` unwraps that field, so
/// the loader *panics* on such a file rather than reporting anything useful.
///
/// The image itself needs no conversion — it is an ordinary glTF image whose
/// `mimeType` is `image/ktx2`, which Bevy decodes with the `basis-universal`
/// and `ktx2` features. Only the index is in the wrong place, so it is lifted
/// and the requirement dropped.
fn lift_basisu_sources(doc: &mut serde_json::Value) {
    let mut lifted = false;
    if let Some(textures) = doc.get_mut("textures").and_then(|v| v.as_array_mut()) {
        for texture in textures.iter_mut() {
            let Some(object) = texture.as_object_mut() else {
                continue;
            };
            if object.contains_key("source") {
                continue;
            }
            let source = object
                .get("extensions")
                .and_then(|e| e.get(BASISU))
                .and_then(|b| b.get("source"))
                .and_then(serde_json::Value::as_u64);
            let Some(source) = source else { continue };
            object.insert("source".into(), serde_json::json!(source));
            if let Some(extensions) = object.get_mut("extensions").and_then(|e| e.as_object_mut()) {
                extensions.remove(BASISU);
                if extensions.is_empty() {
                    object.remove("extensions");
                }
            }
            lifted = true;
        }
    }
    if lifted {
        strip_extension(doc, "extensionsUsed", BASISU);
        strip_extension(doc, "extensionsRequired", BASISU);
    }
}

/// Removes `name` from an extension list, dropping the list when it empties.
fn strip_extension(doc: &mut serde_json::Value, key: &str, name: &str) {
    let Some(array) = doc.get_mut(key).and_then(|v| v.as_array_mut()) else {
        return;
    };
    array.retain(|e| e.as_str() != Some(name));
    if array.is_empty() {
        doc.as_object_mut().map(|o| o.remove(key));
    }
}

/// Serializes a glTF document plus its binary payload back into a GLB.
fn write_glb(doc: &serde_json::Value, bin: &[u8]) -> Result<Vec<u8>> {
    let mut json = serde_json::to_vec(doc)?;
    // Both chunks are padded to 4 bytes: JSON with spaces, BIN with zeros.
    while !json.len().is_multiple_of(4) {
        json.push(b' ');
    }
    let mut bin = bin.to_vec();
    while !bin.len().is_multiple_of(4) {
        bin.push(0);
    }
    let total = 12 + 8 + json.len() + if bin.is_empty() { 0 } else { 8 + bin.len() };
    let mut out = Vec::with_capacity(total);
    out.extend_from_slice(&GLB_MAGIC.to_le_bytes());
    out.extend_from_slice(&2u32.to_le_bytes()); // glTF version
    out.extend_from_slice(&(total as u32).to_le_bytes());
    out.extend_from_slice(&(json.len() as u32).to_le_bytes());
    out.extend_from_slice(&CHUNK_JSON.to_le_bytes());
    out.extend_from_slice(&json);
    if !bin.is_empty() {
        out.extend_from_slice(&(bin.len() as u32).to_le_bytes());
        out.extend_from_slice(&CHUNK_BIN.to_le_bytes());
        out.extend_from_slice(&bin);
    }
    Ok(out)
}

// ------------------------------------------------------------- dequantizing

/// glTF accessor component types.
const COMP_BYTE: u64 = 5120;
const COMP_UNSIGNED_BYTE: u64 = 5121;
const COMP_SHORT: u64 = 5122;
const COMP_UNSIGNED_SHORT: u64 = 5123;
const COMP_FLOAT: u64 = 5126;

/// Vertex semantics Bevy insists on receiving as `f32`.
///
/// Its glTF loader passes POSITION / NORMAL / TANGENT through unconverted
/// (`ConversionMode::Any`) and then checks them against the target attribute,
/// which is `Float32x3` / `Float32x4`. A quantized tangent therefore arrives as
/// `Snorm8x4`, gets dropped, and the mesh panics on the missing attribute.
/// Texture coordinates and joint weights have their own conversion paths in
/// Bevy and are left alone.
const FLOAT_SEMANTICS: [&str; 3] = ["POSITION", "NORMAL", "TANGENT"];

/// Number of components in a glTF accessor `type`.
fn components_of(kind: &str) -> Option<usize> {
    match kind {
        "SCALAR" => Some(1),
        "VEC2" => Some(2),
        "VEC3" => Some(3),
        "VEC4" => Some(4),
        _ => None,
    }
}

/// Reads one integer component and maps it to `f32`.
///
/// A `normalized` accessor stores a fraction of the type's range (glTF spec
/// 3.6.1.2); a plain one stores the value itself, and `KHR_mesh_quantization`
/// leans on the node transform to scale it back. Either way the *meaning* is
/// preserved by converting exactly as the spec prescribes, so the node's TRS
/// still lands the mesh where it belongs.
#[inline]
fn component_to_f32(bytes: &[u8], component_type: u64, normalized: bool) -> f32 {
    match component_type {
        COMP_BYTE => {
            let v = bytes[0] as i8 as f32;
            if normalized { (v / 127.0).max(-1.0) } else { v }
        }
        COMP_UNSIGNED_BYTE => {
            let v = bytes[0] as f32;
            if normalized { v / 255.0 } else { v }
        }
        COMP_SHORT => {
            let v = i16::from_le_bytes([bytes[0], bytes[1]]) as f32;
            if normalized {
                (v / 32767.0).max(-1.0)
            } else {
                v
            }
        }
        COMP_UNSIGNED_SHORT => {
            let v = u16::from_le_bytes([bytes[0], bytes[1]]) as f32;
            if normalized { v / 65535.0 } else { v }
        }
        _ => f32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]),
    }
}

/// Bytes per component of a glTF accessor component type.
fn component_size(component_type: u64) -> usize {
    match component_type {
        COMP_BYTE | COMP_UNSIGNED_BYTE => 1,
        COMP_SHORT | COMP_UNSIGNED_SHORT => 2,
        _ => 4,
    }
}

/// Rewrites quantized POSITION/NORMAL/TANGENT accessors as tightly packed
/// `f32`, appending the new data to `bin`.
///
/// Each converted accessor gets its own buffer view rather than being written
/// back in place: quantized meshes are interleaved, so widening one attribute
/// would change the stride every attribute in that view shares. Emitting a
/// packed view per accessor sidesteps the stride entirely, and is what the
/// renderer wants anyway.
///
/// Returns `true` when anything changed.
fn dequantize_vertex_attributes(doc: &mut serde_json::Value, bin: &mut Vec<u8>) -> Result<bool> {
    // Which accessors feed a semantic Bevy needs as float.
    let mut wanted: Vec<usize> = Vec::new();
    for mesh in doc["meshes"].as_array().into_iter().flatten() {
        for primitive in mesh["primitives"].as_array().into_iter().flatten() {
            let Some(attributes) = primitive["attributes"].as_object() else {
                continue;
            };
            for (semantic, index) in attributes {
                if !FLOAT_SEMANTICS.contains(&semantic.as_str()) {
                    continue;
                }
                if let Some(index) = index.as_u64() {
                    wanted.push(index as usize);
                }
            }
        }
    }
    wanted.sort_unstable();
    wanted.dedup();
    if wanted.is_empty() {
        return Ok(false);
    }

    let views = doc["bufferViews"].as_array().cloned().unwrap_or_default();
    let accessors = doc["accessors"].as_array().cloned().unwrap_or_default();
    let mut converted = false;

    for index in wanted {
        let Some(accessor) = accessors.get(index) else {
            continue;
        };
        let component_type = accessor["componentType"].as_u64().unwrap_or(COMP_FLOAT);
        if component_type == COMP_FLOAT {
            continue; // already float
        }
        let Some(kind) = accessor["type"].as_str().and_then(components_of) else {
            continue;
        };
        let count = accessor["count"].as_u64().unwrap_or(0) as usize;
        let Some(view_index) = accessor["bufferView"].as_u64() else {
            continue;
        };
        let Some(view) = views.get(view_index as usize) else {
            continue;
        };
        let normalized = accessor["normalized"].as_bool().unwrap_or(false);
        let size = component_size(component_type);
        let view_offset = view["byteOffset"].as_u64().unwrap_or(0) as usize;
        let accessor_offset = accessor["byteOffset"].as_u64().unwrap_or(0) as usize;
        // A view without an explicit stride is tightly packed.
        let stride = view["byteStride"]
            .as_u64()
            .map(|s| s as usize)
            .unwrap_or(size * kind);
        let base = view_offset + accessor_offset;

        let needed = base + stride * count.saturating_sub(1) + size * kind;
        if needed > bin.len() {
            bail!(
                "accessor {index}: reads past the buffer ({needed} > {})",
                bin.len()
            );
        }

        // Tightly packed f32 output, appended to the binary chunk.
        while !bin.len().is_multiple_of(4) {
            bin.push(0);
        }
        let out_offset = bin.len();
        bin.reserve(count * kind * 4);
        for element in 0..count {
            let start = base + element * stride;
            for component in 0..kind {
                let at = start + component * size;
                let value = component_to_f32(&bin[at..at + size], component_type, normalized);
                bin.extend_from_slice(&value.to_le_bytes());
            }
        }
        let out_length = count * kind * 4;

        // Point the accessor at the new packed view.
        let new_view = serde_json::json!({
            "buffer": 0,
            "byteOffset": out_offset,
            "byteLength": out_length,
        });
        let new_index = {
            let array = doc["bufferViews"]
                .as_array_mut()
                .context("bufferViews is an array")?;
            array.push(new_view);
            array.len() - 1
        };
        let accessor = doc["accessors"][index]
            .as_object_mut()
            .context("accessor is an object")?;
        accessor.insert("componentType".into(), serde_json::json!(COMP_FLOAT));
        accessor.insert("bufferView".into(), serde_json::json!(new_index));
        accessor.insert("byteOffset".into(), serde_json::json!(0));
        accessor.remove("normalized");
        converted = true;
    }
    Ok(converted)
}

// ------------------------------------------------------------ asset reading

use std::path::{Path, PathBuf};

use bevy::asset::io::{
    AssetReader, AssetReaderError, AssetSourceBuilder, AssetSourceId, ErasedAssetReader,
    PathStream, Reader, VecReader,
};
use bevy::prelude::*;
use bevy::tasks::ConditionalSendFuture;

/// Wraps another [`AssetReader`] and expands `EXT_meshopt_compression` in glTF
/// files as they are read.
///
/// Sitting at the reader means the rest of the engine is untouched: Bevy's own
/// glTF loader receives a plain GLB and handles quantization and KTX2 textures
/// as usual.
pub struct MeshoptAssetReader {
    inner: Box<dyn ErasedAssetReader>,
}

impl MeshoptAssetReader {
    /// Wraps `inner`.
    pub fn new(inner: Box<dyn ErasedAssetReader>) -> Self {
        Self { inner }
    }
}

/// Extensions worth inspecting; anything else is passed through untouched.
fn is_gltf(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("glb") || e.eq_ignore_ascii_case("gltf"))
}

impl AssetReader for MeshoptAssetReader {
    async fn read<'a>(&'a self, path: &'a Path) -> Result<impl Reader + 'a, AssetReaderError> {
        let mut reader = self.inner.read(path).await?;
        let mut bytes = Vec::new();
        reader.read_to_end(&mut bytes).await?;
        // Buffering every asset keeps one reader type; assets are read one at
        // a time and the largest GLB in the pool is a few megabytes.
        if !is_gltf(path) || !needs_rewrite(&bytes) {
            return Ok(VecReader::new(bytes));
        }
        match decode_glb(&bytes) {
            Ok(decoded) => Ok(VecReader::new(decoded)),
            Err(error) => {
                // A decode failure must not lose the asset: hand the original
                // bytes over and let the glTF loader report what it makes of
                // them.
                warn!("{}: meshopt decode failed ({error:#})", path.display());
                Ok(VecReader::new(bytes))
            }
        }
    }

    async fn read_meta<'a>(&'a self, path: &'a Path) -> Result<impl Reader + 'a, AssetReaderError> {
        self.inner.read_meta(path).await
    }

    fn read_directory<'a>(
        &'a self,
        path: &'a Path,
    ) -> impl ConditionalSendFuture<Output = Result<Box<PathStream>, AssetReaderError>> {
        self.inner.read_directory(path)
    }

    fn is_directory<'a>(
        &'a self,
        path: &'a Path,
    ) -> impl ConditionalSendFuture<Output = Result<bool, AssetReaderError>> {
        self.inner.is_directory(path)
    }
}

/// Registers the meshopt-aware reader over the default file source.
///
/// Must run **before** `AssetPlugin` is added, since the plugin snapshots the
/// registered sources when it builds.
pub fn register_asset_source(app: &mut App, asset_root: PathBuf) {
    let root = asset_root.to_string_lossy().into_owned();
    app.register_asset_source(
        AssetSourceId::Default,
        AssetSourceBuilder::new(move || {
            let file = bevy::asset::io::file::FileAssetReader::new(root.clone());
            Box::new(MeshoptAssetReader::new(Box::new(file))) as Box<dyn ErasedAssetReader>
        }),
    );
}

/// Loads a glTF with validation relaxed.
///
/// The `gltf` crate validates `extensionsRequired` against what *it* knows and
/// rejects the whole file otherwise — and with `KHR_texture_basisu` a texture's
/// source lives inside the extension block, so the core validator also reports
/// `textures[0].source: Missing data`. Both complaints are about the
/// validator's vocabulary, not the data: Bevy transcodes KTX2 fine with the
/// `basis-universal` feature, and quantized attributes are ordinary normalized
/// accessors. Bevy exposes `validate` for exactly this case.
pub fn load_gltf(server: &AssetServer, path: String) -> Handle<bevy::gltf::Gltf> {
    server
        .load_builder()
        .with_settings(|settings: &mut bevy::gltf::GltfLoaderSettings| settings.validate = false)
        .load(path)
}

/// The shared asset pool, wherever it currently lives.
///
/// It has moved between the VibeGame and Viber example trees, so both are
/// tried and the tests skip cleanly when neither is checked out.
pub fn shared_asset_pool() -> Option<std::path::PathBuf> {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    [
        root.join("examples/shared-assets/public"),
        root.join("../Viber/examples/shared-assets/public"),
        root.join("../VibeGame/examples/shared-assets/public"),
    ]
    .into_iter()
    .find(|candidate| candidate.is_dir())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_needs_decode_rejects_non_glb() {
        assert!(!needs_decode(b""));
        assert!(!needs_decode(b"not a glb at all"));
    }

    #[test]
    fn test_mode_and_filter_parsing() {
        assert_eq!(Mode::parse("ATTRIBUTES").unwrap(), Mode::Attributes);
        assert_eq!(Mode::parse("TRIANGLES").unwrap(), Mode::Triangles);
        assert_eq!(Mode::parse("INDICES").unwrap(), Mode::Indices);
        assert!(Mode::parse("SOMETHING").is_err());

        assert_eq!(Filter::parse("NONE").unwrap(), Filter::None);
        assert_eq!(Filter::parse("OCTAHEDRAL").unwrap(), Filter::Octahedral);
        assert_eq!(Filter::parse("QUATERNION").unwrap(), Filter::Quaternion);
        assert_eq!(Filter::parse("EXPONENTIAL").unwrap(), Filter::Exponential);
        assert!(Filter::parse("BILINEAR").is_err());
    }

    #[test]
    fn test_decode_stream_is_empty_for_empty_input() {
        let out = decode_stream(&[], 0, 12, Mode::Attributes, Filter::None).unwrap();
        assert!(out.is_empty());
        let out = decode_stream(&[1, 2, 3], 4, 0, Mode::Attributes, Filter::None).unwrap();
        assert!(out.is_empty(), "a zero stride decodes to nothing");
    }

    /// Round-trips real vertex data through the encoder the pipeline uses.
    #[test]
    fn test_decode_stream_round_trips_encoded_vertices() {
        #[repr(C)]
        #[derive(Clone, Copy, Default, PartialEq, Debug)]
        struct Vertex {
            position: [f32; 3],
            uv: [f32; 2],
        }
        let vertices: Vec<Vertex> = (0..64)
            .map(|i| Vertex {
                position: [i as f32, (i * 2) as f32, (i * 3) as f32],
                uv: [i as f32 * 0.5, i as f32 * 0.25],
            })
            .collect();
        let encoded = meshopt::encode_vertex_buffer(&vertices).expect("encodes");

        let stride = std::mem::size_of::<Vertex>();
        let decoded = decode_stream(
            &encoded,
            vertices.len(),
            stride,
            Mode::Attributes,
            Filter::None,
        )
        .expect("decodes");
        assert_eq!(decoded.len(), vertices.len() * stride);

        let raw = unsafe {
            std::slice::from_raw_parts(vertices.as_ptr().cast::<u8>(), vertices.len() * stride)
        };
        assert_eq!(decoded, raw, "decoded bytes match the original vertices");
    }

    /// Garbage in must be an error, not a panic or silent corruption.
    #[test]
    fn test_decode_stream_reports_bad_input() {
        let result = decode_stream(&[0xff; 8], 32, 12, Mode::Attributes, Filter::None);
        assert!(result.is_err(), "an undersized stream is rejected");
    }

    #[test]
    fn test_decode_glb_passes_through_uncompressed_files() {
        let doc = serde_json::json!({ "asset": { "version": "2.0" } });
        let glb = write_glb(&doc, &[1, 2, 3, 4]).expect("writes");
        assert!(!needs_decode(&glb));
        let out = decode_glb(&glb).expect("passes through");
        assert_eq!(out, glb);
    }

    #[test]
    fn test_write_glb_pads_and_sizes_chunks() {
        // A 1-byte payload and an odd-length JSON both need padding.
        let doc = serde_json::json!({ "a": 1 });
        let glb = write_glb(&doc, &[7]).expect("writes");
        assert_eq!(glb.len() % 4, 0, "the container is 4-byte aligned");
        let (json, bin) = parse_glb(&glb).expect("parses back");
        assert_eq!(bin.len() % 4, 0);
        assert_eq!(bin[0], 7);
        let parsed: serde_json::Value = serde_json::from_slice(json).expect("json survives");
        assert_eq!(parsed["a"], 1);
    }

    #[test]
    fn test_parse_glb_rejects_truncated_chunks() {
        let mut glb = write_glb(&serde_json::json!({ "a": 1 }), &[1, 2, 3, 4]).expect("writes");
        // Claim a chunk far longer than the file.
        glb[12..16].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(parse_glb(&glb).is_err());
    }

    /// Bevy needs POSITION/NORMAL/TANGENT as `f32`; a quantized pool asset
    /// must come out of the rewrite that way, and the extension must stop
    /// being advertised once the data no longer is quantized.
    ///
    /// This is the whole point of the de-quantizer: without it a quantized
    /// tangent reaches Bevy as `Snorm8x4`, gets dropped for not matching
    /// `Float32x4`, and the mesh panics on the missing attribute.
    #[test]
    fn test_quantized_pool_asset_comes_out_as_float() {
        let Some(pool) = shared_asset_pool().map(|p| p.join("assets/meshes")) else {
            eprintln!("shared-assets pool absent — skipping");
            return;
        };
        let (path, bytes) = find_with(&pool, |doc| {
            doc["extensionsUsed"]
                .as_array()
                .is_some_and(|used| used.iter().any(|e| e.as_str() == Some(QUANTIZATION)))
        })
        .expect("the pool ships quantized GLBs; finding none means the scan is broken");

        let decoded =
            decode_glb(&bytes).unwrap_or_else(|e| panic!("rewriting {}: {e:#}", path.display()));
        let doc = json_chunk(&decoded).expect("json chunk");

        // Every accessor feeding a float semantic must now be COMPONENT_FLOAT.
        let accessors = doc["accessors"].as_array().expect("accessors");
        let mut checked = 0;
        for mesh in doc["meshes"].as_array().into_iter().flatten() {
            for primitive in mesh["primitives"].as_array().into_iter().flatten() {
                for (semantic, index) in primitive["attributes"].as_object().expect("attributes") {
                    if !FLOAT_SEMANTICS.contains(&semantic.as_str()) {
                        continue;
                    }
                    let accessor = &accessors[index.as_u64().expect("index") as usize];
                    assert_eq!(
                        accessor["componentType"].as_u64(),
                        Some(COMP_FLOAT),
                        "{}: {semantic} is still quantized",
                        path.display()
                    );
                    assert!(
                        !accessor["normalized"].as_bool().unwrap_or(false),
                        "{}: {semantic} is still flagged normalized",
                        path.display()
                    );
                    checked += 1;
                }
            }
        }
        assert!(
            checked > 0,
            "the asset actually has float semantics to check"
        );

        let used: Vec<&str> = doc["extensionsUsed"]
            .as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();
        assert!(
            !used.contains(&QUANTIZATION),
            "quantization is no longer advertised: {used:?}"
        );
    }

    /// A decoded GLB must not *require* extensions the reader cannot name.
    ///
    /// Bevy refuses the whole file when `extensionsRequired` lists something
    /// it does not implement, so both meshopt (which is genuinely gone after
    /// decoding) and quantization (whose data core accessors already describe)
    /// have to leave that list.
    #[test]
    fn test_decoded_pool_asset_requires_no_unsupported_extensions() {
        let pool = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../Viber/examples/shared-assets/public/assets/meshes");
        if !pool.is_dir() {
            eprintln!("shared-assets pool absent — skipping");
            return;
        }
        let Some(path) = find_compressed(&pool) else {
            eprintln!("no compressed GLB — skipping");
            return;
        };
        let bytes = std::fs::read(&path).expect("reads");
        let decoded = decode_glb(&bytes).expect("decodes");
        let json = json_chunk(&decoded).expect("json chunk");

        let required: Vec<String> = json["extensionsRequired"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        assert!(
            !required.iter().any(|e| e == EXT),
            "meshopt is no longer required: {required:?}"
        );
        assert!(
            !required.iter().any(|e| e == QUANTIZATION),
            "quantization is no longer required: {required:?}"
        );
        // `extensionsUsed` keeps the honest record of quantization.
        let used: Vec<String> = json["extensionsUsed"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        assert!(
            !used.iter().any(|e| e == EXT),
            "meshopt leaves `extensionsUsed` too: {used:?}"
        );
    }

    /// A KTX2 texture keeps its image index inside `KHR_texture_basisu`, and
    /// `gltf::Texture::source()` unwraps the core field — so an unlifted file
    /// makes the loader panic rather than fail. The rewrite has to move it.
    #[test]
    fn test_basisu_source_is_lifted_into_the_core_field() {
        let Some(pool) = shared_asset_pool().map(|p| p.join("assets/meshes")) else {
            eprintln!("shared-assets pool absent — skipping");
            return;
        };
        // Find a GLB with a basisu texture.
        let (path, bytes) = find_with(&pool, |doc| {
            doc["textures"].as_array().is_some_and(|textures| {
                textures
                    .iter()
                    .any(|t| t.get("extensions").and_then(|e| e.get(BASISU)).is_some())
            })
        })
        .expect("the pool ships KTX2 textures; finding none means the scan is broken");

        assert!(
            needs_rewrite(&bytes),
            "{} declares KHR_texture_basisu, so it needs a rewrite",
            path.display()
        );
        let decoded =
            decode_glb(&bytes).unwrap_or_else(|e| panic!("rewriting {}: {e:#}", path.display()));
        let doc = json_chunk(&decoded).expect("json chunk");

        for texture in doc["textures"].as_array().expect("textures") {
            assert!(
                texture
                    .get("source")
                    .and_then(serde_json::Value::as_u64)
                    .is_some(),
                "every texture has a core `source` in {}: {texture}",
                path.display()
            );
            assert!(
                texture
                    .get("extensions")
                    .and_then(|e| e.get(BASISU))
                    .is_none(),
                "the basisu block is consumed: {texture}"
            );
        }
        let required: Vec<&str> = doc["extensionsRequired"]
            .as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();
        assert!(
            !required.contains(&BASISU),
            "basisu is no longer required: {required:?}"
        );
    }

    /// First GLB under `dir` whose JSON satisfies `predicate`.
    fn find_with(
        dir: &std::path::Path,
        predicate: impl Fn(&serde_json::Value) -> bool + Copy,
    ) -> Option<(PathBuf, Vec<u8>)> {
        for entry in std::fs::read_dir(dir).ok()?.flatten() {
            let path = entry.path();
            if path.is_dir() {
                if let Some(found) = find_with(&path, predicate) {
                    return Some(found);
                }
            } else if path.extension().is_some_and(|e| e == "glb") {
                let Ok(bytes) = std::fs::read(&path) else {
                    continue;
                };
                if json_chunk(&bytes).is_some_and(|doc| predicate(&doc)) {
                    return Some((path, bytes));
                }
            }
        }
        None
    }

    /// First compressed GLB under `dir`.
    fn find_compressed(dir: &std::path::Path) -> Option<PathBuf> {
        for entry in std::fs::read_dir(dir).ok()?.flatten() {
            let path = entry.path();
            if path.is_dir() {
                if let Some(found) = find_compressed(&path) {
                    return Some(found);
                }
            } else if path.extension().is_some_and(|e| e == "glb")
                && std::fs::read(&path).is_ok_and(|b| needs_decode(&b))
            {
                return Some(path);
            }
        }
        None
    }

    /// Parses the JSON chunk of a GLB.
    fn json_chunk(bytes: &[u8]) -> Option<serde_json::Value> {
        let len = u32::from_le_bytes(bytes.get(12..16)?.try_into().ok()?) as usize;
        serde_json::from_slice(bytes.get(20..20 + len)?).ok()
    }

    use bevy::asset::io::file::FileAssetReader;

    /// The reader must actually decode when it is handed a compressed asset —
    /// the decoder passing its own unit tests is not enough if the plumbing
    /// hands it different bytes.
    #[test]
    fn test_reader_decodes_a_pool_asset() {
        let Some(pool) = shared_asset_pool() else {
            eprintln!("shared-assets pool absent — skipping");
            return;
        };
        // Find a compressed GLB and remember its path relative to the root.
        let meshes = pool.join("assets/meshes");
        let relative = first_compressed(&meshes, &pool).expect(
            "the pool ships meshopt-compressed GLBs; finding none means the detector is broken",
        );

        let reader = MeshoptAssetReader::new(Box::new(FileAssetReader::new(
            pool.to_string_lossy().into_owned(),
        )));
        let bytes = bevy::tasks::block_on(async {
            let mut r = AssetReader::read(&reader, &relative).await.expect("reads");
            let mut out = Vec::new();
            r.read_to_end(&mut out).await.expect("reads to end");
            out
        });

        assert!(!bytes.is_empty(), "the reader returned bytes");
        assert!(
            !needs_decode(&bytes),
            "{}: the reader returned still-compressed bytes",
            relative.display()
        );
    }

    /// First compressed GLB under `dir`, as a path relative to `root`.
    fn first_compressed(dir: &std::path::Path, root: &std::path::Path) -> Option<PathBuf> {
        let entries = std::fs::read_dir(dir).ok()?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                if let Some(found) = first_compressed(&path, root) {
                    return Some(found);
                }
            } else if path.extension().is_some_and(|e| e == "glb")
                && std::fs::read(&path).is_ok_and(|b| needs_decode(&b))
            {
                return path.strip_prefix(root).ok().map(Path::to_path_buf);
            }
        }
        None
    }
}
