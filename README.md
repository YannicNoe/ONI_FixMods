# Oxygen Not Included – OneDrive Mod Fix
This script fixes the **restart loop issue** that can occur when using **OneDrive together with Steam Workshop mods** for *Oxygen Not Included*.
The goal is a script that **“just fixes it”** — no parameters, no manual mod management.
## How It Works
1. OneDrive is stopped
2. All mod configurations are copied into a backup-directory (see the source code for the exclusions)
3. A **Microsoft Edge** window opens and you log into **Steam**. For this needed files are downloaded automatically.
4. The script detects **problematic mods** (`status: 3` in `mods.json`).
5. These mods are **automatically unsubscribed** via the Steam Workshop.
6. *Oxygen Not Included* is started.  
   Wait until the game has started, then press **ENTER** in the console.
7. The game is stopped and all affected mods are **re-subscribed automatically** using Edge.
8. *Oxygen Not Included* is started again.  
   Wait until the game has started, then press **ENTER** in the console.
9. Now all configurations are copied back. Note this can also overwrite files that are not configurations, there can be edge-cases where this is problematic.
The process is now complete.
## Notes
- Browser automation is done using **Selenium**
- **Microsoft Edge** must be installed
- Steam login is required when the browser opens
- OneDrive is stopped during execution and **not restarted automatically**
- The final game restart restores mod-specific configuration files
- This script is provided **as-is** and is a **quick-and-dirty solution**
