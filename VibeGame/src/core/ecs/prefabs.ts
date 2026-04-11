/* global structuredClone */

export interface TemplateData {
  components: Record<string, Record<string, number>>;
}

export interface InstantiateOptions {
  overrides?: Record<string, Record<string, number>>;
  parent?: number;
}

export function deepCloneTemplate(data: TemplateData): TemplateData {
  return structuredClone(data);
}
