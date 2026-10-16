"""Every owner-facing sentence the integration part says, in one place.

Plain everyday words (the owner reads these). Each template is also listed in
``tests/fixtures/ui_strings/integrate.txt`` so the owner-words test reads it.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

NOT_IN_BUILD = "This is not in this build yet. Nothing was changed."

# -- Building lines ------------------------------------------------------------
ADDING = "Adding {name}"
DOWNLOADED = "  Downloaded the version from {when} ({size}) ... done"
DOWNLOADED_NO_DATE = "  Downloaded the version BuildBot picked ({size}) ... done"
CHECKED = "  Drawbridge checked it: {license_words}, size is fine, no hidden connections ... done"
#: Her own GitHub project: the licence rule is waived (puffpai.github_account).
OWN_PROJECT = "Your own project"
OWN_PROJECT_WHY = "It is your own project on GitHub, so its licence is yours to decide."
SET_UP_WEB = "  Set it up to open inside your bot ... done"
TAUGHT = "  Taught your bot what it can do there: {abilities} ... done"
CANNOT_DRIVE = ("Your bot can open it for you, but cannot use it by itself yet "
                "(it works in a way BuildBot cannot drive).")

# -- Held after the check ---------------------------------------------------------
HELD = "Drawbridge found something the plan did not show: {reason}."
HELD_WAITING = "It is waiting for your yes."

REASONS = {
    "license_mismatch": "its licence file says {words}, not what GitHub said",
    "runtime_hosts_new": "it wants to reach {host}, which the plan did not show",
    "size_over_plan": "it is much bigger than the plan said ({mb} MB)",
    "box_needed": "it has install steps of its own",
    "host_run": "it runs as its own program on this computer, with your permissions",
    "needs_program": "it needs a program this computer does not have yet",
    "ai_unreviewed": "its description says it is written by an AI and not checked by a person",
    "scaffold": "it has little code for what it claims to do",
    "pii_rows": "its data has what looks like personal details (names, phone numbers)",
    "mode_changed": "it turned out to be a different kind of project than the plan said",
    "too_many_rows": "it has more rows than one list keeps (5,000), so only the first 5,000 would be kept",
    "lock_offsite": "it wants parts from somewhere other than the usual place for them",
    # Its files are not the ones she said yes to: every earlier yes is void
    # and each finding is asked again (fix-honesty final, held-after-yes).
    "files_changed": "its files changed after you were asked, so every check runs again",
    # The app host's own holds, named so a yes never covers an unnamed one.
    "parts_needed": "it still loads parts of itself from the internet while it runs",
    "not_a_tool": "it looks like a page with nothing to use",
}
#: A hold code with no reason sentence of its own (fail closed: still held).
REASON_OTHER = "one of its checks did not pass"

# -- Stopped and failed --------------------------------------------------------------
STOPPED = "Drawbridge stopped this one: {reason}. It was not added."
BLOCK_REASONS = {
    "wrong_host": "it did not come from the place the plan named",
    "not_pinned": "it was not the exact version the plan showed",
    "pin_changed": "the download changed since the plan was made",
    "no_license": "it has no licence file, so nobody says you may use it",
    "not_open": "its licence does not let you use it freely",
    "too_big": "it is too big to add",
    "orders": "some of its words read like orders to a computer",
    "instruction_text": "some of its words read like orders to a computer",
    "tunnel": "it has code that opens a hidden connection to the internet",
    "hidden_loader": "its page hides encoded code that it unpacks and runs",
    "check_failed": "the checks could not finish",
    "unknown_kind": "BuildBot does not know what kind of thing it is",
    "wrong_type": "it has files BuildBot does not read as data",
    "bad_fields": "a column name in its data is not a plain word",
    "unreadable": "a file in it could not be read",
    "not_raster": "it is not an ordinary picture",
    "bad_picture": "the picture did not open",
    "folder": "that folder cannot be given to an app",
    "no_lock": "it has no recorded list of its parts, so it cannot be built the same way twice",
}

# -- Other kinds of things ----------------------------------------------------------
DATA_CREDIT = "From {url} ({license})"
DATA_ADDED = "  Adding {name} ... done: {rows} rows your bot can read"
TEXT_ADDED = "  Adding {name} ... done: its pages are in what your bot knows"
PICTURES_NONE = "It had no pictures BuildBot could keep, so it was left out. Nothing else changed."
PICTURE_SET_ADDED = "  Kept {n} pictures from {name} ... done"
PICTURES_PURPOSE = "Pictures for your bot, with who made each one."
PICTURE_GONE = "BuildBot could not find that picture any more. Nothing was changed."
PICTURE_ADDED = "  Kept the picture {title} in {shelf} ... done"
NO_LOCK = ("It has no recorded list of its parts, so BuildBot cannot build it the same "
           "way twice. It was left out; nothing else changed.")
BUILDING_IN_BOX = "  Building it in the locked box ... this can take a few minutes"
BUILD_FAILED = ("This project does not build right now. It was left out; nothing else "
                "changed.")
#: Which step of the build in the locked box failed, and
#: why when the box can tell (never the project's own output).
BUILD_FAILED_STEP = {
    "put": ("It could not be copied into the locked box to be built. It was left out; "
            "nothing else changed."),
    "install": ("It could not be built: its parts did not install in the locked box{why}. "
                "It was left out; nothing else changed."),
    "build": ("It could not be built: its own build step stopped in the locked box{why}. "
              "It was left out; nothing else changed."),
    "export": ("It built, but the built files could not be copied out of the locked box. "
               "It was left out; nothing else changed."),
    "no_page": ("It built, but it has no page to open (no index.html), so it cannot run as "
                "an app on its own. It was left out; nothing else changed."),
}
BUILD_WHY = {
    "node": " (a part it needs does not work with this computer's version of Node.js)",
    "time": " (it took longer than the locked box allows)",
    "memory": " (it needed more memory than the locked box allows)",
    "space": " (it needed more disk space than the locked box allows)",
}
#: Its parts come from a place the locked box does not fetch from.
BUILD_HOSTS = ("It could not be built: its list of parts fetches them from {hosts}, and "
               "the locked box only fetches parts from the usual package site. It was "
               "left out; nothing else changed.")
PALETTE_NAME = "Mood board"
PALETTE_PURPOSE = "Colours and themes from your own pictures."
PALETTE_ADDED = ("  Put a Mood board on your home page ... done. Choose a pictures folder "
                 "there and your bot will find the colours and themes in it.")
#: A second plan line served by the Mood board already made (one per bot).
PALETTE_SAME = "  Uses the same Mood board on your home page ... done"
PALETTE_MADE = "Looked at {n} pictures and found {themes} colour themes."
PALETTE_SAYS = "Show the colours and themes from your pictures"
PALETTE_FOLDER_YES = "Let {name} read the pictures in this folder (it never changes them)."
PALETTE_ASK = "Choose a pictures folder"
FOLDER_REFUSED = "That folder holds private PuffPAI or system files, so no app can have it."
FOLDER_PAIRED_ONLY = ("Giving an app a folder has to be done from a browser that is paired "
                      "with your bot.")

# -- abilities, tiles, the terminal -------------------------------------------------
ABILITY_USE = "Use {name}: {says}"
ABILITY_OPEN = "Open {name} inside your bot (your bot cannot use it by itself yet)"
ABILITY_OUTSIDE = "{name} can reach {host} (switched {state})"
TILE_LINE = "{name}. {purpose}. {abilities}"
TILE_ABILITIES = "Your bot can {says} here."
TILE_OPENS = "Opens inside your bot."
TILE_ELSEWHERE = "Open this on the computer BuildBot runs on."
FINISH_LINE = "Finish adding {n} things that were waiting for the locked box"
FINISH_LABEL = "Finish"
CLI_NONE = "Toolsmith has not added anything yet."
CLI_NEED_ID = "Say which one: puffpai integrations remove <id> (the id is in the list)."
BLOCK_OTHER = "one of its checks failed"
OFFLINE = ("This needs the internet to download, and the build is offline. It "
           "was left out; you can add it later from the Add page.")
DOWNLOAD_FAILED = "The download did not work, so it was left out. Nothing else changed."
SET_UP_FAILED = "Setting it up did not work, so it was left out. Nothing else changed."

# -- On this computer, as its authors built it --------------------------------------
CARD_READY = "{name} is checked and ready for your yes."
CARD_CHECKED = "Drawbridge checked it: it passed every check."
CARD_HOLD = "Drawbridge checked it and noticed this: {reason}. Your yes covers it."
CARD_PAGE = "It opens as a page inside your bot."
CARD_BOX = "It is built in the locked box, then opens as a page inside your bot."
HOST_ASK = "{name} runs on this computer, the way its authors built it."
HOST_HELD = "{name} is waiting for your yes to run on this computer."
HOST_RUNNING = "  Started it. It opens at {url} ... done"
HOST_STARTING = "  It is starting. Its page shows when it opens."
HOST_NOT_STARTED = "  It did not start yet: {why} You can start it on its page."
HOST_TOOL_READY = "  Its command line tools are ready for your bot: {names} ... done"
HOST_LIBRARY = "  Kept it on this computer for other programs to use ... done"
HOST_MCP = "  Your bot can use its own tools: {line}"
HOST_CANNOT = "BuildBot cannot run it on this computer: {why}. Nothing was changed."
HOST_NEEDS = "{line} Nothing was set up yet; it stays here until that is installed."
HOST_FAILED = "{line} It was left out; nothing else changed."
ADDED = "Added {name}."
LEFT_OUT = "Left out {name}. Nothing was added."
STILL_HELD_CHANGED = ("{name} changed since you were asked, so it is still "
                      "waiting. Look at the new reason first.")
DECIDE_NOT_PAIRED = ("Saying yes to this has to be done from a browser that is "
                     "paired with your bot.")
NOT_WAITING = "That is not waiting for a yes."
UNKNOWN_ITEM = "BuildBot does not know that one."
REMOVED = "Removed {name}. A copy of its data is kept for 30 days in case you want it back."
REMOVE_FAILED = ("{name} was not removed: BuildBot could not keep a copy of its data first. "
                 "Nothing was changed.")
REMOVE_PAIRED_ONLY = "Removing an app has to be done from a browser that is paired with your bot."

# -- Chat -----------------------------------------------------------------------------
CHANGED_UNDO = 'Say "undo" to take it back.'
CONFIRM_REMOVE = 'That removes {what} from {app}. Remove it? (say "yes, remove")'
VOICE_REMOVE = "Open your dashboard to confirm removing that."
VOICE_CHANGE = "Open your dashboard to do that."
WHICH_APP = "Should I use {a} or {b}?"
READ_NONE = "{app} has nothing there yet."
READ_ROWS = "{n} {things} in {app}: {values} (read from {app})"
READ_MORE = "and {n} more"
DONE_CHANGE = "Done in {app}: {says}."
NEED_MORE = "Tell me the {field} and I will do it in {app}."
NEED_TICK = "Tell me plainly whether to tick {field} or clear it, and I will do it in {app}."
#: What went in, and what she said that did not.
VALUES_SET = "What I put in: {values}."
NOT_SET = "I could not set the {fields} from your words. Tell me which one you want."
EDIT_NOTHING = ("I have not changed anything in your apps: I could not tell what to change "
                "about {what} in {app}. Tell me the new {fields}.")
NOTHING_FITS = ("I have not changed anything in your apps: I could not tell which thing "
                "{app} does that is. It can {can}.")
UNDO_OTHER_APP = ("The last change I made was in {last}, not {named}, so I did not undo "
                  "anything. Say \"undo\" to take back the change in {last}.")
#: Reads that answer the question.
READ_WHOLE_HINT = 'Ask for "the whole list" to hear every one.'
READ_PAGE_MORE = 'That is {shown} of {n}. Say "more" to hear the next ones.'
READ_PAGE = "{first} to {last} of {n} in {app}: {values} (read from {app})"
READ_FILTERED = "{n} {things} in {app} are {value}: {values} (read from {app})"
READ_FILTERED_ONE = "1 {thing} in {app} is {value}: {values} (read from {app})"
READ_FILTERED_NONE = "No {things} in {app} are {value} yet (read from {app})."
#: "who isn't coming?": everyone the app does not mark that way,
#: by what it does mark them.
READ_NOT_VALUE = "{n} {things} in {app} are not {value}. {groups} (read from {app})"
READ_NOT_VALUE_ONE = "1 {thing} in {app} is not {value}. {groups} (read from {app})"
READ_NOT_VALUE_NONE = "Every one of the {things} in {app} is {value} (read from {app})."
#: "is Leo off the list?" when he is still there, or is not.
READ_STILL_IN = "No, {name} is in {app}{details} (read from {app})."
READ_NOT_IN = "Right, I did not find {name} in {app} (read from {app})."
#: An edit or removal for a name that is none of the app's rows.
NO_SUCH_ROW = "I have not changed anything in your apps: no {thing} in {app} is named {name}."
WHICH_ROW = ("I have not changed anything in your apps: {app} has {n} {things} called "
             "{first} ({names}). Say the whole name.")
READ_CHOICES = ('It does not keep that in those words: it marks each one {choices}. '
                'Ask "who is {first}?" to hear just those.')
READ_YES = "Yes, {name} is in {app}{details} (read from {app})."
READ_YES_VALUE = "Yes, {name} is {value} in {app} (read from {app})."
READ_NO_VALUE = "No, {name} is {actual} in {app}, not {value} (read from {app})."
READ_NO = "No, I did not find {name} in {app} (read from {app})."
READ_SEVERAL = "{n} {things} in {app} match {name}: {values} (read from {app})."
KEPT = "Kept it. Nothing was removed."
REMOVE_EXPIRED = "That question is more than 10 minutes old, so nothing was removed. Ask again."
ACTION_FAILED = "{app} did not do that. Nothing was changed."
#: The app said it was done, but its data is exactly what it was before.
NOTHING_CHANGED = ("Nothing changed in {app}: it took the request, but its data is the same "
                   "as before.")
NO_UNDO = "There is nothing of yours to undo from the last 10 minutes."
UNDO_CHANGED = ("The app changed after that, so I did not undo it. Open it to "
                "fix it by hand.")
UNDONE = "Undone. {app} is back to how it was."
NOT_READY = "{app} is not ready to use yet, so nothing was done."
LIST_BUSY = "That list changed while I was saving, so nothing was saved. Say it again."
LIST_NOT_SAVED = "I could not save that list, so nothing was changed."
LIST_UNDONE = "Undone. The list is back to how it was."
#: Said when the brain streamed no words at all (never a change claim).
NO_BRAIN_ANSWER = "The brain gave no answer. Try again."
#: Said in place of any model sentence that claims an app action (none ran).
NO_APP_CHANGE = "I have not changed anything in your apps."
#: Said in place of the model when she asked for a change and none ran.
NO_CHANGE_UNKNOWN = ("I have not changed anything in your apps: I could not tell which "
                     "list or app that is for. Say its name too.")
#: A yes right after the bot talked about her apps or lists: nothing ran.
NO_CHANGE_YES = ("I have not changed anything in your apps. If you want a change, say it "
                 "in your own words, with the list or app it is for.")
#: The same, to a caller with no key (anything on the home network).
NO_CHANGE_KEYLESS = ("I have not changed anything in your apps. They can only be changed "
                     "from a paired device, from this computer, or by Home Assistant "
                     "with its voice key.")
#: "Did you add ...?", "did that work?": answered from the change log, never
#: by the model.
STATUS_NONE = "I have not changed anything in your apps in the last {minutes} minutes."
STATUS_SOME = "Here is what I changed in your apps in the last {minutes} minutes: {lines}"
STATUS_CHECK = 'To check a list, ask "what is on" it.'
#: "Who are you?" from a caller with no key: nothing of the owner's.
KEYLESS_WHO = ("I'm a private helper that runs on a computer in this home. This device has "
               "no key, so I cannot tell it anything about the people who live here or "
               "their plans.")
#: A message shaped like a vault PIN, with no vault question waiting for it.
PIN_NOT_KEPT = ("That looks like it could be a PIN, so I did not keep it, show it or send it "
                "anywhere. To open your vault, ask about what you want from it first.")
#: Home Assistant voice asked about a list in a way it cannot change it.
VOICE_LIST_HELP = ('Say "add tomatoes to the {label} list" or "what is on the {label} '
                   'list". Removing things waits for your dashboard.')

# -- Home Assistant voice key -----------------------------------------------------
VOICE_KEY_TITLE = "Home Assistant voice"
VOICE_KEY_LINE = ("To let Home Assistant voice use your apps, give Home Assistant "
                  "this voice key.")
VOICE_KEY_WHAT = ("With it, Home Assistant voice can read your apps and make changes "
                  "you can undo. Removing things always waits for your dashboard.")
VOICE_KEY_STEPS = (
    "In Home Assistant, open the voice assistant setup that talks to your bot "
    "(the OpenAI-compatible conversation one).",
    "Paste this voice key where it asks for the key, then save.",
    "Ask Home Assistant something about one of your apps to try it.",
)
VOICE_KEY_OFF_LINE = ("Home Assistant voice has no key, so it can chat but cannot "
                      "use your apps.")
VOICE_KEY_NEW = "Make a voice key"
VOICE_KEY_REMAKE = "Make a new voice key (the old one stops working)"
VOICE_KEY_TURN_OFF = "Turn off voice use of your apps"
VOICE_KEY_MADE = ("Made a new voice key. Paste it into Home Assistant; the old one "
                  "no longer works.")
VOICE_KEY_TURNED_OFF = ("Home Assistant voice can no longer use your apps. It can still "
                        "chat.")
VOICE_KEY_PAIRED_ONLY = ("The voice key can only be shown in a browser that is paired "
                         "with your bot.")
#: The home page's way to the voice key page (shown when Home Assistant is set
#: up and the bot has apps it can use, or when a voice key is already on).
VOICE_KEY_HREF = "/dashboard/keys/voice"
VOICE_KEY_LINK_LABEL = "Home Assistant voice key"
VOICE_KEY_LINK_OFF = ("To let Home Assistant voice use your apps, give Home Assistant "
                      "its voice key.")
VOICE_KEY_LINK_ON = "Home Assistant voice can use your apps. See or change its voice key."


def reason_words(code: str, **facts) -> str:
    """The plain reason for one hold code, filled from the check facts."""
    template = REASONS.get(code)
    if template is None:
        return REASON_OTHER
    try:
        return template.format(**facts)
    except (KeyError, IndexError, ValueError):
        return REASON_OTHER


def hosts_words(hosts) -> str:
    """``a``, ``a and b``, ``a, b and c``, ``a, b, c and 2 more``: every
    outside address a hold names, so a yes never covers one she was not told."""
    names = [str(h) for h in hosts or () if str(h)]
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) > 3:
        return ", ".join(names[:3]) + f" and {len(names) - 3} more"
    return ", ".join(names[:-1]) + " and " + names[-1]


def block_words(code: str) -> str:
    return BLOCK_REASONS.get(code, BLOCK_OTHER)


def size_words(n_bytes: int) -> str:
    """``1.2 MB`` / ``340 KB`` for a byte count."""
    try:
        n = max(0, int(n_bytes))
    except (TypeError, ValueError):
        n = 0
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{max(1, round(n / 1024))} KB"


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def month_words(iso_date: str) -> str:
    """``June 2026`` from ``2026-06-18...``; "" when it is not a date."""
    text = str(iso_date or "")
    if len(text) >= 7 and text[:4].isdigit() and text[4] == "-" and text[5:7].isdigit():
        month = int(text[5:7])
        if 1 <= month <= 12:
            return f"{_MONTHS[month - 1]} {text[:4]}"
    return ""


__all__ = [name for name in dir() if name.isupper()] + [
    "block_words", "hosts_words", "month_words", "reason_words", "size_words"]
