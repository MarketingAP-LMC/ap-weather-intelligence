"""Save a GitHub access token for tools/publish_to_github.py without showing it on screen.

Run it yourself in a terminal from the project folder:
    python tools/set_github_token.py           # paste the token at the hidden prompt
    python tools/set_github_token.py --remove  # delete it again after publishing

The token is stored as the Windows user environment variable GITHUB_TOKEN. Never paste
a token into a chat window or an email.
"""
import ctypes
import getpass
import sys


def _broadcast():
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 5000, None)
    except Exception:  # noqa: BLE001
        pass


def main():
    if sys.platform != "win32":
        sys.exit("On macOS/Linux set it in your shell instead: export GITHUB_TOKEN=...")
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
    with key:
        if "--remove" in sys.argv:
            try:
                winreg.DeleteValue(key, "GITHUB_TOKEN")
                print("GITHUB_TOKEN removed.")
            except FileNotFoundError:
                print("GITHUB_TOKEN was not set.")
            _broadcast()
            return 0
        token = getpass.getpass("Paste the GitHub token (it stays hidden) and press Enter: ").strip()
        if not token:
            sys.exit("Nothing entered - no change.")
        if not token.startswith(("github_pat_", "ghp_")):
            print("Note: this does not look like a GitHub token (they start with github_pat_). Saving anyway.")
        winreg.SetValueEx(key, "GITHUB_TOKEN", 0, winreg.REG_SZ, token)
    _broadcast()
    print("Saved as your user environment variable GITHUB_TOKEN. Remove it after publishing with --remove.")
    return 0


if __name__ == "__main__":
    sys.exit(main())