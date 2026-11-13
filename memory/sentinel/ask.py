from .activity import Activity, describe
from .llm import generate
from .profile import Finding

SYSTEM_PROMPT = """You are a host-monitoring analyst triaging ONE host from three inputs. Your job \
is to decide a single thing: does a HUMAN need to make a decision about this now?

- FINDINGS: computed facts, and the reason you are being asked. A `policy` finding is \
an action the written policy forbids, or tampering with a program. A `novel` finding is \
an action no workload of that role has been seen to take before.
- ACTIVITY: what each watched workload did in the last minutes, recorded by the kernel. \
Ground truth. If a path, program or endpoint is not named here, it was NOT touched.
- MEMORY: reference only. The sandbox policy, the learned profile of each role, and \
human reviews of earlier verdicts.

Answer in exactly this shape and nothing else:

VERDICT: <YES or NO>
<2 to 5 sentences. Name each workload that stands out, say what it did, and say whether a \
human needs to decide on it. Cite only paths, programs and endpoints that appear in \
ACTIVITY or FINDINGS.>

VERDICT: YES means a human needs to decide or act now — surface it. VERDICT: NO means \
there is nothing for a human to decide right now.

Answer YES when a finding is a policy violation or tampering not already covered by a \
rule, or a novel action is concerning and no rule or past review in MEMORY settles it.

Answer NO when the workloads stayed within their learned profile and the policy, or when \
MEMORY holds a rule or a prior human review that already covers this behaviour — cite it \
("a reviewer confirmed this in verdict 1a2b3c4d") and let the standing policy handle it.

Rules:
- An attempt counts. A refused open of a forbidden path still needs a decision the first \
time: answer YES unless MEMORY already settles it.
- Never raise a concern that is not a finding, even if ACTIVITY or MEMORY mentions it.
- Never claim an example path from the policy was touched unless ACTIVITY shows it.
- No markdown, no bullets, no restating the policy."""

RETRIEVAL_QUERY = (
    "Sandbox policy for this host: which paths and endpoints are out of bounds for a workload. "
    "The learned profile of each workload role: which programs, directories and endpoints it normally uses. "
    "Human reviews of earlier verdicts about these workloads."
)

MEMORY_CAP = 4500


def verdict(question: str, acts: list[Activity], findings: list[Finding], memory: str, host: str) -> str:
    if not findings:
        return within_profile(acts)
    activity = "\n\n".join(describe(act, host) for act in acts)
    found = "\n".join(str(f) for f in findings)
    prompt = (
        f"===== FINDINGS =====\n{found}\n\n"
        f"===== ACTIVITY (ground truth, from the kernel) =====\n{activity}\n\n"
        f"===== MEMORY (reference only) =====\n{memory[:MEMORY_CAP]}\n\n"
        f"===== END OF INPUTS =====\n\n"
        f"QUESTION: {question}\n\n"
        "Answer in the required shape: a line starting with VERDICT:, then the evidence."
    )
    return generate(prompt, system=SYSTEM_PROMPT)


def needs_decision(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("VERDICT:"):
            return "YES" in stripped
    return False


def within_profile(acts: list[Activity]) -> str:
    if not acts:
        return "VERDICT: NO\nNo watched workload was active in the window."
    names = ", ".join(sorted(a.name for a in acts))
    return f"VERDICT: NO\n{names} stayed within the learned profile and the policy: nothing was run, opened or dialled that the profile does not already allow."


async def recall(dataset: str, query: str | None = None) -> str:
    import cognee

    results = await cognee.recall(query_text=query or RETRIEVAL_QUERY, datasets=[dataset], top_k=20, only_context=True)
    return "\n\n".join(text for text in (getattr(r, "text", None) or str(r) for r in results) if text.strip())
