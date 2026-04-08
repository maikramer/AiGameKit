import { describe, expect, it } from 'bun:test';
import { RenderingPlugin } from '../../../src/plugins/rendering/plugin';
import { TextureRecipeLoadSystem, TextureRecipeCleanupSystem } from '../../../src/plugins/rendering/texture-recipe-system';
import { TextureRecipe, TextureRecipeLoaded } from '../../../src/plugins/rendering/texture-recipe';

describe('RenderingPlugin TextureRecipe wiring', () => {
  it('has TextureRecipeLoadSystem in systems', () => {
    expect(RenderingPlugin.systems).toContain(TextureRecipeLoadSystem);
  });

  it('has TextureRecipeCleanupSystem in systems', () => {
    expect(RenderingPlugin.systems).toContain(TextureRecipeCleanupSystem);
  });

  it('registers TextureRecipe component', () => {
    expect(RenderingPlugin.components.TextureRecipe).toBe(TextureRecipe);
  });

  it('registers TextureRecipeLoaded component', () => {
    expect(RenderingPlugin.components.TextureRecipeLoaded).toBe(TextureRecipeLoaded);
  });

  it('TextureRecipeLoadSystem is in setup group', () => {
    expect(TextureRecipeLoadSystem.group).toBe('setup');
  });

  it('TextureRecipeCleanupSystem is in draw group', () => {
    expect(TextureRecipeCleanupSystem.group).toBe('draw');
  });
});
