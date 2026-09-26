export function createLiveTradeStore(intervalMs = 100) {
  const latest = new Map();
  const listeners = new Map();
  const pending = new Map();
  let lastFlush = -Infinity;
  let timer = null;

  function flush() {
    timer = null;
    lastFlush = performance.now();
    for (const [symbol, trade] of pending) {
      for (const listener of listeners.get(symbol) || []) listener(trade);
    }
    pending.clear();
  }

  function publish(trade) {
    if (!trade?.symbol || !Number.isFinite(trade.price) || !Number.isFinite(trade.timestamp)) return;
    latest.set(trade.symbol, trade);
    pending.set(trade.symbol, trade);
    if (timer === null) timer = setTimeout(flush, Math.max(0, intervalMs - (performance.now() - lastFlush)));
  }

  function subscribe(symbol, listener) {
    const group = listeners.get(symbol) || new Set();
    group.add(listener);
    listeners.set(symbol, group);
    return () => {
      group.delete(listener);
      if (!group.size) listeners.delete(symbol);
    };
  }

  return {
    publish,
    subscribe,
    getLatest: symbol => latest.get(symbol) || null,
    dispose() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      listeners.clear();
      pending.clear();
    },
  };
}

export const liveTrades = createLiveTradeStore(100);
