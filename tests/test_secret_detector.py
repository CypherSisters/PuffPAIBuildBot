"""The key detector: fake keys are found, normal text is left alone.

Every fixture here is fake on purpose (EXAMPLE, 0000).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os

import pytest

from puffpai.security import secret_detector as sd

FAKE = {
    "openai_style_key": "sk-proj-EXAMPLE0000000000000000000000",
    "stripe_style_key": "sk_test_EXAMPLE00000000000000",
    "github_token": "ghp_EXAMPLE000000000000000000000000000000",
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "slack_token": "xoxb-0000000000-EXAMPLE",
    "google_api_key": "AIzaEXAMPLE0000000000000000000000000000",
    "jwt": "eyJhbGciOiJFWEFNUExFIn0.eyJzdWIiOiJFWEFNUExFIn0.EXAMPLE0000sig",
}


@pytest.mark.parametrize("kind,value", sorted(FAKE.items()))
def test_each_shape_is_found_and_held(kind, value):
    text = f"Here is my thing {value} for later."
    hits = sd.find(text)
    assert [h.kind for h in hits] == [kind]
    assert hits[0].value == value
    redacted, _ = sd.redact(text)
    assert value not in redacted
    assert sd.HELD in redacted


def test_pem_block_env_line_assignment_and_url_password():
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEXAMPLE0000\n-----END RSA PRIVATE KEY-----\n"
        "API_TOKEN=EXAMPLE0000abcd\n"
        "db:\n  password: EXAMPLE-pass-0000\n"
        '{"client_secret": "EXAMPLE00000000"}\n'
        "url = https://someone:EXAMPLE0000@db.example.invalid/x\n"
    )
    kinds = [h.kind for h in sd.find(text)]
    assert kinds == ["private_key", "env_line", "secret_assignment",
                     "secret_assignment", "url_password"]
    redacted, hits = sd.redact(text)
    for h in hits:
        assert h.value not in redacted
    # the setting's name stays searchable, only the value is held
    assert "API_TOKEN=" + sd.HELD in redacted
    assert "password: " + sd.HELD in redacted
    assert hits[1].line == 4


def test_normal_text_is_left_alone():
    text = (
        "The password must be at least eight characters long.\n"
        "Set password = <your password here> in the file.\n"
        "API_KEY=${API_KEY}\n"
        "token: true\n"
        "Skiing in the Alps is fun; ask-me-anything about it.\n"
        "Call me at the office, the risk-assessment is due.\n"
    )
    assert sd.find(text) == []
    assert sd.redact_only(text) == text


def test_summary_never_holds_values():
    hits = sd.find("a " + FAKE["github_token"] + " b " + FAKE["aws_access_key"])
    line = sd.summary(hits)
    assert "EXAMPLE" not in line
    assert "github_token x1" in line and "aws_access_key x1" in line


def test_downloaded_book_pages_lose_key_shapes(tmp_path):
    from puffpai.knowledge import text_books
    record = text_books.write_book(str(tmp_path), {
        "id": "example:1", "title": "Example", "license": "CC0"},
        [{"title": "Setup", "url": "", "text": "Use AKIAIOSFODNN7EXAMPLE to sign in."}])
    page = (tmp_path / os.path.basename(record["_path"]) / "text" / "0001.txt")
    body = page.read_text(encoding="utf-8")
    assert "AKIAIOSFODNN7EXAMPLE" not in body and sd.HELD in body
