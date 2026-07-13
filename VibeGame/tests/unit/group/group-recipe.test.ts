import { describe, expect, it } from 'bun:test';
import { GroupPlugin, groupRecipe } from 'vibegame/group';

describe('group: groupRecipe', () => {
  it('expõe o nome "Group"', () => {
    expect(groupRecipe.name).toBe('Group');
  });

  it('depende apenas do componente transform', () => {
    expect(groupRecipe.components).toEqual(['transform']);
  });

  it('não assume posse dos filhos (o parser genérico auto-parenta)', () => {
    expect(groupRecipe.parserOwnsChildren).toBeUndefined();
  });

  it('não faz merge — cada <Group> é a sua própria entidade endereçável', () => {
    expect(groupRecipe.merge).toBeUndefined();
  });
});

describe('group: GroupPlugin', () => {
  it('registra apenas a recipe Group, sem sistemas/componentes/parsers', () => {
    expect(GroupPlugin.recipes).toContain(groupRecipe);
    // A hierarquia é fornecida pelo core (Parent + TransformHierarchySystem),
    // pelo que o plugin não declara nada além da recipe.
    expect(GroupPlugin.systems ?? []).toHaveLength(0);
    expect(GroupPlugin.components ?? {}).toEqual({});
    expect(GroupPlugin.config?.parsers ?? {}).toEqual({});
  });
});
