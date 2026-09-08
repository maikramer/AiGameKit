// Shared WGSL prelude — concatenated (at runtime) in front of every shader body.
// The Rust mirror of `Params` is `crate::preset::PresetParams` (#[repr(C)], 160
// bytes) and of `FilterParams` is `crate::preset::FilterParams` (32 bytes).
// Layout is guarded by `test_preset_params_size` — keep all three in sync.

struct Params {
    // === Height (pyramid + guided filter) ===
    height_sigma_base: f32,       // 1  σ0 of pyramid level 0
    height_pyramid_levels: f32,   // 2  level count 1..7 (σk = σ0·2^k)
    height_contrast: f32,         // 3
    guided_radius: f32,           // 4  guided-filter window radius (px)
    guided_eps: f32,              // 5  variance threshold ε (edge preservation)
    detail_mix: f32,              // 6  weight of detail layer in final height
    // === Normal ===
    normal_strength: f32,         // 7
    normal_flip_y: u32,           // 8  0 = OpenGL (Y-up), 1 = DirectX (Y-down)
    normal_operator: u32,         // 9  0 = Sobel, 1 = Scharr (÷4 normalised)
    normal_slope_z: f32,          // 10 z component of the normal (derivative scale)
    normal_prefilter_sigma: f32,  // 11 σ of pre-gradient blur; 0 = off
    // === Metallic ===
    metallic_scale: f32,               // 12
    metallic_local_variance_factor: f32, // 13
    metallic_gray_world: u32,          // 14 apply gray-world gains before detection
    gray_gain_r: f32,                  // 15 CPU-computed channel gains
    gray_gain_g: f32,                  // 16
    gray_gain_b: f32,                  // 17
    metallic_specular_gain: f32,       // 18 specular-evidence weight (0 = off)
    // === Smoothness ===
    smoothness_base: f32,              // 19
    smoothness_metallic_boost: f32,    // 20
    smoothness_roughness_factor: f32,  // 21
    roughness_slope_scale: f32,        // 22 GGX slope-variance proxy gain
    roughness_slope_mix: f32,          // 23 0 = disabled
    // === Edge ===
    edge_contrast: f32,           // 24
    // === AO ===
    ao_depth_scale: f32,          // 25
    ao_directions: f32,           // 26 ray-march directions (8/16/32)
    ao_steps: f32,                // 27 geometric steps per direction
    ao_radius: f32,               // 28 max radius (px)
    ao_macro_mix: f32,            // 29 blend of macro AO (base layer, radius ×4)
    // === Curvature ===
    curvature_levels: f32,        // 30 LoG levels 1..3 (σ = 2^l)
    curvature_gain: f32,          // 31
    // === Intrinsic (optional ML pre-pass) ===
    shading_height_mix: f32,      // 32 high-freq shading → height; 0 = off
    // === Mode flags ===
    seamless: u32,                // 33 0 = clamp, 1 = wrap
    seam_band: f32,               // 34 make-seamless band width (px)
    _pad0: f32,                   // 35
    _pad1: f32,                   // 36
    _pad2: f32,                   // 37
    _pad3: f32,                   // 38
    _pad4: f32,                   // 39
    _pad5: f32,                   // 40
}

// Uniform for the generic separable filter passes (blur/scale/blend). Bound at
// @group(1) @binding(0) — same layout slot as `Params`, different buffer.
struct FilterParams {
    radius: f32,    // kernel radius in px (box window or gaussian ⌈3σ⌉)
    sigma: f32,     // gaussian σ (ignored for box)
    kind: u32,      // 0 = box, 1 = gaussian
    seamless: u32,  // wrap sampling at borders
    weight: f32,    // blend/scale weight for the unary passes
    _p0: f32,
    _p1: f32,
    _p2: f32,
}

fn wrap_or_clamp(coords: vec2<i32>, dims: vec2<u32>, seamless: u32) -> vec2<i32> {
    let d = vec2<i32>(dims);
    if (seamless == 1u) {
        return ((coords % d) + d) % d;
    }
    return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));
}

fn luma709(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.2126, 0.7152, 0.0722));
}
