---
name: web-search
description: Search the public web and fetch readable page content through the bundled Python scripts in the Conda environment `skills`, routed through Clash. Use when the user asks for current information, web research, source verification, search results, news, or the contents of a URL. Search Bing, Google, and Baidu; force-enable DDGS text Bing; optionally use Bing News; control region, language, and DDGS/HTML backend selection; extract article text with Trafilatura and BeautifulSoup/lxml; optionally use an exported local cookie file when a site needs an authenticated session.
---

# Web Search

Use the bundled script for web search and URL fetching instead of relying on the host's built-in search tool.

## Operations

Run the script with the directory containing this `SKILL.md` as `<skill-dir>`.

```text
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "query"
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/article"
```

The script prints UTF-8 JSON, which should be parsed before answering. Search returns `title`, `url`, `snippet`, and `engine`; fetch returns `final_url`, `status_code`, `title`, `content`, `content_format`, and extraction metadata.

## Search

- Use the default engines `bing,google,baidu` unless the user asks for a specific source.
- `--max-results` is the maximum number returned by each requested engine.
- Keep the default region `cn-zh` for Chinese queries. Use `--region us-en` for international news, and use `--lang "en-US,en;q=0.9"` when an English `Accept-Language` header is needed. The language is inferred from the region when `--lang` is omitted; `WEBSEARCH_REGION` and `WEBSEARCH_LANG` provide defaults.
- Use `--backend auto` by default. `auto` tries DDGS and then the HTTPX HTML adapter; `ddgs` disables that fallback for diagnostics; `html` skips DDGS and uses the direct HTML adapter. HTML may still receive an anti-bot challenge, so it is an alternate path rather than a guaranteed improvement. `WEBSEARCH_BACKEND` provides the default.
- The dependency file pins current DDGS `9.14.4`. The script deliberately re-registers DDGS's `text` Bing engine at runtime even though newer DDGS releases may mark it disabled. This is intentional: Bing is attempted first, and the script still falls back to its direct Clash-routed Bing HTML adapter if DDGS fails. Because the upstream project disabled text Bing for result-quality concerns, verify returned URLs before citing them.
- Use `--news` for a focused Bing News search. News mode uses Bing only when the default engine list is left unchanged; explicitly use `--engines bing` when combining it with other options. News results include `date`, `source`, and may include `image`.
- Preserve result URLs and cite the actual pages in the final answer. Do not treat a search snippet as proof when the source page can be fetched.
- Allow partial results. If one engine fails, report the failed engine and use the successful results.

```text
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "Python 3.13 release" --engines=bing,google,baidu --max-results 5 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "Anthropic" --news --engines bing --region us-en --lang "en-US,en;q=0.9" --max-results 5 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "query" --backend html --engines bing
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "query" --engines google
```

## Fetch

- Fetch the URL, follow normal redirects, and extract the main readable content.
- Prefer Markdown output for research and plain text when the user asks for text.
- Use the fetched page to verify important claims; if extraction is empty or the page reports a challenge/login wall, say so instead of inventing content.

```text
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/article" --format markdown --max-chars 30000 --pretty
```

The script intentionally does not run JavaScript or bypass CAPTCHA, Cloudflare, login, paywall, or access controls. A browser-rendered fallback can be added later if needed.

## Proxy and cookies

Every search-engine and fetch request must use the Clash proxy. The script resolves the proxy in this order:

1. `--proxy`
2. `WEBSEARCH_PROXY`
3. `HTTPS_PROXY` or `HTTP_PROXY`
4. `http://127.0.0.1:7897`

Set `WEBSEARCH_PROXY` when the local Clash port differs. The script disables direct environment bypasses for its own HTTPX clients, so requests go through the selected proxy and Clash can apply its rules.

Use cookies only when the public request is blocked or the page needs an existing session. The file may be a Netscape `cookies.txt` export or a JSON export/Playwright storage-state file. Cookies force the HTTPX HTML path for Google/Bing because DDGS has no cookie injection API:

```text
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/private-page" --cookies "C:\path\to\cookies.json"
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "query" --cookies "C:\path\to\cookies.txt"
```

Never print cookie values, commit cookie files, or copy them into the Skill directory. A raw browser profile database is not accepted; export the required cookies first. The `WEBSEARCH_COOKIE_FILE` environment variable can provide the default path.

Install the Skill's dependencies once in the dedicated Conda environment before first use:

```text
conda create -n skills python=3.13 -y
conda run --no-capture-output -n skills python -m pip install -r <skill-dir>/requirements.txt
```

If the `skills` environment already exists, skip the `conda create` command. Use `conda run --no-capture-output -n skills ...` for every invocation so the Skill does not depend on whether the host shell has activated the environment or the Windows console code page.
