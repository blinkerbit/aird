(function () {
  "use strict";

  class SignalingService {
    constructor(handlerMap) {
      this.handlerMap = handlerMap || {};
    }

    dispatch(message) {
      const type = message?.type;
      const handler = this.handlerMap[type];
      if (handler) {
        handler(message);
      }
    }
  }

  globalThis.P2PPatterns = globalThis.P2PPatterns || {};
  globalThis.P2PPatterns.SignalingService = SignalingService;
})();
