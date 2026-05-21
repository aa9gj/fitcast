import type { Verdict } from "@/lib/types";

const VERDICT_META: Record<Verdict, { label: string; cls: string }> = {
  qualified: { label: "qualified", cls: "bg-verdict-qualified/15 text-verdict-qualified ring-verdict-qualified/30" },
  stretch: { label: "stretch", cls: "bg-verdict-stretch/15 text-verdict-stretch ring-verdict-stretch/30" },
  not_qualified: { label: "not qualified", cls: "bg-verdict-no/15 text-verdict-no ring-verdict-no/30" },
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const m = VERDICT_META[verdict];
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 font-mono text-xs font-medium uppercase tracking-wide ring-1 ${m.cls}`}
    >
      {m.label}
    </span>
  );
}

export function ScoreRing({
  value,
  label,
  size = 116,
}: {
  value: number;
  label: string;
  size?: number;
}) {
  const r = (size - 12) / 2;
  const circ = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value));
  const color =
    pct >= 80 ? "#2ea043" : pct >= 50 ? "#bb8009" : "#cf222e";
  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#21262d"
          strokeWidth={9}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={9}
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={circ - (pct / 100) * circ}
        />
      </svg>
      <div className="-mt-[72px] text-center">
        <div className="font-mono text-3xl font-semibold tabular-nums">{value}</div>
        <div className="text-[11px] uppercase tracking-wide text-zinc-500">/ 100</div>
      </div>
      <div className="mt-7 text-sm font-medium text-zinc-300">{label}</div>
    </div>
  );
}

export function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-lg border border-ink-line bg-ink-soft px-4 py-3">
      <div className="font-mono text-xl font-semibold text-zinc-100">{v}</div>
      <div className="mt-0.5 text-xs text-zinc-500">{k}</div>
    </div>
  );
}
