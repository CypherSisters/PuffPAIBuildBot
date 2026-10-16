"""PuffPAI knowledge — RAG, ingestion, and offline ZIM knowledge packs (Zone 2).

All code in this package is original: the ZIM reader is built from the public ZIM
format spec, and there are no GPL dependencies.
"""

from .zim_reader import ZIMReader
from .ingestion import KnowledgeIngestion
from .registry import DocumentRegistry
from .discovery import discover_zim_files, get_zim_metadata, discover_from_config
from .rag import DualZoneRAG, RAGResponse, RAGResult, ResultSource
from .auto_index import (
    PackIndex,
    find_new_packs,
    sync_new_packs,
    start_background_index,
)

__all__ = [
    "ZIMReader",
    "KnowledgeIngestion",
    "DocumentRegistry",
    "discover_zim_files",
    "get_zim_metadata",
    "discover_from_config",
    "DualZoneRAG",
    "RAGResponse",
    "RAGResult",
    "ResultSource",
    "PackIndex",
    "find_new_packs",
    "sync_new_packs",
    "start_background_index",
]
