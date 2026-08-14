import type { Component, Config, Plugin, Recipe, System } from './core';
import type { ChronoPluginOptions } from './plugins/chrono/plugin';
import { State } from './core/ecs/state';
import { GameRuntime } from './runtime';

export interface BuilderOptions {
  canvas?: string;
  autoStart?: boolean;
  dom?: boolean;
  /** Enable `<scene>` DOM hot-swap watching (defaults to true in dev). */
  hotReload?: boolean;
}

export class GameBuilder {
  private state: State;
  private options: BuilderOptions;
  private useDefaultPlugins = true;
  private excludedPlugins: Set<Plugin> = new Set();
  private plugins: Plugin[] = [];
  private systems: System[] = [];
  private components: Map<string, Component> = new Map();
  private recipes: Recipe[] = [];
  private configs: Config[] = [];
  private chronoOptions?: ChronoPluginOptions;

  constructor(options: BuilderOptions = {}) {
    this.state = new State();
    this.options = options;
  }

  withoutDefaultPlugins(): GameBuilder {
    this.useDefaultPlugins = false;
    return this;
  }

  withoutPlugins(...plugins: Plugin[]): GameBuilder {
    for (const plugin of plugins) {
      this.excludedPlugins.add(plugin);
    }
    return this;
  }

  withPlugin(plugin: Plugin): GameBuilder {
    this.plugins.push(plugin);
    return this;
  }

  withPlugins(...plugins: Plugin[]): GameBuilder {
    this.plugins.push(...plugins);
    return this;
  }

  withSystem(system: System): GameBuilder {
    this.systems.push(system);
    return this;
  }

  withSystems(...systems: System[]): GameBuilder {
    this.systems.push(...systems);
    return this;
  }

  withComponent(name: string, component: Component): GameBuilder {
    this.components.set(name, component);
    return this;
  }

  withRecipe(recipe: Recipe): GameBuilder {
    this.recipes.push(recipe);
    return this;
  }

  withConfig(config: Config): GameBuilder {
    this.configs.push(config);
    return this;
  }

  /**
   * Enable time-travel recording: `GAME.withChrono({ seconds: 60 }).run()`
   * then `chronoRewind(state, 5)` from anywhere (console, debug overlay,
   * pause menu) to restore the world to five seconds ago.
   */
  withChrono(options: ChronoPluginOptions = {}): GameBuilder {
    this.chronoOptions = options;
    return this;
  }

  configure(options: BuilderOptions): GameBuilder {
    this.options = { ...this.options, ...options };
    return this;
  }

  async build(): Promise<GameRuntime> {
    if (this.useDefaultPlugins) {
      const { DefaultPlugins } = await import('./plugins/defaults');
      for (const plugin of DefaultPlugins) {
        if (!this.excludedPlugins.has(plugin)) {
          this.state.registerPlugin(plugin);
        }
      }
    }

    for (const plugin of this.plugins) {
      this.state.registerPlugin(plugin);
    }

    if (this.chronoOptions) {
      const { ChronoPlugin, applyChronoOptions } =
        await import('./plugins/chrono/plugin');
      this.state.registerPlugin(ChronoPlugin);
      applyChronoOptions(this.state, this.chronoOptions);
    }

    for (const system of this.systems) {
      this.state.registerSystem(system);
    }

    for (const [name, component] of this.components) {
      this.state.registerComponent(name, component);
    }

    for (const recipe of this.recipes) {
      this.state.registerRecipe(recipe);
    }

    for (const config of this.configs) {
      this.state.registerConfig(config);
    }

    return new GameRuntime(this.state, this.options);
  }

  async run(): Promise<GameRuntime> {
    const runtime = await this.build();
    await runtime.start();
    return runtime;
  }
}

export function create(options?: BuilderOptions): GameBuilder {
  return new GameBuilder(options);
}
