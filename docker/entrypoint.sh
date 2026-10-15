#!/bin/bash
# Install the egress firewall (unless EGRESS=open), signal readiness, then run CMD.
set -euo pipefail

if [ "${EGRESS:-anthropic}" = "anthropic" ]; then
  /usr/local/bin/init-firewall.sh
fi

touch /tmp/ready
exec "$@"
