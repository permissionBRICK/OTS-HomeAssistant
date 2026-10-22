// Write coalescing for rapid −/+ input (flash wear: one controller write per
// burst). The displayed value updates immediately; one service call is sent
// after `delay` ms of inactivity, and only if it differs from the current value.

const same = (a, b) => (typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-6 : a === b);

export class Coalescer {
  /**
   * @param {object} o
   * @param {(v:any) => Promise<any>} o.send  performs the write
   * @param {() => any} o.current             value currently reported by HA
   * @param {() => void} [o.onChange]         UI refresh hook
   * @param {number} [o.delay=1500]
   * @param {number} [o.confirmTimeout=15000] how long the optimistic value is kept after a successful write
   * @param {{setTimeout:Function, clearTimeout:Function}} [o.timers]
   */
  constructor({ send, current, onChange, delay = 1500, confirmTimeout = 15000, timers } = {}) {
    this._send = send;
    this._current = current;
    this._onChange = onChange || (() => {});
    this.delay = delay;
    this.confirmTimeout = confirmTimeout;
    this._timers = timers || globalThis;
    this._timer = null;
    this._confirmTimer = null;
    this._inflight = null;
    this.state = "idle"; // idle | waiting | sending | sent | error
    this.error = null;
    this._value = undefined; // displayed optimistic value
    this._dirty = false;
  }

  /** Optimistic value to display, or undefined → show HA's value. */
  get value() {
    return this._value;
  }

  /** Display value: optimistic if any, else current. */
  display() {
    return this._value !== undefined ? this._value : this._current();
  }

  push(v) {
    this._value = v;
    this._dirty = true;
    this.error = null;
    this.state = "waiting";
    this._clearConfirm();
    this._arm();
    this._onChange();
  }

  /** Called on every HA state update: drop the optimistic value once HA reflects it. */
  sync() {
    if (this.state === "sent" && this._value !== undefined && same(this._current(), this._value)) {
      this._value = undefined;
      this.state = "idle";
      this._clearConfirm();
      return true;
    }
    return false;
  }

  /** Send now (e.g. on blur) instead of waiting. */
  flush() {
    if (this._timer) {
      this._timers.clearTimeout(this._timer);
      this._timer = null;
      return this._fire();
    }
    return this._inflight || Promise.resolve();
  }

  cancel() {
    if (this._timer) this._timers.clearTimeout(this._timer);
    this._timer = null;
    this._clearConfirm();
    this._value = undefined;
    this._dirty = false;
    this.state = "idle";
    this._onChange();
  }

  dispose() {
    if (this._timer) this._timers.clearTimeout(this._timer);
    this._clearConfirm();
    this._timer = null;
  }

  _arm() {
    if (this._timer) this._timers.clearTimeout(this._timer);
    this._timer = this._timers.setTimeout(() => {
      this._timer = null;
      this._fire();
    }, this.delay);
  }

  _clearConfirm() {
    if (this._confirmTimer) this._timers.clearTimeout(this._confirmTimer);
    this._confirmTimer = null;
  }

  async _fire() {
    if (this._inflight) {
      // A write is still running; re-check once it finished.
      await this._inflight.catch(() => {});
      if (this._timer) return; // newer input re-armed the timer
      if (!this._dirty) return;
    }
    const v = this._value;
    this._dirty = false;
    if (v === undefined) return;
    if (same(v, this._current())) {
      this._value = undefined;
      this.state = "idle";
      this._onChange();
      return;
    }
    this.state = "sending";
    this._onChange();
    const p = Promise.resolve().then(() => this._send(v));
    this._inflight = p;
    try {
      await p;
      if (this._inflight !== p) return;
      if (this._dirty || this._timer) return; // user kept tapping meanwhile
      this.state = "sent";
      if (same(this._current(), v)) {
        this._value = undefined;
        this.state = "idle";
      } else {
        this._confirmTimer = this._timers.setTimeout(() => {
          this._confirmTimer = null;
          if (this.state === "sent") {
            this._value = undefined;
            this.state = "idle";
            this._onChange();
          }
        }, this.confirmTimeout);
      }
    } catch (err) {
      if (this._inflight !== p) return;
      this.error = (err && (err.message || err.code)) || String(err);
      this.state = "error";
      this._value = undefined; // revert to HA's value
      this._dirty = false;
      if (this._timer) {
        this._timers.clearTimeout(this._timer);
        this._timer = null;
      }
    } finally {
      if (this._inflight === p) this._inflight = null;
    }
    this._onChange();
  }
}
