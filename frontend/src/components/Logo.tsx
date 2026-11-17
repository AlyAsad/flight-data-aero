/** Aero mark: a paper plane leaving a dotted contrail. Same artwork as /favicon.svg. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="aero-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0ea5e9" />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill="url(#aero-g)" />
      <path
        d="M9 49 C 19 47, 27 42, 33 34"
        fill="none"
        stroke="#fff"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="1 6"
        opacity=".75"
      />
      <path d="M55 10 L22 26 L34 31 Z" fill="#fff" />
      <path d="M55 10 L34 31 L39 45 Z" fill="#e0e7ff" />
      <path d="M34 31 L33 39 L39 45 Z" fill="#c7d2fe" />
    </svg>
  );
}

export function Brand() {
  return (
    <a href="/" className="flex items-center gap-2" aria-label="Aero home">
      <Logo />
      <span className="bg-gradient-to-r from-sky-600 to-indigo-600 bg-clip-text text-lg font-bold tracking-tight text-transparent">
        Aero
      </span>
    </a>
  );
}
