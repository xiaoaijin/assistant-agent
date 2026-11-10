import { useState } from "react";
import { post } from "../api";
import { saveSession } from "../auth";

interface Props {
  onLogin: () => void;
}

interface TokenClaims {
  sub: string;
  shop_id: string | null;
}

function decodeClaims(token: string): TokenClaims {
  const body = token.split(".")[0];
  return JSON.parse(atob(body.replace(/-/g, "+").replace(/_/g, "/")));
}

export default function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const result = (await post("/api/admin/login", { username, password })) as { token: string };
      const claims = decodeClaims(result.token);
      saveSession(result.token, claims.shop_id, claims.sub);
      window.location.hash = "#/documents";
      onLogin();
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100">
      <form
        className="bg-white p-8 rounded-lg shadow w-80 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <h1 className="text-lg font-semibold text-slate-800">知识库管理 · 登录</h1>
        <input
          className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm"
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm"
          type="password"
          placeholder="密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="text-sm text-red-500">{error}</p>}
        <button
          className="w-full bg-slate-800 text-white rounded-md py-2 text-sm disabled:opacity-50"
          disabled={busy || !username || !password}
        >
          登录
        </button>
      </form>
    </div>
  );
}
