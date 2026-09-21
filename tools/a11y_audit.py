"""Accessibility audit: drive every stage in both themes and run axe-core contrast rules.

Run against the production bundle, which is what ships: ``make dev-api`` in one terminal and
``make preview`` in another, then ``make audit-a11y``. The dev server also works but is slow
enough that the waits below can time out. Requires ``pip install playwright``
and ``playwright install chromium``. Exit code is non-zero when axe reports any violation, so this
can gate a release: re-run it whenever a token in ``frontend/src/styles.css`` changes.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import pathlib
import random
import sys
from typing import Any

from playwright.async_api import Page, async_playwright, expect

AXE = pathlib.Path(__file__).resolve().parents[1] / "frontend/node_modules/axe-core/axe.min.js"
# Audit against the WCAG 2.0/2.1 A and AA rule sets rather than a hand-picked list, so a new
# failure category cannot slip through. axe's "best-practice" rules are deliberately excluded:
# the only one that fires is focus-order-semantics against AG Grid's roving-tabindex grid DOM,
# which is the standard accessible-grid pattern and not a WCAG requirement.
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
UI = "http://localhost:5173/"
SAMPLE_TEXTS = [
    "I absolutely LOVED this movie, best of the year!!!",
    "Not good. Not good at all. The plot was boring.",
    "The cats were running around the houses; it was fine.",
    "Terrible service, never coming back. #disappointed",
    "Wow just wow. Ten out of ten, would watch again.",
]

Finding = tuple[str, str, str, str]


async def run_axe(page: Page, screen: str, findings: list[Finding]) -> None:
    """Inject axe and record every violation node for this screen."""
    # Sample settled rendered colors, rather than different frames of an entrance animation.
    await page.evaluate(
        "Promise.all(document.getAnimations()"
        ".filter(a => a.effect?.getTiming().iterations !== Infinity)"
        ".map(a => a.finished.catch(() => {})))"
    )
    await page.add_script_tag(path=str(AXE))
    options = json.dumps({"runOnly": {"type": "tag", "values": TAGS}})
    result: dict[str, Any] = await page.evaluate(f"async () => axe.run(document, {options})")
    for violation in result["violations"]:
        for node in violation["nodes"]:
            findings.append((screen, violation["id"], violation["impact"], node["html"][:120]))


async def keyboard_diff(page: Page, findings: list[Finding], theme: str) -> None:
    """Reach a real grid action with Tab, activate two rows, and verify native focus return."""
    for _ in range(100):
        await page.keyboard.press("Tab")
        if await page.evaluate("document.activeElement?.classList.contains('record-diff-action')"):
            break
    else:
        raise AssertionError("Record action was unreachable with Tab")

    first_id = await page.locator(".record-diff-action:focus").get_attribute("aria-label")
    for row in range(2):
        action = page.locator(".record-diff-action:focus")
        name = await action.get_attribute("aria-label")
        assert name and name.startswith("View changes for record ")
        if row:
            assert name != first_id, "ArrowDown must preserve the next row's identity"
        trigger = await action.element_handle()
        assert trigger
        focus = await action.evaluate("""element => {
            const style = getComputedStyle(element);
            return {visible: element.matches(':focus-visible'), style: style.outlineStyle,
                    width: parseFloat(style.outlineWidth), offset: parseFloat(style.outlineOffset)};
        }""")
        assert focus["visible"] and focus["style"] == "solid" and focus["width"] >= 2
        assert focus["offset"] < 0, "Grid focus must be drawn inside its clipping cell"
        for key in ("Enter", "Space"):
            await page.keyboard.press(key)
            dialog = page.get_by_role("dialog")
            await expect(dialog).to_be_visible()
            await expect(dialog.locator("del, ins").first).to_be_visible()
            await expect(
                dialog.get_by_role(
                    "heading",
                    name="Record " + name.removeprefix("View changes for record "),
                    exact=True,
                )
            ).to_be_visible()
            await run_axe(page, f"{theme}/diff-{row}-{key}", findings)
            await page.keyboard.press("Escape")
            await expect(dialog).to_have_count(0)
            assert await trigger.evaluate("element => document.activeElement === element")
        if row == 0:
            await page.keyboard.press("Shift+Tab")
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Tab")
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement?.getAttribute('col-id') === 'original'")
    print(f"{theme}: keyboard diff, Enter/Space, Escape, focus return and grid navigation passed")


def sample_csv() -> bytes:
    """520 rows so the 500-record minimum is met without any network source."""
    random.seed(3)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["text"])
    for i in range(520):
        writer.writerow([f"{random.choice(SAMPLE_TEXTS)} (#{i})"])
    return buffer.getvalue().encode()


async def walk_stages(page: Page, theme: str, findings: list[Finding], shots: bool) -> None:
    """Collect → Clean → Analyze → Label → Export, auditing each screen."""

    async def audit(screen: str) -> None:
        await run_axe(page, f"{theme}/{screen}", findings)
        # Opaque surfaces guarantee the same contrast without browser filter support.
        assert await page.locator(".glass-panel, .glass-bar").evaluate_all(
            "elements => elements.every(e => /^rgb\\(/.test(getComputedStyle(e).backgroundColor))"
        )
        fallback = await page.add_style_tag(
            content=(
                "* { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }"
            )
        )
        await run_axe(page, f"{theme}/{screen}-fallback", findings)
        await fallback.evaluate("element => element.remove()")
        if shots:
            await page.screenshot(path=f"/tmp/a11y_{theme}_{screen}.png")

    await page.goto(UI)
    await page.wait_for_selector("text=Search and collect", timeout=15000)
    if theme == "dark":
        await page.click("[aria-label='Switch to dark mode']")
        await page.wait_for_timeout(200)
    await audit("collect")

    await page.click("[role=tab]:has-text('Upload CSV')")
    await page.set_input_files(
        "input[type=file]", {"name": "d.csv", "mimeType": "text/csv", "buffer": sample_csv()}
    )
    await page.click("button[type=submit]")
    await page.wait_for_selector("text=posts collected", timeout=15000)
    await audit("collected")

    await page.click("text=Continue to Clean")
    await page.wait_for_selector("text=Normalise for NLP")
    await audit("clean")
    assert await page.locator("main input[type=checkbox]:checked").count() == 0
    await page.locator("button:visible:has-text('Continue to Analyze')").first.click()
    await page.wait_for_selector(".ag-row", timeout=40000)
    await audit("analyze-original")
    await expect(page.locator(".record-diff-action")).to_have_count(0)
    await page.click("text=Adjust steps")
    await page.wait_for_selector("text=Normalise for NLP")
    step = page.locator("li input[type=checkbox]").first
    await step.uncheck()
    await audit("clean-off-step")
    await step.check()
    await page.locator("details").first.locator("summary").click()
    await audit("clean-explanation")

    await page.locator("button:visible:has-text('Continue to Analyze')").first.click()
    await page.wait_for_selector(".ag-row", timeout=40000)
    await page.wait_for_timeout(500)
    await page.evaluate("window.scrollTo(0,0)")
    await audit("analyze")
    await keyboard_diff(page, findings, theme)

    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.click("text=Continue to Label")
    await page.wait_for_selector("text=Label the posts")
    await page.wait_for_timeout(400)
    await audit("label")

    await page.click("text=Choose what to label by hand")
    await page.wait_for_selector("text=How much will you review")
    await page.locator("input[type=text]").first.fill("6")
    await audit("review-choice")

    await page.click("button:has-text('Start reviewing')")
    await page.wait_for_selector("blockquote", timeout=15000)
    await page.keyboard.press("2")
    await page.wait_for_timeout(150)
    await page.keyboard.press("ArrowLeft")
    await page.wait_for_timeout(150)
    await audit("reviewing")

    await page.click("button:has-text('Stop here')")
    await page.wait_for_selector("text=Labels ready", timeout=15000)
    await page.click("text=Continue to Export")
    await page.wait_for_selector("h2:has-text('Export')", timeout=15000)
    await page.wait_for_timeout(600)
    await audit("export")


async def main() -> int:
    """Desktop in both themes plus a phone viewport; return the process exit code."""
    findings: list[Finding] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for theme in ("light", "dark"):
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await walk_stages(page, theme, findings, shots="--screenshots" in sys.argv)
            await page.close()
        phone = await browser.new_page(
            viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True
        )
        await walk_stages(phone, "dark", findings, shots=False)
        await phone.close()
        await browser.close()

    print(f"violations: {len(findings)}")
    for finding in findings:
        print(*finding, sep=" | ")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
