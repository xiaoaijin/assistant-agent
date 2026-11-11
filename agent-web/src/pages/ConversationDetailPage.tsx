import { useEffect, useState } from "react";
import { get, post } from "../api";
import { statusText, timeText, type AuditRow, type MessageRow } from "../helpers";

interface ConversationInfo {
  id: string;
  customer_id: string;
  status: string;
  created_at: string;
}

const roleLabel: Record<string, string> = {
  customer: "顾客",
  assistant: "助手",
  tool: "工具",
};

const actorLabel: Record<string, string> = {
  customer: "顾客",
  assistant: "助手",
  system: "系统",
  seat: "坐席",
};

export default function ConversationDetailPage({ convId }: { convId: string }) {
  const [conv, setConv] = useState<ConversationInfo | null>(null);
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [error, setError] = useState("");

  async function reload() {
    try {
      const info = (await get(`/api/conversations`)) as ConversationInfo[];
      setConv(info.find((c) => c.id === convId) ?? null);
      setMessages((await get(`/api/conversations/${convId}/messages`)) as MessageRow[]);
      setAudit((await get(`/api/conversations/${convId}/audit`)) as AuditRow[]);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }

  useEffect(() => {
    reload();
  }, []);

  async function handback() {
    if (!confirm("确认交还会话？助手将重新服务该顾客。")) return;
    try {
      await post(`/api/conversations/${convId}/handback`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "交还失败");
    }
    reload();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={() => {
            window.location.hash = "#/conversations";
          }}
          className="text-sm text-slate-500 hover:underline"
        >
          ← 返回列表
        </button>
        {conv && (
          <span className="text-sm text-slate-600">
            顾客 {conv.customer_id} · {statusText(conv.status)}
          </span>
        )}
        {conv?.status === "transferred" && (
          <button
            onClick={handback}
            className="ml-auto px-3 py-1.5 rounded-md text-sm bg-emerald-600 text-white hover:bg-emerald-700"
          >
            交还助手
          </button>
        )}
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}

      <section className="bg-white rounded-lg border border-slate-200 p-4">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">消息</h2>
        <div className="space-y-2">
          {messages.map((m) => (
            <div key={m.sequence} className="flex gap-2 text-sm">
              <span className="w-10 shrink-0 text-slate-400">{roleLabel[m.role] ?? m.role}</span>
              <span className="text-slate-700 whitespace-pre-wrap">
                {m.content.text ?? JSON.stringify(m.content)}
              </span>
            </div>
          ))}
          {messages.length === 0 && <p className="text-sm text-slate-400">暂无消息</p>}
        </div>
      </section>

      <section className="bg-white rounded-lg border border-slate-200 p-4">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">动作留痕</h2>
        <div className="space-y-1 text-xs text-slate-500">
          {audit.map((a) => (
            <div key={a.seq} className="flex gap-2">
              <span className="w-8 text-right text-slate-300">{a.seq}</span>
              <span className="w-12">{actorLabel[a.actor] ?? a.actor}</span>
              <span className="font-mono text-slate-600">{a.action}</span>
              {Object.keys(a.detail).length > 0 && (
                <span className="text-slate-400">{JSON.stringify(a.detail)}</span>
              )}
            </div>
          ))}
          {audit.length === 0 && <p>暂无留痕</p>}
        </div>
      </section>

      <p className="text-xs text-slate-400">创建于 {timeText(conv?.created_at ?? null)}</p>
    </div>
  );
}
