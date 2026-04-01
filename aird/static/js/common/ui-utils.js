(function () {
  "use strict";

  const AirdUtils = {
    formatBytes(bytes) {
      if (bytes === 0) return "0 B";
      const k = 1024;
      const sizes = ["B", "KB", "MB", "GB", "TB"];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return Number.parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
    },

    escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text == null ? "" : String(text);
      return div.innerHTML;
    },

    getFileIcon(mime) {
      if (mime?.startsWith("image/")) return "IMG";
      if (mime?.startsWith("video/")) return "VID";
      if (mime?.startsWith("audio/")) return "AUD";
      if (mime?.includes("pdf")) return "PDF";
      if (mime?.includes("zip")) return "ZIP";
      if (mime?.includes("text")) return "TXT";
      return "FILE";
    },
  };

  globalThis.AirdUtils = AirdUtils;
})();
