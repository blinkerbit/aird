(function () {
  "use strict";

  class P2PStateMachine {
    constructor(initialState) {
      this.state = initialState ? { ...initialState } : {};
      this.listeners = new Set();
    }

    snapshot() {
      return { ...this.state };
    }

    set(patch) {
      this.state = { ...this.state, ...patch };
      this._emit();
    }

    transition(event, payload) {
      switch (event) {
        case "MODE_SELECTED":
          this.set({ currentMode: payload.mode });
          break;
        case "ROOM_CREATED":
          this.set({ currentRoomId: payload.roomId, otherPeerInRoom: false });
          break;
        case "ROOM_JOINED":
          this.set({
            currentRoomId: payload.roomId,
            otherPeerInRoom: payload.peerCount >= 2,
          });
          break;
        case "PEER_JOINED":
          this.set({ otherPeerInRoom: true });
          break;
        case "PEER_LEFT":
          this.set({ otherPeerInRoom: false });
          break;
        case "TRANSFER_STARTED":
          this.set({ isTransferring: true });
          break;
        case "TRANSFER_COMPLETED":
          this.set({ isTransferring: false });
          break;
        default:
          break;
      }
    }

    subscribe(listener) {
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    _emit() {
      for (const listener of this.listeners) {
        listener(this.snapshot());
      }
    }
  }

  globalThis.P2PPatterns = globalThis.P2PPatterns || {};
  globalThis.P2PPatterns.P2PStateMachine = P2PStateMachine;
})();
