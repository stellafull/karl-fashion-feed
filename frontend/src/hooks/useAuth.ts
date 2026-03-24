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
  clearStoredAuthToken,
  getStoredAuthToken,
  storeAuthToken,
} from "@/lib/api-client";

interface UserProfile {
  user_id: string;
  login_name: string;
  display_name: string;
  email: string | null;
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
  login: (loginName: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

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

export function AuthProvider({ children }: PropsWithChildren) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function hydrateAuth() {
      const storedToken = getStoredAuthToken();
      setToken(storedToken);

      if (!storedToken) {
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
      async login(loginName: string, password: string) {
        const normalizedLoginName = loginName.trim();
        if (!normalizedLoginName || !password) {
          setError("请输入账号和密码。");
          return;
        }

        setIsSubmitting(true);
        setError(null);

        try {
          const payload = new URLSearchParams();
          payload.set("username", normalizedLoginName);
          payload.set("password", password);
          payload.set("grant_type", "password");

          const response = await apiClient.post<TokenResponse>("/auth/token", payload, {
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
          setError(extractErrorMessage(loginError, "登录失败，请检查账号密码。"));
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
      },
    };
  }, [error, hydrated, isSubmitting, token, user]);

  return createElement(AuthContext.Provider, { value }, children);
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }

  return context;
}
