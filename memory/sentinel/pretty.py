import datetime as dt
import re

RESET = "\033[0m"
BOLD = "\033[1m"
GREY = "\033[90m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GREEN = "\033[32m"


def stamp() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def emphasize(text: str, base: str = "") -> str:
    bolded = re.sub(r"`([^`]+)`", lambda m: f"{BOLD}{m.group(1)}{RESET}{base}", text)
    return bolded.replace("BLOCKED", f"{RED}{BOLD}BLOCKED{RESET}{base}")


def workload_line(name: str, summary: str) -> str:
    return f"{GREY}{stamp()}{RESET}  {CYAN}{BOLD}{name:<11}{RESET}{emphasize(summary)}"


def finding_line(f: dict) -> str:
    base = RED if f["kind"] == "policy" else YELLOW
    tag = "POLICY" if f["kind"] == "policy" else "novel"
    return f"{GREY}{stamp()}{RESET}  {base}{BOLD}{tag:<6}{RESET} {CYAN}{f['workload']:<11}{RESET}{base}{emphasize(f['what'], base)}{RESET}"


def numbered_finding(i: int, f: dict) -> str:
    base = RED if f["kind"] == "policy" else YELLOW
    return f"  {BOLD}{i:>2}.{RESET} {base}{emphasize(f['what'], base)}{RESET}  {GREY}[{f['kind']}] {f['workload']}{RESET}"


def event_line(ev: dict) -> str:
    verb = ev["verb"]
    color = {"exec": GREEN, "conn": YELLOW}.get(verb, "")
    blocked = f"  {RED}{BOLD}BLOCKED{RESET}" if ev.get("blocked") else ""
    return f"{GREY}{stamp()}  {ev['workload']:<11}{RESET}{color}{verb:<5}{RESET} {BOLD}{ev['what']}{RESET}{blocked}"


def banner(text: str) -> str:
    return f"{GREEN}{BOLD}{text}{RESET}"


def decision_banner(needs: bool) -> str:
    return f"{RED}{BOLD}⚠ DECISION NEEDED{RESET}" if needs else f"{GREEN}{BOLD}✓ no action needed{RESET}"
