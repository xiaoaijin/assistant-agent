import { useEffect, useState } from "react";
import { get, post } from "../api";

interface ConflictRow {
  id: string;
  title: string;
  source_key: string;
  content_type: string;
  updated_at: string;
}

interface ChunkRow {
  seq: number;
  text: string;
  title_path: string;
}

export default function ConflictsPage() {
  const [conflicts, setConflicts] = useState<ConflictRow[]>([]);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [chunks, setChunks] = useState<ChunkRow[]>([]);

  async function reload() {
    try {
      setConflicts((await get("/api/admin/knowledge/conflicts")) as ConflictRow[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }

  useEffect(() => {
    reload();
  }, []);

  async function toggleChunks(docId: string) {
    if (expanded === docId) {
      setExpanded(null);
      return;
    }
    setChunks((await get(`/api/admin/knowledge/documents/${docId}/chunks`)) as ChunkRow[]);
    setExpanded(docId);
  }

  async function resolve(docId: string, decision: "adopt_new" | "reject_new") {
    const label = decision === "adopt_new" ? "生效新文档并让旧文档让位" : "拒绝新文档，保留旧文档";
    if (!confirm(`确认${label}？`)) return;
    try {
      await post(`/api/admin/knowledge/documents/${docId}/resolve`, { decision });
    } catch (e) {
      setError(e instanceof Error ? e.message : "裁决失败");
    }
    setExpanded(null);
    reload();
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-500">{error}</p>}
      <p className="text-sm text-slate-500">
        新上传的文档与已生效知识疑似矛盾（相似但不相同），需要人工裁决。
      </p>
      {conflicts.length === 0 && (
        <div className="bg-white rounded-lg shadow p-8 text-center text-slate-400 text-sm">
          冲突队列是空的
        </div>
      )}
      {conflicts.map((c) => (
        <div key={c.id} className="bg-white rounded-lg shadow p-4 space-y-3">
          <div className="flex items-center gap-3">
            <span className="font-medium text-slate-800">{c.title}</span>
            <span className="text-xs text-slate-400 font-mono">{c.source_key}</span>
            <span className="ml-auto text-xs text-slate-400">
              {c.updated_at.replace("T", " ").slice(0, 16)}
            </span>
          </div>
          <div className="flex gap-2">
            <button
              className="text-sm text-blue-600 hover:underline"
              onClick={() => toggleChunks(c.id)}
            >
              {expanded === c.id ? "收起新文档切片" : "查看新文档切片"}
            </button>
          </div>
          {expanded === c.id && (
            <ul className="space-y-2">
              {chunks.map((ch) => (
                <li key={ch.seq} className="bg-slate-50 border border-slate-200 rounded p-2.5 text-xs">
                  <div className="text-slate-400 mb-1">{ch.title_path || `#${ch.seq}`}</div>
                  <pre className="whitespace-pre-wrap text-slate-700 font-sans">{ch.text}</pre>
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-2 border-t border-slate-100 pt-3">
            <button
              className="bg-green-600 text-white rounded-md px-3 py-1.5 text-sm hover:bg-green-700"
              onClick={() => resolve(c.id, "adopt_new")}
            >
              采纳新文档（旧文档让位）
            </button>
            <button
              className="bg-white border border-slate-300 text-slate-600 rounded-md px-3 py-1.5 text-sm hover:bg-slate-50"
              onClick={() => resolve(c.id, "reject_new")}
            >
              拒绝新文档
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
