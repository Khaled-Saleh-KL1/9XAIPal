# SSRF check has a DNS rebinding window

**Severity:** High  
**Confidence:** Confirmed design-level TOCTOU  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Outbound HTTP transports now resolve and validate the destination at connection time, then pin the TCP socket to that vetted address while preserving the hostname for Host and TLS SNI.

## What happens

An attacker controlling DNS can potentially pass the private-address check
with a public answer and then make the actual HTTP connection resolve to a
loopback, link-local, or private address.

## Why it happens

backend/app/core/net_safety.py:168-175 and 204-210 first call
resolves_to_private_address, which resolves the hostname with getaddrinfo.
They then give the original hostname to HTTPX. HTTPX performs its own DNS
resolution when connecting.

Those lookups are not tied together. A DNS record with changing/short-lived
answers can return a safe IP to validation and an unsafe IP to the connection.
Repeating the same pattern for redirect hops protects against obvious private
redirect targets but does not close this time-of-check/time-of-use gap.

## Root cause

The security decision validates a DNS result, while the network transport
connects using a separately resolved result.

## Impact

Untrusted article and image URLs may reach services on localhost, cloud
metadata addresses, or the private network despite the SSRF guard.

## Suggested fix

Resolve once, reject if any candidate is unsafe, and make the transport connect
to a selected validated IP while preserving the original Host header and TLS
SNI/certificate validation. A network egress policy blocking private ranges is
a strong additional control. Add a rebinding-style resolver/transport test
that changes answers between lookup attempts.
