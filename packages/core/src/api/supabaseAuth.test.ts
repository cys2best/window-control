import { authIdentityFromToken, signInWithPassword, signUpWithPassword, isJwtExpired } from "./supabaseAuth";

function jwt(payload: object): string {
  return `header.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.signature`;
}

describe("supabaseAuth", () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it("returns access_token on successful sign-in", async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ access_token: "jwt-123" }),
    });
    const result = await signInWithPassword(
      "https://project.supabase.co", "anon-key", "a@example.com", "pw"
    );
    expect(result).toEqual({ access_token: "jwt-123" });
    expect(global.fetch).toHaveBeenCalledWith(
      "https://project.supabase.co/auth/v1/token?grant_type=password",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("returns an error message on rejected sign-in", async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      json: async () => ({ error_description: "Invalid login credentials" }),
    });
    const result = await signInWithPassword(
      "https://project.supabase.co", "anon-key", "a@example.com", "wrong"
    );
    expect(result).toEqual({ error: "Invalid login credentials" });
  });

  it("returns access_token on successful sign-up", async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ access_token: "jwt-456" }),
    });
    const result = await signUpWithPassword(
      "https://project.supabase.co", "anon-key", "new@example.com", "pw"
    );
    expect(result).toEqual({ access_token: "jwt-456" });
    expect(global.fetch).toHaveBeenCalledWith(
      "https://project.supabase.co/auth/v1/signup",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("isJwtExpired returns true for null or empty", () => {
    expect(isJwtExpired(null)).toBe(true);
    expect(isJwtExpired("")).toBe(true);
  });

  it("isJwtExpired returns false for non-jwt strings without exp", () => {
    expect(isJwtExpired("plain-token")).toBe(false);
  });

  it("isJwtExpired correctly identifies expired vs valid tokens", () => {
    const expiredPayload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) - 60 })).toString("base64url");
    const expiredToken = `eyJhbGciOiJIUzI1NiJ9.${expiredPayload}.signature`;
    expect(isJwtExpired(expiredToken)).toBe(true);

    const validPayload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    const validToken = `eyJhbGciOiJIUzI1NiJ9.${validPayload}.signature`;
    expect(isJwtExpired(validToken)).toBe(false);
  });

  test("authIdentityFromToken reads real Supabase claims", () => {
    const token = jwt({
      sub: "user-1",
      email: "kai@example.com",
      user_metadata: { display_name: "Kai" },
    });

    expect(authIdentityFromToken(token)).toEqual({
      id: "user-1", email: "kai@example.com", displayName: "Kai", initials: "K",
    });
  });

  test("identity falls back to the email local-part, never a sample name", () => {
    const token = jwt({ sub: "user-2", email: "owner@example.com" });

    expect(authIdentityFromToken(token)).toEqual({
      id: "user-2", email: "owner@example.com", displayName: "owner", initials: "O",
    });
  });
});
