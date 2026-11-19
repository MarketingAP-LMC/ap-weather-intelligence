"""Report whether GITHUB_TOKEN is saved for this Windows user, without printing it."""
import winreg

try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        value = str(winreg.QueryValueEx(k, "GITHUB_TOKEN")[0]).strip()
    print("GITHUB_TOKEN saved:", bool(value), "| looks like a fine-grained token:", value.startswith("github_pat_"))
except FileNotFoundError:
    print("GITHUB_TOKEN is NOT saved for this user")