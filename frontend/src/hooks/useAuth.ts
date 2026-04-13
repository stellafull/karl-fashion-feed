import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import axios from "axios";
import type { PropsWithChildren } from "react";
import apiClient, {
  AUTH_TOKEN_CHANGED_EVENT,
  apiBaseUrl,
  clearStoredAuthToken,
  getStoredAuthToken,
  resolveApiUrl,
  storeAuthToken,
} from "@/lib/api-client";
import {
  consumeAuthCompleteToken,
  detectFeishuClientAuthMode,
  hasFeishuH5Sdk,
  isLikelyFeishuClient,
  type FeishuClientAuthMode,
  waitForFeishuH5SdkReady,
} from "@/lib/auth-flow";

interface UserProfile {
  user_id: string;
  login_name: string | null;
  display_name: string;
  email: string | null;
  avatar_url: string | null;
  auth_source: string;
  is_admin: boolean;
  created_at: string;
}

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserProfile;
}

interface AuthContextValue {
  user: UserProfile | null;
  token: string | null;
  hydrated: boolean;
  isAuthenticated: boolean;
  isSubmitting: boolean;
  error: string | null;
  isLikelyFeishuClient: boolean;
  supportsClientLogin: boolean;
  beginFeishuLogin: () => Promise<void>;
  loginDev: (loginName: string, password: string) => Promise<void>;
  logout: () => void;
}

interface FeishuRequestAccessResult {
  code?: string;
}

interface FeishuWindow extends Window {
  tt?: {
    requestAccess?: (options: {
      scopeList: string[];
      appID: string;
      success?: (payload: FeishuRequestAccessResult) => void;
      fail?: (error: unknown) => void;
    }) => void;
    requestAuthCode?: (options: {
      appId: string;
      success?: (payload: FeishuRequestAccessResult) => void;
      fail?: (error: unknown) => void;
    }) => void;
  };
  h5sdk?: {
    ready?: (callback: () => void) => void;
    error?: (callback: (error: unknown) => void) => void;
  };
}

const AuthContext = createContext<AuthContextValue | null>(null);
const FEISHU_APP_ID = import.meta.env.VITE_FEISHU_APP_ID?.trim() || "";
const FEISHU_SCOPE_LIST = (import.meta.env.VITE_FEISHU_SCOPE_LIST || "")
  .split(",")
  .map((item: string) => item.trim())
  .filter(Boolean);
const DEV_LOGIN_PATH = "/__dev/login";

function extractErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (
      detail &&
      typeof detail === "object" &&
      "detail" in detail &&
      typeof detail.detail === "string" &&
      detail.detail.trim()
    ) {
      return detail.detail;
    }
    if (error.message.trim()) {
      return error.message;
    }
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return fallback;
}

function shouldFallbackToRequestAuthCode(error: unknown) {
  if (!error || typeof error !== "object") {
    return false;
  }
  const candidate = error as { errno?: unknown; errMsg?: unknown; errString?: unknown };
  return (
    candidate.errno === 103 ||
    candidate.errMsg === 103 ||
    candidate.errString === 103
  );
}

async function requestFeishuClientCode(authMode: FeishuClientAuthMode) {
  const currentWindow = window as FeishuWindow;
  if (!FEISHU_APP_ID) {
    throw new Error("缺少飞书 App ID 配置。");
  }

  await waitForFeishuH5SdkReady(currentWindow);

  if (authMode === "request_access") {
    return await new Promise<string>((resolve, reject) => {
      currentWindow.tt?.requestAccess?.({
        scopeList: FEISHU_SCOPE_LIST,
        appID: FEISHU_APP_ID,
        success(payload) {
          const code = payload.code?.trim();
          if (!code) {
            reject(new Error("飞书未返回可用的授权 code。"));
            return;
          }
          resolve(code);
        },
        fail(error) {
          if (
            shouldFallbackToRequestAuthCode(error) &&
            typeof currentWindow.tt?.requestAuthCode === "function"
          ) {
            currentWindow.tt.requestAuthCode({
              appId: FEISHU_APP_ID,
              success(payload) {
                const code = payload.code?.trim();
                if (!code) {
                  reject(new Error("飞书未返回可用的授权 code。"));
                  return;
                }
                resolve(code);
              },
              fail(fallbackError) {
                reject(fallbackError);
              },
            });
            return;
          }
          reject(error);
        },
      });
    });
  }

  if (authMode === "request_auth_code") {
    return await new Promise<string>((resolve, reject) => {
      currentWindow.tt?.requestAuthCode?.({
        appId: FEISHU_APP_ID,
        success(payload) {
          const code = payload.code?.trim();
          if (!code) {
            reject(new Error("飞书未返回可用的授权 code。"));
            return;
          }
          resolve(code);
        },
        fail(error) {
          reject(error);
        },
      });
    });
  }

  throw new Error("当前环境不支持飞书客户端免登录。请改用浏览器飞书授权登录。");
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clientAuthMode, setClientAuthMode] = useState<FeishuClientAuthMode | null>(() => {
    if (typeof window === "undefined") {
      return null;
    }
    return detectFeishuClientAuthMode(window as FeishuWindow, FEISHU_APP_ID);
  });
  const isLikelyFeishuContainer =
    typeof window !== "undefined" && isLikelyFeishuClient(window as FeishuWindow);
  const supportsClientLogin = clientAuthMode !== null;

  useEffect(() => {
    if (
      typeof window === "undefined" ||
      clientAuthMode !== null ||
      !isLikelyFeishuContainer
    ) {
      return;
    }

    const currentWindow = window as FeishuWindow;
    let attempts = 0;
    const maxAttempts = 20;
    const intervalId = window.setInterval(() => {
      attempts += 1;
      if (!hasFeishuH5Sdk(currentWindow)) {
        if (attempts >= maxAttempts) {
          window.clearInterval(intervalId);
        }
        return;
      }
      const nextMode = detectFeishuClientAuthMode(currentWindow, FEISHU_APP_ID);
      if (nextMode !== null) {
        setClientAuthMode(nextMode);
        window.clearInterval(intervalId);
        return;
      }
      if (attempts >= maxAttempts) {
        window.clearInterval(intervalId);
      }
    }, 250);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [clientAuthMode, isLikelyFeishuContainer]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const nextMode = detectFeishuClientAuthMode(window as FeishuWindow, FEISHU_APP_ID);
    if (nextMode !== null && nextMode !== clientAuthMode) {
      setClientAuthMode(nextMode);
    }
  }, [clientAuthMode]);

  useEffect(() => {
    let cancelled = false;

    async function hydrateAuth() {
      let nextToken = getStoredAuthToken();
      if (typeof window !== "undefined") {
        const pendingCompletion = consumeAuthCompleteToken(new URL(window.location.href));
        if (pendingCompletion) {
          storeAuthToken(pendingCompletion.token);
          nextToken = pendingCompletion.token;
          window.history.replaceState({}, document.title, pendingCompletion.cleanupPath);
        }
      }
      setToken(nextToken);

      if (!nextToken) {
        if (!cancelled) {
          setHydrated(true);
        }
        return;
      }

      try {
        const response = await apiClient.get<UserProfile>("/auth/me");
        if (!cancelled) {
          setUser(response.data);
          setError(null);
        }
      } catch {
        clearStoredAuthToken();
        if (!cancelled) {
          setToken(null);
          setUser(null);
        }
      } finally {
        if (!cancelled) {
          setHydrated(true);
        }
      }
    }

    void hydrateAuth();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const handleTokenChanged = (event: Event) => {
      const nextToken =
        (event as CustomEvent<{ token: string | null }>).detail?.token ?? null;
      setToken(nextToken);
      if (!nextToken) {
        setUser(null);
      }
    };

    window.addEventListener(
      AUTH_TOKEN_CHANGED_EVENT,
      handleTokenChanged as EventListener
    );

    return () => {
      window.removeEventListener(
        AUTH_TOKEN_CHANGED_EVENT,
        handleTokenChanged as EventListener
      );
    };
  }, []);

  const value = useMemo<AuthContextValue>(() => {
    return {
      user,
      token,
      hydrated,
      isAuthenticated: Boolean(token && user),
      isSubmitting,
      error,
      isLikelyFeishuClient: isLikelyFeishuContainer,
      supportsClientLogin,
      async beginFeishuLogin() {
        setIsSubmitting(true);
        setError(null);

        try {
          const runtimeAuthMode =
            (typeof window !== "undefined"
              ? detectFeishuClientAuthMode(window as FeishuWindow, FEISHU_APP_ID)
              : null) ?? clientAuthMode;

          if (runtimeAuthMode) {
            const code = await requestFeishuClientCode(runtimeAuthMode);
            const response = await apiClient.post<TokenResponse>(
              "/auth/feishu/client/exchange",
              { code }
            );
            storeAuthToken(response.data.access_token);
            setToken(response.data.access_token);
            setUser(response.data.user);
          } else if (isLikelyFeishuContainer) {
            throw new Error("正在等待飞书客户端登录能力就绪，请稍候自动登录。");
          } else {
            const browserStartUrl = resolveApiUrl(
              `${apiBaseUrl}/auth/feishu/browser/start`
            );
            window.location.assign(browserStartUrl);
            return;
          }
        } catch (loginError) {
          setUser(null);
          setToken(null);
          clearStoredAuthToken();
          setError(extractErrorMessage(loginError, "飞书登录失败，请稍后重试。"));
        } finally {
          setIsSubmitting(false);
          setHydrated(true);
        }
      },
      async loginDev(loginName: string, password: string) {
        const normalizedLoginName = loginName.trim();
        if (!normalizedLoginName || !password) {
          setError("请输入账号和密码。" );
          return;
        }

        setIsSubmitting(true);
        setError(null);

        try {
          const payload = new URLSearchParams();
          payload.set("username", normalizedLoginName);
          payload.set("password", password);
          payload.set("grant_type", "password");

          const response = await apiClient.post<TokenResponse>("/auth/dev/token", payload, {
            headers: {
              "Content-Type": "application/x-www-form-urlencoded",
            },
          });

          storeAuthToken(response.data.access_token);
          setToken(response.data.access_token);
          setUser(response.data.user);
        } catch (loginError) {
          setUser(null);
          setToken(null);
          clearStoredAuthToken();
          setError(extractErrorMessage(loginError, "dev-root 登录失败。"));
        } finally {
          setIsSubmitting(false);
          setHydrated(true);
        }
      },
      logout() {
        clearStoredAuthToken();
        setToken(null);
        setUser(null);
        setError(null);
        if (typeof window !== "undefined" && window.location.pathname === DEV_LOGIN_PATH) {
          window.history.replaceState({}, document.title, "/discover");
        }
      },
    };
  }, [
    clientAuthMode,
    error,
    hydrated,
    isLikelyFeishuContainer,
    isSubmitting,
    supportsClientLogin,
    token,
    user,
  ]);

  return createElement(AuthContext.Provider, { value }, children);
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }

  return context;
}
