#!/usr/bin/env python3
"""
rag_store.py — ChromaDB vector store for The Firm's RAG pipeline
Three collections: disclosures, member_profiles, market_context
Embedding model: all-MiniLM-L6-v2 (local, free, runs on Pi 5)
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger('RAG')

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMA_DIR = os.path.join(BASE_DIR, 'data', 'chroma_db')

# Lazy globals — initialized on first use
_client     = None
_embedder   = None
_col_disc   = None   # disclosures collection
_col_prof   = None   # member profiles collection
_col_ctx    = None   # market context collection


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        log.info('[RAG] Loading embedding model...')
        _embedder = SentenceTransformer('all-MiniLM-L6-v2')
        log.info('[RAG] Embedding model ready')
    return _embedder


def _get_client():
    global _client
    if _client is None:
        import chromadb
        os.makedirs(CHROMA_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    return _client


def init_store():
    """Initialize all three collections. Safe to call multiple times."""
    global _col_disc, _col_prof, _col_ctx
    client = _get_client()
    _col_disc = client.get_or_create_collection(
        name='disclosures',
        metadata={'description': 'STOCK Act congressional trade disclosures'}
    )
    _col_prof = client.get_or_create_collection(
        name='member_profiles',
        metadata={'description': 'Congressional member trade profiles'}
    )
    _col_ctx = client.get_or_create_collection(
        name='market_context',
        metadata={'description': 'Market/macro context events'}
    )
    log.info(f'[RAG] Store initialized — disclosures: {_col_disc.count()}, profiles: {_col_prof.count()}, context: {_col_ctx.count()}')


def _ensure_init():
    if _col_disc is None:
        init_store()


def _embed(texts: list) -> list:
    embedder = _get_embedder()
    return embedder.encode(texts, convert_to_numpy=True).tolist()


def _doc_id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ── Add documents ─────────────────────────────────────────────────────────────

def add_disclosure(member: str, ticker: str, trade_type: str, amount: str,
                   date: str, committees: str = '', specialty: str = '',
                   score: int = 0, outcome: str = '') -> str:
    """Embed and store a congressional trade disclosure."""
    _ensure_init()
    text = (f"{member} | {ticker} | {trade_type} | {amount} | {date} | "
            f"committees: {committees} | specialty: {specialty}")
    if outcome:
        text += f" | outcome: {outcome}"
    doc_id = _doc_id(f"{member}{ticker}{date}{trade_type}")
    embedding = _embed([text])[0]
    _col_disc.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[{
            'member': member, 'ticker': ticker, 'trade_type': trade_type,
            'amount': amount, 'date': date, 'score': score, 'outcome': outcome
        }]
    )
    return doc_id


def add_member_profile(member: str, profile_text: str, score: int = 0,
                       specialties: str = '') -> str:
    """Embed and store a member profile summary."""
    _ensure_init()
    doc_id = _doc_id(f"profile_{member}")
    embedding = _embed([profile_text])[0]
    _col_prof.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[profile_text],
        metadatas=[{'member': member, 'score': score, 'specialties': specialties}]
    )
    return doc_id


def add_market_context(text: str, date: str, category: str = '',
                       tickers: str = '') -> str:
    """Embed and store a market context event."""
    _ensure_init()
    doc_id = _doc_id(f"{date}{text[:50]}")
    embedding = _embed([text])[0]
    _col_ctx.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[{'date': date, 'category': category, 'tickers': tickers}]
    )
    return doc_id


def update_outcome(member: str, ticker: str, date: str, trade_type: str,
                   outcome: str):
    """Update the outcome field on an existing disclosure after resolution."""
    _ensure_init()
    doc_id = _doc_id(f"{member}{ticker}{date}{trade_type}")
    try:
        existing = _col_disc.get(ids=[doc_id])
        if existing['documents']:
            old_text = existing['documents'][0]
            new_text = old_text.split(' | outcome:')[0] + f' | outcome: {outcome}'
            meta = existing['metadatas'][0]
            meta['outcome'] = outcome
            embedding = _embed([new_text])[0]
            _col_disc.upsert(ids=[doc_id], embeddings=[embedding],
                             documents=[new_text], metadatas=[meta])
            log.info(f'[RAG] Updated outcome for {member} {ticker}: {outcome}')
    except Exception as e:
        log.warning(f'[RAG] update_outcome failed: {e}')


# ── Retrieve ──────────────────────────────────────────────────────────────────

def _cosine_similarity(a: list, b: list) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def search(member: str, ticker: str, trade_type: str, n_results: int = 5) -> dict:
    """
    Multi-query retrieval across all three collections.
    Returns assembled context dict for LLM consumption.
    """
    _ensure_init()

    # Generate multiple query perspectives
    queries = [
        f"{member} {ticker} {trade_type}",
        f"{member} committee activity {ticker} semiconductor defense energy",
        f"{ticker} congressional trades outcomes performance",
        f"{member} track record investment returns",
    ]

    embeddings = _embed(queries)

    # Retrieve from each collection with each query
    candidates = {}  # doc_id → {text, metadata, max_score}

    try:
        n_fetch = min(3, max(1, _col_disc.count()))
        for i, (q, emb) in enumerate(zip(queries, embeddings)):
            results = _col_disc.query(
                query_embeddings=[emb],
                n_results=n_fetch,
                include=['documents', 'metadatas', 'embeddings']
            )
            for j, (doc, meta, doc_emb) in enumerate(zip(
                results['documents'][0],
                results['metadatas'][0],
                results['embeddings'][0]
            )):
                doc_id = _doc_id(doc[:50])
                sim = _cosine_similarity(emb, doc_emb)
                if doc_id not in candidates or candidates[doc_id]['score'] < sim:
                    candidates[doc_id] = {
                        'text': doc, 'meta': meta, 'score': sim,
                        'collection': 'disclosures'
                    }
    except Exception as e:
        log.warning(f'[RAG] Disclosure search error: {e}')

    # Member profile — use .get() for exact member lookup (query+where has ChromaDB issues)
    profile_text = ''
    try:
        if _col_prof.count() > 0:
            results = _col_prof.get(
                where={'member': member},
                include=['documents']
            )
            if results['documents']:
                profile_text = results['documents'][0]
    except Exception as e:
        log.warning(f'[RAG] Profile search error: {e}')

    # Market context
    context_chunks = []
    try:
        if _col_ctx.count() > 0:
            ctx_emb = _embed([f"{ticker} market news legislation"])[0]
            results = _col_ctx.query(
                query_embeddings=[ctx_emb],
                n_results=2,
                include=['documents']
            )
            context_chunks = results['documents'][0]
    except Exception as e:
        log.warning(f'[RAG] Context search error: {e}')

    # Rerank: sort by score, deduplicate, take top n
    ranked = sorted(candidates.values(), key=lambda x: (
        x['score'] * 0.6 +                                           # semantic similarity
        (0.2 if x['meta'].get('outcome') else 0) +                   # outcome known
        (0.2 * min(x['meta'].get('score', 0) / 30, 1.0))            # member score weight
    ), reverse=True)[:n_results]

    return {
        'prior_disclosures': [r['text'] for r in ranked],
        'prior_outcomes': [r['meta'].get('outcome', '') for r in ranked],
        'member_profile': profile_text,
        'market_context': context_chunks,
        'total_found': len(candidates),
    }


def store_stats() -> dict:
    """Return store statistics."""
    _ensure_init()
    return {
        'disclosures': _col_disc.count(),
        'profiles': _col_prof.count(),
        'context': _col_ctx.count(),
        'chroma_dir': CHROMA_DIR,
    }
