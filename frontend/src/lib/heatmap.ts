/** Green → red background for a price relative to the week's min/max. */
export function priceColor(
  price: number | null | undefined,
  min: number | null | undefined,
  max: number | null | undefined,
): string | undefined {
  if (price == null || min == null || max == null) return undefined;
  const t = max > min ? Math.min(1, Math.max(0, (price - min) / (max - min))) : 0;
  const hue = Math.round(120 * (1 - t)); // 120 = green, 0 = red
  return `hsl(${hue} 70% 82%)`;
}
