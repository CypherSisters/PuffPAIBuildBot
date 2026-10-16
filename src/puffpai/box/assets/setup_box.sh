#!/bin/sh
# Provision the PuffPAI locked box inside a freshly imported Alpine distro.
# Run by puffpai.box.driver as:
#   wsl.exe -d <box> -u root --exec /bin/sh /tmp/boxsrc/setup_box.sh
# with every file of puffpai/box/assets (and the generated deny list and
# marker) already streamed into /tmp/boxsrc. Ported from the proven prototype.
set -eu
SRC=/tmp/boxsrc

# NEVER run while a Windows drive is mounted. BuildBot writes /etc/wsl.conf
# (automount and interop off) and restarts the box BEFORE this script runs, so
# only WSL's own /mnt/wsl and /mnt/wslg may be mounted under /mnt. Anything
# else under /mnt (for example C: at /mnt/c) stops the setup here, before any
# command runs.
if awk '$2 ~ "^/mnt/" && $2 !~ "^/mnt/wsl" { found = 1 } END { exit !found }' /proc/mounts; then
  echo "a Windows drive is mounted; setup stopped" >&2
  exit 3
fi

# Alpine's own signed package lists, over https only (the release keys come
# from the pinned minirootfs, whose sha256 BuildBot checked before import).
printf 'https://dl-cdn.alpinelinux.org/alpine/v3.24/main\nhttps://dl-cdn.alpinelinux.org/alpine/v3.24/community\n' > /etc/apk/repositories
apk add --no-cache nodejs npm python3 bubblewrap socat util-linux-misc setpriv e2fsprogs tar curl >/dev/null
# Only to build the seccomp filter below; removed again right after.
apk add --no-cache py3-libseccomp >/dev/null

getent group runner >/dev/null || addgroup -g 61000 runner
getent passwd runner >/dev/null || adduser -D -u 61000 -G runner -s /sbin/nologin -h /home/runner runner
getent group pproxy >/dev/null || addgroup -g 61001 pproxy
getent passwd pproxy >/dev/null || adduser -D -u 61001 -G pproxy -s /sbin/nologin -h /var/lib/pproxy pproxy
passwd -l runner >/dev/null 2>&1 || true
passwd -l pproxy >/dev/null 2>&1 || true
passwd -l root >/dev/null 2>&1 || true

install -d -m 755 -o root -g root /opt/puffpai /opt/puffpai/etc /etc/puffpai
install -m 755 "$SRC/box_run.py" /opt/puffpai/box-run
install -m 644 "$SRC/jail.py" /opt/puffpai/jail.py
install -m 755 "$SRC/box-entry" /opt/puffpai/box-entry
install -m 644 "$SRC/egress_proxy.py" /opt/puffpai/egress_proxy.py
install -m 644 "$SRC/deny_windows.json" /etc/puffpai/deny_windows.json
printf 'root:x:0:0::/nonexistent:/sbin/nologin\nrunner:x:61000:61000::/work/.home:/bin/sh\nnobody:x:65534:65534::/nonexistent:/sbin/nologin\n' > /opt/puffpai/etc/passwd
printf 'root:x:0:\nrunner:x:61000:\nnobody:x:65534:\n' > /opt/puffpai/etc/group
printf '127.0.0.1 localhost box\n::1 localhost\n' > /opt/puffpai/etc/hosts

python3 -I "$SRC/make_seccomp.py" /opt/puffpai/box.bpf >/dev/null
chmod 644 /opt/puffpai/box.bpf
apk del --no-cache py3-libseccomp >/dev/null 2>&1 || true

install -d -m 755 /srv/box /srv/box/projects /srv/box/owner
install -d -m 700 /srv/box/images
install -m 644 "$SRC/wsl.conf" /etc/wsl.conf
# The owner marker was written before this script ran; keep it root-owned.
chown root:root /etc/puffpai-box.json && chmod 644 /etc/puffpai-box.json
# An empty leftover folder only: rmdir never deletes anything inside a folder.
rmdir /mnt/c 2>/dev/null || true
# No setuid programs may remain (proof 03).
find / -xdev -perm -4000 -type f -exec chmod u-s {} + 2>/dev/null || true
rm -rf "$SRC"
echo provisioned
