#!/usr/bin/env python3
"""Clash-routed web search implementation and shared HTTP helpers."""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import ctypes.wintypes
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import trafilatura

# DDGS is not thread-safe; serialize all DDGS calls through this lock.
_ddgs_lock = threading.Lock()
from bs4 import BeautifulSoup, FeatureNotFound

try:
    from ddgs import DDGS
except ImportError:  # Google can use the HTML adapter until dependencies are installed.
    DDGS = None  # type: ignore[assignment,misc]

try:
    from ddgs.engines import ENGINES as DDGS_ENGINES
except ImportError:
    DDGS_ENGINES = None


def force_enable_ddgs_bing() -> bool:
    """Re-register DDGS's text Bing engine even when the package disables it."""

    global DDGS_ENGINES

    if DDGS is None:
        return False
    try:
        import ddgs.engines as engine_registry
        from ddgs.engines.bing import Bing
    except (ImportError, AttributeError):
        return False

    # Recent DDGS releases keep Bing in the source tree but mark the text
    # engine disabled. Put it back into the runtime registry deliberately.
    Bing.disabled = False
    engine_registry.ENGINES.setdefault("text", {})["bing"] = Bing
    DDGS_ENGINES = engine_registry.ENGINES
    return True


SUPPORTED_ENGINES = ("bing", "duckduckgo", "baidu", "arxiv", "github")
COOKIE_CACHE_ENGINES = ("bing", "baidu")
API_ENGINES = ("arxiv", "github")
SUPPORTED_BACKENDS = ("auto", "ddgs", "html")
SUPPORTED_BROWSER_COOKIE_SOURCES = ("off", "auto", "edge")
DEFAULT_ENGINES = "bing,duckduckgo"
DEFAULT_BACKEND = "auto"
DEFAULT_BROWSER_COOKIE_SOURCE = "auto"
DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_REGION = "cn-zh"
DEFAULT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_CHARS = 30_000
DEFAULT_COOKIE_CACHE_MAX_AGE = 7 * 24 * 60 * 60
# Cookie exports and the encrypted cache live in a shared cache directory beside
# the skills repository (../../cache relative to this skill), outside version control.
CACHE_ROOT = Path(__file__).resolve().parents[3] / "cache"
COOKIE_CACHE_MAGIC = b"WEBSEARCH-BING-COOKIE-CACHE-V1\n"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": DEFAULT_LANGUAGE,
    "Cache-Control": "no-cache",
}


class WebSearchError(RuntimeError):
    """An expected search/fetch failure that can be returned as JSON."""


class BrowserCookieError(WebSearchError):
    """A browser cookie source could not be read safely."""


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def language_for_region(region: str) -> str:
    """Choose a sensible HTTP Accept-Language value from a region code."""

    _country, _, language = region.lower().partition("-")
    language = language or "en"
    if language == "zh":
        return DEFAULT_LANGUAGE
    if language == "en":
        return "en-US,en;q=0.9,zh-CN;q=0.7"
    return f"{language},{language};q=0.9,en;q=0.7"


def resolve_language(cli_language: str | None, region: str) -> str:
    return cli_language or os.getenv("WEBSEARCH_LANG") or language_for_region(region)


def resolve_proxy(cli_proxy: str | None) -> str:
    """Select a proxy and never silently fall back to a direct connection."""

    return (
        cli_proxy
        or os.getenv("WEBSEARCH_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or DEFAULT_PROXY
    )


def redact_proxy(proxy: str) -> str:
    """Hide optional proxy credentials in diagnostic output."""

    try:
        parsed = urlsplit(proxy)
        if not parsed.hostname:
            return "configured"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, f"{host}{port}", "", "", ""))
    except ValueError:
        return "configured"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_cookie_specs(path_value: str | None) -> list[dict[str, str]]:
    """Load Netscape cookies.txt, browser JSON, or Playwright storage state."""

    if not path_value:
        return []
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise WebSearchError(f"Cookie file not found: {path}")

    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return []

    first = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    if first.startswith("{") or first.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebSearchError(f"Invalid JSON cookie file: {path}") from exc
        if isinstance(parsed, dict) and isinstance(parsed.get("cookies"), list):
            entries = parsed["cookies"]
        elif isinstance(parsed, list):
            entries = parsed
        elif isinstance(parsed, dict):
            entries = [
                {"name": str(name), "value": str(value)}
                for name, value in parsed.items()
            ]
        else:
            raise WebSearchError("Cookie JSON must be an object or array")
        return normalize_cookie_entries(entries)

    specs: list[dict[str, str]] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip("\r\n")
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        fields = line.split("\t", 6)
        if len(fields) != 7:
            continue
        domain, _include_subdomains, cookie_path, _secure, _expires, name, value = fields
        if name:
            specs.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": cookie_path or "/",
                }
            )
    if not specs:
        raise WebSearchError(f"No cookies found in file: {path}")
    return specs


def normalize_cookie_entries(entries: list[Any]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name is None:
            continue
        value = entry.get("value", "")
        domain = entry.get("domain") or entry.get("host") or ""
        cookie_path = entry.get("path") or "/"
        specs.append(
            {
                "name": str(name),
                "value": str(value),
                "domain": str(domain),
                "path": str(cookie_path),
            }
        )
    if not specs:
        raise WebSearchError("No usable cookies found in JSON cookie file")
    return specs


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_unprotect(value: bytes) -> bytes:
    """Decrypt a Windows DPAPI blob without exposing it in diagnostics."""

    if os.name != "nt":
        raise BrowserCookieError("Edge cookie decryption is only supported on Windows")
    if not value:
        raise BrowserCookieError("Edge returned an empty encrypted value")

    input_buffer = ctypes.create_string_buffer(value)
    input_blob = _DataBlob(
        len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_char))
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise BrowserCookieError(
            f"Windows could not decrypt the Edge cookie data: {ctypes.WinError()}"
        )
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _dpapi_protect(value: bytes) -> bytes:
    """Encrypt a cache payload for the current Windows user."""

    if os.name != "nt":
        raise BrowserCookieError("Cookie cache encryption is only supported on Windows")
    if not value:
        raise BrowserCookieError("Cannot encrypt an empty cookie cache")

    input_buffer = ctypes.create_string_buffer(value)
    input_blob = _DataBlob(
        len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_char))
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(output_blob),
    )
    if not ok:
        raise BrowserCookieError(
            f"Windows could not encrypt the cookie cache: {ctypes.WinError()}"
        )
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def cookie_cache_path(engine: str = "bing") -> Path:
    """Return an engine-specific cache path beside the skills directory."""

    if engine not in COOKIE_CACHE_ENGINES:
        raise BrowserCookieError(f"Cookie cache is not supported for {engine}")
    configured = os.getenv(f"WEBSEARCH_{engine.upper()}_COOKIE_CACHE")
    if engine == "bing":
        configured = configured or os.getenv("WEBSEARCH_COOKIE_CACHE")
    if configured:
        return Path(configured).expanduser()
    return CACHE_ROOT / f"{engine}-cookies.dpapi"


def cookie_export_path(engine: str = "bing") -> Path:
    """Return the default Cookie Editor export path for one engine."""

    if engine not in COOKIE_CACHE_ENGINES:
        raise BrowserCookieError(f"Cookie export is not supported for {engine}")
    configured = os.getenv(f"WEBSEARCH_{engine.upper()}_COOKIE_EXPORT")
    if engine == "bing":
        configured = configured or os.getenv("WEBSEARCH_COOKIE_EXPORT")
    if configured:
        return Path(configured).expanduser()
    return cookie_cache_path(engine).with_name(f"{engine}.json")


def _delete_cookie_cache(engine: str = "bing") -> None:
    try:
        cookie_cache_path(engine).unlink(missing_ok=True)
    except OSError:
        # A stale cache must not prevent the manual refresh path.
        pass


def load_cookie_cache_specs(
    engine: str = "bing",
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Load an encrypted engine cookie cache without exposing cookie values."""

    path = cookie_cache_path(engine)
    try:
        encrypted = path.read_bytes()
    except FileNotFoundError as exc:
        raise BrowserCookieError(f"{engine} cookie cache does not exist") from exc
    except OSError as exc:
        raise BrowserCookieError(f"Could not read the {engine} cookie cache: {exc}") from exc

    if not encrypted.startswith(COOKIE_CACHE_MAGIC):
        _delete_cookie_cache(engine)
        raise BrowserCookieError(f"{engine} cookie cache has an unsupported format")
    try:
        payload = json.loads(_dpapi_unprotect(encrypted[len(COOKIE_CACHE_MAGIC) :]))
    except (BrowserCookieError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        _delete_cookie_cache(engine)
        raise BrowserCookieError(f"{engine} cookie cache could not be decrypted") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("cookies"), list):
        _delete_cookie_cache(engine)
        raise BrowserCookieError(f"{engine} cookie cache is incomplete")

    valid_until = payload.get("valid_until")
    try:
        if valid_until is not None and float(valid_until) <= time.time():
            _delete_cookie_cache(engine)
            raise BrowserCookieError(f"{engine} cookie cache has expired")
    except (TypeError, ValueError) as exc:
        _delete_cookie_cache(engine)
        raise BrowserCookieError(f"{engine} cookie cache has an invalid expiry") from exc

    try:
        specs = normalize_cookie_entries(payload["cookies"])
    except WebSearchError as exc:
        _delete_cookie_cache(engine)
        raise BrowserCookieError(f"{engine} cookie cache contains no usable cookies") from exc
    return specs, {
        "source": "cache",
        "engine": engine,
        "status": "loaded",
        "cache_path": str(path),
        "cached_at": payload.get("cached_at"),
        "valid_until": valid_until,
        "cookies_loaded": len(specs),
    }


def save_cookie_cache_specs(
    specs: list[dict[str, str]],
    source_info: dict[str, Any],
    engine: str = "bing",
) -> dict[str, Any]:
    """Persist engine cookies encrypted for the current Windows user."""

    if os.name != "nt":
        raise BrowserCookieError("Automatic cookie caching is only supported on Windows")
    if not specs:
        raise BrowserCookieError(f"Cannot cache an empty {engine} cookie set")

    now = time.time()
    max_age = max(
        300.0,
        env_float("WEBSEARCH_COOKIE_CACHE_MAX_AGE", DEFAULT_COOKIE_CACHE_MAX_AGE),
    )
    valid_until = now + max_age
    source_expiry = source_info.get("cache_expires_at")
    if source_expiry is not None:
        try:
            valid_until = min(valid_until, float(source_expiry))
        except (TypeError, ValueError):
            pass
    if valid_until <= now:
        raise BrowserCookieError(f"All browser {engine} cookies are already expired")

    payload = {
        "version": 1,
        "cached_at": now,
        "valid_until": valid_until,
        "cookies": [
            {
                "name": spec["name"],
                "value": spec["value"],
                "domain": spec.get("domain", ""),
                "path": spec.get("path") or "/",
            }
            for spec in specs
        ],
    }
    encrypted = COOKIE_CACHE_MAGIC + _dpapi_protect(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    path = cookie_cache_path(engine)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        temporary_path.write_bytes(encrypted)
        os.replace(temporary_path, path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        raise BrowserCookieError(
            f"Could not save the {engine} cookie cache: {exc}"
        ) from exc
    return {
        "engine": engine,
        "cache_path": str(path),
        "cached_at": now,
        "valid_until": valid_until,
        "cookies_loaded": len(specs),
    }


def cookie_domain_matches_engine(spec: dict[str, str], engine: str) -> bool:
    """Keep an export scoped to the search engine it is refreshing."""

    domain = spec.get("domain", "").lower().lstrip(".")
    if not domain:
        return True
    if engine == "bing":
        return domain == "bing.com" or domain.endswith(".bing.com")
    if engine == "baidu":
        return domain == "baidu.com" or domain.endswith(".baidu.com")
    return False


def cookie_specs_for_engine(
    specs: list[dict[str, str]], engine: str
) -> list[dict[str, str]]:
    """Restrict a shared export to cookies that can be sent to one engine."""

    return [spec for spec in specs if cookie_domain_matches_engine(spec, engine)]


def load_cookie_export_specs(
    engine: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Load an engine-scoped Cookie Editor export without printing values."""

    path = cookie_export_path(engine)
    if not path.is_file():
        raise BrowserCookieError(
            f"{engine} Cookie Editor export does not exist: {path}"
        )
    try:
        specs = load_cookie_specs(str(path))
    except WebSearchError as exc:
        raise BrowserCookieError(
            f"{engine} Cookie Editor export is invalid: {clean_text(str(exc))}"
        ) from exc
    scoped_specs = [
        spec for spec in specs if cookie_domain_matches_engine(spec, engine)
    ]
    if not scoped_specs:
        raise BrowserCookieError(
            f"{engine} Cookie Editor export contains no {engine} cookies"
        )
    return scoped_specs, {
        "source": "export",
        "engine": engine,
        "export_path": str(path),
        "cookies_loaded": len(scoped_specs),
    }


def _edge_user_data_dir() -> Path:
    configured = os.getenv("WEBSEARCH_EDGE_USER_DATA")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        raise BrowserCookieError("LOCALAPPDATA is not set; cannot locate Edge")
    return Path(local_app_data) / "Microsoft" / "Edge" / "User Data"


def _edge_is_running() -> bool:
    """Return whether any Edge process is still holding the profile open."""

    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(re.search(r'(?im)^"msedge\.exe",', result.stdout or ""))


def _edge_profile_name(user_data_dir: Path) -> str:
    configured = os.getenv("WEBSEARCH_EDGE_PROFILE")
    if configured:
        return configured
    state_path = user_data_dir / "Local State"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        last_used = state.get("profile", {}).get("last_used")
        if isinstance(last_used, str) and last_used:
            return last_used
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return "Default"


def _edge_master_key(user_data_dir: Path) -> bytes:
    state_path = user_data_dir / "Local State"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        encoded_key = state["os_crypt"]["encrypted_key"]
        encrypted_key = base64.b64decode(encoded_key)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BrowserCookieError("Edge encryption key is unavailable") from exc

    if encrypted_key.startswith(b"DPAPI"):
        return _dpapi_unprotect(encrypted_key[5:])
    raise BrowserCookieError(
        "This Edge profile uses an unsupported app-bound cookie encryption format"
    )


def _decrypt_edge_cookie(encrypted_value: bytes, master_key: bytes) -> str | None:
    if not encrypted_value:
        return ""
    if encrypted_value.startswith((b"v10", b"v11", b"v20")):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            nonce = encrypted_value[3:15]
            ciphertext = encrypted_value[15:]
            return AESGCM(master_key).decrypt(nonce, ciphertext, None).decode(
                "utf-8", errors="replace"
            )
        except Exception:  # noqa: BLE001 - one bad cookie must not abort the batch.
            return None
    try:
        return _dpapi_unprotect(encrypted_value).decode("utf-8", errors="replace")
    except BrowserCookieError:
        return None


def _copy_edge_cookie_database(source: Path, destination_dir: Path) -> Path:
    """Copy the DB and sidecars so SQLite can be read without touching Edge."""

    destination = destination_dir / "Cookies"
    try:
        shutil.copy2(source, destination)
        for suffix in ("-wal", "-shm"):
            sidecar = source.with_name(source.name + suffix)
            if sidecar.is_file():
                shutil.copy2(sidecar, destination_dir / sidecar.name)
    except OSError as exc:
        message = str(exc).lower()
        if getattr(exc, "winerror", None) == 32 or "used by another process" in message:
            raise BrowserCookieError(
                "Edge cookie database is locked. Close every Edge window and its "
                "background msedge.exe process, then retry; or provide an exported "
                "cookies.txt/JSON file."
            ) from exc
        raise BrowserCookieError(f"Could not copy the Edge cookie database: {exc}") from exc
    return destination


def load_edge_cookie_specs() -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Load readable Bing cookies from the user's Edge profile.

    The live Edge database is never opened for writing and is copied to a private
    temporary directory first. If Edge holds an exclusive Windows file lock, the
    caller receives a clear manual/export fallback instead of guessing.
    """

    if os.name != "nt":
        raise BrowserCookieError("Automatic Edge cookies are only supported on Windows")
    if _edge_is_running():
        raise BrowserCookieError(
            "Edge is running. Close every Edge window and background msedge.exe "
            "process, confirm that it is closed, then retry before saving a new "
            "cookie cache."
        )
    user_data_dir = _edge_user_data_dir()
    profile = _edge_profile_name(user_data_dir)
    cookie_db = user_data_dir / profile / "Network" / "Cookies"
    if not cookie_db.is_file():
        raise BrowserCookieError(
            f"Edge cookie database was not found for profile {profile!r}"
        )

    master_key = _edge_master_key(user_data_dir)
    specs: list[dict[str, str]] = []
    skipped_encrypted = 0
    with tempfile.TemporaryDirectory(prefix="websearch-edge-") as temporary_dir:
        copied_db = _copy_edge_cookie_database(cookie_db, Path(temporary_dir))
        try:
            uri = f"file:{copied_db.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=3)
            rows = connection.execute(
                "SELECT host_key, name, path, value, encrypted_value, expires_utc "
                "FROM cookies WHERE host_key = 'bing.com' "
                "OR host_key LIKE '%.bing.com'"
            ).fetchall()
            connection.close()
        except sqlite3.Error as exc:
            raise BrowserCookieError(f"Could not read the copied Edge cookie database: {exc}") from exc

    chromium_now = int((time.time() + 11644473600) * 1_000_000)
    cache_expiries: list[float] = []
    for host_key, name, cookie_path, value, encrypted_value, expires_utc in rows:
        if expires_utc and int(expires_utc) < chromium_now:
            continue
        if expires_utc:
            cache_expiries.append(int(expires_utc) / 1_000_000 - 11644473600)
        cookie_value = str(value or "")
        if not cookie_value and encrypted_value:
            cookie_value = _decrypt_edge_cookie(bytes(encrypted_value), master_key) or ""
            if not cookie_value:
                skipped_encrypted += 1
                continue
        if not name:
            continue
        specs.append(
            {
                "name": str(name),
                "value": cookie_value,
                "domain": str(host_key or ""),
                "path": str(cookie_path or "/"),
            }
        )

    if not specs:
        detail = "; encrypted values could not be decrypted" if skipped_encrypted else ""
        raise BrowserCookieError(
            f"No usable Bing cookies were found in Edge profile {profile!r}{detail}"
        )
    info: dict[str, Any] = {
        "source": "edge",
        "profile": profile,
        "cookies_loaded": len(specs),
    }
    if cache_expiries:
        info["cache_expires_at"] = min(cache_expiries)
    return specs, info


def load_edge_browser_cookie_specs() -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Read cookies from a closed Edge profile; never attach to live Edge."""

    return load_edge_cookie_specs()


def build_cookie_jar(specs: list[dict[str, str]]) -> httpx.Cookies:
    cookies = httpx.Cookies()
    for spec in specs:
        domain = spec.get("domain", "")
        cookie_path = spec.get("path") or "/"
        cookies.set(
            spec["name"],
            spec["value"],
            domain=domain or None,
            path=cookie_path,
        )
    return cookies


def make_client(
    proxy: str,
    specs: list[dict[str, str]],
    timeout: float,
    language: str = DEFAULT_LANGUAGE,
) -> httpx.Client:
    headers = dict(BASE_HEADERS)
    headers["Accept-Language"] = language
    unscoped = [spec for spec in specs if not spec.get("domain")]
    if unscoped:
        headers["Cookie"] = "; ".join(
            f"{spec['name']}={spec['value']}" for spec in unscoped
        )
    return httpx.Client(
        proxy=proxy,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers=headers,
        cookies=build_cookie_jar(specs),
        trust_env=False,
    )


def normalize_result_url(raw_url: str, base_url: str) -> str | None:
    if not raw_url:
        return None
    url = urljoin(base_url, raw_url.strip())
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    if host.endswith("google.com") and parsed.path in {"/url", "/searchurl"}:
        url = (query.get("q") or query.get("url") or [url])[0]
    elif host.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        encoded = (query.get("u") or [""])[0]
        if len(encoded) > 2:
            try:
                payload = encoded[2:]
                payload += "=" * (-len(payload) % 4)
                url = base64.urlsafe_b64decode(payload).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                pass

    url = unquote(url)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def make_result(title: Any, url: Any, snippet: Any, engine: str, base_url: str) -> dict[str, str] | None:
    normalized_url = normalize_result_url(str(url or ""), base_url)
    if not normalized_url:
        return None
    return {
        "title": clean_text(title) or normalized_url,
        "url": normalized_url,
        "snippet": clean_text(snippet),
        "engine": engine,
    }


def challenge_error(engine: str, html: str) -> WebSearchError | None:
    """Recognize common anti-bot pages before treating them as search results."""

    soup = BeautifulSoup(html, "lxml")
    title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    visible = clean_text(soup.get_text(" ", strip=True)).lower()
    title_and_text = f"{title.lower()} {visible}"
    baidu_challenge_text = (
        engine == "baidu"
        and any(
            marker in title_and_text
            for marker in (
                "\u5b89\u5168\u9a8c\u8bc1",       # 安全验证
                "\u767e\u5ea6\u5b89\u5168\u9a8c\u8bc1", # 百度安全验证
                "\u5b89\u5168\u68c0\u67e5",       # 安全检查
                "\u8bf7\u5b8c\u6210\u9a8c\u8bc1",   # 请完成验证
                "\u8bf7\u8f93\u5165\u9a8c\u8bc1\u7801", # 请输入验证码
                "\u8bbf\u95ee\u8fc7\u4e8e\u9891\u7e41", # 访问过于频繁
            )
        )
    )
    markers = {
        "bing": (
            "captcha",
            "verify you are human",
            "unusual traffic",
            "robot",
            "one last step",
            "please solve the challenge below",
        ),
        "baidu": ("安全验证", "百度安全验证", "安全检查", "请完成验证"),
    }
    bing_captcha_node = bool(
        engine == "bing"
        and soup.find(
            class_=re.compile(r"captcha|challenge|verify", re.IGNORECASE)
        )
    )
    raw_lower = html.lower()
    bing_captcha_markup = bool(
        engine == "bing"
        and any(
            marker in raw_lower
            for marker in ("captcha_header", "captcha_text", "class=\"captcha\"")
        )
    )
    if (
        any(marker in title_and_text for marker in markers.get(engine, ()))
        or baidu_challenge_text
        or bing_captcha_node
        or bing_captcha_markup
    ):
        return WebSearchError(
            f"{engine} returned a CAPTCHA/challenge page; retry with browser cookies "
            "or manual browser handoff"
        )
    return None


def dedupe_results(results: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for result in results:
        url = result["url"]
        if url in seen:
            continue
        seen.add(url)
        unique.append(result)
        if len(unique) >= limit:
            break
    return unique


def parse_bing_html(html: str, max_results: int) -> list[dict[str, str]]:
    challenge = challenge_error("bing", html)
    if challenge:
        raise challenge
    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, str]] = []
    blocks = soup.select("li.b_algo")
    if not blocks:
        blocks = [heading.parent for heading in soup.select("h2 a")]
    for block in blocks:
        heading = block.select_one("h2 a")
        if heading is None:
            continue
        snippet_node = block.select_one(".b_caption p, p")
        result = make_result(
            heading.get_text(" ", strip=True),
            heading.get("href"),
            snippet_node.get_text(" ", strip=True) if snippet_node else "",
            "bing",
            "https://www.bing.com",
        )
        if result:
            results.append(result)
    return dedupe_results(results, max_results)


def make_news_result(
    title: Any,
    url: Any,
    snippet: Any,
    date: Any,
    source: Any,
    image: Any,
    base_url: str,
) -> dict[str, str] | None:
    normalized_url = normalize_result_url(str(url or ""), base_url)
    if not normalized_url:
        return None
    result = {
        "title": clean_text(title) or normalized_url,
        "url": normalized_url,
        "snippet": clean_text(snippet),
        "engine": "bing-news",
        "date": clean_text(date),
        "source": clean_text(source),
    }
    image_url = normalize_result_url(str(image or ""), base_url)
    if image_url:
        result["image"] = image_url
    return result


def parse_bing_news_html(html: str, max_results: int) -> list[dict[str, str]]:
    challenge = challenge_error("bing", html)
    if challenge:
        raise challenge
    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, str]] = []
    for block in soup.select("div.newsitem"):
        title = block.get("data-title")
        anchor = block.select_one("a.title, a[href]")
        if not title and anchor is not None:
            title = anchor.get_text(" ", strip=True)
        url = block.get("url") or (anchor.get("href") if anchor else "")
        snippet_node = block.select_one("div.snippet, .snippet")
        date_node = block.select_one("span[aria-label]")
        image_node = block.select_one("img")
        result = make_news_result(
            title,
            url,
            snippet_node.get_text(" ", strip=True) if snippet_node else "",
            date_node.get("aria-label", "") if date_node else "",
            block.get("data-author", ""),
            image_node.get("src", "") if image_node else "",
            "https://www.bing.com",
        )
        if result:
            results.append(result)
    return dedupe_results(results, max_results)


def parse_baidu_html(html: str, max_results: int) -> list[dict[str, str]]:
    challenge = challenge_error("baidu", html)
    if challenge:
        raise challenge
    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, str]] = []
    blocks = soup.select("div.result, div.c-container")
    for block in blocks:
        heading = block.select_one("h3 a, h3.t a")
        if heading is None:
            continue
        snippet_node = block.select_one(
            ".c-abstract, [class*='c-abstract'], .c-span-last, .content-right_8Zs40"
        )
        result = make_result(
            heading.get_text(" ", strip=True),
            heading.get("href"),
            snippet_node.get_text(" ", strip=True) if snippet_node else "",
            "baidu",
            "https://www.baidu.com",
        )
        if result:
            results.append(result)
    return dedupe_results(results, max_results)


def get_html_response(
    client: httpx.Client,
    url: str,
    params: dict[str, str],
    max_bytes: int,
) -> tuple[str, dict[str, Any]]:
    chunks: list[bytes] = []
    size = 0
    with client.stream("GET", url, params=params) as response:
        status_code = response.status_code
        final_url = str(response.url)
        content_type = response.headers.get("content-type", "")
        for chunk in response.iter_bytes():
            remaining = max_bytes - size
            if remaining <= 0:
                break
            piece = chunk[:remaining]
            chunks.append(piece)
            size += len(piece)
            if len(piece) < len(chunk):
                break
        encoding = response.encoding or "utf-8"
    body = b"".join(chunks)
    try:
        html = body.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        html = body.decode("utf-8", errors="replace")
    if status_code >= 400:
        raise WebSearchError(f"HTTP {status_code} for {final_url}")
    return html, {
        "status_code": status_code,
        "final_url": final_url,
        "content_type": content_type,
        "truncated": size >= max_bytes,
        "bytes": size,
    }


def search_html_engine(
    engine: str,
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    specs: list[dict[str, str]],
    timeout: float,
    language: str,
) -> list[dict[str, str]]:
    if engine == "bing":
        country, _, language = region.lower().partition("-")
        url = "https://www.bing.com/search"
        params = {
            "q": query,
            "count": str(max_results),
            "setlang": f"{language or 'en'}-{country.upper()}",
        }
        parser = parse_bing_html
    elif engine == "baidu":
        url = "https://www.baidu.com/s"
        params = {
            "wd": query,
            "rn": str(max_results),
            "ie": "utf-8",
        }
        parser = parse_baidu_html
    else:
        raise WebSearchError(f"unsupported engine: {engine}")

    with make_client(proxy, specs, timeout, language) as client:
        html, _response_info = get_html_response(
            client, url, params=params, max_bytes=2 * 1024 * 1024
        )
    results = parser(html, max_results)
    if not results:
        raise WebSearchError(f"{engine} returned no parseable results")
    return results


def search_bing_news_html(
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    specs: list[dict[str, str]],
    timeout: float,
    language: str,
) -> list[dict[str, str]]:
    country, _, region_language = region.lower().partition("-")
    params = {
        "q": query,
        "InfiniteScroll": "1",
        "first": "11",
        "SFX": "1",
        "cc": country,
        "setlang": region_language or "en",
    }
    with make_client(proxy, specs, timeout, language) as client:
        html, _response_info = get_html_response(
            client,
            "https://www.bing.com/news/infinitescrollajax",
            params=params,
            max_bytes=2 * 1024 * 1024,
        )
    results = parse_bing_news_html(html, max_results)
    if not results:
        raise WebSearchError("bing news returned no parseable results")
    return results


def search_bing_news_ddgs(
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    timeout: float,
) -> list[dict[str, str]]:
    """Use DDGS's Bing News backend."""

    if (
        DDGS is None
        or DDGS_ENGINES is None
        or "bing" not in DDGS_ENGINES.get("news", {})
    ):
        raise WebSearchError(
            "installed ddgs release does not expose a Bing News backend"
        )
    ddgs_timeout = max(1, int(round(timeout)))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        items = DDGS(proxy=proxy, timeout=ddgs_timeout).news(
            query,
            region=region,
            safesearch="moderate",
            max_results=max_results,
            backend="bing",
        )
    results: list[dict[str, str]] = []
    for item in items:
        result = make_news_result(
            item.get("title"),
            item.get("url") or item.get("href"),
            item.get("body") or item.get("snippet"),
            item.get("date"),
            item.get("source"),
            item.get("image"),
            "https://www.bing.com",
        )
        if result:
            results.append(result)
    results = dedupe_results(results, max_results)
    if not results:
        raise WebSearchError("ddgs Bing News returned no parseable results")
    return results


def search_bing_ddgs(
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    timeout: float,
) -> list[dict[str, str]]:
    """Use DDGS Bing, deliberately overriding its disabled registry flag."""

    force_enable_ddgs_bing()
    if DDGS is None or DDGS_ENGINES is None or "bing" not in DDGS_ENGINES.get("text", {}):
        raise WebSearchError("installed ddgs release does not expose a Bing backend")
    ddgs_timeout = max(1, int(round(timeout)))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        items = DDGS(proxy=proxy, timeout=ddgs_timeout).text(
            query,
            region=region,
            safesearch="moderate",
            max_results=max_results,
            backend="bing",
        )
    results: list[dict[str, str]] = []
    for item in items:
        result = make_result(
            item.get("title"),
            item.get("href") or item.get("url"),
            item.get("body") or item.get("snippet"),
            "bing",
            "https://www.bing.com",
        )
        if result:
            results.append(result)
    results = dedupe_results(results, max_results)
    if not results:
        raise WebSearchError("ddgs Bing returned no parseable results")
    return results


def search_duckduckgo_ddgs(
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    timeout: float,
) -> list[dict[str, str]]:
    """DDGS native DuckDuckGo backend — no cookies or HTML fallback needed."""

    if DDGS is None:
        raise WebSearchError("ddgs is not installed")
    ddgs_timeout = max(1, int(round(timeout)))
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ddgs = DDGS(proxy=proxy, timeout=ddgs_timeout)
        raw_results = ddgs.text(query, region=region, safesearch="moderate", max_results=max_results)
        # Materialize the generator with a timeout, since DDGS can hang on some proxies.
        with ThreadPoolExecutor(1) as pool:
            fut = pool.submit(list, raw_results)
            try:
                items = fut.result(timeout=ddgs_timeout + 5)
            except FutureTimeout:
                raise WebSearchError("DuckDuckGo DDGS timed out")
    results: list[dict[str, str]] = []
    for item in items:
        result = make_result(
            item.get("title"),
            item.get("href") or item.get("url"),
            item.get("body") or item.get("snippet"),
            "duckduckgo",
            "https://duckduckgo.com",
        )
        if result:
            results.append(result)
    results = dedupe_results(results, max_results)
    if not results:
        raise WebSearchError("DuckDuckGo returned no parseable results")
    return results


def search_news_one(
    engine: str,
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    specs: list[dict[str, str]],
    timeout: float,
    backend_mode: str,
    language: str,
) -> tuple[list[dict[str, str]], str]:
    if engine != "bing":
        raise WebSearchError("--news currently supports Bing only; use --engines bing")

    if specs or backend_mode == "html":
        backend = "httpx-html-news-cookies" if specs else "httpx-html-news"
        return (
            search_bing_news_html(
                query, region, max_results, proxy, specs, timeout, language
            ),
            backend,
        )

    try:
        return (
            search_bing_news_ddgs(query, region, max_results, proxy, timeout),
            "ddgs-news-bing",
        )
    except Exception:
        if backend_mode == "ddgs":
            raise
        return (
            search_bing_news_html(
                query, region, max_results, proxy, specs, timeout, language
            ),
            "httpx-html-news-fallback",
        )


def search_arxiv_api(
    query: str,
    max_results: int,
    proxy: str,
    timeout: float,
) -> list[dict[str, str]]:
    """Search arXiv through the official Atom export API: no scraping or cookies."""

    terms = " ".join(query.split())
    if not terms:
        raise WebSearchError("arxiv engine requires a non-empty query")
    params = {
        "search_query": f"all:{terms}",
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    with make_client(proxy, [], timeout) as client:
        # arXiv throttles by IP and asks for one request every 3 seconds; retry
        # transient throttling once before giving up.
        for attempt in (1, 2):
            response = client.get(
                "https://export.arxiv.org/api/query",
                params=params,
                headers={"Accept": "application/atom+xml, text/xml"},
            )
            if response.status_code not in {429, 500, 502, 503} or attempt == 2:
                break
            time.sleep(3.5)
    if response.status_code == 429:
        raise WebSearchError(
            "arXiv API rate limit exceeded (HTTP 429); arXiv throttles by IP — "
            "wait roughly a minute and rerun the search"
        )
    if response.status_code != 200:
        raise WebSearchError(f"arXiv API returned HTTP {response.status_code}")
    try:
        soup = BeautifulSoup(response.text, "xml")
    except FeatureNotFound:
        soup = BeautifulSoup(response.text, "html.parser")

    results: list[dict[str, str]] = []
    for entry in soup.find_all("entry"):
        id_node = entry.find("id")
        if id_node is None:
            continue
        title_node = entry.find("title")
        summary_node = entry.find("summary")
        authors = [name.get_text(" ", strip=True) for name in entry.find_all("name")]
        author_text = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        published_node = entry.find("published")
        published = published_node.get_text(strip=True)[:10] if published_node else ""
        summary = " ".join(summary_node.get_text().split()) if summary_node else ""
        prefix = " · ".join(part for part in (published, author_text) if part)
        snippet = f"[{prefix}] {summary[:400]}".strip() if prefix else summary[:400]
        result = make_result(
            title_node.get_text(" ", strip=True) if title_node else "",
            id_node.get_text(strip=True),
            snippet,
            "arxiv",
            "https://arxiv.org",
        )
        if result:
            results.append(result)
    return results


def search_github_api(
    query: str,
    max_results: int,
    proxy: str,
    timeout: float,
) -> list[dict[str, str]]:
    """Search GitHub repositories through the REST search API: no scraping or cookies."""

    terms = " ".join(query.split())
    if not terms:
        raise WebSearchError("github engine requires a non-empty query")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with make_client(proxy, [], timeout) as client:
        # Retry transient server errors once; rate-limit answers will not recover.
        for attempt in (1, 2):
            response = client.get(
                "https://api.github.com/search/repositories",
                params={"q": terms, "per_page": str(max_results)},
                headers=headers,
            )
            if response.status_code not in {429, 500, 502, 503} or attempt == 2:
                break
            time.sleep(2.0)
    if response.status_code in {403, 429}:
        hint = (
            "set GITHUB_TOKEN or GH_TOKEN"
            if not token
            else "wait for the rate-limit window to reset"
        )
        raise WebSearchError(
            f"GitHub API rate limit exceeded (HTTP {response.status_code}); {hint}"
        )
    if response.status_code != 200:
        raise WebSearchError(
            f"GitHub API returned HTTP {response.status_code}: "
            f"{clean_text(response.text[:200])}"
        )

    payload = response.json()
    results: list[dict[str, str]] = []
    for item in payload.get("items", []):
        description = " ".join((item.get("description") or "").split())
        updated = (item.get("updated_at") or "")[:10]
        stars = item.get("stargazers_count")
        language = item.get("language") or ""
        meta = " · ".join(
            part
            for part in (
                f"{stars} stars" if stars is not None else "",
                language,
                f"updated {updated}" if updated else "",
            )
            if part
        )
        snippet = f"{meta} — {description}" if meta and description else (meta or description)
        result = make_result(
            item.get("full_name") or "",
            item.get("html_url") or "",
            snippet,
            "github",
            "https://github.com",
        )
        if result:
            results.append(result)
    return results


def search_one(
    engine: str,
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    specs: list[dict[str, str]],
    timeout: float,
    backend_mode: str,
    news: bool,
    language: str,
) -> tuple[list[dict[str, str]], str]:
    if news:
        return search_news_one(
            engine,
            query,
            region,
            max_results,
            proxy,
            specs,
            timeout,
            backend_mode,
            language,
        )

    # arXiv and GitHub use official APIs: no cookies, no HTML scraping, no challenges.
    if engine == "arxiv":
        return search_arxiv_api(query, max_results, proxy, timeout), "arxiv-api"
    if engine == "github":
        return search_github_api(query, max_results, proxy, timeout), "github-api"

    # DuckDuckGo always uses DDGS natively — no cookies or HTML fallback.
    if engine == "duckduckgo":
        with _ddgs_lock:
            return (
                search_duckduckgo_ddgs(query, region, max_results, proxy, timeout),
                "ddgs-duckduckgo",
            )

    # DDGS has no cookie injection API. Use the HTML adapter when cookies are supplied.
    if engine in {"bing"} and not specs and backend_mode != "html":
        try:
            with _ddgs_lock:
                return search_bing_ddgs(query, region, max_results, proxy, timeout), "ddgs-bing-forced"
        except Exception:
            if backend_mode == "ddgs":
                raise
            # Keep the operation useful if DDGS changes or Google changes its HTML.
            return (
                search_html_engine(
                    engine,
                    query,
                    region,
                    max_results,
                    proxy,
                    specs,
                    timeout,
                    language,
                ),
                "httpx-html-fallback",
            )
    backend = "httpx-html-cookies" if specs else "httpx-html"
    return (
        search_html_engine(
            engine, query, region, max_results, proxy, specs, timeout, language
        ),
        backend,
    )


def parse_engines(value: str) -> list[str]:
    engines = []
    for raw_engine in value.split(","):
        engine = raw_engine.strip().lower()
        if not engine:
            continue
        if engine not in SUPPORTED_ENGINES:
            supported = ", ".join(SUPPORTED_ENGINES)
            raise WebSearchError(f"Unsupported engine '{engine}'. Use: {supported}")
        if engine not in engines:
            engines.append(engine)
    if not engines:
        raise WebSearchError("At least one search engine is required")
    return engines


def query_is_chinese_or_china_related(query: str) -> bool:
    """Select Baidu for Chinese-language or China-focused queries."""

    if re.search(r"[\u3400-\u9fff]", query):
        return True
    lowered = query.casefold()
    markers = (
        "china",
        "chinese",
        "beijing",
        "shanghai",
        "shenzhen",
        "guangzhou",
        "hong kong",
        "macau",
        "taiwan",
        "mainland china",
    )
    return any(
        re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", lowered)
        for marker in markers
    )


def query_mentions_api_engines(query: str) -> list[str]:
    """Select arxiv/github when the query names those sites or filters to them."""

    lowered = query.casefold()
    engines = []
    for engine, markers in (
        ("arxiv", ("arxiv", "site:arxiv.org")),
        ("github", ("github", "site:github.com")),
    ):
        if any(marker in lowered for marker in markers):
            engines.append(engine)
    return engines


def resolve_engines(
    configured: str | None,
    query: str,
    region: str,
    news: bool,
) -> list[str]:
    """Resolve explicit engines or the default Bing/Google/Baidu policy."""

    configured = configured or os.getenv("WEBSEARCH_ENGINES")
    if configured:
        engines = parse_engines(configured)
    else:
        engines = ["bing", "duckduckgo"]
        if query_is_chinese_or_china_related(query):
            engines.append("baidu")
        engines.extend(
            engine
            for engine in query_mentions_api_engines(query)
            if engine not in engines
        )

    if news:
        if configured and engines != ["bing"]:
            raise WebSearchError(
                "--news currently supports Bing only; use --engines bing"
            )
        return ["bing"]
    return engines


def is_captcha_error(error: str) -> bool:
    lowered = error.lower()
    return any(
        marker in lowered
        for marker in (
            "captcha",
            "challenge page",
            "consent",
            "anti-bot",
            "verification page",
            "http 403",
            "http 429",
            "http 503",
            "rate limit",
            "too many requests",
        )
    )


def manual_search_action(
    engine: str,
    query: str,
    region: str,
    max_results: int,
    news: bool,
    reason: str,
) -> dict[str, Any]:
    country, _, language = region.lower().partition("-")
    if engine == "baidu":
        base_url = "https://www.baidu.com/s"
        params = {"wd": query, "rn": str(max_results), "ie": "utf-8"}
    elif engine == "arxiv":
        base_url = "https://arxiv.org/search/"
        params = {"query": query, "searchtype": "all"}
    elif engine == "github":
        base_url = "https://github.com/search"
        params = {"q": query, "type": "repositories"}
    elif news:
        base_url = "https://www.bing.com/news/search"
        params = {"q": query, "setlang": language or "en", "cc": country}
    else:
        base_url = "https://www.bing.com/search"
        params = {
            "q": query,
            "count": str(max_results),
            "setlang": language or "en",
        }
    # Only cookie-managed engines have an export path; API engines have none.
    export_path = (
        cookie_export_path(engine) if engine in COOKIE_CACHE_ENGINES else ""
    )
    if reason == "cookie_export_required":
        phase = "export_cookie"
        instructions = (
            f"Open this {engine} URL in Edge. If it shows a challenge or consent "
            f"page, complete it manually, then use Cookie Editor to export JSON or "
            f"Netscape cookies to {export_path}. Rerun the same search afterward. "
            "Do not paste cookie values."
        )
    elif reason == "cookie_export_refresh_required":
        phase = "authenticate_then_export"
        instructions = (
            f"The cached {engine} cookies expired or were rejected. Open this URL "
            f"in Edge, complete the CAPTCHA or consent step manually, overwrite "
            f"{export_path} with a fresh Cookie Editor JSON or Netscape export, and "
            "rerun the same search. Do not paste cookie values."
        )
    elif reason == "api_engine_failed":
        phase = "manual_result_handoff"
        instructions = (
            f"The {engine} API failed after retries. Open this URL in a browser to "
            "collect the results manually, or rerun the search later."
        )
    elif reason == "bing_cookie_refresh_close_edge":
        phase = "close_edge"
        instructions = (
            "Close every Edge window and background msedge.exe process yourself. "
            "Do not force-close it through this Skill. After you have confirmed that "
            "Edge is closed, rerun the same search; the Skill will read the profile, "
            "save an encrypted local cookie cache, and retry. Do not paste cookie values."
        )
    elif reason == "bing_cookie_manual_refresh":
        phase = "authenticate_then_close_edge"
        instructions = (
            "Open this URL in Edge, complete Bing's CAPTCHA or sign-in manually, then "
            "close every Edge window and background msedge.exe process. After that, "
            "rerun the same search; the Skill will read the new cookies, refresh its "
            "encrypted local cache, and retry. Do not paste cookie values."
        )
    else:
        phase = "manual_result_handoff"
        instructions = (
            "Open this URL in Edge and complete the challenge manually. Do not paste "
            "cookie values; rerun the search after completion or provide result URLs."
        )
    return {
        "required": True,
        "engine": engine,
        "reason": reason,
        "phase": phase,
        "url": f"{base_url}?{urlencode(params)}",
        "instructions": instructions,
    }


def run_search(args: argparse.Namespace) -> dict[str, Any]:
    engines = resolve_engines(args.engines, args.query, args.region, args.news)
    proxy = resolve_proxy(args.proxy)
    specs = load_cookie_specs(args.cookies)
    specs_by_engine = {
        engine: cookie_specs_for_engine(specs, engine) for engine in engines
    }
    language = resolve_language(args.lang, args.region)
    started = perf_counter()
    by_engine: dict[str, list[dict[str, str]]] = {}
    backends: dict[str, str] = {}
    errors: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=len(engines)) as executor:
        futures = {
            executor.submit(
                search_one,
                engine,
                args.query,
                args.region,
                args.max_results,
                proxy,
                specs_by_engine[engine],
                args.timeout,
                args.backend,
                args.news,
                language,
            ): engine
            for engine in engines
        }
        for future in as_completed(futures):
            engine = futures[future]
            try:
                results, backend = future.result()
                by_engine[engine] = results
                backends[engine] = backend
            except Exception as exc:  # noqa: BLE001 - preserve partial search results.
                errors.append({"engine": engine, "error": clean_text(str(exc))})

    cookie_fallbacks: dict[str, dict[str, Any]] = {}
    manual_actions: list[dict[str, Any]] = []
    browser_cookie_mode = getattr(args, "browser_cookies", DEFAULT_BROWSER_COOKIE_SOURCE)
    cookie_sources = {
        engine: "file" if specs_by_engine[engine] else "none" for engine in engines
    }
    cookie_counts = {
        engine: len(specs_by_engine[engine]) for engine in engines
    }

    def accept_cookie_retry(
        engine: str,
        candidate_specs: list[dict[str, str]],
        source: str,
        backend_suffix: str,
        fallback_info: dict[str, Any],
    ) -> None:
        retry_results, retry_backend = search_one(
            engine,
            args.query,
            args.region,
            args.max_results,
            proxy,
            candidate_specs,
            args.timeout,
            "html",
            args.news,
            language,
        )
        by_engine[engine] = retry_results
        backends[engine] = f"{retry_backend}-{backend_suffix}"
        cookie_sources[engine] = source
        cookie_counts[engine] = len(candidate_specs)
        errors[:] = [
            item
            for item in errors
            if not (item["engine"] == engine and is_captcha_error(item["error"]))
        ]
        cookie_fallbacks[engine] = fallback_info

    # Keep normal searches browser-free. Only enter this state machine after a
    # positive challenge for a selected engine; an explicit --cookies file is
    # never replaced. Cookie Editor exports are engine-specific and are tested
    # against the live search before being written to the encrypted cache.
    if browser_cookie_mode != "off":
        for engine in engines:
            # API engines never hit CAPTCHAs and have no cookie workflow.
            if engine in API_ENGINES:
                continue
            # A matching explicit --cookies file is authoritative for that
            # engine. If a shared export contains only Bing cookies, Google
            # and Baidu may still use their own encrypted cache/export path.
            if specs_by_engine[engine]:
                continue
            challenged = any(
                item["engine"] == engine and is_captcha_error(item["error"])
                for item in errors
            )
            if not challenged or by_engine.get(engine):
                continue

            cache_error = ""
            export_error = ""
            succeeded = False

            try:
                cache_specs, cache_info = load_cookie_cache_specs(engine)
                accept_cookie_retry(
                    engine,
                    cache_specs,
                    "cache",
                    "cookie-cache",
                    {**cache_info, "status": "succeeded"},
                )
                succeeded = True
            except Exception as exc:  # noqa: BLE001 - continue to export refresh.
                cache_error = clean_text(str(exc))
                if is_captcha_error(cache_error):
                    _delete_cookie_cache(engine)

            if succeeded:
                continue

            try:
                export_specs, export_info = load_cookie_export_specs(engine)
                # Validate first; only a successful request may update the cache.
                retry_results, retry_backend = search_one(
                    engine,
                    args.query,
                    args.region,
                    args.max_results,
                    proxy,
                    export_specs,
                    args.timeout,
                    "html",
                    args.news,
                    language,
                )
                cache_save_error = ""
                try:
                    cache_info = save_cookie_cache_specs(
                        export_specs, export_info, engine=engine
                    )
                except BrowserCookieError as cache_exc:
                    cache_info = {}
                    cache_save_error = clean_text(str(cache_exc))
                by_engine[engine] = retry_results
                backends[engine] = f"{retry_backend}-cookie-export"
                cookie_sources[engine] = "export"
                cookie_counts[engine] = len(export_specs)
                errors[:] = [
                    item
                    for item in errors
                    if not (item["engine"] == engine and is_captcha_error(item["error"]))
                ]
                cookie_fallbacks[engine] = {
                    **export_info,
                    "status": "succeeded",
                    "cache_saved": not bool(cache_save_error),
                    "cache_error": cache_save_error or None,
                    **cache_info,
                }
                succeeded = True
            except Exception as exc:  # noqa: BLE001 - ask for a fresh export.
                export_error = clean_text(str(exc))

            if succeeded:
                continue

            if browser_cookie_mode == "edge" and engine == "bing":
                try:
                    edge_specs, edge_info = load_edge_browser_cookie_specs()
                    retry_results, retry_backend = search_one(
                        engine,
                        args.query,
                        args.region,
                        args.max_results,
                        proxy,
                        edge_specs,
                        args.timeout,
                        "html",
                        args.news,
                        language,
                    )
                    cache_save_error = ""
                    try:
                        cache_info = save_cookie_cache_specs(
                            edge_specs, edge_info, engine=engine
                        )
                    except BrowserCookieError as cache_exc:
                        cache_info = {}
                        cache_save_error = clean_text(str(cache_exc))
                    by_engine[engine] = retry_results
                    backends[engine] = f"{retry_backend}-edge-cookies"
                    cookie_sources[engine] = "edge"
                    cookie_counts[engine] = len(edge_specs)
                    errors[:] = [
                        item
                        for item in errors
                        if not (item["engine"] == engine and is_captcha_error(item["error"]))
                    ]
                    cookie_fallbacks[engine] = {
                        **edge_info,
                        "status": "succeeded",
                        "cache_saved": not bool(cache_save_error),
                        "cache_error": cache_save_error or None,
                        **cache_info,
                    }
                    succeeded = True
                except Exception as exc:  # noqa: BLE001 - preserve manual details.
                    edge_error = clean_text(str(exc))
                    lowered = edge_error.lower()
                    waiting_for_close = (
                        "close every edge" in lowered
                        or "edge is running" in lowered
                        or "used by another process" in lowered
                        or "locked" in lowered
                    )
                    cookie_fallbacks[engine] = {
                        "source": "edge",
                        "status": "waiting_for_user" if waiting_for_close else "failed",
                        "cookies_loaded": 0,
                        "error": edge_error,
                    }
                    errors.append(
                        {
                            "engine": engine,
                            "error": f"Edge cookie refresh failed: {edge_error}",
                        }
                    )

            if succeeded:
                continue

            export_path = cookie_export_path(engine)
            export_exists = export_path.is_file()
            manual_reason = (
                "cookie_export_refresh_required"
                if export_exists
                else "cookie_export_required"
            )
            cookie_fallbacks.setdefault(
                engine,
                {
                    "source": "export",
                    "status": "failed",
                    "cookies_loaded": 0,
                    "cache_error": cache_error or None,
                    "export_error": export_error or None,
                },
            )
            errors.append(
                {
                    "engine": engine,
                    "error": (
                        f"{engine} cookie refresh failed: "
                        f"{export_error or cache_error or 'manual export required'}"
                    ),
                }
            )
            manual_actions.append(
                manual_search_action(
                    engine,
                    args.query,
                    args.region,
                    args.max_results,
                    args.news,
                    manual_reason,
                )
            )

    # API engines have no cookie workflow; surface a browser fallback URL instead.
    for error_item in errors:
        engine = error_item["engine"]
        if engine in API_ENGINES and not by_engine.get(engine):
            manual_actions.append(
                manual_search_action(
                    engine,
                    args.query,
                    args.region,
                    args.max_results,
                    args.news,
                    "api_engine_failed",
                )
            )

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for engine in engines:
        for result in by_engine.get(engine, []):
            if result["url"] in seen:
                continue
            seen.add(result["url"])
            results.append(result)

    engine_results = {engine: by_engine.get(engine, []) for engine in engines}
    complete = all(engine_results[engine] for engine in engines)
    unique_cookie_sources = {source for source in cookie_sources.values()}
    combined_cookie_source = (
        next(iter(unique_cookie_sources))
        if len(unique_cookie_sources) == 1
        else "mixed"
    )
    combined_cookie_count = sum(cookie_counts.values())
    return {
        "operation": "search",
        "query": args.query,
        "engines": engines,
        "search_type": "news" if args.news else "web",
        "region": args.region,
        "language": language,
        "backend_mode": args.backend,
        "backends": backends,
        "proxy": {"enabled": True, "address": redact_proxy(proxy)},
        "complete": complete,
        "engine_results": engine_results,
        "cookies_loaded": combined_cookie_count,
        "cookies_loaded_by_engine": cookie_counts,
        "cookie_source": combined_cookie_source,
        "cookie_sources": cookie_sources,
        "cookie_fallbacks": cookie_fallbacks,
        "browser_cookie_fallback": cookie_fallbacks.get("bing"),
        "manual_actions": manual_actions,
        "manual_action": manual_actions[0] if len(manual_actions) == 1 else None,
        "results": results,
        "errors": errors,
        "elapsed_ms": round((perf_counter() - started) * 1000),
    }


def page_metadata(soup: BeautifulSoup, final_url: str) -> dict[str, str]:
    title_node = soup.select_one("meta[property='og:title'], meta[name='twitter:title']")
    title = title_node.get("content", "") if title_node else ""
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
    description_node = soup.select_one(
        "meta[name='description'], meta[property='og:description']"
    )
    description = description_node.get("content", "") if description_node else ""
    canonical_node = soup.select_one("link[rel='canonical']")
    canonical = (
        urljoin(final_url, canonical_node.get("href", ""))
        if canonical_node and canonical_node.get("href")
        else ""
    )
    return {
        "title": clean_text(title),
        "description": clean_text(description),
        "canonical_url": canonical,
    }


def fallback_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    for tag in soup(["nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def extract_content(html: str, output_format: str) -> tuple[str, str]:
    trafilatura_format = "markdown" if output_format == "markdown" else "txt"
    try:
        content = trafilatura.extract(
            html,
            output_format=trafilatura_format,
            include_links=output_format == "markdown",
            include_tables=True,
            favor_precision=True,
        )
    except TypeError:
        # Compatibility with older Trafilatura releases.
        content = trafilatura.extract(html, output_format=trafilatura_format)
    if content:
        return content.strip(), "trafilatura"
    soup = BeautifulSoup(html, "lxml")
    return fallback_text(soup).strip(), "beautifulsoup-lxml-fallback"


def render_with_playwright(url: str, proxy: str, timeout: float) -> tuple[str, dict[str, Any]]:
    """Load a URL in headless Chromium, wait for JS to finish, return rendered HTML."""
    import asyncio
    from playwright.async_api import async_playwright

    async def _render() -> tuple[str, dict[str, Any]]:
        async with async_playwright() as p:
            proxy_cfg = {"server": proxy} if proxy else None
            browser = await p.chromium.launch(
                headless=True, proxy=proxy_cfg, args=["--no-sandbox"]
            )
            page = await browser.new_page()
            try:
                resp = await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
                status_code = resp.status if resp else 0
                final_url = page.url
                content_type = resp.headers.get("content-type", "") if resp else ""
                html = await page.content()
                return html, {
                    "status_code": status_code,
                    "final_url": final_url,
                    "content_type": content_type,
                    "truncated": False,
                    "bytes": len(html),
                }
            finally:
                await browser.close()

    return asyncio.run(_render())


def run_fetch(args: argparse.Namespace) -> dict[str, Any]:
    proxy = resolve_proxy(args.proxy)
    specs = load_cookie_specs(args.cookies)
    language = resolve_language(args.lang, DEFAULT_REGION)
    started = perf_counter()
    if getattr(args, "render", False):
        html, response_info = render_with_playwright(args.url, proxy, args.timeout)
    else:
        with make_client(proxy, specs, args.timeout, language) as client:
            html, response_info = get_html_response(
                client,
                args.url,
                params={},
                max_bytes=args.max_bytes,
            )
    soup = BeautifulSoup(html, "lxml")
    metadata = page_metadata(soup, response_info["final_url"])
    content, method = extract_content(html, args.format)
    truncated_content = len(content) > args.max_chars
    if truncated_content:
        content = content[: args.max_chars].rstrip() + "\n\n[content truncated]"
    return {
        "operation": "fetch",
        "url": args.url,
        "final_url": response_info["final_url"],
        "status_code": response_info["status_code"],
        "content_type": response_info["content_type"],
        "title": metadata["title"],
        "description": metadata["description"],
        "canonical_url": metadata["canonical_url"],
        "content": content,
        "content_format": args.format,
        "extraction_method": method,
        "content_truncated": truncated_content,
        "response_truncated": response_info["truncated"],
        "response_bytes": response_info["bytes"],
        "cookies_loaded": len(specs),
        "language": language,
        "proxy": {"enabled": True, "address": redact_proxy(proxy)},
        "elapsed_ms": round((perf_counter() - started) * 1000),
    }


def add_network_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--proxy", help="Clash HTTP/HTTPS/SOCKS proxy URL")
    parser.add_argument(
        "--lang",
        default=os.getenv("WEBSEARCH_LANG"),
        help="Accept-Language header, e.g. en-US,en;q=0.9",
    )
    parser.add_argument(
        "--cookies",
        default=os.getenv("WEBSEARCH_COOKIE_FILE"),
        help="Netscape cookies.txt or JSON cookie export",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=env_float("WEBSEARCH_TIMEOUT", DEFAULT_TIMEOUT),
        help="Per-request timeout in seconds",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--engines",
        default=None,
        help=(
            "Comma-separated engines: bing,duckduckgo,baidu,arxiv,github. Default is "
            "Bing+DuckDuckGo; Baidu is added automatically for Chinese/China-related "
            "queries and arxiv/github when the query names those sites"
        ),
    )
    parser.add_argument(
        "--region",
        default=os.getenv("WEBSEARCH_REGION", DEFAULT_REGION),
        help="Search region such as cn-zh or us-en",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=env_int("WEBSEARCH_MAX_RESULTS", DEFAULT_MAX_RESULTS),
        help="Maximum results per engine",
    )
    parser.add_argument(
        "--backend",
        choices=SUPPORTED_BACKENDS,
        default=os.getenv("WEBSEARCH_BACKEND", DEFAULT_BACKEND),
        help="Search backend policy: auto, ddgs, or html",
    )
    parser.add_argument(
        "--news",
        action="store_true",
        help="Use Bing News; defaults to the Bing engine only",
    )
    parser.add_argument(
        "--browser-cookies",
        choices=SUPPORTED_BROWSER_COOKIE_SOURCES,
        default=os.getenv("WEBSEARCH_BROWSER_COOKIES", DEFAULT_BROWSER_COOKIE_SOURCE),
        help=(
            "After a challenge, use the encrypted cache then Cookie Editor export "
            "(auto); use edge for legacy closed-Edge import or off to disable"
        ),
    )
    add_network_options(parser)
    return parser


def emit(payload: dict[str, Any], pretty: bool) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if pretty else None,
        )
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.max_results < 1:
            raise WebSearchError("--max-results must be at least 1")
        payload = run_search(args)
        emit(payload, args.pretty)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must return machine-readable errors.
        emit({"error": clean_text(str(exc)), "operation": "search"}, args.pretty)
        return 0


if __name__ == "__main__":
    sys.exit(main())
