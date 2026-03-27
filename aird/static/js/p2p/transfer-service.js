(function () {
  "use strict";

  class TransferService {
    constructor({ onStringMessage, onBinaryMessage }) {
      this.onStringMessage = onStringMessage;
      this.onBinaryMessage = onBinaryMessage;
    }

    handleIncoming(data) {
      if (typeof data === "string") {
        this.onStringMessage?.(JSON.parse(data));
        return;
      }
      this.onBinaryMessage?.(data);
    }
  }

  globalThis.P2PPatterns = globalThis.P2PPatterns || {};
  globalThis.P2PPatterns.TransferService = TransferService;
})();
