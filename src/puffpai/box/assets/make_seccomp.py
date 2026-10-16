#!/usr/bin/python3 -I
"""Build /opt/puffpai/box.bpf: a deny-list seccomp filter for bwrap --seccomp.

Default ALLOW (npm/node/python need a wide syscall surface); the calls below
return EPERM. They are kernel attack surface or cross-process reach that no
build or web app needs. Run once at provisioning time, as root.
"""
import errno
import sys

import seccomp

DENY = [
    "ptrace", "process_vm_readv", "process_vm_writev", "bpf", "perf_event_open",
    "userfaultfd", "keyctl", "add_key", "request_key", "io_uring_setup",
    "io_uring_enter", "io_uring_register", "kexec_load", "kexec_file_load",
    "init_module", "finit_module", "delete_module", "swapon", "swapoff",
    "reboot", "acct", "quotactl", "syslog", "iopl", "ioperm", "pivot_root",
    "open_by_handle_at", "name_to_handle_at", "mount", "umount2", "fsopen",
    "fsmount", "fsconfig", "move_mount", "open_tree", "mount_setattr",
    "setns", "unshare", "personality", "vhangup", "lookup_dcookie",
    "fanotify_init", "kcmp", "pidfd_getfd",
]

f = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
for name in DENY:
    try:
        f.add_rule(seccomp.ERRNO(errno.EPERM), name)
    except Exception as exc:  # syscall absent on this arch
        print(f"skip {name}: {exc}", file=sys.stderr)
# TIOCSTI (push characters into the terminal input) belt-and-braces with --new-session
f.add_rule(seccomp.ERRNO(errno.EPERM), "ioctl", seccomp.Arg(1, seccomp.MASKED_EQ, 0xFFFFFFFF, 0x5412))
# clone with CLONE_NEWUSER (0x10000000) - no nested user namespaces
f.add_rule(seccomp.ERRNO(errno.EPERM), "clone", seccomp.Arg(0, seccomp.MASKED_EQ, 0x10000000, 0x10000000))
# clone3 passes flags in a struct seccomp cannot read: refuse it with ENOSYS so
# libc falls back to clone(), which the rule above can inspect.
f.add_rule(seccomp.ERRNO(errno.ENOSYS), "clone3")
with open(sys.argv[1], "wb") as out:
    f.export_bpf(out)
print("ok", len(DENY) + 3, "rules")
