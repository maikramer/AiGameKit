// Minimal WebGL stub for headless (Bun/JSDOM) tests.
//
// Lets `THREE.WebGLRenderer` be constructed and run a render pass without a
// real GPU. SMOKE-LEVEL coverage only: rendering systems can be exercised in
// CI (does the system crash? are the types right?) with NO visual correctness.
// Opt-in via install/uninstall — never installed globally, so tests that rely
// on the real headless (no-renderer) path are unaffected.

const GL_CONSTANTS: Record<string, number> = {
  NO_ERROR: 0,
  NONE: 0,
  ZERO: 0,
  ONE: 1,

  VENDOR: 0x1f00,
  RENDERER: 0x1f01,
  VERSION: 0x1f02,
  SHADING_LANGUAGE_VERSION: 0x8b8c,

  MAX_VERTEX_ATTRIBS: 0x8869,
  MAX_VERTEX_UNIFORM_VECTORS: 0x8dfb,
  MAX_VARYING_VECTORS: 0x8dfc,
  MAX_FRAGMENT_UNIFORM_VECTORS: 0x8dfd,
  MAX_TEXTURE_IMAGE_UNITS: 0x8872,
  MAX_VERTEX_TEXTURE_IMAGE_UNITS: 0x8b4c,
  MAX_COMBINED_TEXTURE_IMAGE_UNITS: 0x8b4d,
  MAX_TEXTURE_SIZE: 0x0d33,
  MAX_CUBE_MAP_TEXTURE_SIZE: 0x851c,
  MAX_RENDERBUFFER_SIZE: 0x84e8,
  MAX_VIEWPORT_DIMS: 0x0d3a,
  MAX_DRAW_BUFFERS: 0x8824,
  MAX_COLOR_ATTACHMENTS: 0x8cdf,
  MAX_SAMPLES: 0x8d57,
  MAX_3D_TEXTURE_SIZE: 0x8073,
  MAX_ARRAY_TEXTURE_LAYERS: 0x88ff,
  MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS: 0x8c8a,
  MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS: 0x8c8b,

  RED_BITS: 0x0d52,
  GREEN_BITS: 0x0d53,
  BLUE_BITS: 0x0d54,
  ALPHA_BITS: 0x0d55,
  DEPTH_BITS: 0x0d56,
  STENCIL_BITS: 0x0d57,
  SUBPIXEL_BITS: 0x0d50,
  SAMPLES: 0x80a9,
  SAMPLE_BUFFERS: 0x80a8,

  FRAMEBUFFER_COMPLETE: 0x8cd5,
  FRAMEBUFFER_INCOMPLETE_ATTACHMENT: 0x8cd6,
  FRAMEBUFFER_UNSUPPORTED: 0x8cdd,
  FRAMEBUFFER: 0x8d40,
  RENDERBUFFER: 0x8d41,
  COLOR_ATTACHMENT0: 0x8ce0,
  DEPTH_ATTACHMENT: 0x8d00,
  STENCIL_ATTACHMENT: 0x8d20,
  DEPTH_STENCIL_ATTACHMENT: 0x821a,
  DEPTH_STENCIL: 0x84f9,

  ALREADY_SIGNALED: 0x911a,
  CONDITION_SATISFIED: 0x911c,
  TIMEOUT_EXPIRED: 0x911b,
  SYNC_FLUSH_COMMANDS_BIT: 0x00000001,
  OBJECT_TYPE: 0x9112,
  SYNC_STATUS: 0x9114,

  ACTIVE_UNIFORMS: 0x8b86,
  ACTIVE_ATTRIBUTES: 0x8f89,
  ACTIVE_UNIFORM_BLOCKS: 0x916a,
  ATTACHED_SHADERS: 0x8b85,
  ACTIVE_TEXTURE: 0x84e0,
  TRANSFORM_FEEDBACK_VARYINGS: 0x8f83,

  COMPILE_STATUS: 0x8b81,
  LINK_STATUS: 0x8b82,
  VALIDATE_STATUS: 0x8b83,
  DELETE_STATUS: 0x8b80,

  CURRENT_PROGRAM: 0x8b8d,

  ARRAY_BUFFER: 0x8892,
  ELEMENT_ARRAY_BUFFER: 0x8893,
  STATIC_DRAW: 0x88e4,
  DYNAMIC_DRAW: 0x88e8,
  STREAM_DRAW: 0x88e0,
  BUFFER_SIZE: 0x8764,
  BUFFER_USAGE: 0x8765,

  TEXTURE_2D: 0x0de1,
  TEXTURE_CUBE_MAP: 0x8513,
  TEXTURE_3D: 0x806f,
  TEXTURE_2D_ARRAY: 0x8c1a,
  TEXTURE0: 0x84c0,
  TEXTURE_MIN_FILTER: 0x2801,
  TEXTURE_MAG_FILTER: 0x2800,
  TEXTURE_WRAP_S: 0x2802,
  TEXTURE_WRAP_T: 0x2803,
  TEXTURE_WRAP_R: 0x8072,
  NEAREST: 0x2600,
  LINEAR: 0x2601,
  NEAREST_MIPMAP_NEAREST: 0x2700,
  LINEAR_MIPMAP_NEAREST: 0x2701,
  NEAREST_MIPMAP_LINEAR: 0x2702,
  LINEAR_MIPMAP_LINEAR: 0x2703,
  CLAMP_TO_EDGE: 0x812f,
  REPEAT: 0x2901,
  MIRRORED_REPEAT: 0x8370,
  UNPACK_FLIP_Y: 0x8078,
  UNPACK_PREMULTIPLY_ALPHA: 0x8079,
  UNPACK_ALIGNMENT: 0x0cf5,
  UNPACK_COLORSPACE_CONVERSION: 0x806d,
  TEXTURE_CUBE_MAP_POSITIVE_X: 0x8515,

  RGBA: 0x1908,
  RGB: 0x1907,
  ALPHA: 0x1906,
  LUMINANCE: 0x1909,
  R8: 0x8229,
  RG8: 0x822b,
  RGB8: 0x8051,
  RGBA8: 0x8058,
  R16F: 0x822d,
  RG16F: 0x822f,
  RGB16F: 0x881b,
  RGBA16F: 0x881a,
  R32F: 0x822e,
  RG32F: 0x8230,
  RGB32F: 0x8815,
  RGBA32F: 0x8814,
  R11F_G11F_B10F: 0x8c3a,
  DEPTH_COMPONENT16: 0x81a5,
  DEPTH_COMPONENT24: 0x81a6,
  DEPTH_COMPONENT32F: 0x8cac,
  DEPTH24_STENCIL8: 0x88f0,
  UNSIGNED_BYTE: 0x1401,
  UNSIGNED_SHORT: 0x1403,
  UNSIGNED_INT: 0x1405,
  FLOAT: 0x1406,
  HALF_FLOAT: 0x140b,
  UNSIGNED_SHORT_4_4_4_4: 0x8033,
  UNSIGNED_SHORT_5_5_5_1: 0x8034,
  UNSIGNED_SHORT_5_6_5: 0x8363,
  UNSIGNED_INT_24_8: 0x84fa,
  UNSIGNED_INT_2_10_10_10_REV: 0x8368,
  FLOAT_32_UNSIGNED_INT_24_8_REV: 0x8dad,

  PACK_ALIGNMENT: 0x0d05,

  BLEND: 0x0be2,
  CULL_FACE: 0x0b44,
  DEPTH_TEST: 0x0b71,
  DITHER: 0x0bd0,
  POLYGON_OFFSET_FILL: 0x8037,
  SAMPLE_ALPHA_TO_COVERAGE: 0x809e,
  SAMPLE_COVERAGE: 0x80a0,
  SCISSOR_TEST: 0x0c11,
  STENCIL_TEST: 0x0b90,
  RASTERIZER_DISCARD: 0x8c89,

  SRC_ALPHA: 0x0302,
  ONE_MINUS_SRC_ALPHA: 0x0303,
  SRC_COLOR: 0x0300,
  ONE_MINUS_SRC_COLOR: 0x0301,
  DST_ALPHA: 0x0304,
  ONE_MINUS_DST_ALPHA: 0x0305,
  DST_COLOR: 0x0306,
  ONE_MINUS_DST_COLOR: 0x0307,
  CONSTANT_COLOR: 0x8001,
  FUNC_ADD: 0x8006,
  FUNC_SUBTRACT: 0x800a,
  FUNC_REVERSE_SUBTRACT: 0x800b,
  MIN: 0x8007,
  MAX: 0x8008,
  NEVER: 0x0200,
  LESS: 0x0201,
  EQUAL: 0x0202,
  LEQUAL: 0x0203,
  GREATER: 0x0204,
  NOTEQUAL: 0x0205,
  GEQUAL: 0x0206,
  ALWAYS: 0x0207,
  FRONT: 0x0404,
  BACK: 0x0405,
  FRONT_AND_BACK: 0x0408,
  CW: 0x0900,
  CCW: 0x0901,
  TRIANGLES: 0x0004,
  TRIANGLE_STRIP: 0x0005,
  POINTS: 0x0000,
  LINES: 0x0001,
  LINE_STRIP: 0x0003,

  VERTEX_SHADER: 0x8b31,
  FRAGMENT_SHADER: 0x8b30,
  HIGH_FLOAT: 0x8df2,
  MEDIUM_FLOAT: 0x8df1,
  LOW_FLOAT: 0x8df0,
  HIGH_INT: 0x8df5,
  MEDIUM_INT: 0x8df4,
  LOW_INT: 0x8df3,

  MAX_TEXTURE_MAX_ANISOTROPY_EXT: 0x84ff,
  TEXTURE_MAX_ANISOTROPY_EXT: 0x84fe,

  IMPLEMENTATION_COLOR_READ_TYPE: 0x8b9a,
  IMPLEMENTATION_COLOR_READ_FORMAT: 0x8b9b,
};

const PARAM_RESULTS: Record<number, unknown> = {
  [GL_CONSTANTS.VERSION]: 'WebGL 2.0 (VibeGame headless stub)',
  [GL_CONSTANTS.SHADING_LANGUAGE_VERSION]: 'WebGL GLSL ES 3.00 (VibeGame stub)',
  [GL_CONSTANTS.VENDOR]: 'VibeGame',
  [GL_CONSTANTS.RENDERER]: 'VibeGame WebGL stub',

  [GL_CONSTANTS.MAX_VERTEX_ATTRIBS]: 16,
  [GL_CONSTANTS.MAX_VERTEX_UNIFORM_VECTORS]: 4096,
  [GL_CONSTANTS.MAX_VARYING_VECTORS]: 30,
  [GL_CONSTANTS.MAX_FRAGMENT_UNIFORM_VECTORS]: 1024,
  [GL_CONSTANTS.MAX_TEXTURE_IMAGE_UNITS]: 16,
  [GL_CONSTANTS.MAX_VERTEX_TEXTURE_IMAGE_UNITS]: 16,
  [GL_CONSTANTS.MAX_COMBINED_TEXTURE_IMAGE_UNITS]: 32,
  [GL_CONSTANTS.MAX_TEXTURE_SIZE]: 16384,
  [GL_CONSTANTS.MAX_CUBE_MAP_TEXTURE_SIZE]: 16384,
  [GL_CONSTANTS.MAX_RENDERBUFFER_SIZE]: 16384,
  [GL_CONSTANTS.MAX_3D_TEXTURE_SIZE]: 2048,
  [GL_CONSTANTS.MAX_ARRAY_TEXTURE_LAYERS]: 2048,
  [GL_CONSTANTS.MAX_DRAW_BUFFERS]: 4,
  [GL_CONSTANTS.MAX_COLOR_ATTACHMENTS]: 4,
  [GL_CONSTANTS.MAX_SAMPLES]: 8,

  [GL_CONSTANTS.RED_BITS]: 8,
  [GL_CONSTANTS.GREEN_BITS]: 8,
  [GL_CONSTANTS.BLUE_BITS]: 8,
  [GL_CONSTANTS.ALPHA_BITS]: 8,
  [GL_CONSTANTS.DEPTH_BITS]: 24,
  [GL_CONSTANTS.STENCIL_BITS]: 0,
  [GL_CONSTANTS.SUBPIXEL_BITS]: 4,
  [GL_CONSTANTS.SAMPLES]: 4,
  [GL_CONSTANTS.SAMPLE_BUFFERS]: 1,

  [GL_CONSTANTS.ACTIVE_TEXTURE]: GL_CONSTANTS.TEXTURE0,
  [GL_CONSTANTS.MAX_TEXTURE_MAX_ANISOTROPY_EXT]: 16,
  [GL_CONSTANTS.IMPLEMENTATION_COLOR_READ_TYPE]: GL_CONSTANTS.UNSIGNED_BYTE,
  [GL_CONSTANTS.IMPLEMENTATION_COLOR_READ_FORMAT]: GL_CONSTANTS.RGBA,
};

const VIEWPORT_DIMS = new Int32Array([16384, 16384]);

function getParameterValue(pname: number): unknown {
  if (pname === GL_CONSTANTS.MAX_VIEWPORT_DIMS) return VIEWPORT_DIMS;
  return PARAM_RESULTS[pname] ?? 0;
}

function createExtensionStub(): object {
  const cache = new Map<string, number>();
  let nextId = 0x9000;
  return new Proxy({} as Record<string, unknown>, {
    get(_t, prop) {
      if (typeof prop === 'symbol') {
        if (prop === Symbol.toPrimitive) return () => '[WebGLExtensionStub]';
        return undefined;
      }
      if (prop === 'then') return undefined;
      if (/^[A-Z][A-Z0-9_]*$/.test(prop)) {
        let v = cache.get(prop);
        if (v === undefined) {
          v = nextId++;
          cache.set(prop, v);
        }
        return v;
      }
      return noop;
    },
  });
}

function noop(): void {}

// COMPILE_STATUS / LINK_STATUS / VALIDATE_STATUS must be truthy or Three.js
// throws a shader compile/link error during the first render.
function okStatus(): boolean {
  return true;
}

function zero(): number {
  return 0;
}

function emptyString(): string {
  return '';
}

function newHandle(): object {
  return {};
}

const TYPED_METHODS: Record<string, (...args: unknown[]) => unknown> = {
  getParameter: (p) => getParameterValue(p as number),

  getExtension: () => createExtensionStub(),
  getSupportedExtensions: () => [
    'EXT_texture_filter_anisotropic',
    'OES_texture_float',
    'OES_texture_float_linear',
    'OES_texture_half_float',
    'OES_texture_half_float_linear',
    'OES_standard_derivatives',
    'OES_element_index_uint',
    'EXT_blend_minmax',
    'WEBGL_compressed_texture_astc',
    'WEBGL_compressed_texture_etc',
    'WEBGL_compressed_texture_etc1',
    'WEBGL_compressed_texture_s3tc',
    'WEBGL_compressed_texture_pvrtc',
    'WEBGL_debug_renderer_info',
    'WEBGL_debug_shaders',
    'WEBGL_lose_context',
    'WEBGL_multi_draw',
    'WEBGL_draw_instanced_base_vertex_base_instance',
    'WEBGL_provoking_vertex',
    'KHR_parallel_shader_compile',
    'EXT_color_buffer_float',
    'EXT_color_buffer_half_float',
    'EXT_disjoint_timer_query_webgl2',
    'EXT_float_blend',
    'EXT_texture_norm16',
    'OES_draw_buffers_indexed',
    'OVR_multiview2',
  ],

  getShaderParameter: okStatus,
  getProgramParameter: (_program, pname) => {
    const p = pname as number;
    if (
      p === GL_CONSTANTS.ACTIVE_UNIFORMS ||
      p === GL_CONSTANTS.ACTIVE_ATTRIBUTES ||
      p === GL_CONSTANTS.ACTIVE_UNIFORM_BLOCKS ||
      p === GL_CONSTANTS.ATTACHED_SHADERS ||
      p === GL_CONSTANTS.TRANSFORM_FEEDBACK_VARYINGS
    ) {
      return 0;
    }
    return true;
  },
  getShaderInfoLog: emptyString,
  getProgramInfoLog: emptyString,

  getAttribLocation: zero,
  getUniformLocation: newHandle,
  getActiveAttrib: () => null,
  getActiveUniform: () => null,
  getActiveUniformBlockName: emptyString,
  getActiveUniformBlockParameter: () => null,
  getUniformBlockIndex: zero,
  getUniformIndices: () => [],
  getActiveUniforms: () => null,

  getShaderPrecisionFormat: () => ({
    rangeMin: 127,
    rangeMax: 127,
    precision: 23,
  }),

  getError: zero,
  checkFramebufferStatus: () => GL_CONSTANTS.FRAMEBUFFER_COMPLETE,

  getContextAttributes: () => ({
    alpha: true,
    antialias: true,
    depth: true,
    stencil: false,
    premultipliedAlpha: false,
    preserveDrawingBuffer: false,
    powerPreference: 'high-performance',
    failIfMajorPerformanceCaveat: false,
    desynchronized: false,
  }),

  getBufferParameter: zero,
  getTexParameter: zero,
  getFramebufferAttachmentParameter: zero,
  getRenderbufferParameter: zero,
  getIndexedParameter: zero,
  getInternalformatParameter: () => null,
  getSyncParameter: zero,
  getQuery: () => null,
  getQueryParameter: zero,

  createShader: newHandle,
  createProgram: newHandle,
  createBuffer: newHandle,
  createTexture: newHandle,
  createFramebuffer: newHandle,
  createRenderbuffer: newHandle,
  createVertexArray: newHandle,
  createQuery: newHandle,
  createTransformFeedback: newHandle,
  createSampler: newHandle,
  fenceSync: newHandle,

  isShader: okStatus,
  isProgram: okStatus,
  isBuffer: okStatus,
  isTexture: okStatus,
  isFramebuffer: okStatus,
  isRenderbuffer: okStatus,
  isVertexArray: okStatus,
  isSync: okStatus,
  isQuery: okStatus,
  isEnabled: okStatus,

  clientWaitSync: () => GL_CONSTANTS.ALREADY_SIGNALED,
};

function createWebGLStubContext(canvas: unknown): any {
  const constantCache = new Map<string, number>();
  let nextConstantId = 0x10000;

  const target: Record<string, unknown> = {
    canvas,
    drawingBufferWidth: 1024,
    drawingBufferHeight: 1024,
    ...GL_CONSTANTS,
  };

  const proxy = new Proxy(target, {
    get(t, prop, receiver) {
      if (typeof prop === 'symbol') {
        if (prop === Symbol.toPrimitive) return () => '[WebGLStubContext]';
        return undefined;
      }
      if (prop === 'then') return undefined;

      if (Object.prototype.hasOwnProperty.call(t, prop)) {
        return Reflect.get(t, prop, receiver);
      }
      const typed = TYPED_METHODS[prop];
      if (typed) return typed;

      // UPPER_SNAKE names are GLenum constants; anything else is a method.
      if (/^[A-Z][A-Z0-9_]*$/.test(prop)) {
        let v = constantCache.get(prop);
        if (v === undefined) {
          v = nextConstantId++;
          constantCache.set(prop, v);
        }
        return v;
      }
      return noop;
    },
    has: () => true,
  });

  return proxy;
}

function defineRenderingContext(
  name: 'WebGLRenderingContext' | 'WebGL2RenderingContext'
): any {
  const ctor = function WebGLRenderingContextStub() {} as unknown as any;
  for (const [k, v] of Object.entries(GL_CONSTANTS)) {
    (ctor as unknown as Record<string, number>)[k] = v;
    (ctor.prototype as unknown as Record<string, number>)[k] = v;
  }
  Object.defineProperty(ctor, 'name', { value: name });
  return ctor;
}

interface CanvasPrototype {
  getContext(type: string, ...args: unknown[]): unknown;
}

interface InstallState {
  canvasProto: CanvasPrototype | null;
  originalGetContext: ((...args: unknown[]) => unknown) | null;
  originalWebGL: any;
  originalWebGL2: any;
}

let installed: InstallState | null = null;

function globalWindow(): any {
  return (globalThis as { window?: any }).window ?? globalThis;
}

export function installWebGLStub(): void {
  if (installed) return;

  const w = globalWindow();
  const CanvasCtor = ((
    globalThis as { HTMLCanvasElement?: typeof HTMLCanvasElement }
  ).HTMLCanvasElement ?? w.HTMLCanvasElement) as
    | typeof HTMLCanvasElement
    | undefined;

  const canvasProto = CanvasCtor?.prototype as CanvasPrototype | undefined;
  const originalGetContext = canvasProto?.getContext as
    | ((...args: unknown[]) => unknown)
    | undefined;

  if (canvasProto) {
    const stubbed = function getContext(
      this: HTMLCanvasElement,
      type: string,
      ...attrs: unknown[]
    ): unknown {
      if (
        type === 'webgl2' ||
        type === 'webgl' ||
        type === 'experimental-webgl'
      ) {
        return createWebGLStubContext(this);
      }
      if (typeof originalGetContext === 'function') {
        return originalGetContext.call(this, type, ...attrs);
      }
      return null;
    };
    Object.defineProperty(canvasProto, 'getContext', {
      configurable: true,
      writable: true,
      value: stubbed,
    });
  }

  const g = globalThis as Record<string, unknown>;
  const originalWebGL = g.WebGLRenderingContext as any;
  const originalWebGL2 = g.WebGL2RenderingContext as any;
  if (!originalWebGL)
    g.WebGLRenderingContext = defineRenderingContext('WebGLRenderingContext');
  if (!originalWebGL2)
    g.WebGL2RenderingContext = defineRenderingContext('WebGL2RenderingContext');

  installed = {
    canvasProto: canvasProto ?? null,
    originalGetContext: originalGetContext ?? null,
    originalWebGL,
    originalWebGL2,
  };
}

export function uninstallWebGLStub(): void {
  if (!installed) return;
  const { canvasProto, originalGetContext, originalWebGL, originalWebGL2 } =
    installed;
  const g = globalThis as Record<string, unknown>;

  if (canvasProto) {
    if (originalGetContext) {
      Object.defineProperty(canvasProto, 'getContext', {
        configurable: true,
        writable: true,
        value: originalGetContext,
      });
    } else {
      // Context was missing pre-install; remove our own addition.
      delete (canvasProto as unknown as Record<string, unknown>).getContext;
    }
  }

  if (originalWebGL === undefined) {
    delete g.WebGLRenderingContext;
  } else {
    g.WebGLRenderingContext = originalWebGL;
  }
  if (originalWebGL2 === undefined) {
    delete g.WebGL2RenderingContext;
  } else {
    g.WebGL2RenderingContext = originalWebGL2;
  }

  installed = null;
}
