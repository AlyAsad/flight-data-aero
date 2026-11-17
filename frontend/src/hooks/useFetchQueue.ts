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

  const refetch = useCallback(() => {
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = undefined;
      void qc.invalidateQueries({ queryKey: ["week"] });
      void qc.invalidateQueries({ queryKey: ["flights"] });
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
