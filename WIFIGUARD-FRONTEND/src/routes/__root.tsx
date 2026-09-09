import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext, HeadContent, Scripts } from "@tanstack/react-router";
import { type ReactNode } from "react";

import appCss from "../styles.css?url";
import { AuthGate } from "@/components/AuthGate";
import { Toaster } from "sonner";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "실시간 관제 · CSI-Guard" },
      { name: "description", content: "Wi-Fi CSI 기반 비접촉식 낙상 감지 통합 모니터링 대시보드" },
      { property: "og:title", content: "실시간 관제 · CSI-Guard" },
      {
        property: "og:description",
        content: "Wi-Fi CSI 기반 비접촉식 낙상 감지 통합 모니터링 대시보드",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
      { name: "twitter:title", content: "실시간 관제 · CSI-Guard" },
      {
        name: "twitter:description",
        content: "Wi-Fi CSI 기반 비접촉식 낙상 감지 통합 모니터링 대시보드",
      },
      {
        property: "og:image",
        content:
          "https://pub-bb2e103a32db4e198524a2e9ed8f35b4.r2.dev/a94111ab-a939-41d0-a4e4-d70bde7907ca/id-preview-71094c96--7fe1cc7c-e8cd-46ba-b799-db91a2bdb190.lovable.app-1783571706937.png",
      },
      {
        name: "twitter:image",
        content:
          "https://pub-bb2e103a32db4e198524a2e9ed8f35b4.r2.dev/a94111ab-a939-41d0-a4e4-d70bde7907ca/id-preview-71094c96--7fe1cc7c-e8cd-46ba-b799-db91a2bdb190.lovable.app-1783571706937.png",
      },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: () => (
    <div className="min-h-screen flex items-center justify-center bg-background text-foreground">
      <div className="text-center">
        <div className="font-mono text-6xl font-bold text-primary">404</div>
        <div className="mt-2 text-muted">Route not found</div>
      </div>
    </div>
  ),
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <Outlet />
      </AuthGate>
      <Toaster theme="light" position="top-right" />
    </QueryClientProvider>
  );
}
