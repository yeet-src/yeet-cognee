SENSITIVE_PREFIXES = (
    "/var/run/docker.sock", "/run/docker.sock", "/var/run/containerd", "/run/containerd",
    "/dev/kmsg", "/dev/mem", "/dev/kvm", "/sys/kernel", "/etc/shadow", "/etc/sudoers",
    "/root/", "/proc/1/", "/proc/mounts", "/proc/sys/kernel",
)
SENSITIVE_SUFFIXES = ("/environ", "/mem", "/maps", "id_rsa", "id_ed25519", ".kube/config", ".aws/credentials")
PROGRAM_DIRS = ("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/", "/usr/local/bin/", "/usr/local/sbin/", "/opt/")
KNOWN_HOSTS = ("api.github.com", "api.anthropic.com", "github.com")

# Binaries a watched agent may hold but must not overwrite: a deputy allowed to
# read a secret is only trustworthy while its code is pinned.
PROTECTED_EXECUTABLES: tuple[str, ...] = ()

# Block a watched agent writing to any executable, and reading another
# process's memory (ptrace / process_vm_readv) to lift a deputy's secrets.
PROTECT_EXEC_WRITES = False
PROTECT_PTRACE = True


def is_sensitive(path: str) -> bool:
    return path.startswith(SENSITIVE_PREFIXES) or path.endswith(SENSITIVE_SUFFIXES)


def in_program_dir(path: str) -> bool:
    return path.startswith(PROGRAM_DIRS)
