# FixMods

Python-Skript, das kaputte Steam-Workshop-Mods für **Oxygen Not Included**
automatisch repariert, indem es sie deabonniert und wieder abonniert.

## Problem

Gelegentlich meldet Oxygen Not Included Mods als "kaputt", obwohl die Dateien
eigentlich vorhanden sind – meist reicht ein erneutes Abonnieren über Steam,
um das zu beheben. Bei vielen betroffenen Mods ist das von Hand mühsam.
FixMods automatisiert diesen Ablauf über einen von Playwright gesteuerten
Browser und die Steam-Workshop-Endpunkte.

## Funktionsweise

1. Lokale Mod-Einstellungen sichern (`SteamModsBkp/`)
2. Lokale Ordner der als kaputt markierten Mods löschen
3. Über einen Browser bei Steam anmelden und die Mods deabonnieren
4. ONI starten, warten, wieder beenden
5. Die Mods erneut abonnieren
6. ONI erneut starten, warten, wieder beenden
7. Gesicherte Einstellungen zurückspielen

Der Fortschritt wird laufend in `FixModsStatus.txt` protokolliert, sodass ein
abgebrochener Lauf jederzeit mit `--resume` fortgesetzt werden kann, ohne
bereits erledigte Mods erneut anzufassen.

## Voraussetzungen

- Python 3.10+
- [Playwright](https://playwright.dev/python/) (`pip install playwright`)
- Oxygen Not Included über Steam installiert

Das Skript liegt in einem Unterordner `fixmods` des `mods`-Ordners:

```
<Dokumente>/Klei/OxygenNotIncluded/mods/fixmods/fixmods.py
```

## Verwendung

```
fixmods.py --full            Kompletter Ablauf (siehe oben)
fixmods.py --resume          Abgebrochenen Lauf fortsetzen
fixmods.py --backup          Nur die Mod-Einstellungen sichern
fixmods.py --restore         Nur die gesicherten Einstellungen zurückspielen
fixmods.py --restore --only-broken
                              Nur die in mods.json als kaputt markierten Mods
```

Ohne Modus-Flag zeigt das Skript nur die Hilfe an und macht sonst nichts.

Weitere Optionen (`--mod-dir`, `--subscribe-delay`, `--subscribe-method`) sind
über `fixmods.py --help` einsehbar.

## Hinweise

- Liegt der `mods`-Ordner in einem OneDrive-Pfad, wird OneDrive bei `--full`
  und `--resume` hart beendet und am Ende wieder gestartet, damit gelöschte
  Ordner nicht sofort neu synchronisiert werden. Bei `--backup`/`--restore`
  bleibt OneDrive unberührt.
- Das Ab-/Abonnieren läuft standardmäßig über einen direkten POST-Request pro
  Mod, um Steams Rate-Limit zu schonen. Mit `--subscribe-method page` kommt
  stattdessen die alte, langsamere Variante über die Mod-Webseiten zum
  Einsatz.

## Haftungsausschluss

Dieses Skript nutzt inoffizielle Steam-Web-Endpunkte und ist nicht mit Klei
Entertainment oder Valve verbunden. Nutzung auf eigene Verantwortung.
