/** Aero mark: a white paper plane on black. Same artwork as /favicon.svg. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#0a0a0a" />
      <path d="M51 13 L11 30 L26 35.5 L32 52 Z" fill="#fff" />
      <path d="M51 13 L26 35.5" stroke="#0a0a0a" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

export function Brand() {
  return (
    <a href="/" className="flex items-center gap-2" aria-label="Aero home">
      <Logo />
      <span className="text-lg font-bold tracking-tight text-neutral-900">Aero</span>
    </a>
  );
}
