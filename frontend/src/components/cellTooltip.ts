import { dayMonthLabel, weekdayLabel } from "../lib/dates";
import { expiresIn, fetchedAgo, formatPrice, PARTIAL_TOOLTIP } from "../lib/format";
import type { RouteDaySummary } from "../lib/types";

/** Tooltip text for a heatmap cell or day tab. */
export function cellTooltip(d: RouteDaySummary, groupName?: string): string {
  const lines = [
    `${groupName ? `${groupName} · ` : ""}${weekdayLabel(d.date)} ${dayMonthLabel(d.date)}`,
  ];
  switch (d.status) {
    case "past":
      lines.push("Date is in the past");
      return lines.join("\n");
    case "not_on_sale":
      lines.push("Not yet on sale");
      return lines.join("\n");
    case "pending":
      lines.push("Loading…");
      return lines.join("\n");
    case "empty":
      lines.push(d.is_partial ? "No flights in overview search" : "No flights found");
      break;
    case "error":
      lines.push("Fetch failed");
      break;
    default:
      lines.push(
        `${formatPrice(d.cheapest_price, d.is_partial)} · ${d.option_count} option${d.option_count === 1 ? "" : "s"}`,
      );
  }
  if (d.fetched_at) lines.push(`${fetchedAgo(d.fetched_at)} · ${expiresIn(d.expires_at)}`);
  if (d.is_stale) lines.push("Stale: showing expired data");
  if (d.is_partial) lines.push(PARTIAL_TOOLTIP);
  if (d.loading) lines.push("Updating…");
  if (d.error) lines.push(`Last error: ${d.error}`);
  return lines.join("\n");
}
