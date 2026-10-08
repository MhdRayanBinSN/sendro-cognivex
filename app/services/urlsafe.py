"""URL normalization and public-network SSRF checks."""

import asyncio
import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe to fetch from the worker."""


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
_DOMAIN_EXTRACTOR = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())


def registered_domain(host: str) -> str:
    """Return the registrable domain without triggering a suffix-list download."""
    host = host.strip("[]").lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        result = _DOMAIN_EXTRACTOR(host)
        return f"{result.domain}.{result.suffix}" if result.suffix else host


def canonicalize_url(url: str) -> str:
    """Normalize an HTTP URL and remove common tracking parameters."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeURLError("Only absolute http and https URLs are accepted")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs containing credentials are not accepted")
    host = parts.hostname.lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


async def validate_public_url(url: str) -> str:
    """Canonicalize and reject hostnames resolving to non-public IP addresses."""
    normalized = canonicalize_url(url)
    host = urlsplit(normalized).hostname
    assert host is not None
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            records = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"Hostname did not resolve: {host}") from exc
        addresses = [ipaddress.ip_address(item[4][0]) for item in records]
    if not addresses:
        raise UnsafeURLError(f"Hostname did not resolve: {host}")
    if any(not address.is_global for address in addresses):
        raise UnsafeURLError(f"URL resolves to a non-public address: {host}")
    return normalized
