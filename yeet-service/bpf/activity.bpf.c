/* The activity log: what a process did between two readings.
 *
 * A reading is a snapshot of open descriptors and sockets, so a file read
 * and closed in a millisecond never appears in one. These three programs
 * record the actions themselves as they happen -- every exec, every
 * openat() and every outbound TCP connect on the host -- into ring buffers
 * the isolate drains. Nothing is filtered here; the isolate keeps the
 * events for the processes it is asked about.
 */
#include "vmlinux.h"

#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "GPL";

#define COMM_LEN 16
#define PATH_LEN 128
#define AF_INET 2
#define AF_INET6 10

struct exec_event {
  __u32 pid;
  __u32 ppid;
  __u32 uid;
  __u8 comm[COMM_LEN];
  __u8 filename[PATH_LEN];
};

struct open_event {
  __u32 pid;
  __u32 uid;
  __u32 flags;
  __u8 comm[COMM_LEN];
  __u8 path[PATH_LEN];
};

struct connect_event {
  __u32 pid;
  __u32 uid;
  __u16 family;
  __u16 dport;
  __u8 daddr[16]; /* v4 in the first 4 bytes */
  __u8 comm[COMM_LEN];
};

/* Force the payload structs into BTF so the isolate can decode by name. */
struct exec_event *_unused_exec_event __attribute__((unused));
struct open_event *_unused_open_event __attribute__((unused));
struct connect_event *_unused_connect_event __attribute__((unused));

struct {
  __uint(type, BPF_MAP_TYPE_RINGBUF);
  __uint(max_entries, 256 * 1024);
} execs SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_RINGBUF);
  __uint(max_entries, 1024 * 1024);
} opens SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_RINGBUF);
  __uint(max_entries, 256 * 1024);
} connects SEC(".maps");

SEC("tracepoint/sched/sched_process_exec")
int on_exec(struct trace_event_raw_sched_process_exec *ctx) {
  struct exec_event *event = bpf_ringbuf_reserve(&execs, sizeof(*event), 0);
  if (!event) {
    return 0;
  }

  struct task_struct *task = (struct task_struct *)bpf_get_current_task();
  event->pid = bpf_get_current_pid_tgid() >> 32;
  event->ppid = BPF_CORE_READ(task, real_parent, tgid);
  event->uid = bpf_get_current_uid_gid() & 0xffffffff;
  bpf_get_current_comm(&event->comm, sizeof(event->comm));

  /* The path is appended to the record; its offset is the low 16 bits. */
  unsigned int off = ctx->__data_loc_filename & 0xffff;
  bpf_probe_read_kernel_str(&event->filename, sizeof(event->filename), (void *)ctx + off);

  bpf_ringbuf_submit(event, 0);
  return 0;
}

/* openat(dfd, filename, flags, mode): the path is a user pointer in args[1]. */
SEC("tracepoint/syscalls/sys_enter_openat")
int on_openat(struct trace_event_raw_sys_enter *ctx) {
  struct open_event *event = bpf_ringbuf_reserve(&opens, sizeof(*event), 0);
  if (!event) {
    return 0;
  }

  event->pid = bpf_get_current_pid_tgid() >> 32;
  event->uid = bpf_get_current_uid_gid() & 0xffffffff;
  event->flags = (__u32)ctx->args[2];
  bpf_get_current_comm(&event->comm, sizeof(event->comm));
  bpf_probe_read_user_str(&event->path, sizeof(event->path), (const void *)ctx->args[1]);

  bpf_ringbuf_submit(event, 0);
  return 0;
}

/* The move into SYN_SENT is a connect() leaving the box, and it runs in
 * the caller's context, so the pid on the CPU is the connecting process. */
SEC("tracepoint/sock/inet_sock_set_state")
int on_sock_state(struct trace_event_raw_inet_sock_set_state *ctx) {
  if (ctx->protocol != IPPROTO_TCP || ctx->newstate != TCP_SYN_SENT) {
    return 0;
  }

  struct connect_event *event = bpf_ringbuf_reserve(&connects, sizeof(*event), 0);
  if (!event) {
    return 0;
  }

  event->pid = bpf_get_current_pid_tgid() >> 32;
  event->uid = bpf_get_current_uid_gid() & 0xffffffff;
  event->family = ctx->family;
  event->dport = ctx->dport;
  __builtin_memset(event->daddr, 0, sizeof(event->daddr));
  /* Read the address through a helper: a memcpy from the context compiles
   * to a modified context pointer, which the verifier rejects. */
  if (ctx->family == AF_INET6) {
    bpf_probe_read_kernel(event->daddr, 16, ctx->daddr_v6);
  } else {
    bpf_probe_read_kernel(event->daddr, 4, ctx->daddr);
  }
  bpf_get_current_comm(&event->comm, sizeof(event->comm));

  bpf_ringbuf_submit(event, 0);
  return 0;
}
