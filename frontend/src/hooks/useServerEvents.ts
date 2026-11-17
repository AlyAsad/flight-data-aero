import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { Direction, QueueProgress } from "../lib/types";

const REFETCH_THROTTLE_MS = 400;

/**
 * Subscribes to /api/events. Route-day completions for the current view
 * trigger a throttled refetch of week/flight data; queue progress and
 * provider switches are exposed for the status line.
 */
export function useServerEvents(direction: Direction, foreign: string) {
  const qc = useQueryClient();
  const [progress, setProgress] = useState<QueueProgress | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const es = new EventSource("/api/events");
    let timer: ReturnType<typeof setTimeout> | undefined;

    const scheduleRefetch = () => {
      if (timer) return;
      timer = setTimeout(() => {
        timer = undefined;
        void qc.invalidateQueries({ queryKey: ["week"] });
        void qc.invalidateQueries({ queryKey: ["flights"] });
      }, REFETCH_THROTTLE_MS);
    };

    const onRouteDay = (e: MessageEvent<string>) => {
      const d = JSON.parse(e.data) as { direction: string; foreign: string };
      if (d.direction === direction && d.foreign === foreign) scheduleRefetch();
    };
    const onQueue = (e: MessageEvent<string>) => {
      const p = JSON.parse(e.data) as QueueProgress;
      setProgress(p);
      if (p.pending === 0 && p.running === 0) void qc.invalidateQueries({ queryKey: ["status"] });
    };
    const onSwitch = () => void qc.invalidateQueries({ queryKey: ["status"] });

    es.addEventListener("route_day_done", onRouteDay);
    es.addEventListener("route_day_error", onRouteDay);
    es.addEventListener("queue_size", onQueue);
    es.addEventListener("provider_switched", onSwitch);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
      if (timer) clearTimeout(timer);
    };
  }, [qc, direction, foreign]);

  return { progress, connected };
}
