import { describe, expect, it } from 'vitest';
import { wirePersistedToggle } from '/static/js/browse/persisted-toggle.js';

describe('wirePersistedToggle', () => {
  it('restores the stored flag and flips it on click', () => {
    const data = new Map([['layout', 'grid']]);
    const storage = {
      getItem: (key) => (data.has(key) ? data.get(key) : null),
      setItem: (key, value) => data.set(key, String(value)),
    };
    document.body.innerHTML = '<div id="panel"></div><button id="toggle"></button>';
    const panel = document.getElementById('panel');
    wirePersistedToggle({
      storage,
      key: 'layout',
      onValue: 'grid',
      offValue: 'list',
      buttonId: 'toggle',
      isOn: () => panel.classList.contains('grid'),
      apply: (on) => panel.classList.toggle('grid', on),
    });
    expect(panel.classList.contains('grid')).toBe(true);
    document.getElementById('toggle').click();
    expect(panel.classList.contains('grid')).toBe(false);
    expect(storage.getItem('layout')).toBe('list');
  });
});
