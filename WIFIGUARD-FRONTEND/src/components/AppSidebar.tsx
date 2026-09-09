import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/api/auth";
import { useDetectionStatus, useFalls, useResidents } from "@/api/queries";
import { fmtDateTime, stateColor, stateLabel } from "@/lib/format";

/** F-W11 3상태 표시: ACTIVE(정상) / INACTIVE(낙상 감지 미동작) / OFFLINE(백엔드 미응답) */
const STATUS_TEXT = {
  ACTIVE: "System Operational",
  INACTIVE: "Detection Inactive · 감지 미동작",
  OFFLINE: "Backend Offline",
} as const;

export function AppSidebar() {
  const { user, facility, logout } = useAuth();
  const detection = useDetectionStatus();
  const { data: residents } = useResidents();
  const { data: falls } = useFalls({ limit: 1 });
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const navigate = useNavigate();
  const qc = useQueryClient();

  // 실시간 경로가 없으면 state 는 null → "감지 미동작". 목업에서는 첫 거주자의 시뮬레이션 상태.
  const currentState = residents?.[0]?.state ?? null;
  const lastFallAt = falls?.items[0]?.occurredAt ?? null;
  const isFacility = user?.service === "FACILITY";
  const isRoot = user?.role === "ROOT";

  const navMain = [
    { to: "/", label: "실시간 관제" },
    { to: "/history", label: "낙상 이력" },
    { to: "/event-log", label: "이벤트 로그" },
    { to: "/devices", label: "장치 설정" },
  ];
  const navMgmt = [
    { to: "/residents", label: isFacility ? "입소자 관리" : "재실 대상 관리" },
    ...(isFacility && isRoot ? [{ to: "/facility-members", label: "시설 멤버" }] : []),
    { to: "/notifications", label: "알림 게이트웨이" },
    { to: "/config", label: "알고리즘 설정" },
    { to: "/train", label: "모델 학습" },
  ];

  const handleLogout = async () => {
    await logout();
    qc.clear();
    navigate({ to: "/login" });
  };

  const dot =
    detection === "ACTIVE"
      ? "bg-success animate-signal"
      : detection === "OFFLINE"
        ? "bg-primary"
        : "bg-muted";

  return (
    <aside className="w-64 border-r border-border flex flex-col shrink-0 bg-background">
      <div className="p-6 border-b border-border">
        <div className="flex items-center gap-2 mb-1">
          <div className={`size-3 rounded-full ${dot}`} />
          <h1 className="font-mono font-bold tracking-tighter text-lg">CSI-GUARD</h1>
        </div>
        <p className="text-[10px] text-muted uppercase tracking-widest font-mono">
          {STATUS_TEXT[detection]}
        </p>
      </div>

      {user && (
        <div className="px-4 pt-4 pb-2 border-b border-border">
          <div className="bg-surface border border-border rounded p-3 space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold truncate">{user.name}</span>
              <span className="text-[9px] font-mono uppercase text-muted">{user.role}</span>
            </div>
            <div className="text-[10px] font-mono text-muted truncate">
              {isFacility ? `${facility?.name ?? "-"} · ${user.service}` : "가정 서비스 · HOME"}
            </div>
            {isFacility && facility && (
              <div className="text-[9px] font-mono text-muted">
                INVITE: <span className="text-foreground/70">{facility.inviteCode}</span>
              </div>
            )}
          </div>
        </div>
      )}

      <nav className="flex-1 px-4 py-4 space-y-1 overflow-y-auto">
        <div className="text-[10px] font-semibold text-muted mb-2 px-2 uppercase tracking-wider">
          Main Dashboard
        </div>
        {navMain.map((n) => (
          <NavLink key={n.to} to={n.to} label={n.label} active={pathname === n.to} />
        ))}
        <div className="pt-4 text-[10px] font-semibold text-muted mb-2 px-2 uppercase tracking-wider">
          Management
        </div>
        {navMgmt.map((n) => (
          <NavLink key={n.to} to={n.to} label={n.label} active={pathname === n.to} />
        ))}

        <div className="pt-6 text-[10px] font-semibold text-muted mb-2 px-2 uppercase tracking-wider">
          Fall Status
        </div>
        <div className="px-2 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted font-mono">STATE</span>
            <span className={`font-mono font-medium ${stateColor(currentState)}`}>
              {stateLabel(currentState)}
            </span>
          </div>
          <div className="flex items-center justify-between text-[10px] font-mono">
            <span className="text-muted">LAST FALL</span>
            <span className="text-foreground/70">
              {lastFallAt ? fmtDateTime(lastFallAt).slice(-8) : "—"}
            </span>
          </div>
        </div>
      </nav>

      <div className="p-4 border-t border-border space-y-2">
        <Link
          to="/account"
          className="block w-full text-center py-2 border border-border rounded text-[11px] font-mono uppercase tracking-widest text-muted hover:text-foreground hover:bg-surface/50"
        >
          계정 관리
        </Link>
        <button
          onClick={handleLogout}
          className="w-full py-2 border border-border rounded text-[11px] font-mono uppercase tracking-widest text-muted hover:text-primary hover:border-primary/40"
        >
          Logout
        </button>
      </div>
    </aside>
  );
}

function NavLink({ to, label, active }: { to: string; label: string; active: boolean }) {
  return (
    <Link
      to={to}
      className={`flex items-center gap-3 px-3 py-2 rounded text-sm transition-colors ${
        active
          ? "bg-surface text-foreground border border-border/50"
          : "text-muted hover:text-foreground hover:bg-surface/50 border border-transparent"
      }`}
    >
      <span>{label}</span>
    </Link>
  );
}
