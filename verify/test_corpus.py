from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from django.test import SimpleTestCase

from .ingest import chunk_text, snapshot_documents
from .retrieval import retrieve


class CorpusTests(SimpleTestCase):
    def test_chunking_preserves_words_and_limits_length(self):
        words = [f"słowo{i}" for i in range(200)]
        chunks = list(chunk_text("  \n".join(words), size=120, overlap=20))
        self.assertGreater(len(chunks), 1)
        self.assertLessEqual(max(map(len, chunks)), 120)
        self.assertTrue(set(words).issubset(set(" ".join(chunks).split())))
        self.assertEqual(list(chunk_text("   ")), [])

    def test_long_unbroken_text(self):
        chunks = list(chunk_text("a" * 520, size=100, overlap=20))
        self.assertEqual([len(part) for part in chunks], [100] * 6 + [40])

    def test_snapshot_preserves_dates_and_combines_shared_url(self):
        now = datetime.now(timezone.utc)
        base = dict(canonical_url="https://odyseusz.gov.pl/", fetched_at=now, regions=[],
                    valid_from=None, valid_to=None, document_date="2026-01-10")
        with TemporaryDirectory() as path:
            folder = Path(path)
            docs = [dict(base, title="Podróże", text="Test A"),
                    dict(base, title="Przygotowanie", text="Test B")]
            water = dict(base, canonical_url="https://www.gov.pl/web/gov/ostrzezenia#/123",
                         location_name="Test", river_name="X", water_level_value="48",
                         water_level_alarm_status_value="150", water_level_warning_status_value="120")
            for records, filename in [(docs, "dokumenty.parquet"), ([water], "pomiary_wod.parquet")]:
                pq.write_table(pa.Table.from_pylist(records), folder / filename)
            first, second = list(snapshot_documents(folder))
        self.assertIn("Test A", first[2])
        self.assertIn("Test B", first[2])
        self.assertIn("2026-01-10", first[2])
        self.assertIn("Stan wody: 48", second[2])
        self.assertEqual(first[3], now)

    @patch("verify.retrieval.embed")
    @patch("verify.retrieval.Chunk.objects")
    def test_empty_corpus_skips_query_embedding(self, objects, embed):
        objects.filter.return_value.exists.return_value = False
        self.assertEqual(retrieve("Treść"), [])
        embed.assert_not_called()

    @patch("verify.retrieval.embed", return_value=[[1.0, 0.0]])
    def test_retrieval_sql_orders_by_cosine_and_limits(self, embed):
        with (
            patch.dict("os.environ", RAG_TOP_K="5"),
            patch("django.db.models.query.QuerySet.exists", return_value=True),
            patch("django.db.models.query.QuerySet._fetch_all", autospec=True) as execute,
        ):
            def record(queryset):
                self.assertIn("<=>", str(queryset.query))
                self.assertIn("ORDER BY", str(queryset.query))
                self.assertIn('"embedding" IS NOT NULL', str(queryset.query))
                self.assertEqual(queryset.query.high_mark, 15)
                queryset._result_cache = []
            execute.side_effect = record
            self.assertEqual(retrieve("Treść"), [])
        embed.assert_called_once_with(["Treść"], query=True)
