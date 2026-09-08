export type AuthResult =
  | { access_token: string }
  | { error: string }
  | { needs_confirmation: true; message: string };

export type AuthIdentity = {
  id: string;
  email: string;
  displayName: string;
  initials: string;
};

export type SignUpOptions = {
  redirectTo?: string;
  metadata?: Record<string, string>;
};

async function _authRequest(
  supabaseUrl: string,
  anonKey: string,
  path: string,
  email: string,
  password: string,
  options: SignUpOptions = {}
): Promise<AuthResult> {
  const payload: any = { email, password };
  if (options.metadata) payload.data = options.metadata;
  if (options.redirectTo) {
    payload.options = { emailRedirectTo: options.redirectTo };
  }
  const r = await fetch(`${supabaseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: anonKey },
    body: JSON.stringify(payload),
  });
  const body = await r.json();
  if (r.ok) {
    if (body.access_token) return { access_token: body.access_token };
    if (body.id || body.user) {
      return {
        needs_confirmation: true,
        message: "Confirmation email sent. Please check your inbox.",
      };
    }
  }
  return { error: body.error_description ?? body.msg ?? "Authentication failed" };
}

export function signInWithPassword(
  supabaseUrl: string, anonKey: string, email: string, password: string
): Promise<AuthResult> {
  return _authRequest(supabaseUrl, anonKey, "/auth/v1/token?grant_type=password", email, password);
}

export function signUpWithPassword(
  supabaseUrl: string, anonKey: string, email: string, password: string, options: SignUpOptions = {}
): Promise<AuthResult> {
  const query = options.redirectTo ? `?redirect_to=${encodeURIComponent(options.redirectTo)}` : "";
  return _authRequest(supabaseUrl, anonKey, `/auth/v1/signup${query}`, email, password, options);
}


export function decodeBase64Url(str: string): string {
  let output = str.replace(/-/g, "+").replace(/_/g, "/");
  switch (output.length % 4) {
    case 0:
      break;
    case 2:
      output += "==";
      break;
    case 3:
      output += "=";
      break;
    default:
      return "";
  }
  if (typeof atob === "function") {
    return atob(output);
  }
  if (typeof Buffer !== "undefined") {
    return Buffer.from(output, "base64").toString("binary");
  }
  return "";
}

export function authIdentityFromToken(token: string | null): AuthIdentity | null {
  if (!token) return null;
  try {
    const payload = JSON.parse(decodeBase64Url(token.split(".")[1]));
    const email = typeof payload.email === "string" ? payload.email : "";
    const metadata = payload.user_metadata && typeof payload.user_metadata === "object"
      ? payload.user_metadata
      : {};
    const displayName = metadata.display_name || metadata.full_name || metadata.name || email.split("@")[0] || payload.sub;
    const initials = String(displayName).trim().split(/\s+/).slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "").join("");
    return { id: String(payload.sub), email, displayName: String(displayName), initials };
  } catch {
    return null;
  }
}

export function isJwtExpired(token: string | null): boolean {
  if (!token) return true;
  try {
    const parts = token.split(".");
    if (parts.length < 2) return false;
    const decoded = decodeBase64Url(parts[1]);
    if (!decoded) return false;
    const payload = JSON.parse(decoded);
    if (typeof payload?.exp === "number") {
      return Date.now() / 1000 >= payload.exp - 10;
    }
    return false;
  } catch {
    return false;
  }
}
