import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";

export const Route = createFileRoute("/login")({
  head: () => ({ meta: [{ title: "로그인 · CSI-Guard" }] }),
  component: LoginPage,
});

function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await login(email, password);
      toast.success("환영합니다");
      navigate({ to: "/" });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "서버에 연결할 수 없습니다");
    } finally {
      setBusy(false);
    }
  };

  const fill = (e: string) => {
    setEmail(e);
    setPassword("demo");
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="font-mono font-bold text-2xl tracking-tighter">CSI-GUARD</div>
          <p className="text-xs font-mono text-muted uppercase tracking-widest mt-1">
            Fall Detection Console
          </p>
        </div>
        <form
          onSubmit={submit}
          className="bg-surface border border-border rounded-lg p-6 space-y-4"
        >
          <div>
            <label className="text-[10px] font-mono uppercase text-muted">이메일</label>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              required
              autoComplete="email"
              className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-muted"
            />
          </div>
          <div>
            <label className="text-[10px] font-mono uppercase text-muted">비밀번호</label>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              required
              autoComplete="current-password"
              className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-muted"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="w-full py-2.5 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold tracking-widest hover:brightness-110 disabled:opacity-60"
          >
            {busy ? "로그인 중…" : "로그인"}
          </button>
          <div className="text-center text-xs text-muted">
            계정이 없으신가요?{" "}
            <Link to="/signup" className="text-primary font-medium">
              회원가입
            </Link>
          </div>
        </form>

        <div className="mt-4 bg-surface border border-dashed border-border rounded p-3 text-[10px] font-mono text-muted space-y-1">
          <div className="uppercase tracking-widest text-foreground/70">
            데모 계정 (클릭시 자동입력 · 시드 DB 기준)
          </div>
          <button
            type="button"
            onClick={() => fill("root@demo.io")}
            className="block hover:text-foreground"
          >
            root@demo.io · FACILITY Root
          </button>
          <button
            type="button"
            onClick={() => fill("member@demo.io")}
            className="block hover:text-foreground"
          >
            member@demo.io · FACILITY Member
          </button>
          <button
            type="button"
            onClick={() => fill("home@demo.io")}
            className="block hover:text-foreground"
          >
            home@demo.io · HOME
          </button>
          <div className="text-muted/70">비밀번호: demo</div>
        </div>
      </div>
    </div>
  );
}
