export type AuthResult = { access_token: string } | { error: string };

async function _authRequest(
  supabaseUrl: string,
  anonKey: string,
  path: string,
  email: string,
  password: string
): Promise<AuthResult> {
  const r = await fetch(`${supabaseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: anonKey },
    body: JSON.stringify({ email, password }),
  });
  const body = await r.json();
  if (r.ok && body.access_token) return { access_token: body.access_token };
  return { error: body.error_description ?? body.msg ?? "Authentication failed" };
}

export function signInWithPassword(
  supabaseUrl: string, anonKey: string, email: string, password: string
): Promise<AuthResult> {
  return _authRequest(supabaseUrl, anonKey, "/auth/v1/token?grant_type=password", email, password);
}

export function signUpWithPassword(
  supabaseUrl: string, anonKey: string, email: string, password: string
): Promise<AuthResult> {
  return _authRequest(supabaseUrl, anonKey, "/auth/v1/signup", email, password);
}
