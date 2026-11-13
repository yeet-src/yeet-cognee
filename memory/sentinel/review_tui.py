import curses
import textwrap

CYCLE = {"": "bad", "bad": "good", "good": ""}
MARK = {"": "[ ]", "bad": "[x]", "good": "[+]"}
HELP = "↑↓ move   space/g/b toggle   r reason   enter submit   q cancel"


class Review:
    def __init__(self, answer: dict):
        self.id = answer["id"]
        self.verdict = answer.get("verdict", "")
        self.findings = answer.get("findings", [])
        self.marks = ["" for _ in self.findings]
        self.reason = ""
        self.row = 0
        self.decision: dict | None = None

    def run(self) -> dict | None:
        if self.findings:
            curses.wrapper(self._loop)
        return self.decision

    def _loop(self, scr) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        for i, color in enumerate((curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_CYAN), 1):
            curses.init_pair(i, color, -1)
        while True:
            self._draw(scr)
            key = scr.getch()
            if key in (ord("q"), 27):
                return
            if key in (curses.KEY_UP, ord("k")):
                self.row = (self.row - 1) % len(self.findings)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.row = (self.row + 1) % len(self.findings)
            elif key == ord(" "):
                self.marks[self.row] = CYCLE[self.marks[self.row]]
            elif key in (ord("g"), ord("b")):
                want = "good" if key == ord("g") else "bad"
                self.marks[self.row] = "" if self.marks[self.row] == want else want
            elif key == ord("r"):
                self.reason = self._prompt(scr, self.reason)
            elif key in (curses.KEY_ENTER, 10, 13):
                if self._commit():
                    return

    def _draw(self, scr) -> None:
        scr.erase()
        height, width = scr.getmaxyx()
        y = 0
        for line in textwrap.wrap(self.verdict, width - 2)[: max(1, height // 3)]:
            scr.addnstr(y, 1, line, width - 2, curses.color_pair(3))
            y += 1
        y += 1

        for i, finding in enumerate(self.findings):
            if y >= height - 2:
                break
            mark = self.marks[i]
            tag = MARK[mark]
            kind_color = curses.color_pair(1 if finding["kind"] == "policy" else 0)
            text = f" {i + 1:>2}. {finding['workload']}: {finding['what']}"
            attr = curses.A_REVERSE if i == self.row else 0
            scr.addnstr(y, 1, tag, 3, curses.color_pair({"good": 2, "bad": 1}.get(mark, 0)) | attr)
            scr.addnstr(y, 4, text, width - 5, kind_color | attr)
            y += 1

        reason = f"reason: {self.reason or '(none — press r)'}"
        scr.addnstr(height - 2, 1, reason, width - 2, curses.A_DIM)
        scr.addnstr(height - 1, 1, HELP, width - 2, curses.A_DIM)
        scr.refresh()

    def _prompt(self, scr, value: str) -> str:
        height, width = scr.getmaxyx()
        curses.curs_set(1)
        buffer = list(value)
        while True:
            shown = "reason: " + "".join(buffer)
            scr.addnstr(height - 2, 1, shown.ljust(width - 2), width - 2)
            scr.move(height - 2, min(1 + len(shown), width - 2))
            scr.refresh()
            key = scr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                break
            if key == 27:
                buffer = list(value)
                break
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if buffer:
                    buffer.pop()
            elif 32 <= key < 127:
                buffer.append(chr(key))
        curses.curs_set(0)
        return "".join(buffer).strip()

    def _commit(self) -> bool:
        bad = [i for i, m in enumerate(self.marks) if m == "bad"]
        good = [i for i, m in enumerate(self.marks) if m == "good"]
        if not bad and not good:
            return False
        self.decision = {"id": self.id, "bad": bad, "good": good, "reason": self.reason}
        return True
