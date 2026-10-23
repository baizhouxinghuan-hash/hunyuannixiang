"""浏览器自动化（任务书 §4A / §30 / §31）。

原则
----
* 只用 Chromium + 持久化 Profile，**登录必须由用户手动完成**；
* 不注入任何绕过验证码/风控的脚本（这里唯一的注入是抓包钩子）；
* 原始凭证只写 ``secrets/``，``captures/`` 里的内容一律经过脱敏。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from analyzer.network_capture import NetworkRecorder

DEFAULT_URL = "https://aistudio.tencent.com/"
#: 去掉最明显的自动化特征（不涉及任何绕过验证码/风控的行为）
DEFAULT_ARGS = ["--disable-blink-features=AutomationControlled"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


class BrowserSession:
    """一个带抓包能力的 Chromium 会话。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_URL,
        profile_dir: str | Path = "secrets/browser_profile",
        storage_state: str | Path | None = None,
        har_path: str | Path | None = None,
        headless: bool = False,
        slow_mo: int = 0,
        url_patterns: list[str] | None = None,
        capture_all: bool = False,
        logger: Any = None,
    ) -> None:
        self.base_url = base_url
        self.profile_dir = Path(profile_dir)
        self.storage_state = Path(storage_state) if storage_state else None
        self.har_path = Path(har_path) if har_path else None
        self.headless = headless
        self.slow_mo = slow_mo
        self.recorder = NetworkRecorder(
            url_patterns=url_patterns, capture_all=capture_all, logger=logger
        )
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self._attached_pages: set[int] = set()

    # -- 生命周期 ---------------------------------------------------------
    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> Any:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "playwright 未安装：pip install playwright==1.58.0 然后 python -m playwright install chromium"
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        if self.har_path:
            self.har_path.parent.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "slow_mo": self.slow_mo,
            "args": DEFAULT_ARGS,
            "user_agent": USER_AGENT,
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
        }
        if self.har_path:
            launch_kwargs.update(
                {
                    "record_har_path": str(self.har_path),
                    "record_har_mode": "full",
                    "record_har_content": "embed",
                }
            )
        if self.storage_state and self.storage_state.exists():
            launch_kwargs["storage_state"] = str(self.storage_state)

        self._context = await self._pw.chromium.launch_persistent_context(**launch_kwargs)
        await self.recorder.attach(self._context)

        self._context.on("page", lambda page: asyncio.get_running_loop().create_task(self._on_new_page(page)))
        pages = list(self._context.pages)
        self._page = pages[0] if pages else await self._context.new_page()
        await self._attach_page(self._page)
        return self._page

    async def _on_new_page(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:  # pragma: no cover
            pass
        await self._attach_page(page)

    async def _attach_page(self, page: Any) -> None:
        if id(page) in self._attached_pages:
            return
        self._attached_pages.add(id(page))
        await self.recorder.attach_page(page)

    async def goto_home(self) -> Any:
        page = await self.page()
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=60_000)
        return page

    async def page(self) -> Any:
        if self._page is None:
            self._page = await self._context.new_page()
            await self._attach_page(self._page)
        return self._page

    async def context(self) -> Any:
        return self._context

    async def close(self) -> None:
        await self.recorder.detach()
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # pragma: no cover
                pass
            self._context = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # pragma: no cover
                pass
            self._pw = None

    # -- 会话导出（原始凭证只允许写 secrets/）-----------------------------
    async def export_storage_state(self, path: str | Path) -> Path:
        """导出 Playwright storage_state（供 API 服务复用）。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        state = await self._context.storage_state()
        target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    async def cookies(self) -> list[dict[str, Any]]:
        return await self._context.cookies()

    async def storage_snapshot(self) -> dict[str, Any]:
        """cookies + localStorage + sessionStorage 快照（含明文，调用方负责放进 secrets/）。"""
        page = await self.page()
        local = await page.evaluate(
            "() => { try { return Object.fromEntries(Object.entries(window.localStorage)); } catch (e) { return {}; } }"
        )
        session = await page.evaluate(
            "() => { try { return Object.fromEntries(Object.entries(window.sessionStorage)); } catch (e) { return {}; } }"
        )
        return {
            "url": page.url,
            "cookies": await self.cookies(),
            "localStorage": local or {},
            "sessionStorage": session or {},
            "storage_state": await self._context.storage_state(),
        }

    async def session_summary(self) -> dict[str, Any]:
        """脱敏摘要（可安全打印）。"""
        from app.security.redaction import redact_obj

        page = await self.page()
        cookies = await self.cookies()
        return redact_obj(
            {
                "url": page.url,
                "cookie_count": len(cookies),
                "cookie_names": sorted({str(c.get("name")) for c in cookies}),
                "domains": sorted({str(c.get("domain")) for c in cookies if c.get("domain")}),
                "has_storage_state": self._context is not None,
            }
        )
