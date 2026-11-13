import { newId } from './hlc.js';
import { midOrder } from './order-key.js';
import { blockType } from './types.js';

export function listReducer(state, action) {
  switch (action.type) {
    case 'REPLACE':
      return {
        ...state,
        items: action.payload.items || [],
        activeItemId: action.payload.activeItemId ?? state.activeItemId,
        cursorPosition: action.payload.cursorPosition ?? 0,
      };
    case 'SPLIT_ITEM':
      return splitItem(state, action.payload);
    case 'CONVERT_TYPE':
      return convertType(state, action.payload);
    case 'INDENT_ITEM':
      return indentItem(state, action.payload);
    case 'OUTDENT_ITEM':
      return outdentItem(state, action.payload);
    case 'TOGGLE_CHECKBOX':
      return toggle(state, action.payload);
    case 'DELETE_ITEM':
      return deleteItem(state, action.payload);
    case 'UPDATE_CONTENT':
      return updateContent(state, action.payload);
    case 'MERGE_PREV':
      return mergePrev(state, action.payload);
    case 'REORDER':
      return reorder(state, action.payload);
    case 'INSERT_EMPTY':
      return insertEmpty(state, action.payload);
    default:
      return state;
  }
}

function indexOf(items, id) {
  return items.findIndex((row) => row.id === id);
}

function splitItem(state, { itemId, splitIndex }) {
  const index = indexOf(state.items, itemId);
  if (index < 0) return state;
  const current = state.items[index];
  const head = current.content.slice(0, splitIndex);
  const tail = current.content.slice(splitIndex);
  const updated = { ...current, content: head, updatedAt: Date.now() };
  const next = {
    id: newId(),
    listId: current.listId,
    type: current.type,
    content: tail,
    order: midOrder(current.order, state.items[index + 1]?.order),
    depth: current.depth,
    isChecked: false,
    isCollapsed: false,
    tags: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
    version: 1,
  };
  const items = [...state.items];
  items.splice(index, 1, updated, next);
  return { ...state, items, activeItemId: next.id, cursorPosition: 0 };
}

function convertType(state, { itemId, newType }) {
  const type = blockType(newType);
  return {
    ...state,
    items: state.items.map((item) => (
      item.id === itemId
        ? {
          ...item,
          type,
          isChecked: type === 'checkbox' ? item.isChecked : false,
          updatedAt: Date.now(),
        }
        : item
    )),
  };
}

function indentItem(state, { itemId }) {
  const index = indexOf(state.items, itemId);
  if (index <= 0) return state;
  const prev = state.items[index - 1];
  const current = state.items[index];
  if (current.depth >= prev.depth + 1 || current.depth >= 5) return state;
  return {
    ...state,
    items: state.items.map((item) => (
      item.id === itemId ? { ...item, depth: item.depth + 1, updatedAt: Date.now() } : item
    )),
  };
}

function outdentItem(state, { itemId }) {
  return {
    ...state,
    items: state.items.map((item) => (
      item.id === itemId && item.depth > 0
        ? { ...item, depth: item.depth - 1, updatedAt: Date.now() }
        : item
    )),
  };
}

function toggle(state, { itemId }) {
  return {
    ...state,
    items: state.items.map((item) => (
      item.id === itemId && item.type === 'checkbox'
        ? { ...item, isChecked: !item.isChecked, updatedAt: Date.now() }
        : item
    )),
  };
}

function deleteItem(state, { itemId }) {
  const index = indexOf(state.items, itemId);
  if (index < 0) return state;
  const nextActive = index > 0 ? state.items[index - 1].id : state.items[1]?.id || null;
  return {
    ...state,
    items: state.items.filter((item) => item.id !== itemId),
    activeItemId: nextActive,
    cursorPosition: nextActive ? (state.items[index - 1]?.content.length || 0) : 0,
  };
}

function updateContent(state, { itemId, content }) {
  return {
    ...state,
    items: state.items.map((item) => (
      item.id === itemId ? { ...item, content, updatedAt: Date.now() } : item
    )),
  };
}

function mergePrev(state, { itemId }) {
  const index = indexOf(state.items, itemId);
  if (index <= 0) return state;
  const prev = state.items[index - 1];
  const current = state.items[index];
  const merged = {
    ...prev,
    content: `${prev.content}${current.content}`,
    updatedAt: Date.now(),
  };
  const items = [...state.items];
  items.splice(index - 1, 2, merged);
  return {
    ...state,
    items,
    activeItemId: merged.id,
    cursorPosition: prev.content.length,
  };
}

function reorder(state, { itemId, direction }) {
  const index = indexOf(state.items, itemId);
  const target = direction === 'up' ? index - 1 : index + 1;
  if (index < 0 || target < 0 || target >= state.items.length) return state;
  const items = [...state.items];
  const a = items[index];
  const b = items[target];
  const aOrder = a.order;
  items[index] = { ...a, order: b.order, updatedAt: Date.now() };
  items[target] = { ...b, order: aOrder, updatedAt: Date.now() };
  items.sort((x, y) => String(x.order).localeCompare(String(y.order)));
  return { ...state, items };
}

function insertEmpty(state, { afterId, type, depth }) {
  const index = afterId ? indexOf(state.items, afterId) : state.items.length - 1;
  const prev = index >= 0 ? state.items[index] : null;
  const next = index >= 0 ? state.items[index + 1] : null;
  const item = {
    id: newId(),
    listId: prev?.listId || state.items[0]?.listId || '',
    type: blockType(type || prev?.type || 'paragraph'),
    content: '',
    order: midOrder(prev?.order, next?.order),
    depth: depth == null ? (prev?.depth || 0) : depth,
    isChecked: false,
    isCollapsed: false,
    tags: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
    version: 1,
  };
  const items = [...state.items];
  items.splice(index + 1, 0, item);
  return { ...state, items, activeItemId: item.id, cursorPosition: 0 };
}

/** Diff two item arrays into sync ops relative to previous snapshot. */
export function opsFromTransition(prevItems, nextItems, actorId, clock, listId) {
  const prevMap = new Map(prevItems.map((row) => [row.id, row]));
  const nextMap = new Map(nextItems.map((row) => [row.id, row]));
  const ops = [];
  for (const item of nextItems) {
    const before = prevMap.get(item.id);
    if (!before) {
      const index = nextItems.indexOf(item);
      ops.push({
        opId: newId(),
        blockId: item.id,
        listId,
        actorId,
        hlcTimestamp: clock.tick(),
        action: 'INSERT',
        payload: {
          content: item.content,
          type: item.type,
          depth: item.depth,
          order: item.order,
          afterId: index > 0 ? nextItems[index - 1].id : null,
          tags: item.tags,
          isChecked: item.isChecked,
        },
      });
      continue;
    }
    if (before.content !== item.content) {
      ops.push({
        opId: newId(),
        blockId: item.id,
        listId,
        actorId,
        hlcTimestamp: clock.tick(),
        action: 'UPDATE_CONTENT',
        payload: { content: item.content },
      });
    }
    if (before.type !== item.type) {
      ops.push({
        opId: newId(),
        blockId: item.id,
        listId,
        actorId,
        hlcTimestamp: clock.tick(),
        action: 'CHANGE_TYPE',
        payload: { type: item.type },
      });
    }
    if (before.isChecked !== item.isChecked) {
      ops.push({
        opId: newId(),
        blockId: item.id,
        listId,
        actorId,
        hlcTimestamp: clock.tick(),
        action: 'TOGGLE',
        payload: { isChecked: item.isChecked },
      });
    }
    if (before.depth !== item.depth || before.order !== item.order) {
      ops.push({
        opId: newId(),
        blockId: item.id,
        listId,
        actorId,
        hlcTimestamp: clock.tick(),
        action: 'MOVE',
        payload: { depth: item.depth, order: item.order, isCollapsed: item.isCollapsed },
      });
    }
  }
  for (const item of prevItems) {
    if (!nextMap.has(item.id)) {
      ops.push({
        opId: newId(),
        blockId: item.id,
        listId,
        actorId,
        hlcTimestamp: clock.tick(),
        action: 'DELETE',
        payload: {},
      });
    }
  }
  return ops;
}
