import axios from "axios";

export const AUTH_TOKEN_STORAGE_KEY = "auth_token";
export const AUTH_TOKEN_CHANGED_EVENT = "fashion-feed-auth-token-changed";

const DEFAULT_API_BASE_URL = "/api/v1";

export const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;

const hasAbsoluteApiBaseUrl = /^https?:\/\//i.test(apiBaseUrl);
const apiOrigin = hasAbsoluteApiBaseUrl ? new URL(apiBaseUrl).origin : "";

function emitAuthTokenChanged(token: string | null) {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(
    new CustomEvent(AUTH_TOKEN_CHANGED_EVENT, {
      detail: { token },
    })
  );
}

export function getStoredAuthToken() {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
}

export function storeAuthToken(token: string) {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  emitAuthTokenChanged(token);
}

export function clearStoredAuthToken() {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  emitAuthTokenChanged(null);
}

export function resolveApiUrl(path: string) {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }

  if (hasAbsoluteApiBaseUrl) {
    return new URL(path, apiOrigin).toString();
  }

  return path;
}

const apiClient = axios.create({
  baseURL: apiBaseUrl,
});

apiClient.interceptors.request.use((config) => {
  const token = getStoredAuthToken();
  if (!token) {
    return config;
  }

  config.headers = config.headers ?? {};
  config.headers.Authorization = `Bearer ${token}`;
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      clearStoredAuthToken();
    }

    return Promise.reject(error);
  }
);

export default apiClient;
