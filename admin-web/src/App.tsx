import { useEffect, useState } from "react";
import { getToken, clearSession, sessionName, isSuperadmin } from "./auth";
import LoginPage from "./pages/LoginPage";
import DocumentsPage from "./pages/DocumentsPage";
import ConflictsPage from "./pages/ConflictsPage";
import ShopsPage from "./pages/ShopsPage";

type Route = "documents" | "conflicts" | "shops";

function currentRoute(): Route {
  const hash = window.location.hash.replace("#/", "");
  if (hash === "conflicts" || hash === "shops") return hash;
  return "documents";
}

export default function App() {
  const [route, setRoute] = useState<Route>(currentRoute());
  const [loggedIn, setLoggedIn] = useState<boolean>(getToken() !== null);

  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (!loggedIn) return <LoginPage onLogin={() => setLoggedIn(true)} />;

  const nav = (r: Route, label: string) => (
    <button
      key={r}
      onClick={() => {
        window.location.hash = `#/${r}`;
        setRoute(r);
      }}
      className={`px-3 py-1.5 rounded-md text-sm ${
        route === r ? "bg-slate-800 text-white" : "text-slate-600 hover:bg-slate-200"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="flex items-center gap-4 bg-white border-b border-slate-200 px-6 py-3">
        <span className="font-semibold text-slate-800">知识库管理</span>
        <nav className="flex gap-1">
          {nav("documents", "文档")}
          {nav("conflicts", "冲突队列")}
          {isSuperadmin() && nav("shops", "店铺")}
        </nav>
        <span className="ml-auto text-sm text-slate-500">
          {sessionName()}
          <button
            className="ml-3 text-red-500 hover:underline"
            onClick={() => {
              clearSession();
              setLoggedIn(false);
            }}
          >
            退出
          </button>
        </span>
      </header>
      <main className="max-w-5xl mx-auto p-6">
        {route === "documents" && <DocumentsPage />}
        {route === "conflicts" && <ConflictsPage />}
        {route === "shops" && <ShopsPage />}
      </main>
    </div>
  );
}
