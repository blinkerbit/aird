/**
 * DOM wiring helpers — element creation, direct binding, and delegation.
 *
 * Pattern: event delegation. `delegate` attaches one listener on a stable
 * ancestor and dispatches by selector, so list/table UIs stop rebinding
 * per-row handlers after every render. `onById`/`wireButtons` collapse the
 * repeated `getElementById(id)?.addEventListener(...)` boilerplate, and
 * every helper returns an unsubscribe/off function for teardown.
 */

/** Run `fn` once the DOM is parsed (immediately when it already is). */
export function ready(fn) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fn, { once: true });
  } else {
    fn();
  }
}

/** Create an element with class/dataset/attributes and optional text. */
export function createEl(tag, props, text) {
  const el = document.createElement(tag);
  if (props) {
    for (const key of Object.keys(props)) {
      if (key === 'className') el.className = props[key];
      else if (key === 'dataset') {
        for (const [dk, dv] of Object.entries(props[key])) {
          el.dataset[dk] = dv;
        }
      } else el.setAttribute(key, props[key]);
    }
  }
  if (text != null) el.textContent = text;
  return el;
}

/** Attach a listener; returns a function that detaches it. */
export function on(target, type, handler, options) {
  if (!target) return () => {};
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}

/** Attach `handler` to the element with `id`; returns the element or null. */
export function onById(id, type, handler, options) {
  const el = document.getElementById(id);
  on(el, type, handler, options);
  return el;
}

/** Wire a `{ elementId: clickHandler }` map; missing ids are skipped. */
export function wireButtons(bindings) {
  for (const [id, handler] of Object.entries(bindings)) {
    onById(id, 'click', handler);
  }
}

/**
 * Delegate `type` events under `root` to elements matching `selector`.
 * The handler receives `(event, matchedElement)`. Returns an off function.
 */
export function delegate(root, type, selector, handler) {
  if (!root) return () => {};
  const listener = (event) => {
    const matched = event.target?.closest?.(selector);
    if (matched && root.contains(matched)) handler(event, matched);
  };
  return on(root, type, listener);
}
