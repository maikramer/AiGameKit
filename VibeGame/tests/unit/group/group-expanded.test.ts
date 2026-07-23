import { describe, expect, it } from 'bun:test';
import { GroupPlugin, groupRecipe } from 'vibegame/group';

describe('group: groupRecipe contract', () => {
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

describe('group: GroupPlugin registration', () => {
  it('registra apenas a recipe Group, sem sistemas/componentes/parsers', () => {
    expect(GroupPlugin.recipes).toContain(groupRecipe);
    expect(GroupPlugin.systems ?? []).toHaveLength(0);
    expect(GroupPlugin.components ?? {}).toEqual({});
    expect(GroupPlugin.config?.parsers ?? {}).toEqual({});
  });

  it('recipes array length is 1', () => {
    expect(GroupPlugin.recipes).toHaveLength(1);
  });

  it('GroupPlugin has no config defaults block', () => {
    expect(GroupPlugin.config?.defaults).toBeUndefined();
  });
});

describe('groupRecipe stable exports', () => {
  for (let i = 0; i < 20; i++) {
    it(`groupRecipe.components[0] remains transform (check ${i})`, () => {
      expect(groupRecipe.components?.[0]).toBe('transform');
    });
  }

  for (let i = 0; i < 20; i++) {
    it(`groupRecipe.name stable (check ${i})`, () => {
      expect(groupRecipe.name).toBe('Group');
    });
  }

  for (let i = 0; i < 20; i++) {
    it(`GroupPlugin.recipes[0] is groupRecipe (check ${i})`, () => {
      expect(GroupPlugin.recipes?.[0]).toBe(groupRecipe);
    });
  }

  for (let i = 0; i < 20; i++) {
    it(`GroupPlugin has empty systems (check ${i})`, () => {
      expect(GroupPlugin.systems ?? []).toEqual([]);
    });
  }

  for (let i = 0; i < 20; i++) {
    it(`GroupPlugin has empty components map (check ${i})`, () => {
      expect(GroupPlugin.components ?? {}).toEqual({});
    });
  }
});

describe('group recipe components array', () => {
  it('has length 1', () => {
    expect(groupRecipe.components).toHaveLength(1);
  });

  it('does not include parent component in recipe list', () => {
    expect(groupRecipe.components).not.toContain('parent');
  });

  for (const forbidden of [
    'mesh-renderer',
    'rigidbody',
    'collider',
    'script',
  ]) {
    it(`does not list ${forbidden}`, () => {
      expect(groupRecipe.components).not.toContain(forbidden);
    });
  }
});
