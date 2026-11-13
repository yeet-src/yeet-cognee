import fnmatch
import json
import pathlib
import posixpath
from dataclasses import asdict, dataclass

from .llm import generate

RULES = pathlib.Path(__file__).resolve().parent.parent / "rules.json"

SYSTEM_PROMPT = """A human reviewer confirmed that a workload's behaviour was an incident. Turn their \
ruling into file path patterns that would catch the same kind of thing next time, not just \
the same path. Generalize by what the reviewer is worried about: if they object to reading \
AWS credentials because secrets must not leak, the patterns cover other secret files too.

You are also given PRECEDENT: the rules already in force, each with the verdict it came \
from, and earlier human reviews. Stay consistent with it. If this ruling is the same \
concern as an existing rule, extend that rule: return its verdict id in "extends" and the \
patterns to add. If it is a new concern, "extends" is null.

Answer with JSON only, in this shape and nothing else:
{"extends": null, "patterns": ["*/.aws/credentials", "*/.env*", "*.pem"], "reason": "credential files must not be read"}

Patterns are shell globs matched against the full path and against the file name. Three to \
eight patterns. The reason is one short sentence in the reviewer's words."""


@dataclass(frozen=True)
class Rule:
    patterns: tuple[str, ...]
    reason: str
    precedents: tuple[str, ...]

    @property
    def verdict(self) -> str:
        return self.precedents[0]

    def matches(self, path: str) -> bool:
        name = posixpath.basename(path)
        return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in self.patterns)

    def extended(self, patterns: tuple[str, ...], verdict: str) -> "Rule":
        return Rule(tuple(dict.fromkeys(self.patterns + patterns)), self.reason, self.precedents + (verdict,))

    def __str__(self) -> str:
        return f"{', '.join(self.patterns)}  ({self.reason}; from {', '.join(self.precedents)})"


def load() -> list[Rule]:
    if not RULES.exists():
        return []
    return [Rule(tuple(r["patterns"]), r["reason"], tuple(r["precedents"])) for r in json.loads(RULES.read_text())]


def save(rules: list[Rule]) -> None:
    RULES.write_text(json.dumps([asdict(r) for r in rules], indent=2) + "\n")


def compile(verdict_id: str, findings: list[dict], note: str, reviews: str) -> Rule | None:
    paths = [f["what"] for f in findings if "`" in f["what"]]
    if not paths:
        return None
    existing = load()
    precedent = "\n".join(f"- rule from verdict {r.verdict}: {', '.join(r.patterns)} ({r.reason})" for r in existing) or "- no rules yet"
    prompt = (
        f"===== PRECEDENT =====\nRules in force:\n{precedent}\n\nEarlier reviews:\n{reviews[:3000] or 'none'}\n\n"
        f"===== THIS RULING =====\nThe reviewer said: {note or 'confirmed'}\n\nThe findings they confirmed:\n" + "\n".join(paths)
    )
    try:
        answer = json.loads(_json_only(generate(prompt, system=SYSTEM_PROMPT)))
        patterns = tuple(str(p) for p in answer["patterns"] if str(p).strip() and ":" not in str(p))
        reason = str(answer["reason"]).strip()
        extends = answer.get("extends")
    except (ValueError, KeyError, TypeError):
        patterns, reason, extends = tuple(_quoted(p) for p in paths), note or "confirmed by a reviewer", None
    if not patterns:
        return None
    parent = next((r for r in existing if extends in r.precedents), None)
    rule = parent.extended(patterns, verdict_id) if parent else Rule(patterns, reason, (verdict_id,))
    save([*(r for r in existing if r is not parent), rule])
    return rule


def _json_only(text: str) -> str:
    return text[text.find("{"):text.rfind("}") + 1]


def _quoted(what: str) -> str:
    return what.split("`")[1]
