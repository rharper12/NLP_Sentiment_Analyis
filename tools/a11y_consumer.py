"""Browser coverage for the consumer review loop using an isolated in-memory dataset.

Intercept every API request before navigation. The real review and counting services apply
decisions, while collection adds one synthetic candidate without contacting a provider.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, Route, expect
from sentiment_prep import eligibility
from sentiment_prep.api.collection import summarize
from sentiment_prep.api.routes import eligibility_page
from sentiment_prep.eligibility import EligibilityItem
from sentiment_prep.labeling import service as labeling
from sentiment_prep.labeling.service import ManualLabel
from sentiment_prep.models import (
    ConsumerPolicy,
    Dataset,
    DatasetBundle,
    Record,
    ScreeningSuggestion,
)
from sentiment_prep.storage.repository import InMemoryRepository

Audit = Callable[[str], Awaitable[None]]


class ConsumerFixture:
    """Small, fully reviewed first batch that is one included record short of its target."""

    def __init__(self, ui: str) -> None:
        self.origin = urlparse(ui).netloc
        self.repo = InMemoryRepository()
        self.additional_requests = 0
        self.unexpected: list[str] = []
        self.repo.save(
            DatasetBundle(
                dataset_id="consumer-a11y",
                original=Dataset(
                    source_type="x",
                    query='"coffee maker"',
                    consumer_policy=ConsumerPolicy(
                        version="consumer-reactions-v2",
                        start_date=date(2026, 9, 9),
                        end_date=date(2026, 9, 10),
                        reviewed_target=2,
                    ),
                    records=[
                        Record(
                            id=f"reviewed-{i}",
                            author_id=f"author-{i}",
                            source_type="x",
                            text="I like the coffee maker." if i == 0 else "Product news headline.",
                            created_at=datetime(2026, 9, 9, 12, tzinfo=UTC),
                            eligibility="include" if i == 0 else "exclude",
                            eligibility_reviewed=True,
                            eligibility_reason=None if i == 0 else "news_or_article",
                            label="positive" if i == 0 else None,
                            label_source="manual" if i == 0 else None,
                            sentiment_reviewed=i == 0,
                        )
                        for i in range(20)
                    ],
                ),
            )
        )

    def summary(self) -> dict[str, object]:
        """Retain real counting semantics while supplying a synthetic resumable source."""
        bundle = self.repo.get("consumer-a11y")
        return (
            summarize(bundle)
            .model_copy(
                update={"can_collect_more": True, "candidate_target": len(bundle.original.records)}
            )
            .model_dump(mode="json")
        )

    async def respond(self, route: Route) -> None:
        """Allow static preview assets; fulfil or reject every other request locally."""
        request = route.request
        url = urlparse(request.url)
        path = url.path.removeprefix("/api")
        if (
            url.netloc == self.origin
            and not url.path.startswith("/api")
            and request.resource_type not in {"fetch", "xhr"}
            and request.method == "GET"
        ):
            await route.continue_()
            return
        if url.path == "/inter/inter.css":
            await route.fulfill(status=200, content_type="text/css", body="")
            return
        data: object
        if path == "/health":
            data = {
                "status": "ok",
                "version": "browser-test",
                "diagnostics": False,
                "auth_required": False,
                "x_configured": True,
                "x_cost_per_read_usd": 0.005,
                "comprehend_enabled": False,
                "local_datasets_available": False,
            }
        elif path == "/steps":
            data = []
        elif path == "/spend":
            data = {
                "today_reads": 0,
                "today_cost_usd": 0,
                "month_reads": 0,
                "month_cost_usd": 0,
                "cap_per_fetch": 25,
                "cap_per_day": 50,
                "remaining_today": 50,
                "cost_per_read_usd": 0.005,
                "x_configured": True,
            }
        elif path in {"/dataset/load", "/dataset/consumer-a11y"}:
            data = self.summary()
        elif path == "/dataset/consumer-a11y/records":
            records = self.repo.get("consumer-a11y").original.records
            data = {
                "total": len(records),
                "offset": 0,
                "items": [
                    {"original": record.model_dump(mode="json"), "processed": None}
                    for record in records
                ],
            }
        elif path == "/dataset/consumer-a11y/candidates":
            assert request.post_data_json == {"candidate_target": 21, "confirm_cost": True}
            self.additional_requests += 1
            assert self.additional_requests == 1, "A shortfall must never start an automatic batch"
            with self.repo.edit("consumer-a11y") as edit:
                bundle = edit.bundle
                bundle.original.records.append(
                    Record(
                        id="new-candidate",
                        author_id="new-author",
                        source_type="x",
                        created_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
                        text="I love the coffee maker, but the price puts me off.",
                        screening=ScreeningSuggestion(
                            decision="include",
                            evidence=["A personal consumer reaction."],
                        ),
                    )
                )
                edit.save(bundle)
            data = self.summary()
        elif path == "/dataset/consumer-a11y/eligibility":
            if request.method == "PUT":
                items = [
                    EligibilityItem.model_validate(item) for item in request.post_data_json["items"]
                ]
                with self.repo.edit("consumer-a11y") as edit:
                    edit.save(eligibility.apply_decisions(edit.bundle, items))
                data = self.summary()
            else:
                query = parse_qs(url.query)
                data = eligibility_page(
                    "consumer-a11y",
                    self.repo,
                    offset=int(query.get("offset", ["0"])[0]),
                    limit=1,
                    status=query.get("status", ["all"])[0],
                ).model_dump(mode="json")
        elif path == "/dataset/consumer-a11y/labels/manual":
            items = [ManualLabel.model_validate(item) for item in request.post_data_json["items"]]
            with self.repo.edit("consumer-a11y") as edit:
                edit.save(labeling.apply_manual_labels(edit.bundle, items))
            data = labeling.summary(self.repo.get("consumer-a11y")).model_dump(mode="json")
        else:
            self.unexpected.append(f"{request.method} {path}")
            await route.abort()
            return
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(data),
            headers={"Access-Control-Allow-Origin": "*"},
        )


async def walk_consumer(page: Page, ui: str, theme: str, audit: Audit) -> None:
    """Request one candidate explicitly, confirm both reviews, and reach the export target."""
    fixture = ConsumerFixture(ui)
    await page.route("**/*", fixture.respond)
    await page.goto(ui)
    collect_heading = page.get_by_role("heading", name="Collect your dataset", exact=True)
    await expect(collect_heading).to_be_visible()
    await expect(collect_heading).not_to_be_focused()
    if theme == "dark":
        await page.get_by_role("button", name="Switch to dark mode").click()
    await page.get_by_label(re.compile("Collection option")).select_option("consumer")
    await expect(page.get_by_label("Topic", exact=True)).to_have_value("")
    await expect(page.get_by_label("From", exact=True)).to_have_value("")
    await expect(page.get_by_label("To", exact=True)).to_have_value("")
    await page.get_by_label("Topic", exact=True).fill('"coffee maker"')
    await page.get_by_label("From", exact=True).fill("2026-09-09")
    await page.get_by_label("To", exact=True).fill("2026-09-10")
    timezone = page.get_by_role("combobox", name="Timezone", exact=True)
    await timezone.select_option("America/Chicago")
    await timezone.focus()
    await page.keyboard.press("u")
    await page.keyboard.press("Enter")
    await expect(timezone).to_have_value("UTC")
    await timezone.select_option("America/Chicago")
    await page.get_by_label("Final reviewed target", exact=True).fill("2")
    await page.get_by_label(re.compile("Candidate target")).fill("20")
    await audit("consumer-collect")
    await page.get_by_role("button", name="Search and collect", exact=True).click()
    await page.get_by_role("button", name="Review eligibility →", exact=True).click()
    queue = page.get_by_label(re.compile("Review queue"))
    await page.get_by_label(re.compile("Eligibility decision")).select_option("exclude")
    await page.get_by_label(re.compile("Eligibility reason")).select_option("off_topic")
    await page.get_by_role("button", name="Save eligibility decision", exact=True).click()
    await expect(page.get_by_text("0 of 2 final reviewed examples", exact=False)).to_be_visible()
    await queue.select_option("exclude")
    await expect(page.locator("blockquote")).to_have_text("I like the coffee maker.")
    await audit("consumer-saved-exclusion")
    await page.get_by_label(re.compile("Eligibility decision")).select_option("include")
    await page.get_by_role("button", name="Save eligibility decision", exact=True).click()
    await queue.select_option("sentiment")
    await expect(page.locator("blockquote")).to_have_text("I like the coffee maker.")
    await page.get_by_role("button", name="positive (1)", exact=True).click()
    await expect(page.get_by_text("1 of 2 final reviewed examples", exact=False)).to_be_visible()
    await queue.select_option("pending")
    await expect(page.get_by_label("Additional candidate quota")).to_have_value("1")
    await expect(page.get_by_text("1 of 2 final reviewed examples", exact=False)).to_be_visible()
    await audit("consumer-shortfall")
    request = page.get_by_role("button", name="Collect additional candidates", exact=True)
    await request.focus()
    await page.keyboard.press("Enter")
    heading = page.get_by_role("heading", name="Review consumer reactions", exact=True)
    await expect(heading).to_be_focused()
    await queue.select_option("pending")
    await expect(
        page.get_by_text("1 eligibility reviews and 0 sentiment reviews", exact=False)
    ).to_be_visible()
    await audit("consumer-eligibility")
    await page.get_by_label(re.compile("Eligibility decision")).select_option("exclude")
    save = page.get_by_role("button", name="Save eligibility decision", exact=True)
    await expect(save).to_be_disabled()
    await audit("consumer-exclusion-required")
    await page.get_by_label(re.compile("Eligibility decision")).select_option("include")
    await save.focus()
    await page.keyboard.press("Enter")
    await expect(page.get_by_text("No records in this queue", exact=False)).to_be_visible()
    await queue.select_option("sentiment")
    await expect(page.locator("blockquote")).to_have_text("I like the coffee maker.")
    await page.get_by_role("button", name="Next →", exact=True).click()
    await expect(page.locator("blockquote")).to_have_text(
        "I love the coffee maker, but the price puts me off."
    )
    mixed = page.get_by_role("button", name="mixed (4)", exact=True)
    await expect(mixed).to_be_enabled()
    await mixed.focus()
    await page.keyboard.press("4")
    await expect(page.get_by_text("2 of 2 final reviewed examples", exact=False)).to_be_visible()
    await expect(request).to_have_count(0)
    await audit("consumer-target-reached")
    await page.get_by_role("button", name="Continue to Export →", exact=True).focus()
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("heading", name="Export", exact=True)).to_be_focused()
    await expect(
        page.get_by_role("button", name="Download Reviewed consumer Parquet", exact=True)
    ).to_be_enabled()
    await audit("consumer-export")
    await (
        page.get_by_role("navigation", name="Progress", exact=True)
        .get_by_role("button", name=re.compile("Analyze"))
        .focus()
    )
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("heading", name="What changed", exact=True)).to_be_focused()
    await (
        page.get_by_role("navigation", name="Progress", exact=True)
        .get_by_role("button", name=re.compile("Collect"))
        .focus()
    )
    await page.keyboard.press("Enter")
    await expect(collect_heading).to_be_focused()
    assert fixture.additional_requests == 1
    assert not fixture.unexpected, fixture.unexpected
    assert len(eligibility.reviewed_records(fixture.repo.get("consumer-a11y"))) == 2
