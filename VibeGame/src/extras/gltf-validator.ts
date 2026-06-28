import { logger } from '../core/utils/logger';

/**
 * Runtime GLB/glTF validation built on `@gltf-transform/core` + `@gltf-transform/functions`.
 *
 * Important: glTF Transform is *not* a spec-compliance validator (the canonical one is the
 * separate `gltf-validator` WASM package, which VibeGame does not bundle). This module runs a
 * focused set of structural + spec checks on the parsed glTF JSON (via
 * `PlatformIO.binaryToJSON`) and folds in `@gltf-transform/functions`' `inspect` advisory
 * warnings, mapping everything to a uniform {@link GltfValidationReport} with stable issue
 * codes and JSON pointer paths. For exhaustive Khronos spec validation, run `gamedev-lab check
 * glb` or the `gltf-validator` CLI on the asset.
 *
 * The glTF-transform modules are imported lazily so users who never validate pay no bundle cost.
 */

export type GltfIssueSeverity = 'error' | 'warning' | 'info';

export interface GltfValidationIssue {
  /** Stable machine-readable code, e.g. ``ASSET_VERSION_MISSING``. */
  code: string;
  message: string;
  /** RFC-6901 JSON pointer into the glTF document, e.g. ``/asset/version``. */
  pointer: string;
  severity: GltfIssueSeverity;
}

export interface GltfValidationReport {
  /** ``true`` when no error-severity issues were found. */
  valid: boolean;
  errors: GltfValidationIssue[];
  warnings: GltfValidationIssue[];
  infos: GltfValidationIssue[];
  /** All issues, sorted by severity (error → warning → info) then pointer. */
  issues: GltfValidationIssue[];
  byteLength: number;
  /** ``model/gltf-binary`` for GLB containers, ``model/gltf+json`` for bare JSON. */
  mimeType: string;
}

export interface ValidateGltfOptions {
  /**
   * When ``true`` (default), also fold in `inspect` advisory warnings/errors (duplicate
   * materials, unused textures, …). Set to ``false`` to skip building a full Document.
   */
  includeAdvisory?: boolean;
}

const VERSION_PATTERN = /^\d+\.\d+$/;
const SEVERITY_ORDER: Record<GltfIssueSeverity, number> = {
  error: 0,
  warning: 1,
  info: 2,
};

function push(
  issues: GltfValidationIssue[],
  severity: GltfIssueSeverity,
  code: string,
  message: string,
  pointer: string
): void {
  issues.push({ code, message, pointer, severity });
}

/** Decode bytes as UTF-8 text, or return ``null`` if the payload is binary/garbage. */
function tryDecodeText(bytes: Uint8Array): string | null {
  let s = '';
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i];
    if (b === 0) return null; // NUL → binary payload
    if (b < 0x09 || (b > 0x0d && b < 0x20)) return null; // non-printable control char
    s += String.fromCharCode(b);
  }
  try {
    return decodeURIComponent(escape(s));
  } catch {
    return null;
  }
}

async function resolveBytes(
  input: ArrayBuffer | Uint8Array | string
): Promise<Uint8Array> {
  if (typeof input === 'string') {
    if (typeof fetch !== 'function') {
      throw new Error(
        'validateGltf: URL input requires a global fetch implementation.'
      );
    }
    const res = await fetch(input);
    if (!res.ok) {
      throw new Error(
        `validateGltf: fetch failed for ${input} (HTTP ${res.status})`
      );
    }
    const ab = await res.arrayBuffer();
    return new Uint8Array(ab);
  }
  if (input instanceof ArrayBuffer) {
    return new Uint8Array(input);
  }
  if (ArrayBuffer.isView(input)) {
    const view = input as Uint8Array;
    return new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
  }
  throw new Error(
    'validateGltf: input must be a URL string, ArrayBuffer, or Uint8Array.'
  );
}

async function makeIO(): Promise<unknown> {
  const inBrowser =
    typeof window !== 'undefined' &&
    typeof (globalThis as { fetch?: unknown }).fetch === 'function';
  const core = await import('@gltf-transform/core');
  return inBrowser ? new core.WebIO() : new core.NodeIO();
}

interface GltfJsonShape {
  asset?: { version?: unknown };
  scene?: unknown;
  scenes?: unknown[];
  nodes?: unknown[];
  meshes?: { primitives?: { attributes?: Record<string, unknown> }[] }[];
  accessors?: { count?: unknown; bufferView?: unknown; sparse?: unknown }[];
  bufferViews?: { buffer?: unknown }[];
  buffers?: unknown[];
}

/** Focused spec + structural checks on the raw glTF JSON object. */
function checkSpec(json: GltfJsonShape, issues: GltfValidationIssue[]): void {
  const asset = json.asset;
  const version =
    asset && typeof (asset as { version?: unknown }).version === 'string'
      ? (asset as { version: string }).version
      : null;
  if (!version) {
    push(
      issues,
      'error',
      'ASSET_VERSION_MISSING',
      'glTF asset.version is required (e.g. "2.0").',
      '/asset/version'
    );
  } else if (!VERSION_PATTERN.test(version)) {
    push(
      issues,
      'error',
      'ASSET_VERSION_FORMAT',
      `asset.version "${version}" must match major.minor (e.g. "2.0").`,
      '/asset/version'
    );
  }

  if (Array.isArray(json.scenes) && json.scenes.length > 0) {
    if (
      typeof json.scene === 'number' &&
      (json.scene < 0 || json.scene >= json.scenes.length)
    ) {
      push(
        issues,
        'error',
        'SCENE_INDEX_OUT_OF_RANGE',
        `root scene index ${json.scene} is out of range (0..${json.scenes.length - 1}).`,
        '/scene'
      );
    }
  } else if (
    (Array.isArray(json.nodes) && json.nodes.length > 0) ||
    (Array.isArray(json.meshes) && json.meshes.length > 0)
  ) {
    push(
      issues,
      'warning',
      'SCENES_EMPTY',
      'Document defines nodes/meshes but no scenes — nothing will render.',
      '/scenes'
    );
  }

  const buffersLen = Array.isArray(json.buffers) ? json.buffers.length : 0;
  if (Array.isArray(json.bufferViews)) {
    json.bufferViews.forEach((bv, i) => {
      if (
        typeof bv.buffer !== 'number' ||
        bv.buffer < 0 ||
        bv.buffer >= buffersLen
      ) {
        push(
          issues,
          'error',
          'BUFFERVIEW_BUFFER_OUT_OF_RANGE',
          `bufferView[${i}].buffer does not reference a valid buffer.`,
          `/bufferViews/${i}/buffer`
        );
      }
    });
  }

  const bufferViewsLen = Array.isArray(json.bufferViews)
    ? json.bufferViews.length
    : 0;
  if (Array.isArray(json.accessors)) {
    json.accessors.forEach((a, i) => {
      if (
        typeof a.bufferView === 'number' &&
        (a.bufferView < 0 || a.bufferView >= bufferViewsLen)
      ) {
        push(
          issues,
          'error',
          'ACCESSOR_BUFFERVIEW_OUT_OF_RANGE',
          `accessor[${i}].bufferView does not reference a valid bufferView.`,
          `/accessors/${i}/bufferView`
        );
      }
      if (typeof a.count === 'number' && a.count <= 0 && !a.sparse) {
        push(
          issues,
          'warning',
          'ACCESSOR_COUNT_NONPOSITIVE',
          `accessor[${i}].count is ${a.count}; expected a positive integer.`,
          `/accessors/${i}/count`
        );
      }
    });
  }

  if (Array.isArray(json.meshes)) {
    json.meshes.forEach((mesh, mi) => {
      const primitives = mesh.primitives;
      if (!Array.isArray(primitives)) return;
      primitives.forEach((p, pi) => {
        if (!p.attributes || typeof p.attributes.POSITION !== 'number') {
          push(
            issues,
            'warning',
            'MESH_PRIMITIVE_NO_POSITION',
            `meshes[${mi}].primitives[${pi}] is missing the POSITION attribute.`,
            `/meshes/${mi}/primitives/${pi}/attributes/POSITION`
          );
        }
      });
    });
  }
}

interface JsonDocumentShape {
  json: GltfJsonShape;
  resources: unknown;
}

interface AdvisoryGroup {
  errors?: string[];
  warnings?: string[];
}

/**
 * Validate a GLB/glTF asset and return a structured {@link GltfValidationReport}.
 *
 * Accepts a URL string (fetched via the global `fetch`), raw GLB bytes
 * (`ArrayBuffer`/`Uint8Array`), or glTF JSON text bytes (decoded automatically). The
 * glTF-transform modules are imported dynamically on first use so the main bundle stays
 * slim for consumers that never validate.
 *
 * @example
 * ```ts
 * import { validateGltf } from 'vibegame';
 *
 * const report = await validateGltf('/assets/models/hero.glb');
 * if (!report.valid) {
 *   for (const issue of report.errors) {
 *     console.error(`${issue.code} at ${issue.pointer}: ${issue.message}`);
 *   }
 * }
 * ```
 */
export async function validateGltf(
  input: ArrayBuffer | Uint8Array | string,
  options: ValidateGltfOptions = {}
): Promise<GltfValidationReport> {
  const includeAdvisory = options.includeAdvisory !== false;
  const issues: GltfValidationIssue[] = [];
  let mimeType = 'model/gltf-binary';

  const bytes = await resolveBytes(input);

  // GLB container first, then a fallback to bare glTF JSON text.
  let json: GltfJsonShape | null = null;
  try {
    const io = (await makeIO()) as {
      binaryToJSON: (b: Uint8Array) => Promise<JsonDocumentShape>;
      readJSON: (jd: JsonDocumentShape) => Promise<unknown>;
    };
    const jsonDoc = await io.binaryToJSON(bytes);
    json = jsonDoc.json;
    mimeType = 'model/gltf-binary';

    if (includeAdvisory) {
      try {
        const doc = await io.readJSON(jsonDoc);
        const functions = await import('@gltf-transform/functions');
        const inspect = functions.inspect as (
          d: unknown
        ) => Record<
          'scenes' | 'meshes' | 'materials' | 'textures' | 'animations',
          AdvisoryGroup
        >;
        const report = inspect(doc);
        const groups = [
          'scenes',
          'meshes',
          'materials',
          'textures',
          'animations',
        ] as const;
        for (const key of groups) {
          const g = report[key];
          const upper = key.toUpperCase();
          for (const e of g.errors ?? []) {
            push(issues, 'error', `INSPECT_${upper}_ERROR`, e, `/${key}`);
          }
          for (const w of g.warnings ?? []) {
            push(issues, 'warning', `INSPECT_${upper}_WARNING`, w, `/${key}`);
          }
        }
      } catch (e) {
        logger.debug('[VibeGame] validateGltf: advisory inspect skipped', e);
      }
    }
  } catch {
    const text = tryDecodeText(bytes);
    if (text != null) {
      try {
        const parsed = JSON.parse(text) as GltfJsonShape;
        if (
          parsed &&
          (parsed.asset !== undefined ||
            Array.isArray(parsed.meshes) ||
            Array.isArray(parsed.accessors))
        ) {
          json = parsed;
          mimeType = 'model/gltf+json';
        }
      } catch {
        json = null;
      }
    }
    if (json === null) {
      push(
        issues,
        'error',
        'GLTF_PARSE_FAILED',
        'Payload is neither a valid GLB container nor parseable glTF JSON.',
        ''
      );
    }
  }

  if (json !== null) {
    checkSpec(json, issues);
  }

  issues.sort((a, b) => {
    const s = SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity];
    if (s !== 0) return s;
    return a.pointer < b.pointer ? -1 : a.pointer > b.pointer ? 1 : 0;
  });

  const errors = issues.filter((i) => i.severity === 'error');
  const warnings = issues.filter((i) => i.severity === 'warning');
  const infos = issues.filter((i) => i.severity === 'info');

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    infos,
    issues,
    byteLength: bytes.byteLength,
    mimeType,
  };
}
