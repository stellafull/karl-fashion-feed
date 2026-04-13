import { describe, expect, it } from "vitest";

import { getUserDisplayAvatarUrl, getUserDisplayLabel } from "./auth-profile";

describe("getUserDisplayLabel", () => {
  it("prefers display_name and falls back to login_name", () => {
    expect(getUserDisplayLabel({ display_name: "Feishu User", login_name: "dev-root" })).toBe(
      "Feishu User"
    );
    expect(getUserDisplayLabel({ display_name: "", login_name: "dev-root" })).toBe("dev-root");
    expect(getUserDisplayLabel(null)).toBe("Local User");
  });
});

describe("getUserDisplayAvatarUrl", () => {
  it("returns a trimmed avatar url when present", () => {
    expect(
      getUserDisplayAvatarUrl({ avatar_url: " https://example.com/avatar.png " })
    ).toBe("https://example.com/avatar.png");
  });

  it("returns null when avatar_url is empty or missing", () => {
    expect(getUserDisplayAvatarUrl({ avatar_url: "   " })).toBeNull();
    expect(getUserDisplayAvatarUrl({})).toBeNull();
  });
});
