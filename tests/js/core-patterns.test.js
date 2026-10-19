import { afterEach, describe, expect, it, vi } from 'vitest';
import { createEventBus } from '/static/js/core/events.js';
import { HttpError, postForm, postJson, requestJson } from '/static/js/core/http.js';
import { createStore } from '/static/js/core/store.js';
import { createEl, delegate, onById, wireButtons } from '/static/js/core/dom.js';

describe('http gateway', () => {
  afterEach(() => {
    document.cookie = '_xsrf=; Max-Age=0';
    vi.unstubAllGlobals();
  });

  function stubFetch(handler) {
    const fetchMock = vi.fn(handler);
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('serialises JSON, injects XSRF, and parses the response', async () => {
    document.cookie = '_xsrf=tok';
    const fetchMock = stubFetch(async () => new Response(JSON.stringify({ ok: true }), {
      headers: { 'Content-Type': 'application/json' },
    }));
    await expect(postJson('/api/x', { a: 1 })).resolves.toEqual({ ok: true });
    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.method).toBe('POST');
    expect(opts.body).toBe('{"a":1}');
    expect(opts.headers.get('X-XSRFToken')).toBe('tok');
    expect(opts.headers.get('Content-Type')).toBe('application/json');
  });

  it('throws HttpError with the server payload on failure', async () => {
    stubFetch(async () => new Response(JSON.stringify({ error: 'nope' }), { status: 403 }));
    const err = await postJson('/api/x', {}).catch((e) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect(err.status).toBe(403);
    expect(err.message).toBe('nope');
    expect(err.data).toEqual({ error: 'nope' });
  });

  it('returns the payload without throwing when throwOnError is false', async () => {
    stubFetch(async () => new Response(JSON.stringify({ error: 'bad' }), { status: 500 }));
    await expect(postJson('/api/x', {}, { throwOnError: false })).resolves.toEqual({ error: 'bad' });
  });

  it('tolerates unparsable JSON bodies', async () => {
    stubFetch(async () => new Response('not json'));
    await expect(requestJson('/api/x')).resolves.toEqual({});
  });

  it('posts form data and returns the raw response', async () => {
    document.cookie = '_xsrf=tok2';
    const fetchMock = stubFetch(async () => new Response('done'));
    const params = new URLSearchParams({ path: 'a.txt' });
    const res = await postForm('/rename', params);
    expect(await res.text()).toBe('done');
    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.body).toBe('path=a.txt');
    expect(opts.headers.get('Content-Type')).toBe('application/x-www-form-urlencoded');
    expect(opts.headers.get('X-XSRFToken')).toBe('tok2');
  });
});

describe('createEventBus', () => {
  it('delivers, unsubscribes, and fires once-listeners once', () => {
    const bus = createEventBus();
    const seen = [];
    const off = bus.on('sel', (p) => seen.push(['a', p]));
    bus.once('sel', (p) => seen.push(['once', p]));
    bus.emit('sel', 1);
    off();
    bus.emit('sel', 2);
    expect(seen).toEqual([['a', 1], ['once', 1]]);
    expect(bus.emit('missing')).toBe(false);
  });

  it('isolates a throwing listener from the others', () => {
    const bus = createEventBus();
    const seen = [];
    bus.on('x', () => { throw new Error('boom'); });
    bus.on('x', (p) => seen.push(p));
    expect(bus.emit('x', 7)).toBe(true);
    expect(seen).toEqual([7]);
  });
});

describe('createStore', () => {
  it('merges patches, notifies on change only, and supports select', () => {
    const store = createStore({ count: 0, name: 'a' });
    const counts = [];
    const unsub = store.select((s) => s.count, (v) => counts.push(v));
    store.setState({ name: 'b' });
    store.setState({ count: 0 });
    store.setState({ count: 2 });
    store.setState((s) => ({ count: s.count + 1 }));
    expect(counts).toEqual([2, 3]);
    expect(store.getState()).toEqual({ count: 3, name: 'b' });
    unsub();
    store.setState({ count: 9 });
    expect(counts).toEqual([2, 3]);
  });

  it('hands out copies so callers cannot mutate state', () => {
    const store = createStore({ n: 1 });
    store.getState().n = 99;
    expect(store.getState().n).toBe(1);
  });
});

describe('dom helpers', () => {
  it('creates elements with class, dataset, and text', () => {
    const el = createEl('button', { className: 'btn', dataset: { action: 'go' } }, 'Go');
    expect(el.tagName).toBe('BUTTON');
    expect(el.className).toBe('btn');
    expect(el.dataset.action).toBe('go');
    expect(el.textContent).toBe('Go');
  });

  it('binds by id and wires a button map, skipping missing ids', () => {
    document.body.innerHTML = '<button id="a"></button>';
    const clicks = [];
    expect(onById('a', 'click', () => clicks.push('a'))?.id).toBe('a');
    wireButtons({ a: () => clicks.push('wired'), missing: () => clicks.push('x') });
    document.getElementById('a').click();
    expect(clicks).toEqual(['a', 'wired']);
  });

  it('delegates events to matching descendants only', () => {
    document.body.innerHTML = '<div id="root"><button class="row"><span>in</span></button><p>out</p></div>';
    const root = document.getElementById('root');
    const hits = [];
    const off = delegate(root, 'click', '.row', (e, el) => hits.push(el.className));
    root.querySelector('span').click();
    root.querySelector('p').click();
    off();
    root.querySelector('span').click();
    expect(hits).toEqual(['row']);
  });
});
