/** Aero mark: an outlined paper plane in the current text colour (/favicon.svg uses a slightly thicker line for small tab sizes). */
export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      stroke="currentColor"
      strokeWidth="4"
      strokeLinejoin="round"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M56 8 L6 29 L25 37 L33 57 Z" />
      <path d="M56 8 L25 37" />
    </svg>
  );
}

export function Brand() {
  return (
    <a href="/" className="flex items-center gap-2 text-neutral-900" aria-label="Aero home">
      <Logo />
      <span className="text-lg font-bold tracking-tight">Aero</span>
    </a>
  );
}
