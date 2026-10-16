"""Tests for the model profile / adapter layer and the routing rulepacks.

Three properties get special attention, because they are the ones whose
regression would be quiet and expensive:

* **Budgets clamp DOWN, never up.** A profile is a ceiling. A small brain handed
  the reference brain's 8k context truncates the prompt without complaining, so
  there must be no path through :func:`resolve_budgets` that raises a number.
* **The honesty rule holds.** ``basis: measured:*`` belongs to exactly one
  model on exactly one board. Everything else, including quantised variants of
  the benched tag, is a conservative default and says so.
* **Moving the vocabulary into JSON changed nothing.** The reference build runs
  at the ``light`` tier, and at that tier the assembled rulepacks and the router
  must be indistinguishable from the hardcoded lists they replaced.

Run:
    pytest tests/test_model_profile.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import model_profile, router, rulepacks

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── The pre-migration vocabulary, frozen ────────────────────────────────────
# Copied verbatim from router.py as it stood before the keyword lists moved
# into config/rulepacks/. This is the reference the migration is checked
# against; it is a fixture, not a source of truth, and it should only ever
# change alongside a deliberate, reviewed vocabulary change.

OLD_BRIEFING_KEYWORDS = [
    "daily update", "daily briefing", "daily report",
    "morning briefing", "morning update", "morning report",
    "what did i miss", "catch me up", "brief me", "daily digest",
    "today's briefing", "today's update",
]

OLD_HOME_KEYWORDS = [
    "home assistant", "smart home", "turn on", "turn off",
    "thermostat", "set brightness", "dim the",
    "the light", "the lights", "the switch", "the fan",
    "list devices", "control the", "activate scene",
    "my dashboard", "the dashboard", "widget", "widgets",
]

OLD_SECURITY_KEYWORDS = [
    "security status", "needs approval", "approval",
    "who is on my network", "devices on my network",
    "approve", "consent", "permission", "authorize",
    "go ahead", "allow it", "deny it",
]

OLD_MEDIA_KEYWORDS = [
    "media server", "jellyfin", "movie", "movies", "film", "films",
    "music library", "my music", "song", "songs", "album", "albums",
    "artist", "audiobook", "audiobooks", "ebook", "ebooks",
    "playlist", "playlists", "now playing", "currently playing",
    "play music", "play movie", "play video", "watch",
    "what movies", "what music", "what books", "my library",
    "media library", "search movies", "search music", "search books",
]

OLD_APP_KEYWORDS = [
    "timer", "alarm", "countdown", "remind me in", "remind me to",
    "set a timer", "set an alarm",
    "calendar", "schedule", "event", "appointment", "meeting",
    "task", "to do", "todo",
    "pantry", "shopping list", "grocery", "meal plan",
    "workout", "exercise", "fitness",
    "trivia", "quiz",
]

OLD_ACTION_KEYWORDS = [
    "calculate", "compute", "multiply", "divide", "sum of",
    "create file", "write file", "save to", "read file", "open file",
]

OLD_LIBRARY_KEYWORDS = [
    "librar", "database", "indexed", "collection",
    "wikipedia", "knowledge base",
]

OLD_LIBRARY_QUERY_WORDS = [
    "list", "how many", "what", "which", "show", "tell",
    "about", "describe", "do you have", "do you know",
    "available", "access", "can you search",
]

OLD_LISTS = {
    "briefing": OLD_BRIEFING_KEYWORDS,
    "house": OLD_HOME_KEYWORDS,
    "security": OLD_SECURITY_KEYWORDS,
    "media": OLD_MEDIA_KEYWORDS,
    "apps": OLD_APP_KEYWORDS,
    "action": OLD_ACTION_KEYWORDS,
    "library": OLD_LIBRARY_KEYWORDS,
}


@pytest.fixture
def profiles():
    """The shipped profile table, read from disk."""
    return model_profile.load_profiles()


@pytest.fixture
def packs():
    """The shipped rulepacks, read from disk."""
    return rulepacks.load_packs()


# ── profile lookup ───────────────────────────────────────────────────────────

def test_every_catalog_model_has_a_profile(profiles):
    with open(os.path.join(REPO_ROOT, "config", "model_catalog.json"),
              "r", encoding="utf-8") as fh:
        catalog = json.load(fh)
    table = profiles["profiles"]
    for model in catalog["models"]:
        assert model["id"] in table, f"no profile for catalog model {model['id']}"
    assert "_default" in table


def test_exact_lookup(profiles):
    prof = model_profile.get_profile("qwen3:30b-a3b", profiles)
    assert prof["routing_tier"] == "light"
    assert prof["supports"]["planning"] is True


def test_family_prefix_falls_back_to_the_base_tag(profiles):
    prof = model_profile.get_profile("qwen2.5:7b-instruct-q4_K_M", profiles)
    assert prof["routing_tier"] == "guided"
    assert prof["inherited_from"] == "qwen2.5:7b-instruct"


def test_family_matching_is_one_directional(profiles):
    """A bare family name must not inherit the reference brain's light tier."""
    assert model_profile.routing_tier("qwen3", profiles) == "guided"
    assert model_profile.routing_tier("qwen2.5", profiles) == "guided"


def test_unknown_and_empty_models_get_the_default(profiles):
    default = profiles["profiles"]["_default"]
    for unknown in ("mystery-model:99b", "", "   "):
        prof = model_profile.get_profile(unknown, profiles)
        assert prof["routing_tier"] == default["routing_tier"]
        assert prof["basis"] == "conservative-default"


def test_default_is_never_the_light_tier(profiles):
    assert model_profile.routing_tier("mystery-model:99b", profiles) != "light"


def test_lookup_never_raises_on_a_broken_table():
    for junk in ({}, {"profiles": None}, {"profiles": {}}, {"profiles": {"x": 5}}):
        prof = model_profile.get_profile("anything", junk)
        assert prof["routing_tier"] in rulepacks.TIER_RANK


def test_load_profiles_degrades_to_the_baked_in_table(tmp_path):
    missing = model_profile.load_profiles(str(tmp_path / "nope.json"))
    assert missing is model_profile._FALLBACK_PROFILES

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert model_profile.load_profiles(str(broken)) is model_profile._FALLBACK_PROFILES


def test_supports_is_fail_closed(profiles):
    assert model_profile.supports("qwen3:30b-a3b", "thinking", profiles) is True
    assert model_profile.supports("llama3.2:1b", "planning", profiles) is False
    assert model_profile.supports("qwen3:30b-a3b", "telepathy", profiles) is False


def test_limits_sentence_is_a_plain_ascii_sentence(profiles):
    for model_id in profiles["profiles"]:
        sentence = model_profile.limits_sentence(model_id, profiles)
        assert sentence and sentence.endswith(".")
        sentence.encode("ascii")  # raises if a smart quote or em dash crept in


# ── honesty rule ─────────────────────────────────────────────────────────────

def test_only_the_benched_pair_claims_a_measured_basis(profiles):
    """measured:* is reserved for the one model/board combination we ran."""
    measured = {mid: p["basis"] for mid, p in profiles["profiles"].items()
                if str(p.get("basis", "")).startswith("measured:")}
    assert measured == {"qwen3:30b-a3b": "measured:jetson_agx_orin_64gb"}


def test_a_measured_basis_names_a_real_silicon_entry(profiles):
    with open(os.path.join(REPO_ROOT, "config", "model_catalog.json"),
              "r", encoding="utf-8") as fh:
        silicon = json.load(fh)["silicon"]
    for prof in profiles["profiles"].values():
        b = str(prof.get("basis", ""))
        if b.startswith("measured:"):
            assert b.split(":", 1)[1] in silicon


def test_every_basis_is_one_of_the_two_allowed_forms(profiles):
    for prof in profiles["profiles"].values():
        b = str(prof["basis"])
        assert b == "conservative-default" or b.startswith("measured:")


def test_a_variant_of_the_benched_tag_does_not_inherit_the_measurement(profiles):
    assert model_profile.basis("qwen3:30b-a3b", profiles).startswith("measured:")
    assert model_profile.basis("qwen3:30b-a3b-q8_0", profiles) == "conservative-default"


# ── budgets ──────────────────────────────────────────────────────────────────

def test_budgets_clamp_down_never_up(profiles):
    small = model_profile.resolve_budgets(
        "llama3.2:1b", requested_ctx=8192, requested_predict=4096,
        profiles=profiles)
    ceiling = profiles["profiles"]["llama3.2:1b"]["budgets"]
    assert small["num_ctx"] == ceiling["num_ctx"]
    assert small["num_predict"] == ceiling["num_predict"]
    assert small["num_ctx"] < 8192


def test_a_smaller_request_is_kept(profiles):
    got = model_profile.resolve_budgets(
        "qwen3:30b-a3b", requested_ctx=1024, requested_predict=64,
        profiles=profiles)
    assert got["num_ctx"] == 1024
    assert got["num_predict"] == 64


def test_no_request_yields_the_ceiling(profiles):
    for model_id, prof in profiles["profiles"].items():
        got = model_profile.resolve_budgets(model_id, profiles=profiles)
        assert got["num_ctx"] == prof["budgets"]["num_ctx"]
        assert got["num_predict"] == prof["budgets"]["num_predict"]
        assert got["plan_ctx"] == prof["budgets"]["plan_ctx"]


def test_budgets_never_exceed_the_profile_for_any_request(profiles):
    for model_id, prof in profiles["profiles"].items():
        for want in (1, 100, 512, 8192, 131072):
            got = model_profile.resolve_budgets(
                model_id, requested_ctx=want, requested_predict=want,
                requested_plan_ctx=want, profiles=profiles)
            assert got["num_ctx"] <= prof["budgets"]["num_ctx"]
            assert got["num_predict"] <= prof["budgets"]["num_predict"]
            assert got["plan_ctx"] <= prof["budgets"]["plan_ctx"]


def test_nonsense_requests_fall_back_to_the_ceiling(profiles):
    ceiling = profiles["profiles"]["qwen3:30b-a3b"]["budgets"]["num_ctx"]
    for junk in (0, -1, None):
        got = model_profile.resolve_budgets(
            "qwen3:30b-a3b", requested_ctx=junk, profiles=profiles)
        assert got["num_ctx"] == ceiling


def test_the_reference_brain_is_never_clamped_by_its_own_profile(profiles):
    """A 64 GB board must behave identically after the adapter layer landed.

    Every num_predict the code asks for today (512 for most calls, 1024 for the
    streaming RAG answer) has to survive the ceiling, or replies get quietly
    shortened on the one build we know works. The config default ctx likewise.
    """
    from puffpai.config import get_config
    cfg = get_config()
    for want_predict in (1, 512, 1024):
        got = model_profile.resolve_budgets(
            "qwen3:30b-a3b",
            requested_ctx=cfg.ollama.num_ctx,
            requested_predict=want_predict,
            profiles=profiles)
        assert got["num_predict"] == want_predict
        assert got["num_ctx"] == cfg.ollama.num_ctx


def test_the_shipped_default_model_holds_the_configured_context(profiles):
    """The default config's model must not be clamped below its own num_ctx."""
    from puffpai.config import get_config
    cfg = get_config()
    got = model_profile.resolve_budgets(
        cfg.ollama.model, requested_ctx=cfg.ollama.num_ctx, profiles=profiles)
    assert got["num_ctx"] == cfg.ollama.num_ctx


def test_budgets_survive_a_profile_with_no_budgets():
    got = model_profile.resolve_budgets(
        "x", profiles={"profiles": {"_default": {"routing_tier": "rails"}}})
    assert got["num_ctx"] > 0 and got["num_predict"] > 0


# ── rulepacks: the migration changed no words ────────────────────────────────

def test_shipped_packs_match_the_baked_in_fallback(packs):
    """The JSON on disk and the in-code safety net must not drift apart."""
    on_disk = {p["surface"]: p for p in packs}
    baked = {p["surface"]: p for p in rulepacks._FALLBACK_PACKS}
    assert set(on_disk) == set(baked)
    for surface, pack in baked.items():
        for field in ("keywords", "verbs", "objects", "query_words",
                      "exclude_phrases", "min_tier", "order", "category",
                      "route", "match", "title", "examples"):
            assert pack.get(field) == on_disk[surface].get(field), (
                f"{surface}.{field} drifted between JSON and fallback")


@pytest.mark.parametrize("surface", sorted(OLD_LISTS))
def test_light_tier_keywords_never_lose_a_word_from_the_old_lists(surface, packs):
    """Nothing was lost when the vocabulary moved from code into JSON.

    This began as byte-for-byte equality, which was the right guard while the
    migration was in flight: it proved the move changed no behaviour. That job
    is done. Equality also forbade ever ADDING a word, which defeats the point
    of making the lists data - the whole reason they are JSON is so a build can
    extend them. So the invariant is now "the migrated words are all still
    here", not "these lists may never grow".

    Order is preserved for the original words so the router's first-match
    behaviour on pre-existing vocabulary is unchanged; additions go on the end.
    """
    light = {p["surface"]: p for p in rulepacks.assemble("light", packs)}
    assert surface in light, f"{surface} must load at the light tier"
    current = light[surface]["keywords"]
    old = OLD_LISTS[surface]
    assert current[:len(old)] == old, (
        f"{surface} lost or reordered a migrated keyword; additions belong "
        f"at the end so existing routing is untouched")


def test_light_tier_library_extras_are_unchanged(packs):
    library = next(p for p in rulepacks.assemble("light", packs)
                   if p["surface"] == "library")
    assert library["query_words"] == OLD_LIBRARY_QUERY_WORDS


def test_router_module_constants_still_hold_the_old_lists():
    """The router's module-level names still carry every migrated word.

    Prefix equality, not full equality - see the note on
    ``test_light_tier_keywords_never_lose_a_word_from_the_old_lists``. A pack
    may grow; it may not lose or reorder what it started with.
    """
    for name, old in (
        ("_BRIEFING_KEYWORDS", OLD_BRIEFING_KEYWORDS),
        ("_HOME_KEYWORDS", OLD_HOME_KEYWORDS),
        ("_SECURITY_KEYWORDS", OLD_SECURITY_KEYWORDS),
        ("_MEDIA_KEYWORDS", OLD_MEDIA_KEYWORDS),
        ("_APP_KEYWORDS", OLD_APP_KEYWORDS),
        ("_ACTION_KEYWORDS", OLD_ACTION_KEYWORDS),
        ("_LIBRARY_KEYWORDS", OLD_LIBRARY_KEYWORDS),
        ("_LIBRARY_QUERY_WORDS", OLD_LIBRARY_QUERY_WORDS),
    ):
        current = getattr(router, name)
        assert current[:len(old)] == old, f"router.{name} lost a migrated word"


def test_tier_assembly_is_monotonic(packs):
    """A weaker brain loads more packs, never fewer."""
    light = {p["surface"] for p in rulepacks.assemble("light", packs)}
    guided = {p["surface"] for p in rulepacks.assemble("guided", packs)}
    rails = {p["surface"] for p in rulepacks.assemble("rails", packs)}
    assert light <= guided <= rails
    assert guided > light, "some pack must be reserved for the weaker tiers"


def test_an_unknown_tier_loads_the_safe_middle(packs):
    unknown = {p["surface"] for p in rulepacks.assemble("nonsense", packs)}
    guided = {p["surface"] for p in rulepacks.assemble("guided", packs)}
    assert unknown == guided


def test_load_packs_degrades_to_the_baked_in_set(tmp_path):
    got = rulepacks.load_packs(str(tmp_path / "no-such-dir"))
    assert {p["surface"] for p in got} == {
        p["surface"] for p in rulepacks._FALLBACK_PACKS}


def test_a_corrupt_pack_is_skipped_not_fatal(tmp_path):
    (tmp_path / "house.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "media.json").write_text(
        json.dumps({"surface": "media", "keywords": ["jellyfin"],
                    "category": "media", "min_tier": "light", "order": 40}),
        encoding="utf-8")
    got = rulepacks.load_packs(str(tmp_path))
    assert [p["surface"] for p in got] == ["media"]


# ── router equivalence corpus ────────────────────────────────────────────────
# Every category, plus the shapes that historically got misrouted: "my library"
# belongs to media, citation questions stay on RAG, and indirect action requests
# reach an agent rather than a text model that would claim it complied.

CORPUS: list[tuple[str, str, str]] = [
    # (message, expected route value, expected category)
    ("give me my daily briefing", "briefing", "briefing"),
    ("catch me up on today", "briefing", "briefing"),
    ("what did i miss overnight", "briefing", "briefing"),
    ("morning report please", "briefing", "briefing"),
    ("turn on the lights", "agent", "home"),
    ("turn off the fan in here", "agent", "home"),
    ("set brightness to 40", "agent", "home"),
    ("dim the lamp by the couch", "agent", "home"),
    ("list devices you can see", "agent", "home"),
    ("is home assistant connected", "agent", "home"),
    ("show me my dashboard", "agent", "home"),
    ("what is my security status", "agent", "security"),
    ("does anything need approval", "agent", "security"),
    ("who is on my network", "agent", "security"),
    ("go ahead", "agent", "security"),
    ("i approve that", "agent", "security"),
    ("what movies do i have", "agent", "media"),
    ("play music in the kitchen", "agent", "media"),
    ("is jellyfin up", "agent", "media"),
    ("what is in my library", "agent", "media"),
    ("shuffle my rock playlist", "agent", "media"),
    ("search books about sailing", "agent", "media"),
    ("what is now playing", "agent", "media"),
    ("set a timer for ten minutes", "agent", "app"),
    ("what is on my calendar tomorrow", "agent", "app"),
    ("add milk to the shopping list", "agent", "app"),
    ("remind me to call the vet", "agent", "app"),
    ("what is in the pantry", "agent", "app"),
    ("give me a trivia question", "agent", "app"),
    ("log my workout", "agent", "app"),
    ("calculate 17 times 4", "agent", "action"),
    ("read file notes.txt", "agent", "action"),
    ("what is the sum of those", "agent", "action"),
    ("list what is in the knowledge base", "library", "library"),
    ("how many wikipedia articles are indexed", "library", "library"),
    ("what collections do you have", "library", "library"),
    ("cite your sources for that", "rag", "rag"),
    ("where did you get that from", "rag", "rag"),
    ("why is the sky blue", "rag", "rag"),
    ("tell me about the roman empire", "rag", "rag"),
    ("thanks, that was helpful", "rag", "rag"),
]


@pytest.mark.parametrize("message,route_value,category", CORPUS)
def test_light_tier_routing_is_unchanged(monkeypatch, message, route_value, category):
    """The reference build routes exactly as it did before the migration."""
    monkeypatch.setattr(router, "_active_tier", "light")
    assert router.route_message(message).value == route_value
    assert router.get_route_category(message) == category


@pytest.mark.parametrize("message,route_value,category", CORPUS)
def test_weaker_tiers_never_downgrade_a_named_surface(
        monkeypatch, message, route_value, category):
    """Extra packs may name a surface RAG left unnamed; they may not rename one."""
    for tier in ("guided", "rails"):
        monkeypatch.setattr(router, "_active_tier", tier)
        assert router.route_message(message).value == route_value
        got = router.get_route_category(message)
        assert got == category or (category == "rag" and got == "action")


def test_weaker_tiers_name_a_surface_the_light_tier_leaves_to_the_model(monkeypatch):
    """The tier difference is real: an indirect action gets a name on rails.

    "cancel the meeting" hits no keyword list, so the light tier leaves it to
    the model to work out. The action net gives the weaker tiers a named
    surface instead. Both tiers already send it to an agent; what changes is
    whether the category is guessed or stated.
    """
    message = "cancel the meeting"
    monkeypatch.setattr(router, "_active_tier", "light")
    assert router.get_route_category(message) == "app"

    message = "lower the volume"
    monkeypatch.setattr(router, "_active_tier", "light")
    assert router.get_route_category(message) == "rag"
    assert router.route_message(message) is router.RouteTarget.AGENT
    for tier in ("guided", "rails"):
        monkeypatch.setattr(router, "_active_tier", tier)
        assert router.get_route_category(message) == "action"
        assert router.route_message(message) is router.RouteTarget.AGENT


def test_action_net_needs_both_a_verb_and_an_object(monkeypatch, packs):
    net = next(p for p in packs if p["surface"] == "action_net")
    assert rulepacks.matches(net, "turn off the lamp")
    assert not rulepacks.matches(net, "turn of phrase")
    assert not rulepacks.matches(net, "the lamp is nice")
    # Word-boundary matching, so a short object does not fire inside a word.
    assert not rulepacks.matches(net, "please set the acclimatisation")


def test_forced_modes_bypass_the_keywords(monkeypatch):
    monkeypatch.setattr(router, "_active_tier", "light")
    assert router.route_message("why is the sky blue", mode="home").value == "home"
    assert router.route_message("turn on the lights", mode="general").value == "rag"


def test_indirect_action_requests_still_reach_an_agent(monkeypatch):
    monkeypatch.setattr(router, "_active_tier", "light")
    for message in ("it is too dark in here", "don't let me forget the meeting",
                    "wake me at six"):
        assert router.route_message(message) is router.RouteTarget.AGENT


def test_active_tier_never_raises_and_is_a_known_tier():
    router._reset_tier_cache()
    assert router.active_tier() in rulepacks.TIER_RANK


# ── phrasebook ───────────────────────────────────────────────────────────────

def test_phrasebook_for_a_light_model(profiles, packs):
    text = model_profile.generate_phrasebook(
        "qwen3:30b-a3b", packs=packs, profiles=profiles)
    assert text.startswith("# Phrasebook for qwen3:30b-a3b")
    assert "**light**" in text
    assert model_profile.limits_sentence("qwen3:30b-a3b", profiles) in text
    # On the light tier the honest sentence describes reach, not a limit, so it
    # must not be published under a "cannot do" heading.
    assert "What to expect from this brain:" in text
    assert "What this brain cannot do" not in text
    assert "## Movies, music and books" in text and "## The house" in text
    assert "- \"turn on the kitchen lights\"" in text
    # The light tier does not load the extra deterministic packs.
    assert "## Asking for something in passing" not in text


def test_phrasebook_for_a_rails_model(profiles, packs):
    text = model_profile.generate_phrasebook(
        "llama3.2:1b", packs=packs, profiles=profiles)
    assert "**rails**" in text
    assert "Use these phrasings." in text
    assert "What this brain cannot do:" in text
    # The rails tier loads more scaffolding, so it documents more.
    assert "## Asking for something in passing" in text
    assert "- \"lower the volume\"" in text


def test_every_published_example_actually_routes(packs):
    """The anti-drift guarantee, mechanically.

    The phrasebook publishes hand-written sentences rather than matcher
    tokens, so nothing but this test stops the two from parting company. Every
    example goes through the REAL matcher and the REAL router and has to land
    on its own pack's surface. An example that stops working fails the build;
    it does not quietly become a lie on a public page.
    """
    for pack in packs:
        examples = rulepacks.examples_for(pack)
        # Load every pack, so a guided/rails-only pack is reachable too.
        router._active_tier = "rails"
        try:
            for phrase in examples:
                lower = phrase.lower()
                assert rulepacks.matches(pack, lower), (
                    f"{pack['surface']} publishes a phrase its own matcher "
                    f"rejects: {phrase!r}")
                assert router.get_route_category(phrase) == pack["category"], (
                    f"{pack['surface']} publishes {phrase!r}, which the router "
                    f"sends to {router.get_route_category(phrase)!r}")
                assert router.route_message(phrase).value == pack["route"], (
                    f"{pack['surface']} publishes {phrase!r}, which routes to "
                    f"{router.route_message(phrase).value!r}")
        finally:
            router._reset_tier_cache()


def test_light_tier_examples_route_at_the_light_tier_too(monkeypatch, packs):
    """A pack the reference build loads must have examples that work there."""
    monkeypatch.setattr(router, "_active_tier", "light")
    for pack in rulepacks.assemble("light", packs):
        for phrase in rulepacks.examples_for(pack):
            assert router.get_route_category(phrase) == pack["category"], phrase


def test_every_pack_publishes_examples_and_a_title(packs):
    """A new pack cannot ship without published wordings.

    The phrasebook is a public artifact and an invitation to extend the packs,
    so an examples-less pack would appear as an empty heading. Four is the
    floor: fewer cannot span a surface's distinct abilities.
    """
    assert packs, "no rulepacks loaded"
    for pack in packs:
        examples = rulepacks.examples_for(pack)
        assert len(examples) >= 4, (
            f"{pack['surface']} publishes {len(examples)} examples; needs 4+")
        assert len(set(examples)) == len(examples), (
            f"{pack['surface']} repeats an example")
        assert rulepacks.title_for(pack) == pack.get("title"), (
            f"{pack['surface']} has no published title")


def test_published_examples_read_like_sentences(packs):
    """Plain, lowercase, several words. Not matcher tokens, not command syntax."""
    for pack in packs:
        for phrase in rulepacks.examples_for(pack):
            phrase.encode("ascii")
            assert phrase == phrase.lower(), f"{phrase!r} is not lowercase"
            words = phrase.split()
            assert len(words) >= 3, f"{phrase!r} is a fragment"
            # Compare the last WORD, not the string's tail. str.endswith on a
            # bare "a" also matches "camera", "pizza", "media" - the guard was
            # rejecting perfectly good sentences for their final letter.
            assert words[-1] not in ("the", "to", "in", "a", "of", "for"), (
                f"{phrase!r} trails off")


def test_matcher_tokens_never_reach_the_published_page(profiles, packs):
    """The internals that embarrassed us stay internal."""
    for model_id in ("qwen3:30b-a3b", "llama3.2:1b"):
        text = model_profile.generate_phrasebook(
            model_id, packs=packs, profiles=profiles)
        for token in ("\"librar\"", "\"control the\"", "\"to do\"",
                      "\"save to\"", "\"remind me in\"", "\"permission\""):
            assert token not in text, f"{token} leaked into the phrasebook"
        assert "Action words:" not in text
        assert "Things:" not in text


def test_phrasebook_can_be_narrowed_to_enabled_surfaces(profiles, packs):
    text = model_profile.generate_phrasebook(
        "qwen3:30b-a3b", enabled_surfaces=["house"], packs=packs, profiles=profiles)
    assert "## The house" in text
    assert "## Movies, music and books" not in text


def test_phrasebook_with_no_surfaces_says_so(profiles, packs):
    text = model_profile.generate_phrasebook(
        "qwen3:30b-a3b", enabled_surfaces=[], packs=packs, profiles=profiles)
    assert "No surfaces are enabled" in text


def test_phrasebook_is_ascii_for_the_console(profiles, packs):
    for model_id in ("qwen3:30b-a3b", "llama3.2:1b", "unknown:1b"):
        model_profile.generate_phrasebook(
            model_id, packs=packs, profiles=profiles).encode("ascii")
