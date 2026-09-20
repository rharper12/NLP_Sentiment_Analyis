"""Every ingress and persistence boundary rejects ambiguous record identities."""

import json

import pytest

from sentiment_prep.config import Settings
from sentiment_prep.errors import ValidationError
from sentiment_prep.export.rows import bundle_rows
from sentiment_prep.labeling.service import ManualLabel, apply_manual_labels, label_with_comprehend
from sentiment_prep.models import Dataset, DatasetBundle, Record
from sentiment_prep.sources.csv_upload import CsvUploadSource
from sentiment_prep.storage.repository import InMemoryRepository, S3Repository
from tests.conftest import FakeComprehend


@pytest.mark.parametrize(
    "rows",
    [
        "same,one opinion\nsame,another opinion\n",
        "same,identical\nsame,identical\n",
        ",first generated\ncsv-0,supplied collision\n",
        "csv-1,supplied first\n,generated collision\n",
    ],
)
def test_duplicate_csv_ids_are_rejected_precisely(rows):
    with pytest.raises(
        ValidationError, match=r"duplicate record id .*CSV row 3; first used at CSV row 2"
    ):
        CsvUploadSource(("id,text\n" + rows).encode()).fetch(20)


def test_domain_validation_covers_other_sources_and_restored_data():
    records = [Record(id="same", text=t, source_type="x") for t in ("one", "two")]
    with pytest.raises(ValidationError, match="duplicate record id"):
        Dataset(records=records, source_type="x")
    with pytest.raises(ValidationError, match="duplicate record id"):
        DatasetBundle.model_validate(
            {
                "dataset_id": "d",
                "original": {"records": [r.model_dump() for r in records], "source_type": "x"},
            }
        )


def test_repositories_revalidate_model_copies_and_legacy_s3_objects(s3_bucket):
    client, bucket = s3_bucket
    valid = Dataset(records=[Record(id="r", text="one", source_type="csv")], source_type="csv")
    bundle = DatasetBundle(dataset_id="d", original=valid)
    invalid = bundle.model_copy(
        update={"original": valid.model_copy(update={"records": valid.records * 2})}
    )
    for repo in (InMemoryRepository(), S3Repository(bucket, client)):
        with pytest.raises(ValidationError, match="duplicate record id"):
            repo.save(invalid)
    client.put_object(
        Bucket=bucket, Key="_work/d.json", Body=json.dumps(invalid.model_dump(mode="json"))
    )
    with pytest.raises(ValidationError, match="duplicate record id"):
        S3Repository(bucket, client).get("d")


def test_valid_ids_survive_reload_manual_comprehend_and_export(s3_bucket):
    client, bucket = s3_bucket
    dataset = CsvUploadSource(
        b"id,text\n00123,I loved it\n99999999999999999999999,terrible stuff\n"
    ).fetch(10)
    bundle = DatasetBundle(dataset_id="d", original=dataset, processed=dataset)
    for repo in (InMemoryRepository(), S3Repository(bucket, client)):
        repo.save(bundle)
        with repo.edit("d") as edit:
            labelled = apply_manual_labels(edit.bundle, [ManualLabel(id="00123", label="mixed")])
            labelled, progress = label_with_comprehend(labelled, FakeComprehend(), Settings(), 25)
            edit.save(labelled)
        restored = repo.get("d")
        rows = bundle_rows(restored)
        assert [r["id"] for r in rows] == ["00123", "99999999999999999999999"]
        assert rows[0]["label"] == "mixed" and rows[1]["label"] == "negative"
        assert rows[0]["comprehend_label"] == "positive"
        assert rows[0]["processed_text"] == "I loved it"
        assert rows[1]["processed_text"] == "terrible stuff"
        assert progress.labelled_total == 2
        assert len({r["id"] for r in rows}) == len(rows)
