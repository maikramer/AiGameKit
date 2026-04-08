import { describe, expect, it } from 'bun:test';
import { FogPlugin } from '../../../src/plugins/fog/plugin';

describe('Fog Enum Mapping', () => {
  describe('mode enum', () => {
    const mode = FogPlugin.config!.enums!.fog.mode;

    it('should map "exponential" to 0', () => {
      expect(mode.exponential).toBe(0);
    });

    it('should map "exponential-squared" to 1', () => {
      expect(mode['exponential-squared']).toBe(1);
    });

    it('should map "linear" to 2', () => {
      expect(mode.linear).toBe(2);
    });

    it('should have exactly 3 entries', () => {
      expect(Object.keys(mode)).toHaveLength(3);
    });
  });

  describe('quality enum', () => {
    const quality = FogPlugin.config!.enums!.fog.quality;

    it('should map "low" to 0', () => {
      expect(quality.low).toBe(0);
    });

    it('should map "medium" to 1', () => {
      expect(quality.medium).toBe(1);
    });

    it('should map "high" to 2', () => {
      expect(quality.high).toBe(2);
    });

    it('should have exactly 3 entries', () => {
      expect(Object.keys(quality)).toHaveLength(3);
    });
  });
});
