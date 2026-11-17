import { useEffect, useRef, useState } from "react";
import { useAirportSearch } from "../hooks/queries";
import type { Airport } from "../lib/types";

interface Props {
  value: string;
  onChange: (iata: string) => void;
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function airportLabel(a: Airport | undefined, iata: string): string {
  return a ? `${a.city || a.name} (${a.iata})` : iata;
}

/** Foreign-airport autocomplete. */
export function AirportPicker({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const query = useDebounced(text, 200);
  const { data: results = [] } = useAirportSearch(query);
  const { data: current } = useAirportSearch(value);
  const currentAirport = current?.find((a) => a.iata === value);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const choose = (a: Airport) => {
    onChange(a.iata);
    setOpen(false);
    setText("");
  };

  return (
    <div ref={boxRef} className="relative">
      <input
        aria-label="Foreign airport"
        className="w-56 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm focus:border-sky-500 focus:outline-none"
        placeholder={airportLabel(currentAirport, value)}
        value={open ? text : airportLabel(currentAirport, value)}
        onFocus={() => {
          setOpen(true);
          setText("");
          setActive(0);
        }}
        onChange={(e) => {
          setText(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((i) => Math.min(i + 1, results.length - 1));
          else if (e.key === "ArrowUp") setActive((i) => Math.max(i - 1, 0));
          else if (e.key === "Enter" && results[active]) choose(results[active]);
          else if (e.key === "Escape") {
            setOpen(false);
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
      {open && results.length > 0 && (
        <ul
          role="listbox"
          className="absolute z-30 mt-1 max-h-72 w-80 overflow-auto rounded-md border border-slate-200 bg-white text-sm shadow-lg"
        >
          {results.map((a, i) => (
            <li
              key={a.iata}
              role="option"
              aria-selected={i === active}
              className={`cursor-pointer px-3 py-1.5 ${i === active ? "bg-sky-50" : ""}`}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(a);
              }}
            >
              <span className="font-mono font-semibold">{a.iata}</span>{" "}
              <span>{a.name}</span>
              <span className="block text-xs text-slate-500">
                {a.city}, {a.country}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
