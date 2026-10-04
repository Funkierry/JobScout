"""Bounded settling and a read-only, noise-reduced view of the live DOM."""

import asyncio
from typing import Any

from app.application_tracker.browser.models import BrowserAccessConfig

DOM_TEXT_SCRIPT = r"""() => {
    const root = document.querySelector('main,[role="main"]') || document.body;
    if (!root) return '';
    const excluded = 'nav,footer,script,style,noscript,[role="navigation"],[role="contentinfo"],'
        + '[hidden],[aria-hidden="true"],[class*="recommend" i],[id*="recommend" i],'
        + '[data-testid*="recommend" i],.cookie-banner';
    const blocks = new Set(['DIV','P','LI','TR','SECTION','ARTICLE','H1','H2','H3','BR']);
    let count = 0;
    const parts = [];
    function visit(node) {
        if (++count > 50000) return;
        if (node.nodeType === Node.TEXT_NODE) { parts.push(node.textContent); return; }
        if (node.nodeType !== Node.ELEMENT_NODE || node.matches(excluded)) return;
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden') return;
        if (blocks.has(node.tagName)) parts.push('\n');
        for (const child of node.childNodes) visit(child);
        if (blocks.has(node.tagName)) parts.push('\n');
    }
    visit(root);
    return parts.join('').replace(/[ \t]+/g, ' ').replace(/\n[ \t]+/g, '\n')
        .replace(/\n{3,}/g, '\n\n').trim();
}"""


async def read_dom_text(page: Any, config: BrowserAccessConfig) -> str:
    if hasattr(page, "evaluate"):
        try:
            text = await asyncio.wait_for(page.evaluate(DOM_TEXT_SCRIPT), config.page_text_timeout_ms / 1000)
            if isinstance(text, str):
                return text[:50_000]
        except Exception:
            pass
    # Compatibility for text-only browser adapters, and a failed DOM evaluation.
    return (await page.locator("body").inner_text(timeout=config.page_text_timeout_ms))[:50_000]


async def wait_for_observation(page: Any, config: BrowserAccessConfig, *, after_click: bool = False) -> None:
    """First of network-idle or non-empty stable body, with one overall deadline."""

    async def idle():
        try:
            await page.wait_for_load_state("networkidle", timeout=config.settle_timeout_ms)
        except Exception:
            # A load-state timeout is not readiness; allow the stability probe.
            await asyncio.Future()

    async def stable():
        previous, repeats = None, 0
        while True:
            text = await page.locator("body").inner_text(timeout=min(config.page_text_timeout_ms, config.settle_timeout_ms))
            length = len(text.strip())
            repeats = repeats + 1 if length and length == previous else 0
            if repeats >= 3:
                return
            previous = length
            await asyncio.sleep(config.stable_poll_ms / 1000)

    # A same-document click retains the old networkidle state. Only fresh body
    # samples can settle this action; otherwise a delayed tab update is missed.
    tasks = [asyncio.create_task(stable())]
    if not after_click:
        tasks.append(asyncio.create_task(idle()))
    try:
        # Failed probes never win the readiness race.
        async with asyncio.timeout(config.settle_timeout_ms / 1000):
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if any(not task.cancelled() and task.exception() is None for task in done):
                    return
    except TimeoutError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
