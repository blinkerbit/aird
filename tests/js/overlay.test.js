import { describe, expect, it } from 'vitest';
import { dismissTopOverlay, registerOverlay } from '/static/js/core/overlay.js';

function pressEscape() {
  const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  document.dispatchEvent(event);
  return event;
}

describe('overlay dismiss stack', () => {
  it('closes only the highest open layer', () => {
    const closed = [];
    const low = registerOverlay({
      priority: 10,
      isOpen: () => true,
      close() { closed.push('low'); },
    });
    const high = registerOverlay({
      priority: 20,
      isOpen: () => closed[0] !== 'high',
      close() { closed.push('high'); },
    });

    const first = pressEscape();
    expect(closed).toEqual(['high']);
    expect(first.defaultPrevented).toBe(true);

    pressEscape();
    expect(closed).toEqual(['high', 'low']);

    low();
    high();
  });

  it('leaves the event alone when nothing is open', () => {
    const off = registerOverlay({
      priority: 1,
      isOpen: () => false,
      close() { throw new Error('should not close'); },
    });
    const event = pressEscape();
    expect(event.defaultPrevented).toBe(false);
    expect(dismissTopOverlay()).toBe(false);
    off();
  });
});
