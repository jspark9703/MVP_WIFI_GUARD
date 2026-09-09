// 로컬 백엔드(backend/main.py, 127.0.0.1:8000) API 클라이언트.
// 대시보드와 같은 맥에서 돌아가는 FastAPI 서버로, 수신기 시리얼 스트림을 중계한다.

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

// 서비스 API 와 같은 값을 쓴다 (src/api/client.ts API_BASE). 실시간 경로가 클라우드로 옮겨지기 전까지는
// 로컬 백엔드(main.py) 전용이며 VITE_ENABLE_LIVE=1 일 때만 사용된다.
export const BACKEND_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);
export const WS_URL =
  import.meta.env.VITE_WS_URL ?? `${BACKEND_URL.replace(/^http/, "ws")}/ws/live`;

export interface DetectedPort {
  device: string;
  description: string | null;
  serial_number: string | null;
  active: boolean;
}

export async function fetchPorts(): Promise<DetectedPort[]> {
  const res = await fetch(`${BACKEND_URL}/ports`, { signal: AbortSignal.timeout(2000) });
  if (!res.ok) throw new Error(`ports ${res.status}`);
  const data = (await res.json()) as { ports: DetectedPort[] };
  return data.ports;
}

export interface MonitorStartPayload {
  port?: string;
  baud?: number;
  preferred_serial?: string;
}

export async function startMonitor(
  payload: MonitorStartPayload = {},
): Promise<{ status: string; port: string | null; connected: boolean }> {
  const res = await fetch(`${BACKEND_URL}/monitor/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `monitor/start ${res.status}` }));
    throw new Error(body.detail ?? `monitor/start ${res.status}`);
  }
  return res.json();
}

export async function stopMonitor(): Promise<{ status: string }> {
  const res = await fetch(`${BACKEND_URL}/monitor/stop`, {
    method: "POST",
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`monitor/stop ${res.status}`);
  return res.json();
}

// 로컬 백엔드 프로세스 자체가 떠 있는지(수신기 연결 여부와 무관) — 온보딩/장치 재설정
// 화면이 mock 타이머 대신 실제 캘리브레이션 API를 쓸지 결정하는 데 쓰인다.
export function useBackendUp(): boolean {
  const [up, setUp] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchPorts()
      .then(() => {
        if (!cancelled) setUp(true);
      })
      .catch(() => {
        if (!cancelled) setUp(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return up;
}

// ---- 실시간 스트림 (/ws/live, 10Hz) ----

export type DetectState = "IDLE" | "SUSPECT" | "FALL" | "COOLDOWN";

// 재실(Presence) 상태 — 낙상 DL 모델과 무관한 별도 신호(움직임 MV + Wander)의 결과.
export type PresenceState = "present" | "absent";

export interface LiveSample {
  t: number;
  connected: boolean; // 백엔드-수신기 시리얼 연결 여부
  hz_1s: number;
  rssi: number | null;
  buffered_seconds: number;
  amp_mean: number | null;
  amp_std: number | null; // 최근 0.5초 진폭 표준편차 (움직임 근사 지표)
  // 낙상 판정 (백엔드 모델 가동 시에만 존재)
  detect_state?: DetectState;
  proba_fall?: number | null; // 최근 3초 윈도우 낙상 확률 (0.25초 주기 갱신)
  threshold?: number; // DL 낙상 판정 임계값 (기본 0.468) — 아래 presence_mv_threshold와는 무관한 별개 값
  fall_count?: number; // 백엔드 기동 후 낙상 확정 횟수
  last_fall_time?: number | null;
  // 재실 감지 (낙상 DL 추론과 병렬로 계산되는 움직임(MV)/Wander 신호 — backend/presence 참고)
  presence_state?: PresenceState | null;
  mv_current?: number | null; // 움직임(이동분산) 현재값
  presence_mv_threshold?: number | null; // 캘리브레이션으로 도출된 움직임 임계값
  wander_current?: number | null;
  wander_baseline?: number | null; // 캘리브레이션으로 도출된 재실 baseline
  wander_ratio_threshold?: number | null;
  wander_ratio?: number | null;
  wander_confirmed?: boolean | null;
  last_activity_at?: number | null;
  presence_just_changed?: boolean | null;
}

// ---- 온보딩 캘리브레이션 (/onboarding/calibrate/*) ----
// 4단계: leaving(공간 비우기) -> waiting_ack(장치 응답 대기) -> waiting_agc(AGC 보정)
// -> measuring(baseline 측정), 총 약 61초. 압축하지 않은 실제 소요시간이다.
export type CalibrationPhase =
  | "idle"
  | "leaving"
  | "waiting_ack"
  | "waiting_agc"
  | "measuring"
  | "done"
  | "error";

export interface CalibrationStatus {
  phase: CalibrationPhase;
  elapsed_s: number | null;
  phase_elapsed_s: number | null;
  agc_duration_s: number | null;
  presence_mv_threshold: number | null;
  wander_baseline: number | null;
  error: string | null;
}

export async function startCalibration(
  payload: MonitorStartPayload = {},
): Promise<{ status: string }> {
  const res = await fetch(`${BACKEND_URL}/onboarding/calibrate/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `calibrate/start ${res.status}` }));
    throw new Error(body.detail ?? `calibrate/start ${res.status}`);
  }
  return res.json();
}

export async function fetchCalibrationStatus(): Promise<CalibrationStatus> {
  const res = await fetch(`${BACKEND_URL}/onboarding/calibrate/status`, {
    signal: AbortSignal.timeout(2000),
  });
  if (!res.ok) throw new Error(`calibrate/status ${res.status}`);
  return res.json();
}

// active=true인 동안 1초 간격으로 캘리브레이션 상태만 폴링한다(시작은 트리거하지
// 않음) — devices.tsx처럼 "재설정" 버튼 클릭 시점에 별도로 startCalibration()을
// 호출하고, 진행 상황 표시만 계속 따라가야 하는 화면에서 쓴다.
export function useCalibrationStatusPoll(active: boolean): CalibrationStatus | null {
  const [status, setStatus] = useState<CalibrationStatus | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await fetchCalibrationStatus();
        if (!cancelled) setStatus(s);
      } catch {
        // 폴링 실패는 무시 — 다음 tick에 재시도 (일시적 네트워크 hiccup 대비)
      }
    };
    poll();
    const id = setInterval(poll, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active]);

  return status;
}

export interface LiveStreamState {
  wsUp: boolean; // 브라우저-백엔드 WebSocket 연결 여부
  last: LiveSample | null;
  history: LiveSample[];
}

// 모든 useLiveStream() 호출자가 이 하나의 WS 연결을 공유하는 모듈 싱글톤.
// 예전에는 컴포넌트마다(BackendDetectionBridge, index.tsx) 각자 별도의 /ws/live
// 연결을 열었다 — 실시간 감지 자체(백엔드의 presence_loop/detector)는 브라우저
// 연결과 무관하게 항상 계속 돌지만, 프론트가 페이지마다 별도 연결을 열고 닫다 보니
// "실시간 관제 페이지에 들어올 때만 연결되는 것처럼" 관찰되는 문제가 있었다.
// 하나의 연결로 통합하면 첫 구독자(로그인 즉시 마운트되는 BackendDetectionBridge)가
// 붙는 순간부터 마지막 구독자가 사라질 때까지 — 즉 로그인해 있는 한 어느 페이지에
// 있든 — 계속 연결이 유지된다.
const SHARED_HISTORY_MAX = 300;
let sharedState: LiveStreamState = { wsUp: false, last: null, history: [] };
let sharedWs: WebSocket | null = null;
let sharedRetryTimer: ReturnType<typeof setTimeout> | null = null;
const sharedListeners = new Set<() => void>();

function notifyShared() {
  sharedListeners.forEach((cb) => cb());
}

function connectShared() {
  if (sharedWs) return;
  try {
    sharedWs = new WebSocket(WS_URL);
  } catch {
    sharedRetryTimer = setTimeout(connectShared, 2000);
    return;
  }
  sharedWs.onopen = () => {
    sharedState = { ...sharedState, wsUp: true };
    notifyShared();
  };
  sharedWs.onmessage = (e) => {
    const sample = JSON.parse(e.data as string) as LiveSample;
    sharedState = {
      wsUp: true,
      last: sample,
      history: [...sharedState.history.slice(-(SHARED_HISTORY_MAX - 1)), sample],
    };
    notifyShared();
  };
  sharedWs.onclose = () => {
    sharedWs = null;
    sharedState = { ...sharedState, wsUp: false };
    notifyShared();
    // 구독자가 아직 남아 있으면(즉 여전히 로그인 상태면) 계속 재연결 시도
    if (sharedListeners.size > 0) sharedRetryTimer = setTimeout(connectShared, 2000);
  };
  sharedWs.onerror = () => sharedWs?.close();
}

function subscribeShared(callback: () => void): () => void {
  sharedListeners.add(callback);
  if (!sharedWs && !sharedRetryTimer) connectShared();
  return () => {
    sharedListeners.delete(callback);
    if (sharedListeners.size === 0) {
      if (sharedRetryTimer) {
        clearTimeout(sharedRetryTimer);
        sharedRetryTimer = null;
      }
      sharedWs?.close();
      sharedWs = null;
    }
  };
}

function getSharedSnapshot(): LiveStreamState {
  return sharedState;
}

export function useLiveStream(): LiveStreamState {
  return useSyncExternalStore(subscribeShared, getSharedSnapshot, getSharedSnapshot);
}

// ---- 진단(Diagnostics) — /monitor/status ----
// 연결/캘리브레이션/스트림 상태를 한 번에 보여주기 위한 폴링 API. 백엔드가 이미
// 계산해 두는 값이지만 지금까지 프론트가 이 엔드포인트를 호출한 적이 없었다 —
// "지금 연결/캘리브레이션/스트림이 되고 있는지 모니터링할 수 없다"는 문제의 직접
// 원인이었다.

export interface SerialStatus {
  connected: boolean;
  port: string | null;
  baud: number;
  reconnects: number;
  frames_ok: number;
  checksum_errors: number;
  resyncs: number;
  mac_filtered: number;
}

export interface BufferStats {
  buffered_frames: number;
  buffered_seconds: number;
  hz_1s: number;
  hz_5s: number;
  last_rssi: number | null;
  last_agc_gain: number | null;
  subcarriers: number | null;
  total_frames: number;
}

export interface DetectStatus {
  enabled: boolean;
  reason?: string | null;
  device?: string;
  threshold?: number;
  state?: DetectState;
  fall_count?: number;
  last_fall_time?: number | null;
  inference_count?: number;
  skip_count?: number;
  latency_ema_ms?: number | null;
  last_error?: string | null;
}

export interface PresenceLoopStatus {
  enabled: boolean;
  tick_count?: number;
  skip_count?: number;
  last_error?: string | null;
  presence_state?: PresenceState | null;
  mv_current?: number | null;
  presence_mv_threshold?: number | null;
  wander_current?: number | null;
  wander_baseline?: number | null;
}

export interface NtfyRecipient {
  id: string;
  name: string | null;
  enabled: boolean;
  notify_fall_enabled: boolean;
  server: string;
  topic: string;
  sent_count: number;
  failed_count: number;
  dropped_count: number;
  last_sent_time: number | null;
  last_error: string | null;
}

export interface NotifyStatus {
  enabled: boolean;
  count: number;
  reason?: string | null;
  recipients: NtfyRecipient[];
}

export interface MonitorStatus {
  serial: SerialStatus | null;
  buffer: BufferStats;
  detect: DetectStatus;
  presence: PresenceLoopStatus;
  notify: NotifyStatus;
  server_time: number;
}

export async function fetchMonitorStatus(): Promise<MonitorStatus> {
  const res = await fetch(`${BACKEND_URL}/monitor/status`, { signal: AbortSignal.timeout(2000) });
  if (!res.ok) throw new Error(`monitor/status ${res.status}`);
  return res.json();
}

// 1초 주기 — "지금 실제로 프레임이 들어오고 있는가"를 실기기 테스트 중 거의
// 실시간에 가깝게 확인할 수 있도록 캘리브레이션 상태 폴링과 동일한 주기를 쓴다.
export function useMonitorStatus(intervalMs = 1000): MonitorStatus | null {
  const [status, setStatus] = useState<MonitorStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await fetchMonitorStatus();
        if (!cancelled) setStatus(s);
      } catch {
        // 폴링 실패는 무시 — 다음 tick에 재시도
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return status;
}

// ---- 알고리즘 설정 실시간 반영 (/presence/config, /detection/config) ----
// presence_loop.py/detector.py 모두 매 tick(0.25초)마다 이 값들을 다시 읽으므로,
// 여기서 바꾸면 백엔드 재시작 없이 곧바로 반영된다 — 단, 인메모리 전용이라
// 백엔드 재시작 시 기본값으로 되돌아간다.

export interface PresenceConfigSnapshot {
  presence_mv_threshold: number;
  wander_ratio_threshold: number;
  presence_timeout_s: number;
  [key: string]: number; // PresenceConfig의 나머지 필드(윈도우/필터 등)도 그대로 내려온다
}

export interface PresenceConfigUpdate {
  presence_mv_threshold?: number;
  wander_ratio_threshold?: number;
  presence_timeout_s?: number;
}

export async function fetchPresenceConfig(): Promise<PresenceConfigSnapshot> {
  const res = await fetch(`${BACKEND_URL}/presence/config`, { signal: AbortSignal.timeout(2000) });
  if (!res.ok) throw new Error(`presence/config ${res.status}`);
  return res.json();
}

export async function updatePresenceConfig(
  payload: PresenceConfigUpdate,
): Promise<PresenceConfigSnapshot> {
  const res = await fetch(`${BACKEND_URL}/presence/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `presence/config ${res.status}` }));
    throw new Error(body.detail ?? `presence/config ${res.status}`);
  }
  return res.json();
}

export interface DetectionConfigSnapshot {
  enabled: boolean;
  threshold: number | null;
  cooldown_seconds: number | null;
}

export interface DetectionConfigUpdate {
  threshold?: number;
  cooldown_seconds?: number;
}

export async function fetchDetectionConfig(): Promise<DetectionConfigSnapshot> {
  const res = await fetch(`${BACKEND_URL}/detection/config`, { signal: AbortSignal.timeout(2000) });
  if (!res.ok) throw new Error(`detection/config ${res.status}`);
  return res.json();
}

export async function updateDetectionConfig(
  payload: DetectionConfigUpdate,
): Promise<DetectionConfigSnapshot> {
  const res = await fetch(`${BACKEND_URL}/detection/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `detection/config ${res.status}` }));
    throw new Error(body.detail ?? `detection/config ${res.status}`);
  }
  return res.json();
}

// ---- 알림(Ntfy) 다중 수신자 관리 ----

export async function fetchNtfyRecipients(): Promise<NtfyRecipient[]> {
  const res = await fetch(`${BACKEND_URL}/notify/recipients`, {
    signal: AbortSignal.timeout(2000),
  });
  if (!res.ok) throw new Error(`notify/recipients ${res.status}`);
  const data = await res.json();
  return data.recipients;
}

export async function addNtfyRecipient(payload: {
  name?: string;
  topic: string;
  server?: string;
  notify_fall_enabled?: boolean;
}): Promise<NtfyRecipient> {
  const res = await fetch(`${BACKEND_URL}/notify/recipients`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `notify/recipients ${res.status}` }));
    throw new Error(body.detail ?? `notify/recipients ${res.status}`);
  }
  return res.json();
}

export async function updateNtfyRecipient(
  id: string,
  payload: { notify_fall_enabled?: boolean },
): Promise<NtfyRecipient> {
  const res = await fetch(`${BACKEND_URL}/notify/recipients/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    const body = await res
      .json()
      .catch(() => ({ detail: `notify/recipients/${id} ${res.status}` }));
    throw new Error(body.detail ?? `notify/recipients/${id} ${res.status}`);
  }
  return res.json();
}

export async function removeNtfyRecipient(id: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/notify/recipients/${id}`, {
    method: "DELETE",
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`notify/recipients/${id} ${res.status}`);
}

export async function testNtfyRecipient(id: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/notify/recipients/${id}/test`, {
    method: "POST",
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`notify/recipients/${id}/test ${res.status}`);
}

export async function testAllNtfyRecipients(): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/notify/test`, {
    method: "POST",
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`notify/test ${res.status}`);
}

export function useNtfyRecipients(intervalMs = 1000): NtfyRecipient[] | null {
  const [recipients, setRecipients] = useState<NtfyRecipient[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetchNtfyRecipients();
        if (!cancelled) setRecipients(r);
      } catch {
        // 폴링 실패 무시
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return recipients;
}
