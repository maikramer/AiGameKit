// NPC idle variety: periodic one-shot gestures (talk/foldarms/lean/call/…)
// between idle loops, plus interaction reactions (yes/no head nods). Keeps
// villages feeling alive without bespoke per-NPC state machines.
import type { GltfAnimator } from 'vibegame';

export interface NpcIdleConfig {
  /** Base loop clip (default 'idle'). */
  idle?: string;
  /** One-shot gesture clip names played between idles. */
  gestures: string[];
  /** Min/max seconds between gestures (default 8–16 s). */
  minInterval?: number;
  maxInterval?: number;
}

export class NpcIdleAnimator {
  private timer: number;

  constructor(private readonly cfg: NpcIdleConfig) {
    this.timer = this.nextDelay();
  }

  private get idleClip(): string {
    return this.cfg.idle ?? 'idle';
  }

  private nextDelay(): number {
    const lo = this.cfg.minInterval ?? 8;
    const hi = this.cfg.maxInterval ?? 16;
    return lo + Math.random() * Math.max(0, hi - lo);
  }

  /** Play the base idle (call once when the animator is ready). */
  start(animator: GltfAnimator | null): void {
    animator?.play(this.idleClip);
  }

  /** Tick the gesture scheduler (call every frame with the NPC animator). */
  update(dt: number, animator: GltfAnimator | null): void {
    if (!animator) return;
    this.timer -= dt;
    if (this.timer > 0) return;
    this.timer = this.nextDelay();
    this.playGesture(animator, this.pickGesture(animator));
  }

  /**
   * Gesture pools are authored against a clip library, but each NPC GLB pack
   * ships only a subset (e.g. the guard pack has no `foldarms`/`lean`). Pick
   * variety only from clips the animator actually has; with none available
   * the NPC simply stays on its idle loop instead of warning per gesture.
   */
  private pickGesture(animator: GltfAnimator): string | undefined {
    const names = animator.clipNames;
    if (names.length === 0) return undefined;
    const available = this.cfg.gestures.filter((g) => names.includes(g));
    if (available.length === 0) return undefined;
    return available[Math.floor(Math.random() * available.length)];
  }

  /** One-shot interaction reaction (e.g. 'yes' after a heal). */
  react(animator: GltfAnimator | null, gesture: string): void {
    if (!animator) return;
    // Same pack-subset rule as pickGesture — never request a missing clip.
    if (!animator.clipNames.includes(gesture)) return;
    this.timer = this.nextDelay();
    this.playGesture(animator, gesture);
  }

  private playGesture(
    animator: GltfAnimator,
    gesture: string | undefined
  ): void {
    if (!gesture) return;
    const played = animator.playOverride(gesture, {
      loop: false,
      onFinished: () => animator.play(this.idleClip),
    });
    if (!played) animator.play(this.idleClip);
  }
}
