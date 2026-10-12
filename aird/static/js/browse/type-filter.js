"use strict";

const STORAGE_KEY = "aird-browse-type-filter";
const EXT_RE = /^\.[a-z0-9]{1,16}$/;

const CATEGORIES = {
  documents: [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"],
  images: [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp"],
  videos: [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"],
  archives: [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"],
  code: [".js", ".py", ".html", ".css", ".java", ".cpp", ".c"],
  audio: [".mp3", ".wav", ".flac", ".aac", ".ogg"],
};

let state = { categories: [], extensions: [] };

function rowExt(row) {
  const name = (row.querySelector(".file-link")?.textContent || "").trim();
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return "";
  return name.slice(dot).toLowerCase();
}

function parseExtInput(raw) {
  return String(raw || "")
    .split(/[\s,]+/)
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean)
    .map((part) => (part.startsWith(".") ? part : `.${part}`))
    .filter((part) => EXT_RE.test(part));
}

function filtering() {
  return state.categories.length > 0 || state.extensions.length > 0;
}

function rowMatches(row) {
  if (row.dataset.isDir === "1") return true;
  if (!filtering()) return true;
  const ext = rowExt(row);
  if (state.extensions.includes(ext)) return true;
  return state.categories.some((cat) => (CATEGORIES[cat] || []).includes(ext));
}

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
    if (!saved || typeof saved !== "object") return;
    state.categories = (saved.categories || []).filter((cat) => CATEGORIES[cat]);
    state.extensions = (saved.extensions || []).filter((ext) => EXT_RE.test(ext));
  } catch {
    /* ignore bad storage */
  }
}

function readInitial() {
  const url = new URL(globalThis.location.href);
  const type = url.searchParams.get("type");
  const ext = url.searchParams.get("ext");
  if (type || ext) {
    state.categories = (type || "").split(",").filter((cat) => CATEGORIES[cat]);
    state.extensions = parseExtInput(ext || "");
    return;
  }
  readStored();
}

function updateCount(shown, files) {
  const count = document.getElementById("browseTypeFilterCount");
  if (!count) return;
  count.textContent = filtering() ? `${shown} of ${files} files shown` : "";
}

export function applyBrowseTypeFilter() {
  const rows = document.querySelectorAll("#fileTable tr.file-row");
  let files = 0;
  let shown = 0;
  rows.forEach((row) => {
    const isDir = row.dataset.isDir === "1";
    if (!isDir) files += 1;
    const show = rowMatches(row);
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
  const cat = btn.getAttribute("data-type-filter");
  if (!cat || !CATEGORIES[cat]) return;
  if (state.categories.includes(cat)) {
    state.categories = state.categories.filter((item) => item !== cat);
  } else {
    state.categories = state.categories.concat(cat);
  }
  syncPressed(root);
  persist();
  applyBrowseTypeFilter();
}

function onExtChange(input) {
  state.extensions = parseExtInput(input.value);
  input.value = state.extensions.join(", ");
  persist();
  applyBrowseTypeFilter();
}

function onClear(root, input) {
  state = { categories: [], extensions: [] };
  syncPressed(root);
  if (input) input.value = "";
  persist();
  applyBrowseTypeFilter();
}

export function initBrowseTypeFilter() {
  const root = document.getElementById("browseTypeFilter");
  if (!root) return;
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
