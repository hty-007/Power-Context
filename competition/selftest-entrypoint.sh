#!/bin/sh
set -eu

# The embedded engine requires a non-loopback address even when offline.
ip link add pcselftest0 type dummy
ip address add 198.18.0.1/32 dev pcselftest0
ip link set pcselftest0 up
exec setpriv --reuid 10001 --regid 10001 --clear-groups "$@"
