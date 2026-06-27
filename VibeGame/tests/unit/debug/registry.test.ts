import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  getDebugRegistry,
  getDebugRegistryHandle,
  registerDebugAction,
  registerDebugVar,
} from '../../../src/plugins/debug/registry';

describe('Debug registry', () => {
  it('registers and invokes a debug action', () => {
    const state = new State();
    let calledWith: unknown[] = [];
    registerDebugAction(state, 'bump', (...args: unknown[]) => {
      calledWith = args;
      return 'ok';
    });

    const handle = getDebugRegistryHandle(state);
    expect(handle.hasAction('bump')).toBe(true);
    expect(handle.actionNames()).toEqual(['bump']);

    const result = handle.callAction('bump', 1, 2, 3);
    expect(result).toBe('ok');
    expect(calledWith).toEqual([1, 2, 3]);
  });

  it('registers a read/write debug var', () => {
    const state = new State();
    let value = 10;
    registerDebugVar(
      state,
      'counter',
      () => value,
      (v: unknown) => {
        value = Number(v);
      }
    );

    const handle = getDebugRegistryHandle(state);
    expect(handle.hasVar('counter')).toBe(true);
    expect(handle.varNames()).toEqual(['counter']);
    expect(handle.getVar('counter')).toBe(10);

    expect(handle.setVar('counter', 42)).toBe(true);
    expect(value).toBe(42);
    expect(handle.getVar('counter')).toBe(42);
  });

  it('reports false for unknown actions/vars', () => {
    const state = new State();
    const handle = getDebugRegistryHandle(state);
    expect(handle.hasAction('nope')).toBe(false);
    expect(handle.hasVar('nope')).toBe(false);
    expect(handle.callAction('nope')).toBeUndefined();
    expect(handle.getVar('nope')).toBeUndefined();
    expect(handle.setVar('nope', 1)).toBe(false);
  });

  it('overwrites an existing action with the same name', () => {
    const state = new State();
    registerDebugAction(state, 'ping', () => 'first');
    registerDebugAction(state, 'ping', () => 'second', {
      description: 'ping pong',
    });
    const handle = getDebugRegistryHandle(state);
    expect(handle.actionNames()).toEqual(['ping']);
    expect(handle.callAction('ping')).toBe('second');
    expect(getDebugRegistry(state).actions.get('ping')?.description).toBe(
      'ping pong'
    );
  });

  it('treats a var without setter as read-only', () => {
    const state = new State();
    registerDebugVar(state, 'solo', () => 'locked');
    const handle = getDebugRegistryHandle(state);
    expect(handle.getVar('solo')).toBe('locked');
    expect(handle.setVar('solo', 'x')).toBe(false);
  });
});
