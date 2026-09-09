/**
 * 레거시 로컬 백엔드(main.py, USB 시리얼) 제어·진단 패널 — VITE_ENABLE_LIVE=1 일 때만 렌더된다.
 * 실시간 경로가 클라우드(엣지 → MQTT)로 옮겨지면 이 파일은 제거 대상이다. 내부 상태는 여전히 mock-store 의
 * port/serialBaud/backendConnected 를 쓴다 (BackendDetectionBridge 가 채운다).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { SectionTitle } from "@/routes/index";
import {
  fetchPorts,
  startMonitor as startBackendMonitor,
  stopMonitor as stopBackendMonitor,
  useMonitorStatus,
  type DetectedPort,
} from "@/lib/backend";
import { setPort, setSerialBaud, useStore } from "@/lib/mock-store";

export function useDetectedPorts() {
  const [ports, setPorts] = useState<DetectedPort[]>([]);
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const refresh = useCallback(async () => {
    try {
      const list = await fetchPorts();
      setPorts(list);
      setBackendUp(true);
    } catch {
      setPorts([]);
      setBackendUp(false);
    }
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);
  return { ports, backendUp, refresh };
}

// tick_count 같은 누적 카운터가 2초 동안 값이 바뀌지 않으면 "루프가 멎었다"고 판단한다.
function useStalledCounter(value: number | undefined, enabled: boolean | undefined): boolean {
  const prevRef = useRef<{ value: number; at: number } | null>(null);
  const [stalled, setStalled] = useState(false);
  useEffect(() => {
    if (!enabled || value == null) {
      setStalled(false);
      prevRef.current = null;
      return;
    }
    const prev = prevRef.current;
    if (prev && prev.value === value) {
      if (Date.now() - prev.at > 2000) setStalled(true);
    } else {
      prevRef.current = { value, at: Date.now() };
      setStalled(false);
    }
  }, [value, enabled]);
  return stalled;
}

export function LegacyDiagnosticsPanel() {
  const status = useMonitorStatus(1000);
  const presenceStalled = useStalledCounter(status?.presence.tick_count, status?.presence.enabled);

  if (!status) {
    return (
      <div className="bg-surface border border-border rounded-lg p-5">
        <SectionTitle>진단 · Diagnostics</SectionTitle>
        <p className="text-xs text-muted">로컬 백엔드 응답 대기 중…</p>
      </div>
    );
  }

  const { serial, buffer, presence, detect, notify } = status;
  const streaming = buffer.hz_1s > 0;
  const macTotal = (serial?.frames_ok ?? 0) + (serial?.mac_filtered ?? 0);
  const macRatio = macTotal > 0 ? (serial?.mac_filtered ?? 0) / macTotal : 0;

  return (
    <div className="bg-surface border border-border rounded-lg p-5 space-y-4">
      <div className="flex items-center justify-between">
        <SectionTitle>진단 · Diagnostics (로컬 백엔드 · 연결 / 캘리브레이션 / 스트림)</SectionTitle>
        <span
          className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${
            streaming
              ? "text-success border-success/30 bg-success/10"
              : "text-primary border-primary/30 bg-primary/10"
          }`}
        >
          {streaming ? `● 스트림 수신 중 · ${buffer.hz_1s}Hz` : "○ 프레임 없음 · 스트림 정지"}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <DiagGroup title="시리얼">
          <DiagRow
            label="연결"
            value={serial?.connected ? `● ${serial.port ?? "-"}` : "○ 미연결"}
            tone={serial?.connected ? "success" : "danger"}
          />
          <DiagRow label="Baud" value={String(serial?.baud ?? "-")} />
          <DiagRow label="재연결 횟수" value={String(serial?.reconnects ?? 0)} />
          <DiagRow label="파싱 성공 프레임" value={String(serial?.frames_ok ?? 0)} />
          <DiagRow
            label="체크섬 오류"
            value={String(serial?.checksum_errors ?? 0)}
            tone={(serial?.checksum_errors ?? 0) > 0 ? "warn" : undefined}
          />
          <DiagRow
            label="재동기화"
            value={String(serial?.resyncs ?? 0)}
            tone={(serial?.resyncs ?? 0) > 0 ? "warn" : undefined}
          />
          <DiagRow
            label="MAC 필터링됨"
            value={String(serial?.mac_filtered ?? 0)}
            tone={macRatio > 0.5 ? "danger" : macRatio > 0 ? "warn" : undefined}
            hint={
              macRatio > 0.5
                ? "프레임 대부분이 MAC 필터에 걸리고 있습니다 — 송신기 MAC이 예상(1a:00:00:00:00:00)과 다를 수 있습니다"
                : undefined
            }
          />
        </DiagGroup>

        <DiagGroup title="버퍼 · 스트림 수신">
          <DiagRow
            label="현재 Hz (1초)"
            value={String(buffer.hz_1s)}
            tone={streaming ? "success" : "danger"}
            hint={!streaming ? "0이면 지금 프레임이 전혀 들어오지 않고 있다는 뜻입니다" : undefined}
          />
          <DiagRow label="평균 Hz (5초)" value={String(buffer.hz_5s)} />
          <DiagRow label="버퍼 길이" value={`${buffer.buffered_seconds}s`} />
          <DiagRow label="누적 프레임" value={String(buffer.total_frames)} />
        </DiagGroup>

        <DiagGroup title="재실 루프 · 움직임/Wander (DL 모델과 무관)">
          <DiagRow
            label="상태"
            value={presence.enabled ? "● 동작 중" : "○ 비활성"}
            tone={presence.enabled ? "success" : "danger"}
          />
          <DiagRow
            label="tick 횟수"
            value={String(presence.tick_count ?? 0)}
            tone={presenceStalled ? "danger" : undefined}
            hint={
              presenceStalled ? "값이 멈춰 있습니다 — 재실 루프가 멎었을 수 있습니다" : undefined
            }
          />
          <DiagRow label="skip 횟수" value={String(presence.skip_count ?? 0)} />
          {presence.last_error && (
            <DiagRow label="마지막 오류" value={presence.last_error} tone="danger" />
          )}
        </DiagGroup>

        <DiagGroup title="낙상 모델 (DL)">
          <DiagRow
            label="상태"
            value={detect.enabled ? "● 가동 중" : "○ 비활성"}
            tone={detect.enabled ? "success" : "warn"}
          />
          {!detect.enabled && detect.reason && (
            <DiagRow label="비활성 사유" value={detect.reason} />
          )}
          {detect.enabled && (
            <>
              <DiagRow label="추론 횟수" value={String(detect.inference_count ?? 0)} />
              <DiagRow label="skip 횟수" value={String(detect.skip_count ?? 0)} />
              {detect.last_error && (
                <DiagRow label="마지막 오류" value={detect.last_error} tone="danger" />
              )}
            </>
          )}
        </DiagGroup>
      </div>

      <div className="text-[10px] font-mono text-muted">
        알림(ntfy):{" "}
        {notify.enabled
          ? `활성 · 수신자 ${notify.count}명`
          : `비활성${notify.reason ? ` · ${notify.reason}` : ""}`}
      </div>
    </div>
  );
}

function DiagGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-border rounded p-3 space-y-1.5">
      <div className="text-[10px] font-mono uppercase text-muted tracking-wider mb-1">{title}</div>
      {children}
    </div>
  );
}

function DiagRow({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "success" | "warn" | "danger";
  hint?: string;
}) {
  const cls =
    tone === "success"
      ? "text-success"
      : tone === "warn"
        ? "text-warning"
        : tone === "danger"
          ? "text-primary"
          : "text-foreground";
  return (
    <div>
      <div className="flex justify-between font-mono text-xs">
        <span className="text-muted">{label}</span>
        <span className={cls}>{value}</span>
      </div>
      {hint && (
        <div className="text-[9px] text-primary/80 font-mono mt-0.5 leading-relaxed">{hint}</div>
      )}
    </div>
  );
}

/** 로컬 백엔드 시리얼 연결 제어 (HOME 전용). */
export function LegacyConnectionPanel() {
  const port = useStore((s) => s.port);
  const baud = useStore((s) => s.serialBaud);
  const backendConnected = useStore((s) => s.backendConnected);
  const [busy, setBusy] = useState(false);
  const { ports, backendUp, refresh } = useDetectedPorts();
  const connectionBusy = backendConnected || busy;

  const handleToggle = async () => {
    setBusy(true);
    try {
      if (backendConnected) {
        await stopBackendMonitor();
        toast.success("실장치 연결 해제됨");
      } else {
        await startBackendMonitor({ port: port || undefined, baud });
        toast.success("연결 요청 완료 · 아래 진단 패널에서 상태를 확인하세요");
      }
    } catch (e) {
      toast.error(`연결 제어 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!backendUp || ports.length === 0 || connectionBusy) return;
    if (!ports.some((p) => p.device === port)) {
      const preferred = ports.find((p) => p.active) ?? ports[0];
      setPort(preferred.device);
    }
  }, [backendUp, ports, port, connectionBusy]);

  return (
    <div className="bg-surface border border-border rounded-lg p-5 space-y-4">
      <div className="flex justify-between items-center">
        <div>
          <SectionTitle>로컬 시리얼 연결 · Legacy Connection</SectionTitle>
          <p className="text-xs text-muted">
            개발 PC 의 로컬 백엔드(main.py)에 USB 수신기를 연결합니다. 연결 중에는 포트를 변경할 수
            없습니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`text-[10px] font-mono uppercase ${backendConnected ? "text-success" : "text-muted"}`}
          >
            {backendConnected ? "● Connected" : "○ Disconnected"}
          </span>
          <button
            onClick={() => void handleToggle()}
            disabled={busy || backendUp !== true}
            className={`px-4 py-2 rounded font-mono text-xs font-bold uppercase tracking-widest ${backendConnected ? "bg-primary text-primary-foreground" : "bg-success text-white"} hover:brightness-110 disabled:opacity-50`}
          >
            {busy ? "…" : backendConnected ? "■ Disconnect" : "▶ Connect"}
          </button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="flex items-center justify-between">
            <label className="text-[10px] font-mono uppercase text-muted">Port</label>
            <button
              onClick={refresh}
              disabled={connectionBusy}
              className="text-[9px] font-mono uppercase text-muted hover:text-foreground disabled:opacity-50"
            >
              ↻ 재탐지
            </button>
          </div>
          {backendUp ? (
            <select
              value={port}
              onChange={(e) => setPort(e.target.value)}
              disabled={connectionBusy || ports.length === 0}
              className="w-full mt-1 bg-surface border border-border rounded px-3 py-2 text-xs font-mono disabled:opacity-50"
            >
              {ports.length === 0 && <option value="">감지된 포트 없음</option>}
              {ports.map((p) => (
                <option key={p.device} value={p.device}>
                  {p.device}
                  {p.active ? " (수신 중)" : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={port}
              onChange={(e) => setPort(e.target.value)}
              disabled={connectionBusy}
              placeholder="/dev/cu.usbmodemXXXX"
              className="w-full mt-1 bg-surface border border-border rounded px-3 py-2 text-xs font-mono disabled:opacity-50"
            />
          )}
          {backendUp === false && (
            <p className="mt-1 text-[9px] text-warning font-mono">
              로컬 백엔드 미실행 · main.py 실행 시 자동 탐지
            </p>
          )}
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-muted">Baud Rate</label>
          <select
            value={baud}
            onChange={(e) => setSerialBaud(Number(e.target.value))}
            disabled={connectionBusy}
            className="w-full mt-1 bg-surface border border-border rounded px-3 py-2 text-xs font-mono disabled:opacity-50"
          >
            {[115200, 230400, 460800, 921600, 1500000].map((b) => (
              <option key={b} value={b}>
                {b.toLocaleString()}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
