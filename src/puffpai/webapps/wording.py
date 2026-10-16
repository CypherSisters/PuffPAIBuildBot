"""Every sentence the owner can read from the web apps part, in one place.

Plain everyday words, no em dashes. Each line here is also listed in
``tests/fixtures/ui_strings/webapps.txt`` for the owner-words test.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

FRAME_ONLY = "This app opens inside your bot's dashboard."
UNKNOWN_SITE = "Unknown site name."
NOT_FOUND = "Not found."
READ_ONLY = "This app's files cannot be changed."
REFUSED = "That was refused."
NOT_IN_BUILD = "That is not in this build yet."
OUTSIDE_NOT_ON = "This app has no outside service switched on for that, so it was not sent."
APP_USES = "{name} uses {domain}"
# The owner's question before an app may reach one outside site (pinned words).
WANTS_TO_REACH = "{app} wants to reach {domain}."
WOULD_USE_FOR = "Toolsmith says it would use it for: {words}."
FOLDER_AND_OUTSIDE = ("This app can also read {folder}. With an outside service on, it could "
                      "send those files there.")
SWITCH_ON = "Switch on"
NO = "No"
NOT_THIS_SITE = "This app cannot be given that site."
SWITCH_ON_APP_PAGE = "Switch it on or off on the app's own page, under Outside services."
# A folder the owner gives an app.
FOLDER_QUESTION = "Let {app} read the files in your folder {folder}?"
FOLDER_QUESTION_RW = "Let {app} read and change the files in your folder {folder}?"
FOLDER_NEVER_VAULT = "It never sees your vault, your keys or other apps' data."
GIVE_FOLDER = "Give it"
FOLDER_GIVEN = "{app} can now read {folder}."
FOLDER_TAKEN_BACK = "{app} can no longer read your folder."
FOLDER_NOT_IN_BUILD = "Giving this app a folder is not in this build yet."
# Undo from the dashboard.
UNDONE = "Put back the way it was before that change."
UNDO_NEWER = "The app changed after that, so I did not undo it. Open it to fix it by hand."
UNDO_BUSY = "Your bot is using this app right now. Try again in a moment."
UNDO_GONE = "That change can no longer be undone."
NOT_PAIRED = "This screen is not paired with your bot, so it cannot change that."
APP_WHAT_LEAVES = "{words}. What the app sends there, and your internet address."

NOT_DRIVABLE = ("Your bot can open it for you, but cannot use it by itself yet "
                "(it works in a way BuildBot cannot drive).")
KEEPS_NOTHING = ("Your bot can open it for you. It keeps nothing of its own, so there is "
                 "nothing for your bot to do in it.")
NODE_TOO_OLD = ("Your bot can open this app, but cannot use it by itself until "
                "Node.js is updated to 22.4 or newer.")
NO_BROWSER = ("Your bot can open this app, but cannot use it by itself because "
              "Microsoft Edge or Google Chrome was not found on this computer.")
RUNNER_FAILED = ("Your bot can open this app, but its hidden browser did not start, "
                 "so it cannot use the app by itself right now.")
NO_SEAL_KEY = ("Your bot can open this app, but cannot use it by itself because "
               "this computer's password store is not available.")
PARTS_NEEDED = ("This app loads parts from the internet, and saving a copy of them "
                "is not in this build yet, so it was not added.")
PARTS_NOT_SAVED = ("This app loads parts from the internet, and Toolsmith could not save a copy "
                   "of all of them, so it was not added.")
BLOCKED = "Drawbridge stopped this app, so nothing of it runs."
HELD = "Drawbridge wants you to look at this app before it runs."
BEING_CHECKED = "being checked"
# While Toolsmith learns what the bot can do in an app (install).
LEARN_FORMS = "Looking at the app's forms: {i} of {n}."
LEARN_BUTTONS = "Trying the app's buttons: {i} of {n}."
LEARN_FOUND = "Toolsmith found {n} things to try in the app. Drawbridge checks each one."
LEARN_TRIED = "Tried {i} of {n} things so far."
#: Once more things turned up, the count has no end to show.
LEARN_FOUND_MORE = "Found {n} more {things} to try."
LEARN_TRIED_SO_FAR = "Tried {i} things so far."
LEARN_STOPPED = ("Stopped learning after {minutes} minutes: tried {i} of {n} things. "
                 "Your bot can use the ones that passed.")
# The app came with entries of its own (its examples), never deleted by the bot.
EXAMPLES_READ = "{n} of these came with the app as examples."
EXAMPLES_HEADER = "This app came with {n} example entries of its own. They are not yours."
# One tap on the app's page takes them out (webapps.examples.clear).
EXAMPLES_CLEAR = "Clear the examples"
EXAMPLES_CLEARED = "Took out {n} example entries. Your own entries are kept. You can undo this."
EXAMPLES_CLEARED_ONE = "Took out 1 example entry. Your own entries are kept. You can undo this."
EXAMPLES_NONE_LEFT = "There are no example entries left in this app."
# Details outside the entries (a setup block: a couple's names, a date, a
# place, an amount) that are still the app's own are emptied too.
EXAMPLES_DETAILS_HEADER = ("This app came with example details of its own, like names, a date "
                           "or an amount. They are not yours.")
EXAMPLES_BOTH_HEADER = ("This app came with {n} example entries of its own, and example "
                        "details like names, a date or an amount. They are not yours.")
EXAMPLES_CLEARED_DETAILS = ("Took out the app's example details, like names, a date or an "
                            "amount. Your own entries are kept. You can undo this.")
EXAMPLES_CLEARED_BOTH = ("Took out {n} example entries and the app's example details, like "
                         "names, a date or an amount. Your own entries are kept. You can undo this.")
EXAMPLES_NOT_OPENED = "The app is still opening. Try again in a moment."
EXAMPLES_NOT_CLEARED = "Your bot is using this app right now, so nothing was taken out. Try again in a moment."
OPENS_INSIDE = "Opens inside your bot."

REMOVE_NEEDS_YES = "Your bot needs your yes before it removes anything."
ACTION_GONE = "That action is not available any more."
APP_DID_NOT_TAKE = "The app did not take the change, so nothing was saved."
BAD_ARGS = "Some of the details did not fit what the app accepts."
RUNNER_BUSY = "Your bot could not reach the app just now."
PUT_BACK = ("The app did more than that action should, so your bot put the "
            "app's data back the way it was.")
PUT_BACK_FAILED = ("Your bot could not put all of the app's data back the way it was. "
                   "Please open the app and check it.")
NOT_FINISHED_PUT_BACK = ("The app did not finish that, so your bot put its data back "
                         "the way it was.")
NOT_AS_ASKED_PUT_BACK = ("The app did not make that change the way it was asked, so your bot "
                         "put its data back the way it was.")
MORE_THAN_ONE = ("More than one entry in the app matches that, so your bot changed nothing. "
                 "Make this change in the app itself.")
NO_MATCH = "Nothing in the app matches that, so your bot changed nothing."
NO_SUCH_CHOICE = ("None of the app's choices matches your words, so your bot changed "
                  "nothing. Say the choice the way the app shows it.")
NO_PLAIN_TICK = ("Your words did not say plainly whether to tick that box or clear it, so "
                 "your bot changed nothing.")
BROKE_PUT_BACK = ("Your bot lost touch with the app partway through, so it put the "
                  "app's data back the way it was.")


def added_line(name: str, count: int) -> str:
    if count <= 0:
        return f"Added {name}. You can open it, but there are 0 things your bot can do in it by itself."
    if count == 1:
        return f"Added {name}. Your bot can do 1 thing in it."
    return f"Added {name}. Your bot can do {count} things in it."


def uses_line(name: str, domain: str) -> str:
    return APP_USES.format(name=name, domain=domain)


def what_leaves_line(words: str) -> str:
    words = str(words or "").strip() or "Something the app does online"
    return APP_WHAT_LEAVES.format(words=words[:1].upper() + words[1:])


def learn_stopped_line(minutes: int, tried: int, total: int) -> str:
    return LEARN_STOPPED.format(minutes=minutes, i=tried, n=total)


def examples_read_line(count: int) -> str:
    if count <= 0:
        return ""
    if count == 1:
        return "1 of these came with the app as an example."
    return EXAMPLES_READ.format(n=count)


def examples_header_line(count: int, details: int = 0) -> str:
    if count <= 0:
        return EXAMPLES_DETAILS_HEADER if details > 0 else ""
    if details > 0:
        return EXAMPLES_BOTH_HEADER.format(n=count) if count > 1 else (
            "This app came with 1 example entry of its own, and example details like names, "
            "a date or an amount. They are not yours.")
    if count == 1:
        return "This app came with 1 example entry of its own. It is not yours."
    return EXAMPLES_HEADER.format(n=count)


def examples_cleared_line(removed: int, details: int) -> str:
    """What one tap on "Clear the examples" took out."""
    if removed <= 0:
        return EXAMPLES_CLEARED_DETAILS if details > 0 else EXAMPLES_NONE_LEFT
    if details > 0:
        return EXAMPLES_CLEARED_BOTH.format(n=removed) if removed > 1 else (
            "Took out 1 example entry and the app's example details, like names, a date or "
            "an amount. Your own entries are kept. You can undo this.")
    return EXAMPLES_CLEARED_ONE if removed == 1 else EXAMPLES_CLEARED.format(n=removed)


def did_line(app_name: str, says: str) -> str:
    return f"Done in {app_name}: {says}."


def read_line(app_name: str, count: int) -> str:
    return f"{app_name} has {count} in that list."


# Templates for the words an action says (``says``). ``<noun>`` and ``<field>``
# come from the app's own words, cleaned, and are checked by Drawbridge (R8).
SAYS_ADD = "Add {a} {noun}"
SAYS_REMOVE = "Remove {a} {noun}"
SAYS_CHANGE = "Change the {field} of {a} {noun}"
SAYS_EDIT = "Edit {a} {noun}"
SAYS_READ = "Read the {noun} list"


def article(noun: str) -> str:
    """"a" or "an" for the noun (the templates' ``{a}``)."""
    return "an" if str(noun or "").strip()[:1].lower() in "aeiou" and noun else "a"


__all__ = [n for n in dir() if n.isupper() or n.endswith("_line")]
