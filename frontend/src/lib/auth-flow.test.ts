import { describe, expect, it } from "vitest";

import {
  buildAppPath,
  consumeAuthCompleteToken,
  detectFeishuClientAuthMode,
  hasFeishuH5Sdk,
  isFeishuClientRequestAuthCodeAvailable,
  isFeishuClientRequestAccessAvailable,
  isLikelyFeishuClient,
  shouldAutoStartFeishuClientLogin,
  waitForFeishuH5SdkReady,
} from "./auth-flow";

describe("isFeishuClientRequestAccessAvailable", () => {
  it("returns true only when requestAccess bridge and app id are present", () => {
    expect(
      isFeishuClientRequestAccessAvailable(
        { tt: { requestAccess: () => undefined } },
        "cli_test"
      )
    ).toBe(true);
    expect(isFeishuClientRequestAccessAvailable({ tt: {} }, "cli_test")).toBe(false);
    expect(
      isFeishuClientRequestAccessAvailable(
        { tt: { requestAccess: () => undefined } },
        ""
      )
    ).toBe(false);
  });
});

describe("detectFeishuClientAuthMode", () => {
  it("prefers requestAccess and falls back to requestAuthCode", () => {
    expect(
      detectFeishuClientAuthMode(
        { tt: { requestAccess: () => undefined, requestAuthCode: () => undefined } },
        "cli_test"
      )
    ).toBe("request_access");
    expect(
      detectFeishuClientAuthMode(
        { tt: { requestAuthCode: () => undefined } },
        "cli_test"
      )
    ).toBe("request_auth_code");
    expect(detectFeishuClientAuthMode({ tt: {} }, "cli_test")).toBeNull();
  });
});

describe("isFeishuClientRequestAuthCodeAvailable", () => {
  it("returns true only when requestAuthCode bridge and app id are present", () => {
    expect(
      isFeishuClientRequestAuthCodeAvailable(
        { tt: { requestAuthCode: () => undefined } },
        "cli_test"
      )
    ).toBe(true);
    expect(isFeishuClientRequestAuthCodeAvailable({ tt: {} }, "cli_test")).toBe(false);
    expect(
      isFeishuClientRequestAuthCodeAvailable(
        { tt: { requestAuthCode: () => undefined } },
        ""
      )
    ).toBe(false);
  });
});

describe("isLikelyFeishuClient", () => {
  it("detects Feishu or Lark user agents", () => {
    expect(isLikelyFeishuClient({ navigator: { userAgent: "Mozilla/5.0 Feishu" } })).toBe(true);
    expect(isLikelyFeishuClient({ navigator: { userAgent: "Mozilla/5.0 Lark" } })).toBe(true);
    expect(isLikelyFeishuClient({ navigator: { userAgent: "Mozilla/5.0 Safari" } })).toBe(false);
  });
});

describe("Feishu H5 SDK helpers", () => {
  it("detects whether h5sdk is present", () => {
    expect(hasFeishuH5Sdk({ h5sdk: { ready: () => undefined } })).toBe(true);
    expect(hasFeishuH5Sdk({ h5sdk: {} })).toBe(false);
  });

  it("waits for h5sdk.ready to resolve", async () => {
    await expect(
      waitForFeishuH5SdkReady({
        h5sdk: {
          ready: (callback) => callback(),
          error: () => undefined,
        },
      })
    ).resolves.toBeUndefined();
  });

  it("rejects when h5sdk.error fires", async () => {
    await expect(
      waitForFeishuH5SdkReady({
        h5sdk: {
          ready: () => undefined,
          error: (callback) => callback(new Error("sdk failed")),
        },
      })
    ).rejects.toThrow("sdk failed");
  });
});

describe("consumeAuthCompleteToken", () => {
  it("extracts the token from the auth-complete route and returns the cleanup path", () => {
    expect(
      consumeAuthCompleteToken(
        new URL("https://frontend.example.com/auth/complete?token=test-token")
      )
    ).toEqual({ token: "test-token", cleanupPath: "/discover" });
  });

  it("returns null for unrelated routes or when token is missing", () => {
    expect(
      consumeAuthCompleteToken(new URL("https://frontend.example.com/discover?token=test"))
    ).toBeNull();
    expect(
      consumeAuthCompleteToken(new URL("https://frontend.example.com/auth/complete"))
    ).toBeNull();
  });
});

describe("buildAppPath", () => {
  it("normalizes a base path with a leading slash", () => {
    expect(buildAppPath("/discover", "/console/")).toBe("/console/discover");
    expect(buildAppPath("discover", "/")).toBe("/discover");
  });
});

describe("shouldAutoStartFeishuClientLogin", () => {
  it("auto-starts only for unauthenticated Feishu-client login screens", () => {
    expect(
      shouldAutoStartFeishuClientLogin({
        locationPath: "/",
        isHydrated: true,
        supportsClientLogin: true,
        isAuthenticated: false,
        isSubmitting: false,
        hasError: false,
      })
    ).toBe(true);
    expect(
      shouldAutoStartFeishuClientLogin({
        locationPath: "/__dev/login",
        isHydrated: true,
        supportsClientLogin: true,
        isAuthenticated: false,
        isSubmitting: false,
        hasError: false,
      })
    ).toBe(false);
    expect(
      shouldAutoStartFeishuClientLogin({
        locationPath: "/",
        isHydrated: true,
        supportsClientLogin: false,
        isAuthenticated: false,
        isSubmitting: false,
        hasError: false,
      })
    ).toBe(false);
    expect(
      shouldAutoStartFeishuClientLogin({
        locationPath: "/",
        isHydrated: true,
        supportsClientLogin: true,
        isAuthenticated: false,
        isSubmitting: true,
        hasError: false,
      })
    ).toBe(false);
    expect(
      shouldAutoStartFeishuClientLogin({
        locationPath: "/",
        isHydrated: true,
        supportsClientLogin: true,
        isAuthenticated: false,
        isSubmitting: false,
        hasError: true,
      })
    ).toBe(false);
    expect(
      shouldAutoStartFeishuClientLogin({
        locationPath: "/",
        isHydrated: false,
        supportsClientLogin: true,
        isAuthenticated: false,
        isSubmitting: false,
        hasError: false,
      })
    ).toBe(false);
  });
});
