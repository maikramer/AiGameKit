import { toKebabCase } from '../utils/naming';
import type {
  Adapter,
  ComponentDefaults,
  ComponentEnums,
  ComponentShorthands,
  Config,
  EnumMapping,
  Parser,
  ShorthandMapping,
  ValidationRule,
} from './types';

/**
 * Component-name aliases: a user-facing attribute name that resolves to a
 * different canonical component (registered under the value). Config is keyed
 * by the canonical name, so lookups must resolve aliases the same way
 * `State.getComponent` does — otherwise defaults/enums silently vanish when a
 * component is addressed by its alias (e.g. `body` for `rigidbody`).
 */
export const COMPONENT_ALIASES: Record<string, string> = {
  body: 'rigidbody',
  player: 'player-controller',
  renderer: 'mesh-renderer',
};

export class ConfigRegistry {
  private readonly parsers = new Map<string, Parser[]>();
  private readonly componentDefaults: ComponentDefaults = {};
  private readonly componentShorthands: ComponentShorthands = {};
  private readonly componentEnums: ComponentEnums = {};
  private readonly validations: ValidationRule[] = [];
  private readonly skipProperties: Record<string, Set<string>> = {};
  private readonly adapters: Record<string, Record<string, Adapter>> = {};

  register(config: Config): void {
    if (config.parsers) {
      for (const [name, parser] of Object.entries(config.parsers)) {
        const existing = this.parsers.get(name) || [];
        existing.push(parser);
        this.parsers.set(name, existing);
        const lowerName = name.toLowerCase();
        if (lowerName !== name) {
          const lowerExisting = this.parsers.get(lowerName) || [];
          lowerExisting.push(parser);
          this.parsers.set(lowerName, lowerExisting);
        }
      }
    }

    if (config.defaults) {
      for (const [componentName, defaults] of Object.entries(config.defaults)) {
        const kebabName = toKebabCase(componentName);
        if (!this.componentDefaults[kebabName]) {
          this.componentDefaults[kebabName] = {};
        }
        Object.assign(this.componentDefaults[kebabName], defaults);
      }
    }

    if (config.shorthands) {
      for (const [componentName, shorthands] of Object.entries(
        config.shorthands
      )) {
        const kebabName = toKebabCase(componentName);
        if (!this.componentShorthands[kebabName]) {
          this.componentShorthands[kebabName] = {};
        }
        Object.assign(this.componentShorthands[kebabName], shorthands);
      }
    }

    if (config.enums) {
      for (const [componentName, enums] of Object.entries(config.enums)) {
        const kebabName = toKebabCase(componentName);
        if (!this.componentEnums[kebabName]) {
          this.componentEnums[kebabName] = {};
        }
        Object.assign(this.componentEnums[kebabName], enums);
      }
    }

    if (config.validations) {
      this.validations.push(...config.validations);
    }

    if (config.skip) {
      for (const [componentName, props] of Object.entries(config.skip)) {
        const kebabName = toKebabCase(componentName);
        if (!this.skipProperties[kebabName]) {
          this.skipProperties[kebabName] = new Set();
        }
        for (const prop of props) {
          this.skipProperties[kebabName].add(prop);
        }
      }
    }

    if (config.adapters) {
      for (const [componentName, componentAdapters] of Object.entries(
        config.adapters
      )) {
        const kebabName = toKebabCase(componentName);
        if (!this.adapters[kebabName]) {
          this.adapters[kebabName] = {};
        }
        Object.assign(this.adapters[kebabName], componentAdapters);
      }
    }
  }

  getParser(name: string): Parser | undefined {
    const parsers = this.parsers.get(name);
    if (!parsers || parsers.length === 0) return undefined;
    if (parsers.length === 1) return parsers[0];
    return (args) => {
      for (const parser of parsers) {
        parser(args);
      }
    };
  }

  private resolveKey(componentName: string): string {
    const kebab = toKebabCase(componentName);
    return COMPONENT_ALIASES[kebab] ?? kebab;
  }

  getDefaults(componentName: string): Record<string, number> {
    return this.componentDefaults[this.resolveKey(componentName)] || {};
  }

  getShorthands(componentName: string): Record<string, ShorthandMapping> {
    return this.componentShorthands[this.resolveKey(componentName)] || {};
  }

  getAllShorthands(): ComponentShorthands {
    return this.componentShorthands;
  }

  getEnums(componentName: string): Record<string, EnumMapping> {
    return this.componentEnums[this.resolveKey(componentName)] || {};
  }

  getValidations(): ValidationRule[] {
    return this.validations;
  }

  shouldSkip(componentName: string, propertyName: string): boolean {
    const skip = this.skipProperties[this.resolveKey(componentName)];
    return skip ? skip.has(propertyName) : false;
  }

  getAdapter(componentName: string, propertyName: string): Adapter | undefined {
    return this.adapters[this.resolveKey(componentName)]?.[propertyName];
  }

  getAdapterProperties(componentName: string): string[] {
    const componentAdapters = this.adapters[this.resolveKey(componentName)];
    return componentAdapters ? Object.keys(componentAdapters) : [];
  }

  /** Component kebab-names that declare an explicit shorthand for `attrKey` (e.g. `script` → entity-script). */
  getComponentNamesForShorthandKey(attrKey: string): string[] {
    const out: string[] = [];
    for (const [componentName, shorthands] of Object.entries(
      this.componentShorthands
    )) {
      if (attrKey in shorthands) {
        out.push(componentName);
      }
    }
    return out;
  }
}
