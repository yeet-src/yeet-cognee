/* The activity log: what processes did, not just what they hold open.
 *
 * Three BPF programs (every exec, every openat, every outbound TCP connect)
 * feed ring buffers this module drains into one bounded in-memory log. A
 * reading answers "what is open right now"; this answers "what happened
 * between two readings", which is where a brief file read or a one-shot
 * connection lives.
 *
 * Events are kept host-wide and attributed on request: `activity()` picks
 * the processes whose identity matches the watch list, follows their
 * children (an agent that runs `git` did what `git` did), and returns the
 * events in the window. Nothing is interpreted here.
 */

import probe from "../activity.bpf.o";
import { RingBuf } from "yeet:bpf";

import { PROCS } from "./queries.js";

const CAPACITY = Number(yeet.args?.["log-capacity"] ?? 500_000);
const O_WRONLY = 1, O_RDWR = 2, O_CREAT = 0o100;

// Opens every process makes just by running: shared libraries, locale and
// certificate data, its own /proc entry, the null device. A busy host makes
// tens of thousands of these a second; they would push the log's window
// down to seconds, and none of them tells a judgement anything.
const NOISE_PREFIXES = [
  "/usr/lib", "/lib", "/usr/share", "/usr/local/lib", "/etc/ld.so", "/proc/self/",
  "/proc/sys/", "/dev/null", "/dev/urandom", "/dev/random", "/dev/tty", "/dev/pts/",
  "/sys/fs/cgroup/", "/sys/devices/", "/sys/class/", "/etc/localtime", "/etc/nsswitch.conf",
  "/etc/host.conf", "/etc/resolv.conf", "/etc/hosts", "/etc/gai.conf", "/etc/ssl/", "/etc/pki/",
  "/etc/crypto-policies/", "/var/lib/ca-certificates/", "/proc/stat", "/proc/meminfo",
  "/proc/loadavg", "/proc/uptime", "/proc/net/", "/proc/diskstats", "/proc/vmstat",
];
const NOISE_PARTS = ["/site-packages/", "/__pycache__/", "/dist-packages/", "/node_modules/", "/lib/python"];
const isNoise = (path) => {
  for (const p of NOISE_PREFIXES) if (path.startsWith(p)) return true;
  for (const p of NOISE_PARTS) if (path.includes(p)) return true;
  return path.endsWith(".so") || path.includes(".so.") || path.endsWith(".pyc");
};
let filtered = 0;

const str = (bytes) => {
  const v = Array.isArray(bytes) ? bytes : Object.values(bytes ?? {});
  let s = "";
  for (const b of v) {
    if (!b) break;
    s += String.fromCharCode(b);
  }
  return s;
};

const addr = (bytes, family) => {
  const v = Array.isArray(bytes) ? bytes : Object.values(bytes ?? {});
  if (family !== 10) return v.slice(0, 4).join(".");
  const parts = [];
  for (let i = 0; i < 16; i += 2) parts.push(((v[i] << 8) | v[i + 1]).toString(16));
  return parts.join(":").replace(/(^|:)0(:0)+(:|$)/, "::");
};

// A ring of events, oldest dropped first.
const log = new Array(CAPACITY);
let head = 0, size = 0, dropped = 0;
function push(event) {
  log[(head + size) % CAPACITY] = event;
  if (size < CAPACITY) size++;
  else { head = (head + 1) % CAPACITY; dropped++; }
}
function* events() {
  for (let i = 0; i < size; i++) yield log[(head + i) % CAPACITY];
}

// Who a pid is: from its exec (filename, parent) and, for processes that
// were already running, from the system graph on request.
const identity = new Map(); // pid -> { comm, exe, cmdline, cwd, ppid, uid }
const parent = new Map();   // pid -> ppid, from execs

function remember(pid, fields) {
  identity.set(pid, { ...(identity.get(pid) ?? {}), ...fields });
}

export async function start() {
  const control = await probe
    .bind("execs", { kind: "ringbuf", btf_struct: "exec_event", capacity: 8192 })
    .bind("opens", { kind: "ringbuf", btf_struct: "open_event", capacity: 65536 })
    .bind("connects", { kind: "ringbuf", btf_struct: "connect_event", capacity: 8192 })
    .start();

  await new RingBuf(control, "execs").subscribe(({ exec_event: e }) => {
    const filename = str(e.filename);
    parent.set(e.pid, e.ppid);
    remember(e.pid, { comm: str(e.comm), exe: filename, ppid: e.ppid, uid: e.uid });
    push({ t: Date.now(), kind: "exec", pid: e.pid, ppid: e.ppid, uid: e.uid, comm: str(e.comm), filename });
  });
  await new RingBuf(control, "opens").subscribe(({ open_event: e }) => {
    const path = str(e.path);
    if (!path) return;
    if (isNoise(path)) { filtered++; return; }
    const acc = e.flags & O_RDWR ? "read-write" : e.flags & O_WRONLY ? "write" : "read";
    push({ t: Date.now(), kind: "open", pid: e.pid, uid: e.uid, comm: str(e.comm), path, access: acc, create: Boolean(e.flags & O_CREAT) });
  });
  await new RingBuf(control, "connects").subscribe(({ connect_event: e }) => {
    push({ t: Date.now(), kind: "connect", pid: e.pid, uid: e.uid, comm: str(e.comm), addr: addr(e.daddr, e.family), port: e.dport, family: e.family === 10 ? "inet6" : "inet" });
  });

  console.log(`activity: logging execs, opens and connects (capacity ${CAPACITY})`);
  return control;
}

async function refreshIdentities() {
  const { data } = await yeet.graph.query(PROCS);
  for (const p of data?.procs ?? []) {
    remember(p.pid, {
      comm: p.status?.name ?? identity.get(p.pid)?.comm,
      exe: p.exe, cmdline: p.cmdline ?? [], cwd: p.cwd, uid: p.uid, ppid: p.status?.ppid,
    });
    if (p.status?.ppid != null && !parent.has(p.pid)) parent.set(p.pid, p.status.ppid);
  }
}

function haystack(id) {
  return [id?.exe ?? "", (id?.cmdline ?? []).join(" "), id?.comm ?? ""].join(" ");
}

/**
 * Events in [since, until] for the processes matching `watch`, grouped by
 * matched (root) process. Children found through exec parentage are folded
 * into their root.
 */
export async function activity({ watch = [], self = [], since = 0, until = Infinity, limit = 2000 } = {}) {
  await refreshIdentities();

  const isSelf = (id) => self.some((m) => haystack(id).includes(m));
  const roots = new Map(); // pid -> group
  const pidsInWindow = new Set();
  for (const e of events()) {
    if (e.t < since || e.t > until) continue;
    pidsInWindow.add(e.pid);
  }
  for (const pid of pidsInWindow) {
    const id = identity.get(pid);
    if (!id || isSelf(id)) continue;
    if (watch.some((needle) => haystack(id).includes(needle))) {
      roots.set(pid, { pid, comm: id.comm, exe: id.exe, cmdline: id.cmdline ?? [], cwd: id.cwd, uid: id.uid, events: [], truncated: 0, children: new Set() });
    }
  }

  // Walk each pid up its exec parentage to a root, a few levels at most.
  const rootOf = (pid) => {
    let cur = pid;
    for (let depth = 0; depth < 6 && cur != null; depth++) {
      if (roots.has(cur)) return cur;
      cur = parent.get(cur);
    }
    return null;
  };

  let total = 0;
  for (const e of events()) {
    if (e.t < since || e.t > until) continue;
    const root = rootOf(e.pid);
    if (root == null) continue;
    const group = roots.get(root);
    if (e.pid !== root) group.children.add(e.pid);
    total++;
    if (group.events.length >= limit) { group.truncated++; continue; }
    group.events.push(e);
  }

  return {
    since: new Date(since).toISOString(),
    until: new Date(Math.min(until, Date.now())).toISOString(),
    log: { size, dropped, filtered, capacity: CAPACITY },
    events: total,
    processes: [...roots.values()].map((g) => ({ ...g, children: [...g.children] })),
  };
}
