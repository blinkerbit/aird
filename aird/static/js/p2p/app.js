(function () {
  "use strict";

  function createP2PPatternKit(initialState) {
    const StateMachine = globalThis.P2PPatterns?.P2PStateMachine;
    const Mediator = globalThis.P2PPatterns?.P2PMediator;
    if (!StateMachine || !Mediator) {
      return null;
    }
    const stateMachine = new StateMachine(initialState);
    const mediator = new Mediator();
    return { stateMachine, mediator };
  }

  globalThis.P2PPatterns = globalThis.P2PPatterns || {};
  globalThis.P2PPatterns.createP2PPatternKit = createP2PPatternKit;
})();
