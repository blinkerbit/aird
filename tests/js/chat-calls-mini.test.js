import { describe, expect, it } from 'vitest';
import {
  clampMiniCallPosition,
  defaultMiniCallPosition,
} from '/static/js/chat-calls.js';

describe('minimized call position helpers', () => {
  it('clamps inside the viewport with a margin', () => {
    expect(clampMiniCallPosition({ left: -40, top: 900 }, 280, 56, 1280, 800, 10))
      .toEqual({ left: 10, top: 734 });
    expect(clampMiniCallPosition({ left: 2000, top: -20 }, 272, 184, 1280, 800, 10))
      .toEqual({ left: 998, top: 10 });
  });

  it('defaults to top-right and shifts left of a dock window', () => {
    const plain = defaultMiniCallPosition(280, 56, 1280, 800, null);
    expect(plain).toEqual({ left: 980, top: 76 });

    const shifted = defaultMiniCallPosition(280, 56, 1280, 800, {
      left: 1000,
      top: 40,
      width: 260,
      height: 420,
    });
    expect(shifted.left).toBeLessThan(plain.left);
    expect(shifted.top).toBe(76);
  });
});
