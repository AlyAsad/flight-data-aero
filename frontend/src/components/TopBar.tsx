import { useEffect, useRef, useState } from "react";
import { useAirportSearch } from "../hooks/queries";
import { addDays, rangeLabel, weekDates } from "../lib/dates";
import type { Direction, QueueProgress, Status } from "../lib/types";
import { AirportPicker, airportLabel } from "./AirportPicker";
import { Spinner } from "./Spinner";

interface Props {
  direction: Direction;
  foreign: string;
  center: string;
  progress: QueueProgress | null;
  status: Status | undefined;
  onDirection: (d: Direction) => void;
  onForeign: (iata: string) => void;
  onCenter: (date: string) => void;
  onRefreshWeek: () => void;
  onExpireAll: () => void;
  onClearCache: () => void;
  onOpenSettings: () => void;
}

export function TopBar(props: Props) {
  const { direction, foreign, center } = props;
  const { data: current } = useAirportSearch(foreign);
  const place = airportLabel(
    current?.find((a) => a.iata === foreign),
    foreign,
  );

  return (
    <header className="border-b border-slate-200 bg-white px-4 py-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div
          role="group"
          aria-label="Direction"
          className="inline-flex overflow-hidden rounded-md border border-slate-300 text-sm"
        >
          {(["from_uk", "to_uk"] as const).map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={direction === d}
              onClick={() => props.onDirection(d)}
              className={`px-3 py-1 ${direction === d ? "bg-sky-600 text-white" : "bg-white hover:bg-slate-50"}`}
            >
              {d === "from_uk" ? "From UK ▸" : "◂ To UK"}
            </button>
          ))}
        </div>

        <AirportPicker value={foreign} onChange={props.onForeign} />

        <span className="text-sm text-slate-500">
          {direction === "from_uk" ? `From UK to ${place}` : `From ${place} to UK`}
        </span>

        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="Previous week"
            className="rounded px-2 py-1 hover:bg-slate-100"
            onClick={() => props.onCenter(addDays(center, -7))}
          >
            ◂
          </button>
          <span className="min-w-44 text-center text-sm font-medium">
            {rangeLabel(weekDates(center))}
          </span>
          <button
            type="button"
            aria-label="Next week"
            className="rounded px-2 py-1 hover:bg-slate-100"
            onClick={() => props.onCenter(addDays(center, 7))}
          >
            ▸
          </button>
          <input
            type="date"
            aria-label="Centre date"
            className="ml-1 rounded-md border border-slate-300 px-1.5 py-0.5 text-sm"
            value={center}
            onChange={(e) => e.target.value && props.onCenter(e.target.value)}
          />
        </div>

        <div className="ml-auto flex items-center gap-1">
          <RefreshMenu
            onRefreshWeek={props.onRefreshWeek}
            onExpireAll={props.onExpireAll}
            onClearCache={props.onClearCache}
          />
          <button
            type="button"
            aria-label="Settings"
            title="Settings"
            className="rounded px-2 py-1 text-lg hover:bg-slate-100"
            onClick={props.onOpenSettings}
          >
            ⚙
          </button>
        </div>
      </div>
      <StatusLine progress={props.progress} status={props.status} />
    </header>
  );
}

function RefreshMenu(props: Pick<Props, "onRefreshWeek" | "onExpireAll" | "onClearCache">) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const item = (label: string, action: () => void, confirmText?: string) => (
    <button
      type="button"
      role="menuitem"
      className="block w-full px-3 py-1.5 text-left text-sm hover:bg-slate-50"
      onClick={() => {
        setOpen(false);
        if (!confirmText || window.confirm(confirmText)) action();
      }}
    >
      {label}
    </button>
  );

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label="Refresh menu"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Refresh"
        className="rounded px-2 py-1 text-lg hover:bg-slate-100"
        onClick={() => setOpen((o) => !o)}
      >
        ⟳
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1 w-48 rounded-md border border-slate-200 bg-white py-1 shadow-lg"
        >
          {item("Refresh week", props.onRefreshWeek)}
          {item(
            "Refresh all cache",
            props.onExpireAll,
            "Mark all cached flights as expired and re-fetch the current view?",
          )}
          {item("Clear cache", props.onClearCache, "Delete all cached flight data?")}
        </div>
      )}
    </div>
  );
}

function StatusLine({ progress, status }: { progress: QueueProgress | null; status?: Status }) {
  const loading = progress && (progress.pending > 0 || progress.running > 0);
  const ffError = status?.providers.fast_flights.last_error;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-4 text-xs text-slate-500" aria-live="polite">
      {loading ? (
        <span className="flex items-center gap-1.5">
          <Spinner /> Loading {Math.min(progress.done, progress.total)} of {progress.total}
        </span>
      ) : (
        <span>Up to date</span>
      )}
      {status && (
        <span>
          Provider: <strong>{status.active_provider ?? "none available"}</strong> ({status.provider_mode})
        </span>
      )}
      {status?.switch_reason && <span className="text-amber-700">⚠ {status.switch_reason}</span>}
      {ffError && !status?.switch_reason && status?.active_provider === "fast_flights" && (
        <span className="text-amber-700" title={ffError}>
          ⚠ fast-flights: {ffError.length > 80 ? `${ffError.slice(0, 80)}…` : ffError}
        </span>
      )}
      {status?.providers.serpapi.available && (
        <span>
          SerpApi: {status.serpapi_usage.used} / {status.serpapi_usage.cap}
        </span>
      )}
    </div>
  );
}
