import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { FetchQueue } from "../lib/fetchQueue";
import type { FetchJob, QueueProgress } from "../lib/types";

const REFETCH_THROTTLE_MS = 400;

/** React wrapper around FetchQueue: refreshes week/flight data as jobs finish. */
export function useFetchQueue(view: string, concurrency: number) {
  const qc = useQueryClient();
  const [progress, setProgress] = useState<QueueProgress>({ done: 0, total: 0, running: 0 });
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const queueRef = useRef<FetchQueue | null>(null);

  const inFlight = useRef(false);
  const dirty = useRef(false);

  // Refetch week/flights after jobs finish. A refetch already running is never
  // cancelled (that kept the grid from updating while jobs finished back to
  // back); if more jobs finish meanwhile, one more refetch follows it.
  const refetch = useCallback(() => {
    if (inFlight.current) {
      dirty.current = true;
      return;
    }
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = undefined;
      inFlight.current = true;
      dirty.current = false;
      const opts = { cancelRefetch: false };
      void Promise.allSettled([
        qc.invalidateQueries({ queryKey: ["week"] }, opts),
        qc.invalidateQueries({ queryKey: ["flights"] }, opts),
      ]).then(() => {
        inFlight.current = false;
        if (dirty.current) refetch();
      });
    }, REFETCH_THROTTLE_MS);
  }, [qc]);

  if (!queueRef.current) {
    queueRef.current = new FetchQueue({
      runJob: api.fetchJob,
      onProgress: setProgress,
      onJobFinished: refetch,
      concurrency,
    });
  }
  const queue = queueRef.current;

  useEffect(() => queue.setView(view), [queue, view]);
  useEffect(() => queue.setConcurrency(concurrency), [queue, concurrency]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const offer = useCallback(
    (jobs: FetchJob[], opts?: { front?: boolean }) => queue.offer(jobs, opts),
    [queue],
  );
  const reset = useCallback(() => queue.reset(), [queue]);
  return { progress, offer, reset };
}
