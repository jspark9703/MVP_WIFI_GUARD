import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Header, ResponseBadge } from "./index";
import { useUpdateFallResponse } from "@/api/mutations";
import { useConfig, useFalls } from "@/api/queries";
import {
  FALL_RESPONSES,
  FALL_RESPONSE_LABEL,
  type FallEvent,
  type FallResponse,
} from "@/lib/domain";
import { fmtDateTime } from "@/lib/format";

export const Route = createFileRoute("/history")({
  head: () => ({ meta: [{ title: "탐지 이력 · CSI-Guard" }] }),
  component: HistoryPage,
});

function HistoryPage() {
  const [filter, setFilter] = useState<"ALL" | FallResponse>("ALL");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // 스코핑은 서버가 한다 (FACILITY facility_id / HOME owner_user_id).
  const { data: page, isLoading } = useFalls({
    response: filter === "ALL" ? null : filter,
    limit: 200,
  });
  const { data: config } = useConfig();
  const respond = useUpdateFallResponse();
  const falls = page?.items ?? [];
  const threshold = config?.presenceMvThreshold ?? 2.0;

  const exportCsv = () => {
    const rows = [["timestamp", "resident", "room", "confidence", "duration_s", "response"]];
    falls.forEach((f) =>
      rows.push([
        f.occurredAt,
        f.residentName,
        f.room,
        String(f.confidence),
        String(f.durationS),
        f.response,
      ]),
    );
    const csv = rows.map((r) => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `fall-history-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const changeResponse = async (id: string, response: FallResponse) => {
    try {
      await respond.mutateAsync({ id, response });
      toast.success(`응답 상태: ${FALL_RESPONSE_LABEL[response]}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "응답 상태를 바꾸지 못했습니다");
    }
  };

  return (
    <div>
      <Header title="낙상 탐지 이력" />
      <div className="p-6 space-y-4 max-w-6xl">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight mb-1">Detection History</h1>
            <p className="text-sm text-muted">
              지금까지 총 {page?.total ?? 0}건이 감지되었습니다 · 각 항목을 클릭하면 당시 움직임
              그래프와 낙상 발생 구간을 확인할 수 있습니다.
            </p>
          </div>
          <div className="flex gap-2">
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as "ALL" | FallResponse)}
              className="bg-surface border border-border rounded px-3 py-2 text-xs font-mono"
            >
              <option value="ALL">전체</option>
              {FALL_RESPONSES.map((r) => (
                <option key={r} value={r}>
                  {FALL_RESPONSE_LABEL[r]}
                </option>
              ))}
            </select>
            <button
              onClick={exportCsv}
              className="px-4 py-2 border border-border rounded text-xs font-mono uppercase text-muted hover:text-foreground"
            >
              Export CSV
            </button>
          </div>
        </div>

        <div className="bg-surface border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm font-mono">
            <thead>
              <tr className="text-[10px] text-muted border-b border-border bg-background/30">
                <th className="p-3 w-6"></th>
                <th className="p-3 font-medium uppercase">Timestamp</th>
                <th className="p-3 font-medium uppercase">Resident</th>
                <th className="p-3 font-medium uppercase">Room</th>
                <th className="p-3 font-medium uppercase">Confidence</th>
                <th className="p-3 font-medium uppercase">Duration</th>
                <th className="p-3 font-medium uppercase">Response</th>
                <th className="p-3 font-medium uppercase text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {falls.map((f) => {
                const open = expandedId === f.id;
                return (
                  <FallRow
                    key={f.id}
                    fall={f}
                    open={open}
                    threshold={threshold}
                    onToggle={() => setExpandedId(open ? null : f.id)}
                    onRespond={(r) => changeResponse(f.id, r)}
                  />
                );
              })}
              {!isLoading && falls.length === 0 && (
                <tr>
                  <td colSpan={8} className="p-8 text-center text-muted text-xs">
                    이력이 없습니다.
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

function FallRow({
  fall,
  open,
  threshold,
  onToggle,
  onRespond,
}: {
  fall: FallEvent;
  open: boolean;
  threshold: number;
  onToggle: () => void;
  onRespond: (r: FallResponse) => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer hover:bg-black/5 ${open ? "bg-primary/5" : ""}`}
      >
        <td className="p-3 text-muted text-xs">{open ? "▾" : "▸"}</td>
        <td className="p-3 text-muted">{fmtDateTime(fall.occurredAt)}</td>
        <td className="p-3">{fall.residentName}</td>
        <td className="p-3 whitespace-pre-line">{fall.room}</td>
        <td className={`p-3 ${fall.confidence > 0.9 ? "text-primary" : ""}`}>
          {(fall.confidence * 100).toFixed(1)}%
        </td>
        <td className="p-3">{fall.durationS.toFixed(2)}s</td>
        <td className="p-3">
          <ResponseBadge response={fall.response} />
        </td>
        <td className="p-3 text-right" onClick={(e) => e.stopPropagation()}>
          <select
            value={fall.response}
            onChange={(e) => onRespond(e.target.value as FallResponse)}
            className="bg-background border border-border rounded px-2 py-1 text-[10px] font-mono"
          >
            {FALL_RESPONSES.map((r) => (
              <option key={r} value={r}>
                {FALL_RESPONSE_LABEL[r]}
              </option>
            ))}
          </select>
        </td>
      </tr>
      {open && (
        <tr className="bg-background/40">
          <td colSpan={8} className="p-0">
            <FallDetailPanel fall={fall} threshold={threshold} />
          </td>
        </tr>
      )}
    </>
  );
}

// Deterministic pseudo-random from fall id — same graph on every open.
function seedRand(seed: string) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * F-W08: 실제 MV 시계열이 저장되기 전까지는 fall.id 시드로 **재구성한 시각화**다 (실측 아님).
 * 실데이터 전환은 TimescaleDB 에 MV 시계열이 쌓인 뒤(백엔드 Phase 2)로 미룬다.
 */
function FallDetailPanel({ fall, threshold }: { fall: FallEvent; threshold: number }) {
  const { series, fallStartIdx, fallEndIdx, peak } = useMemo(() => {
    const rand = seedRand(fall.id);
    const N = 120;
    const dt = 6 / N;
    const fallCenter = 3;
    const dur = Math.max(0.4, Math.min(fall.durationS, 2.0));
    const s0 = fallCenter - dur / 2;
    const s1 = fallCenter + dur / 2;
    const arr: { t: number; mv: number; inFall: boolean }[] = [];
    let peak = 0;
    let fs = -1;
    let fe = -1;
    for (let i = 0; i < N; i++) {
      const t = i * dt;
      const inFall = t >= s0 && t <= s1;
      const base = 0.3 + rand() * 0.35;
      let v = base;
      if (inFall) {
        const u = (t - fallCenter) / (dur / 2);
        const bump = Math.exp(-u * u * 2.2) * (threshold * (1.2 + fall.confidence * 0.8));
        v = base + bump + (rand() - 0.5) * 0.3;
        if (fs < 0) fs = i;
        fe = i;
      } else {
        const dist = Math.min(Math.abs(t - s0), Math.abs(t - s1));
        if (dist < 0.4) v += rand() * 0.6;
      }
      if (v > peak) peak = v;
      arr.push({ t, mv: v, inFall });
    }
    return { series: arr, fallStartIdx: fs, fallEndIdx: fe, peak };
  }, [fall.id, fall.durationS, fall.confidence, threshold]);

  const W = 720,
    H = 180,
    PAD = 24;
  const yMax = Math.max(threshold * 2, peak * 1.15);
  const x = (i: number) => PAD + (i / (series.length - 1)) * (W - PAD * 2);
  const y = (v: number) => H - PAD - (v / yMax) * (H - PAD * 2);
  const path = series
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(p.mv).toFixed(1)}`)
    .join(" ");
  const areaPath = `${path} L${x(series.length - 1)} ${H - PAD} L${x(0)} ${H - PAD} Z`;
  const fallX0 = x(fallStartIdx);
  const fallX1 = x(fallEndIdx);
  const threshY = y(threshold);

  return (
    <div className="p-5 space-y-4 border-t border-primary/20">
      <div className="grid grid-cols-4 gap-3 text-xs font-mono">
        <Stat
          label="DNN Confidence"
          value={`${(fall.confidence * 100).toFixed(1)}%`}
          accent={fall.confidence > 0.9}
        />
        <Stat label="Duration" value={`${fall.durationS.toFixed(2)} s`} />
        <Stat label="Peak MV" value={peak.toFixed(2)} accent />
        <Stat label="Threshold" value={threshold.toFixed(2)} />
      </div>

      <div className="bg-background border border-border rounded p-3">
        <div className="flex justify-between items-center mb-2">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted">
            움직임 그래프 · MV over 6s window ·{" "}
            <span className="text-warning">재구성 시각화 (실측 파형 아님)</span>
          </div>
          <div className="flex gap-3 text-[9px] font-mono">
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-0.5 bg-primary" />
              MV
            </span>
            <span className="flex items-center gap-1">
              <span
                className="inline-block w-3 h-[2px] bg-warning"
                style={{ borderTop: "1px dashed" }}
              />
              Threshold
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-2 bg-primary/25" />
              낙상 구간
            </span>
          </div>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[180px]">
          {[0.25, 0.5, 0.75].map((f) => (
            <line
              key={f}
              x1={PAD}
              x2={W - PAD}
              y1={PAD + f * (H - PAD * 2)}
              y2={PAD + f * (H - PAD * 2)}
              stroke="currentColor"
              className="text-border"
              strokeWidth={1}
            />
          ))}
          {fallStartIdx >= 0 && (
            <>
              <rect
                x={fallX0}
                y={PAD}
                width={Math.max(2, fallX1 - fallX0)}
                height={H - PAD * 2}
                className="fill-primary/15"
              />
              <line
                x1={fallX0}
                x2={fallX0}
                y1={PAD}
                y2={H - PAD}
                className="stroke-primary"
                strokeWidth={1}
                strokeDasharray="2 3"
              />
              <line
                x1={fallX1}
                x2={fallX1}
                y1={PAD}
                y2={H - PAD}
                className="stroke-primary"
                strokeWidth={1}
                strokeDasharray="2 3"
              />
              <text
                x={(fallX0 + fallX1) / 2}
                y={PAD + 12}
                textAnchor="middle"
                className="fill-primary text-[10px] font-mono"
              >
                FALL · {fall.durationS.toFixed(2)}s
              </text>
            </>
          )}
          <line
            x1={PAD}
            x2={W - PAD}
            y1={threshY}
            y2={threshY}
            className="stroke-warning"
            strokeWidth={1}
            strokeDasharray="4 3"
          />
          <text
            x={W - PAD - 4}
            y={threshY - 4}
            textAnchor="end"
            className="fill-warning text-[9px] font-mono"
          >
            threshold {threshold.toFixed(2)}
          </text>
          <path d={areaPath} className="fill-primary/10" />
          <path d={path} className="stroke-primary" fill="none" strokeWidth={1.5} />
          {[0, 1, 2, 3, 4, 5, 6].map((s) => (
            <text
              key={s}
              x={PAD + (s / 6) * (W - PAD * 2)}
              y={H - 6}
              textAnchor="middle"
              className="fill-muted text-[9px] font-mono"
            >
              t{s - 3 >= 0 ? "+" : ""}
              {s - 3}s
            </text>
          ))}
        </svg>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="bg-background border border-border rounded p-3">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted mb-2">
            State Timeline
          </div>
          <StateTimeline
            fallStart={3 - fall.durationS / 2}
            fallEnd={3 + fall.durationS / 2}
            totalSec={6}
          />
        </div>
        <div className="bg-background border border-border rounded p-3 space-y-1 text-[11px] font-mono">
          <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Fall Segment</div>
          <div className="flex justify-between">
            <span className="text-muted">Start (rel.)</span>
            <span>t+{(-fall.durationS / 2).toFixed(2)}s</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">End (rel.)</span>
            <span>t+{(fall.durationS / 2).toFixed(2)}s</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">Peak / Threshold</span>
            <span>{(peak / threshold).toFixed(2)}×</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">Source</span>
            <span>{fall.source}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">Responded</span>
            <span>{fall.respondedAt ? fmtDateTime(fall.respondedAt) : "—"}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">Response</span>
            <ResponseBadge response={fall.response} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="bg-background border border-border rounded p-2.5">
      <div className="text-[9px] uppercase text-muted">{label}</div>
      <div className={`text-lg mt-0.5 ${accent ? "text-primary" : ""}`}>{value}</div>
    </div>
  );
}

function StateTimeline({
  fallStart,
  fallEnd,
  totalSec,
}: {
  fallStart: number;
  fallEnd: number;
  totalSec: number;
}) {
  const pct = (s: number) => (s / totalSec) * 100;
  const suspectStart = Math.max(0, fallStart - 0.3);
  const cooldownEnd = Math.min(totalSec, fallEnd + 1.5);
  const seg = (bg: string, left: number, right: number, label: string) => (
    <div
      className={`absolute top-0 bottom-0 flex items-center justify-center text-[9px] font-mono uppercase ${bg}`}
      style={{ left: `${pct(left)}%`, width: `${pct(right - left)}%` }}
    >
      {label}
    </div>
  );
  return (
    <div className="relative h-8 rounded overflow-hidden border border-border bg-surface">
      {seg("bg-muted/20 text-muted", 0, suspectStart, "IDLE")}
      {seg("bg-warning/25 text-warning", suspectStart, fallStart, "SUSPECT")}
      {seg("bg-primary/30 text-primary", fallStart, fallEnd, "FALL")}
      {seg("bg-blue-500/20 text-blue-600 dark:text-blue-400", fallEnd, cooldownEnd, "COOLDOWN")}
      {seg("bg-muted/20 text-muted", cooldownEnd, totalSec, "IDLE")}
    </div>
  );
}
