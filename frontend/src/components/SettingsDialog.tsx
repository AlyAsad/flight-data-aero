import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useSettings, useStatus } from "../hooks/queries";
import { api } from "../lib/api";
import type { AppSettings, ProviderMode } from "../lib/types";

interface Props {
  open: boolean;
  onClose: () => void;
}

const MODES: { value: ProviderMode; label: string; help: string }[] = [
  { value: "auto", label: "Auto", help: "fast-flights first; SerpApi for 30 min after 3 failures (if a key is set)." },
  { value: "fast_flights", label: "fast-flights only", help: "Free, unofficial Google Flights scraper." },
  { value: "serpapi", label: "SerpApi only", help: "Uses your SerpApi quota, never beyond the cap." },
];

export function SettingsDialog({ open, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const qc = useQueryClient();
  const { data: settings } = useSettings();
  const { data: status } = useStatus();
  const [draft, setDraft] = useState<AppSettings | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  useEffect(() => {
    if (open && settings) setDraft(settings);
  }, [open, settings]);

  const save = useMutation({
    mutationFn: api.putSettings,
    onSuccess: (s) => {
      qc.setQueryData(["settings"], s);
      void qc.invalidateQueries({ queryKey: ["status"] });
      void qc.invalidateQueries({ queryKey: ["week"] }); // expiry times may have changed
      void qc.invalidateQueries({ queryKey: ["flights"] });
      onClose();
    },
  });

  const set = <K extends keyof AppSettings>(k: K, v: AppSettings[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  const serpapiAvailable = status?.providers.serpapi.available ?? false;

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onCancel={onClose}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg p-0 shadow-xl backdrop:bg-slate-900/40"
    >
      {draft && (
        <form
          method="dialog"
          className="space-y-5 p-5"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(draft);
          }}
        >
          <h2 className="text-lg font-semibold">Settings</h2>

          <label className="block">
            <span className="text-sm font-medium">Cache expiry: {draft.ttl_days} day{draft.ttl_days > 1 ? "s" : ""}</span>
            <input
              type="range"
              min={1}
              max={5}
              step={1}
              value={draft.ttl_days}
              onChange={(e) => set("ttl_days", Number(e.target.value))}
              className="mt-1 w-full accent-sky-600"
            />
            <span className="text-xs text-slate-500">
              Applies to existing entries too. Data is not re-fetched until you press refresh.
            </span>
          </label>

          <fieldset>
            <legend className="text-sm font-medium">Data provider</legend>
            <div className="mt-1 space-y-1">
              {MODES.map((m) => (
                <label key={m.value} className="flex items-start gap-2 text-sm">
                  <input
                    type="radio"
                    name="provider_mode"
                    value={m.value}
                    checked={draft.provider_mode === m.value}
                    disabled={m.value === "serpapi" && !serpapiAvailable}
                    onChange={() => set("provider_mode", m.value)}
                    className="mt-1 accent-sky-600"
                  />
                  <span>
                    {m.label}
                    <span className="block text-xs text-slate-500">{m.help}</span>
                  </span>
                </label>
              ))}
            </div>
            {!serpapiAvailable && (
              <p className="mt-1 text-xs text-amber-700">
                SerpApi is unavailable: set SERPAPI_API_KEY in .env and restart the server.
              </p>
            )}
          </fieldset>

          <div className="grid grid-cols-2 gap-4">
            <label className="block text-sm">
              <span className="font-medium">SerpApi monthly cap</span>
              <input
                type="number"
                min={0}
                max={250}
                value={draft.serpapi_monthly_cap}
                onChange={(e) => set("serpapi_monthly_cap", Number(e.target.value))}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              />
              {status && (
                <span className="text-xs text-slate-500">
                  SerpApi: {status.serpapi_usage.used} / 250 used this month
                </span>
              )}
            </label>
            <label className="block text-sm">
              <span className="font-medium">Concurrency</span>
              <input
                type="number"
                min={1}
                max={8}
                value={draft.concurrency}
                onChange={(e) => set("concurrency", Number(e.target.value))}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              />
              <span className="text-xs text-slate-500">Parallel requests</span>
            </label>
            <label className="col-span-2 block text-sm">
              <span className="font-medium">Delay between requests (seconds)</span>
              <span className="mt-1 flex items-center gap-2">
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  aria-label="Minimum delay"
                  value={draft.delay_seconds[0]}
                  onChange={(e) => set("delay_seconds", [Number(e.target.value), draft.delay_seconds[1]])}
                  className="w-24 rounded border border-slate-300 px-2 py-1"
                />
                to
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  aria-label="Maximum delay"
                  value={draft.delay_seconds[1]}
                  onChange={(e) => set("delay_seconds", [draft.delay_seconds[0], Number(e.target.value)])}
                  className="w-24 rounded border border-slate-300 px-2 py-1"
                />
              </span>
            </label>
          </div>

          {save.error && <p className="text-sm text-rose-600">Could not save: {save.error.message}</p>}

          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded px-3 py-1.5 text-sm hover:bg-slate-100">
              Cancel
            </button>
            <button
              type="submit"
              disabled={save.isPending}
              className="rounded bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-700 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
