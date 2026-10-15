#!/bin/bash
# Outbound traffic allowed: DNS, loopback, and HTTPS to Anthropic. Nothing else.
#
# A much-simplified take on the reference devcontainer firewall in the Claude
# Code repo (.devcontainer/init-firewall.sh). Needs --cap-add NET_ADMIN NET_RAW.
# Why: the agent runs with permission prompts off, so the container must not be
# able to send anything (code, the auth token) anywhere but the model API.
set -euo pipefail

ALLOWED_DOMAINS=(api.anthropic.com claude.ai platform.claude.com)

iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

for domain in "${ALLOWED_DOMAINS[@]}"; do
  ips=$(dig +short A "$domain" | grep -E '^[0-9]+(\.[0-9]+){3}$' || true)
  if [ -z "$ips" ]; then
    echo "firewall: could not resolve $domain" >&2
    exit 1
  fi
  first=""
  for ip in $ips; do
    iptables -A OUTPUT -p tcp -d "$ip" --dport 443 -j ACCEPT
    if [ -z "$first" ]; then first="$ip"; fi
  done
  # Pin the name to an allowed address so a later lookup can't return a blocked one.
  echo "$first $domain" >> /etc/hosts
done

iptables -P OUTPUT DROP
ip6tables -P OUTPUT DROP 2>/dev/null || true

# Verify both directions before any trial starts.
if curl -sS --max-time 5 -o /dev/null https://example.com 2>/dev/null; then
  echo "firewall: example.com is reachable; refusing to start" >&2
  exit 1
fi
if ! curl -sS --max-time 10 -o /dev/null https://api.anthropic.com 2>/dev/null; then
  echo "firewall: api.anthropic.com is not reachable" >&2
  exit 1
fi
echo "firewall: ready (only Anthropic is reachable)"
