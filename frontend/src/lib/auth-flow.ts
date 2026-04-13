export interface FeishuRequestAccessBridge {
  requestAccess?: (options: {
    scopeList: string[];
    appID: string;
    success?: (payload: { code?: string }) => void;
    fail?: (error: unknown) => void;
  }) => void;
  requestAuthCode?: (options: {
    appId: string;
    success?: (payload: { code?: string }) => void;
    fail?: (error: unknown) => void;
  }) => void;
}

export interface FeishuH5SdkBridge {
  ready?: (callback: () => void) => void;
  error?: (callback: (error: unknown) => void) => void;
}

export interface FeishuWindowLike {
  tt?: FeishuRequestAccessBridge;
  h5sdk?: FeishuH5SdkBridge;
  navigator?: {
    userAgent?: string;
  };
}

export type FeishuClientAuthMode = "request_access" | "request_auth_code";

export function normalizeBasePath(basePath: string) {
  if (!basePath || basePath === "/") {
    return "";
  }
  return basePath.endsWith("/") ? basePath.slice(0, -1) : basePath;
}

export function buildAppPath(path: string, basePath = import.meta.env.BASE_URL || "/") {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${normalizeBasePath(basePath)}${normalizedPath}` || normalizedPath;
}

export function isFeishuClientRequestAccessAvailable(
  target: FeishuWindowLike,
  appId: string | undefined | null
) {
  return Boolean(appId && target.tt && typeof target.tt.requestAccess === "function");
}

export function isFeishuClientRequestAuthCodeAvailable(
  target: FeishuWindowLike,
  appId: string | undefined | null
) {
  return Boolean(appId && target.tt && typeof target.tt.requestAuthCode === "function");
}

export function detectFeishuClientAuthMode(
  target: FeishuWindowLike,
  appId: string | undefined | null
): FeishuClientAuthMode | null {
  if (isFeishuClientRequestAccessAvailable(target, appId)) {
    return "request_access";
  }
  if (isFeishuClientRequestAuthCodeAvailable(target, appId)) {
    return "request_auth_code";
  }
  return null;
}

export function isLikelyFeishuClient(target: FeishuWindowLike) {
  const userAgent = target.navigator?.userAgent?.trim() || "";
  if (!userAgent) {
    return false;
  }
  return /feishu|lark/i.test(userAgent);
}

export function hasFeishuH5Sdk(target: FeishuWindowLike) {
  return Boolean(target.h5sdk && typeof target.h5sdk.ready === "function");
}

export async function waitForFeishuH5SdkReady(
  target: FeishuWindowLike,
  timeoutMs = 5000
) {
  const sdk = target.h5sdk;
  if (!sdk || typeof sdk.ready !== "function") {
    throw new Error("飞书 H5 SDK 未注入。");
  }
  const ready = sdk.ready;

  return await new Promise<void>((resolve, reject) => {
    let settled = false;
    const timeoutId = globalThis.setTimeout(() => {
      if (settled) {
        return;
      }
      settled = true;
      reject(new Error("等待飞书 H5 SDK 就绪超时。"));
    }, timeoutMs);

    const settleResolve = () => {
      if (settled) {
        return;
      }
      settled = true;
      globalThis.clearTimeout(timeoutId);
      resolve();
    };

    const settleReject = (error: unknown) => {
      if (settled) {
        return;
      }
      settled = true;
      globalThis.clearTimeout(timeoutId);
      reject(error);
    };

    if (typeof sdk.error === "function") {
      sdk.error((error) => {
        settleReject(error);
      });
    }

    ready(() => {
      settleResolve();
    });
  });
}

interface AutoStartFeishuClientLoginOptions {
  locationPath: string;
  isHydrated: boolean;
  supportsClientLogin: boolean;
  isAuthenticated: boolean;
  isSubmitting: boolean;
  hasError: boolean;
}

export function shouldAutoStartFeishuClientLogin({
  locationPath,
  isHydrated,
  supportsClientLogin,
  isAuthenticated,
  isSubmitting,
  hasError,
}: AutoStartFeishuClientLoginOptions) {
  if (!isHydrated || !supportsClientLogin || isAuthenticated || isSubmitting || hasError) {
    return false;
  }
  return locationPath !== "/__dev/login";
}

export function consumeAuthCompleteToken(
  input: URL,
  authCompletePath = "/auth/complete"
) {
  if (input.pathname !== authCompletePath) {
    return null;
  }

  const token = input.searchParams.get("token")?.trim();
  if (!token) {
    return null;
  }

  return {
    token,
    cleanupPath: buildAppPath("/discover"),
  };
}
