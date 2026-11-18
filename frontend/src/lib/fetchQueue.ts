import type { FetchJob, FetchResult, QueueProgress } from "./types";

export const jobKey = (j: FetchJob) => `${j.direction}:${j.foreign}:${j.group}:${j.date}`;

/** How long to wait before re-offering a job another visitor was already fetching. */
const BUSY_RETRY_MS = 4000;

interface Options {
  runJob: (job: FetchJob) => Promise<FetchResult>;
  /** Called with progress after every change. */
  onProgress: (p: QueueProgress) => void;
  /** Called when a job finishes, so the UI can refetch the week/flights. */
  onJobFinished: () => void;
  sleep?: (ms: number) => Promise<void>;
  concurrency?: number;
}

/**
 * Browser-side fetch queue. The server says which group-days a view needs
 * (`/api/week` → `jobs`); this runs them via `/api/fetch`, `concurrency` at a
 * time, waiting the delay the server suggests between requests.
 *
 * - Jobs are de-duplicated by key; a job is not retried within the same view
 *   (errors are cached server-side for 15 minutes), unless it's a refresh.
 * - Changing the view drops jobs that haven't started; running ones finish.
 */
export class FetchQueue {
  private pending: FetchJob[] = [];
  private running = new Set<string>();
  private attempted = new Set<string>();
  private view = "";
  private workers = 0;
  private concurrency: number;
  private done = 0;
  private total = 0;
  private readonly opts: Required<Omit<Options, "concurrency">>;

  constructor(opts: Options) {
    this.opts = { sleep: (ms) => new Promise((r) => setTimeout(r, ms)), ...opts };
    this.concurrency = Math.max(1, opts.concurrency ?? 8);
  }

  setConcurrency(n: number): void {
    this.concurrency = Math.max(1, n);
    this.pump();
  }

  /** Switch to another view: unstarted jobs are dropped and progress restarts. */
  setView(view: string): void {
    if (view === this.view) return;
    this.view = view;
    this.reset();
  }

  /** Forget what was tried (e.g. after the cache was expired or cleared). */
  reset(): void {
    this.pending = [];
    this.attempted.clear();
    this.done = 0;
    this.total = 0;
    this.emit();
  }

  /** Add jobs; `front` puts them ahead of everything queued (explicit refreshes). */
  offer(jobs: FetchJob[], { front = false } = {}): void {
    const queued = new Set(this.pending.map(jobKey));
    const fresh = jobs.filter((j) => {
      const k = jobKey(j);
      if (this.running.has(k) || queued.has(k)) return false;
      if (this.attempted.has(k) && !j.force) return false;
      queued.add(k);
      return true;
    });
    if (!fresh.length) return;
    this.pending = front ? [...fresh, ...this.pending] : [...this.pending, ...fresh];
    this.total += fresh.reduce((n, j) => n + j.route_days, 0);
    this.emit();
    this.pump();
  }

  progress(): QueueProgress {
    return { done: this.done, total: this.total, running: this.running.size };
  }

  private emit(): void {
    this.opts.onProgress(this.progress());
  }

  private pump(): void {
    while (this.workers < this.concurrency && this.pending.length) {
      this.workers++;
      void this.work();
    }
  }

  private async work(): Promise<void> {
    try {
      while (this.pending.length && this.workers <= this.concurrency) {
        const job = this.pending.shift()!;
        const key = jobKey(job);
        const view = this.view;
        this.running.add(key);
        this.attempted.add(key);
        this.emit();
        let result: FetchResult;
        try {
          result = await this.opts.runJob(job);
        } catch (e) {
          result = { status: "error", error: String(e), next_delay_seconds: 1 };
        }
        this.running.delete(key);
        if (view === this.view) this.done += job.route_days;
        if (result.status === "busy") {
          // Someone else is fetching it: let it be offered again once they're done.
          this.attempted.delete(key);
          setTimeout(this.opts.onJobFinished, BUSY_RETRY_MS);
        }
        this.emit();
        this.opts.onJobFinished();
        if (result.status !== "skipped") await this.opts.sleep(result.next_delay_seconds * 1000);
      }
    } finally {
      this.workers--;
      this.emit();
    }
  }
}
