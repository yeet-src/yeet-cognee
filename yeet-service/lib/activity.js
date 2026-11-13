import probe from "../activity.bpf.o";
import { ArrayMap, RingBuf } from "yeet:bpf";

import { watched } from "./match.js";
import { PROCS } from "./queries.js";

const CAPACITY = Number(yeet.args?.["log-capacity"] ?? 500_000);
const AT_FDCWD = -100;
const O_WRONLY = 1, O_RDWR = 2, O_CREAT = 0o100;
const AF_INET6 = 10;
const PARENT_DEPTH = 6;
const STREAMS = ["execs", "opens", "connects", "chdirs"];

const NOISE_PREFIXES = [
  "/usr/lib", "/lib", "/usr/share", "/usr/local/lib", "/etc/ld.so", "/proc/self/",
  "/proc/sys/", "/dev/null", "/dev/urandom", "/dev/random", "/dev/tty", "/dev/pts/",
  "/sys/fs/cgroup/", "/sys/devices/", "/sys/class/", "/etc/localtime", "/etc/nsswitch.conf",
  "/etc/host.conf", "/etc/resolv.conf", "/etc/hosts", "/etc/gai.conf", "/etc/ssl/", "/etc/pki/",
  "/etc/crypto-policies/", "/var/lib/ca-certificates/", "/proc/stat", "/proc/meminfo",
  "/proc/loadavg", "/proc/uptime", "/proc/diskstats", "/proc/vmstat",
];
const NOISE_PARTS = ["/site-packages/", "/__pycache__/", "/dist-packages/", "/node_modules/", "/lib/python", "/lib64/python"];

const isNoise = (path) =>
  NOISE_PREFIXES.some((p) => path.startsWith(p)) ||
  NOISE_PARTS.some((p) => path.includes(p)) ||
  path.endsWith(".so") || path.includes(".so.") || path.endsWith(".pyc");

const bytes = (value) => (Array.isArray(value) ? value : Object.values(value ?? {}));

const cstring = (value) => {
  const end = bytes(value).indexOf(0);
  return String.fromCharCode(...bytes(value).slice(0, end < 0 ? undefined : end));
};

const argv = (value, len) =>
  String.fromCharCode(...bytes(value).slice(0, len)).split("\0").filter(Boolean);

const address = (value, family) => {
  const v = bytes(value);
  if (family !== AF_INET6) return v.slice(0, 4).join(".");
  const groups = [];
  for (let i = 0; i < 16; i += 2) groups.push(((v[i] << 8) | v[i + 1]).toString(16));
  return groups.join(":").replace(/(^|:)0(:0)+(:|$)/, "::");
};

const access = (flags) => (flags & O_RDWR ? "read-write" : flags & O_WRONLY ? "write" : "read");

const normalize = (path) => path.replace(/\/\.(?=\/|$)/g, "").replace(/\/{2,}/g, "/") || "/";

const join = (path, cwd) => normalize(path.startsWith("/") || !cwd ? path : `${cwd}/${path}`);


class Ring {
  constructor(capacity) {
    this.slots = new Array(capacity);
    this.capacity = capacity;
    this.head = 0;
    this.size = 0;
    this.dropped = 0;
  }

  push(event) {
    this.slots[(this.head + this.size) % this.capacity] = event;
    if (this.size < this.capacity) this.size++;
    else { this.head = (this.head + 1) % this.capacity; this.dropped++; }
  }

  *between(since, until) {
    for (let i = 0; i < this.size; i++) {
      const event = this.slots[(this.head + i) % this.capacity];
      if (event.t >= since && event.t <= until) yield event;
    }
  }
}

const log = new Ring(CAPACITY);
const identity = new Map();
const executed = new Set();
let filtered = 0;
let lost;

let clockOffsetMs = null;

const learn = (pid, fields) => identity.set(pid, { cwds: [], ...identity.get(pid), ...fields });

const wallClock = (ns) => {
  if (clockOffsetMs === null) clockOffsetMs = Date.now() - Number(ns / 1_000_000n);
  return Number(ns / 1_000_000n) + clockOffsetMs;
};

const stamp = (e) => ({ ns: BigInt(e.ts), t: wallClock(BigInt(e.ts)) });

const onExec = ({ exec_event: e }) => {
  const { ns, t } = stamp(e);
  const comm = cstring(e.comm);
  const cmdline = argv(e.argv, e.argv_len);
  learn(e.pid, { comm, exe: cstring(e.filename), cmdline, ppid: e.ppid, uid: e.uid, execNs: ns });
  log.push({ ns, t, kind: "exec", pid: e.pid, ppid: e.ppid, uid: e.uid, comm, filename: cstring(e.filename), cmdline, ino: Number(e.ino), dev: e.dev });
};

const onOpen = ({ open_event: e }) => {
  const path = cstring(e.path);
  if (!path) return;
  if (isNoise(path)) { filtered++; return; }
  const { ns, t } = stamp(e);
  log.push({ ns, t, kind: "open", pid: e.pid, uid: e.uid, comm: cstring(e.comm), path, dfd: e.dfd, access: access(e.flags), create: Boolean(e.flags & O_CREAT) });
};

const onChdir = ({ chdir_event: e }) => {
  learn(e.pid, {});
  identity.get(e.pid).cwds.push({ ns: BigInt(e.ts), path: cstring(e.path) });
};

const onConnect = ({ connect_event: e }) => {
  const { ns, t } = stamp(e);
  log.push({ ns, t, kind: "connect", pid: e.pid, uid: e.uid, comm: cstring(e.comm), addr: address(e.daddr, e.family), port: e.dport, family: e.family === AF_INET6 ? "inet6" : "inet" });
};

export async function start() {
  const control = await probe
    .bind("execs", { kind: "ringbuf", btf_struct: "exec_event", capacity: 8192 })
    .bind("opens", { kind: "ringbuf", btf_struct: "open_event", capacity: 65536 })
    .bind("connects", { kind: "ringbuf", btf_struct: "connect_event", capacity: 8192 })
    .bind("chdirs", { kind: "ringbuf", btf_struct: "chdir_event", capacity: 4096 })
    .bind("lost", { kind: "array" })
    .start();
  lost = new ArrayMap(control, "lost");
  await new RingBuf(control, "execs").subscribe(onExec);
  await new RingBuf(control, "opens").subscribe(onOpen);
  await new RingBuf(control, "connects").subscribe(onConnect);
  await new RingBuf(control, "chdirs").subscribe(onChdir);
  console.log(`activity: logging execs, opens, chdirs and connects (capacity ${CAPACITY})`);
  return control;
}

async function refreshIdentities() {
  const { data } = await yeet.graph.query(PROCS);
  for (const p of data?.procs ?? []) {
    learn(p.pid, { comm: p.status?.name ?? identity.get(p.pid)?.comm, exe: p.exe, cmdline: p.cmdline ?? [], uid: p.uid, ppid: p.status?.ppid });
    const cwds = identity.get(p.pid).cwds;
    if (p.cwd && !cwds.some((c) => c.path === p.cwd)) cwds.push({ ns: null, path: p.cwd });
  }
}

const cwdAt = (pid, ns, depth = 0) => {
  const id = identity.get(pid);
  if (!id || depth > PARENT_DEPTH) return undefined;
  const changes = id.cwds.filter((c) => c.ns !== null && c.ns <= ns);
  if (changes.length) {
    const last = changes[changes.length - 1];
    return join(last.path, cwdAt(pid, last.ns - 1n, depth));
  }
  const inherited = id.execNs ? cwdAt(id.ppid, id.execNs, depth + 1) : undefined;
  return inherited ?? id.cwds.find((c) => c.ns === null)?.path ?? id.cwds[0]?.path;
};

const resolved = (event) => {
  if (event.kind === "exec") return { ...event, filename: join(event.filename, cwdAt(event.pid, event.ns)) };
  if (event.kind === "open" && event.dfd === AT_FDCWD) return { ...event, path: join(event.path, cwdAt(event.pid, event.ns)) };
  return event;
};

const annotated = (event) =>
  event.kind === "open" && event.access !== "read" && executed.has(event.path) ? { ...event, executed: true } : event;

const serialized = ({ ns, dfd, ...event }) => event;

async function lostCounts() {
  const counts = await Promise.all(STREAMS.map((_, i) => lost.lookup(i)));
  return Object.fromEntries(STREAMS.map((name, i) => [name, Number(counts[i] ?? 0)]));
}

const rootOf = (roots, pid) => {
  for (let depth = 0, cur = pid; depth < PARENT_DEPTH && cur != null; depth++) {
    if (roots.has(cur)) return roots.get(cur);
    cur = identity.get(cur)?.ppid;
  }
  return null;
};

export async function activity({ watch = [], self = [], since = 0, until = Infinity, limit = 2000 } = {}) {
  await refreshIdentities();
  for (const event of log.between(0, Infinity)) if (event.kind === "exec") executed.add(resolved(event).filename);

  const roots = new Map();
  for (const event of log.between(since, until)) {
    const id = identity.get(event.pid);
    if (id && !roots.has(event.pid) && watched(id, watch, self)) {
      roots.set(event.pid, {
        pid: event.pid, comm: id.comm, exe: id.exe, cmdline: id.cmdline ?? [], cwd: cwdAt(event.pid, event.ns), uid: id.uid,
        events: [], truncated: 0, children: new Set(),
      });
    }
  }

  for (const pid of [...roots.keys()]) if (rootOf(roots, identity.get(pid)?.ppid)) roots.delete(pid);

  let total = 0;
  for (const event of log.between(since, until)) {
    const group = rootOf(roots, event.pid);
    if (!group) continue;
    if (event.pid !== group.pid) group.children.add(event.pid);
    total++;
    if (group.events.length >= limit) group.truncated++;
    else group.events.push(serialized(annotated(resolved(event))));
  }

  return {
    since: new Date(since).toISOString(),
    until: new Date(Math.min(until, Date.now())).toISOString(),
    log: { size: log.size, dropped: log.dropped, filtered, lost: await lostCounts(), capacity: CAPACITY },
    events: total,
    processes: [...roots.values()].map((g) => ({ ...g, children: [...g.children] })),
  };
}
