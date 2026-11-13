#include "vmlinux.h"

#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "GPL";

#define COMM_LEN 16
#define PATH_LEN 128
#define ARGV_LEN 256
#define AF_INET6 10
#define AT_FDCWD -100
#define O_WRONLY 01
#define O_CREAT 0100
#define O_TRUNC 01000

enum stream { EXECS, OPENS, CONNECTS, CHDIRS, STREAMS };

struct exec_event {
  __u64 ts;
  __u32 pid;
  __u32 ppid;
  __u32 uid;
  __u32 dev;
  __u64 ino;
  __u16 argv_len;
  __u8 comm[COMM_LEN];
  __u8 filename[PATH_LEN];
  __u8 argv[ARGV_LEN];
};

struct open_event {
  __u64 ts;
  __u32 pid;
  __u32 uid;
  __u32 flags;
  __s32 dfd;
  __u8 comm[COMM_LEN];
  __u8 path[PATH_LEN];
};

struct connect_event {
  __u64 ts;
  __u32 pid;
  __u32 uid;
  __u16 family;
  __u16 dport;
  __u8 daddr[16];
  __u8 comm[COMM_LEN];
};

struct chdir_event {
  __u64 ts;
  __u32 pid;
  __u8 path[PATH_LEN];
};

struct exec_event *_exec_event_btf __attribute__((unused));
struct open_event *_open_event_btf __attribute__((unused));
struct connect_event *_connect_event_btf __attribute__((unused));
struct chdir_event *_chdir_event_btf __attribute__((unused));

struct {
  __uint(type, BPF_MAP_TYPE_RINGBUF);
  __uint(max_entries, 512 * 1024);
} execs SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_RINGBUF);
  __uint(max_entries, 2 * 1024 * 1024);
} opens SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_RINGBUF);
  __uint(max_entries, 256 * 1024);
} connects SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_RINGBUF);
  __uint(max_entries, 64 * 1024);
} chdirs SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_ARRAY);
  __uint(max_entries, STREAMS);
  __type(key, __u32);
  __type(value, __u64);
} lost SEC(".maps");

static __always_inline void count_lost(__u32 stream) {
  __u64 *n = bpf_map_lookup_elem(&lost, &stream);
  if (n) {
    __sync_fetch_and_add(n, 1);
  }
}

static __always_inline void identify(__u64 *ts, __u32 *pid, __u32 *uid, __u8 comm[COMM_LEN]) {
  *ts = bpf_ktime_get_ns();
  *pid = bpf_get_current_pid_tgid() >> 32;
  *uid = bpf_get_current_uid_gid();
  bpf_get_current_comm(comm, COMM_LEN);
}

SEC("tracepoint/sched/sched_process_exec")
int on_exec(struct trace_event_raw_sched_process_exec *ctx) {
  struct exec_event *event = bpf_ringbuf_reserve(&execs, sizeof(*event), 0);
  if (!event) {
    count_lost(EXECS);
    return 0;
  }

  struct task_struct *task = (struct task_struct *)bpf_get_current_task();
  struct mm_struct *mm = BPF_CORE_READ(task, mm);
  struct inode *exe = BPF_CORE_READ(mm, exe_file, f_inode);
  identify(&event->ts, &event->pid, &event->uid, event->comm);
  event->ppid = BPF_CORE_READ(task, real_parent, tgid);
  event->ino = BPF_CORE_READ(exe, i_ino);
  event->dev = BPF_CORE_READ(exe, i_sb, s_dev);

  unsigned int offset = ctx->__data_loc_filename & 0xffff;
  bpf_probe_read_kernel_str(event->filename, sizeof(event->filename), (void *)ctx + offset);

  unsigned long start = BPF_CORE_READ(mm, arg_start);
  unsigned long end = BPF_CORE_READ(mm, arg_end);
  __u32 len = end > start ? end - start : 0;
  if (len > ARGV_LEN) {
    len = ARGV_LEN;
  }
  asm volatile("" : "+r"(len));
  event->argv_len = len;
  if (len > 0 && bpf_probe_read_user(event->argv, len, (void *)start) < 0) {
    event->argv_len = 0;
  }

  bpf_ringbuf_submit(event, 0);
  return 0;
}

static __always_inline int record_open(__s32 dfd, const char *path, __u32 flags) {
  struct open_event *event = bpf_ringbuf_reserve(&opens, sizeof(*event), 0);
  if (!event) {
    count_lost(OPENS);
    return 0;
  }

  identify(&event->ts, &event->pid, &event->uid, event->comm);
  event->dfd = dfd;
  event->flags = flags;
  bpf_probe_read_user_str(event->path, sizeof(event->path), path);

  bpf_ringbuf_submit(event, 0);
  return 0;
}

SEC("tracepoint/syscalls/sys_enter_open")
int on_open(struct trace_event_raw_sys_enter *ctx) {
  return record_open(AT_FDCWD, (const char *)ctx->args[0], ctx->args[1]);
}

SEC("tracepoint/syscalls/sys_enter_openat")
int on_openat(struct trace_event_raw_sys_enter *ctx) {
  return record_open(ctx->args[0], (const char *)ctx->args[1], ctx->args[2]);
}

SEC("tracepoint/syscalls/sys_enter_openat2")
int on_openat2(struct trace_event_raw_sys_enter *ctx) {
  __u64 flags = 0;
  bpf_probe_read_user(&flags, sizeof(flags), (void *)ctx->args[2]);
  return record_open(ctx->args[0], (const char *)ctx->args[1], flags);
}

SEC("tracepoint/syscalls/sys_enter_creat")
int on_creat(struct trace_event_raw_sys_enter *ctx) {
  return record_open(AT_FDCWD, (const char *)ctx->args[0], O_CREAT | O_WRONLY | O_TRUNC);
}

SEC("tracepoint/syscalls/sys_enter_chdir")
int on_chdir(struct trace_event_raw_sys_enter *ctx) {
  struct chdir_event *event = bpf_ringbuf_reserve(&chdirs, sizeof(*event), 0);
  if (!event) {
    count_lost(CHDIRS);
    return 0;
  }

  event->ts = bpf_ktime_get_ns();
  event->pid = bpf_get_current_pid_tgid() >> 32;
  bpf_probe_read_user_str(event->path, sizeof(event->path), (const char *)ctx->args[0]);

  bpf_ringbuf_submit(event, 0);
  return 0;
}

SEC("tracepoint/sock/inet_sock_set_state")
int on_sock_state(struct trace_event_raw_inet_sock_set_state *ctx) {
  if (ctx->protocol != IPPROTO_TCP || ctx->newstate != TCP_SYN_SENT) {
    return 0;
  }

  struct connect_event *event = bpf_ringbuf_reserve(&connects, sizeof(*event), 0);
  if (!event) {
    count_lost(CONNECTS);
    return 0;
  }

  identify(&event->ts, &event->pid, &event->uid, event->comm);
  event->family = ctx->family;
  event->dport = ctx->dport;
  __builtin_memset(event->daddr, 0, sizeof(event->daddr));
  if (ctx->family == AF_INET6) {
    bpf_probe_read_kernel(event->daddr, 16, ctx->daddr_v6);
  } else {
    bpf_probe_read_kernel(event->daddr, 4, ctx->daddr);
  }

  bpf_ringbuf_submit(event, 0);
  return 0;
}
