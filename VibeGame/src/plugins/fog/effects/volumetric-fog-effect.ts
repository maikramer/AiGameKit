import { BlendFunction, Effect, EffectAttribute } from 'postprocessing';
import { Uniform, Vector3 } from 'three';

const fragmentShader = /* glsl */ `
uniform vec3 fogColor;
uniform float density;
uniform float heightFalloff;
uniform float baseHeight;
uniform float volumetricStrength;
uniform float noiseScale;

float hash(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void mainImage(const in vec4 inputColor, const in vec2 uv, out vec4 outputColor) {
    float depth = readDepth(uv);
    float viewZ = getViewZ(depth);

    float fogAmount = 1.0 - exp(-density * viewZ);

    float heightProxy = (0.5 - uv.y) * viewZ;
    float heightFog = exp(-heightFalloff * max(0.0, heightProxy - baseHeight));

    float noise = hash(uv * noiseScale * 100.0);
    float noiseFactor = 0.9 + 0.2 * noise;

    float totalFog = fogAmount * volumetricStrength * heightFog * noiseFactor;
    outputColor = vec4(mix(inputColor.rgb, fogColor, clamp(totalFog, 0.0, 1.0)), inputColor.a);
}
`;

export interface VolumetricFogEffectOptions {
    fogColor?: [number, number, number];
    density?: number;
    heightFalloff?: number;
    baseHeight?: number;
    volumetricStrength?: number;
    noiseScale?: number;
    quality?: 'low' | 'medium' | 'high';
}

export class VolumetricFogEffect extends Effect {
    constructor(options?: VolumetricFogEffectOptions) {
        const quality = options?.quality ?? 'medium';
        super('VolumetricFogEffect', fragmentShader, {
            blendFunction: quality === 'low' ? BlendFunction.SKIP : BlendFunction.NORMAL,
            attributes: EffectAttribute.DEPTH,
            uniforms: new Map<string, Uniform>([
                ['fogColor', new Uniform(new Vector3(...(options?.fogColor ?? [0.533, 0.6, 0.667])))],
                ['density', new Uniform(options?.density ?? 0.015)],
                ['heightFalloff', new Uniform(options?.heightFalloff ?? 1.0)],
                ['baseHeight', new Uniform(options?.baseHeight ?? 0)],
                ['volumetricStrength', new Uniform(options?.volumetricStrength ?? 0.5)],
                ['noiseScale', new Uniform(options?.noiseScale ?? 1.0)],
            ]),
        });
    }

    get fogColor(): [number, number, number] {
        const v = this.uniforms.get('fogColor')!.value as Vector3;
        return [v.x, v.y, v.z];
    }

    set fogColor(value: [number, number, number]) {
        (this.uniforms.get('fogColor')!.value as Vector3).set(value[0], value[1], value[2]);
    }

    get density(): number {
        return this.uniforms.get('density')!.value;
    }

    set density(value: number) {
        this.uniforms.get('density')!.value = value;
    }

    get heightFalloff(): number {
        return this.uniforms.get('heightFalloff')!.value;
    }

    set heightFalloff(value: number) {
        this.uniforms.get('heightFalloff')!.value = value;
    }

    get baseHeight(): number {
        return this.uniforms.get('baseHeight')!.value;
    }

    set baseHeight(value: number) {
        this.uniforms.get('baseHeight')!.value = value;
    }

    get volumetricStrength(): number {
        return this.uniforms.get('volumetricStrength')!.value;
    }

    set volumetricStrength(value: number) {
        this.uniforms.get('volumetricStrength')!.value = value;
    }

    get noiseScale(): number {
        return this.uniforms.get('noiseScale')!.value;
    }

    set noiseScale(value: number) {
        this.uniforms.get('noiseScale')!.value = value;
    }
}
