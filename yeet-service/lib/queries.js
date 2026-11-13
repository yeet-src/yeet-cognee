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

export const PROCS = `
{
  procs {
    pid uid exe cwd cmdline fd_count
    status { name ppid vmrss threads }
  }
}`;

export const TCP = `
{
  tcp  { state uid inode local_address { addr port } remote_address { addr port } }
  tcp6 { state uid inode local_address { addr port } remote_address { addr port } }
}`;

export const procDetail = (pid) => `
{
  proc(pid: ${Number(pid)}) {
    pid uid exe cwd cmdline fd_count
    status { name ppid vmrss threads seccomp capeff }
    io { read_bytes write_bytes syscr syscw }
    fds { fd kind path inode name }
  }
}`;
