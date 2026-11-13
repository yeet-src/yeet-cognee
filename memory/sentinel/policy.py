SENSITIVE_PREFIXES = (
    "/var/run/docker.sock", "/run/docker.sock", "/var/run/containerd", "/run/containerd",
    "/dev/kmsg", "/dev/mem", "/dev/kvm", "/sys/kernel", "/etc/shadow", "/etc/sudoers",
    "/root/", "/proc/1/", "/proc/mounts", "/proc/sys/kernel",
)
SENSITIVE_SUFFIXES = ("/environ", "/mem", "/maps", "id_rsa", "id_ed25519", ".kube/config", ".aws/credentials")
PROGRAM_DIRS = ("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/", "/usr/local/bin/", "/usr/local/sbin/", "/opt/")
KNOWN_HOSTS = ("api.github.com", "api.anthropic.com", "github.com")


def is_sensitive(path: str) -> bool:
    return path.startswith(SENSITIVE_PREFIXES) or path.endswith(SENSITIVE_SUFFIXES)


def in_program_dir(path: str) -> bool:
    return path.startswith(PROGRAM_DIRS)
