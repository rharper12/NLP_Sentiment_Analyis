"""Accessibility audit: drive every stage in both themes and run axe-core contrast rules.

Run against an isolated API and production preview; the general flow imports synthetic CSVs.
``--consumer-only`` intercepts all API calls and requires only the preview. Install the locked
development requirements and run ``playwright install chromium`` first. A system Chromium
executable can be selected with ``A11Y_BROWSER_EXECUTABLE``. Failed assertions or axe violations
return a non-zero exit code; unresolved automated checks are reported for manual evaluation.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import pathlib
import random
import re
import sys
from typing import Any

from a11y_consumer import walk_consumer
from playwright.async_api import Page, Route, async_playwright, expect

AXE = pathlib.Path(__file__).resolve().parents[1] / "frontend/node_modules/axe-core/axe.min.js"
# Include WCAG 2.2 AA. Report unresolved checks separately; an empty violations list alone
# cannot establish conformance. Best-practice heuristics are not WCAG success criteria.
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
UI = os.environ.get("A11Y_UI_URL", "http://localhost:5173/")
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
    if result["incomplete"]:
        print(
            f"{screen}: manual checks needed: " + ", ".join(v["id"] for v in result["incomplete"])
        )
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
        width = (page.viewport_size or {})["width"]
        await run_axe(page, f"{theme}-{width}/{screen}", findings)
        if screen.startswith(("csv-", "saved-", "export")):
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                screen + " overflows the viewport"
            )
        # Opaque surfaces guarantee the same contrast without browser filter support.
        assert await page.locator(".glass-panel, .glass-bar").evaluate_all(
            "elements => elements.every(e => /^rgb\\(/.test(getComputedStyle(e).backgroundColor))"
        )
        fallback = await page.add_style_tag(
            content=(
                "* { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }"
            )
        )
        await run_axe(page, f"{theme}-{width}/{screen}-fallback", findings)
        await fallback.evaluate("element => element.remove()")
        if shots:
            await page.screenshot(path=f"/tmp/a11y_{theme}_{width}_{screen}.png")

    await page.goto(UI)
    await page.wait_for_selector("text=Search and collect", timeout=15000)
    await page.get_by_text("Search tips", exact=True).click()
    guide = page.get_by_role("link", name=re.compile("X query guide"))
    await expect(guide).to_have_attribute(
        "href", "https://docs.x.com/x-api/posts/search/integrate/build-a-query"
    )
    await expect(guide).to_have_attribute("target", "_blank")
    if theme == "dark":
        await page.click("[aria-label='Switch to dark mode']")
        await page.wait_for_timeout(200)
    await audit("collect")

    await page.get_by_role("tab", name="Search X").focus()
    await page.keyboard.press("ArrowRight")
    await page.keyboard.press("ArrowRight")
    await expect(page.get_by_role("tab", name="Upload CSV")).to_be_focused()
    await audit("csv-empty")
    await page.get_by_text("CSV example and formatting rules").click()
    await audit("csv-guidance")
    # The visible button must open the file chooser with keyboard activation.
    await page.get_by_role("button", name="Choose CSV file", exact=True).focus()
    async with page.expect_file_chooser() as chooser:
        await page.keyboard.press("Enter")
    await (await chooser.value).set_files(
        {"name": "invalid.csv", "mimeType": "text/csv", "buffer": b"body\nmissing text header\n"}
    )
    await expect(page.get_by_role("alert")).to_contain_text("text")
    await expect(page.get_by_role("button", name="Import CSV")).to_be_disabled()
    await audit("csv-error")
    transfer = await page.evaluate_handle(
        """text => {
        const transfer = new DataTransfer();
        transfer.items.add(new File([text], "d.csv", {type: "text/csv"}));
        return transfer;
    }""",
        sample_csv().decode(),
    )
    dropzone = page.locator("input[type=file]").locator("..")
    await dropzone.dispatch_event("dragover", {"dataTransfer": transfer})
    await audit("csv-drag")
    await dropzone.dispatch_event("drop", {"dataTransfer": transfer})
    await expect(page.get_by_role("status").filter(has_text="CSV validated")).to_be_visible()
    await expect(page.get_by_role("button", name="Import CSV")).to_be_enabled()
    await audit("csv-ready")
    await page.click("button[type=submit]")
    await page.wait_for_selector("text=Collection results", timeout=15000)
    await audit("collected")

    # Keep the archive download covered, then open the automatically saved local JSON.
    async with page.expect_download() as original_download:
        await page.get_by_role("button", name="Download original dataset").click()
    downloaded = await original_download.value
    assert re.fullmatch(
        r"csv-import-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-UTC-0[56]00\.json",
        downloaded.suggested_filename,
    )
    original_path = await downloaded.path()
    assert original_path
    await page.get_by_text("Start another collection", exact=True).click()
    await page.get_by_role("tab", name="Saved datasets").click()
    await audit("saved-dataset")
    picker = page.get_by_role("combobox", name="Saved dataset", exact=True)
    await expect(picker).to_be_enabled()
    await expect(page.get_by_role("button", name="Open dataset →")).to_be_disabled()
    await picker.select_option(index=1)
    await audit("saved-selected")
    await page.locator("button[type=submit]").click()
    await page.wait_for_selector("text=Normalise for NLP")
    await audit("clean")
    assert await page.locator("main input[type=checkbox]:checked").count() == 0
    await page.locator("button:visible:has-text('Continue to Analyze')").first.click()
    await page.wait_for_selector(".ag-row", timeout=40000)
    await expect(page.locator("main h2").first).to_be_focused()
    await audit("analyze-original")
    await expect(page.locator(".record-diff-action")).to_have_count(0)
    await page.click("text=Adjust steps")
    await page.wait_for_selector("text=Normalise for NLP")
    await expect(page.locator("main h2").first).to_be_focused()
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

    # Advancing without a decision must not inflate completion.
    await page.get_by_role("button", name="Next →", exact=True).click()
    await expect(page.get_by_text("1 of 6 reviewed", exact=False)).to_be_visible()
    await page.get_by_role("button", name="Save and pause review").click()
    await page.get_by_role("heading", name="Review paused — labels saved").wait_for()
    await audit("review-paused")
    await page.get_by_role("button", name="Return to selected review").click()
    await page.locator("blockquote").wait_for()
    await expect(page.get_by_role("button", name=re.compile(r"^negative", re.I))).to_have_attribute(
        "aria-pressed", "true"
    )
    # Correct the first saved decision, then complete the selection with scoped shortcuts.
    for _ in range(6):
        await page.keyboard.press("1")
    await expect(page.get_by_role("status").filter(has_text="All 6 selected")).to_be_visible()
    await audit("review-unsaved-end")
    await page.get_by_role("button", name="Save and finish review").click()
    await page.get_by_role("heading", name="Review complete — labels saved").wait_for()
    await expect(
        page.get_by_text("6 of 6 selected posts have saved manual labels.", exact=False)
    ).to_be_visible()
    await audit("review-complete")
    await page.click("text=Continue to Export")
    await page.wait_for_selector("h2:has-text('Export')", timeout=15000)
    await page.wait_for_timeout(600)
    await audit("export")

    custom = page.get_by_role("checkbox", name="Custom filename for CSV", exact=True)
    name = page.get_by_role("textbox", name="CSV filename", exact=True)
    await expect(name).to_have_attribute("readonly", "")
    await custom.focus()
    await page.keyboard.press("Space")
    await page.keyboard.press("Tab")
    await expect(name).to_be_focused()
    await name.fill("results.csv")
    await expect(name).to_have_attribute("aria-invalid", "true")
    await expect(page.get_by_role("button", name="Download CSV", exact=True)).to_be_disabled()
    await audit("export-invalid-name")
    await name.fill("My reviewed posts")
    await page.keyboard.press("Tab")
    await expect(page.get_by_role("button", name="Download CSV", exact=True)).to_be_focused()
    async with page.expect_download() as renamed_download:
        await page.keyboard.press("Enter")
    exported = await renamed_download.value
    assert exported.suggested_filename == "My reviewed posts.csv"
    path = await exported.path()
    assert path
    content = await asyncio.to_thread(pathlib.Path(path).read_text, encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    reviewed = [row for row in rows if row["label_source"] == "manual"]
    assert len(reviewed) == 6 and all(row["label"] == "positive" for row in reviewed)
    await cloud_download(page, content)
    await audit("export-custom-name")


async def cloud_download(page: Page, content: str) -> None:
    """Exercise the real client against a cloud response and a mocked private S3 attachment."""
    export_path = "**/dataset/*/export.csv*"
    object_url = "https://downloads.example.test/_downloads/test/My-reviewed-posts.csv"

    async def link(route: Route) -> None:
        await route.fulfill(
            content_type="application/vnd.sentiment-prep.download+json",
            body=json.dumps(
                {"url": object_url, "filename": "My reviewed posts.csv", "expires_in": 300}
            ),
        )

    async def attachment(route: Route) -> None:
        assert "authorization" not in route.request.headers
        assert "referer" not in route.request.headers
        await route.fulfill(
            content_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="My reviewed posts.csv"'},
            body=content.encode(),
        )

    await page.route(export_path, link)
    await page.route(object_url, attachment)
    previous_url = page.url
    try:
        async with page.expect_download() as pending:
            await page.get_by_role("button", name="Download CSV", exact=True).click()
        exported = await pending.value
        assert exported.suggested_filename == "My reviewed posts.csv"
        path = await exported.path()
        assert path and await asyncio.to_thread(pathlib.Path(path).read_text) == content
        assert page.url == previous_url, "The attachment must leave the review screen open"
    finally:
        await page.unroute(export_path, link)
        await page.unroute(object_url, attachment)
    print("Cloud download: private link, filename, complete bytes, and session isolation passed")


async def main() -> int:
    """Exercise general and consumer workflows in separate browser pages."""
    findings: list[Finding] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=os.environ.get("A11Y_BROWSER_EXECUTABLE")
        )
        for theme, width in (("light", 1440), ("dark", 1440), ("dark", 390)):
            page = await browser.new_page(
                viewport={"width": width, "height": 900},
                timezone_id="America/Chicago",
                reduced_motion="reduce",
            )
            if "--consumer-only" not in sys.argv:
                await walk_stages(page, theme, findings, shots="--screenshots" in sys.argv)
            await page.close()
        for theme in ("light", "dark"):
            for width in (1440, 390, 320):
                page = await browser.new_page(
                    viewport={"width": width, "height": 900},
                    timezone_id="America/Chicago",
                    reduced_motion="reduce",
                )

                async def audit_consumer(
                    screen: str, page: Page = page, theme: str = theme, width: int = width
                ) -> None:
                    await run_axe(page, f"{theme}-{width}/{screen}", findings)
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), screen + " overflows the viewport"

                await walk_consumer(page, UI, theme, audit_consumer)
                await page.close()
        await browser.close()

    print(f"violations: {len(findings)}")
    for finding in findings:
        print(*finding, sep=" | ")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
