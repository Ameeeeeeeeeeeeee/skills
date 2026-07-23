#!/usr/bin/env python3
"""Clash-routed web search implementation and shared HTTP helpers."""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import httpx
import trafilatura
from bs4 import BeautifulSoup

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


SUPPORTED_ENGINES = ("bing", "google", "baidu")
DEFAULT_ENGINES = "bing,google,baidu"
DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_REGION = "cn-zh"
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_CHARS = 30_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}


class WebSearchError(RuntimeError):
    """An expected search/fetch failure that can be returned as JSON."""


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


def make_client(proxy: str, specs: list[dict[str, str]], timeout: float) -> httpx.Client:
    headers = dict(BASE_HEADERS)
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
    if engine == "google" and title.lower() == "google search" and not soup.select("h3"):
        return WebSearchError(
            "google returned a challenge/consent page; retry with --cookies"
        )
    markers = {
        "bing": ("captcha", "verify you are human", "unusual traffic", "robot"),
        "google": (
            "unusual traffic",
            "our systems have detected unusual traffic",
            "not a robot",
            "consent.google.com",
            "if you're having trouble accessing google search",
            "please click here if you are not redirected",
        ),
        "baidu": ("安全验证", "百度安全验证", "安全检查", "请完成验证"),
    }
    if any(marker in title_and_text for marker in markers.get(engine, ())):
        return WebSearchError(
            f"{engine} returned an anti-bot verification page; retry with --cookies"
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


def google_region_params(region: str) -> dict[str, str]:
    country, _, language = region.lower().partition("-")
    language = language or "en"
    return {
        "hl": f"{language}-{country.upper()}",
        "lr": f"lang_{language}",
        "cr": f"country{country.upper()}",
    }


def parse_google_html(html: str, max_results: int) -> list[dict[str, str]]:
    challenge = challenge_error("google", html)
    if challenge:
        raise challenge
    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, str]] = []
    containers = soup.select("div.MjjYud, div.g")
    headings = [container.select_one("h3") for container in containers]
    if not any(headings):
        headings = soup.select("h3")

    for heading in headings:
        if heading is None:
            continue
        anchor = heading.find_parent("a")
        if anchor is None:
            anchor = heading.find("a")
        if anchor is None:
            continue
        snippet = ""
        container = heading.parent
        for _ in range(6):
            if container is None:
                break
            snippet_node = container.select_one(".VwiC3b, .IsZvec, .aCOpRe")
            if snippet_node is not None:
                snippet = snippet_node.get_text(" ", strip=True)
                break
            container = container.parent
        result = make_result(
            heading.get_text(" ", strip=True),
            anchor.get("href"),
            snippet,
            "google",
            "https://www.google.com",
        )
        if result:
            results.append(result)
    return dedupe_results(results, max_results)


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
) -> list[dict[str, str]]:
    if engine == "bing":
        url = "https://www.bing.com/search"
        params = {
            "q": query,
            "count": str(max_results),
            "setlang": google_region_params(region)["hl"],
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
        url = "https://www.google.com/search"
        params = {
            "q": query,
            "num": str(max_results),
            "gbv": "1",
            **google_region_params(region),
        }
        parser = parse_google_html

    with make_client(proxy, specs, timeout) as client:
        html, _response_info = get_html_response(
            client, url, params=params, max_bytes=2 * 1024 * 1024
        )
    results = parser(html, max_results)
    if not results:
        raise WebSearchError(f"{engine} returned no parseable results")
    return results


def search_google_ddgs(
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    timeout: float,
) -> list[dict[str, str]]:
    if DDGS is None:
        raise WebSearchError("ddgs is not installed")
    ddgs_timeout = max(1, int(round(timeout)))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        raw_results = DDGS(proxy=proxy, timeout=ddgs_timeout).text(
            query,
            region=region,
            safesearch="moderate",
            max_results=max_results,
            backend="google",
        )
    results: list[dict[str, str]] = []
    for item in raw_results:
        result = make_result(
            item.get("title"),
            item.get("href") or item.get("url"),
            item.get("body") or item.get("snippet"),
            "google",
            "https://www.google.com",
        )
        if result:
            results.append(result)
    results = dedupe_results(results, max_results)
    if not results:
        raise WebSearchError("ddgs Google returned no parseable results")
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
        raw_results = DDGS(proxy=proxy, timeout=ddgs_timeout).text(
            query,
            region=region,
            safesearch="moderate",
            max_results=max_results,
            backend="bing",
        )
    results: list[dict[str, str]] = []
    for item in raw_results:
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


def search_one(
    engine: str,
    query: str,
    region: str,
    max_results: int,
    proxy: str,
    specs: list[dict[str, str]],
    timeout: float,
) -> tuple[list[dict[str, str]], str]:
    # DDGS has no cookie injection API. Use the HTML adapter when cookies are supplied.
    if engine in {"bing", "google"} and not specs:
        try:
            ddgs_search = search_bing_ddgs if engine == "bing" else search_google_ddgs
            backend = "ddgs-bing-forced" if engine == "bing" else "ddgs"
            return ddgs_search(query, region, max_results, proxy, timeout), backend
        except Exception:
            # Keep the operation useful if DDGS changes or Google changes its HTML.
            return (
                search_html_engine(
                    engine, query, region, max_results, proxy, specs, timeout
                ),
                "httpx-html-fallback",
            )
    return (
        search_html_engine(engine, query, region, max_results, proxy, specs, timeout),
        "httpx-html",
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


def run_search(args: argparse.Namespace) -> dict[str, Any]:
    engines = parse_engines(args.engines)
    proxy = resolve_proxy(args.proxy)
    specs = load_cookie_specs(args.cookies)
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
                specs,
                args.timeout,
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

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for engine in engines:
        for result in by_engine.get(engine, []):
            if result["url"] in seen:
                continue
            seen.add(result["url"])
            results.append(result)

    return {
        "operation": "search",
        "query": args.query,
        "engines": engines,
        "backends": backends,
        "proxy": {"enabled": True, "address": redact_proxy(proxy)},
        "cookies_loaded": len(specs),
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


def run_fetch(args: argparse.Namespace) -> dict[str, Any]:
    proxy = resolve_proxy(args.proxy)
    specs = load_cookie_specs(args.cookies)
    started = perf_counter()
    with make_client(proxy, specs, args.timeout) as client:
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
        "proxy": {"enabled": True, "address": redact_proxy(proxy)},
        "elapsed_ms": round((perf_counter() - started) * 1000),
    }


def add_network_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--proxy", help="Clash HTTP/HTTPS/SOCKS proxy URL")
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
        default=os.getenv("WEBSEARCH_ENGINES", DEFAULT_ENGINES),
        help="Comma-separated engines: bing,google,baidu",
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
        return 0 if payload["results"] else 1
    except Exception as exc:  # noqa: BLE001 - CLI must return machine-readable errors.
        emit({"error": clean_text(str(exc)), "operation": "search"}, args.pretty)
        return 1


if __name__ == "__main__":
    sys.exit(main())
