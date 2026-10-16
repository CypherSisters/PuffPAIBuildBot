"""PuffPAI PII Classifier — shim module.

The ingestion pipeline imports ``PIIClassifier`` from this module to scan
uploaded documents for personally identifiable information so that anything
carrying a personal identifier gets auto-routed to the Zone-1 vault instead of
being indexed as a searchable note in plain text.

The actual implementation lives in :class:`~puffpai.security.data_classifier.DataClassifier`,
which already provides ``scan_for_pii`` and ``classify_content``. This module
just re-exports it under the expected name so callers can depend on a stable
``PIIClassifier`` symbol while the detector implementation is free to evolve.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from .data_classifier import DataClassifier, PIIDetection

# Public alias — callers import ``PIIClassifier``.
PIIClassifier = DataClassifier

__all__ = ["PIIClassifier", "PIIDetection"]
