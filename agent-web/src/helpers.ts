export interface PendingApprovalSummary {
  id: string;
  amount: number;
  sla_deadline: string | null;
  escalated: boolean;
}

export interface ConversationRow {
  id: string;
  customer_id: string;
  shop_id: string;
  channel: string;
  status: string;
  created_at: string;
  last_message_at: string | null;
  pending_approval: PendingApprovalSummary | null;
}

export interface MessageRow {
  sequence: number;
  role: string;
  content: { text?: string };
}

export interface AuditRow {
  seq: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  step: string | null;
}

export interface ApprovalRow {
  id: string;
  conversation_id: string;
  tool_name: string;
  amount: number;
  status: string;
  owner: string;
  sla_deadline: string | null;
  escalated: boolean;
}

export function timeText(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function slaText(iso: string | null): { text: string; overdue: boolean } {
  if (!iso) return { text: "无 SLA", overdue: false };
  const remain = new Date(iso).getTime() - Date.now();
  const minutes = Math.floor(Math.abs(remain) / 60000);
  const label = remain >= 0 ? `剩 ${minutes} 分钟` : `超时 ${minutes} 分钟`;
  return { text: label, overdue: remain < 0 };
}

export function statusText(status: string): string {
  return status === "transferred" ? "转人工中" : "服务中";
}
