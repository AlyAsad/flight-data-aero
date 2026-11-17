// Calendar-date helpers on "YYYY-MM-DD" strings, using UTC to avoid DST/timezone drift.

const MS_DAY = 86_400_000;

export function parseDate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(Date.UTC(y!, m! - 1, d!));
}

export function formatISODate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function addDays(s: string, n: number): string {
  return formatISODate(new Date(parseDate(s).getTime() + n * MS_DAY));
}

export function todayISO(now: Date = new Date()): string {
  return formatISODate(new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())));
}

export function isValidISODate(s: string | null | undefined): s is string {
  if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  return formatISODate(parseDate(s)) === s;
}

export function weekDates(center: string): string[] {
  return [-3, -2, -1, 0, 1, 2, 3].map((n) => addDays(center, n));
}

const fmt = (opts: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...opts });

const weekdayFmt = fmt({ weekday: "short" });
const dayMonthFmt = fmt({ day: "numeric", month: "short" });
const dayMonthYearFmt = fmt({ day: "numeric", month: "short", year: "numeric" });

export const weekdayLabel = (s: string) => weekdayFmt.format(parseDate(s));
export const dayMonthLabel = (s: string) => dayMonthFmt.format(parseDate(s));

/** "29 Dec – 4 Jan 2027" */
export function rangeLabel(dates: string[]): string {
  if (dates.length === 0) return "";
  const first = dates[0]!;
  const last = dates[dates.length - 1]!;
  return `${dayMonthLabel(first)} – ${dayMonthYearFmt.format(parseDate(last))}`;
}
