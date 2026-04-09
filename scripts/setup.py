#!/usr/bin/env python3
"""
Setup script — generates launchd plist and validates config.
Reads from local.json (user-specific, gitignored) to avoid
leaking personal paths into the repo.
"""

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
LOCAL_CONFIG = PROJECT_ROOT / "local.json"
TEMPLATE = PROJECT_ROOT / "local.json.example"
PLIST_NAME = "com.aijournal.daily.plist"


def load_config() -> dict:
    if not LOCAL_CONFIG.exists():
        print(f"local.json not found. Creating from template...")
        shutil.copy(TEMPLATE, LOCAL_CONFIG)
        print(f"Created {LOCAL_CONFIG}")
        print("Edit it with your paths, then run this script again.")
        sys.exit(1)

    with open(LOCAL_CONFIG) as f:
        return json.load(f)


def generate_plist(config: dict) -> str:
    project_dir = config["project_dir"]
    hour = config.get("schedule_hour", 7)
    minute = config.get("schedule_minute", 0)
    path = config.get("path", "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aijournal.daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>{project_dir}/scripts/run.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>{project_dir}/logs/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{project_dir}/logs/launchd-stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path}</string>
    </dict>

    <key>KeepAlive</key>
    <false/>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def install_plist(plist_content: str, config: dict):
    """Write plist and install to ~/Library/LaunchAgents."""
    # Write to project dir (gitignored)
    local_plist = PROJECT_ROOT / PLIST_NAME
    local_plist.write_text(plist_content)
    print(f"Generated {local_plist}")

    # Copy to LaunchAgents
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    dest = launch_agents / PLIST_NAME
    shutil.copy(local_plist, dest)
    print(f"Installed to {dest}")
    print()
    print("To load:")
    print(f"  launchctl bootstrap gui/$(id -u) {dest}")
    print()
    print("To test immediately:")
    print(f"  launchctl kickstart gui/$(id -u)/com.aijournal.daily")


def main():
    print("The Watcher — Setup")
    print("=" * 40)

    config = load_config()

    # Validate
    project_dir = Path(config["project_dir"])
    if not project_dir.exists():
        print(f"Error: project_dir '{project_dir}' does not exist.")
        sys.exit(1)

    # Generate and install plist
    plist = generate_plist(config)
    install_plist(plist, config)

    # Check .env
    env_file = project_dir / ".env"
    if not env_file.exists():
        print()
        print("Warning: .env not found. Copy .env.example and add your API key:")
        print(f"  cp .env.example .env")

    print()
    print("Setup complete.")


if __name__ == "__main__":
    main()
