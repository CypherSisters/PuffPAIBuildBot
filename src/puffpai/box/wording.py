"""Every sentence the owner reads about the locked box, Remove BuildBot and
owner folders. One place, so the plain-words tests can read them all.

Rules (the owner's): plain everyday English, short sentences, no em dashes, the
box is "the locked box", Windows' Linux feature is "Windows' Linux feature
(WSL)". ``{name}`` style fields are filled by the caller.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

# ── box status (puffpai.box.driver.status) ──────────────────────────────────

READY = "The locked box is ready."
NO_WSL = ("Windows' Linux feature (WSL) is not turned on, and the locked box "
          "needs it. BuildBot can turn it on for you: Windows asks for your "
          "permission, then you restart the computer once.")
WSL_OFF = ("Windows' Linux feature (WSL) is on this computer but did not "
           "answer. Restart the computer and try again.")
WSL_NO_VIRTUALIZATION = (
    "Windows' Linux feature (WSL) is on this computer, but the computer's "
    "virtualization setting is off, so the locked box cannot start. It is a "
    "switch in the computer's startup (BIOS) settings.")
NOT_NAT = ("The locked box stays off because Windows' Linux feature (WSL) is "
           "set to share this computer's own network. Its settings file, "
           ".wslconfig in your user folder, would need the usual network "
           "setting back.")
NO_BOX = ("The locked box for outside code is not set up yet. Setting it up "
          "downloads about 4 MB, then about 130 MB of tools.")
SHARED = ("Another BuildBot on this computer already set up the locked box. "
          "Say yes to let this one share it.")
BOX_V1 = ("The locked box was set up the old way and cannot lock code in. "
          "It needs to be set up again.")
BOX_BROKEN = ("The locked box is there but is missing parts or did not pass "
              "its safety check. It needs to be set up again.")
ORPHAN = ("There is a locked box left over from a BuildBot that was deleted "
          "({size}). Remove it?")
TAKEN = ("Something else on this computer already uses the name {distro}, so "
         "BuildBot leaves it alone.")
UNSUPPORTED = ("This part needs to build or run outside code, which BuildBot "
               "only does inside its locked box on Windows or Linux.")
LINUX_NO_BWRAP = ('BuildBot needs the "bubblewrap" program to run outside '
                  'code. Install it with your system\'s package manager, then '
                  'try again.')
SHARED_STAYS = ("The locked box is also used by another BuildBot on this "
                "computer, so it stays.")
DENY_LIST_NOT_SET = ("The locked box could not be told which of BuildBot's "
                     "own folders stay private, so no folder was shared with "
                     "it. Nothing ran. Try again.")
#: The box's own programs are not the ones this BuildBot ships, and putting
#: the right ones in did not work: nothing runs until it is set up again.
LAUNCHER_OLD = ("The locked box has an older copy of BuildBot's own programs, "
                "and BuildBot could not update them, so nothing runs in it. It "
                "needs to be set up again.")

# ── a web project built in the box (puffpai.box.driver.build_failure) ───────

#: The step that stopped, and why, both from the fixed lists below. The
#: project's own output is read for known causes and never shown.
BUILD_STOPPED = ("It could not be built. It stopped while {step}: {cause}. It "
                 "was left out; nothing else changed.")
BUILD_NO_PAGE = ("It built, but it made no page to open, so it cannot be an app "
                 "on its own (it may be a part made for another program). It was "
                 "left out; nothing else changed.")
BUILD_STEP_WORDS = {
    "put": "copying it into the locked box",
    "install": "getting its parts",
    "build": "building it",
    "export": "copying the finished app out of the locked box",
}
BUILD_CAUSE_WORDS = {
    "too_long": "it took longer than the locked box allows",
    "memory": "it needed more memory than the locked box allows",
    "no_room": "it needed more room than the locked box gives it",
    "box": "the locked box would not run it",
    "old_node": ("it was made for an older version of Node (the program that "
                 "builds it) than the one in the locked box"),
    "own_install_steps": ("one of its parts has to run its own install steps, "
                          "which the locked box never does"),
    "lock_mismatch": ("its list of parts does not match the project, so it "
                      "cannot be built the same way twice"),
    "part_gone": "a part on its list is no longer on the package site",
    "part_changed": "a part on the package site no longer matches its fingerprint",
    "no_build_step": "it has no build step",
    "install_failed": "getting its parts stopped with an error",
    "build_failed": "its own build step stopped with an error",
    "put_failed": "BuildBot could not copy it in",
    "export_failed": "BuildBot could not copy the finished files out",
}

# ── Remove BuildBot (puffpai.remove_cli and remove-buildbot.ps1) ─────────────

REMOVE_TITLE = "Remove BuildBot"
REMOVE_INTRO = "This stops your bot and its web page, then deletes:"
REMOVE_FOLDER = "  the BuildBot folder: {path} ({size})"
REMOVE_STATE = "  your bot's settings, books and chats: {path} ({size})"
REMOVE_APPS = "  the apps BuildBot added: {path} ({size})"
REMOVE_APPS_OWN = "  the {n} apps this BuildBot added, in {path} ({size})"
REMOVE_APP_OWN = "  the 1 app this BuildBot added, in {path} ({size})"
REMOVE_BOX_PROJECTS = "  the {n} apps and tools this BuildBot put in the locked box"
REMOVE_BOX_PROJECT = "  the 1 app or tool this BuildBot put in the locked box"
REMOVE_OTHER_APPS_STAY = ("Other apps in {path} stay, because another BuildBot "
                          "on this computer may use them.")
REMOVE_BOX = "  the locked box BuildBot set up: {distro} ({size})"
REMOVE_KEYS_WINDOWS = "  {n} saved entries BuildBot kept in Windows Credential Manager"
REMOVE_KEY_WINDOWS = "  1 saved entry BuildBot kept in Windows Credential Manager"
REMOVE_KEYS_OTHER = "  {n} saved entries BuildBot kept in this computer's password store"
REMOVE_KEY_OTHER = "  1 saved entry BuildBot kept in this computer's password store"
REMOVE_VAULT = ("Your vault (your private files and keys) is deleted too, and "
                "that cannot be undone.")
REMOVE_VAULT_ASK = ("  Type K to keep a copy of the vault in Documents first, "
                    "or press Enter to go on.")
REMOVE_OWN_FILES = ("Your own files in Pictures, Documents and other folders "
                    "are not touched.")
REMOVE_CONFIRM = ("Remove all of this? Type Y and press Enter. Anything else "
                  "keeps it.")
REMOVE_APPS_STAY = ("The apps BuildBot added stay in {path}, because another "
                    "BuildBot on this computer may use them.")
REMOVE_STATE_STAYS = ("Your bot's settings stay in {path}, because they "
                      "belong to another BuildBot folder.")
REMOVE_DATA_STAYS = ("BuildBot's other files stay in {path}, because that "
                     "folder is outside BuildBot's own folders.")
REMOVE_STOPPING = "Stopping your bot ..."
REMOVE_DELETING = "Deleting ..."
REMOVE_DONE = " done"
REMOVE_FINISHED = "BuildBot is removed."
REMOVE_KEPT = "Nothing was removed."
REMOVE_NOTHING = "There is nothing of BuildBot left to remove."
REMOVE_VAULT_COPIED = "Kept a copy of the vault in {path}."
REMOVE_VAULT_COPY_FAILED = ("The copy of the vault did not work, so nothing "
                            "was removed.")
REMOVE_LOCKED_WINDOWS = ("Windows is still using a file in {path}. Restart the "
                         "computer, then run Remove BuildBot again.")
#: Checked before anything is deleted: something still holds a file.
REMOVE_IN_USE_WINDOWS = ("Windows is still using a file in {path}, so nothing "
                         "was deleted. Close the program that uses it, or "
                         "restart the computer, then run Remove BuildBot again.")
REMOVE_IN_USE_BY = "Still running from it: {names}."
REMOVE_NAME_NOT_BACK = ("BuildBot could not put back the name of {path} "
                        "after checking it. It is now called {now}. Nothing "
                        "was deleted. Rename it back, then run Remove BuildBot "
                        "again.")
#: Deleting stopped part way: say why, what is still here, and how to finish.
REMOVE_PART_WAY_WINDOWS = ("Deleting stopped part way, because Windows is "
                           "still using a file in {path}.")
REMOVE_PART_WAY_OTHER = ("Deleting stopped part way, because this computer is "
                         "still using a file in {path}.")
REMOVE_PART_WAY_CHANGED = ("Deleting stopped part way, because something in "
                           "{path} changed while it was being deleted, so "
                           "BuildBot left it alone.")
REMOVE_STILL_HERE = "Still here:"
REMOVE_LEFT_FOLDER = "  the rest of the BuildBot folder: {path}"
REMOVE_LEFT_STATE = "  your bot's settings, books and chats: {path}"
REMOVE_LEFT_VAULT = "  your vault: {path}"
REMOVE_LEFT_APPS = "  the apps BuildBot added: {path}"
REMOVE_RUN_AGAIN_WINDOWS = ("Close the program that uses it, or restart the "
                            "computer, then double-click Remove BuildBot.cmd "
                            "in {path} again to finish.")
REMOVE_RUN_AGAIN_OTHER = ("Close the program that uses it, or restart the "
                          "computer, then run \"bash install.sh remove\" in "
                          "{path} again to finish.")
REMOVE_REFUSED = ("BuildBot will not delete {path}, because it is {why}. "
                  "Nothing was deleted.")
REMOVE_WHY_DRIVE = "a whole drive"
REMOVE_WHY_MAIN_FOLDER = "one of your own main folders, or holds one"
REMOVE_WHY_LINK = "a link to another place"
REMOVE_WHY_NOT_BUILDBOT = "not a BuildBot folder"
REMOVE_NOT_CHECKED = ("The locked box and the saved entries could not be "
                      "checked, so they were left as they are.")
REMOVE_NEW_WINDOW = ("The removal goes on in a new window, because this window "
                     "uses a file inside the BuildBot folder.")
REMOVE_USE_CMD = ("To remove BuildBot, double-click Remove BuildBot.cmd in "
                  "{path}.")
REMOVE_WRONG_FOLDER = ("This removal file belongs to a different BuildBot "
                       "folder, so nothing was removed.")
REMOVE_CLOSE = "Press Enter to close this window."

# ── owner folders (puffpai.security.owner_folders) ──────────────────────────

FOLDER_PRIVATE = ("That folder holds private PuffPAI or system files, so no "
                  "app can have it.")
FOLDER_NOT_THERE = "That folder is not there."
FOLDER_NOT_A_FOLDER = "That is a file, not a folder."
FOLDER_NETWORK = "Folders on other computers or special places cannot be shared."
FOLDER_OUTSIDE = ("Only folders inside Pictures, Documents, Music, Videos, "
                  "Desktop or Downloads can be shared.")
FOLDER_WRITE_ONLY_OWN = ("An app can only save files in its own folder that "
                         "BuildBot makes in Documents.")
FOLDER_BAD_MODE = "BuildBot did not understand how that folder should be shared."
FOLDER_TOO_BIG = ("That folder holds too many files for BuildBot to check them all. "
                  "Pick a smaller folder inside it.")

# ── installers ───────────────────────────────────────────────────────────────

INSTALL_BOX_LATER = ("BuildBot can set up a locked box for outside code later, "
                     "when you pick something that needs it.")

# ── setting the box up (puffpai.box.driver.setup) ───────────────────────────

SETUP_DOWNLOADING = "Downloading the base of the locked box (about 4 MB) ..."
SETUP_UNPACKING = "Putting the locked box in place ..."
SETUP_TOOLS = "Adding the tools it needs (about 130 MB) ..."
SETUP_CHECKING = "Checking that the locked box is locked ..."
SETUP_DONE = "The locked box is set up."
SETUP_ALREADY = "The locked box was already set up."
SETUP_SHARED = ("This BuildBot now shares the locked box that is already on "
                "this computer.")
SETUP_FAILED = ("Could not set up the locked box: {message} Things that need it "
                "were left out.")
SETUP_DOWNLOAD_FAILED = ("The download for the locked box did not work. Check "
                         "the internet and try again.")
SETUP_NOT_EXPECTED = ("The download for the locked box was not the one BuildBot "
                      "expects, so it was not used.")
SETUP_BROKE = ("Setting up the locked box stopped partway, so BuildBot took it "
               "back out. Nothing else changed.")
SETUP_NOT_IN_BUILD = "Setting up the locked box is not in this build yet."
SETUP_OFFLINE = ("This needs the internet to download, and the build is "
                 "offline. It was left out; you can add it later from the Add "
                 "page.")
SETUP_FOLDER_IN_USE = ("The folder for the locked box already has files in it, "
                       "so BuildBot did not set it up there.")

# ── Windows' Linux feature missing ──────────────────────────────────────────

WSL_TURNING_ON = ("Windows is turning on its Linux feature. Restart the computer "
                  "when it says it is done, then open BuildBot.")
WSL_DECLINED = ("Windows did not turn it on, so the {n} things that need it were "
                "left out. You can try again from the home page.")
WSL_DECLINED_ONE = ("Windows did not turn it on, so the 1 thing that needs it was "
                    "left out. You can try again from the home page.")
WSL_NEEDED = ("Windows needs its Linux feature turned on for {n} things you "
              "picked. Windows will ask for permission when you turn it on. "
              "After that, restart the computer and open BuildBot. It will "
              "finish them then.")
WSL_NEEDED_ONE = ("Windows needs its Linux feature turned on for 1 thing you "
                  "picked. Windows will ask for permission when you turn it on. "
                  "After that, restart the computer and open BuildBot. It will "
                  "finish it then.")
FINISH_CARD = ("Finish adding {n} things that were waiting for the locked box")
FINISH_CARD_ONE = "Finish adding 1 thing that was waiting for the locked box"
FINISH_NOTHING = "Nothing is waiting for the locked box."
FINISH_STILL_NO_WSL = ("Windows' Linux feature (WSL) is still not on, so the "
                       "things that need the locked box are still waiting.")

# ── the plan line (puffpai.box.plan) ─────────────────────────────────────────

PLAN_BOX_LABEL = "Set up the locked box for outside code (one time)"
PLAN_BOX_DETAIL = ("A separate, locked Linux space on this computer where "
                   "outside code is built and run. It cannot see your files, "
                   "your vault or the rest of this computer. About 140 MB.")
#: With Windows' Linux feature missing, the detail is this plus PLAN_BOX_WSL
#: (together under the plan's 300-character limit, never cut).
PLAN_BOX_DETAIL_SHORT = ("A separate, locked Linux space on this computer for "
                         "outside code. About 140 MB.")
PLAN_BOX_WSL = ("Windows needs its Linux feature turned on first. BuildBot will "
                "ask Windows to turn it on after you say Looks good; Windows "
                "asks for permission, then you restart the computer and open "
                "BuildBot again.")

# ── command-line tools (puffpai.box.cli_tools) ───────────────────────────────

CLI_ADDING = "Adding {name} ..."
CLI_INSTALLING = ("Putting {name} into the locked box (the exact version the "
                  "plan showed) ...")
CLI_LEARNING = "Reading what {name} can do ..."
CLI_READY = ("Added {name}. It runs in the locked box. Choose a folder for it "
             "on its page, and your bot can use it on files there.")
CLI_ABILITIES = "Your bot can now use it to: {abilities}."
CLI_NO_ACTIONS = ("Your bot can open it for you, but cannot use it by itself yet "
                  "(it works in a way BuildBot cannot drive).")
CLI_INSTALL_FAILED = ("{name} did not install in the locked box. It was left "
                      "out; nothing else changed.")
CLI_NEEDS_BOX = ("{name} needs the locked box, which is not set up. It was left "
                 "out; set up the locked box, then add it again.")
CLI_WAITING = "{name} is waiting for the locked box."
CLI_STOPPED = "Drawbridge stopped this one: {reason}. It was not added."
CLI_LEFT_OUT = "{name} was left out: {reason}. Nothing else changed."
CLI_GONE = ("BuildBot could not find that one any more. Nothing was changed.")
CLI_NO_FOLDER = "Choose a folder for {name} first, on its page."
CLI_BAD_FILE = "That file is not in the folder you gave {name}."
CLI_DONE = "{name} finished."
CLI_FAILED = "{name} did not finish. Nothing in your folder was changed by BuildBot."
CLI_TOO_LONG = "{name} took too long, so the locked box stopped it."
CLI_SAYS_FILE = "Use {command} on a file from its folder"
FOLDER_GIVEN_RO = "{name} can now read the files in {folder}."
FOLDER_GIVEN_RW = "{name} can now save files in {folder}."
FOLDER_NOT_A_TOOL = "That is not a tool that can be given a folder."

#: Plain words for the Drawbridge rows of a command-line tool (reason codes).
CLI_REASONS = {
    "not_exact": "it is not exactly the version the plan showed",
    "scripts_allowed": "it would run its own install steps",
    "no_hashes": "its parts could not all be fingerprinted",
    "lock_offsite": "some of its parts come from somewhere other than the package site",
    "lock_changed": "its list of parts changed since it was first added",
    "not_open": "its licence does not let everyone use it",
    "starjacking": "it points at a code project with another name",
    "hosts_at_run": "it wants to reach the internet while it runs",
    "shell_chars": "the way it would be started is not safe",
    "rootfs_changed": "the download was not the one BuildBot expects",
}

#: Plain words for a command-line tool whose download was not used (the
#: download or unpack helpers' reason codes). Shown with CLI_LEFT_OUT.
CLI_DOWNLOAD_REASONS = {
    "bomb": ("its download grows more than 100 times bigger when it is opened, "
             "a trick harmful downloads use, so BuildBot did not open it"),
    "too_big": "its download is bigger than BuildBot takes",
    "too_many_files": "its download has more files than BuildBot takes",
    "download_failed": "its download did not work",
    "extract_failed": "its download could not be opened safely",
}


def size_words(n_bytes: int) -> str:
    """A size the owner reads: "1.4 GB", "320 MB", "less than 1 MB"."""
    n = max(0, int(n_bytes or 0))
    gb = 1024 ** 3
    mb = 1024 ** 2
    if n >= gb:
        return f"{n / gb:.1f} GB"
    if n >= mb:
        return f"{n // mb} MB"
    return "less than 1 MB"


def keyring_line(n: int, *, windows: bool) -> str:
    if n == 1:
        return REMOVE_KEY_WINDOWS if windows else REMOVE_KEY_OTHER
    tmpl = REMOVE_KEYS_WINDOWS if windows else REMOVE_KEYS_OTHER
    return tmpl.format(n=n)


#: Every owner-facing template, for the plain-words tests.
ALL = tuple(v for k, v in sorted(globals().items())
            if k.isupper() and isinstance(v, str))
