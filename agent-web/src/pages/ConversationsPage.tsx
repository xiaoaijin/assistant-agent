import { useEffect, useState } from "react";
import { get } from "../api";
import { slaText, statusText, timeText, type ConversationRow } from "../helpers";

export default function ConversationsPage() {
  const [rows, setRows] = useState<ConversationRow[]>([]);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  async function reload(statusFilter: string) {
    try {
      const query = statusFilter ? `?status=${statusFilter}` : "";
      setRows((await get(`/api/conversations${query}`)) as ConversationRow[]);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
    setLoaded(true);
  }

  useEffect(() => {
    reload(status);
  }, [status]);

  const filterBtn = (value: string, label: string) => (
    <button
      onClick={() => setStatus(value)}
      className={`px-3 py-1.5 rounded-md text-sm ${
        status === value ? "bg-slate-800 text-white" : "text-slate-600 hover:bg-slate-200"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="flex gap-1">
          {filterBtn("", "全部")}
          {filterBtn("ai_serving", "服务中")}
          {filterBtn("transferred", "转人工中")}
        </div>
        <button
          onClick={() => reload(status)}
          className="ml-auto px-3 py-1.5 rounded-md text-sm bg-white border border-slate-300 hover:bg-slate-50"
        >
          刷新
        </button>
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}
      {loaded && rows.length === 0 && !error && (
        <p className="text-sm text-slate-500">暂无会话</p>
      )}
      <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-100">
        {rows.map((r) => {
          const sla = r.pending_approval ? slaText(r.pending_approval.sla_deadline) : null;
          return (
            <button
              key={r.id}
              onClick={() => {
                window.location.hash = `#/conversation/${r.id}`;
              }}
              className="w-full text-left px-4 py-3 hover:bg-slate-50"
            >
              <div className="flex items-center gap-3">
                <span className="font-medium text-slate-800">顾客 {r.customer_id}</span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    r.status === "transferred"
                      ? "bg-orange-100 text-orange-700"
                      : "bg-emerald-100 text-emerald-700"
                  }`}
                >
                  {statusText(r.status)}
                </span>
                {r.pending_approval && (
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full ${
                      r.pending_approval.escalated || (sla && sla.overdue)
                        ? "bg-red-100 text-red-700"
                        : "bg-sky-100 text-sky-700"
                    }`}
                  >
                    待审批 {r.pending_approval.id} · ¥{r.pending_approval.amount}
                    {sla ? ` · SLA ${sla.text}` : ""}
                    {r.pending_approval.escalated ? " · 已升级" : ""}
                  </span>
                )}
                <span className="ml-auto text-xs text-slate-400">
                  最近消息 {timeText(r.last_message_at)}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
