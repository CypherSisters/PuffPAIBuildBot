"""The wizard's own words, in two plain versions.

The wizard has to be easy to read and to show plainly what is being chosen.
Every sentence the wizard itself writes for the person building a
bot lives here, twice:

* ``standard`` -- plain everyday English.
* ``simple`` -- for a builder under 13: short sentences, one idea per line,
  friendly, and no numbers unless the number is the point (a download size is).

Both are plain. The simple one is not a different wizard: it asks the same
questions in the same order and shows the same summary. Only the wording
changes, and only here. **The finished bot is not affected** (there
is no kid mode) -- nothing outside the wizard reads the age band.

The age itself is never stored. :func:`band_for_age` turns what was typed into
one of three bands, and only the band goes into the build record.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any

STANDARD = "standard"
SIMPLE = "simple"
MODES = (STANDARD, SIMPLE)

UNDER_13 = "under_13"
TEEN = "13_17"
ADULT = "adult"
AGE_BANDS = (UNDER_13, TEEN, ADULT)

#: What a band is called if a screen ever needs to say it back.
BAND_WORDS = {UNDER_13: "under 13", TEEN: "13 to 17", ADULT: "18 or older"}

#: At most this many choices on one screen in the simple wording.
SIMPLE_MAX_CHOICES = 3


def band_for_age(text: Any) -> str | None:
    """"10" -> ``under_13``. Anything that is not an age is ``None`` (skipped).

    Accepts a bare number with optional words around it ("I'm 10", "10 years").
    A number outside 3..120 is not an age anybody means, so it is a skip too,
    rather than a guess.
    """
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        age = int(text)
    else:
        match = re.search(r"\d{1,3}", str(text or ""))
        if not match:
            return None
        age = int(match.group(0))
    if age < 3 or age > 120:
        return None
    if age < 13:
        return UNDER_13
    if age < 18:
        return TEEN
    return ADULT


def mode_for_band(band: Any) -> str:
    """The wording for a band. No band (skipped) is the standard wording."""
    return SIMPLE if band == UNDER_13 else STANDARD


def mode_of(shared: Any) -> str:
    """The wording mode a run is in, read from its shared notebook."""
    if isinstance(shared, dict):
        mode = shared.get("wording")
        if mode in MODES:
            return mode
        return mode_for_band(shared.get("age_band"))
    return STANDARD


#: key -> {"standard": ..., "simple": ...}. ``{name}``-style fields are filled
#: by :func:`say`. Nothing in here may use the words the plain-words test bans.
TEXT: dict[str, dict[str, str]] = {
    # -- the opening -------------------------------------------------------
    "welcome": {
        STANDARD: "Welcome to BuildBot. Let's make your bot together.",
        SIMPLE: "Hi! Let's make your bot.",
    },
    "welcome_how": {
        STANDARD: "First I will show you what this computer has and which "
                  "thinking model fits it. Then three short questions, and one "
                  "plan to say yes to.",
        SIMPLE: "First I look at your computer.\nThen I ask you three "
                "things.\nThen I show you a plan.",
    },
    "goal_title": {
        STANDARD: "Your bot",
        SIMPLE: "Your bot",
    },
    "goal_question": {
        STANDARD: "What kind of bot do you want? Say it in your own words.",
        SIMPLE: "What kind of bot do you want? Say it in your own words.",
    },
    "goal_help": {
        STANDARD: "For example: a garden helper for my vegetable beds, or a bot "
                  "that knows every dinosaur.",
        SIMPLE: "For example: a bot that knows every dinosaur.",
    },
    # Three sample goals under the goal box (the browser's buttons fill the
    # box). Each says what the bot is for, then what it should do: the shape
    # that builds well. Different areas on purpose; nothing in the code
    # depends on what they are about.
    "goal_example_1": {
        STANDARD: "A meal helper for our family: plan dinners for the week, make the "
                  "shopping list, and find recipes for what is already in the fridge.",
        SIMPLE: "A dinosaur bot: tell me a new dinosaur fact every day and quiz me on "
                "them.",
    },
    "goal_example_2": {
        STANDARD: "A study buddy for high school biology: quiz me, explain answers in "
                  "simple words, and keep track of what I got wrong.",
        SIMPLE: "A reading buddy: help me pick books and ask me about the story.",
    },
    "goal_example_3": {
        STANDARD: "A home helper: keep our shared calendar, remind us about bills and "
                  "chores, and turn the porch light on at sunset.",
        SIMPLE: "A space bot: show me the planets and answer my space questions.",
    },
    "name_title": {
        STANDARD: "Its name",
        SIMPLE: "Its name",
    },
    "name_question": {
        STANDARD: "What should we call it?",
        SIMPLE: "What should we call it?",
    },
    "name_help": {
        STANDARD: "Press Enter to use {suggestion}, or type a name you like.",
        SIMPLE: "Press Enter to use {suggestion}.\nOr type a name you like.",
    },
    # The same in the browser, where there is a box and a Next button.
    "name_help_page": {
        STANDARD: "Keep {suggestion}, or type a name you like.",
        SIMPLE: "Keep {suggestion}, or type a name you like.",
    },
    # Optional first: it is not a question every adult must answer.
    # It says when it matters: only an age under 13 changes anything
    # (mode_for_band), so everyone else is told to leave it empty. At most cards.MAX_LABEL (120)
    # characters. The same words in the terminal and the browser.
    "age_question": {
        STANDARD: "How old are you? Only needed if you are under 13, so I can use "
                  "simpler words. Everyone else can leave it empty.",
        SIMPLE: "How old are you? Only needed if you are under 13, so I can use "
                "simpler words. Everyone else can leave it empty.",
    },
    "age_help": {
        STANDARD: "Only a rough age group is kept, never the number.",
        SIMPLE: "Only a rough age group is kept, never the number.",
    },
    # -- checking the computer --------------------------------------------
    "checking": {
        STANDARD: "Checking this computer...",
        SIMPLE: "Looking at your computer...",
    },
    "checked_memory": {
        STANDARD: "This computer has {memory} of memory and {disk} of free "
                  "space.",
        SIMPLE: "Your computer has {memory} of memory.",
    },
    "checked_online": {
        STANDARD: "The internet is reachable, so downloads can happen while "
                  "we build.",
        SIMPLE: "The internet works. We can download things.",
    },
    "checked_offline": {
        STANDARD: "The internet is not reachable from here, so nothing will be "
                  "downloaded while we build.",
        SIMPLE: "The internet does not work here.\nSo we will not download "
                "anything.",
    },
    "checked_unknown": {
        STANDARD: "I did not check the internet, so nothing will be downloaded "
                  "unless you turn it on below.",
        SIMPLE: "I did not check the internet.\nNothing will be downloaded "
                "unless you say so.",
    },
    # -- the one summary --------------------------------------------------
    "summary_title": {
        STANDARD: "Here is the plan for {name}",
        SIMPLE: "Here is the plan for {name}",
    },
    "summary_intro": {
        STANDARD: "Nothing has been downloaded or turned on yet. Read it, then "
                  "say \"Looks good\" or change something.",
        SIMPLE: "Nothing has happened yet.\nRead the plan.\nThen say if it "
                "looks good.",
    },
    "section_thinking": {
        STANDARD: "How it thinks", SIMPLE: "How it thinks"},
    "section_knowledge": {
        STANDARD: "What it will know", SIMPLE: "What it will know"},
    "section_live_info": {
        STANDARD: "Live info it can show you", SIMPLE: "Live info it can show you"},
    "section_outside_services": {
        STANDARD: "Websites and services it may use",
        SIMPLE: "Websites and services it may use"},
    "section_updates": {
        STANDARD: "Updates it can send you", SIMPLE: "Updates it can send you"},
    "section_private": {
        STANDARD: "Your private stuff", SIMPLE: "Your private stuff"},
    "section_devices": {
        STANDARD: "Your gadgets", SIMPLE: "Your gadgets"},
    "section_looks": {
        STANDARD: "How it looks", SIMPLE: "How it looks"},
    "section_empty": {
        STANDARD: "Nothing here yet.",
        SIMPLE: "Nothing here yet.",
    },
    "look_yourself_label": {
        STANDARD: "Worth a look: {name}",
        SIMPLE: "Worth a look: {name}",
    },
    "look_yourself_detail": {
        STANDARD: "The brain suggested this, but it is not on the lists "
                  "BuildBot checks. Nothing is downloaded from it and {name} "
                  "will not visit it. Look it up yourself if you want it.",
        SIMPLE: "The brain thought of this one. It is not on BuildBot's "
                "lists, so nothing is downloaded. You can look it up "
                "yourself.",
    },
    # Found, and not switched on. Says what it means and that it can be had
    # (so no one is left asking what they mean and why they were left out).
    "item_off": {
        STANDARD: "Not included (you can add it): {label}",
        SIMPLE: "Not included (you can add it): {label}",
    },
    # -- why a found thing is not included: one short plain reason ----------
    "off_offline": {
        STANDARD: "the internet is not reachable now",
        SIMPLE: "the internet is not working now"},
    "off_personal_use": {
        STANDARD: "free for personal use only",
        SIMPLE: "free for personal use only"},
    "off_big": {
        STANDARD: "a big download, so it waits for your yes",
        SIMPLE: "a big download, so it waits for your yes"},
    "off_size_unknown": {
        STANDARD: "its size is not known, so it waits for your yes",
        SIMPLE: "its size is not known, so it waits for your yes"},
    "off_bigger": {
        STANDARD: "bigger than the books already picked",
        SIMPLE: "bigger than the books already picked"},
    "off_general": {
        STANDARD: "a general encyclopedia, not about your subject",
        SIMPLE: "a book about everything, not just your subject"},
    "off_owner_pick": {
        STANDARD: "whole books like this are added only when you pick them",
        SIMPLE: "you pick books like this yourself"},
    "off_loose": {
        STANDARD: "matched your words more loosely",
        SIMPLE: "matched your words less well"},
    "off_outside": {
        STANDARD: "it reaches an outside service, so it is off until you say "
                  "yes",
        SIMPLE: "it uses the internet, so it waits for your yes"},
    "off_update": {
        STANDARD: "it starts off; switch it on if you want it",
        SIMPLE: "it starts off; switch it on if you want it"},
    "off_hub": {
        STANDARD: "only if you have one; nothing else needs it",
        SIMPLE: "only if you have one"},
    "off_brain": {
        STANDARD: "it waits for your yes",
        SIMPLE: "it waits for your yes"},
    "off_default": {
        STANDARD: "off until you switch it on",
        SIMPLE: "off until you switch it on"},
    # -- the first screen: what this computer has, before any question -----
    "first_look_title": {
        STANDARD: "Let's see what we're working with here",
        SIMPLE: "Let's see what we're working with here",
    },
    "first_look_intro": {
        STANDARD: "Before any questions, here is what BuildBot found on this "
                  "computer. Nothing has been downloaded or changed.",
        SIMPLE: "Before any questions, here is what BuildBot found on this "
                "computer. Nothing has been downloaded or changed.",
    },
    "first_look_recommend": {
        STANDARD: "The thinking model I recommend for this computer: {model}",
        SIMPLE: "The thinking model I recommend for this computer: {model}",
    },
    "first_look_others": {
        STANDARD: "Other thinking models that fit:",
        SIMPLE: "Other thinking models that fit:",
    },
    "first_look_use": {STANDARD: "Use this one", SIMPLE: "Use this one"},
    "first_look_change": {STANDARD: "Change", SIMPLE: "Change"},
    "first_look_prompt": {
        STANDARD: "Press Enter to use {model}, or type 2 to pick another.",
        SIMPLE: "Press Enter to use {model}, or type 2 to pick another.",
    },
    "first_look_go_on": {
        STANDARD: "Press Enter to go on.",
        SIMPLE: "Press Enter to go on.",
    },
    "first_look_which": {
        STANDARD: "Which thinking model? Type its number, or press Enter to "
                  "keep {model}.",
        SIMPLE: "Which thinking model? Type its number, or press Enter to "
                "keep {model}.",
    },
    "first_look_picked": {
        STANDARD: "The plan will use {model}. You can still change it there.",
        SIMPLE: "The plan will use {model}. You can still change it there.",
    },
    "team_label": {
        STANDARD: "A team of {count} helpers works inside {name}",
        SIMPLE: "{count} helpers work inside {name}",
    },
    "team_detail": {
        STANDARD: "Meet them later on the Agent Team page.",
        SIMPLE: "You can meet them later on the Agent Team page.",
    },
    "brain_off": {
        STANDARD: "Without a brain, {name} cannot answer anything until you "
                  "download one.",
        SIMPLE: "Without a brain, {name} cannot talk yet.",
    },
    "team_line": {
        STANDARD: "A team of {count} helpers works inside {name}; meet them "
                  "later on the Agent Team page.",
        SIMPLE: "{count} helpers work inside {name}.\nYou can meet them later "
                "on the Agent Team page.",
    },
    "total_download": {
        STANDARD: "Total to download: {size}.",
        SIMPLE: "Total to download: {size}.",
    },
    "total_nothing": {
        STANDARD: "Nothing will be downloaded.",
        SIMPLE: "Nothing will be downloaded.",
    },
    # The plan footer is drawn by
    # puffpai.plan_outside.footer; these two are kept for older callers and
    # say the same rule.
    "leaves_hosts": {
        STANDARD: "What leaves your computer: only what you say yes to, "
                  "here requests to {hosts}.",
        SIMPLE: "Only what you say yes to leaves your computer.\nYour "
                "computer will talk to: {hosts}.",
    },
    "leaves_nothing": {
        STANDARD: "What leaves your computer: only what you say yes to. "
                  "Nothing leaves your computer without your consent.",
        SIMPLE: "Nothing leaves your computer without your consent.",
    },
    "reach_line": {
        STANDARD: "Internet use: {choice}",
        SIMPLE: "Internet: {choice}",
    },
    "looks_good": {STANDARD: "Looks good", SIMPLE: "Looks good"},
    "change_something": {
        STANDARD: "Change something", SIMPLE: "Change something"},
    "choose_prompt": {
        STANDARD: "Press Enter for \"Looks good\", or type 2 to change "
                  "something.",
        SIMPLE: "Press Enter for \"Looks good\".\nOr type 2 to change "
                "something.",
    },
    "change_which": {
        STANDARD: "Which one? Type its number, or press Enter when you are "
                  "done.",
        SIMPLE: "Which one? Type its number.\nPress Enter when you are done.",
    },
    "change_many": {
        STANDARD: "To tick or untick several at once, type their numbers, "
                  "like 2 7.",
        SIMPLE: "You can type more than one number, like 2 7.",
    },
    "change_include": {
        STANDARD: "Include \"{label}\"? Type yes or no. Press Enter to keep "
                  "{current}.",
        SIMPLE: "Keep \"{label}\"? Type yes or no.",
    },
    "change_brain": {
        STANDARD: "Which brain should it use? Type a number. Press Enter to "
                  "keep this one.",
        SIMPLE: "Which brain? Type a number.",
    },
    "change_reach": {
        STANDARD: "How should {name} use the internet? Type a number. Press "
                  "Enter to keep this one.",
        SIMPLE: "How should {name} use the internet? Type a number.",
    },
    "reach_item": {
        STANDARD: "How {name} uses the internet: {choice}",
        SIMPLE: "Internet: {choice}",
    },
    "reach_offline": {
        STANDARD: "Keep {name} off the internet completely",
        SIMPLE: "Keep {name} off the internet",
    },
    "reach_only_listed": {
        STANDARD: "Only the services listed above",
        SIMPLE: "Only the services listed above",
    },
    # An older record's "ask each time" choice reads as what it always did:
    # only the services listed (it was never built; zone_bridge folds it).
    "reach_ask_each_time": {
        STANDARD: "Only the services listed above",
        SIMPLE: "Only the services listed above",
    },
    "leave_out_brain": {
        STANDARD: "Do not download a brain now",
        SIMPLE: "Not now",
    },
    "changed": {
        STANDARD: "Changed. Here is the plan again.",
        SIMPLE: "Done! Here is the plan again.",
    },
    # -- the home town, asked once when a line on the plan needs it -------
    "town_question": {
        STANDARD: "Which town are you in? (only the town, for the weather)",
        SIMPLE: "Which town do you live in? (just the town, for the weather)",
    },
    "town_help": {
        STANDARD: "Only the town name is sent, once, to {host} to find it on "
                  "the map. {name} keeps a rough position (to about 11 km), "
                  "never your street or house.",
        SIMPLE: "Only the town name is sent, once.\n{name} never knows your "
                "street.",
    },
    "town_skip": {
        STANDARD: "Leave it empty to skip. You can type your town on the "
                  "weather card on the home page later.",
        SIMPLE: "Press Enter to skip.\nYou can add it later on the weather "
                "card.",
    },
    "town_skipped": {
        STANDARD: "Skipped. Type your town on the weather card on the home "
                  "page whenever you like.",
        SIMPLE: "Skipped. You can add it later on the weather card.",
    },
    "approved": {
        STANDARD: "Great. Building {name} now.",
        SIMPLE: "Great! Building {name} now.",
    },
    "approved_by_flag": {
        STANDARD: "No one is here to answer, so only what you named on the "
                  "command line is downloaded or turned on.",
        SIMPLE: "No one is here to answer.\nSo only what the command line "
                "named is downloaded or turned on.",
    },
    "building": {
        STANDARD: "Now building {name}. Big downloads can take a while; leave "
                  "this window open.",
        SIMPLE: "Now building {name}.\nThis can take a while.",
    },
    # -- the vault --------------------------------------------------------
    "vault_title": {
        STANDARD: "Your private stuff", SIMPLE: "Your secret lock"},
    "vault_what": {
        STANDARD: "{name} keeps your private things in a locked vault, on "
                  "this computer or in a folder you choose. You choose the "
                  "PIN. It never leaves this computer and is never saved as "
                  "plain text.",
        SIMPLE: "{name} keeps your private things locked up.\nYou pick where "
                "it lives.\nYou pick the PIN.",
    },
    "vault_pin": {
        STANDARD: "Choose a vault PIN", SIMPLE: "Pick a PIN"},
    "vault_pin_help": {
        STANDARD: "Use at least 10 digits, or a mix of letters and numbers. "
                  "Easy ones like 1234567890 are turned down.",
        SIMPLE: "Use at least 10 numbers.\nDo not use easy ones like "
                "1234567890.",
    },
    "vault_pin_again": {
        STANDARD: "Type the same PIN again", SIMPLE: "Type it again"},
    "vault_pin_again_help": {
        STANDARD: "Both have to match, or no vault is made.",
        SIMPLE: "Both have to match.",
    },
    "vault_exists": {
        STANDARD: "A vault is already set up on this computer. It is left as "
                  "it is.",
        SIMPLE: "Your vault is already set up.",
    },
    "vault_skipped": {
        STANDARD: "You asked to skip the vault. Nothing is locked away until "
                  "you set a PIN.",
        SIMPLE: "No vault yet.",
    },
    "vault_no_screen": {
        STANDARD: "The PIN is set on the next screen, where your recovery code "
                  "can be shown to you once.",
        SIMPLE: "You pick your PIN on the next screen.",
    },
    "vault_no_keyboard": {
        STANDARD: "No one is here to type a PIN, so no vault is made on this "
                  "run. Run puffpai setup at a keyboard, or pass --vault-pin.",
        SIMPLE: "No one is here to type a PIN, so no vault is made now.",
    },
    # -- where the vault lives -------------------------------------------
    "vault_where": {
        STANDARD: "Where should your vault live?",
        SIMPLE: "Where should your vault live?",
    },
    "vault_where_help": {
        STANDARD: "Press Enter to keep it on this computer.",
        SIMPLE: "Press Enter to keep it on this computer.",
    },
    "vault_folder": {
        STANDARD: "Which folder?",
        SIMPLE: "Which folder?",
    },
    "vault_folder_help": {
        STANDARD: "Only for a drive or folder you choose, or a vault you "
                  "already have. Type the whole path, like E:\\PuffPAI Vault "
                  "or \\\\mynas\\vault. A drive you already lock: unlock that "
                  "drive yourself first, then BuildBot keeps its vault inside "
                  "it.",
        SIMPLE: "Only if you picked your own folder. Type the whole path, "
                "like E:\\PuffPAI Vault.",
    },
    "vault_pin_existing_help": {
        STANDARD: "If you picked a vault you already have, type its PIN "
                  "instead.",
        SIMPLE: "If you already have a vault, type its PIN.",
    },
    # -- "Finding out what a <kind> bot needs" -------------------------------
    "needs_title_unknown": {
        STANDARD: "Finding out what your bot needs",
        SIMPLE: "Finding out what your bot needs",
    },
    "needs_title": {
        STANDARD: "Finding out what a {kind} bot needs",
        SIMPLE: "Finding out what a {kind} bot needs",
    },
    "needs_reading": {
        STANDARD: "Reading what you wrote ...",
        SIMPLE: "Reading what you wrote ...",
    },
    "needs_reading_done": {
        STANDARD: "Reading what you wrote ... done",
        SIMPLE: "Reading what you wrote ... done",
    },
    "needs_from_words": {
        STANDARD: "From your words: {count} things",
        SIMPLE: "From your words: {count} things",
    },
    "needs_asking": {
        STANDARD: "Asking your bot's brain what {kind} bots usually need ...",
        SIMPLE: "Asking your bot's brain what {kind} bots usually need ...",
    },
    "needs_asking_done": {
        STANDARD: "Asking your bot's brain what {kind} bots usually need ... "
                  "{count} more",
        SIMPLE: "Asking your bot's brain what {kind} bots usually need ... "
                "{count} more",
    },
    "needs_private": {
        STANDARD: "Kept private, never searched: {words}",
        SIMPLE: "Kept private, never searched: {words}",
    },
    "needs_slow": {
        STANDARD: "Your bot's brain is slow on this computer, so BuildBot used "
                  "your words and a short list.",
        SIMPLE: "Your bot's brain is slow on this computer.\nSo BuildBot used "
                "your words and a short list.",
    },
    "needs_failed": {
        STANDARD: "Your bot's brain did not answer in time, so this list comes "
                  "from your own words. You can add more below.",
        SIMPLE: "Your bot's brain did not answer in time.\nSo this list comes "
                "from your own words.\nYou can add more below.",
    },
    "needs_held": {
        STANDARD: "Not searched unless you tick it:",
        SIMPLE: "Not searched unless you tick it:",
    },
    "needs_intro": {
        STANDARD: "Here is what a {kind} bot needs. Untick anything you do not "
                  "want, or add your own.",
        SIMPLE: "Here is what a {kind} bot needs.\nUntick anything you do not "
                "want.\nOr add your own.",
    },
    "needs_intro_unknown": {
        STANDARD: "Here is what your bot needs. Untick anything you do not "
                  "want, or add your own.",
        SIMPLE: "Here is what your bot needs.\nUntick anything you do not "
                "want.\nOr add your own.",
    },
    "needs_no_goal": {
        STANDARD: "Say what kind of bot you want first.",
        SIMPLE: "Say what kind of bot you want first.",
    },
    "needs_not_built": {
        STANDARD: "Finding out what your bot needs is not in this build yet.",
        SIMPLE: "Finding out what your bot needs is not in this build yet.",
    },
    "needs_already_looking": {
        STANDARD: "BuildBot has already started looking, so the list cannot "
                  "change now. You can add more from the Add page later.",
        SIMPLE: "BuildBot has already started looking.\nYou can add more "
                "from the Add page later.",
    },
    "needs_chip_asked": {STANDARD: "you asked", SIMPLE: "you asked"},
    "needs_chip_usual": {
        STANDARD: "most {kind} bots have this",
        SIMPLE: "most {kind} bots have this",
    },
    "needs_chip_added": {STANDARD: "you added", SIMPLE: "you added"},
    "needs_add": {
        STANDARD: "Add something in your own words",
        SIMPLE: "Add something in your own words",
    },
    "needs_add_prompt": {
        STANDARD: "What else should it do? Say it in your own words.",
        SIMPLE: "What else should it do?",
    },
    "needs_words_prompt": {
        STANDARD: "Which word may BuildBot search with? Type its number.",
        SIMPLE: "Which word may BuildBot search with? Type its number.",
    },
    "needs_searching_with": {
        STANDARD: "Searching with: {subject}",
        SIMPLE: "Searching with: {subject}",
    },
    "needs_health_note": {
        STANDARD: " (a health word; choose \"Only look on this computer\" to "
                  "keep it here)",
        SIMPLE: " (a health word; choose \"Only look on this computer\" to "
                "keep it here)",
    },
    "needs_private_note": {
        STANDARD: " (a private subject; choose \"Only look on this computer\" to "
                  "keep it here)",
        SIMPLE: " (a private subject; choose \"Only look on this computer\" to "
                "keep it here)",
    },
    "needs_brand_note": {STANDARD: " (a brand name)", SIMPLE: " (a brand name)"},
    "needs_job_words": {
        STANDARD: "and these words for your jobs: {phrases}",
        SIMPLE: "and these words for your jobs: {phrases}",
    },
    "needs_only_words": {
        STANDARD: "Searching with these words for your jobs: {phrases}",
        SIMPLE: "Searching with these words for your jobs: {phrases}",
    },
    "needs_sources": {
        STANDARD: "BuildBot looks on this computer first, then asks {sources}.",
        SIMPLE: "BuildBot looks on this computer first, then asks {sources}.",
    },
    "needs_sources_none": {
        STANDARD: "BuildBot looks only on this computer.",
        SIMPLE: "BuildBot looks only on this computer.",
    },
    # The look step says, before anything runs,
    # that only these words go to those sites, and only on her press.
    "needs_nothing_else": {
        STANDARD: "Only these words go to those sites, and only when you "
                  "press Start looking. Nothing leaves your computer without "
                  "your consent.",
        SIMPLE: "Only these words go out, and only when you press Start "
                "looking.\nNothing leaves your computer without your consent.",
    },
    "needs_start": {STANDARD: "Start looking", SIMPLE: "Start looking"},
    "needs_here": {
        STANDARD: "Only look on this computer",
        SIMPLE: "Only look on this computer",
    },
    "needs_offline": {
        STANDARD: "This build does not use the internet, so BuildBot looks "
                  "only on this computer.",
        SIMPLE: "This build does not use the internet.\nSo BuildBot looks "
                "only on this computer.",
    },
    "needs_terminal_prompt": {
        STANDARD: "Type numbers to tick or untick (like 2 7), \"add\" to add "
                  "your own, \"words\" to allow a held word, or press Enter to "
                  "start looking. Type \"here\" to look only on this computer.",
        SIMPLE: "Type numbers to tick or untick them, like 2 7.\nType \"add\" "
                "to add your own.\nType \"words\" to allow a held word.\n"
                "Press Enter to start looking.\nType \"here\" to look only on "
                "this computer.",
    },
    "needs_terminal_prompt_offline": {
        STANDARD: "Type numbers to tick or untick (like 2 7), \"add\" to add "
                  "your own, or press Enter to start looking.",
        SIMPLE: "Type numbers to tick or untick them, like 2 7.\nType \"add\" "
                "to add your own.\nPress Enter to start looking.",
    },
    "needs_wait": {
        STANDARD: "Still finding out what your bot needs. Try again in a "
                  "moment.",
        SIMPLE: "Still finding out what your bot needs.\nTry again in a "
                "moment.",
    },
    "needs_stopped": {
        STANDARD: "Something went wrong while finding out what your bot needs, "
                  "so the plan uses what BuildBot already knows.",
        SIMPLE: "Something went wrong.\nSo the plan uses what BuildBot already "
                "knows.",
    },
    "looking_title": {
        STANDARD: "Looking for things your bot can use",
        SIMPLE: "Looking for things your bot can use",
    },
    "looking_checked": {
        STANDARD: "Checking what fits your jobs ... {kept} kept, {left_out} "
                  "left out",
        SIMPLE: "Checking what fits your jobs ... {kept} kept, {left_out} "
                "left out",
    },
    "looking_stopped": {
        STANDARD: "Stopped looking to keep this quick.",
        SIMPLE: "Stopped looking to keep this quick.",
    },
    "looking_failed": {
        STANDARD: "Something went wrong while looking. The plan shows what "
                  "BuildBot could find on this computer.",
        SIMPLE: "Something went wrong while looking.\nThe plan shows what "
                "BuildBot found on this computer.",
    },
    # -- the plan grouped by job ----------------------------------------------
    "plan_what_it_does": {STANDARD: "What it will do", SIMPLE: "What it will do"},
    "plan_also_found": {
        STANDARD: "Also found, look at it yourself:",
        SIMPLE: "Also found, look at it yourself:",
    },
    "plan_not_reached": {
        STANDARD: "Did not get to look for \"{job}\" this time. You can look "
                  "from the Add page.",
        SIMPLE: "Did not get to look for \"{job}\" this time.\nYou can look "
                "from the Add page.",
    },
    # -- similar projects, the reading helper, Building ----------------------
    "plan_similar_heading": {
        STANDARD: "Similar projects also have (tick to add):",
        SIMPLE: "Similar projects also have (tick to add):",
    },
    "plan_similar_line": {
        STANDARD: "{name}    seen in {n} similar projects",
        SIMPLE: "{name}    seen in {n} similar projects",
    },
    "plan_moved_to": {
        STANDARD: "What BuildBot found for this is now under \"{job}\".",
        SIMPLE: "What BuildBot found for this is now under \"{job}\".",
    },
    "plan_similar_unknown": {
        STANDARD: "That is not one of the similar projects on the plan.",
        SIMPLE: "That is not one of the similar projects on the plan.",
    },
    "plan_similar_later": {
        STANDARD: "Adding that is not in this build yet. Nothing was changed.",
        SIMPLE: "Adding that is not in this build yet. Nothing was changed.",
    },
    "needs_helper_offer": {
        STANDARD: "BuildBot's reading helper is not on this computer. It lets "
                  "BuildBot tell which finds really fit your jobs. Get it now? "
                  "(83 MB, from the same place as when BuildBot was installed)",
        SIMPLE: "BuildBot's reading helper is not on this computer. It lets "
                "BuildBot tell which finds really fit your jobs. Get it now? "
                "(83 MB, from the same place as when BuildBot was installed)",
    },
    "needs_helper_get": {STANDARD: "Get it", SIMPLE: "Get it"},
    "needs_helper_not_now": {STANDARD: "Not now", SIMPLE: "Not now"},
    "needs_helper_prompt": {
        STANDARD: "Type yes to get it now, or press Enter for not now.",
        SIMPLE: "Type yes to get it now.\nPress Enter for not now.",
    },
    "needs_helper_sentence": {
        STANDARD: "Get BuildBot's reading helper (83 MB).",
        SIMPLE: "Get BuildBot's reading helper (83 MB).",
    },
    "needs_helper_later": {
        STANDARD: "Getting the reading helper is not in this build yet, so "
                  "nothing was downloaded.",
        SIMPLE: "Getting the reading helper is not in this build yet.\nSo "
                "nothing was downloaded.",
    },
    "needs_helper_failed": {
        STANDARD: "The reading helper could not be got right now. Nothing "
                  "else changed.",
        SIMPLE: "The reading helper could not be got right now.\nNothing "
                "else changed.",
    },
    "building_held_yes": {STANDARD: "Add it anyway", SIMPLE: "Add it anyway"},
    "building_held_no": {STANDARD: "Leave it out", SIMPLE: "Leave it out"},
    "building_held_prompt": {
        STANDARD: "Add {name} anyway? Type yes or no.",
        SIMPLE: "Add {name} anyway? Type yes or no.",
    },
    "building_wsl": {
        STANDARD: "Windows needs its Linux feature turned on for {n} {things} "
                  "you picked.",
        SIMPLE: "Windows needs its Linux feature turned on for {n} {things} "
                "you picked.",
    },
    "building_wsl_button": {STANDARD: "Turn it on", SIMPLE: "Turn it on"},
    "building_wsl_after": {
        STANDARD: "(Windows will ask for permission.) After that, restart the "
                  "computer and open BuildBot. It will finish them then.",
        SIMPLE: "(Windows will ask for permission.) After that, restart the "
                "computer and open BuildBot. It will finish them then.",
    },
    "building_wsl_prompt": {
        STANDARD: "Turn it on now? Type yes or no.",
        SIMPLE: "Turn it on now? Type yes or no.",
    },
    "building_wsl_sentence": {
        STANDARD: "Turn on Windows' Linux feature (WSL) for the locked box.",
        SIMPLE: "Turn on Windows' Linux feature (WSL) for the locked box.",
    },
    "building_wsl_later": {
        STANDARD: "Turning it on from here is not in this build yet. Nothing "
                  "was changed.",
        SIMPLE: "Turning it on from here is not in this build yet. Nothing "
                "was changed.",
    },
    "building_go_on": {
        STANDARD: "Go on without them for now",
        SIMPLE: "Go on without them for now",
    },
    "plan_box_off": {
        STANDARD: "Needs the locked box, so it is off too.",
        SIMPLE: "Needs the locked box, so it is off too.",
    },
    "section_apps_code": {STANDARD: "Apps and code", SIMPLE: "Apps and code"},
    "section_pictures": {STANDARD: "Pictures", SIMPLE: "Pictures"},
    "section_box": {STANDARD: "The locked box", SIMPLE: "The locked box"},
}

#: The note find_parts gives a job it searched and found nothing addable for.
#: It is a line of its own under the job, not one of the "Also
#: found" finds, so the plan does not put that heading over it.
FOUND_NOTHING = ("BuildBot found nothing ready-made it can add for this. Your "
                 "bot's brain can still help with it in chat.")


def say(key: str, mode: str = STANDARD, **values: Any) -> str:
    """The sentence for *key* in *mode*, filled in. Never raises.

    An unknown key is a programming mistake, and it shows up as the key itself
    rather than as a crash in front of a builder -- the plain-words test then
    fails on it, which is where it should be caught.
    """
    entry = TEXT.get(key)
    if not entry:
        return key
    text = entry.get(mode) or entry.get(STANDARD) or key
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


#: How many sample goals the goal question shows.
GOAL_EXAMPLES = 3


def goal_examples(mode: str = STANDARD) -> list[str]:
    """The sample goals under the goal question, in *mode*."""
    return [say(f"goal_example_{n}", mode) for n in range(1, GOAL_EXAMPLES + 1)]


def lines(key: str, mode: str = STANDARD, **values: Any) -> list[str]:
    """:func:`say`, split on the line breaks the simple wording uses."""
    return [part for part in say(key, mode, **values).split("\n")]


__all__ = ["ADULT", "AGE_BANDS", "BAND_WORDS", "MODES", "SIMPLE",
           "SIMPLE_MAX_CHOICES", "STANDARD", "TEEN", "TEXT", "UNDER_13",
           "band_for_age", "goal_examples", "lines", "mode_for_band", "mode_of", "say"]
