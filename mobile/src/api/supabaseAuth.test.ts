import { signInWithPassword, signUpWithPassword } from "./supabaseAuth";

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
});
