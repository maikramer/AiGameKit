import * as THREE from 'three';
import { logger } from '../../core/utils/logger';

/**
 * Percentage-Closer Soft Shadows (PCSS) for directional lights.
 *
 * three.js 0.185 ships a Vogel-disk PCF (5 samples, ~20 effective taps) for
 * directional shadows under `SHADOWMAP_TYPE_PCF`. PCF gives a *uniform* blur
 * radius everywhere — the shadow is equally soft near the contact point and
 * far from the caster, which reads as wrong (real penumbra widens with
 * distance from the blocker).
 *
 * Classic PCSS estimates the average blocker depth with a search pass, but
 * three's PCF path binds the shadow map as a `sampler2DShadow` — a
 * compare-only sampler that returns 0/1 test results, never raw depths, so
 * the textbook blocker average is unavailable. Instead we estimate blocker
 * *proximity* with a dual-reference trick: run the same comparison disk at
 * `zReceiver` and at `zReceiver - DELTA`. Blockers hugging the receiver fail
 * the shifted test (`occFar << occNear` → sharp contact shadow); blockers far
 * above still pass it (`occFar ≈ occNear` → wide penumbra). The occlusion
 * ratio drives a variable-radius Vogel PCF, giving hard contact shadows that
 * soften with caster distance — sunlight-through-leaves behaviour.
 *
 * Implementation note: three removed `examples/jsm/shadows/PCSSShadowPlugin`
 * during the `renderers/common` migration, so this is a direct
 * `THREE.ShaderChunk.shadowmap_pars_fragment` replacement performed once at
 * renderer setup. It only patches the directional-light `getShadow` overload
 * (the chunk guards it under `#if defined( SHADOWMAP_TYPE_PCF )`) and reuses
 * the chunk's own `vogelDiskSample` / `interleavedGradientNoise` helpers.
 * Point/spot lights keep their existing PCF path. Works with CSM too: the
 * cascade lights sample through this same chunk. The patch is idempotent.
 */

let pcssApplied = false;

// PCSS tuning. Conservative defaults read well on a 2048 shadow map and stay
// stable at 4096.
// Leaner than classic 12/16 — contact penumbra kept, ~45% fewer shadow taps
// per lit fragment vs classic (blocker disk is dual-reference so ×2).
const PCSS_BLOCKER_SAMPLES = 6; // dual-reference occlusion estimate disk
const PCSS_PCF_SAMPLES = 8; // final variable-radius filter disk
const PCSS_SEARCH_RADIUS_TEXELS = 5.0; // blocker-estimate disk, in texels
const PCSS_MIN_RADIUS = 0.5; // penumbra floor (texels × shadowRadius)
const PCSS_MAX_RADIUS = 5.0; // penumbra ceiling (texels × shadowRadius)
// Window-space depth offset separating "contact" from "distant" blockers.
// ~1% of the light frustum depth range; with CSM each cascade has its own
// (shorter) range, so near-camera cascades resolve contact hardening finer.
const PCSS_DEPTH_DELTA = 0.01;

/**
 * Replacement for the directional-light `getShadow` overload. Mirrors the
 * stock function's frustum test and `mix(1.0, shadow, shadowIntensity)`
 * contract; only the sampling strategy differs.
 */
const PCSS_OVERLOAD = /* glsl */ `#if defined( SHADOWMAP_TYPE_PCF )

		float getShadow( sampler2DShadow shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord ) {

			float shadow = 1.0;

			shadowCoord.xyz /= shadowCoord.w;
			shadowCoord.z += shadowBias;

			bool inFrustum = shadowCoord.x >= 0.0 && shadowCoord.x <= 1.0 && shadowCoord.y >= 0.0 && shadowCoord.y <= 1.0;
			bool frustumTest = inFrustum && shadowCoord.z <= 1.0;

			if ( frustumTest ) {

				vec2 texelSize = vec2( 1.0 ) / shadowMapSize;
				float phi = interleavedGradientNoise( gl_FragCoord.xy ) * PI2;

				// PCSS blocker-proximity estimate (see pcss-shadow.ts header):
				// occlusion compared at two reference depths stands in for the
				// average blocker depth a compare-only sampler cannot provide.
				float searchRadius = ${PCSS_SEARCH_RADIUS_TEXELS.toFixed(1)} * max( shadowRadius, 1.0 ) * texelSize.x;
				float occNear = 0.0;
				float occFar = 0.0;
				for ( int i = 0; i < ${PCSS_BLOCKER_SAMPLES}; i ++ ) {
					vec2 offset = vogelDiskSample( i, ${PCSS_BLOCKER_SAMPLES}, phi ) * searchRadius;
					occNear += 1.0 - texture( shadowMap, vec3( shadowCoord.xy + offset, shadowCoord.z ) );
					occFar += 1.0 - texture( shadowMap, vec3( shadowCoord.xy + offset, shadowCoord.z - ${PCSS_DEPTH_DELTA.toFixed(3)} ) );
				}
				occNear /= float( ${PCSS_BLOCKER_SAMPLES} );
				occFar /= float( ${PCSS_BLOCKER_SAMPLES} );

				if ( occNear > 0.0 ) {

					float penumbra = clamp( occFar / occNear, 0.0, 1.0 );
					float filterRadius = mix( ${PCSS_MIN_RADIUS.toFixed(2)}, ${PCSS_MAX_RADIUS.toFixed(2)}, penumbra ) * max( shadowRadius, 1.0 ) * texelSize.x;

					shadow = 0.0;
					for ( int i = 0; i < ${PCSS_PCF_SAMPLES}; i ++ ) {
						vec2 offset = vogelDiskSample( i, ${PCSS_PCF_SAMPLES}, phi ) * filterRadius;
						shadow += texture( shadowMap, vec3( shadowCoord.xy + offset, shadowCoord.z ) );
					}
					shadow /= float( ${PCSS_PCF_SAMPLES} );

				}

			}

			return mix( 1.0, shadow, shadowIntensity );

		}`;

/**
 * Replace the directional-light `getShadow` overload in the
 * `shadowmap_pars_fragment` shader chunk with the PCSS version. The
 * replacement string-substitutes from the `#if defined( SHADOWMAP_TYPE_PCF )`
 * guard (which only wraps the directional overload) up to the
 * `#elif defined( SHADOWMAP_TYPE_VSM )` guard, so the VSM/BASIC overloads and
 * the point-light path are untouched.
 *
 * Idempotent: a second call is a no-op. Wrapped in try/catch so a malformed
 * chunk (e.g. on a future three.js version) can't break renderer creation — it
 * logs a warning and falls back to the stock PCF path.
 */
let pcssFailed = false;

export function applyPcssShadowPatch(): void {
  if (pcssApplied || pcssFailed) return;
  try {
    const original = THREE.ShaderChunk.shadowmap_pars_fragment;
    if (typeof original !== 'string' || original.length === 0) {
      pcssFailed = true;
      logger.warn(
        '[pcss] shadowmap_pars_fragment chunk not found — PCSS patch skipped, falling back to PCF'
      );
      return;
    }

    // Whitespace inside the chunk varies between three's source and the
    // packaged build (blank lines are stripped), so locate the directional
    // overload with a whitespace-tolerant regex: the `SHADOWMAP_TYPE_PCF`
    // guard immediately followed by the `sampler2DShadow` getShadow signature
    // (the other occurrence of that guard wraps a uniform declaration).
    const dirOverloadStart =
      /#if defined\( SHADOWMAP_TYPE_PCF \)\s*\n\s*float getShadow\( sampler2DShadow/.exec(
        original
      );
    const vsmGuard = '#elif defined( SHADOWMAP_TYPE_VSM )';

    const startIdx = dirOverloadStart?.index ?? -1;
    if (startIdx === -1) {
      pcssFailed = true;
      logger.warn(
        '[pcss] PCF directional signature not found in shader chunk — PCSS patch skipped'
      );
      return;
    }
    const endIdx = original.indexOf(vsmGuard, startIdx);
    if (endIdx === -1) {
      pcssFailed = true;
      logger.warn(
        '[pcss] could not locate end of PCF directional overload — PCSS patch skipped'
      );
      return;
    }

    THREE.ShaderChunk.shadowmap_pars_fragment =
      original.slice(0, startIdx) +
      PCSS_OVERLOAD +
      '\n\t' +
      original.slice(endIdx);
    pcssApplied = true;
    logger.info(
      `[pcss] Applied PCSS shadow patch (blocker=${PCSS_BLOCKER_SAMPLES}x2, pcf=${PCSS_PCF_SAMPLES} samples)`
    );
  } catch (err) {
    pcssFailed = true;
    logger.warn('[pcss] Failed to apply PCSS patch — falling back to PCF', err);
  }
}
