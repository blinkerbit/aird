import { describe, expect, it } from 'vitest';
import { placeNearAnchor } from '/static/js/core/anchor.js';

const viewport = { width: 400, height: 300 };

describe('placeNearAnchor', () => {
  it('opens below the anchor when it fits', () => {
    expect(placeNearAnchor({
      anchor: { top: 10, bottom: 30, left: 20 },
      panel: { width: 100, height: 80 },
      viewport,
    })).toEqual({ top: 36, left: 20 });
  });

  it('flips above and clamps to the left edge', () => {
    expect(placeNearAnchor({
      anchor: { top: 250, bottom: 270, left: 350 },
      panel: { width: 120, height: 80 },
      viewport,
      safe: { left: 4, right: 4, top: 2, bottom: 2 },
    })).toEqual({ top: 164, left: 268 });
  });
});
