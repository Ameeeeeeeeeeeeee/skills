---
name: arxiv-cs-digest
description: Use when running or refining the user's daily arXiv CS digest workflow manually, especially to turn prepared CSV and html/pdf notes into a Chinese 5-15 paper briefing with fresh-vs-carryover labeling, 单位信息, and术语解释.
---

# arXiv CS Digest

Use this skill for the manual daily arXiv computer-science digest workflow under `${ARXIV_CS_DIGEST_HOME:-${CODEX_HOME:-$HOME/.codex}/data/arxiv-cs-digest}`.

## Working Assumption

- Prefer manual interactive runs over Codex UI automation when fresh network access matters.
- The UI automation path has shown DNS failures reaching `arxiv.org`; treat it as unreliable for fetching.
- The examples below assume the skill is installed at `${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest`. If your Python runner is not `conda run -n hybrid python`, keep the same script path and swap only the launcher.
- The `hybrid` environment needs Python ≥ 3.11 plus the packages in `requirements.txt`: `conda run -n hybrid python -m pip install -r <skill-dir>/requirements.txt`.
- In a manual session, start with the script's `doctor` output. If `doctor` shows that `.codex/...` is not writable or `arxiv.org` is not reachable inside the sandbox, rerun the exact `scripts/run_digest.py` command with elevated permissions instead of debugging the parser.
- `arXiv` pages are public. No browser cookie is needed for `cs/new`, `abs`, `html`, or `pdf` access.

## Manual Workflow

1. Run `conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" doctor`.
2. Run `conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" status`.
3. If `status.done` is true, stop unless the user explicitly asks for a rerun.
4. Preferred prep path: run `conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" daily --phase prep`.
5. If prep fails because sandbox DNS/network is blocked but cached raw listing HTML already exists, use `conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" prepare-from-raw --date YYMMDD`.
6. Read:
   - `data/md/arxiv_cs_YYMMDD_context.md`
   - `data/csv/arxiv_cs_YYMMDD_candidates.csv`
7. Apply the interest profile in [references/interest_profile.md](references/interest_profile.md).
8. Select 5-15 papers.
   - Prefer fresh papers from today.
   - Use carryover papers only when today's strong papers are too few or there are no good fresh papers.
   - Default to fewer papers with deeper reading. Prefer 5-10 strong papers over filling the quota.
   - Keep the final set practical and research-useful rather than exhaustive.
9. Run `conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" daily --phase materialize --ids <id1> <id2> ...` for the chosen papers.
10. Read `data/md/arxiv_cs_YYMMDD_materialized.md` before writing the report.
11. For author and affiliation metadata, open each selected paper's rendered PDF first-page image and visually confirm the names, units, and role markers before writing the report.
12. The report must be written in Chinese and must rely on the html/pdf reading notes, not only the list-page abstract.
13. Select papers using the list-page title/abstract/candidate context first. Download and deep-read PDFs only after the shortlist is fixed.
14. Before writing each paper section, derive the final 作者 / 单位&组 / 共一 / 通讯 metadata from `PDF 首页图 + HTML 作者区块原文 + HTML 作者注释原文 + PDF 首页文本摘录`. Treat machine candidate lines as hints only.
15. Write the final report to `data/md/arxiv_cs_YYMMDD.md`.
16. Run `conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" daily --phase finalize` after the report is saved.
17. Do not create cache images, cropped figures, or temporary directories on `Desktop`. If figure crops are needed for multimodal reading or markdown embedding, store them under the digest data root such as `data/figures/YYMMDD/` or a system temp directory, and clean temporary-only artifacts before finalize.

## Execution Rules

- Do not trust sandboxed shell access to `.codex/...` paths or `arxiv.org` networking. Use `doctor` to confirm the environment first.
- If a script command fails because of sandboxed write permissions or DNS/network restrictions, rerun that exact command with elevated permissions.
- Do not claim the digest is complete unless `finalize` has run successfully.
- If prep fails and there is no usable cached raw HTML, stop and explain that the fetch failed. Do not invent candidate rows or report content.
- `prepare-from-raw` is a fallback for already cached list HTML only. It does not replace later html/pdf fetching during `materialize`.
- `Desktop` is for the final synced report only. Do not leave figure caches, cropped images, or scratch folders there.

## Report Rules

- Separate `今日新文章` and `前一天已抓到但今天补讲` only when carryovers actually exist.
- Add a short table of contents near the top, with internal links to the major sections and selected papers.
- The opening should contain only stable day-specific facts: scan count, selected count, and 2-4 major themes. Do not write process chatter such as `筛选标准比上一版更严` or long notes about script verification behavior in the report body.
- `对照阅读` is optional. Only include it when there are 2-4 genuinely tight relations such as same task, same benchmark, same system bottleneck, or directly competing formulations. If the relation is weak, omit the section entirely.
- Do not present pairings as `A vs B` unless the paper itself or the experiment setting is explicitly comparative. Prefer plain relation labels such as `一起看可以回答什么问题` or skip the pairing.
- Use `单位`, not `institution hint`.
- Make the Chinese title visually more prominent than surrounding metadata. Prefer putting it in each paper heading and moving the English title to a separate line.
- If a literal Chinese translation sounds awkward, keep the key English term and add a short Chinese gloss instead of forcing a hard translation. For example, prefer `Ground Truth` over a stiff literal rendering.
- For each paper, include `原题 / arXiv id`, `作者`, `单位&组`, `来源校验`, `这篇在解决什么问题`, `任务 / 实验怎么定义`, `核心算法 / 方法机制`, `创新点`, `关键对比结果`, `作者讨论的不足`, `我认为的可能不足`, and `术语解释`.
- `任务 / 实验怎么定义` must explain the benchmark or system setup, the main baselines, and what the main metric measures. Do not report bare percentages without saying what task they belong to.
- Every benchmark, dataset, architecture label, and evaluation metric that appears for the first time should get a one-line explanation. Assume the reader may not know `LoCoMo`, `LongMemEval`, `EpBench`, `client-server`, `Pass@k`, or similar terms.
- On first mention, keep key English terms together with Chinese glosses, for example `Tool Discovery（工具发现）`, `Pass@4（四次尝试中至少成功一次）`.
- Prefer structured markdown with fine-grained subsections inside each paper, such as `#### 研究问题`, `#### 实验设定`, `#### 方法机制`, `#### 结果`, `#### 局限`. Do not compress a whole paper into a few oversized bullets.
- Add one global `作者标记说明` block only if the report actually uses co-first or corresponding markers. In the `作者` line, mark co-first and corresponding authors with separate superscripts such as `<sup>†</sup>` and `<sup>&#42;</sup>`. Do not combine them into one superscript token like `<sup>†*</sup>`.
- The `单位&组` line should merge institution and explicit group or PI clues. Only name a PI or group when the html/pdf materials support it.
- If the institution is a smaller company, startup, or non-obvious lab, append a short parenthetical identity note after the name, for example what kind of company it is or what it builds. Keep it to one short phrase.
- If an author is truly widely known in the field, add a very short note in parentheses after the relevant name. Use this sparingly; skip ordinary senior authors.
- Read the materialized `作者与单位证据` block before deciding final metadata. The machine candidate lines are only auxiliary hints.
- Treat the script's `单位&组` extraction as normalized source text, not authoritative Chinese translation. Translate to Chinese only when confident; otherwise keep the original English name and mark the Chinese rendering as pending.
- Prefer concrete details over generic praise. If the paper reports numbers, include them.
- Use the extracted html/pdf notes to identify method modules, experiment sections, numeric comparisons, and limitations cues.
- When the deep-read notes contain explicit modules, stages, benchmarks, metrics, or numbers, surface them directly instead of paraphrasing at a high level.
- Do not write a method, result, or limitation claim unless it is supported by the materialized notes. If support is weak, say `材料中未可靠定位到`.
- Do not write author, unit, co-first, or corresponding metadata unless it is supported by the rendered PDF first-page image plus html/pdf text. If support is weak, say `未可靠识别`.
- Do not speculate about the user's motives or preferences inside the report. Avoid rhetoric such as `如果你最近...` `对你这种...` `这篇最值得你看...`.
- Avoid novelty phrasing built around `不是...` `没有...` `不再...` unless the paper itself is explicitly framed as a rebuttal or ablation against that claim. Describe contributions positively and plainly.
- `创新点` and `核心算法 / 方法机制` must be concrete: modules, stage decomposition, routing logic, optimization target, training signal, system loop, or evaluation framing. Avoid empty praise and avoid `又做了一个...`.
- `关键对比结果` must name the compared baseline or prior SOTA, the metric name, and the task definition. If only a relative improvement is available, say relative to what.
- `术语解释` should prioritize important older or common concepts the user may not know yet (`RAG`, `ReAct`, `LoRA`, `MRR`, `Pass@k`, etc.), not just the new method name. Paper-specific method names should stay in the main analysis with bilingual phrasing.
- For image reading, prefer rendered page images plus model-side multimodal reading over relying on local OCR-like PDF text formatting for visual content.
- When a paper has a central pipeline figure or main result figure and it is easy to render locally as an image, include one image near the method or result block. If figure extraction is expensive or the main figure is not easy to isolate, skip it.
- If you need a cropped figure for the report, save it under the digest data directory, not on `Desktop`.
- Be selective. A shorter, higher-signal report is better than covering too many weak matches.
- Default to 5-10 main papers, and only exceed 10 when the day is unusually strong.
- Call out uncertainty when unit extraction is weak or missing.
- If no strong fresh papers exist, say so explicitly and use carryovers.
- `对照阅读` or `观察` should be short, comparison-driven, and evidence-based. Do not use them to imply field-wide shifts from a single day.
- Chinese wording must read naturally. Avoid translationese, awkward metaphors, and over-literal English syntax. Rewrite machine-like phrasing into normal Chinese order.
- Treat single-day arXiv evidence conservatively. Prefer wording like `今天这批论文里有人在尝试...` or `从今天选到的样本看...` rather than declaring a field-wide shift or stable trend.
- Do not present unvalidated new-paper directions as established consensus. Distinguish `论文主张` from `你现在可以暂时关注的线索`.

## References

- Interest profile: [references/interest_profile.md](references/interest_profile.md)
- Report format: [references/report_format.md](references/report_format.md)
