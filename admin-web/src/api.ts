import { getToken, clearSession } from "./auth";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const resp = await fetch(path, { ...init, headers });
  if (resp.status === 401) {
    clearSession();
    window.location.hash = "#/login";
    throw new ApiError(401, "登录已过期，请重新登录");
  }
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new ApiError(resp.status, detail?.detail ?? `请求失败（${resp.status}）`);
  }
  return resp.json();
}

export function get(path: string): Promise<unknown> {
  return request(path);
}

export function post(path: string, body?: unknown): Promise<unknown> {
  return request(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
}

export function postForm(path: string, form: FormData): Promise<unknown> {
  return request(path, { method: "POST", body: form });
}

export function del(path: string): Promise<unknown> {
  return request(path, { method: "DELETE" });
}
