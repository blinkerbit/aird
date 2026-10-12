"use strict";

import {
  emptyTypeFilter,
  isFiltering,
  normalizeTypeFilter,
  parseExtensions,
  rowMatchesFilter,
  toggleCategory,
  typeFilterFromSearch,
} from "./type-filter-model.js";

const STORAGE_KEY = "aird-browse-type-filter";

let state = emptyTypeFilter();

function writeUrl() {
  const url = new URL(globalThis.location.href);
  if (state.categories.length) url.searchParams.set("type", state.categories.join(","));
  else url.searchParams.delete("type");
  if (state.extensions.length) url.searchParams.set("ext", state.extensions.join(","));
  else url.searchParams.delete("ext");
  globalThis.history.replaceState(null, "", url);
}

function persist() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* ignore quota / private mode */
  }
  writeUrl();
}

function readStored() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
    state = normalizeTypeFilter(saved);
  } catch {
    state = emptyTypeFilter();
  }
}

function readInitial() {
  const url = new URL(globalThis.location.href);
  const fromUrl = typeFilterFromSearch(
    url.searchParams.get("type"),
    url.searchParams.get("ext"),
  );
  if (fromUrl) {
    state = fromUrl;
    return;
  }
  readStored();
}

function updateCount(shown, files) {
  const count = document.getElementById("browseTypeFilterCount");
  if (!count) return;
  count.textContent = isFiltering(state) ? `${shown} of ${files} files shown` : "";
}

function rowName(row) {
  return (row.querySelector(".file-link")?.textContent || "").trim();
}

export function applyBrowseTypeFilter() {
  const rows = document.querySelectorAll("#fileTable tr.file-row");
  let files = 0;
  let shown = 0;
  rows.forEach((row) => {
    const isDir = row.dataset.isDir === "1";
    if (!isDir) files += 1;
    const show = rowMatchesFilter(state, { isDir, name: rowName(row) });
    row.hidden = !show;
    if (!isDir && show) shown += 1;
  });
  updateCount(shown, files);
}

function syncPressed(root) {
  root.querySelectorAll("[data-type-filter]").forEach((btn) => {
    const cat = btn.getAttribute("data-type-filter");
    btn.setAttribute("aria-pressed", state.categories.includes(cat) ? "true" : "false");
  });
}

function onCategoryClick(root, btn) {
  state = toggleCategory(state, btn.getAttribute("data-type-filter"));
  syncPressed(root);
  persist();
  applyBrowseTypeFilter();
}

function onExtChange(input) {
  state = { categories: state.categories, extensions: parseExtensions(input.value) };
  input.value = state.extensions.join(", ");
  persist();
  applyBrowseTypeFilter();
}

function onClear(root, input) {
  state = emptyTypeFilter();
  syncPressed(root);
  if (input) input.value = "";
  persist();
  applyBrowseTypeFilter();
}

export function initBrowseTypeFilter() {
  const root = document.getElementById("browseTypeFilter");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  readInitial();
  syncPressed(root);
  root.querySelectorAll("[data-type-filter]").forEach((btn) => {
    btn.addEventListener("click", () => onCategoryClick(root, btn));
  });
  const input = document.getElementById("browseTypeFilterExt");
  if (input) {
    input.value = state.extensions.join(", ");
    input.addEventListener("change", () => onExtChange(input));
  }
  document.getElementById("browseTypeFilterClear")?.addEventListener("click", () => {
    onClear(root, input);
  });
  applyBrowseTypeFilter();
}
