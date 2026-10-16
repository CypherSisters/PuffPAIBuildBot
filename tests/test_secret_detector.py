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


# ── settings: held for her vault, hidden from text that leaves (2026-10-15) ─

_HEX = "0123456789abcdef" * 2                   # a fake `openssl rand -hex 16`
_B64 = "EXAMPLE0000AAAAbbbbCCCCdddd0000EXAMPLE00="  # a fake `openssl rand -base64`


@pytest.mark.parametrize("line, name", [
    (f'X_KEY="{_HEX}"', 'X_KEY="'),
    ("X_SECRET='EXAMPLE-fake'", "X_SECRET='"),
    (f"APP_KEY={_HEX}", "APP_KEY="),
    (f"APP_KEY=base64:{_B64}", "APP_KEY="),
    ("POSTGRES_PASSWORD: abc123", "POSTGRES_PASSWORD: "),
    ("    environment:\n      POSTGRES_PASSWORD: two words", "POSTGRES_PASSWORD: "),
    ("      - POSTGRES_PASSWORD=abc", "- POSTGRES_PASSWORD="),
    (f"export JWT_SECRET={_HEX}", "export JWT_SECRET="),
    ("DB_PASS=fake99", "DB_PASS="),
    (f"SESSION_SALT={_HEX}", "SESSION_SALT="),
    ("AUTH=EXAMPLE", "AUTH="),
    ("PRIVATE=EXAMPLE", "PRIVATE="),
    ("DB_PASSWORD=abc", "DB_PASSWORD="),
    ("DB_PASSWORD=12345", "DB_PASSWORD="),
    ("MYSQL_ROOT_PASSWORD: \"ab\"", 'MYSQL_ROOT_PASSWORD: "'),
    (f"      SIGNING: {_HEX}", "SIGNING: "),
    (f'{{"api_key": "{_HEX}", "dbPass": "EXAMPLE"}}', '"api_key": "'),
    (f"docker run -e POSTGRES_PASSWORD=abc -e WEBHOOK={_B64} img", "POSTGRES_PASSWORD="),
])
def test_secret_settings_are_held_and_their_names_kept(line, name):
    cloud = sd.redact_only(line, anywhere=True)             # text going to a cloud model
    vault, hits = sd.redact(line)                           # her upload: values to the vault
    for out in (cloud, vault):
        assert name + sd.HELD in out, out
    for value in (_HEX, _B64, "12345", "abc123", "two words", "fake99", "EXAMPLE-fake",
                  "EXAMPLE"):
        if value in line:
            assert value not in cloud, cloud
            if "docker run" not in line:                    # an inline -e: cloud only
                assert value not in vault, vault
                assert any(value in h.value for h in hits)
    assert sd.redact_only(cloud, anywhere=True) == cloud    # idempotent
    assert sd.redact(vault)[0] == vault


def test_text_that_leaves_keeps_plain_settings():
    text = ("services:\n  db:\n    image: postgres:16\n    ports:\n      - \"5432:5432\"\n"
            "      - 127.0.0.1:55946:80\n    environment:\n"
            "      POSTGRES_USER: app\n      POSTGRES_HOST: db.example.internal\n"
            "      DATABASE_URL: postgres://db.example.internal:5432/app\n"
            "      AUTH_ENABLED: true\n      AUTH: password\n"
            "      POSTGRES_HOST_AUTH_METHOD: trust\n"
            "      DB_PASSWORD:\n      SECRET_KEY: ''\n      TOKEN_TTL: 3600\n"
            "      PORT: 8080\n      DEBUG: \"false\"\n      API_KEY: ${API_KEY}\n"
            "      COMPOSE_PROJECT_NAME: my-very-long-service-name-here\n"
            "      GIT_COMMIT: 9fc7b5bb0123456789abcdef0123456789abcdef\n"
            '{"integrity": "sha512-EXAMPLE0000AAAAbbbbCCCCdddd0000EXAMPLE00==", '
            '"private": true, "version": "1.2.3"}\n'
            "The author: Robin; compass: north; bypass: off\n")
    assert sd.redact_only(text, anywhere=True) == text
    assert sd.find(text) == []


def test_a_url_with_a_password_in_a_setting_is_still_hidden():
    out = sd.redact_only("DATABASE_URL=postgres://app:EXAMPLEpw@db:5432/app")
    assert "EXAMPLEpw" not in out and "DATABASE_URL=" in out


def test_her_files_hold_the_new_setting_shapes_in_the_vault():
    """Owner's rule: PII and keys in her own files go to the vault (find()
    is what uploads and linked folders run)."""
    hits = sd.find(f"DB_PASS=fake99\nSIGNING: {_HEX}\nAUTH=ab\n"
                   "POSTGRES_PASSWORD_FILE: /run/secrets/db_password\n")
    assert [(h.kind, h.value) for h in hits] == [
        ("secret_setting", "fake99"), ("generated_key", _HEX), ("secret_setting", "ab"),
        ("secret_assignment", "/run/secrets/db_password")]     # held, as base find() did


def test_a_long_public_id_in_a_book_page_sentence_is_not_held():
    page = ("Chapter 3. The archive copy has record id 507f1f77bcf86cd799439011, and the "
            f"build key: {_HEX} is printed on the cover. First pass: sand the board. "
            "The mountain pass: Brenner, open in May.\n"
            f"Reference: see build key {_B64} in the appendix.\n")
    assert sd.find(page) == []
    from puffpai.knowledge import text_books
    assert text_books.set_aside_orders(page) == (page, 0)  # the book page path
    # The same value on a setting line is a key (a ..._KEY name: any value).
    assert [h.kind for h in sd.find(f"build_seal: {_HEX}\n")] == ["generated_key"]
    assert [h.kind for h in sd.find("build_key: abc\n")] == ["secret_setting"]


# ── verifier probes, 2026-10-15: shapes, prose, command lines, speed ─────────

def _held_everywhere(line: str, value: str) -> None:
    """*value* goes to the vault from her files and never leaves for a cloud model."""
    assert any(value in h.value for h in sd.find(line)), line
    assert value not in sd.redact(line)[0], line
    assert value not in sd.redact_only(line, anywhere=True), line
    from puffpai.security import toolsmith_cloud
    assert value not in toolsmith_cloud.clean(line), line


@pytest.mark.parametrize("line, value", [
    ("SLACK_WEBHOOK=https://hooks.example.invalid/services/T0/B0/EXAMPLE0000webhook",
     "EXAMPLE0000webhook"),
    ("WEBHOOK_TOKEN=https://chat.example.invalid/api/webhooks/1/EXAMPLEsecret",
     "EXAMPLEsecret"),
    ("SENTRY_DSN=https://EXAMPLE0000key@o1.ingest.example.invalid/1", "EXAMPLE0000key"),
    ("API_TOKEN=/x8EXAMPLE+abc/def0123456789==", "/x8EXAMPLE+abc/def0123456789=="),
    ("SECRET_KEY=/9jEXAMPLE0000", "/9jEXAMPLE0000"),
    ("PASSWORD=./EXAMPLEpw", "./EXAMPLEpw"),
    ("ACCESS_TOKEN=//EXAMPLEtoken", "//EXAMPLEtoken"),
    ("DB_PASSWORD=C:\\EXAMPLEpw", "C:\\EXAMPLEpw"),
    ("AUTH_TOKEN=~/EXAMPLEtok", "~/EXAMPLEtok"),
    ("PASSWORD=/etc/EXAMPLEpw", "/etc/EXAMPLEpw"),
    ('"token": "/EXAMPLE0000tok"', "/EXAMPLE0000tok"),
    ("API_KEY=https://api.example.invalid/?key=EXAMPLE0000", "EXAMPLE0000"),
    ("DB_PASSWORD_URL=EXAMPLEpw", "EXAMPLEpw"),           # not a URL: a password
    ("TOKEN_URL=https://auth.example.invalid/t?token=EXAMPLEtok", "EXAMPLEtok"),
    ("AUTH_URL=https://EXAMPLEuser:EXAMPLEpw@auth.example.invalid/", "EXAMPLEpw"),
    # A secret word in the name wins over a where-suffix (base find() held these).
    ("SECRET_URL=https://hooks.example.invalid/services/T1/B2/EXAMPLEhook0",
     "EXAMPLEhook0"),
    ("API_TOKEN_URL=https://example.invalid/hook/EXAMPLE0000tok", "EXAMPLE0000tok"),
    ("WEBHOOK_SECRET_ENDPOINT=https://chat.example.invalid/api/webhooks/1/EXAMPLEw",
     "EXAMPLEw"),
    ("TOKEN_URL=https://auth.example.invalid/oauth/token", "auth.example.invalid/oauth"),
    ("SECRET_HOST=EXAMPLEsecret.value", "EXAMPLEsecret.value"),
    ("TOKEN_SERVER=abc.def.ghi", "abc.def.ghi"),
    ("PASSWORD_HOST=EXAMPLEpw.x", "EXAMPLEpw.x"),
    ("SECRET_PATH=AbC/EXAMPLE456", "AbC/EXAMPLE456"),
    ("SECRET_DIR=EXAMPLE/value", "EXAMPLE/value"),
    ("API_KEY_FILE=/run/secrets/api_key", "/run/secrets/api_key"),
    ("POSTGRES_PASSWORD_FILE=/run/secrets/db_password", "/run/secrets/db_password"),
    ("SLACK_WEBHOOK_URL=https://hooks.example.invalid/services/T0/B0/EXAMPLEslack",
     "EXAMPLEslack"),
    ("DISCORD_WEBHOOK_URL=https://chat.example.invalid/api/webhooks/123/EXAMPLEdisc",
     "EXAMPLEdisc"),
])
def test_a_value_under_a_secret_name_is_held_whatever_its_shape(line, value):
    _held_everywhere(line, value)


def test_a_one_letter_value_is_still_a_value():
    assert [h.value for h in sd.find("WEBHOOK_SECRET=x")] == ["x"]
    assert sd.redact_only("SMTP_PASSWORD=x", anywhere=True) == "SMTP_PASSWORD=" + sd.HELD
    assert sd.find("PASSWORD=xxxx") == []                   # a mask, not a password


def test_placeholders_option_words_and_public_keys_stay():
    text = ("TOKEN_TTL: 3600\n"
            "AUTH=password\n"
            "REDIS_HOST=cache.invalid\n"
            "DATABASE_URL=postgres://db.invalid:5432/app\n"
            "API_KEY=$(cat key.txt)\n"
            "PASSWORD=${DB_PASS}\n"
            "PRIMARY_KEY: id\n"
            "PUBLIC_KEY=EXAMPLEpub\n")
    assert sd.find(text) == []
    assert sd.redact_only(text, anywhere=True) == text


def test_a_recipe_page_keeps_its_words():
    """Capitalised words in a book's lines are not settings (owner: books stay
    searchable); only what base find() held before is held."""
    recipe = ("Pancakes\nFlour: 2 cups\nSalt: a pinch, to taste.\nSugar: 2 tbsp\n"
              "Pass: the batter through a sieve.\nSecret: let it rest an hour.\n"
              "Token: a subway token from 1953.\nAuth: J. Smith\n"
              "Private: keep out of reach of children.\n"
              "First pass: the code was read. He used the token: a small brass coin.\n")
    assert sd.find(recipe) == []
    from puffpai.knowledge import text_books
    assert text_books.set_aside_orders(recipe) == (recipe, 0)
    # The old rule still holds a password written into a sentence.
    assert [h.value for h in sd.find("Password: swordfish, said the doorman.")] == [
        "swordfish"]


@pytest.mark.parametrize("line, value", [
    ("Authorization: Bearer EXAMPLE.tok.0000", "EXAMPLE.tok.0000"),
    ("curl -H 'Authorization: Bearer EXAMPLE0000tok' https://x.invalid", "EXAMPLE0000tok"),
    ('curl -H "Authorization: Bearer EXAMPLE0000tok" https://x.invalid', "EXAMPLE0000tok"),
    ("Authorization: Basic RVhBTVBMRTpwdw==", "RVhBTVBMRTpwdw=="),
    ("curl -u admin:EXAMPLEpw https://x.invalid", "EXAMPLEpw"),
    ("curl --user=admin:EXAMPLEpw https://x.invalid", "EXAMPLEpw"),
    ("mysql -pEXAMPLEpw -u root", "EXAMPLEpw"),
    ("mysql -u root -p EXAMPLEdb", "EXAMPLEdb"),
    ("mysql --password=abc -u root", "abc"),
    ("app --token EXAMPLE0000tok", "EXAMPLE0000tok"),
    ("gh auth login --with-token EXAMPLEtok", "EXAMPLEtok"),
    ("app --api-key=abc", "abc"),
    ("psql postgres://user:pw@db.invalid/app", "pw"),
    ("redis://:EXAMPLEpw@cache.invalid:6379", "EXAMPLEpw"),
    ("amqp://guest:q9@rabbit.invalid:5672/", "q9"),         # a two-letter password
    ("ENCRYPTION_KEY=abc123", "abc123"),
    ("SIGNING_KEY=abc123", "abc123"),
    ("MASTER_KEY=abc123", "abc123"),
    ("$env:API_KEY='EXAMPLEpw'", "EXAMPLEpw"),             # the head does not swallow it
    ("<password>EXAMPLEpw</password>", "EXAMPLEpw"),
    ("-e PGPASSWORD=EXAMPLEpw", "EXAMPLEpw"),
    ("MYSQL_ROOT_PASSWORD: EXAMPLEpw#notacomment", "EXAMPLEpw#notacomment"),
])
def test_command_line_and_connection_secrets_are_held(line, value):
    _held_everywhere(line, value)


def test_ids_and_ports_on_a_command_line_stay():
    for text in ("docker run -u 1000:1000 -p 8080:80 img", "mysql -u root -P 3306 app",
                 "curl https://example.invalid/a?page=2"):
        assert sd.find(text) == [] and sd.redact_only(text, anywhere=True) == text, text


def test_the_classifier_leaves_the_held_mark_alone():
    from puffpai.security import data_classifier, toolsmith_cloud
    for text in ("PASSWORD: EXAMPLEpw", "my password is EXAMPLEpw, ok"):
        out = toolsmith_cloud.clean(text)
        assert "EXAMPLEpw" not in out
        assert "in vault] in vault]" not in out and "kept private] in vault]" not in out
    held = "my password is [held in vault] ok"
    assert data_classifier.DataClassifier().redact_pii(held)[0] == held


_LARGE = {
    "many settings": (lambda: "DB_PASS=abc\n" * 40000, 3.0),
    "1 MB mixed": (lambda: ("DB_PASS=abc PORT=80 hello world: foo " * 30000)[:1_000_000], 6.0),
    "one long word": (lambda: "A" * 100000, 1.0),
    "a word full of names": (lambda: "token" * 20000 + "=x", 1.0),
    "heads in one run": (lambda: "A=" * 30000, 2.0),
    "drive heads": (lambda: "a=C:\\" * 30000, 2.0),
    "unclosed quotes": (lambda: "a:'" * 30000, 2.0),
}


@pytest.mark.parametrize("name", sorted(_LARGE))
def test_large_text_is_read_in_linear_time(name):
    """Loose bounds (a few times the measured time): a quadratic read takes
    minutes here."""
    import time
    make, seconds = _LARGE[name]
    text = make()
    t0 = time.perf_counter()
    sd.find(text)
    sd.redact_only(text, anywhere=True)
    assert time.perf_counter() - t0 < seconds, name


# ── verifier round 3 (2026-10-15): what base held, install commands, marks ───

_BS, _DQ, _SQ = chr(92), chr(34), chr(39)


@pytest.mark.parametrize("line, value", [
    ("DATABASE_URL=postgresql+asyncpg://app:EXAMPLEpw@db:5432/app", "EXAMPLEpw"),
    ("sqlalchemy.url = postgresql+psycopg2://app:EXAMPLEpw@db/app", "EXAMPLEpw"),
    ("db: postgresql+asyncpg://app:EXAMPLEpw@db/app", "EXAMPLEpw"),
    ("SQLALCHEMY_DATABASE_URI=postgresql+psycopg2://app:EXAMPLEpw@db/app", "EXAMPLEpw"),
    ("DATABASE_URL=mysql+pymysql://app:EXAMPLEpw@db/app", "EXAMPLEpw"),
    ("CELERY_BROKER_URL=sqla+postgresql://app:EXAMPLEpw@db/app", "EXAMPLEpw"),
    ("url: x-very-long-prefix-postgres://app:EXAMPLEpw@db/app", "EXAMPLEpw"),
    ("x_postgres://app:EXAMPLEpw@db", "EXAMPLEpw"),
    ("x" + _SQ + "password" + _SQ + ": " + _SQ + "EXAMPLEpw" + _SQ, "EXAMPLEpw"),
    ("a" + _DQ + "password" + _DQ + ":" + _DQ + "EXAMPLEpw" + _DQ, "EXAMPLEpw"),
    ("a" + _DQ + "password" + _DQ + ":EXAMPLEpw", "EXAMPLEpw"),
    ("u" + _DQ + "password" + _DQ + ": " + _DQ + "EXAMPLEpw" + _DQ, "EXAMPLEpw"),
    ("{u" + _SQ + "password" + _SQ + ": u" + _SQ + "EXAMPLEpw" + _SQ + "}", "EXAMPLEpw"),
    ("{" + _BS + _DQ + "password" + _BS + _DQ + ":" + _BS + _DQ + "EXAMPLEpw" + _BS + _DQ + "}",
     "EXAMPLEpw"),
    ("log: body=" + _DQ + "{" + _BS + _DQ + "user" + _BS + _DQ + ":1," + _BS + _DQ + "password"
     + _BS + _DQ + ":" + _BS + _DQ + "EXAMPLEpw" + _BS + _DQ + "}" + _DQ, "EXAMPLEpw"),
    ("A" * 90 + "_PASSWORD=EXAMPLE22secret", "EXAMPLE22secret"),    # a name over 80 long
    ("x my_" + "a" * 90 + "_password: EXAMPLE22secret", "EXAMPLE22secret"),
    ("PASSWORD_" + "A" * 98 + "=EXAMPLE22secret", "EXAMPLE22secret"),
    ("curl -uadmin:EXAMPLEpw https://x.invalid", "EXAMPLEpw"),
    ("GET https://maps.example.invalid/q?key=EXAMPLE0000key&list=x", "EXAMPLE0000key"),
    ("my password is: EXAMPLEpw", "EXAMPLEpw"),
    ("app --password EXAMPLEpw", "EXAMPLEpw"),
    ("app --api-key EXAMPLEk", "EXAMPLEk"),
])
def test_what_base_held_is_held_again_and_more(line, value):
    _held_everywhere(line, value)


@pytest.mark.parametrize("text", [
    "docker login -u myuser --password-stdin ghcr.io",
    "llm --max-tokens 4096 --model x",
    "pip install x --tokenizer bert-base-uncased",
    "code-server --auth password",
    "code-server --auth none",
    "code-server --auth-type oauth",
    "https://www.youtube.com/watch?v=abc123&list=PLx9AbCdEfGhIjKlMnOpQrStUvWx12",
    "AUTH=password",
    "POSTGRES_HOST_AUTH_METHOD=trust",
    "app --token-file ./t.txt",
    "the password is required",
    "docker run -u 1000:1000 img",
])
def test_install_commands_stay_readable(text):
    from puffpai.security import toolsmith_cloud
    assert sd.find(text) == [], text
    assert toolsmith_cloud.clean(text) == text


@pytest.mark.parametrize("text", [
    "PASSWORD: EXAMPLEpw",
    "password = changeme",
    "My password is EXAMPLEpw",
    "passcode: 4421",
    "Password: [password kept private]",
    "PASSWORD: [held in vault] EXAMPLEpw",
    "password=[held in vault]EXAMPLEpw",
    _DQ + "password" + _DQ + ": " + _DQ + "[held in vault]EXAMPLEpw" + _DQ,
    "docker run -e POSTGRES_PASSWORD=EXAMPLEpw -e WEBHOOK=" + _B64 + " img",
])
def test_cleaning_twice_changes_nothing_and_marks_stay_whole(text):
    from puffpai.security import toolsmith_cloud
    once = toolsmith_cloud.clean(text)
    assert toolsmith_cloud.clean(once) == once
    assert "EXAMPLEpw" not in once
    assert "] kept private]" not in once and "] in vault]" not in once
    vault = sd.redact(text)[0]
    assert sd.redact(vault)[0] == vault
