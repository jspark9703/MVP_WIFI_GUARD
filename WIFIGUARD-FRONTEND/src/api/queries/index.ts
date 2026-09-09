/**
 * 조회 훅 — VITE_USE_MOCK=1 이면 목업 훅, 아니면 TanStack Query(실API).
 * 선택은 모듈 로드 시 한 번(빌드 상수)이라 훅 호출 순서가 바뀌지 않는다.
 */
import { useQuery } from "@tanstack/react-query";

import type {
  Device,
  DetectionStatus,
  EventLogPage,
  Facility,
  FacilityMember,
  FallPage,
  FallResponse,
  LogLevel,
  Recipient,
  Resident,
  TenantConfig,
} from "@/lib/domain";
import { useStore as useMockStore } from "@/lib/mock-store";

import { useAuth } from "../auth";
import { API_BASE, USE_MOCK, apiFetch } from "../client";
import { qk } from "../keys";
import * as mock from "../mock/hooks";
import { ready, type QueryResultLike } from "../types";

function useAuthed(): boolean {
  return useAuth().status === "authed";
}

// ── devices ─────────────────────────────────────────────────────────
function useDevicesReal(): QueryResultLike<Device[]> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.devices(),
    queryFn: () => apiFetch<Device[]>("/devices"),
    enabled,
  });
}
export const useDevices: () => QueryResultLike<Device[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useDevices : useDevicesReal;

// ── residents ───────────────────────────────────────────────────────
function useResidentsReal(): QueryResultLike<Resident[]> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.residents(),
    queryFn: () => apiFetch<Resident[]>("/residents"),
    enabled,
  });
}
export const useResidents: () => QueryResultLike<Resident[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useResidents : useResidentsReal;

// ── falls ───────────────────────────────────────────────────────────
export interface FallsParams {
  response?: FallResponse | null;
  residentId?: string | null;
  from?: string | null;
  to?: string | null;
  limit?: number;
  offset?: number;
}
function useFallsReal(params: FallsParams = {}): QueryResultLike<FallPage> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.falls(params as Record<string, unknown>),
    queryFn: () => apiFetch<FallPage>("/falls", { query: { ...params } }),
    enabled,
  });
}
function useFallsMock(params: FallsParams = {}): QueryResultLike<FallPage> {
  const r = mock.useFalls({ response: params.response, limit: params.limit });
  return ready({
    items: r.data?.items ?? [],
    total: r.data?.total ?? 0,
    limit: params.limit ?? 50,
    offset: 0,
  });
}
export const useFalls: (params?: FallsParams) => QueryResultLike<FallPage> =
  import.meta.env.VITE_USE_MOCK === "1" ? useFallsMock : useFallsReal;

// ── event logs ──────────────────────────────────────────────────────
export interface EventLogsParams {
  level?: LogLevel | null;
  q?: string | null;
  residentId?: string | null;
  from?: string | null;
  to?: string | null;
  limit?: number;
  offset?: number;
}
function useEventLogsReal(params: EventLogsParams = {}): QueryResultLike<EventLogPage> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.eventLogs(params as Record<string, unknown>),
    queryFn: () => apiFetch<EventLogPage>("/event-logs", { query: { ...params } }),
    enabled,
    refetchInterval: 15_000,
  });
}
function useEventLogsMock(params: EventLogsParams = {}): QueryResultLike<EventLogPage> {
  const r = mock.useEventLogs({ level: params.level, q: params.q, limit: params.limit });
  return ready({
    items: r.data?.items ?? [],
    total: r.data?.total ?? 0,
    limit: params.limit ?? 50,
    offset: 0,
  });
}
export const useEventLogs: (params?: EventLogsParams) => QueryResultLike<EventLogPage> =
  import.meta.env.VITE_USE_MOCK === "1" ? useEventLogsMock : useEventLogsReal;

// ── recipients ──────────────────────────────────────────────────────
function useRecipientsReal(): QueryResultLike<Recipient[]> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.recipients(),
    queryFn: () => apiFetch<Recipient[]>("/recipients"),
    enabled,
  });
}
export const useRecipients: () => QueryResultLike<Recipient[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useRecipients : useRecipientsReal;

// ── config ──────────────────────────────────────────────────────────
function useConfigReal(): QueryResultLike<TenantConfig> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.config(),
    queryFn: () => apiFetch<TenantConfig>("/config"),
    enabled,
  });
}
export const useConfig: () => QueryResultLike<TenantConfig> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useConfig : useConfigReal;

// ── facility ────────────────────────────────────────────────────────
function useFacilityReal(): QueryResultLike<Facility | null> {
  const { status, user } = useAuth();
  return useQuery({
    queryKey: qk.facility(),
    queryFn: () => apiFetch<Facility>("/facilities/me"),
    enabled: status === "authed" && user?.service === "FACILITY",
  });
}
export const useFacility: () => QueryResultLike<Facility | null> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useFacility : useFacilityReal;

function useFacilityMembersReal(): QueryResultLike<FacilityMember[]> {
  const { status, user } = useAuth();
  return useQuery({
    queryKey: qk.members(),
    queryFn: () => apiFetch<FacilityMember[]>("/facilities/me/members"),
    enabled: status === "authed" && user?.role === "ROOT",
  });
}
export const useFacilityMembers: () => QueryResultLike<FacilityMember[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useFacilityMembers : useFacilityMembersReal;

// ── MV 시계열 (차트) ─────────────────────────────────────────────────
/** 실모드는 실시간 경로가 없어 빈 배열(차트에 "감지 미동작" 플레이스홀더). 목업은 시뮬레이션 240샘플. */
function useMvHistoryReal(): { t: number; mv: number }[] {
  return EMPTY_HISTORY;
}
const EMPTY_HISTORY: { t: number; mv: number }[] = [];
function useMvHistoryMock(): { t: number; mv: number }[] {
  return useMockStore((s) => s.mvHistory);
}
export const useMvHistory: () => { t: number; mv: number }[] =
  import.meta.env.VITE_USE_MOCK === "1" ? useMvHistoryMock : useMvHistoryReal;

// ── 감지 상태 (F-W11 3상태) ─────────────────────────────────────────
/**
 * ACTIVE  : 실시간 경로가 살아 있음 (이번 단계에서는 목업 시뮬레이션일 때만)
 * INACTIVE: 백엔드는 응답하지만 낙상 감지 경로가 없음 — "감지 미동작"
 * OFFLINE : 백엔드 자체가 응답하지 않음
 */
function useDetectionStatusReal(): DetectionStatus {
  const enabled = useAuthed();
  const q = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
      return res.ok;
    },
    enabled,
    refetchInterval: 30_000,
    retry: 0,
  });
  if (q.isError || q.data === false) return "OFFLINE";
  return "INACTIVE";
}
function useDetectionStatusMock(): DetectionStatus {
  const running = useMockStore((s) => s.running);
  const backendConnected = useMockStore((s) => s.backendConnected);
  const { user } = useAuth();
  const active = user?.service === "FACILITY" ? running : backendConnected;
  return active ? "ACTIVE" : "INACTIVE";
}
export const useDetectionStatus: () => DetectionStatus =
  import.meta.env.VITE_USE_MOCK === "1" ? useDetectionStatusMock : useDetectionStatusReal;
