// Viber procedural sky — atmospheric gradient, sun disc + mie glow, FBM
// clouds drifting with the wind, moon and stars at night.
//
// The dome is an inverted sphere centered on the camera; the fragment
// direction is `normalize(world_position - view_position)`.

#import bevy_render::view::View
#import bevy_pbr::forward_io::VertexOutput

@group(0) @binding(0) var<uniform> view: View;
// NOTE: binding (2,0) MUST be `var<storage, read>` — the AsBindGroup derive
// in Bevy 0.19 gives the pipeline a Storage-LOAD layout entry for this
// buffer; a `var<uniform>` declaration fails validation at runtime with
// "doesn't match the shader Uniform".
@group(2) @binding(0) var<storage, read> sky: SkyUniform;

struct SkyUniform {
    sun_dir: vec3<f32>,
    time: f32,
    night: f32,
    turbidity: f32,
    rayleigh: f32,
    mie: f32,
    mie_g: f32,
    sun_intensity: f32,
    cloud_coverage: f32,
    cloud_density: f32,
    cloud_elevation: f32,
    wind: vec2<f32>,
};

@fragment
fn fragment(mesh: VertexOutput) -> @location(0) vec4<f32> {
    let cam_pos = view.world_from_view[3].xyz;
    let dir = normalize(mesh.world_position.xyz - cam_pos);
    let sun = normalize(sky.sun_dir);
    let up = clamp(dir.y, -1.0, 1.0);
    let sun_h = clamp(sun.y, -1.0, 1.0);

    // ── Day/night weights ────────────────────────────────────────────
    let day = clamp(1.0 - sky.night, 0.0, 1.0);
    let sunset = exp(-abs(sun_h) * 5.0) * day; // sol perto do horizonte

    // ── Base gradient ────────────────────────────────────────────────
    let zenith_day = vec3(0.12, 0.34, 0.75);
    let horizon_day = vec3(0.62, 0.78, 0.96);
    let zenith_night = vec3(0.012, 0.018, 0.05);
    let horizon_night = vec3(0.04, 0.06, 0.11);

    let zenith = mix(zenith_night, zenith_day, day);
    let horizon = mix(horizon_night, horizon_day, day);

    let t = pow(clamp(1.0 - up, 0.0, 1.0), 2.2);
    var color = mix(zenith, horizon, t);

    // ── Sunset warmth perto do sol, abaixo do horizonte-alto ────────
    let sun_az = normalize(vec2(sun.x, sun.z) + vec2(1e-5));
    let dir_az = normalize(vec2(dir.x, dir.z) + vec2(1e-5));
    let toward_sun = max(dot(dir_az, sun_az), 0.0);
    let warm_band = smoothstep(0.45, -0.1, up) * sunset * pow(toward_sun, 2.0);
    color += vec3(0.95, 0.35, 0.08) * warm_band * 0.9;
    color += vec3(0.9, 0.55, 0.15) * sunset * 0.25 * day;

    // ── Sol: disco + mie glow ────────────────────────────────────────
    let d = clamp(dot(dir, sun), 0.0, 1.0);
    let disc = smoothstep(0.99955, 0.99985, d);
    let glow = pow(d, mix(1200.0, 250.0, clamp(sky.mie_g, 0.0, 1.0)));
    let sun_col = mix(
        vec3(1.0, 0.55, 0.2),   // horizonte: âmbar
        vec3(1.0, 0.97, 0.9),   // alto: quase branco
        clamp(sun_h * 2.5, 0.0, 1.0),
    );
    color += sun_col * disc * sky.sun_intensity;
    color += sun_col * glow * sky.mie * 400.0 * day;

    // ── Lua: disco oposto ao sol ─────────────────────────────────────
    let moon = normalize(-sun);
    let md = clamp(dot(dir, moon), 0.0, 1.0);
    let moon_disc = smoothstep(0.99975, 0.99993, md);
    let moon_glow = pow(md, 600.0) * 0.4;
    let moon_col = vec3(0.86, 0.9, 1.0);
    color += (moon_disc * moon_col + moon_glow * moon_col * 0.35) * sky.night;

    // ── Estrelas: hash grid na direção, só de noite ─────────────────
    let sp = dir * 220.0;
    let cell = floor(sp);
    let h = hash31(cell);
    let twinkle = 0.6 + 0.4 * sin(sky.time * 2.0 + h * 40.0);
    let star = step(0.9985, hash31(cell + vec3<f32>(17.0))) * sky.night * twinkle;
    color += vec3(0.9, 0.93, 1.0) * star;

    // ── Nuvens: FBM num plano projetado, deslocadas pelo vento ──────
    // Frequência calibrada para nuvens de 3-8°: a versão anterior amostrava
    // 0-6 unidades de ruído no céu VISÍVEL inteiro, o que gerava blobs do
    // tamanho de continentes que cobriam o zénite ao passar (o céu "piscava"
    // branco↔azul) e uma névoa branca permanente de ~20-30% de alpha.
    if (dir.y > 0.02) {
        // cloud_elevation controla a altura do plano: mais alto = nuvens
        // mais pequenas/distantes. Perto do zénite a projeção ao plano
        // colapsa (dir.xz → 0), por isso misturamos com um mapeamento
        // tangencial — sem singularidade, sem nuvem congelada no topo.
        let height = mix(0.7, 2.6, sky.cloud_elevation);
        let plane = dir.xz / (dir.y + 0.12) * 3.0 * height;
        let tangential = dir.xz * 4.0;
        let uv = mix(plane, tangential, smoothstep(0.55, 0.95, dir.y))
            + sky.wind * sky.time * 0.004;
        let n = fbm(uv);
        let threshold = mix(0.74, 0.30, clamp(sky.cloud_coverage, 0.0, 1.0));
        let cov = smoothstep(threshold, threshold + 0.20, n);
        let alpha = cov * mix(0.5, 1.05, clamp(sky.cloud_density, 0.0, 1.0))
            * smoothstep(0.02, 0.12, dir.y);

        // Sombreamento interno: densidade escurece a base; uma segunda
        // amostra deslocada na direção do sol acende o lado virado a ele.
        let lit = fbm(uv + sun_az * 0.6);
        let shade = mix(0.58, 1.02, n) + (lit - n) * 0.8;
        var cloud_col = vec3(1.04, 1.03, 1.0) * shade * (0.35 + 0.65 * day);
        // Nuvens pegam o tom do pôr-do-sol.
        cloud_col += vec3(0.9, 0.35, 0.1) * warm_band * 0.8;
        cloud_col = mix(cloud_col * vec3(0.25, 0.3, 0.45), cloud_col, day);

        color = mix(color, cloud_col, clamp(alpha, 0.0, 1.0));
    }

    return vec4(color, 1.0);
}

// ── Noise helpers ────────────────────────────────────────────────────
fn hash21(p: vec2<f32>) -> f32 {
    var q = fract(p * vec2(123.34, 456.21));
    q += dot(q, q + 45.32);
    return fract(q.x * q.y);
}

fn hash31(p: vec3<f32>) -> f32 {
    var q = fract(p * 0.1031);
    q += dot(q, q.yzx + 33.33);
    return fract((q.x + q.y) * q.z);
}

fn value_noise(p: vec2<f32>) -> f32 {
    let i = floor(p);
    let f = fract(p);
    let u = f * f * (3.0 - 2.0 * f);
    let a = hash21(i);
    let b = hash21(i + vec2(1.0, 0.0));
    let c = hash21(i + vec2(0.0, 1.0));
    let d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

fn fbm(p: vec2<f32>) -> f32 {
    var v = 0.0;
    var amp = 0.5;
    var pp = p;
    // Rotação por oitava: sem ela as oitavas alinham-se à grelha do value
    // noise e as nuvens ganham artefactos quadrados axis-aligned.
    let rot = mat2x2<f32>(0.737, 0.676, -0.676, 0.737);
    for (var i = 0; i < 5; i++) {
        v += amp * value_noise(pp);
        pp = rot * pp * 2.03 + vec2(11.7, 5.3);
        amp *= 0.5;
    }
    return v;
}
