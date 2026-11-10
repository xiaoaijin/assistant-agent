import { useEffect, useState } from "react";
import { get, postForm, del } from "../api";
import { isSuperadmin, sessionShopId } from "../auth";

interface DocumentRow {
  id: string;
  shop_id: string;
  content_type: string;
  title: string;
  source_key: string;
  filename: string;
  status: string;
  updated_at: string;
}

interface ChunkRow {
  seq: number;
  text: string;
  title_path: string;
  page: number | null;
  char_count: number;
}

interface Shop {
  id: string;
  name: string;
}

const STATUS_LABELS: Record<string, string> = {
  active: "生效中",
  pending_review: "待审冲突",
  rejected: "已拒绝",
  superseded: "已让位",
};

const STATUS_STYLES: Record<string, string> = {
  active: "bg-green-100 text-green-700",
  pending_review: "bg-amber-100 text-amber-700",
  rejected: "bg-slate-200 text-slate-500",
  superseded: "bg-slate-200 text-slate-500",
};

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [chunks, setChunks] = useState<ChunkRow[]>([]);
  const [shops, setShops] = useState<Shop[]>([]);

  async function reload() {
    try {
      const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
      setDocs((await get(`/api/admin/knowledge/documents${qs}`)) as DocumentRow[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }

  useEffect(() => {
    reload();
    if (isSuperadmin()) {
      get("/api/admin/shops")
        .then((rows) => setShops(rows as Shop[]))
        .catch(() => setShops([]));
    }
  }, [statusFilter]);

  async function toggleChunks(docId: string) {
    if (expanded === docId) {
      setExpanded(null);
      return;
    }
    setChunks((await get(`/api/admin/knowledge/documents/${docId}/chunks`)) as ChunkRow[]);
    setExpanded(docId);
  }

  async function removeDoc(docId: string) {
    if (!confirm("确认删除该文档及其全部切片？")) return;
    try {
      await del(`/api/admin/knowledge/documents/${docId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
    reload();
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-500">{error}</p>}
      <UploadCard shops={shops} onDone={reload} />
      <div className="flex gap-2 items-center">
        <span className="text-sm text-slate-500">状态：</span>
        {["", "active", "pending_review", "rejected", "superseded"].map((s) => (
          <button
            key={s || "all"}
            onClick={() => setStatusFilter(s)}
            className={`px-2.5 py-1 rounded text-xs ${
              statusFilter === s ? "bg-slate-800 text-white" : "bg-white border border-slate-300 text-slate-600"
            }`}
          >
            {s === "" ? "全部" : STATUS_LABELS[s]}
          </button>
        ))}
      </div>
      <table className="w-full bg-white rounded-lg shadow text-sm">
        <thead>
          <tr className="text-left text-slate-500 border-b border-slate-200">
            <th className="px-4 py-2.5">标题</th>
            <th className="px-4 py-2.5">类型</th>
            <th className="px-4 py-2.5">source_key</th>
            <th className="px-4 py-2.5">状态</th>
            <th className="px-4 py-2.5">更新时间</th>
            <th className="px-4 py-2.5"></th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <DocumentRowView
              key={d.id}
              doc={d}
              expanded={expanded === d.id}
              chunks={expanded === d.id ? chunks : []}
              onToggle={() => toggleChunks(d.id)}
              onDelete={() => removeDoc(d.id)}
            />
          ))}
          {docs.length === 0 && (
            <tr>
              <td colSpan={6} className="px-4 py-6 text-center text-slate-400">
                暂无文档
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function DocumentRowView(props: {
  doc: DocumentRow;
  expanded: boolean;
  chunks: ChunkRow[];
  onToggle: () => void;
  onDelete: () => void;
}) {
  const { doc, expanded, chunks, onToggle, onDelete } = props;
  return (
    <>
      <tr className="border-b border-slate-100">
        <td className="px-4 py-2.5 font-medium text-slate-800">{doc.title}</td>
        <td className="px-4 py-2.5 text-slate-600">{doc.content_type}</td>
        <td className="px-4 py-2.5 text-slate-500 font-mono text-xs">{doc.source_key}</td>
        <td className="px-4 py-2.5">
          <span className={`px-2 py-0.5 rounded text-xs ${STATUS_STYLES[doc.status] ?? ""}`}>
            {STATUS_LABELS[doc.status] ?? doc.status}
          </span>
        </td>
        <td className="px-4 py-2.5 text-slate-500 text-xs">{doc.updated_at.replace("T", " ").slice(0, 16)}</td>
        <td className="px-4 py-2.5 text-right space-x-2">
          <button className="text-blue-600 hover:underline" onClick={onToggle}>
            {expanded ? "收起切片" : "查看切片"}
          </button>
          <button className="text-red-500 hover:underline" onClick={onDelete}>
            删除
          </button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6} className="bg-slate-50 px-6 py-3">
            {chunks.length === 0 && <p className="text-xs text-slate-400">无切片</p>}
            <ul className="space-y-2">
              {chunks.map((c) => (
                <li key={c.seq} className="bg-white border border-slate-200 rounded p-2.5 text-xs">
                  <div className="text-slate-400 mb-1">
                    #{c.seq} · {c.title_path || "（无标题路径）"}
                    {c.page !== null && ` · 第 ${c.page} 页`} · {c.char_count} 字
                  </div>
                  <pre className="whitespace-pre-wrap text-slate-700 font-sans">{c.text}</pre>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}

function UploadCard(props: { shops: Shop[]; onDone: () => void }) {
  const [shopId, setShopId] = useState(sessionShopId() ?? "");
  const [sourceKey, setSourceKey] = useState("");
  const [title, setTitle] = useState("");
  const [contentType, setContentType] = useState("policy");
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setMessage("");
    try {
      const form = new FormData();
      form.set("shop_id", shopId);
      form.set("source_key", sourceKey);
      form.set("title", title);
      form.set("content_type", contentType);
      if (file) {
        form.set("file", file);
      } else {
        form.set("file", new File([pasted], "pasted.md", { type: "text/markdown" }));
      }
      const result = (await postForm("/api/admin/knowledge/documents", form)) as { status: string };
      setMessage(
        result.status === "active"
          ? "已生效"
          : result.status === "pending_review"
            ? "发现疑似冲突，已进冲突队列"
            : result.status === "duplicate"
              ? "内容与已有文档重复，未入库"
              : `状态：${result.status}`,
      );
      setSourceKey("");
      setTitle("");
      setFile(null);
      setPasted("");
      props.onDone();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = Boolean(shopId && sourceKey && title && (file || pasted.trim()));

  return (
    <div className="bg-white rounded-lg shadow p-4 space-y-3">
      <h2 className="text-sm font-semibold text-slate-700">新建文档</h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {isSuperadmin() && (
          <select
            className="border border-slate-300 rounded-md px-2 py-1.5 text-sm"
            value={shopId}
            onChange={(e) => setShopId(e.target.value)}
          >
            <option value="">选择店铺…</option>
            {props.shops.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        )}
        <input
          className="border border-slate-300 rounded-md px-2 py-1.5 text-sm"
          placeholder="source_key（如 policy_v2，重复即更新）"
          value={sourceKey}
          onChange={(e) => setSourceKey(e.target.value)}
        />
        <input
          className="border border-slate-300 rounded-md px-2 py-1.5 text-sm"
          placeholder="标题"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <select
          className="border border-slate-300 rounded-md px-2 py-1.5 text-sm"
          value={contentType}
          onChange={(e) => setContentType(e.target.value)}
        >
          <option value="policy">售后政策</option>
          <option value="faq">FAQ</option>
        </select>
      </div>
      <div className="flex gap-3 items-center">
        <input
          type="file"
          accept=".md,.markdown,.txt,.pdf,.yaml,.yml"
          className="text-sm"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <span className="text-xs text-slate-400">或</span>
      </div>
      <textarea
        className="w-full border border-slate-300 rounded-md px-2 py-1.5 text-sm font-mono"
        rows={4}
        placeholder="粘贴 markdown / 纯文本内容（选了文件则忽略）"
        value={pasted}
        onChange={(e) => setPasted(e.target.value)}
      />
      <div className="flex items-center gap-3">
        <button
          className="bg-slate-800 text-white rounded-md px-4 py-1.5 text-sm disabled:opacity-50"
          disabled={busy || !canSubmit}
          onClick={submit}
        >
          上传并索引
        </button>
        {message && <span className="text-xs text-slate-600">{message}</span>}
      </div>
      <p className="text-xs text-slate-400">支持 markdown / 纯文本 / FAQ YAML / 文字版 PDF（扫描件暂不支持）</p>
    </div>
  );
}
