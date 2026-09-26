import test from 'node:test';
import assert from 'node:assert/strict';
import { liveDateKey, chartBusinessDay, formatKstInstant } from './chart-time.mjs';

test('live daily bars use the exchange-local date while retaining a business-day chart value', () => {
  const key = liveDateKey(1790343000, '1D');
  assert.equal(key, '2026-09-25');
  assert.deepEqual(chartBusinessDay(key), { year: 2026, month: 9, day: 25 });
});

test('weekly and monthly live bars match historical candle buckets', () => {
  assert.equal(liveDateKey(1790343000, '1W'), '2026-09-21');
  assert.equal(liveDateKey(1790343000, '1M'), '2026-09-01');
});

test('exchange date conversion respects standard time near UTC midnight', () => {
  const utc = Date.UTC(2026, 10, 2, 0, 30) / 1000;
  assert.equal(liveDateKey(utc, '1D'), '2026-11-01');
});

test('intraday chart labels render the timestamp in Seoul time', () => {
  assert.match(formatKstInstant(1790343000), /22:30:00/);
});
