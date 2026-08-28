#!/usr/bin/env python3
"""
FixMods - resubscribes broken Steam Workshop mods for Oxygen Not Included.

Dieses Skript liegt in einem Unterordner des mods-Ordners, z.B.:
    <Dokumente>/Klei/OxygenNotIncluded/mods/ONI_FixMods/fixmods.py

Wie dieser Unterordner heisst, ist egal (z.B. der Name, unter dem das
GitHub-Repo geklont wurde) - der mods-Ordner ist einfach das uebergeordnete
Verzeichnis. Alle Pfade werden von dort abgeleitet, deshalb ist es egal, ob
Python als Administrator oder als normaler Benutzer laeuft.

Liegt der mods-Ordner in einem OneDrive-Pfad, wird OneDrive bei --full und
--resume hart beendet und am Ende wieder gestartet. Bei --backup und --restore
bleibt OneDrive unberuehrt, ebenso wenn der Pfad nichts mit OneDrive zu tun hat.

Ordnerstruktur:
    mods/
      mods.json              <- gelesen
      Steam/                 <- die eigentlichen Mods
      <beliebiger Name>/     <- z.B. "ONI_FixMods", der Name des geklonten Repos
        fixmods.py           <- dieses Skript
        SteamModsBkp/        <- Backup
        FixMods.txt          <- Liste der kaputten Mods
        FixModsStatus.txt    <- Bearbeitungsstand je Mod
        FixModsBrowser/      <- Playwright-Profil (Steam-Login)

Workflow (--full):
  1. Back up Steam mods to SteamModsBkp
  2. Delete the local folder of every broken mod
  3. Open Steam login in browser, then unsubscribe every broken mod
  4. Launch ONI, wait for user, kill it
  5. Subscribe every mod again
  6. Launch ONI, wait for user, kill it
  7. Restore backed-up mod files

Ohne Modus-Flag zeigt das Skript nur die Hilfe an und macht sonst nichts.

Modi:
    fixmods.py --full            kompletter Ablauf (siehe oben)
    fixmods.py --resume          abgebrochenen Lauf fortsetzen
    fixmods.py --backup          nur die Config-Dateien sichern
    fixmods.py --restore         nur die gesicherten Config-Dateien zurueckspielen
    fixmods.py --restore --only-broken
                                 nur die in mods.json als kaputt markierten Mods

Waehrend eines --full-Laufs wird nach jedem Schritt FixModsStatus.txt
geschrieben. Dort steht pro Mod-ID, wie weit sie gekommen ist. Nach einem
Abbruch sieht man daran, welche Mods noch von Hand abonniert werden muessen.

Ab-/Abonnieren laeuft standardmaessig ueber einen direkten POST (ein Request
pro Mod). Mit --subscribe-method page kommt die alte Variante zum Einsatz, die
jede Mod-Seite oeffnet und die Buttons der Seite aufruft.
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


# ---------------------------------------------------------------------------
# Paths - alles relativ zum Speicherort dieses Skripts
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent   # .../mods/fixmods
MOD_DIR = SCRIPT_DIR.parent                    # .../mods

APP_ID = "457140"

# Pause zwischen zwei Subscribe-Requests und Obergrenze fuer das Backoff,
# wenn Steam trotzdem "zu viele Anfragen" meldet.
DEFAULT_DELAY = 2.0
MAX_BACKOFF = 480.0

EXCLUDE_DIRS = [
    "anim", "assets", "archived_versions", "templates", "worldgen",
    "elements", "codex", "translations", "Source", "true_tiles_addon", "strings",
]
EXCLUDE_FILES = [
    "mod_info.yaml", "mod.yaml", "LauncherMetadata",
    "*.pdb", "*.dll", "*.png", "*.jpg", "*.pot", "LICENSE", "*.md",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_dir(path: Path) -> None:
    """mkdir mit verstaendlicher Fehlermeldung statt Traceback."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.exit(
            f"[FEHLER] Ordner konnte nicht angelegt werden: {path}\n"
            f"         {exc}\n"
            f"         Haeufige Ursachen: OneDrive haengt (Prozess neu starten oder\n"
            f"         Synchronisierung anhalten), oder der Ordnerschutz von Windows\n"
            f"         Defender blockiert Schreibzugriffe auf Dokumente."
        )


def in_onedrive(path: Path) -> bool:
    """True, wenn irgendein Ordner im Pfad OneDrive ist.

    Trifft auch auf Geschaeftskonten zu, die als "OneDrive - Firmenname"
    angelegt werden.
    """
    return any(part.lower().startswith("onedrive") for part in path.parts)


def stop_onedrive() -> None:
    subprocess.run(["taskkill", "/F", "/IM", "OneDrive.exe"], capture_output=True)


def start_onedrive() -> None:
    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "OneDrive" / "OneDrive.exe"
    if exe.is_file():
        subprocess.Popen([str(exe)])


def backup_mods(steam_mods: Path, backup_dir: Path,
                mod_ids: list[int] | None = None) -> None:
    """Mirrors steam_mods into backup_dir via Robocopy.

    mod_ids = None -> kompletter Steam-Ordner, sonst nur die genannten Mods.
    """
    if mod_ids is None:
        pairs = [(steam_mods, backup_dir)]
    else:
        pairs = [(steam_mods / str(i), backup_dir / str(i)) for i in mod_ids]

    for src, dst in pairs:
        if not src.is_dir():
            print(f"[WARN] Mod-Ordner nicht gefunden: {src}")
            continue
        cmd = (
            f'Robocopy.exe "{src}" "{dst}" '
            f"/s /sec /a-:RH /mt:4 /xx /xo "
            f"/XD {' '.join(EXCLUDE_DIRS)} "
            f"/XF {' '.join(EXCLUDE_FILES)}"
        )
        result = subprocess.run(cmd, shell=True)
        # Robocopy exit codes 0-7 are success / informational
        if result.returncode > 7:
            print(f"[WARN] Robocopy exited with code {result.returncode}")


def restore_mods(backup_dir: Path, steam_mods: Path,
                 mod_ids: list[int] | None = None) -> int:
    """Spielt gesicherte Einstellungen zurueck.

    mod_ids = None -> alles zurueckspielen, was im Backup liegt.
    Vorhandene Dateien werden ueberschrieben, alles andere bleibt unangetastet.
    """
    if mod_ids is None:
        sources = sorted(p for p in backup_dir.iterdir() if p.is_dir())
    else:
        sources = [backup_dir / str(i) for i in mod_ids]

    restored = 0
    for src in sources:
        if not src.is_dir():
            print(f"[WARN] Kein Backup vorhanden fuer {src.name}")
            continue
        print(f"  Restoring {src.name}...")
        shutil.copytree(src, steam_mods / src.name, dirs_exist_ok=True)
        restored += 1
    return restored


def force_rmtree(path: Path) -> None:
    """rmtree, das auch schreibgeschuetzte Dateien wegbekommt."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def parse_mods_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_broken_ids(data: dict) -> list[int]:
    ids: list[int] = []
    for mod in data.get("mods", []):
        if mod.get("status") == 1:
            continue  # mod is fine
        if re.search(r"\.Local$", mod.get("staticID", "")):
            continue  # local mod, skip

        raw_id = mod.get("label", {}).get("id")
        if not raw_id:
            continue
        try:
            ids.append(int(raw_id))
        except (ValueError, TypeError):
            print(f"[WARN] Could not parse mod id: {raw_id!r}")
    return ids


def ensure_logged_in(page) -> None:
    page.goto("https://steamcommunity.com/", timeout=60_000, wait_until="domcontentloaded")
    if page.locator("#account_pulldown").count():
        print("Bereits eingeloggt.")
        return
    page.goto("https://steamcommunity.com/login/home",
              timeout=60_000, wait_until="domcontentloaded")
    print("Bitte im Browserfenster bei Steam anmelden (bis zu 5 Minuten Zeit)...")
    page.wait_for_selector("#account_pulldown", timeout=300_000)
    print("Logged in.")


class StatusFile:
    """Haelt den Bearbeitungsstand jedes Mods in einer Textdatei fest.

    Nach jeder Zustandsaenderung wird die Datei sofort neu geschrieben (erst
    in eine .tmp, dann umbenannt). Wird das Skript irgendwo abgewuergt - auch
    mit Strg+C oder hartem Kill - liegt der letzte Stand vollstaendig auf der
    Platte und man sieht, welche Mods noch offen sind.
    """

    HEADER = (
        "# FixMods-Status\n"
        "# Stand: {stamp}\n"
        "# Schritt: {step}\n"
        "#\n"
        "# offen         = noch nichts passiert\n"
        "# geloescht     = lokaler Mod-Ordner entfernt\n"
        "# deabonniert   = Abo geloescht, muss noch WIEDER ABONNIERT werden\n"
        "# abonniert     = fertig\n"
        "# FEHLER-deabo  = Deabonnieren fehlgeschlagen\n"
        "# FEHLER-abo    = Abonnieren fehlgeschlagen\n"
        "#\n"
        "# Alles ausser 'abonniert' bedeutet: noch offen. Entweder\n"
        "#   fixmods.py --resume\n"
        "# oder von Hand unter\n"
        "#   https://steamcommunity.com/sharedfiles/filedetails/?id=<ID>\n"
        "\n"
    )

    # Zustaende, aus denen sich ergibt, was noch zu tun ist
    NEEDS_DELETE = ("offen",)
    NEEDS_UNSUB = ("offen", "geloescht", "FEHLER-deabo")
    DONE = "abonniert"

    def __init__(self, path: Path, mod_ids: list[int]) -> None:
        self.path = path
        self.step = "Start"
        self.state: dict[int, str] = {mod_id: "offen" for mod_id in mod_ids}
        self.write()

    @classmethod
    def load(cls, path: Path) -> "StatusFile":
        """Liest eine vorhandene Statusdatei ein, ohne sie zu ueberschreiben."""
        obj = cls.__new__(cls)
        obj.path = path
        obj.step = "Fortsetzen"
        obj.state = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            try:
                mod_id = int(parts[0])
            except (ValueError, IndexError):
                print(f"[WARN] Statuszeile nicht lesbar: {line!r}")
                continue
            obj.state[mod_id] = parts[1] if len(parts) > 1 else "offen"
        return obj

    def ids_where(self, states: tuple[str, ...]) -> list[int]:
        return [i for i, s in self.state.items() if s in states]

    def ids_unfinished(self) -> list[int]:
        return [i for i, s in self.state.items() if s != self.DONE]

    def set(self, mod_id: int, state: str) -> None:
        self.state[mod_id] = state
        self.write()

    def set_step(self, step: str) -> None:
        self.step = step
        self.write()

    def write(self) -> None:
        text = self.HEADER.format(
            stamp=time.strftime("%Y-%m-%d %H:%M:%S"), step=self.step
        )
        text += "".join(f"{mod_id}\t{state}\n"
                        for mod_id, state in self.state.items())
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[WARN] Statusdatei nicht schreibbar: {exc}")


def _action_via_fetch(page, workshop_id: int, action: str) -> tuple[bool, bool]:
    """Schneller Weg: direkter POST an den Endpunkt der Seite.

    Es wird KEINE filedetails-Seite geladen. Ein Seitenaufruf zieht dutzende
    Requests (Bilder, JS, Kommentare, aehnliche Items) nach sich - genau das
    laesst Steam nach rund 20 Mods dichtmachen. Der POST ist ein Request.
    """
    try:
        res = page.evaluate(
            """async ([id, appid, action]) => {
                const r = await fetch('https://steamcommunity.com/sharedfiles/' + action, {
                    method: 'POST',
                    credentials: 'include',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                    body: new URLSearchParams({
                        id: id, appid: appid, sessionid: g_sessionID
                    }).toString()
                });
                let body = null;
                try { body = await r.json(); } catch (e) { body = null; }
                return {status: r.status, body: body};
            }""",
            [str(workshop_id), APP_ID, action],
        )
    except Exception as exc:
        print(f"[WARN] {action} fuer {workshop_id} fehlgeschlagen: {exc}")
        return False, False

    status = res.get("status")
    body = res.get("body") or {}
    success = body.get("success")

    if status == 200 and success == 1:
        return True, False

    # 429 = zu viele Anfragen, EResult 84 = RateLimitExceeded
    if status == 429 or success == 84:
        return False, True

    print(f"[WARN] {action} fuer {workshop_id}: HTTP {status}, success={success}")
    return False, False


def _action_via_page(page, workshop_id: int, action: str) -> tuple[bool, bool]:
    """Alter Weg: Mod-Seite oeffnen und die Button-Funktion der Seite aufrufen.

    Langsamer und der Grund fuer die Rate-Limits, aber als Rueckfallebene
    nuetzlich, falls Steam den Endpunkt oder g_sessionID mal umbaut.
    """
    js_fn = "SubscribeItem" if action == "subscribe" else "UnsubscribeItem"
    url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"
    try:
        resp = page.goto(url, timeout=60_000, wait_until="domcontentloaded")
    except Exception as exc:
        print(f"[WARN] Seite fuer {workshop_id} nicht ladbar: {exc}")
        return False, False

    if resp is not None and resp.status == 429:
        return False, True

    try:
        page.evaluate(f"{js_fn}('{workshop_id}', '{APP_ID}')")
    except Exception as exc:
        print(f"[WARN] {js_fn} fuer {workshop_id} fehlgeschlagen: {exc}")
        return False, False
    # Die Seitenfunktion meldet nichts zurueck - kurz warten, damit der
    # AJAX-Request noch rausgeht, bevor die naechste Seite geladen wird.
    time.sleep(1)
    return True, False


def workshop_action(page, workshop_id: int, action: str,
                    method: str) -> tuple[bool, bool]:
    """Rueckgabe: (erfolgreich, rate_limit_getroffen)"""
    if method == "page":
        return _action_via_page(page, workshop_id, action)
    return _action_via_fetch(page, workshop_id, action)


def run_pass(page, mod_ids: list[int], delay: float, action: str = "subscribe",
             method: str = "fetch", status: "StatusFile | None" = None) -> list[int]:
    """Fuehrt action fuer alle IDs aus und faengt Rate-Limits ab.

    Rueckgabe: Liste der IDs, die NICHT durchgekommen sind.
    """
    done_state = "deabonniert" if action == "unsubscribe" else "abonniert"
    fail_state = "FEHLER-deabo" if action == "unsubscribe" else "FEHLER-abo"
    failed: list[int] = []
    for pos, mod_id in enumerate(mod_ids, start=1):
        backoff = 60.0
        while True:
            ok, rate_limited = workshop_action(page, mod_id, action, method)
            if not rate_limited:
                break
            if backoff > MAX_BACKOFF:
                print(f"[FEHLER] Steam limitiert weiterhin. Abbruch bei {mod_id}.")
                return failed + mod_ids[pos - 1:]
            print(f"  [Rate-Limit] Warte {int(backoff)}s und versuche es erneut...")
            time.sleep(backoff)
            backoff *= 2
            # Nach einem Treffer dauerhaft langsamer weitermachen
            new_delay = min(delay * 1.5, 30.0)
            if new_delay > delay:
                delay = new_delay
                print(f"  [Rate-Limit] Pause zwischen Mods jetzt {delay:.1f}s")

        if status is not None:
            status.set(mod_id, done_state if ok else fail_state)

        print(f"  {'OK  ' if ok else 'FEHL'} {mod_id}  ({pos}/{len(mod_ids)})")
        if not ok:
            failed.append(mod_id)
        if pos < len(mod_ids):
            time.sleep(delay)
    return failed


def launch_oni() -> None:
    """Ueber explorer.exe, damit Steam als normaler Benutzer startet und nicht
    als Administrator - auch wenn dieses Skript elevated laeuft."""
    subprocess.run(["explorer.exe", f"steam://rungameid/{APP_ID}"], capture_output=True)


def kill_oni() -> None:
    subprocess.run(["taskkill", "/F", "/IM", "OxygenNotIncluded.exe"], capture_output=True)


def wait_for_user(message: str) -> None:
    input(message)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def repair_round(args, status: StatusFile, steam_mods: Path,
                 browser_profile: Path, to_delete: list[int],
                 to_unsub: list[int], to_sub: list[int]) -> None:
    """Loeschen, deabonnieren, ONI, abonnieren, ONI.

    Wird von --full und von --resume benutzt; nur die Listen unterscheiden sich.
    """
    if to_delete:
        print(f"\n>>> Loesche {len(to_delete)} Mod-Ordner...")
        status.set_step("Mod-Ordner loeschen")
        for mod_id in to_delete:
            local_folder = steam_mods / str(mod_id)
            if local_folder.exists():
                print(f"  Deleting {local_folder}...")
                try:
                    force_rmtree(local_folder)
                except OSError as exc:
                    print(f"[WARN] Konnte {local_folder} nicht loeschen: {exc}")
            status.set(mod_id, "geloescht")

    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    with sync_playwright() as pw:
        # Persistentes Profil im Skript-Ordner: der Steam-Login bleibt erhalten,
        # egal ob elevated oder normal gestartet wird.
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile), headless=False
        )
        page = context.pages[0] if context.pages else context.new_page()

        print("\n>>> Anmeldung bei Steam...")
        status.set_step("Anmeldung")
        ensure_logged_in(page)

        if to_unsub:
            print(f"\n>>> Deabonniere {len(to_unsub)} Mod(s)...")
            status.set_step("Deabonnieren")
            failed = run_pass(page, to_unsub, args.subscribe_delay,
                              action="unsubscribe", method=args.subscribe_method,
                              status=status)
            if failed:
                print(f"[WARN] Nicht deabonniert: {failed}")

            print("\n>>> Bitte Oxygen Not Included starten.")
            status.set_step("1. ONI-Start (wartet auf Benutzer)")
            launch_oni()
            wait_for_user("Press ENTER after Oxygen Not Included has started...")
            kill_oni()
            print("ONI closed.")

        print(f"\n>>> Abonniere {len(to_sub)} Mod(s)...")
        status.set_step("Abonnieren")
        failed = run_pass(page, to_sub, args.subscribe_delay,
                          method=args.subscribe_method, status=status)
        if failed:
            print(f"[WARN] Nicht abonniert: {failed}")

        context.close()

    print("\n>>> Bitte Oxygen Not Included erneut starten.")
    status.set_step("2. ONI-Start (wartet auf Benutzer)")
    launch_oni()
    wait_for_user("Press ENTER after Oxygen Not Included has started (2nd time)...")
    kill_oni()
    print("ONI closed.")


def finish(status: StatusFile, backup_dir: Path, steam_mods: Path,
           mod_ids: list[int]) -> None:
    """Backup zurueckspielen und Schlussmeldung ausgeben."""
    print("\n>>> Stelle gesicherte Einstellungen wieder her...")
    status.set_step("Wiederherstellen")
    restore_mods(backup_dir, steam_mods, mod_ids)

    offen = status.ids_unfinished()
    status.set_step("Fertig" if not offen else "Fertig, aber mit offenen Mods")
    if offen:
        print(f"\n[WARN] {len(offen)} Mod(s) sind NICHT abonniert: {offen}")
        print(f"       Details in {status.path}")
        print("       Weitermachen mit: fixmods.py --resume")
    print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(description="FixMods for Oxygen Not Included")
    parser.add_argument(
        "--mod-dir", type=Path, default=MOD_DIR,
        help="Mods-Ordner (Standard: uebergeordneter Ordner dieses Skripts)",
    )
    parser.add_argument(
        "--subscribe-delay", type=float, default=DEFAULT_DELAY, metavar="SEK",
        help=f"Pause zwischen zwei Subscribe-Requests (Standard: {DEFAULT_DELAY}). "
             f"Bei 'zu viele Anfragen' hochsetzen, z.B. 5",
    )
    parser.add_argument(
        "--subscribe-method", choices=["fetch", "page"], default="fetch",
        help="fetch (Standard): ein POST pro Mod, schnell und schont das "
             "Anfragelimit. page: alte Methode, oeffnet die Mod-Seite und "
             "klickt die Seitenfunktion - langsam, aber unabhaengig vom "
             "AJAX-Endpunkt",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full", action="store_true",
        help="Kompletter Ablauf: sichern, loeschen, deabonnieren, ONI, "
             "abonnieren, ONI, wiederherstellen",
    )
    mode.add_argument(
        "--resume", action="store_true",
        help="Abgebrochenen --full-Lauf anhand von FixModsStatus.txt "
             "fortsetzen: holt nur nach, was noch offen ist",
    )
    mode.add_argument(
        "--backup-only", "--backup", action="store_true",
        help="Nur die Mod-Einstellungen sichern, sonst nichts",
    )
    mode.add_argument(
        "--restore-only", "--restore", action="store_true",
        help="Nur die gesicherten Einstellungen zurueckspielen, sonst nichts",
    )
    parser.add_argument(
        "--only-broken", action="store_true",
        help="Bei --backup-only/--restore-only nur die in mods.json als kaputt "
             "markierten Mods beruecksichtigen (Standard: alle)",
    )
    args = parser.parse_args()

    # Ohne Modus-Flag nur die Hilfe zeigen. So loest ein Doppelklick auf das
    # Skript nicht versehentlich den kompletten Ablauf aus.
    if not (args.full or args.resume or args.backup_only or args.restore_only):
        parser.print_help()
        print("\nBeispiele:")
        print("  fixmods.py --full                        kompletter Ablauf")
        print("  fixmods.py --resume                      abgebrochenen Lauf fortsetzen")
        print("  fixmods.py --backup                      nur sichern")
        print("  fixmods.py --restore                     nur wiederherstellen")
        print("  fixmods.py --restore --only-broken       nur die kaputten Mods")
        return

    mod_dir: Path = args.mod_dir.resolve()
    steam_mods = mod_dir / "Steam"
    mods_json = mod_dir / "mods.json"

    # Alles, was das Skript selbst anlegt, bleibt im Skript-Ordner
    backup_dir = SCRIPT_DIR / "SteamModsBkp"
    backup_list = SCRIPT_DIR / "FixMods.txt"
    status_file = SCRIPT_DIR / "FixModsStatus.txt"
    browser_profile = SCRIPT_DIR / "FixModsBrowser"

    print(f"Mods-Ordner   : {mod_dir}")
    print(f"Skript-Ordner : {SCRIPT_DIR}")

    # mods.json wird nur gebraucht, wenn die Liste der kaputten Mods noetig ist
    needs_mods_json = args.full or args.only_broken
    if needs_mods_json and not mods_json.is_file():
        sys.exit(f"[FEHLER] mods.json nicht gefunden: {mods_json}\n"
                 f"Erwartet wird fixmods.py in einem Unterordner des mods-Ordners, "
                 f"also .../OxygenNotIncluded/mods/{SCRIPT_DIR.name}/fixmods.py")

    # Ordner anlegen, SOLANGE OneDrive noch laeuft. Ein hart beendeter
    # OneDrive-Prozess laesst mkdir im synchronisierten Dokumente-Ordner
    # sonst mit "WinError 2" scheitern.
    ensure_dir(steam_mods)
    ensure_dir(backup_dir)
    if args.full or args.resume:
        ensure_dir(browser_profile)

    # OneDrive nur anfassen, wenn Mods ab-/deabonniert werden (--full/--resume)
    # UND die Mods tatsaechlich in einem OneDrive-Ordner liegen. Reine
    # Backup- oder Restore-Laeufe lassen OneDrive in Ruhe.
    changes_subs = args.full or args.resume
    onedrive = changes_subs and in_onedrive(mod_dir)
    if onedrive:
        print("OneDrive-Pfad erkannt - beende OneDrive hart...")
        stop_onedrive()
        time.sleep(2)  # let OneDrive release directory handles before rmtree
    elif changes_subs:
        print("Kein OneDrive-Pfad - OneDrive wird nicht angefasst.")

    def run_mode() -> None:
        # --- Modus: nur sichern -------------------------------------------------
        if args.backup_only:
            ids = None
            if args.only_broken:
                ids = collect_broken_ids(parse_mods_json(mods_json))
                if not ids:
                    print("No broken mods found. Exiting.")
                    return
                print(f"Backing up {len(ids)} broken mod(s): {ids}")
            else:
                print("Backing up mods...")
            backup_mods(steam_mods, backup_dir, ids)
            print(f"\nBackup liegt in: {backup_dir}")
            return

        # --- Modus: nur wiederherstellen ---------------------------------------
        if args.restore_only:
            ids = None
            if args.only_broken:
                ids = collect_broken_ids(parse_mods_json(mods_json))
                if not ids:
                    print("No broken mods found. Exiting.")
                    return
                print(f"Restoring {len(ids)} broken mod(s): {ids}")
            else:
                print(f"Stelle alle Backups wieder her aus: {backup_dir}")
            restored = restore_mods(backup_dir, steam_mods, ids)
            print(f"\n{restored} Mod-Ordner wiederhergestellt.")
            return

        # --- Modus: abgebrochenen Lauf fortsetzen -------------------------------
        if args.resume:
            if not status_file.is_file():
                sys.exit(f"[FEHLER] Keine Statusdatei gefunden: {status_file}\n"
                         f"--resume setzt einen abgebrochenen --full-Lauf fort.")
            status = StatusFile.load(status_file)
            if not status.state:
                sys.exit(f"[FEHLER] Statusdatei enthaelt keine Mod-IDs: {status_file}")

            to_sub = status.ids_unfinished()
            if not to_sub:
                print("Alle Mods sind bereits abonniert. Nichts zu tun.")
                return

            to_delete = status.ids_where(StatusFile.NEEDS_DELETE)
            to_unsub = status.ids_where(StatusFile.NEEDS_UNSUB)

            print(f"Statusdatei    : {status_file}")
            print(f"Offen          : {len(to_sub)} von {len(status.state)} Mod(s)")
            print(f"  noch loeschen     : {to_delete or '-'}")
            print(f"  noch deabonnieren : {to_unsub or '-'}")
            print(f"  noch abonnieren   : {to_sub}")

            repair_round(args, status, steam_mods, browser_profile,
                         to_delete, to_unsub, to_sub)
            finish(status, backup_dir, steam_mods, list(status.state))
            return

        # --- Modus: kompletter Ablauf ------------------------------------------
        print("\n>>> Backing up mods...")
        backup_mods(steam_mods, backup_dir)

        broken_ids = collect_broken_ids(parse_mods_json(mods_json))
        if not broken_ids:
            print("No broken mods found. Exiting.")
            return

        print(f"Found {len(broken_ids)} broken mod(s): {broken_ids}")
        backup_list.write_text("\n".join(str(i) for i in broken_ids), encoding="utf-8")

        status = StatusFile(status_file, broken_ids)
        print(f"Statusdatei    : {status_file}")

        repair_round(args, status, steam_mods, browser_profile,
                     broken_ids, broken_ids, broken_ids)
        finish(status, backup_dir, steam_mods, broken_ids)

    try:
        run_mode()
    finally:
        # Auch nach Abbruch oder Fehler wieder starten
        if onedrive:
            print("\nStarte OneDrive wieder...")
            start_onedrive()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAbgebrochen. Der Stand jedes Mods steht in "
              f"{SCRIPT_DIR / 'FixModsStatus.txt'}")
        sys.exit(1)