"use strict";

import { getMaxFileSize } from '/static/js/browse/util.js';

/** Plain-language message for the upload dialog (not console-only). */
export function friendlyUploadErrorMessage(err) {
  const raw = err?.message ? String(err.message).trim() : "";
  if (!raw || raw === "cancelled") return raw;
  const lower = raw.toLowerCase();
  if (lower.includes("network") || lower.includes("websocket")) {
    return "Upload interrupted. Check your connection and try again.";
  }
  // Prefer ABAC / server JSON reason when present
  try {
    const parsed = JSON.parse(raw);
    if (parsed?.reason) {
      return `Upload denied: ${parsed.reason}`;
    }
    if (parsed?.error && parsed.error !== "Access denied") {
      return parsed.error;
    }
    if (parsed?.error === "Access denied" && parsed?.reason) {
      return `Upload denied: ${parsed.reason}`;
    }
    if (parsed?.error === "Access denied") {
      return "Upload denied by access policy. Ask an admin to permit file.write for your account (Admin → Policies).";
    }
  } catch (_) { /* not JSON */ }
  if (lower.includes("no matching permit") || lower.includes("default deny")) {
    return "Upload denied by access policy. Ask an admin to permit file.write for your account (Admin → Policies).";
  }
  if (lower.includes("403") || lower.includes("access denied")) {
    return "Upload denied by access policy. Ask an admin to permit file.write for your account (Admin → Policies).";
  }
  if (lower.includes("413") || lower.includes("too large")) {
    if (lower.includes("chunk too large")) {
      return raw;
    }
    const m = /^(\d+)\s*MB/i.exec(raw.trim());
    if (m) {
      return `This file exceeds the server limit (${m[1]} MB). Admin → Upload settings → raise Max file size, then refresh this page.`;
    }
    if (lower.includes("entity too large") || lower.includes("request entity")) {
      return (
        'Upload blocked by the reverse proxy (body size limit). ' +
        'Set Admin → Single-request max to 100 MB or lower so large files use parallel HTTP chunks, ' +
        'and raise client_max_body_size in nginx to at least your HTTP chunk size.'
      );
    }
    const limitGB = (getMaxFileSize() / (1024 * 1024 * 1024)).toFixed(2);
    return `This file exceeds the server limit (${limitGB} GB). Admin → Upload settings → raise Max file size, then refresh this page.`;
  }
  if (raw.length > 0 && raw.length < 500) {
    return raw;
  }
  return "Upload could not be completed. Please try again.";
}
