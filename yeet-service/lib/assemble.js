/* Assemble one complete host reading from the system graph.
 *
 * This is the orchestration the cognee client used to do over SSH: run the
 * standing queries, match the watched processes, pull their detail, attribute
 * each process's sockets by inode, and rank the rest by memory. The result is
 * a single JSON object -- structured kernel truth -- that the HTTP route
 * returns and the cognee side folds into prose.
 */

import { SYSTEM, PROCS, TCP, procDetail } from "./queries.js";

// Processes that are part of the sentinel itself, never reported as a
// watched workload so the tools never observe themselves. Extendable via
// the `self` query parameter.
const DEFAULT_SELF = ["sentinel.", "-m sentinel", "scenario/fleet.py", "demo.sh", "/ask", "/remember", "/activity", "/reading"];

async function query(gql) {
  const { data, errors } = await yeet.graph.query(gql);
  if (errors?.length) {
    // A process that exited between the listing and its detail query is a
    // path error, not a reason to fail the whole read.
    const fatal = errors.find((e) => !e.path?.includes("proc"));
    if (fatal) throw new Error(fatal.message);
  }
  return data ?? {};
}

function haystack(proc) {
  return [proc.exe || "", (proc.cmdline || []).join(" ")].join(" ").trim();
}

function gone(message) {
  const m = String(message).toLowerCase();
  return m.includes("no such") || m.includes("not found") || m.includes("esrch");
}

/**
 * @param {object} opts
 * @param {string[]} opts.watch  substrings matched against each process's exe + cmdline
 * @param {string[]} opts.self   extra self-markers to exclude from watch matching
 * @param {string}   opts.hostname  the host's own name, for the reading header
 * @param {number}   opts.topOther  how many non-watched processes to include, ranked by RSS
 */
export async function reading({ watch = [], self = [], hostname = null, topOther = 5 } = {}) {
  const selfMarkers = [...DEFAULT_SELF, ...self];

  const [system, procsData, tcpData] = await Promise.all([
    query(SYSTEM),
    query(PROCS),
    query(TCP),
  ]);

  const procs = procsData.procs ?? [];
  const sockets = [...(tcpData.tcp ?? []), ...(tcpData.tcp6 ?? [])];
  const socketByInode = new Map();
  for (const s of sockets) if (s.inode) socketByInode.set(s.inode, s);

  const isSelf = (proc) => selfMarkers.some((m) => haystack(proc).includes(m));
  const matches = (proc) =>
    !isSelf(proc) && watch.some((needle) => haystack(proc).includes(needle));

  const watched = [];
  if (watch.length) {
    for (const proc of procs) {
      if (!matches(proc)) continue;
      let detail;
      try {
        detail = (await query(procDetail(proc.pid))).proc;
      } catch (err) {
        if (gone(err?.message)) continue; // exited mid-read -- expected
        throw err;
      }
      if (!detail) continue;
      detail.sockets = (detail.fds ?? [])
        .filter((fd) => fd.kind === "SOCKET" && socketByInode.has(fd.inode))
        .map((fd) => socketByInode.get(fd.inode));
      watched.push(detail);
    }
  }

  const otherProcs = procs
    .filter((p) => p.status?.vmrss)
    .sort((a, b) => b.status.vmrss - a.status.vmrss)
    .slice(0, topOther);

  return {
    hostname,
    taken_at: new Date().toISOString(),
    system,
    watched,
    other_procs: otherProcs,
  };
}
