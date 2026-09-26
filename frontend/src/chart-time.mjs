export function liveDateKey(timestampSeconds, timeframe) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(timestampSeconds * 1000));
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  let date = `${values.year}-${values.month}-${values.day}`;
  if (timeframe === '1W') {
    const week = new Date(`${date}T00:00:00Z`);
    week.setUTCDate(week.getUTCDate() - ((week.getUTCDay() + 6) % 7));
    date = week.toISOString().slice(0, 10);
  } else if (timeframe === '1M') {
    date = `${date.slice(0, 7)}-01`;
  }
  return date;
}

export function chartBusinessDay(value) {
  const [year, month, day] = value.split('-').map(Number);
  return { year, month, day };
}

export function businessDayText(value) {
  return typeof value === 'string'
    ? value
    : `${value.year}-${String(value.month).padStart(2, '0')}-${String(value.day).padStart(2, '0')}`;
}

export function formatKstInstant(timestampSeconds, options = {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
}) {
  return new Date(timestampSeconds * 1000).toLocaleString('ko-KR', { ...options, timeZone: 'Asia/Seoul' });
}
