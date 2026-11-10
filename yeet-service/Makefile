# Builds the activity log's BPF object. The object is CO-RE and checked in,
# so running the service needs neither clang nor bpftool; rebuild after
# editing bpf/activity.bpf.c.
.PHONY: all clean
.DELETE_ON_ERROR:

ARCH    ?= $(shell uname -m | sed 's/x86_64/x86/; s/aarch64/arm64/')
CLANG   ?= clang
BPFTOOL ?= $(shell command -v bpftool 2>/dev/null || echo /usr/sbin/bpftool)

all: activity.bpf.o

bpf/include/vmlinux.h:
	@mkdir -p bpf/include
	$(BPFTOOL) btf dump file /sys/kernel/btf/vmlinux format c > $@

activity.bpf.o: bpf/activity.bpf.c bpf/include/vmlinux.h
	$(CLANG) -O2 -g -Wall -target bpf -D__TARGET_ARCH_$(ARCH) -Ibpf/include -c $< -o $@

clean:
	rm -f activity.bpf.o bpf/include/vmlinux.h
