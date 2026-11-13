/* Enforcement via LSM hooks. Each hook sees the object the kernel actually
 * resolved, so matching an inode defeats symlinks, hard links, relative paths
 * and bind mounts, and matching a basename defeats a `cd` into the directory.
 * A match returns -EPERM. Every guard runs only after a cheap watched-pid
 * check, so unwatched processes pay nothing.
 *
 * The guards:
 *   file_open          deny reading a protected file (inode / basename / an
 *                      ancestor directory name), and deny writing to a
 *                      tamper-protected file or, optionally, any executable.
 *   bprm_check_security deny executing a forbidden binary.
 *   ptrace_access_check deny reading another process's memory.
 */
#ifndef ENFORCE_H
#define ENFORCE_H

#include "vmlinux.h"
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>

#define EPERM 1
#define NAME_CAP 64
#define BASE_MAX 128
#define FMODE_WRITE 0x2
#define S_IXUGO 0111
#define ANCESTOR_MAX 16

enum base_kind { BASE_NONE, BASE_EXACT, BASE_PREFIX, BASE_SUFFIX };

/* Concrete objects to deny, by (device, inode). The sentinel stat()s each
 * sensitive path and writes its key into the right set:
 *   deny_inode  may not be opened at all (secrets)
 *   deny_write  may not be written (tamper-protected binaries, policy files)
 *   deny_exec   may not be executed (forbidden programs)
 * Matching the inode holds even when the object is reached by another name. */
struct inode_key {
  __u64 ino;
  __u32 dev;
} __attribute__((packed));
#define INODE_SET(name)                     \
  struct {                                  \
    __uint(type, BPF_MAP_TYPE_HASH);        \
    __uint(max_entries, 4096);              \
    __type(key, struct inode_key);          \
    __type(value, __u8);                    \
  } name SEC(".maps")
INODE_SET(deny_inode);
INODE_SET(deny_write);
INODE_SET(deny_exec);

/* Basename patterns (no directory component): matched against the opened
 * file's final path component. [kind, len, text]. Index 0's len holds the
 * live count; entries start at 1. */
struct base_entry {
  __u8 kind;
  __u8 len;
  char text[NAME_CAP];
};
struct base_entry *_base_entry_btf __attribute__((unused));
#define BASE_SET(name)                      \
  struct {                                  \
    __uint(type, BPF_MAP_TYPE_ARRAY);       \
    __uint(max_entries, BASE_MAX);          \
    __type(key, __u32);                     \
    __type(value, struct base_entry);       \
  } name SEC(".maps")

/* Final-component patterns, matched against the opened file's own name. */
BASE_SET(deny_base);

/* Directory-name patterns, matched against every resolved ancestor of the
 * opened file: a rule for a directory anywhere in the path becomes "an
 * ancestor directory with this name". This is the bounded dentry walk — up to
 * ANCESTOR_MAX parents, no backward jump — that lets a path rule enforce
 * instead of staying advisory. */
BASE_SET(dir_component);

struct {
  __uint(type, BPF_MAP_TYPE_HASH);
  __uint(max_entries, 4096);
  __type(key, __u32);
  __type(value, __u8);
} watched SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_ARRAY);
  __uint(max_entries, 8);
  __type(key, __u32);
  __type(value, __u64);
} enforce SEC(".maps");

#define ENFORCE_ON 0
#define ENFORCE_DENIED 1
#define PROTECT_EXEC 2
#define PROTECT_PTRACE 3

struct {
  __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
  __uint(max_entries, 1);
  __type(key, __u32);
  __type(value, char[NAME_CAP]);
} namebuf SEC(".maps");

static __always_inline __u8 is_watched(__u32 tgid) {
  return bpf_map_lookup_elem(&watched, &tgid) != 0;
}

static __always_inline void watch_child(__u32 ppid, __u32 tgid) {
  if (is_watched(ppid)) {
    __u8 one = 1;
    bpf_map_update_elem(&watched, &tgid, &one, BPF_ANY);
  }
}

static __always_inline int enforcing(void) {
  __u32 k = ENFORCE_ON;
  __u64 *on = bpf_map_lookup_elem(&enforce, &k);
  return on && *on;
}

static __always_inline int flag_set(__u32 index) {
  __u64 *v = bpf_map_lookup_elem(&enforce, &index);
  return v && *v;
}

static __always_inline int guarding(void) {
  return enforcing() && is_watched(bpf_get_current_pid_tgid() >> 32);
}

static __always_inline void bump_denied(void) {
  __u32 k = ENFORCE_DENIED;
  __u64 *n = bpf_map_lookup_elem(&enforce, &k);
  if (n) {
    __sync_fetch_and_add(n, 1);
  }
}

/* text[0..len) equals name[start..start+len). Each index is checked and
 * masked so the verifier admits the variable offset. */
static __always_inline int name_span_eq(const char *name, int nlen, int start,
                                         const char *text, int len) {
  if (start < 0 || start + len > nlen) {
    return 0;
  }
  for (int i = 0; i < NAME_CAP; i++) {
    if (i >= len) {
      return 1;
    }
    int at = start + i;
    if (at < 0 || at >= NAME_CAP) {
      return 0;
    }
    if (name[at & (NAME_CAP - 1)] != text[i & (NAME_CAP - 1)]) {
      return 0;
    }
  }
  return 1;
}

struct base_ctx {
  const char *name;
  int nlen;
  int hit;
};

static long base_one(__u32 i, void *ctx_) {
  struct base_ctx *ctx = ctx_;
  __u32 key = i + 1;
  struct base_entry *e = bpf_map_lookup_elem(&deny_base, &key);
  if (!e) {
    return 0;
  }
  int len = e->len;
  if (len <= 0 || len > NAME_CAP) {
    return 0;
  }
  int hit = 0;
  if (e->kind == BASE_EXACT) {
    hit = len == ctx->nlen && name_span_eq(ctx->name, ctx->nlen, 0, e->text, len);
  } else if (e->kind == BASE_PREFIX) {
    hit = name_span_eq(ctx->name, ctx->nlen, 0, e->text, len);
  } else if (e->kind == BASE_SUFFIX) {
    hit = name_span_eq(ctx->name, ctx->nlen, ctx->nlen - len, e->text, len);
  }
  if (hit) {
    ctx->hit = 1;
    return 1;
  }
  return 0;
}

static __always_inline int basename_denied(const char *name, int nlen) {
  __u32 zero = 0;
  struct base_entry *head = bpf_map_lookup_elem(&deny_base, &zero);
  if (!head) {
    return 0;
  }
  __u32 count = head->len;
  if (count >= BASE_MAX) {
    count = BASE_MAX - 1;
  }
  struct base_ctx ctx = { .name = name, .nlen = nlen, .hit = 0 };
  bpf_loop(count, base_one, &ctx, 0);
  return ctx.hit;
}

static __always_inline struct inode_key inode_key_of(struct inode *inode) {
  struct inode_key key = {
    .ino = BPF_CORE_READ(inode, i_ino),
    .dev = BPF_CORE_READ(inode, i_sb, s_dev),
  };
  return key;
}

static __always_inline int inode_denied(struct file *file) {
  struct inode_key key = inode_key_of(BPF_CORE_READ(file, f_inode));
  return bpf_map_lookup_elem(&deny_inode, &key) != 0;
}

static __always_inline int write_denied(struct file *file) {
  if (!(BPF_CORE_READ(file, f_mode) & FMODE_WRITE)) {
    return 0;
  }
  struct inode *inode = BPF_CORE_READ(file, f_inode);
  struct inode_key key = inode_key_of(inode);
  if (bpf_map_lookup_elem(&deny_write, &key)) {
    return 1;
  }
  return flag_set(PROTECT_EXEC) && (BPF_CORE_READ(inode, i_mode) & S_IXUGO);
}

static __always_inline int exec_denied(struct file *file) {
  struct inode_key key = inode_key_of(BPF_CORE_READ(file, f_inode));
  return bpf_map_lookup_elem(&deny_exec, &key) != 0;
}

static __always_inline int name_denied(struct file *file) {
  const unsigned char *raw = BPF_CORE_READ(file, f_path.dentry, d_name.name);
  if (!raw) {
    return 0;
  }
  __u32 zero = 0;
  char *name = bpf_map_lookup_elem(&namebuf, &zero);
  if (!name) {
    return 0;
  }
  long n = bpf_probe_read_kernel_str(name, NAME_CAP, raw);
  if (n <= 1 || n > NAME_CAP) {
    return 0;
  }
  return basename_denied(name, n - 1);
}

#define DIR_MAX 16

static __always_inline int dir_component_eq(const char *name, int nlen) {
#pragma unroll
  for (__u32 i = 1; i <= DIR_MAX; i++) {
    struct base_entry *e = bpf_map_lookup_elem(&dir_component, &i);
    if (!e) {
      continue;
    }
    int len = e->len;
    if (len > 0 && len == nlen && len <= NAME_CAP && name_span_eq(name, nlen, 0, e->text, len)) {
      return 1;
    }
  }
  return 0;
}

struct ancestor_ctx {
  struct dentry *dentry;
  int hit;
};

static long ancestor_step(__u32 i, void *ctx_) {
  struct ancestor_ctx *ctx = ctx_;
  struct dentry *current = ctx->dentry;
  struct dentry *parent = BPF_CORE_READ(current, d_parent);
  if (!parent || parent == current) {
    return 1;
  }
  ctx->dentry = parent;

  __u32 zero = 0;
  char *name = bpf_map_lookup_elem(&namebuf, &zero);
  if (!name) {
    return 1;
  }
  long n = bpf_probe_read_kernel_str(name, NAME_CAP, BPF_CORE_READ(parent, d_name.name));
  if (n > 1 && n <= NAME_CAP && dir_component_eq(name, n - 1)) {
    ctx->hit = 1;
    return 1;
  }
  return 0;
}

static __always_inline int dir_denied(struct file *file) {
  struct base_entry *head = bpf_map_lookup_elem(&dir_component, &(__u32){0});
  if (!head || head->len == 0) {
    return 0;
  }
  struct ancestor_ctx ctx = { .dentry = BPF_CORE_READ(file, f_path.dentry), .hit = 0 };
  bpf_loop(ANCESTOR_MAX, ancestor_step, &ctx, 0);
  return ctx.hit;
}

#endif
