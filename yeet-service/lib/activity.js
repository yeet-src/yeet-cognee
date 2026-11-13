import probe from "../activity.bpf.o";
import { ArrayMap, HashMap, RingBuf } from "yeet:bpf";

import { watched } from "./match.js";
import { PROCS } from "./queries.js";

const CAPACITY = Number(yeet.args?.["log-capacity"] ?? 500_000);
const AT_FDCWD = -100;
const O_WRONLY = 1, O_RDWR = 2, O_CREAT = 0o100;
const AF_INET6 = 10;
const PARENT_DEPTH = 6;
const STREAMS = ["execs", "opens", "connects", "chdirs"];
const NAME_CAP = 64;
const BASE = { exact: 1, prefix: 2, suffix: 3 };
const ENFORCE_ON = 0, ENFORCE_DENIED = 1, PROTECT_EXEC = 2, PROTECT_PTRACE = 3;
const DIR_MAX = 16;

const NOISE_PREFIXES = [
  "/usr/lib", "/lib", "/usr/share", "/usr/local/lib", "/etc/ld.so", "/proc/self/",
  "/proc/sys/", "/dev/null", "/dev/urandom", "/dev/random", "/dev/tty", "/dev/pts/",
  "/sys/fs/cgroup/", "/sys/devices/", "/sys/class/", "/etc/localtime", "/etc/nsswitch.conf",
  "/etc/host.conf", "/etc/resolv.conf", "/etc/hosts", "/etc/gai.conf", "/etc/ssl/", "/etc/pki/",
  "/etc/crypto-policies/", "/var/lib/ca-certificates/", "/proc/stat", "/proc/meminfo",
  "/proc/loadavg", "/proc/uptime", "/proc/diskstats", "/proc/vmstat",
];
const NOISE_PARTS = ["/site-packages/", "/__pycache__/", "/dist-packages/", "/node_modules/", "/lib/python", "/lib64/python", "/.pyenv/", "/pyenv.d/"];

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
let lost, denyInode, denyWrite, denyExec, denyBase, dirComponent, watchedMap, enforce;
let enforceWatch = [];

let clockOffsetMs = null;
let latestT = 0;

const learn = (pid, fields) => identity.set(pid, { cwds: [], ...identity.get(pid), ...fields });

const wallClock = (ns) => {
  if (clockOffsetMs === null) clockOffsetMs = Date.now() - Number(ns / 1_000_000n);
  return Number(ns / 1_000_000n) + clockOffsetMs;
};

const stamp = (e) => {
  const ns = BigInt(e.ts);
  const t = wallClock(ns);
  if (t > latestT) latestT = t;
  return { ns, t };
};

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
  log.push({ ns, t, kind: "open", pid: e.pid, uid: e.uid, comm: cstring(e.comm), path, dfd: e.dfd, access: access(e.flags), create: Boolean(e.flags & O_CREAT), blocked: Boolean(e.blocked) });
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
    .bind("deny_inode", { kind: "hash" })
    .bind("deny_write", { kind: "hash" })
    .bind("deny_exec", { kind: "hash" })
    .bind("deny_base", { kind: "array" })
    .bind("dir_component", { kind: "array" })
    .bind("watched", { kind: "hash" })
    .bind("enforce", { kind: "array" })
    .start();
  lost = new ArrayMap(control, "lost");
  denyInode = new HashMap(control, "deny_inode");
  denyWrite = new HashMap(control, "deny_write");
  denyExec = new HashMap(control, "deny_exec");
  denyBase = new ArrayMap(control, "deny_base");
  dirComponent = new ArrayMap(control, "dir_component");
  watchedMap = new HashMap(control, "watched");
  enforce = new ArrayMap(control, "enforce");
  await new RingBuf(control, "execs").subscribe(onExec);
  await new RingBuf(control, "opens").subscribe(onOpen);
  await new RingBuf(control, "connects").subscribe(onConnect);
  await new RingBuf(control, "chdirs").subscribe(onChdir);
  console.log(`activity: logging execs, opens, chdirs and connects (capacity ${CAPACITY})`);
  return control;
}

const fillInodes = async (map, inodes) => {
  for await (const [key] of map.entries()) await map.delete(key).catch(() => {});
  for (const { dev, ino } of inodes) await map.update({ ino: BigInt(ino), dev }, 1);
  return inodes.length;
};

const fillBases = async (map, entries, cap) => {
  const kept = entries.filter((b) => BASE[b.kind] && b.text && b.text.length < NAME_CAP).slice(0, cap);
  await map.update(0, { kind: 0, len: kept.length, text: "" });  // index 0: count in the len byte
  for (let i = 0; i < kept.length; i++) {
    await map.update(i + 1, { kind: BASE[kept[i].kind], len: kept[i].text.length, text: kept[i].text });
  }
  return kept.length;
};

export async function setEnforcement({
  on = false, watch = [], inodes = [], basenames = [],
  writes = [], execs = [], dirs = [], protectExec = false, protectPtrace = false,
} = {}) {
  if (!enforce) return { on: false };
  enforceWatch = watch;

  const counts = {
    inodes: await fillInodes(denyInode, inodes),
    writes: await fillInodes(denyWrite, writes),
    execs: await fillInodes(denyExec, execs),
    basenames: await fillBases(denyBase, basenames, 126),
    dirs: await fillBases(dirComponent, dirs.map((text) => ({ kind: "exact", text })), DIR_MAX),
  };

  await enforce.update(PROTECT_EXEC, protectExec ? 1n : 0n);
  await enforce.update(PROTECT_PTRACE, protectPtrace ? 1n : 0n);
  await enforce.update(ENFORCE_ON, on ? 1n : 0n);
  return { on, watch, protectExec, protectPtrace, ...counts };
}

async function syncWatched(procs) {
  if (!watchedMap || !enforceWatch.length) return;
  const live = new Set(procs.map((p) => p.pid));
  for (const proc of procs) {
    if (watched(proc, enforceWatch, [])) await watchedMap.update(proc.pid, 1);
  }
  for await (const [pid] of watchedMap.entries()) {
    if (!live.has(Number(pid))) await watchedMap.delete(pid).catch(() => {});
  }
}

async function deniedCount() {
  const n = enforce ? await enforce.lookup(ENFORCE_DENIED) : 0n;
  return Number(n ?? 0n);
}

const count = async (map) => {
  let n = 0;
  for await (const _ of map.entries()) n++;
  return n;
};

export async function enforceStatus() {
  if (!enforce) return { bound: false };
  const base = await denyBase.lookup(0);
  const dir = await dirComponent.lookup(0);
  return {
    bound: true,
    on: Number((await enforce.lookup(ENFORCE_ON)) ?? 0n),
    denied: await deniedCount(),
    watchedPids: await count(watchedMap),
    inodes: await count(denyInode),
    writes: await count(denyWrite),
    execs: await count(denyExec),
    basenames: base ? base.len : 0,
    dirs: dir ? dir.len : 0,
    protectExec: Number((await enforce.lookup(PROTECT_EXEC)) ?? 0n),
    protectPtrace: Number((await enforce.lookup(PROTECT_PTRACE)) ?? 0n),
    enforceWatch,
  };
}

async function refreshIdentities() {
  const { data } = await yeet.graph.query(PROCS);
  for (const p of data?.procs ?? []) {
    learn(p.pid, { comm: p.status?.name ?? identity.get(p.pid)?.comm, exe: p.exe, cmdline: p.cmdline ?? [], uid: p.uid, ppid: p.status?.ppid });
    const cwds = identity.get(p.pid).cwds;
    if (p.cwd && !cwds.some((c) => c.path === p.cwd)) cwds.push({ ns: null, path: p.cwd });
  }
  await syncWatched(data?.procs ?? []);
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

const serialized = ({ ns, dfd, blocked, ...event }) => (blocked ? { ...event, blocked } : event);

async function lostCounts() {
  const counts = await Promise.all(STREAMS.map((_, i) => lost.lookup(i)));
  return Object.fromEntries(STREAMS.map((name, i) => [name, Number(counts[i] ?? 0)]));
}

const watchedRoot = (pid, watch, self) => {
  let root = null;
  for (let depth = 0, cur = pid; depth < PARENT_DEPTH && cur != null; depth++) {
    const id = identity.get(cur);
    if (!id) break;
    if (watched(id, watch, self)) root = cur;
    cur = id.ppid;
  }
  return root;
};

export async function activity({ watch = [], self = [], since = 0, until = Infinity, limit = 2000 } = {}) {
  await refreshIdentities();
  for (const event of log.between(0, Infinity)) if (event.kind === "exec") executed.add(resolved(event).filename);

  // Bound the window by the newest event we actually have, not Date.now(): the
  // reader's wall clock can drift ahead of the monotonic clock events are
  // stamped with, and advancing a caller's cursor past that gap starves it.
  const ceiling = Math.min(until, latestT || Date.now());

  // Attribute each event to the topmost watched ancestor, so a child's events
  // still land on the group when the root process itself was idle this window.
  const roots = new Map();
  let total = 0;
  for (const event of log.between(since, ceiling)) {
    const rootPid = watchedRoot(event.pid, watch, self);
    if (rootPid == null) continue;
    if (!roots.has(rootPid)) {
      const id = identity.get(rootPid);
      roots.set(rootPid, {
        pid: rootPid, comm: id.comm, exe: id.exe, cmdline: id.cmdline ?? [], cwd: cwdAt(rootPid, event.ns), uid: id.uid,
        events: [], truncated: 0, children: new Set(),
      });
    }
    const group = roots.get(rootPid);
    if (event.pid !== rootPid) group.children.add(event.pid);
    total++;
    if (group.events.length >= limit) group.truncated++;
    else group.events.push(serialized(annotated(resolved(event))));
  }

  return {
    since: new Date(since).toISOString(),
    until: new Date(ceiling).toISOString(),
    log: { size: log.size, dropped: log.dropped, filtered, lost: await lostCounts(), denied: await deniedCount(), capacity: CAPACITY },
    events: total,
    processes: [...roots.values()].map((g) => ({ ...g, children: [...g.children] })),
  };
}
