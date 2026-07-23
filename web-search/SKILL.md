---
name: web-search
description: Search the public web and fetch readable page content through a local Python script routed through Clash. Use when the user asks for current information, web research, source verification, search results, or the contents of a URL. Search Bing, Google, and Baidu; extract article text with Trafilatura and BeautifulSoup/lxml; optionally use an exported local cookie file when a site needs an authenticated session.
---

# Web Search

Use the bundled script for web search and URL fetching instead of relying on the host's built-in search tool.

## Operations

Run the script with the directory containing this `SKILL.md` as `<skill-dir>`.

```text
python <skill-dir>/scripts/search.py "query"
python <skill-dir>/scripts/fetch.py "https://example.com/article"
```

The script prints UTF-8 JSON, which should be parsed before answering. Search returns `title`, `url`, `snippet`, and `engine`; fetch returns `final_url`, `status_code`, `title`, `content`, `content_format`, and extraction metadata.

## Search

- Use the default engines `bing,google,baidu` unless the user asks for a specific source.
- `--max-results` is the maximum number returned by each requested engine.
- The dependency file pins `ddgs==9.2.3`, the last tested release whose Bing backend is exposed. Newer DDGS releases may disable Bing even while their README still lists it. The script still falls back to its direct Clash-routed Bing HTML adapter if DDGS fails. Do not silently remove Bing from the result set.
- Preserve result URLs and cite the actual pages in the final answer. Do not treat a search snippet as proof when the source page can be fetched.
- Allow partial results. If one engine fails, report the failed engine and use the successful results.

```text
python <skill-dir>/scripts/search.py "Python 3.13 release" --engines bing,google,baidu --max-results 5 --pretty
python <skill-dir>/scripts/search.py "query" --engines google
```

## Fetch

- Fetch the URL, follow normal redirects, and extract the main readable content.
- Prefer Markdown output for research and plain text when the user asks for text.
- Use the fetched page to verify important claims; if extraction is empty or the page reports a challenge/login wall, say so instead of inventing content.

```text
python <skill-dir>/scripts/fetch.py "https://example.com/article" --format markdown --max-chars 30000 --pretty
```

The script intentionally does not run JavaScript or bypass CAPTCHA, Cloudflare, login, paywall, or access controls. A browser-rendered fallback can be added later if needed.

## Proxy and cookies

Every search-engine and fetch request must use the Clash proxy. The script resolves the proxy in this order:

1. `--proxy`
2. `WEBSEARCH_PROXY`
3. `HTTPS_PROXY` or `HTTP_PROXY`
4. `http://127.0.0.1:7897`

Set `WEBSEARCH_PROXY` when the local Clash port differs. The script disables direct environment bypasses for its own HTTPX clients, so requests go through the selected proxy and Clash can apply its rules.

Use cookies only when the public request is blocked or the page needs an existing session. The file may be a Netscape `cookies.txt` export or a JSON export/Playwright storage-state file:

```text
python <skill-dir>/scripts/fetch.py "https://example.com/private-page" --cookies "C:\path\to\cookies.json"
python <skill-dir>/scripts/search.py "query" --cookies "C:\path\to\cookies.txt"
```

Never print cookie values, commit cookie files, or copy them into the Skill directory. A raw browser profile database is not accepted; export the required cookies first. The `WEBSEARCH_COOKIE_FILE` environment variable can provide the default path.

Install the Skill's dependencies once before first use:

```text
python -m pip install -r <skill-dir>/requirements.txt
```
