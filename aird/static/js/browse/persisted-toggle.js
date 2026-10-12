/**
 * A button that flips a boolean stored in localStorage and applies it to the page.
 */

export function readStoredFlag(storage, key, onValue) {
  try {
    return storage.getItem(key) === onValue;
  } catch {
    return false;
  }
}

export function writeStoredFlag(storage, key, on, onValue, offValue) {
  try {
    storage.setItem(key, on ? onValue : offValue);
  } catch {
    /* quota / private mode */
  }
}

/**
 * @param {{
 *   storage?: Storage,
 *   key: string,
 *   onValue: string,
 *   offValue: string,
 *   buttonId: string,
 *   isOn: () => boolean,
 *   apply: (on: boolean) => void,
 * }} spec
 */
export function wirePersistedToggle(spec) {
  const storage = spec.storage || localStorage;
  spec.apply(readStoredFlag(storage, spec.key, spec.onValue));
  const btn = document.getElementById(spec.buttonId);
  if (!btn) return;
  btn.addEventListener('click', () => {
    const next = !spec.isOn();
    writeStoredFlag(storage, spec.key, next, spec.onValue, spec.offValue);
    spec.apply(next);
  });
}
