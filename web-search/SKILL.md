---
name: web-search
description: Search the public web and fetch readable page content through the bundled Python scripts in the Conda environment `skills`, routed through Clash. Use for current information, web research, source verification, search results, news, or the contents of a URL. Search Bing and DuckDuckGo by default, add Baidu automatically for Chinese or China-related queries, query arXiv and GitHub through their official APIs when the query names those sites, and use the per-engine encrypted Cookie Editor cache only when a challenge requires it.
---

# Web Search

Use the bundled scripts instead of the host's built-in search tool.

```text
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "query" --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/article" --format markdown --pretty
```

If the terminal shows `gbk codec can't encode` errors, set `PYTHONIOENCODING=utf-8`:
```text
PYTHONIOENCODING=utf-8 conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "query" --pretty
```

Parse the UTF-8 JSON before answering. Search results contain `title`, `url`, `snippet`, and `engine`. Fetch returns `final_url`, `status_code`, `title`, `content`, and extraction metadata.

## Search policy

- With no `--engines`, search Bing and DuckDuckGo. If the query contains Chinese text or is clearly China-related, also search Baidu. An explicit `--engines` list overrides this rule.
- The response has `engine_results` for every selected engine and `complete`. Treat the search as complete only when `complete` is `true`; do not silently answer from one engine when another selected engine failed.
- `--max-results` applies to each selected engine. `--region` defaults to `cn-zh`; use `--region us-en` and an English `--lang` for international results.
- `--backend auto` tries DDGS and then the direct HTTPX HTML adapter. `--backend html` skips DDGS. Bing text DDGS is deliberately force-enabled at runtime, and the HTML path remains available if DDGS fails.
- `--news` is Bing-only; use `--engines bing` explicitly when needed.
- `arxiv` and `github` are API engines: they call the arXiv export API and the GitHub REST search API directly — never scraping and never routed through Bing/DuckDuckGo. Use `--engines arxiv` or `--engines github` alone to search only that source; queries that mention arxiv/github (or use `site:arxiv.org`/`site:github.com`) add the matching API engine automatically alongside the web engines.
- GitHub search accepts GitHub qualifiers, e.g. `"attention pytorch language:python stars:>1000"`. Unauthenticated GitHub search is rate-limited (about 10 requests/minute); set `GITHUB_TOKEN` or `GH_TOKEN` to raise the limit. arXiv results link to `arxiv.org/abs` pages — fetch one for the full abstract.
- Preserve result URLs and fetch important source pages before citing them. A snippet is not proof.
- For complex research tasks: search → fetch → reflect → repeat. After each cycle, identify what information is still missing and formulate a new targeted query rather than repeating similar synonyms. If a page returns minimal content (JS-rendered, paywalled), retry with `--render` or find an alternative source.

Examples:

```text
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "OpenAI" --region us-en --max-results 5 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "中国人工智能政策" --max-results 5 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "Anthropic" --news --engines bing --region us-en --max-results 5 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "query" --engines duckduckgo --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "diffusion models image generation" --engines arxiv --max-results 5 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/search.py "fastapi language:python stars:>10000" --engines github --max-results 5 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/spa" --render --format markdown --pretty
```

## Cookie workflow and required user intervention

Normal searches do not open or control Edge. Every search-engine request still goes through the configured Clash proxy.

When an engine returns a CAPTCHA, consent, or anti-bot challenge, use this state machine:

1. Search without cookies.
2. Load that engine's encrypted Windows-DPAPI cache from `~/.agents/cache/web-search/<engine>-cookies.dpapi`.
3. If the cache is absent, expired, undecipherable, or rejected by the engine, look for a Cookie Editor export at `~/.agents/cache/web-search/<engine>.json`. A successful live retry saves a new encrypted cache, so later searches do not require Edge or user clicks.
4. If the export is missing, invalid, or rejected, the JSON response contains `manual_actions` and `complete: false`. This is a hard user-intervention state: tell the user exactly which engine needs a fresh export, the exact path, and the supplied URL; ask the user to open it in Edge, complete the CAPTCHA/consent/login, export with Cookie Editor, save/overwrite that file, and confirm. Then rerun the original search. Never claim the search succeeded while `complete` is false.

Do not ask the user to perform this on every search. Manual intervention is needed only when a cache is missing or no longer accepted. The script never kills Edge and never prints cookie values.

Cookie Editor JSON is preferred; Netscape `cookies.txt` is also accepted. Header-string exports and encrypted `E2EE_...` exports are not accepted. Treat raw exports as bearer credentials: do not paste them into chat, commit them, or put them in the Skill repository. After the encrypted cache is successfully created, the user may delete the raw JSON export if they want the cache to be the only copy.

Default export/cache paths can be changed independently:

```text
WEBSEARCH_BING_COOKIE_EXPORT
WEBSEARCH_BAIDU_COOKIE_EXPORT
WEBSEARCH_BING_COOKIE_CACHE
WEBSEARCH_BAIDU_COOKIE_CACHE
WEBSEARCH_COOKIE_CACHE_MAX_AGE   # default: 7 days
```

`--cookies` is an explicit cookie file for the current invocation. Its cookies are filtered by engine domain; a matching explicit file takes precedence for that engine and is not silently replaced by an automatic cache.

`--browser-cookies off` disables automatic cookie fallback from the encrypted DPAPI cache and Cookie Editor export.

## Fetch

Fetch follows redirects and extracts readable Markdown or plain text with Trafilatura, falling back to BeautifulSoup/lxml. It does not run JavaScript and does not bypass CAPTCHA, Cloudflare, login, paywall, or access controls. If a page requires a session, pass an explicit JSON/Netscape export with `--cookies`; report a challenge or empty extraction instead of inventing content.

For JavaScript-rendered pages, add `--render` to load the page in headless Chromium before extraction. This adds ~3-5s of startup overhead, so only use it when the page is known to be JS-heavy.

```text
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/article" --format markdown --max-chars 30000 --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/private-page" --cookies "C:\path\to\cookies.json" --pretty
conda run --no-capture-output -n skills python <skill-dir>/scripts/fetch.py "https://example.com/spa-page" --render --format markdown --max-chars 30000 --pretty
```

## Installation and proxy

Install dependencies once in the dedicated environment:

```text
conda create -n skills python=3.13 -y
conda run --no-capture-output -n skills python -m pip install -r <skill-dir>/requirements.txt
```

The proxy is resolved in this order: `--proxy`, `WEBSEARCH_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY`, then `http://127.0.0.1:7897`. HTTPX disables ambient environment bypasses for its own clients, so selected requests go through Clash and Clash decides rule-based routing.
