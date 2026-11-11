import { useEffect, useState } from "react";
import { get, post } from "../api";
import { slaText, type ApprovalRow } from "../helpers";

export default function ApprovalsPage() {
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  async function reload() {
    try {
      const all = (await get("/api/approvals")) as ApprovalRow[];
      const pending = all.filter((r) => r.status === "pending");
      pending.sort((a, b) => {
        if (a.escalated !== b.escalated) return a.escalated ? -1 : 1;
        return (a.sla_deadline ?? "").localeCompare(b.sla_deadline ?? "");
      });
      setRows(pending);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
    setLoaded(true);
  }

  useEffect(() => {
    reload();
  }, []);

  async function sweep() {
    try {
      await post("/api/approvals/sweep");
    } catch (e) {
      setError(e instanceof Error ? e.message : "清扫失败");
    }
    reload();
  }

  async function decide(id: string, action: "approve" | "reject") {
    const label = action === "approve" ? "批准" : "拒绝";
    if (!confirm(`确认${label} ${id}？`)) return;
    try {
      await post(`/api/approvals/${id}/${action}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : `${label}失败`);
    }
    reload();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <p className="text-sm text-slate-500">超时未决的待审批项会自动升级置顶（红色）。</p>
        <button
          onClick={sweep}
          className="ml-auto px-3 py-1.5 rounded-md text-sm bg-white border border-slate-300 hover:bg-slate-50"
        >
          清扫超时项
        </button>
        <button
          onClick={reload}
          className="px-3 py-1.5 rounded-md text-sm bg-white border border-slate-300 hover:bg-slate-50"
        >
          刷新
        </button>
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}
      {loaded && rows.length === 0 && !error && (
        <p className="text-sm text-slate-500">暂无待审批项</p>
      )}
      <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-100">
        {rows.map((r) => {
          const sla = slaText(r.sla_deadline);
          return (
            <div key={r.id} className="px-4 py-3">
              <div className="flex items-center gap-3">
                <span className="font-mono text-sm text-slate-800">{r.id}</span>
                <span className="text-sm text-slate-600">退款 ¥{r.amount}</span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    r.escalated ? "bg-red-100 text-red-700" : sla.overdue ? "bg-orange-100 text-orange-700" : "bg-sky-100 text-sky-700"
                  }`}
                >
                  {r.escalated ? "已超时升级" : `SLA ${sla.text}`}
                </span>
                <button
                  onClick={() => window.location.assign(`#/conversation/${r.conversation_id}`)}
                  className="text-xs text-slate-400 hover:underline"
                >
                  查看会话
                </button>
                <span className="ml-auto flex gap-2">
                  <button
                    onClick={() => decide(r.id, "approve")}
                    className="px-3 py-1.5 rounded-md text-sm bg-emerald-600 text-white hover:bg-emerald-700"
                  >
                    批准
                  </button>
                  <button
                    onClick={() => decide(r.id, "reject")}
                    className="px-3 py-1.5 rounded-md text-sm bg-white border border-slate-300 text-slate-700 hover:bg-slate-50"
                  >
                    拒绝
                  </button>
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
