import { useEffect, useState } from "react";
import { get, post } from "../api";

interface Shop {
  id: string;
  name: string;
}

export default function ShopsPage() {
  const [shops, setShops] = useState<Shop[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  async function reload() {
    try {
      setShops((await get("/api/admin/shops")) as Shop[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }

  useEffect(() => {
    reload();
  }, []);

  async function create() {
    try {
      await post("/api/admin/shops", { name });
      setName("");
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    }
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-500">{error}</p>}
      <div className="bg-white rounded-lg shadow p-4 flex gap-3 items-center">
        <input
          className="border border-slate-300 rounded-md px-2 py-1.5 text-sm flex-1"
          placeholder="店铺名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          className="bg-slate-800 text-white rounded-md px-4 py-1.5 text-sm disabled:opacity-50"
          disabled={!name.trim()}
          onClick={create}
        >
          新建店铺
        </button>
      </div>
      <table className="w-full bg-white rounded-lg shadow text-sm">
        <thead>
          <tr className="text-left text-slate-500 border-b border-slate-200">
            <th className="px-4 py-2.5">名称</th>
            <th className="px-4 py-2.5">ID</th>
          </tr>
        </thead>
        <tbody>
          {shops.map((s) => (
            <tr key={s.id} className="border-b border-slate-100">
              <td className="px-4 py-2.5 font-medium text-slate-800">{s.name}</td>
              <td className="px-4 py-2.5 text-slate-400 font-mono text-xs">{s.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-slate-400">
        管理员账号暂由后端直接建表管理（admin_users 表），账号管理界面后续版本提供。
      </p>
    </div>
  );
}
