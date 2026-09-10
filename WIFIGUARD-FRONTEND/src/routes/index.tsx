import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useSimulateFall } from "@/api/mutations";
import {
  useConfig,
  useDetectionStatus,
  useDevices,
  useFalls,
  useMvHistory,
  useResidents,
} from "@/api/queries";
import { useLiveDevice } from "@/api/realtime/useLiveStream";
import { EventLogPanel } from "@/components/EventLogPanel";
import { alarmStore, useAlarm } from "@/lib/alarm-store";
import { FALL_RESPONSE_LABEL, type FallResponse, type StateMachine } from "@/lib/domain";
import {
  DETECTION_INACTIVE_LABEL,
  fmtDateTime,
  presenceColor,
  presenceLabel,
  stateColor,
  stateLabel,
  unifiedStatusColor,
  unifiedStatusLabel,
} from "@/lib/format";
import { useTick } from "@/lib/mock-store";

export const Route = createFileRoute("/")({
  head: () => ({ meta: [{ title: "실시간 관제 · CSI-Guard" }] }),
  component: MonitoringPage,
});

const CHART_STYLE = {
  tick: { fill: "#71717a", fontSize: 10, fontFamily: "JetBrains Mono" },
  axis: { stroke: "#e4e4e7" },
  tooltip: {
    background: "#ffffff",
    border: "1px solid #e4e4e7",
    fontFamily: "JetBrains Mono",
    fontSize: 11,
  },
};

/**
 * 실시간 관제. 값은 `/ws/live`(`useLiveDevice`)에서 오고, 없으면 "감지 미동작"으로 표시된다
 * — 낙상 없음이 아니라 감지가 동작하지 않는 상태다 (F-W11). `VITE_USE_MOCK=1` 이면
 * 시뮬레이션이 채운다.
 */
function MonitoringPage() {
  if (import.meta.env.VITE_USE_MOCK === "1") useTick(); // eslint-disable-line react-hooks/rules-of-hooks -- 빌드 상수라 호출 순서가 바뀌지 않는다
  const { user, features } = useAuth();
  const isFacility = user?.service === "FACILITY";
  const detection = useDetectionStatus();
  const { data: residents = [] } = useResidents();
  const { data: devices = [] } = useDevices();
  const { data: config } = useConfig();
  const { data: fallPage } = useFalls({ limit: 5 });
  const simulate = useSimulateFall();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeDeviceId, setActiveDeviceId] = useState<string | null>(null);

  const active = residents.find((r) => r.id === activeId) ?? residents[0];
  const activeDevice = devices.find((d) => d.id === active?.deviceId);
  const falls = fallPage?.items ?? [];
  const threshold = active?.thresholdOverride ?? config?.presenceMvThreshold ?? 2.0;
  const wanderRatioThreshold = config?.wanderRatioThreshold ?? 1.8;

  // HOME 다중 장치 선택 (대표 장치 = 첫 거주자의 주 장치)
  const homeDevices = !isFacility ? devices : [];
  const primaryDeviceId = !isFacility ? (residents[0]?.deviceId ?? null) : null;
  const selectedDevice =
    homeDevices.find((d) => d.id === activeDeviceId) ??
    homeDevices.find((d) => d.id === primaryDeviceId) ??
    homeDevices[0];
  const selectedResident = residents.find(
    (r) => r.deviceId === selectedDevice?.id || r.deviceIds.includes(selectedDevice?.id ?? ""),
  );
  const showDevicePicker = !isFacility && homeDevices.length > 1;

  // 실시간 — 화면에 그리는 기기 하나를 구독한다. FACILITY 는 선택된 거주자의 주 장치,
  // HOME 은 위에서 고른 장치. 소켓 자체는 LiveBridge 가 앱 전역에서 열어 둔다.
  const watchedDeviceId = (isFacility ? active?.deviceId : selectedDevice?.id) ?? null;
  const liveDevice = useLiveDevice(watchedDeviceId);
  const livePresence = liveDevice?.presence ?? null;
  const liveFall = liveDevice?.fall ?? null;
  const liveMode = livePresence != null;
  const mvHistory = useMvHistory(watchedDeviceId);
  const chartData = mvHistory.map((p, i) => ({ i, mv: Number(p.mv.toFixed(3)) }));
  const liveMvThreshold = livePresence?.mv_threshold ?? threshold;

  const runSimulation = async () => {
    try {
      const fall = await simulate.mutateAsync({
        residentId: (isFacility ? active?.id : selectedResident?.id) ?? null,
      });
      if (fall) alarmStore.raise(fall);
      toast.warning("[시뮬레이션] 낙상 이벤트 발생 — 알람 응답 파이프라인 확인");
    } catch (err) {
      toast.error(
        err instanceof ApiError || err instanceof Error ? err.message : "시뮬레이션 실패",
      );
    }
  };

  const title = isFacility ? "요양원 통합 대시보드" : "가정 모니터링 대시보드";
  const inactive = detection !== "ACTIVE" && !liveMode;

  return (
    <div className="flex flex-col">
      <Header title={title} />

      <div className="p-6 space-y-6">
        {inactive && (
          <div
            className={`bg-surface border rounded p-4 flex items-center gap-3 ${detection === "OFFLINE" ? "border-primary/50" : "border-warning/40"}`}
          >
            <div
              className={`size-2 rounded-full ${detection === "OFFLINE" ? "bg-primary" : "bg-warning"}`}
            />
            <div className="text-sm">
              <span
                className={`font-semibold ${detection === "OFFLINE" ? "text-primary" : "text-warning"}`}
              >
                {detection === "OFFLINE" ? "백엔드에 연결할 수 없습니다" : "낙상 감지 미동작"}
              </span>
              <span className="text-muted">
                {" "}
                ·{" "}
                {detection === "OFFLINE"
                  ? "서비스 API 가 응답하지 않습니다. 이력·설정은 다시 연결되면 표시됩니다."
                  : "실시간 감지 경로(엣지 → 클라우드)가 아직 연결되지 않았습니다. 이력·설정·기기 관리는 정상 동작합니다."}
              </span>
            </div>
          </div>
        )}

        {showDevicePicker && (
          <section>
            <SectionTitle>장치 선택 · 선택한 장치의 모니터링 정보만 표시</SectionTitle>
            <div className="flex flex-wrap gap-2">
              {homeDevices.map((d) => (
                <button
                  key={d.id}
                  onClick={() => setActiveDeviceId(d.id)}
                  className={`px-3 py-1.5 rounded border text-xs font-mono ${
                    selectedDevice?.id === d.id
                      ? "border-foreground bg-surface"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {d.name}
                  {d.id === primaryDeviceId && (
                    <span className="ml-1.5 text-[9px] text-success uppercase">primary</span>
                  )}
                </button>
              ))}
            </div>
          </section>
        )}

        {isFacility && (
          <section>
            <SectionTitle>다중 거주자 상태 그리드 · 호실 클릭 시 해당 거주자를 활성화</SectionTitle>
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
              {residents.map((r) => (
                <button
                  key={r.id}
                  onClick={() => setActiveId(r.id)}
                  className={`text-left bg-surface border rounded-lg p-4 hover:border-muted transition-colors ${
                    r.id === active?.id
                      ? "border-foreground ring-2 ring-primary/30"
                      : r.state === "FALL"
                        ? "border-primary animate-alert"
                        : r.state === "SUSPECT"
                          ? "border-warning"
                          : "border-border"
                  }`}
                >
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <div className="text-sm font-semibold">
                        {r.room}호 · {r.name}
                      </div>
                      <div className="text-[10px] text-muted font-mono">
                        {devices.find((d) => d.id === r.deviceId)?.name ?? "장치 미매핑"} ·{" "}
                        {r.age ?? "—"}세
                      </div>
                    </div>
                    <span
                      className={`text-[10px] font-mono uppercase font-bold ${unifiedStatusColor(r.state, r.presence)}`}
                    >
                      {unifiedStatusLabel(r.state, r.presence)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[10px] font-mono">
                    <span className={`font-bold ${presenceColor(r.presence)}`}>
                      {presenceLabel(r.presence)}
                    </span>
                    <span className="text-muted">MV {r.mv != null ? r.mv.toFixed(2) : "—"}</span>
                    <span className="text-muted">
                      W {r.wander != null ? r.wander.toFixed(2) : "—"}
                    </span>
                    <span className={r.online ? "text-success" : "text-muted"}>
                      {r.online == null ? "—" : r.online ? "ON" : "OFF"}
                    </span>
                  </div>
                </button>
              ))}
              {residents.length === 0 && (
                <div className="col-span-full bg-surface border border-border rounded p-6 text-center text-xs text-muted">
                  등록된 거주자가 없습니다. 입소자 관리에서 추가하세요.
                </div>
              )}
            </div>
          </section>
        )}

        <section className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          {isFacility ? (
            <>
              <StatCard
                label="대상자"
                value={active?.name ?? "미등록"}
                sub={activeDevice ? `${activeDevice.room}호 · ${activeDevice.name}` : "장치 미매핑"}
              />
              <StatCard
                label="재실감지"
                value={presenceLabel(active?.presence)}
                sub={
                  active?.presence != null
                    ? `MV ${(active.mv ?? 0).toFixed(2)}/${threshold.toFixed(2)} · WANDER ${(active.wander ?? 0).toFixed(2)}/${wanderRatioThreshold.toFixed(2)}×`
                    : "실시간 경로 연결 후 표시됩니다"
                }
                tone={active?.presence === "PRESENT" ? "success" : "default"}
              />
              <StatCard
                label="낙상 감지"
                value={stateLabel(active?.state)}
                sub={
                  active?.state != null
                    ? `DNN 신뢰도 ${((active.confidence ?? 0) * 100).toFixed(1)}%`
                    : `판정 임계값 ${(config?.threshold ?? 0.468).toFixed(3)} · 추론 경로 미연결`
                }
                tone={fallTone(active?.state)}
              />
              <StatCard
                label="수신기 상태"
                value={activeDevice ? (activeDevice.online ? "수신 중" : "연결 끊김") : "장치 없음"}
                sub={
                  activeDevice
                    ? `RSSI ${activeDevice.currentRssi ?? "—"}dBm · ${activeDevice.connection}`
                    : "장치 매핑 필요"
                }
                tone={activeDevice?.online ? "success" : "danger"}
              />
            </>
          ) : (
            <>
              <StatCard
                label="대상자"
                value={selectedResident?.name ?? "미등록"}
                sub={
                  selectedDevice
                    ? `${selectedDevice.room} · ${selectedDevice.name}`
                    : "등록된 장치 없음"
                }
              />
              <StatCard
                label="재실감지"
                value={
                  livePresence
                    ? presenceLabel(livePresence.state === "present" ? "PRESENT" : "ABSENT")
                    : presenceLabel(selectedResident?.presence)
                }
                sub={
                  livePresence?.mv_current != null
                    ? `MV ${livePresence.mv_current.toFixed(2)}/${liveMvThreshold.toFixed(2)} · WANDER ${(livePresence.wander_ratio ?? 0).toFixed(2)}/${(livePresence.wander_ratio_threshold ?? 0).toFixed(2)}`
                    : selectedResident?.presence != null
                      ? `MV ${(selectedResident.mv ?? 0).toFixed(2)}/${threshold.toFixed(2)}`
                      : "실시간 경로 연결 후 표시됩니다"
                }
                tone={
                  (
                    livePresence
                      ? livePresence.state === "present"
                      : selectedResident?.presence === "PRESENT"
                  )
                    ? "success"
                    : "default"
                }
              />
              {/* 낙상 축은 재실과 **독립**이다. 재실이 흐르고 있어도 추론이 안 붙었으면
                  "감지 미동작"이어야 한다 — 그 부재를 "이상 없음"으로 보이면 안 된다. */}
              <StatCard
                label="낙상 감지"
                value={
                  liveFall ? stateLabel(liveFall.detect_state) : stateLabel(selectedResident?.state)
                }
                sub={
                  liveFall
                    ? `낙상 확률 ${((liveFall.proba_fall ?? 0) * 100).toFixed(1)}% · 임계값 ${liveFall.threshold.toFixed(3)} · ${liveFall.postprocess}`
                    : `판정 임계값 ${(config?.threshold ?? 0.468).toFixed(3)} · 추론 경로 미연결`
                }
                tone={
                  liveFall ? fallTone(liveFall.detect_state) : fallTone(selectedResident?.state)
                }
              />
              <StatCard
                label="수신기 상태"
                value={
                  liveDevice?.online === true
                    ? "수신 중"
                    : liveDevice?.online === false
                      ? "연결 끊김"
                      : DETECTION_INACTIVE_LABEL
                }
                sub={
                  liveDevice?.link
                    ? `${(liveDevice.link.hz_1s ?? 0).toFixed(0)}Hz · RSSI ${liveDevice.link.rssi ?? "—"}dBm · ${liveDevice.link.transport}`
                    : "엣지 텔레메트리 연결 후 표시됩니다"
                }
                tone={liveDevice?.online === true ? "success" : "default"}
              />
            </>
          )}
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-surface border border-border rounded-lg flex flex-col">
            <div className="p-4 border-b border-border flex justify-between items-center">
              <h3 className="text-xs font-mono font-semibold uppercase tracking-widest">
                실시간 움직임 감지 ·{" "}
                {isFacility ? (active?.name ?? "—") : (selectedResident?.name ?? "수신기")}
              </h3>
              <div className="flex items-center gap-2">
                {liveMode ? (
                  <span className="text-[10px] font-mono text-success px-2 py-0.5 rounded bg-success/10 border border-success/30">
                    ● LIVE · {(liveDevice?.link?.hz_1s ?? 0).toFixed(0)}Hz · {chartData.length}{" "}
                    samples
                  </span>
                ) : chartData.length > 0 ? (
                  <span className="text-[10px] font-mono text-muted px-2 py-0.5 rounded bg-background border border-border">
                    시뮬레이션 · {chartData.length} samples
                  </span>
                ) : (
                  <span className="text-[10px] font-mono text-muted px-2 py-0.5 rounded bg-background border border-border">
                    ○ {DETECTION_INACTIVE_LABEL}
                  </span>
                )}
                {features.fallSimulate && (
                  <button
                    onClick={runSimulation}
                    disabled={simulate.isPending}
                    className="px-2.5 py-1 rounded text-[10px] font-mono uppercase font-bold bg-danger text-white hover:brightness-110 disabled:opacity-60"
                    title="현재 활성 대상에 낙상 이벤트를 강제 발생시켜 알람·응답·이력 경로를 검증합니다"
                  >
                    ⚠ 낙상 시뮬레이션
                  </button>
                )}
              </div>
            </div>
            <div className="p-4 h-72">
              {chartData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                    <XAxis
                      dataKey="i"
                      tick={CHART_STYLE.tick}
                      axisLine={CHART_STYLE.axis}
                      tickLine={false}
                    />
                    <YAxis
                      tick={CHART_STYLE.tick}
                      axisLine={CHART_STYLE.axis}
                      tickLine={false}
                      domain={[0, "dataMax + 1"]}
                    />
                    <Tooltip contentStyle={CHART_STYLE.tooltip} labelStyle={{ color: "#71717a" }} />
                    <ReferenceLine
                      y={liveMode ? liveMvThreshold : threshold}
                      stroke="#ef4444"
                      strokeDasharray="4 4"
                      label={{
                        value: `임계값 ${(liveMode ? liveMvThreshold : threshold).toFixed(2)}`,
                        fill: "#ef4444",
                        fontSize: 10,
                        position: "insideTopRight",
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="mv"
                      stroke="#22c55e"
                      strokeWidth={1.5}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex flex-col items-center justify-center gap-2 text-muted">
                  <div className="text-3xl">📡</div>
                  <div className="text-sm font-semibold">
                    실시간 데이터 없음 · {DETECTION_INACTIVE_LABEL}
                  </div>
                  <div className="text-xs font-mono text-center max-w-md">
                    엣지(라즈베리파이) → 클라우드 실시간 경로는 다음 단계에서 연결됩니다. 그 전까지
                    이 화면은 저장된 이력과 설정만 보여주며, 낙상 감지가 동작하지 않는 상태입니다.
                  </div>
                </div>
              )}
            </div>
          </div>

          <EventLogPanel />
        </div>

        <section className="bg-surface border border-border rounded-lg overflow-hidden">
          <div className="p-4 border-b border-border">
            <h3 className="text-xs font-mono font-semibold uppercase tracking-widest">
              최근 낙상 이벤트
            </h3>
          </div>
          <table className="w-full text-left text-sm font-mono">
            <thead>
              <tr className="text-[10px] text-muted border-b border-border">
                <th className="p-3 font-medium uppercase">Timestamp</th>
                <th className="p-3 font-medium uppercase">Resident</th>
                <th className="p-3 font-medium uppercase">DNN Confidence</th>
                <th className="p-3 font-medium uppercase text-right">Response</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {falls.map((f) => (
                <tr key={f.id}>
                  <td className="p-3 text-muted">{fmtDateTime(f.occurredAt)}</td>
                  <td className="p-3">
                    {f.residentName} (Room {f.room})
                  </td>
                  <td className="p-3">{(f.confidence * 100).toFixed(1)}%</td>
                  <td className="p-3 text-right">
                    <ResponseBadge response={f.response} />
                  </td>
                </tr>
              ))}
              {falls.length === 0 && (
                <tr>
                  <td colSpan={4} className="p-6 text-center text-muted text-xs">
                    감지된 낙상이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}

const STATE_SEVERITY: StateMachine[] = ["FALL", "SUSPECT", "COOLDOWN", "IDLE"];

function worstState(list: { state?: StateMachine | null }[]): StateMachine | null {
  for (const s of STATE_SEVERITY) {
    if (list.some((r) => r.state === s)) return s;
  }
  return null;
}

function fallTone(state: StateMachine | null | undefined): "danger" | "warn" | "default" {
  if (state === "FALL") return "danger";
  if (state === "SUSPECT") return "warn";
  return "default";
}

export function Header({ title }: { title: string }) {
  const [now, setNow] = useState<string>("");
  const { user } = useAuth();
  const isFacility = user?.service === "FACILITY";
  const detection = useDetectionStatus();
  const alarm = useAlarm();
  const { data: residents = [] } = useResidents();
  const presentCount = residents.filter((r) => r.presence === "PRESENT").length;
  const anyPresenceKnown = residents.some((r) => r.presence != null);
  const absentCount = residents.filter((r) => r.presence === "ABSENT").length;
  // alarm 이 있으면 사용자가 응답하기 전까지 FALL 을 유지한다.
  const displayState: StateMachine | null = alarm ? "FALL" : worstState(residents);
  useEffect(() => {
    const fmt = () => setNow(new Date().toLocaleString("sv-SE").replace("T", " ").slice(0, 19));
    fmt();
    const id = setInterval(fmt, 1000);
    return () => clearInterval(id);
  }, []);
  const statusChip =
    detection === "ACTIVE"
      ? { text: "ONLINE", cls: "bg-success/10 text-success border-success/20" }
      : detection === "OFFLINE"
        ? { text: "OFFLINE", cls: "bg-primary/10 text-primary border-primary/20" }
        : { text: DETECTION_INACTIVE_LABEL, cls: "bg-surface text-muted border-border" };
  return (
    <header className="h-14 border-b border-border flex items-center justify-between px-6 shrink-0 bg-background sticky top-0 z-10">
      <div className="flex items-center gap-4">
        <h2 className="text-sm font-medium">{title}</h2>
        <div className="flex gap-2">
          <span
            className={`px-2 py-0.5 rounded text-[10px] font-mono border font-bold ${stateColor(displayState)} ${
              displayState === "FALL"
                ? "bg-primary/10 border-primary/20 animate-pulse"
                : "bg-surface border-border"
            }`}
          >
            {stateLabel(displayState)}
          </span>
          <span
            className={`px-2 py-0.5 rounded text-[10px] font-mono border ${
              presentCount > 0
                ? "bg-success/10 text-success border-success/20"
                : "bg-muted/10 text-muted border-muted/20"
            }`}
            title="재실 감지: 움직임+WANDER 통합 판단"
          >
            {!anyPresenceKnown
              ? `재실 ${DETECTION_INACTIVE_LABEL}`
              : isFacility
                ? presentCount > 0
                  ? `재실 ${presentCount}`
                  : `퇴실 ${absentCount}`
                : presentCount > 0
                  ? "재실"
                  : "퇴실"}
          </span>
          <span className={`px-2 py-0.5 rounded text-[10px] font-mono border ${statusChip.cls}`}>
            {statusChip.text}
          </span>
        </div>
      </div>
      <div className="text-right">
        <div className="text-[10px] font-mono text-muted">SYSTEM TIME</div>
        <div className="text-xs font-mono min-h-[14px]" suppressHydrationWarning>
          {now || "—"}
        </div>
      </div>
    </header>
  );
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-mono font-semibold uppercase tracking-widest text-muted mb-3">
      {children}
    </h3>
  );
}

function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "danger" | "warn" | "success" | "default";
}) {
  const toneClass =
    tone === "danger"
      ? "text-primary"
      : tone === "warn"
        ? "text-warning"
        : tone === "success"
          ? "text-success"
          : "text-foreground";
  return (
    <div className="bg-surface p-4 rounded border border-border">
      <div className="text-[10px] font-mono text-muted mb-1 uppercase">{label}</div>
      <div className={`text-2xl font-mono font-medium tracking-tight ${toneClass}`}>{value}</div>
      <div className="text-[10px] text-muted mt-2 font-mono truncate">{sub}</div>
    </div>
  );
}

export function ResponseBadge({ response }: { response: FallResponse | string }) {
  const map: Record<FallResponse, string> = {
    PENDING: "text-warning border-warning/30 bg-warning/10",
    ACKNOWLEDGED: "text-sky-600 border-sky-600/30 bg-sky-600/10",
    DISPATCHED: "text-success border-success/30 bg-success/10",
    FALSE_ALARM: "text-muted border-border bg-background",
  };
  const key = (response in map ? response : "PENDING") as FallResponse;
  return (
    <span className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${map[key]}`}>
      {FALL_RESPONSE_LABEL[key]}
    </span>
  );
}
