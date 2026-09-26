import test from 'node:test';
import assert from 'node:assert/strict';
import { createLiveTradeStore } from './live-trades.mjs';

test('stores each real trade immediately and notifies subscribers at most every 100ms', async () => {
  const store = createLiveTradeStore(100);
  const seen = [];
  store.subscribe('NVDA', trade => seen.push({ ...trade, at: performance.now() }));

  store.publish({ symbol: 'NVDA', price: 120, timestamp: 1000, volume: 1 });
  assert.equal(store.getLatest('NVDA').price, 120);
  store.publish({ symbol: 'NVDA', price: 121, timestamp: 1050, volume: 2 });
  assert.equal(store.getLatest('NVDA').timestamp, 1050);

  await new Promise(resolve => setTimeout(resolve, 125));
  assert.deepEqual(seen.map(({ price }) => price), [121]);
  store.dispose();
});

test('notifies only subscribers for the changed ticker', async () => {
  const store = createLiveTradeStore(100);
  const seen = [];
  store.subscribe('NVDA', () => seen.push('NVDA'));
  store.subscribe('AMD', () => seen.push('AMD'));
  store.publish({ symbol: 'NVDA', price: 120, timestamp: 1000, volume: 1 });
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.deepEqual(seen, ['NVDA']);
  store.dispose();
});

test('caps UI notifications at ten per second while keeping the newest trade', async () => {
  const store = createLiveTradeStore(100);
  const seen = [];
  store.subscribe('NVDA', trade => seen.push({ price: trade.price, at: performance.now() }));
  store.publish({ symbol: 'NVDA', price: 120, timestamp: 1000, volume: 1 });
  await new Promise(resolve => setTimeout(resolve, 15));
  store.publish({ symbol: 'NVDA', price: 121, timestamp: 1015, volume: 2 });
  await new Promise(resolve => setTimeout(resolve, 20));
  store.publish({ symbol: 'NVDA', price: 122, timestamp: 1035, volume: 3 });
  await new Promise(resolve => setTimeout(resolve, 100));

  assert.deepEqual(seen.map(x => x.price), [120, 122]);
  assert.ok(seen[1].at - seen[0].at >= 90);
  store.dispose();
});
