import { useEffect, type ReactNode } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useAuth } from "@/api/auth";
import { AppSidebar } from "@/components/AppSidebar";
import { FallAlarmModal } from "@/components/FallAlarmModal";
import { BackendDetectionBridge } from "@/components/BackendDetectionBridge";

/** 인증 없이 접근 가능한 경로의 단일 진실원. */
export const PUBLIC_PATHS = ["/login", "/signup"];

export function AuthGate({ children }: { children: ReactNode }) {
  // status === "loading" 이 기존 `hydrated` 게이트 역할을 한다 (SSR 에서는 항상 loading → hydration 불일치 없음).
  const { status } = useAuth();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const navigate = useNavigate();
  const isPublic = PUBLIC_PATHS.includes(pathname);

  useEffect(() => {
    if (status === "loading") return;
    if (status === "anon" && !isPublic) {
      navigate({ to: "/login" });
    } else if (status === "authed" && isPublic) {
      navigate({ to: "/" });
    }
  }, [status, isPublic, navigate]);

  if (status === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted font-mono text-xs">
        Loading…
      </div>
    );
  }

  if (isPublic || status !== "authed") {
    return <>{children}</>;
  }

  return (
    <div className="flex h-screen bg-background text-foreground overflow-hidden">
      <AppSidebar />
      <main className="flex-1 overflow-y-auto">{children}</main>
      <FallAlarmModal />
      {/* 레거시 실시간 브리지(/ws/live → 목업 스토어)는 VITE_ENABLE_LIVE=1 일 때만 */}
      {import.meta.env.VITE_ENABLE_LIVE === "1" && <BackendDetectionBridge />}
    </div>
  );
}
