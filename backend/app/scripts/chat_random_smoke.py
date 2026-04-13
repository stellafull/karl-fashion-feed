"""Run a real frontend-backend chat smoke with a random realistic prompt."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000/api/v1"
DEFAULT_LOGIN_NAME = "dev-root"
DEFAULT_PASSWORD = "dev-root"
DEFAULT_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 1.0
AUTH_TOKEN_STORAGE_KEY = "auth_token"
LOGIN_BUTTON_LABEL = "使用 dev-root 登录"
LOGOUT_BUTTON_LABEL = "退出登录"
CHAT_PLACEHOLDER = "Ask anything about fashion trends, brands, or recent signals..."
OUTPUT_DIR = REPO_ROOT / "backend" / "runtime_reviews" / "chat_smoke"


@dataclass(frozen=True, slots=True)
class ChatSmokePrompt:
    """One realistic user prompt used for manual chat smoke validation."""

    category: str
    prompt: str


@dataclass(frozen=True, slots=True)
class SmokeArtifact:
    """Structured output saved after one smoke run."""

    started_at: str
    finished_at: str
    frontend_base_url: str
    api_base_url: str
    category: str
    prompt: str
    session_id: str
    final_status: str
    assistant_answer: str
    assistant_message_id: str
    response_json: dict | None
    screenshot_path: str
    screenshot_error: str | None = None


PROMPT_BANK: tuple[ChatSmokePrompt, ...] = (
    ChatSmokePrompt(
        category="trend-summary",
        prompt="如果我要在明早例会上用 1 分钟讲清楚最近最值得追的女装趋势，你会抓哪三点？",
    ),
    ChatSmokePrompt(
        category="brand-watchlist",
        prompt="最近有哪些品牌动作值得中国区同事重点盯？按品牌给我结论，不要铺太多背景。",
    ),
    ChatSmokePrompt(
        category="commercial-angle",
        prompt="从商业转化角度看，现在哪几个趋势已经从秀场语言走向可买？请直接给判断。",
    ),
    ChatSmokePrompt(
        category="color-signal",
        prompt="帮我总结一下这两周最强的颜色信号，并说说分别适合落到哪些品类。",
    ),
    ChatSmokePrompt(
        category="accessory-focus",
        prompt="如果内部 briefing 只能单独拎一个配饰方向，你建议盯什么？理由说清楚。",
    ),
    ChatSmokePrompt(
        category="brand-pattern",
        prompt="最近哪些品牌在用 archive、复古或经典款语言讲新故事？给我一个精简名单。",
    ),
    ChatSmokePrompt(
        category="editorial-brief",
        prompt="如果今天要给编辑团队一页中文快报，最值得写进标题的三个信号是什么？",
    ),
    ChatSmokePrompt(
        category="styling-direction",
        prompt="我想找偏学院感但不过分保守的乐福鞋方向，重点说鞋型、材质和搭配思路。",
    ),
    ChatSmokePrompt(
        category="market-mapping",
        prompt="最近哪些品牌在把运动元素做得更时装化？请按品牌拆给我看。",
    ),
    ChatSmokePrompt(
        category="buying-note",
        prompt="如果站在买手视角，你觉得最近最值得继续观察的廓形变化是什么？",
    ),
)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run one random frontend-backend chat smoke through the real UI.",
    )
    parser.add_argument(
        "--frontend-base-url",
        default=DEFAULT_FRONTEND_BASE_URL,
        help=f"Frontend base URL. Defaults to {DEFAULT_FRONTEND_BASE_URL}.",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help=f"Backend API base URL. Defaults to {DEFAULT_API_BASE_URL}.",
    )
    parser.add_argument(
        "--login-name",
        default=DEFAULT_LOGIN_NAME,
        help=f"Local login name. Defaults to {DEFAULT_LOGIN_NAME}.",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"Local password. Defaults to {DEFAULT_PASSWORD}.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional seed for reproducible prompt selection.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout for the full smoke run. Defaults to {DEFAULT_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser instead of running headless.",
    )
    return parser.parse_args()


def choose_prompt(seed: int | None) -> ChatSmokePrompt:
    """Choose one prompt from the realistic prompt bank."""
    if seed is None:
        return random.SystemRandom().choice(PROMPT_BANK)
    return random.Random(seed).choice(PROMPT_BANK)


def build_output_paths(run_started_at: datetime) -> tuple[Path, Path]:
    """Build artifact paths for one smoke run."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_key = run_started_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        OUTPUT_DIR / f"{run_key}.json",
        OUTPUT_DIR / f"{run_key}.png",
    )


def extract_session_id_from_url(url: str) -> str:
    """Extract the chat session id from a `/chat/{session_id}` URL."""
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    if len(path_parts) < 2 or path_parts[-2] != "chat":
        raise ValueError(f"unexpected chat URL: {url}")
    session_id = path_parts[-1]
    if session_id == "new":
        raise ValueError(f"chat session was not created yet: {url}")
    return session_id


async def ensure_http_ready(url: str) -> None:
    """Fail fast when the frontend or backend endpoint is not reachable."""
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status >= 400:
                raise RuntimeError(f"{url} returned unexpected status {response.status}")


async def login_if_needed(
    *,
    page,
    frontend_base_url: str,
    login_name: str,
    password: str,
    timeout_seconds: int,
) -> None:
    """Log into the hidden dev page when the smoke run is not authenticated yet."""
    await page.goto(
        f"{frontend_base_url.rstrip('/')}/__dev/login",
        wait_until="domcontentloaded",
    )
    login_button = page.get_by_role("button", name=LOGIN_BUTTON_LABEL)
    try:
        await login_button.wait_for(state="visible", timeout=2_000)
    except PlaywrightTimeoutError:
        return

    await page.get_by_placeholder("root").fill(login_name)
    await page.get_by_placeholder("••••••••").fill(password)
    await login_button.click()
    await page.get_by_role("button", name=LOGOUT_BUTTON_LABEL).wait_for(
        state="visible",
        timeout=timeout_seconds * 1_000,
    )


async def open_clean_chat_page(
    *,
    page,
    frontend_base_url: str,
    timeout_seconds: int,
) -> None:
    """Navigate to a fresh chat page with the composer ready."""
    await page.goto(
        f"{frontend_base_url.rstrip('/')}/chat/new",
        wait_until="domcontentloaded",
    )
    await page.get_by_placeholder(CHAT_PLACEHOLDER).wait_for(
        state="visible",
        timeout=timeout_seconds * 1_000,
    )


async def submit_prompt_and_get_session_id(
    *,
    page,
    prompt: str,
    timeout_seconds: int,
) -> str:
    """Submit one prompt through the real UI and return the created session id."""
    composer = page.get_by_placeholder(CHAT_PLACEHOLDER)
    await composer.click()
    await composer.press_sequentially(prompt)
    await composer.press("Enter")
    await page.wait_for_url(
        re.compile(r".*/chat/(?!new$)[^/?#]+$"),
        timeout=timeout_seconds * 1_000,
    )
    return extract_session_id_from_url(page.url)


async def read_auth_token(page) -> str:
    """Read the JWT persisted by the real frontend."""
    token = await page.evaluate(
        f"window.localStorage.getItem('{AUTH_TOKEN_STORAGE_KEY}')"
    )
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("frontend auth token not found after login")
    return token


async def wait_for_terminal_assistant_message(
    *,
    api_base_url: str,
    token: str,
    session_id: str,
    timeout_seconds: int,
) -> dict:
    """Poll backend messages until the assistant reaches a terminal status."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=30)
    messages_url = f"{api_base_url.rstrip('/')}/chat/sessions/{session_id}/messages"

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        while asyncio.get_running_loop().time() < deadline:
            async with session.get(messages_url) as response:
                if response.status >= 400:
                    raise RuntimeError(
                        f"polling chat messages failed with status {response.status}"
                    )
                payload = await response.json()

            messages = payload.get("messages")
            if isinstance(messages, list):
                for message in reversed(messages):
                    if message.get("role") != "assistant":
                        continue
                    status = message.get("status")
                    if status in {"done", "failed", "interrupted"}:
                        return message
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"assistant message did not reach a terminal state within {timeout_seconds} seconds"
    )


async def save_artifacts(
    *,
    page,
    artifact: SmokeArtifact,
    json_path: Path,
    screenshot_path: Path,
) -> SmokeArtifact:
    """Persist JSON plus final page screenshot for manual inspection."""
    try:
        await page.screenshot(
            path=str(screenshot_path),
            full_page=True,
            timeout=5_000,
        )
    except PlaywrightTimeoutError:
        artifact = SmokeArtifact(
            **{
                **asdict(artifact),
                "screenshot_error": "page.screenshot timeout after 5000ms",
            }
        )
    json_path.write_text(
        json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifact


async def run_smoke(args: argparse.Namespace) -> SmokeArtifact:
    """Run one randomized chat smoke end to end."""
    await ensure_http_ready(args.frontend_base_url)
    backend_docs_url = args.api_base_url.removesuffix("/api/v1") + "/docs"
    await ensure_http_ready(backend_docs_url)

    selected_prompt = choose_prompt(args.seed)
    started_at = datetime.now(UTC)
    json_path, screenshot_path = build_output_paths(started_at)
    artifact: SmokeArtifact | None = None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        page = await browser.new_page(viewport={"width": 1440, "height": 1280})
        try:
            await page.goto(args.frontend_base_url, wait_until="domcontentloaded")
            await login_if_needed(
                page=page,
                frontend_base_url=args.frontend_base_url,
                login_name=args.login_name,
                password=args.password,
                timeout_seconds=args.timeout_seconds,
            )
            await open_clean_chat_page(
                page=page,
                frontend_base_url=args.frontend_base_url,
                timeout_seconds=args.timeout_seconds,
            )
            session_id = await submit_prompt_and_get_session_id(
                page=page,
                prompt=selected_prompt.prompt,
                timeout_seconds=args.timeout_seconds,
            )
            token = await read_auth_token(page)
            assistant_message = await wait_for_terminal_assistant_message(
                api_base_url=args.api_base_url,
                token=token,
                session_id=session_id,
                timeout_seconds=args.timeout_seconds,
            )
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(800)

            artifact = SmokeArtifact(
                started_at=started_at.isoformat(),
                finished_at=datetime.now(UTC).isoformat(),
                frontend_base_url=args.frontend_base_url,
                api_base_url=args.api_base_url,
                category=selected_prompt.category,
                prompt=selected_prompt.prompt,
                session_id=session_id,
                final_status=str(assistant_message.get("status") or ""),
                assistant_answer=str(assistant_message.get("content_text") or "").strip(),
                assistant_message_id=str(assistant_message.get("chat_message_id") or ""),
                response_json=assistant_message.get("response_json"),
                screenshot_path=str(screenshot_path),
            )
            artifact = await save_artifacts(
                page=page,
                artifact=artifact,
                json_path=json_path,
                screenshot_path=screenshot_path,
            )
        finally:
            await browser.close()

    if artifact is None:
        raise RuntimeError("chat smoke did not produce an artifact")

    if artifact.final_status != "done":
        raise RuntimeError(
            f"chat smoke ended with status={artifact.final_status} "
            f"(artifact: {json_path})"
        )

    print(f"[chat-smoke] category: {artifact.category}")
    print(f"[chat-smoke] prompt: {artifact.prompt}")
    print("[chat-smoke] answer:")
    print(artifact.assistant_answer)
    print(f"[chat-smoke] artifact: {json_path}")
    print(f"[chat-smoke] screenshot: {screenshot_path}")
    if artifact.screenshot_error:
        print(f"[chat-smoke] screenshot warning: {artifact.screenshot_error}")
    return artifact


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(run_smoke(parse_args()))


if __name__ == "__main__":
    main()
