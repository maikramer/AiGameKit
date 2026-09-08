// Ambient occlusion (F3): horizon-based ray marching over the heightfield
// (2D adaptation of Bavoil & Sander, HBAO, NVIDIA 2008) with a multi-scale
// blend — fine AO from the full height, macro AO from the guided-filter base
// layer at 4× radius.

@group(0) @binding(0)
var height_texture: texture_2d<f32>;

@group(0) @binding(1)
var base_texture: texture_2d<f32>;

@group(0) @binding(2)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_tex(tex: texture_2d<f32>, coords: vec2<i32>) -> f32 {
    let dims = textureDimensions(tex);
    let c = wrap_or_clamp(coords, dims, params.seamless);
    return textureLoad(tex, c, 0).r;
}

// Occlusion for one heightfield sample, ray-marched over `dirs` directions
// with geometric step spacing (near samples dense, far samples sparse).
fn horizon_occlusion(tex: texture_2d<f32>, center: vec2<i32>, center_h: f32, radius: f32) -> f32 {
    let dirs = clamp(i32(params.ao_directions), 4, 32);
    let steps = clamp(i32(params.ao_steps), 4, 64);

    var occ = 0.0;
    for (var d = 0; d < dirs; d++) {
        let theta = (f32(d) + 0.5) * 6.2831853 / f32(dirs);
        let dir = vec2<f32>(cos(theta), sin(theta));

        var horizon = 0.0;
        for (var s = 1; s <= steps; s++) {
            // Geometric progression: sample distance ∝ (s/steps)².
            let t = radius * f32(s * s) / f32(steps * steps);
            let dist = max(t, 1.0);
            let p = center + vec2<i32>(dir * dist);

            let dh = sample_tex(tex, p) - center_h;
            // Tangent of the horizon angle; height scale is the world-space
            // height of one unit of heightfield value.
            let tangent = dh * params.ao_depth_scale / dist;
            let angle = atan(tangent);
            horizon = max(horizon, angle);

            // Early out: nothing higher can matter past π/2.
            if (horizon > 1.5707) {
                break;
            }
        }

        // HBAO weighting: sin(horizon) is the cosine-lobe occlusion of the
        // uniform hemispherical ambient; negative horizons contribute 0.
        occ += max(sin(horizon), 0.0);
    }

    return occ / f32(dirs);
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let center_h = sample_tex(height_texture, coords);
    let radius = max(params.ao_radius, 1.0);

    // Fine AO: full height (base + detail) at nominal radius.
    let occ_fine = horizon_occlusion(height_texture, coords, center_h, radius);

    // Macro AO: edge-preserved base layer at 4× radius — large-scale cavities.
    var ao = 1.0 - clamp(occ_fine, 0.0, 1.0);

    let macro_mix = clamp(params.ao_macro_mix, 0.0, 1.0);
    if (macro_mix > 0.0) {
        let center_b = sample_tex(base_texture, coords);
        let occ_macro = horizon_occlusion(base_texture, coords, center_b, radius * 4.0);
        let ao_macro = 1.0 - clamp(occ_macro, 0.0, 1.0);
        ao = mix(ao, ao_macro, macro_mix);
    }

    ao = clamp(ao, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(ao, ao, ao, 1.0));
}
