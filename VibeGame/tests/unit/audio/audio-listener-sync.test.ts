import { describe, expect, it } from 'bun:test';
import { AudioListener } from '../../../src/plugins/audio/components';

describe('AudioListener component (T7: camera sync)', () => {
  it('has posX field', () => {
    expect(AudioListener.posX).toBeDefined();
  });

  it('has posY field', () => {
    expect(AudioListener.posY).toBeDefined();
  });

  it('has posZ field', () => {
    expect(AudioListener.posZ).toBeDefined();
  });

  it('is a valid bitecs component object', () => {
    expect(typeof AudioListener).toBe('object');
    expect(AudioListener).not.toBeNull();
  });
});
