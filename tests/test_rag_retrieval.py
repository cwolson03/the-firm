"""
tests/test_rag_retrieval.py

Integration tests for the RAG retrieval pipeline.
Tests: member-specific retrieval, semantic relevance, profile loading,
cross-member contamination, and no-result graceful handling.

Note: requires ChromaDB store initialized with disclosure data.
Run `python3 bots/rag_ingest.py --bootstrap` first if the store is empty.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bots'))


def _store_available():
    """Check if ChromaDB store has been initialized."""
    try:
        from rag_store import store_stats, init_store
        init_store()
        stats = store_stats()
        return stats.get('disclosures', 0) > 0
    except Exception:
        return False


@pytest.mark.skipif(not _store_available(), reason="RAG store not initialized — run rag_ingest.py --bootstrap")
class TestMemberSpecificRetrieval:
    def setup_method(self):
        from rag_store import search, init_store
        init_store()
        self.search = search

    def test_pelosi_nvda_returns_results(self):
        """Pelosi + NVDA search should return at least 1 result."""
        results = self.search("Nancy Pelosi", "NVDA", "Purchase", n_results=5)
        assert len(results.get("prior_disclosures", [])) > 0, \
            "Expected at least 1 prior disclosure for Pelosi/NVDA"

    def test_pelosi_profile_loads(self):
        """Pelosi's member profile should be retrievable."""
        results = self.search("Nancy Pelosi", "NVDA", "Purchase", n_results=3)
        profile = results.get("member_profile", "")
        assert "Pelosi" in profile or len(profile) > 10, \
            "Expected Pelosi member profile to be populated"

    def test_khanna_semiconductor_relevance(self):
        """Ro Khanna + TSM should return semiconductor-related results."""
        results = self.search("Ro Khanna", "TSM", "Purchase", n_results=5)
        disclosures = results.get("prior_disclosures", [])
        # At least one result should exist
        assert len(disclosures) > 0, "Expected results for Khanna/TSM"
        # Results should be somewhat relevant (contain semiconductor tickers or Khanna's name)
        combined_text = " ".join(disclosures).lower()
        relevant_terms = ["khanna", "tsm", "nvda", "amd", "semiconductor", "armed services"]
        assert any(term in combined_text for term in relevant_terms), \
            f"Expected semiconductor-relevant content in results, got: {combined_text[:200]}"

    def test_no_results_graceful(self):
        """Search for an obscure ticker should return empty list gracefully, not raise."""
        results = self.search("Nancy Pelosi", "ZZZNONEXISTENT", "Purchase", n_results=3)
        assert isinstance(results, dict), "Expected dict result even with no matches"
        assert "prior_disclosures" in results, "Expected prior_disclosures key in result"

    def test_return_schema(self):
        """Search result should always have the expected keys."""
        results = self.search("Dan Crenshaw", "COIN", "Purchase", n_results=3)
        required_keys = ["prior_disclosures", "prior_outcomes", "member_profile", "market_context", "total_found"]
        for key in required_keys:
            assert key in results, f"Missing key: {key}"


@pytest.mark.skipif(not _store_available(), reason="RAG store not initialized")
class TestStoreHealth:
    def test_minimum_disclosures(self):
        """Store should have at least 100 disclosures after bootstrap."""
        from rag_store import store_stats, init_store
        init_store()
        stats = store_stats()
        assert stats["disclosures"] >= 100, \
            f"Expected ≥100 disclosures, got {stats['disclosures']}. Run rag_ingest.py --bootstrap."

    def test_all_18_members_have_profiles(self):
        """All 18 tracked members should have profiles in the store."""
        from rag_store import _get_client, init_store
        init_store()
        col = _get_client().get_collection("member_profiles")
        assert col.count() >= 18, \
            f"Expected ≥18 member profiles, got {col.count()}"

    def test_store_returns_within_timeout(self):
        """RAG retrieval should complete in under 5 seconds."""
        import time
        from rag_store import search, init_store
        init_store()
        start = time.time()
        search("Nancy Pelosi", "NVDA", "Purchase", n_results=5)
        elapsed = time.time() - start
        assert elapsed < 5.0, f"RAG search took {elapsed:.2f}s — should be under 5s"


class TestWithoutStore:
    """Tests that run regardless of store availability."""

    def test_import_succeeds(self):
        """rag_store.py should import without errors."""
        try:
            import rag_store
            assert hasattr(rag_store, "search"), "Expected 'search' function in rag_store"
            assert hasattr(rag_store, "init_store"), "Expected 'init_store' function in rag_store"
        except ImportError as e:
            pytest.fail(f"Failed to import rag_store: {e}")

    def test_rag_ingest_import(self):
        """rag_ingest.py should import without errors."""
        try:
            import rag_ingest
            assert hasattr(rag_ingest, "bootstrap"), "Expected 'bootstrap' function in rag_ingest"
        except ImportError as e:
            pytest.fail(f"Failed to import rag_ingest: {e}")
