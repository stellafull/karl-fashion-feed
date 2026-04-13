"""Feishu OAuth and user-profile exchange service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp

from backend.app.config.auth_config import auth_settings


@dataclass(slots=True)
class FeishuUserIdentity:
    """Normalized user identity returned from Feishu."""

    feishu_user_id: str
    display_name: str
    email: str | None
    avatar_url: str | None
    open_id: str | None = None
    union_id: str | None = None


class FeishuAuthService:
    """Exchange Feishu auth codes and fetch normalized user identity."""

    _oauth_token_url = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    _browser_authorize_url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    _user_info_url = "https://open.feishu.cn/open-apis/authen/v1/user_info"

    def __init__(self) -> None:
        auth_settings.validate_feishu_settings()
        self._app_id = auth_settings.FEISHU_APP_ID or ""
        self._app_secret = auth_settings.FEISHU_APP_SECRET or ""
        self._browser_redirect_uri = auth_settings.FEISHU_BROWSER_REDIRECT_URI or ""
        self._timeout = aiohttp.ClientTimeout(total=auth_settings.FEISHU_REQUEST_TIMEOUT_SECONDS)

    def build_browser_authorize_url(self, *, state: str) -> str:
        """Build the Feishu browser authorization URL."""
        query = {
            "client_id": self._app_id,
            "response_type": "code",
            "redirect_uri": self._browser_redirect_uri,
            "state": state,
        }
        scope = auth_settings.FEISHU_OAUTH_SCOPE.strip()
        if scope:
            query["scope"] = scope
        return f"{self._browser_authorize_url}?{urlencode(query)}"

    async def exchange_client_code(self, code: str) -> FeishuUserIdentity:
        """Exchange an in-client requestAccess code for a normalized identity."""
        user_access_token = await self._exchange_code_for_user_access_token(code=code, redirect_uri=None)
        return await self._fetch_user_identity(user_access_token)

    async def exchange_browser_code(self, code: str) -> FeishuUserIdentity:
        """Exchange a browser OAuth code for a normalized identity."""
        user_access_token = await self._exchange_code_for_user_access_token(
            code=code,
            redirect_uri=self._browser_redirect_uri,
        )
        return await self._fetch_user_identity(user_access_token)

    async def _exchange_code_for_user_access_token(
        self,
        *,
        code: str,
        redirect_uri: str | None,
    ) -> str:
        request_body: dict[str, Any] = {
            "grant_type": "authorization_code",
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "code": code,
        }
        if redirect_uri:
            request_body["redirect_uri"] = redirect_uri

        payload = await self._post_json(self._oauth_token_url, request_body)
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise ValueError("Feishu token exchange response missing access_token")
        return token

    async def _fetch_user_identity(self, user_access_token: str) -> FeishuUserIdentity:
        payload = await self._get_json(
            self._user_info_url,
            headers={
                "Authorization": f"Bearer {user_access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Feishu user info response missing data payload")

        feishu_user_id = str(data.get("user_id") or "").strip()
        display_name = str(data.get("name") or "").strip()
        if not feishu_user_id or not display_name:
            raise ValueError("Feishu user info missing required user_id or name")

        email = str(data.get("email") or "").strip() or None
        avatar_url = str(data.get("avatar_url") or "").strip() or None
        open_id = str(data.get("open_id") or "").strip() or None
        union_id = str(data.get("union_id") or "").strip() or None
        return FeishuUserIdentity(
            feishu_user_id=feishu_user_id,
            display_name=display_name,
            email=email,
            avatar_url=avatar_url,
            open_id=open_id,
            union_id=union_id,
        )

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(
                url,
                json=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            ) as response:
                text = await response.text()
                if response.status != 200:
                    raise ValueError(
                        f"Feishu POST {url} failed: status={response.status} body={text[:500]}"
                    )
                payload = await response.json()
        return self._validate_success_payload(payload, url)

    async def _get_json(self, url: str, *, headers: dict[str, str]) -> dict[str, Any]:
        async with aiohttp.ClientSession(timeout=self._timeout, headers=headers) as session:
            async with session.get(url) as response:
                text = await response.text()
                if response.status != 200:
                    raise ValueError(
                        f"Feishu GET {url} failed: status={response.status} body={text[:500]}"
                    )
                payload = await response.json()
        return self._validate_success_payload(payload, url)

    def _validate_success_payload(self, payload: Any, url: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError(f"Feishu response from {url} must be a JSON object")
        code = payload.get("code")
        if code not in (None, 0):
            message = str(payload.get("msg") or "unknown error").strip()
            raise ValueError(f"Feishu API {url} returned code={code}: {message}")
        return payload
