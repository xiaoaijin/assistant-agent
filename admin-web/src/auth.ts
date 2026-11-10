const TOKEN_KEY = "admin_token";
const SHOP_KEY = "admin_shop_id";
const NAME_KEY = "admin_name";

export function saveSession(token: string, shopId: string | null, name: string) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(SHOP_KEY, shopId ?? "");
  localStorage.setItem(NAME_KEY, name);
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(SHOP_KEY);
  localStorage.removeItem(NAME_KEY);
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function sessionShopId(): string | null {
  return localStorage.getItem(SHOP_KEY) || null;
}

export function sessionName(): string {
  return localStorage.getItem(NAME_KEY) || "";
}

export function isSuperadmin(): boolean {
  return sessionShopId() === null;
}
