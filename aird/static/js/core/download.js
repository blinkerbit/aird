/**
 * Save a Blob as a file download.
 *
 * Uses the File System Access API when the browser offers it, otherwise a
 * temporary object URL. Transfer HTTP, transfer WebSocket, and browse zip
 * downloads all used to carry their own copy of this.
 */

const REVOKE_MS = 60000;

export async function saveBlob(blob, filename) {
  const name = filename || 'download';
  if (typeof globalThis.showSaveFilePicker === 'function' && blob.stream) {
    try {
      const handle = await globalThis.showSaveFilePicker({ suggestedName: name });
      const writable = await handle.createWritable();
      const reader = blob.stream().getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        await writable.write(value);
      }
      await writable.close();
      return;
    } catch (err) {
      if (err && err.name === 'AbortError') throw err;
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_MS);
}
