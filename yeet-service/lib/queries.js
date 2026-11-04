/* The GraphQL the sysgraph reader runs against yeet's system graph.
 *
 * These are read-only queries against `yeet.graph` -- the live graph the
 * daemon builds straight from the kernel. They are the ground truth: a
 * process cannot hide an open fd or socket from the graph. The reader runs
 * the standing queries once per request, then one detail query per watched
 * process, and hands the assembled JSON to the HTTP route.
 *
 * Nothing here is interpreted. Meaning (what is sensitive, what is normal)
 * is added downstream, by the cognee side, on purpose.
 */

// Host, load, memory, GPU -- one read of the whole-box state.
export const SYSTEM = `
{
  host { uptime { uptime } }
  load_average { one five fifteen cur max }
  meminfo { mem_total mem_available }
  nvidia {
    devices {
      index name temperature
      utilization_rates { gpu memory }
      memory { used total }
      running_compute_processes { pid used_gpu_memory }
    }
  }
}`;

// Every process, shallow -- enough to match the watch and rank by memory.
export const PROCS = `
{
  procs {
    pid uid exe cwd cmdline fd_count
    status { name ppid vmrss threads }
  }
}`;

// Open sockets, keyed later by inode so they can be attributed to a process.
export const TCP = `
{
  tcp  { state uid inode local_address { addr port } remote_address { addr port } }
  tcp6 { state uid inode local_address { addr port } remote_address { addr port } }
}`;

// One watched process, deep: fds (with paths), io counters, full status.
export const procDetail = (pid) => `
{
  proc(pid: ${Number(pid)}) {
    pid uid exe cwd cmdline fd_count
    status { name ppid vmrss threads seccomp capeff }
    io { read_bytes write_bytes syscr syscw }
    fds { fd kind path inode name }
  }
}`;
