import { watched as isWatched } from "./match.js";
import { PROCS, SYSTEM, TCP, procDetail } from "./queries.js";

async function query(gql) {
  const { data, errors } = await yeet.graph.query(gql);
  const fatal = errors?.find((e) => !e.path?.includes("proc"));
  if (fatal) throw new Error(fatal.message);
  return data ?? {};
}

const gone = (error) => /no such|not found|esrch/i.test(String(error?.message));

async function detail(pid, socketByInode) {
  try {
    const { proc } = await query(procDetail(pid));
    if (!proc) return null;
    proc.sockets = (proc.fds ?? [])
      .filter((fd) => fd.kind === "SOCKET" && socketByInode.has(fd.inode))
      .map((fd) => socketByInode.get(fd.inode));
    return proc;
  } catch (error) {
    if (gone(error)) return null;
    throw error;
  }
}

export async function reading({ watch = [], self = [], hostname = null, topOther = 5 } = {}) {
  const [system, { procs = [] }, { tcp = [], tcp6 = [] }] = await Promise.all([query(SYSTEM), query(PROCS), query(TCP)]);
  const socketByInode = new Map([...tcp, ...tcp6].filter((s) => s.inode).map((s) => [s.inode, s]));
  const watched = [];
  for (const proc of procs.filter((p) => isWatched(p, watch, self))) {
    const found = await detail(proc.pid, socketByInode);
    if (found) watched.push(found);
  }

  const otherProcs = procs
    .filter((p) => p.status?.vmrss)
    .sort((a, b) => b.status.vmrss - a.status.vmrss)
    .slice(0, topOther);

  return { hostname, taken_at: new Date().toISOString(), system, watched, other_procs: otherProcs };
}
