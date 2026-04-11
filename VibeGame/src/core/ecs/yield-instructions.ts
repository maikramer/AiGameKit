export interface YieldInstruction {
  readonly type: string;
}

export interface WaitForSecondsInstruction extends YieldInstruction {
  readonly type: 'waitForSeconds';
  readonly seconds: number;
}

export interface WaitForSecondsRealtimeInstruction extends YieldInstruction {
  readonly type: 'waitForSecondsRealtime';
  readonly seconds: number;
}

export interface WaitForEndOfFrameInstruction extends YieldInstruction {
  readonly type: 'waitForEndOfFrame';
}

export interface WaitForFixedUpdateInstruction extends YieldInstruction {
  readonly type: 'waitForFixedUpdate';
}

export interface WaitUntilInstruction extends YieldInstruction {
  readonly type: 'waitUntil';
  readonly predicate: () => boolean;
}

export interface WaitWhileInstruction extends YieldInstruction {
  readonly type: 'waitWhile';
  readonly predicate: () => boolean;
}

export type CoroutineYieldValue =
  | null
  | undefined
  | WaitForSecondsInstruction
  | WaitForSecondsRealtimeInstruction
  | WaitForEndOfFrameInstruction
  | WaitForFixedUpdateInstruction
  | WaitUntilInstruction
  | WaitWhileInstruction;

export function WaitForSeconds(seconds: number): WaitForSecondsInstruction {
  return { type: 'waitForSeconds', seconds };
}

export function WaitForSecondsRealtime(
  seconds: number
): WaitForSecondsRealtimeInstruction {
  return { type: 'waitForSecondsRealtime', seconds };
}

export function WaitForEndOfFrame(): WaitForEndOfFrameInstruction {
  return { type: 'waitForEndOfFrame' };
}

export function WaitForFixedUpdate(): WaitForFixedUpdateInstruction {
  return { type: 'waitForFixedUpdate' };
}

export function WaitUntil(predicate: () => boolean): WaitUntilInstruction {
  return { type: 'waitUntil', predicate };
}

export function WaitWhile(predicate: () => boolean): WaitWhileInstruction {
  return { type: 'waitWhile', predicate };
}
