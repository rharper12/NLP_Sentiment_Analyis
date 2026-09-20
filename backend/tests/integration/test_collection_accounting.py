"""API summaries report settled reads and cost independently from retained records."""

import httpx
import pytest

from sentiment_prep.api import deps, routes
from sentiment_prep.api.schemas import LoadRequest
from sentiment_prep.config import Settings
from sentiment_prep.history.services import DbLedger
from sentiment_prep.models import CollectionProgress, Dataset, DatasetBundle
from sentiment_prep.sources.spend_guard import SpendGuard
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.repository import InMemoryRepository


def test_legacy_collection_does_not_invent_zero_spend():
    bundle = DatasetBundle(
        dataset_id="x-old",
        original=Dataset(source_type="x", records=[]),
        collection=CollectionProgress.model_validate({"request": {}, "reads": 100}),
    )
    result = routes._summary(bundle)
    assert result.billed_reads is None
    assert result.committed_cost_usd is None


@pytest.mark.parametrize("limit", [20, 100])
def test_filtering_and_oversized_final_pages_report_committed_cost(tmp_path, monkeypatch, limit):
    settings = Settings(_env_file=None, x_cost_per_read_usd=0.017, dedupe_enabled=False)
    ledger = DbLedger(settings.x_cost_per_read_usd)
    guard = SpendGuard(ledger, 1000, 3000, settings.x_cost_per_read_usd)
    monkeypatch.setattr(guard, "today", lambda: f"2099-01-{limit // 10:02d}")
    posts = [
        {
            "id": str(i),
            "text": f"post {i} has enough useful words",
            "lang": "en" if i < 20 else "fr",
        }
        for i in range(100)
    ]
    provider = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": posts, "meta": {}})
        ),
        base_url="https://offline.test",
    )
    monkeypatch.setattr(
        deps, "get_x_source", lambda *args, **kwargs: XSearchSource(guard, provider)
    )
    repo = InMemoryRepository()
    result = routes._collect_x(
        LoadRequest(source="x", query="accounting", limit=limit, request_id=f"billing-{limit}"),
        repo,
        LocalCheckpointStore(tmp_path),
        settings,
        lambda: False,
    )
    assert result.record_count == 20
    assert result.billed_reads == 100
    assert result.committed_cost_usd == pytest.approx(1.7)
    assert ledger.get(guard.today()) == 100
    reloaded = routes._summary(repo.get(result.dataset_id))
    assert reloaded.committed_cost_usd == result.committed_cost_usd
    provider.close()
