import { useEffect, useState } from "react";
import ConversationsPage from "./pages/ConversationsPage";
import ConversationDetailPage from "./pages/ConversationDetailPage";
import ApprovalsPage from "./pages/ApprovalsPage";

type Route = { page: "conversations" | "approvals" } | { page: "detail"; convId: string };

function currentRoute(): Route {
  const hash = window.location.hash.replace("#/", "");
  if (hash.startsWith("conversation/")) {
    return { page: "detail", convId: hash.slice("conversation/".length) };
  }
  if (hash === "approvals") return { page: "approvals" };
  return { page: "conversations" };
}

export default function App() {
  const [route, setRoute] = useState<Route>(currentRoute());

  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const nav = (hash: string, label: string, active: boolean) => (
    <button
      onClick={() => {
        window.location.hash = hash;
        setRoute(currentRoute());
      }}
      className={`px-3 py-1.5 rounded-md text-sm ${
        active ? "bg-slate-800 text-white" : "text-slate-600 hover:bg-slate-200"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="flex items-center gap-4 bg-white border-b border-slate-200 px-6 py-3">
        <span className="font-semibold text-slate-800">坐席工作台</span>
        <nav className="flex gap-1">
          {nav("#/conversations", "会话", route.page !== "approvals")}
          {nav("#/approvals", "审批队列", route.page === "approvals")}
        </nav>
      </header>
      <main className="max-w-5xl mx-auto p-6">
        {route.page === "conversations" && <ConversationsPage />}
        {route.page === "approvals" && <ApprovalsPage />}
        {route.page === "detail" && <ConversationDetailPage convId={route.convId} />}
      </main>
    </div>
  );
}
