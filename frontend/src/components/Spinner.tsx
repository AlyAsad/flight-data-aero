export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-sky-600 ${className}`}
    />
  );
}
