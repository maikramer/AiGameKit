import { logger } from './core/utils/logger';
import type { BuilderOptions } from './builder';
import type { State } from './core';
import {
  TIME_CONSTANTS,
  XMLParser,
  XMLValueParser,
  applyWorldXmlHooks,
  createFetchIncludeLoader,
  expandIncludes,
} from './core';
import {
  beginExternalProfilerFrame,
  endExternalProfilerFrame,
  isProfilerEnabled,
  profileRenderPass,
} from './core/profiler';
import { parseXMLToEntities } from './core/recipes/parser';
import {
  RenderContext,
  applyNeutralEnvironment,
  createRenderer,
  getRenderingContext,
  setCanvasElement,
  getScene,
  threeCameras,
} from './plugins/rendering';
import { MainCamera } from './plugins/rendering/components';
import { defineQueryLive } from './core';
import { setTargetCanvas } from './plugins/input';
import { registerRuntime, unregisterRuntime } from './core/runtime-manager';
import { syncComposerSize } from './plugins/postprocessing/composer';
import { resumeAudioContextOnFirstUserGesture } from './plugins/audio/systems';
import { cancelLoadingFade } from './plugins/loading/context';

const mainCameraQuery = defineQueryLive([MainCamera]);

export class GameRuntime {
  private state: State;
  private options: BuilderOptions;
  private isRunning = false;
  private isDestroyed = false;
  private mutationObserver?: MutationObserver;
  private canvasElements = new Set<HTMLCanvasElement>();

  constructor(state: State, options: BuilderOptions = {}) {
    this.state = state;
    this.options = options;
    registerRuntime(this);
  }

  async start(): Promise<void> {
    if (this.isRunning || this.isDestroyed) return;

    if (typeof document !== 'undefined' && this.options.dom !== false) {
      await this.initializeBrowser();
    }

    if (this.isDestroyed) return;

    this.isRunning = true;

    if (
      typeof requestAnimationFrame !== 'undefined' &&
      this.options.autoStart !== false
    ) {
      this.startAnimationLoop();
    }
  }

  stop(): void {
    this.isRunning = false;
    // Three.js keeps invoking the rAF callback after stop() until the loop is
    // explicitly detached. Halting it here makes stop() actually stop.
    if (this.state && !this.state.headless) {
      const renderer = getRenderingContext(this.state).renderer;
      if (renderer) {
        renderer.setAnimationLoop(null);
      }
    }
    if (this.mutationObserver) {
      this.mutationObserver.disconnect();
      this.mutationObserver = undefined;
    }
  }

  /**
   * Drop the WebGL context without tearing down ECS/WASM.
   * Safe to call from pagehide / full-reload (must stay sync and fast).
   */
  releaseGpuContext(): void {
    if (this.state?.headless) return;
    try {
      const context = getRenderingContext(this.state);
      if (context.renderer) {
        context.renderer.setAnimationLoop(null);
        try {
          context.renderer.forceContextLoss();
        } catch (e) {
          logger.warn('forceContextLoss failed', e);
        }
      }
    } catch (e) {
      logger.warn('releaseGpuContext failed', e);
    }
  }

  destroy(): void {
    if (this.isDestroyed) return;
    this.isDestroyed = true;
    // Cancel any pending loading-screen fade so its setTimeout does not fire
    // on a detached DOM node after teardown.
    cancelLoadingFade();
    this.stop();
    this.releaseGpuContext();
    try {
      this.state.dispose();
    } catch (e) {
      logger.warn('[VibeGame] state.dispose failed', e);
    }
    this.canvasElements.clear();
    unregisterRuntime(this);
  }

  step(deltaTime: number = TIME_CONSTANTS.DEFAULT_DELTA): void {
    this.state.step(deltaTime);
  }

  getState(): State {
    return this.state;
  }

  private startAnimationLoop(): void {
    const context = getRenderingContext(this.state);
    const renderer = context.renderer;

    if (renderer) {
      let lastTime = performance.now();
      let lastErrorLogTime = 0;

      renderer.setAnimationLoop((currentTime: number) => {
        if (!this.isRunning) return;

        try {
          const deltaTime = ((currentTime as number) - lastTime) / 1000;
          lastTime = currentTime as number;

          const clamped =
            deltaTime > TIME_CONSTANTS.MAX_FRAME_DELTA
              ? TIME_CONSTANTS.MAX_FRAME_DELTA
              : deltaTime;

          const profiling = isProfilerEnabled();
          if (profiling) beginExternalProfilerFrame();

          this.state.step(clamped);

          const scene = getScene(this.state);
          if (!scene) {
            if (profiling) endExternalProfilerFrame(clamped);
            return;
          }

          const cameraEntities = mainCameraQuery(this.state.world);
          if (cameraEntities.length === 0) {
            if (profiling) endExternalProfilerFrame(clamped);
            return;
          }

          const camera = threeCameras.get(cameraEntities[0]);
          if (!camera) {
            if (profiling) endExternalProfilerFrame(clamped);
            return;
          }

          const draw = () => {
            if (context.postProcessing) {
              // Skip until canvas has a real size — Firefox boots at 0×0 and
              // EffectComposer depth targets are incomplete until then.
              if (!syncComposerSize(context.postProcessing, renderer)) return;
              context.postProcessing.render();
            } else {
              renderer.render(scene, camera);
            }
          };
          if (profiling) {
            profileRenderPass(draw);
            endExternalProfilerFrame(clamped);
          } else {
            draw();
          }
        } catch (e) {
          if (isProfilerEnabled()) {
            try {
              endExternalProfilerFrame(0);
            } catch {
              // ignore profiler teardown errors during loop failures
            }
          }
          const now = currentTime as number;
          if (now - lastErrorLogTime >= 1000) {
            logger.error('[VibeGame] Animation loop error:', e);
            lastErrorLogTime = now;
          }
        }
      });

      return;
    }

    let lastTime = performance.now();

    const animate = (currentTime: number) => {
      if (!this.isRunning) return;
      requestAnimationFrame(animate);

      const deltaTime = (currentTime - lastTime) / 1000;
      lastTime = currentTime;

      const clamped =
        deltaTime > TIME_CONSTANTS.MAX_FRAME_DELTA
          ? TIME_CONSTANTS.MAX_FRAME_DELTA
          : deltaTime;

      this.state.step(clamped);
    };

    requestAnimationFrame(animate);
  }

  private async initializeBrowser(): Promise<void> {
    if (document.readyState === 'loading') {
      await new Promise<void>((resolve) => {
        document.addEventListener('DOMContentLoaded', () => resolve());
      });
    }

    await this.state.initializePlugins();
    await this.processWorldElements();
    this.setupMutationObserver();
    this.state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  }

  private async processWorldElements(): Promise<void> {
    const elements = document.querySelectorAll('scene');
    for (const element of elements) {
      await this.processWorldElement(element as HTMLElement);
    }
  }

  private async processWorldElement(element: HTMLElement): Promise<void> {
    if (element.tagName.toLowerCase() !== 'scene') return;

    element.style.display = 'none';

    const canvasSelector = element.getAttribute('canvas');
    if (canvasSelector) {
      const canvas = document.querySelector(
        canvasSelector
      ) as HTMLCanvasElement;
      if (canvas) {
        this.canvasElements.add(canvas);
        const rendererEntity = this.state.createEntity();
        this.state.addComponent(rendererEntity, RenderContext);
        RenderContext.hasCanvas[rendererEntity] = 1;

        const skyColor = element.getAttribute('sky');
        if (skyColor) {
          const parsedColor = XMLValueParser.parse(skyColor);
          if (typeof parsedColor === 'number') {
            RenderContext.clearColor[rendererEntity] = parsedColor;
          }
        }

        setCanvasElement(rendererEntity, canvas);
        setTargetCanvas(canvas);

        // Create renderer immediately so KTX2Loader can be initialized
        // before any GLTF load functions are called during processWorldContent().
        const renderingCtx = getRenderingContext(this.state);
        if (!renderingCtx.renderer) {
          const clearColor =
            RenderContext.clearColor[rendererEntity] ?? 0x000000;
          // MSAA on the default framebuffer is redundant when the composer is
          // active (it runs its own SMAA/FXAA pass). The composer is built
          // lazily later, but the `postprocessing=` attribute lives in the
          // scene's innerHTML which is already available here — scan for it so
          // we can skip allocating the multisampled default buffer. MSAA can't
          // be toggled after context creation, so this is the only chance.
          const sceneHtml = element.innerHTML;
          const composerActive =
            /\bpostprocessing\s*=/.test(sceneHtml) &&
            !/\bpostprocessing\s*=\s*"[^"]*enabled\s*:\s*0/.test(sceneHtml);
          try {
            const renderer = await createRenderer(canvas, clearColor, {
              antialias: !composerActive,
            });
            renderingCtx.renderer = renderer;
            renderingCtx.canvas = canvas;
            applyNeutralEnvironment(renderer, renderingCtx.scene);
          } catch (e) {
            logger.warn('WebGL renderer init failed; continuing headless', e);
          }
        }
      }
    }

    await this.processWorldContent(element);

    const resumeAudio = element.getAttribute('resume-audio-on-user-gesture');
    if (resumeAudio === 'true' || resumeAudio === '') {
      resumeAudioContextOnFirstUserGesture();
    }
  }

  private async processWorldContent(worldElement: HTMLElement): Promise<void> {
    try {
      const originalHTML = worldElement.innerHTML;

      this.validateNoSelfClosingTags(originalHTML);

      const expandedHTML = await expandIncludes(originalHTML, {
        load: createFetchIncludeLoader(),
      });

      if (
        typeof process !== 'undefined' &&
        process.env?.NODE_ENV !== 'production'
      ) {
        this.validateXMLStructure(expandedHTML);
      }

      const xmlContent = `<Scene>${expandedHTML}</Scene>`;
      this.state.xmlSource = xmlContent;

      if (/<script\b/i.test(expandedHTML)) {
        logger.warn(
          '[VibeGame] <script> tags in world XML are ignored. ' +
            'Use recipe `script` attributes or entity MonoBehaviour scripts instead.'
        );
      }

      const parseResult = XMLParser.parse(xmlContent);

      if (parseResult.root.tagName === 'parsererror') {
        const errorText = expandedHTML.substring(0, 200);
        throw new Error(
          `[XML Parsing] Invalid XML syntax detected.\n` +
            `  Check your HTML for malformed tags or attributes.\n` +
            `  Content preview: ${errorText}...`
        );
      }

      // Generated geometry lives in its own XML file too: hooks get the fully
      // expanded document and can fill in attributes an author cannot type.
      applyWorldXmlHooks(parseResult.root, (error) => {
        logger.error('[VibeGame] world XML hook failed:', error);
      });

      parseXMLToEntities(this.state, parseResult.root);
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      const errStack = error instanceof Error ? error.stack : undefined;
      logger.error('❌ World content parsing failed:', errMsg);
      if (errStack) {
        logger.error(errStack);
      }
      if (
        typeof process !== 'undefined' &&
        process.env?.NODE_ENV !== 'production'
      ) {
        throw error;
      }
    }
  }

  private validateNoSelfClosingTags(htmlContent: string): void {
    const selfClosingPattern =
      /<(tween|player|entity|static-part|dynamic-part|kinematic-part)[^>]*\/>/g;
    const matches = htmlContent.match(selfClosingPattern);

    if (matches) {
      const tag = matches[0].match(/<(\w+)/)?.[1];
      throw new Error(
        `[VibeGame] Self-closing <${tag} /> tags are not supported.\n` +
          `  HTML5 doesn't recognize self-closing custom elements.\n` +
          `  Use explicit closing tags: <${tag}></${tag}>`
      );
    }
  }

  private validateXMLStructure(xmlContent: string): void {
    const voidElements = new Set([
      'area',
      'base',
      'br',
      'col',
      'embed',
      'hr',
      'img',
      'input',
      'link',
      'meta',
      'param',
      'source',
      'track',
      'wbr',
    ]);

    const tagStack: Array<{ name: string; line: number }> = [];
    const lines = xmlContent.split('\n');
    let lineNum = 0;

    for (const line of lines) {
      lineNum++;
      const openTags = line.matchAll(/<(\w+)([^>]*?)>/g);
      const closeTags = line.matchAll(/<\/(\w+)>/g);

      for (const match of openTags) {
        const tagName = match[1].toLowerCase();
        const attrs = match[2];

        if (!voidElements.has(tagName) && !attrs.endsWith('/')) {
          tagStack.push({ name: tagName, line: lineNum });
        }
      }

      for (const match of closeTags) {
        const tagName = match[1].toLowerCase();
        const lastTag = tagStack.pop();

        if (!lastTag) {
          throw new Error(
            `[XML Validation] Unexpected closing tag </${tagName}> at line ${lineNum}.\n` +
              `  No matching opening tag found.`
          );
        }

        if (lastTag.name !== tagName) {
          throw new Error(
            `[XML Validation] Mismatched tags at line ${lineNum}.\n` +
              `  Expected </${lastTag.name}> (opened at line ${lastTag.line})\n` +
              `  Found </${tagName}>`
          );
        }
      }
    }

    if (tagStack.length > 0) {
      const unclosed = tagStack
        .map((t) => `<${t.name}> at line ${t.line}`)
        .join(', ');
      throw new Error(
        `[XML Validation] Unclosed tags detected:\n  ${unclosed}\n` +
          `  Hint: Browser may have misinterpreted self-closing custom elements.`
      );
    }
  }

  private setupMutationObserver(): void {
    if (typeof MutationObserver === 'undefined') return;

    const processedElements = new WeakSet<Element>();

    this.mutationObserver = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            const element = node as HTMLElement;

            if (element.tagName.toLowerCase() === 'scene') {
              if (!processedElements.has(element)) {
                processedElements.add(element);
                void this.processWorldElement(element).catch((e) =>
                  logger.warn('processWorldElement failed', e)
                );
              }
            }

            element.querySelectorAll?.('scene').forEach((worldEl) => {
              if (!processedElements.has(worldEl)) {
                processedElements.add(worldEl);
                void this.processWorldElement(worldEl as HTMLElement).catch(
                  (e) => logger.warn('processWorldElement failed', e)
                );
              }
            });
          }
        });

        mutation.removedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            const element = node as HTMLElement;

            if (
              element.tagName.toLowerCase() === 'canvas' &&
              this.canvasElements.has(element as HTMLCanvasElement)
            ) {
              logger.warn(
                '[VibeGame] Canvas removed from DOM, disposing runtime'
              );
              this.destroy();
              return;
            }

            element.querySelectorAll?.('canvas').forEach((canvasEl) => {
              if (this.canvasElements.has(canvasEl as HTMLCanvasElement)) {
                logger.warn(
                  '[VibeGame] Canvas removed from DOM, disposing runtime'
                );
                this.destroy();
                return;
              }
            });
          }
        });
      });
    });

    this.mutationObserver.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
  }
}
