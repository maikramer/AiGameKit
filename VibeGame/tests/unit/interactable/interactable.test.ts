import { describe, expect, it } from 'bun:test';
import {
  createInteractable,
  createPickup,
  InteractableBehaviour,
  PickupBehaviour,
  PlayerController,
  State,
  Transform,
  toMonoBehaviourModule,
  type PickupConfig,
} from 'vibegame';

interface StubState {
  time: { deltaTime: number; elapsed: number };
  destroyEntity: (eid: number) => void;
  exists: (eid: number) => boolean;
}

function makeState(destroyed: Set<number>): StubState {
  return {
    time: { deltaTime: 0.016, elapsed: 0 },
    destroyEntity: (eid: number) => {
      destroyed.add(eid);
    },
    exists: (eid: number) => !destroyed.has(eid),
  };
}

const PLAYER_EID = 7;
const PICKUP_EID = 11;
const INTERACT_EID = 13;

function place(eid: number, x: number, z: number): void {
  Transform.posX[eid] = x;
  Transform.posY[eid] = 0;
  Transform.posZ[eid] = z;
}

describe('PickupBehaviour', () => {
  it('collects when the player is within pickupRange (proximity trigger)', () => {
    const destroyed = new Set<number>();
    const state = makeState(destroyed);
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 1, 0);

    let pickedBy = 0;
    const cfg: PickupConfig = {
      pickupRange: 2,
      playerEid: PLAYER_EID,
      onPickup: (_s, pickerEid) => {
        pickedBy = pickerEid;
        return true;
      },
    };
    const pickup = new PickupBehaviour(cfg);
    pickup.start(state as unknown as State, PICKUP_EID);
    pickup.update(state as unknown as State, PICKUP_EID);

    expect(pickedBy).toBe(PLAYER_EID);
    expect(destroyed.has(PICKUP_EID)).toBe(true);
  });

  it('does NOT collect when the player is out of range', () => {
    const destroyed = new Set<number>();
    const state = makeState(destroyed);
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 10, 0);

    let calls = 0;
    const pickup = new PickupBehaviour({
      pickupRange: 2,
      playerEid: PLAYER_EID,
      onPickup: () => {
        calls += 1;
        return true;
      },
    });
    pickup.update(state as unknown as State, PICKUP_EID);

    expect(calls).toBe(0);
    expect(destroyed.has(PICKUP_EID)).toBe(false);
  });

  it('keeps the entity alive when onPickup returns false', () => {
    const destroyed = new Set<number>();
    const state = makeState(destroyed);
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 0.5, 0);

    const pickup = new PickupBehaviour({
      pickupRange: 2,
      playerEid: PLAYER_EID,
      onPickup: () => false,
    });
    pickup.update(state as unknown as State, PICKUP_EID);

    expect(destroyed.has(PICKUP_EID)).toBe(false);
  });

  it('does not fire again after a successful pickup (no double-destroy)', () => {
    const destroyed = new Set<number>();
    const state = makeState(destroyed);
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 1, 0);

    let calls = 0;
    const pickup = new PickupBehaviour({
      pickupRange: 2,
      playerEid: PLAYER_EID,
      onPickup: () => {
        calls += 1;
        return true;
      },
    });
    const s = state as unknown as State;
    pickup.update(s, PICKUP_EID);
    pickup.update(s, PICKUP_EID);

    expect(calls).toBe(1);
  });

  it('requires the configured key when trigger="input" (player must approve)', () => {
    const destroyed = new Set<number>();
    const state = makeState(destroyed);
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 1, 0);

    let calls = 0;
    let keyHeld = false;
    const pickup = new PickupBehaviour({
      pickupRange: 2,
      trigger: 'input',
      pickupKey: 'KeyE',
      playerEid: PLAYER_EID,
      isKeyDown: () => keyHeld,
      onPickup: () => {
        calls += 1;
        return true;
      },
    });
    const s = state as unknown as State;

    pickup.update(s, PICKUP_EID);
    expect(calls).toBe(0);

    keyHeld = true;
    pickup.update(s, PICKUP_EID);
    expect(calls).toBe(1);
    expect(destroyed.has(PICKUP_EID)).toBe(true);
  });

  it('applies visualSpin to Transform.eulerY', () => {
    const state = makeState(new Set());
    state.time.deltaTime = 0.5;
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 100, 0);

    Transform.eulerY[PICKUP_EID] = 0;
    const pickup = new PickupBehaviour({
      pickupRange: 1,
      visualSpin: 3,
      playerEid: PLAYER_EID,
      onPickup: () => false,
    });
    pickup.update(state as unknown as State, PICKUP_EID);

    expect(Transform.eulerY[PICKUP_EID]).toBeCloseTo(1.5, 5);
  });
});

describe('InteractableBehaviour', () => {
  it('does NOT auto-activate while in range without input (input is the gate)', () => {
    const state = makeState(new Set());
    place(INTERACT_EID, 0, 0);
    place(PLAYER_EID, 1, 0);

    let calls = 0;
    const itc = new InteractableBehaviour({
      range: 2,
      promptKey: 'KeyF',
      playerEid: PLAYER_EID,
      isKeyDown: () => false,
      onActivate: () => {
        calls += 1;
      },
    });
    itc.update(state as unknown as State, INTERACT_EID);

    expect(calls).toBe(0);
  });

  it('activates when in range AND the prompt key is pressed', () => {
    const state = makeState(new Set());
    place(INTERACT_EID, 0, 0);
    place(PLAYER_EID, 1, 0);

    let activatedBy = 0;
    const itc = new InteractableBehaviour({
      range: 2,
      promptKey: 'KeyF',
      playerEid: PLAYER_EID,
      isKeyDown: () => true,
      onActivate: (_s, eid) => {
        activatedBy = eid;
      },
    });
    itc.update(state as unknown as State, INTERACT_EID);

    expect(activatedBy).toBe(PLAYER_EID);
  });

  it('does NOT activate when out of range even if the key is pressed', () => {
    const state = makeState(new Set());
    place(INTERACT_EID, 0, 0);
    place(PLAYER_EID, 50, 0);

    let calls = 0;
    const itc = new InteractableBehaviour({
      range: 2,
      promptKey: 'KeyF',
      playerEid: PLAYER_EID,
      isKeyDown: () => true,
      onActivate: () => {
        calls += 1;
      },
    });
    itc.update(state as unknown as State, INTERACT_EID);

    expect(calls).toBe(0);
  });

  it('fires only once per key press (edge-triggered), not every frame while held', () => {
    const state = makeState(new Set());
    place(INTERACT_EID, 0, 0);
    place(PLAYER_EID, 1, 0);

    let calls = 0;
    const itc = new InteractableBehaviour({
      range: 2,
      promptKey: 'KeyF',
      playerEid: PLAYER_EID,
      isKeyDown: () => true,
      onActivate: () => {
        calls += 1;
      },
    });
    const s = state as unknown as State;
    itc.update(s, INTERACT_EID);
    itc.update(s, INTERACT_EID);
    itc.update(s, INTERACT_EID);

    expect(calls).toBe(1);
  });

  it('re-arms after the key is released and pressed again', () => {
    const state = makeState(new Set());
    place(INTERACT_EID, 0, 0);
    place(PLAYER_EID, 1, 0);

    let calls = 0;
    let held = true;
    const itc = new InteractableBehaviour({
      range: 2,
      promptKey: 'KeyF',
      playerEid: PLAYER_EID,
      isKeyDown: () => held,
      onActivate: () => {
        calls += 1;
      },
    });
    const s = state as unknown as State;
    itc.update(s, INTERACT_EID);
    itc.update(s, INTERACT_EID);
    held = false;
    itc.update(s, INTERACT_EID);
    held = true;
    itc.update(s, INTERACT_EID);

    expect(calls).toBe(2);
  });
});

describe('createPickup / createInteractable factories', () => {
  it('createPickup returns an instantiable MonoBehaviour that runs the baked config', () => {
    const destroyed = new Set<number>();
    const state = makeState(destroyed);
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 0, 0.5);

    const Ctor = createPickup({
      pickupRange: 1,
      playerEid: PLAYER_EID,
      onPickup: () => true,
    });
    const inst = new Ctor();
    expect(inst).toBeInstanceOf(PickupBehaviour);
    expect(typeof inst.update).toBe('function');

    inst.update(state as unknown as State, PICKUP_EID);
    expect(destroyed.has(PICKUP_EID)).toBe(true);
  });

  it('createInteractable returns an instantiable MonoBehaviour honoring the gate', () => {
    const state = makeState(new Set());
    place(INTERACT_EID, 0, 0);
    place(PLAYER_EID, 0.5, 0);

    let calls = 0;
    const Ctor = createInteractable({
      range: 1,
      promptKey: 'KeyE',
      playerEid: PLAYER_EID,
      isKeyDown: () => false,
      onActivate: () => {
        calls += 1;
      },
    });
    const inst = new Ctor();
    expect(inst).toBeInstanceOf(InteractableBehaviour);
    expect(typeof inst.update).toBe('function');

    inst.update(state as unknown as State, INTERACT_EID);
    expect(calls).toBe(0);
  });
});

describe('toMonoBehaviourModule adapter', () => {
  it('produces a module whose start/update/onDestroy bind state+entity', () => {
    const destroyed = new Set<number>();
    const state = makeState(destroyed);
    place(PICKUP_EID, 0, 0);
    place(PLAYER_EID, 0, 0);

    const inst = new PickupBehaviour({
      pickupRange: 1,
      playerEid: PLAYER_EID,
      onPickup: () => true,
    });
    type MinimalCtx = { state: State; entity: number };
    const mod = toMonoBehaviourModule(inst) as {
      start?: (ctx: MinimalCtx) => void;
      update?: (ctx: MinimalCtx) => void;
      onDestroy?: (ctx: MinimalCtx) => void;
    };

    const ctx: MinimalCtx = {
      state: state as unknown as State,
      entity: PICKUP_EID,
    };
    mod.start?.(ctx);
    mod.update?.(ctx);

    expect(destroyed.has(PICKUP_EID)).toBe(true);
  });
});

describe('player auto-detection via PlayerController query', () => {
  it('finds the player through a PlayerController component when playerEid is omitted', () => {
    const state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player-controller', PlayerController);

    const player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);
    Transform.posX[player] = 0;
    Transform.posZ[player] = 0;

    const pickup = state.createEntity();
    state.addComponent(pickup, Transform);
    Transform.posX[pickup] = 0.5;
    Transform.posZ[pickup] = 0;

    let pickedBy = 0;
    const beh = new PickupBehaviour({
      pickupRange: 2,
      onPickup: (_s, eid) => {
        pickedBy = eid;
        return false;
      },
    });
    beh.update(state, pickup);

    expect(pickedBy).toBe(player);
  });
});
