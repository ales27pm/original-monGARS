# Public-search egress policy

SearXNG has no direct route to the host or public network. Its HTTP clients use
the `search-egress-proxy` Squid service on an internal network. Only Squid joins
the ordinary bridge network with outbound connectivity.

`squid.conf` resolves each destination and denies loopback, private, shared,
link-local, multicast, reserved, documentation, benchmark, Docker-host, and
cloud-metadata address ranges before opening the upstream connection. Only
ports 80 and 443 are allowed, and `CONNECT` is restricted to port 443. This
design prevents a compromised search engine or DNS-rebinding response inside
SearXNG from directly reaching LAN or host services. The proxy itself remains a
trusted security boundary and should stay pinned, read-only, non-root, and
capability-free.

Run the focused policy check with:

```bash
deploy/egress-proxy/check.sh
```

It validates the Squid configuration, proves representative private and
metadata destinations return `403`, and proves a public HTTPS destination can
still be reached.
