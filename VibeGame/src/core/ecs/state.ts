import {
  addComponent,
  addEntity,
  createWorld,
  entityExists,
  hasComponent,
  removeComponent,
  removeEntity,
  type Component,
  type IWorld,
} from 'bitecs';
import { defineQuery } from 'bitecs';
import { toKebabCase } from '../utils/naming';
import { ConfigRegistry } from './config';
import { setComponentFields } from './utils';
import { Parent } from './components';
import { TIME_CONSTANTS } from './constants';
import { Scheduler } from './scheduler';
import type {
  Config,
  GameTime,
  Parser,
  Plugin,
  Recipe,
  System,
  XMLValue,
} from './types';
import {
  createSnapshot,
  formatSnapshot,
  type SnapshotOptions,
  type WorldSnapshot,
} from './snapshot';
import { createEntityFromRecipe } from '../recipes/parser';
import { Tag, addTag, getTagId, getTagName } from './tags';
import { Layer } from './layers';
import {
  addEventListener as _addEventListener,
  dispatchEvent as _dispatchEvent,
  removeAllListeners,
  removeEventListener as _removeEventListener,
} from './events';

export class State {
  public readonly world: IWorld;
  public readonly time: GameTime;
  public readonly scheduler = new Scheduler();
  public readonly systems = new Set<System>();
  public readonly config = new ConfigRegistry();
  public headless = false;
  private readonly recipes = new Map<string, Recipe>();
  private readonly components = new Map<string, Component>();
  private readonly componentNames = new WeakMap<Component, string>();
  private readonly plugins: Plugin[] = [];
  private readonly entityNames = new Map<string, number>();
  private readonly destroyCallbacks = new Map<number, Set<(eid: number) => void>>();
  private readonly globalDestroyCallbacks = new Set<(eid: number) => void>();
  private isDisposed = false;

  constructor() {
    this.world = createWorld();
    this.time = {
      deltaTime: 0,
      unscaledDeltaTime: 0,
      fixedDeltaTime: TIME_CONSTANTS.FIXED_TIMESTEP,
      fixedTime: 0,
      timeScale: 1.0,
      frameCount: 0,
      realtimeSinceStartup: 0,
      elapsed: 0,
    };

    this.registerComponent('parent', Parent);
    this.registerRecipe({
      name: 'entity',
      components: ['transform'],
    });
  }

  registerPlugin(plugin: Plugin): void {
    this.plugins.push(plugin);
    if (plugin.components) {
      for (const [name, component] of Object.entries(plugin.components)) {
        this.registerComponent(name, component);
      }
    }
    if (plugin.systems) {
      for (const system of plugin.systems) {
        this.registerSystem(system);
      }
    }
    if (plugin.recipes) {
      for (const recipe of plugin.recipes) {
        this.registerRecipe(recipe);
      }
    }
    if (plugin.config) {
      this.registerConfig(plugin.config);
    }
  }

  async initializePlugins(): Promise<void> {
    for (const plugin of this.plugins) {
      if (plugin.initialize) {
        await plugin.initialize(this);
      }
    }
  }

  registerSystem(system: System): void {
    if (!this.systems.has(system)) {
      this.systems.add(system);
    }
  }

  registerRecipe(recipe: Recipe): void {
    this.recipes.set(recipe.name, recipe);
  }

  registerComponent(name: string, component: Component): void {
    const kebabName = toKebabCase(name);
    this.components.set(kebabName, component);
    this.componentNames.set(component, kebabName);
  }

  registerConfig(config: Config): void {
    this.config.register(config);
  }

  getParser(tag: string): Parser | undefined {
    return this.config.getParser(tag);
  }

  getRecipe(name: string): Recipe | undefined {
    return this.recipes.get(name);
  }

  getComponent(name: string): Component | undefined {
    return this.components.get(toKebabCase(name));
  }

  hasRecipe(name: string): boolean {
    return this.recipes.has(name);
  }

  getRecipeNames(): Set<string> {
    return new Set(this.recipes.keys());
  }

  getComponentNames(): string[] {
    return Array.from(this.components.keys());
  }

  setEntityName(name: string, entity: number): void {
    this.entityNames.set(name, entity);
  }

  getEntityByName(name: string): number | null {
    return this.entityNames.get(name) ?? null;
  }

  getEntityName(eid: number): string | undefined {
    for (const [name, entity] of this.entityNames) {
      if (entity === eid) return name;
    }
    return undefined;
  }

  getNamedEntities(): Map<string, number> {
    return new Map(this.entityNames);
  }

  private getComponentName(component: Component): string | undefined {
    return this.componentNames.get(component);
  }

  step(deltaTime = TIME_CONSTANTS.DEFAULT_DELTA): void {
    this.checkDisposed();
    this.scheduler.step(this, deltaTime);
  }

  createEntity(): number {
    this.checkDisposed();
    return addEntity(this.world);
  }

  destroyEntity(eid: number): void {
    this.checkDisposed();
    const perEntity = this.destroyCallbacks.get(eid);
    if (perEntity) {
      for (const cb of perEntity) {
        try {
          cb(eid);
        } catch (err) {
          console.error("[VibeGame] destroyEntity callback error:", err);
        }
      }
      this.destroyCallbacks.delete(eid);
    }
    for (const cb of this.globalDestroyCallbacks) {
      try {
        cb(eid);
      } catch (err) {
        console.error("[VibeGame] destroyEntity global callback error:", err);
      }
    }
    removeAllListeners(eid);
    removeEntity(this.world, eid);
  }

  onDestroy(eid: number, callback: (eid: number) => void): void {
    let set = this.destroyCallbacks.get(eid);
    if (!set) {
      set = new Set();
      this.destroyCallbacks.set(eid, set);
    }
    set.add(callback);
  }

  offDestroy(eid: number, callback: (eid: number) => void): void {
    this.destroyCallbacks.get(eid)?.delete(callback);
  }

  onDestroyAll(callback: (eid: number) => void): void {
    this.globalDestroyCallbacks.add(callback);
  }

  exists(eid: number): boolean {
    return entityExists(this.world, eid);
  }

  setTag(eid: number, name: string): void {
    let id = getTagId(name);
    if (id < 0) id = addTag(name);
    if (!hasComponent(this.world, Tag, eid)) {
      addComponent(this.world, Tag, eid);
    }
    Tag.value[eid] = id;
  }

  getTag(eid: number): string {
    if (!hasComponent(this.world, Tag, eid)) return "Untagged";
    return getTagName(Tag.value[eid]);
  }

  findByTag(name: string): number | undefined {
    const id = getTagId(name);
    if (id < 0) return undefined;
    const entities = defineQuery([Tag])(this.world);
    for (const eid of entities) {
      if (Tag.value[eid] === id) return eid;
    }
    return undefined;
  }

  findGameObjectsWithTag(name: string): number[] {
    const id = getTagId(name);
    if (id < 0) return [];
    const result: number[] = [];
    const entities = defineQuery([Tag])(this.world);
    for (const eid of entities) {
      if (Tag.value[eid] === id) result.push(eid);
    }
    return result;
  }

  setLayer(eid: number, layer: number): void {
    if (!hasComponent(this.world, Layer, eid)) {
      addComponent(this.world, Layer, eid);
    }
    Layer.value[eid] = layer;
  }

  getLayer(eid: number): number {
    if (!hasComponent(this.world, Layer, eid)) return 0;
    return Layer.value[eid];
  }

  addEventListener(eid: number, eventName: string, callback: (data?: unknown) => void): void {
    _addEventListener(eid, eventName, callback);
  }

  removeEventListener(eid: number, eventName: string, callback: (data?: unknown) => void): void {
    _removeEventListener(eid, eventName, callback);
  }

  addEventListenerOnce(eid: number, eventName: string, callback: (data?: unknown) => void): void {
    const wrapper = (data?: unknown) => {
      _removeEventListener(eid, eventName, wrapper);
      callback(data);
    };
    _addEventListener(eid, eventName, wrapper);
  }

  dispatchEvent(eid: number, eventName: string, data?: unknown): void {
    _dispatchEvent(eid, eventName, data);
  }

  removeAllEventListeners(eid: number, eventName?: string): void {
    removeAllListeners(eid, eventName);
  }

  addComponent<T extends Component>(
    eid: number,
    component: T,
    values?: Record<string, number>
  ): void {
    addComponent(this.world, component, eid);

    const componentName = this.getComponentName(component);
    if (componentName) {
      const defaults = this.config.getDefaults(componentName);
      setComponentFields(component, eid, defaults);
    }

    if (values) {
      setComponentFields(component, eid, values);
    }
  }

  removeComponent<T extends Component>(eid: number, component: T): void {
    removeComponent(this.world, component, eid);
  }

  hasComponent<T extends Component>(eid: number, component: T): boolean {
    return hasComponent(this.world, component, eid);
  }

  createFromRecipe(
    recipeName: string,
    attributes: Record<string, XMLValue> = {}
  ): number {
    return createEntityFromRecipe(this, recipeName, attributes);
  }

  dispose(): void {
    if (this.isDisposed) {
      throw new Error('[VibeGame] State already disposed');
    }
    for (const system of this.systems) {
      system.dispose?.(this);
    }
    this.systems.clear();
    this.isDisposed = true;
  }

  private checkDisposed(): void {
    if (this.isDisposed) {
      throw new Error('[VibeGame] Cannot use disposed State');
    }
  }

  snapshot(
    options?: SnapshotOptions
  ): WorldSnapshot & { format: () => string } {
    const snap = createSnapshot(this, options);
    return {
      ...snap,
      format: () => formatSnapshot(snap),
    };
  }
}
