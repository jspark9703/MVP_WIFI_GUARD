import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Header } from "./index";
import { useEventLogs } from "@/api/queries";
import type { LogLevel } from "@/lib/domain";
import { fmtDateTime } from "@/lib/format";

export const Route = createFileRoute("/event-log")({
  head: () => ({ meta: [{ title: "이벤트 로그 · CSI-Guard" }] }),
  component: EventLogPage,
});

/** F-027: 당일 로그만. 스코핑(FACILITY/HOME)은 서버가 한다. */
function EventLogPage() {
  const [filter, setFilter] = useState<"ALL" | LogLevel>("ALL");
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 300);
  const { from, to } = useMemo(todayRange, []);
  const { data, isLoading } = useEventLogs({
    level: filter === "ALL" ? null : filter,
    q: q || null,
    from,
    to,
    limit: 500,
  });
  const logs = data?.items ?? [];

  return (
    <div>
      <Header title="이벤트 로그" />
      <div className="p-6 space-y-4 max-w-6xl">
        <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight mb-1">Event Log</h1>
            <p className="text-sm text-muted">오늘 조건에 맞는 이벤트 {data?.total ?? 0}건.</p>
          </div>
          <div className="flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="메시지 검색"
              className="bg-surface border border-border rounded px-3 py-2 text-xs font-mono placeholder:text-muted"
            />
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as "ALL" | LogLevel)}
              className="bg-surface border border-border rounded px-3 py-2 text-xs font-mono"
            >
              <option value="ALL">전체 레벨</option>
              <option value="FALL">FALL</option>
              <option value="ERROR">ERROR</option>
              <option value="WARN">WARN</option>
              <option value="INFO">INFO</option>
            </select>
          </div>
        </div>

        <div className="bg-surface border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm font-mono">
            <thead>
              <tr className="text-[10px] text-muted border-b border-border bg-background/30">
                <th className="p-3 font-medium uppercase w-44">Timestamp</th>
                <th className="p-3 font-medium uppercase w-20">Level</th>
                <th className="p-3 font-medium uppercase">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {logs.map((l) => (
                <tr key={l.id}>
                  <td className="p-3 text-muted">{fmtDateTime(l.ts)}</td>
                  <td className="p-3">
                    <span className={`text-[10px] font-bold uppercase ${levelColor(l.level)}`}>
                      {l.level}
                    </span>
                  </td>
                  <td className="p-3 text-foreground/80">{l.msg}</td>
                </tr>
              ))}
              {!isLoading && logs.length === 0 && (
                <tr>
                  <td colSpan={3} className="p-8 text-center text-muted text-xs">
                    오늘 조건에 맞는 이벤트가 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function todayRange() {
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const to = new Date(from.getTime() + 24 * 60 * 60 * 1000 - 1);
  return { from: from.toISOString(), to: to.toISOString() };
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

function levelColor(level: string) {
  switch (level) {
    case "FALL":
    case "ERROR":
      return "text-primary";
    case "WARN":
      return "text-warning";
    case "INFO":
      return "text-success";
    default:
      return "text-muted";
  }
}
