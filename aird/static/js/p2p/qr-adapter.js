(function () {
  "use strict";

  class QRAdapter {
    static renderCanvas(text, options) {
      if (!globalThis.QRCode?.toCanvas) {
        throw new Error("QRCode renderer not available");
      }
      return globalThis.QRCode.toCanvas(text, options);
    }
  }

  globalThis.P2PPatterns = globalThis.P2PPatterns || {};
  globalThis.P2PPatterns.QRAdapter = QRAdapter;
})();
