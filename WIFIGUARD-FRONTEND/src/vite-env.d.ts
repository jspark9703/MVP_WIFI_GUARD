/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 서비스 API 베이스 URL (예: http://127.0.0.1:8000). 없으면 client.ts 의 기본값. */
  readonly VITE_API_BASE_URL?: string;
  /** /ws/live 주소. 실시간 경로는 아직 미구현 — 설정해도 VITE_ENABLE_LIVE=1 일 때만 사용. */
  readonly VITE_WS_URL?: string;
  /** "1" 이면 mock-store 시뮬레이션(setInterval tick)과 목업 데이터 훅을 사용한다. */
  readonly VITE_USE_MOCK?: string;
  /** "1" 이면 레거시 실시간/시리얼 경로(BackendDetectionBridge, ConnectionPanel)를 켠다. 기본 꺼짐. */
  readonly VITE_ENABLE_LIVE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
