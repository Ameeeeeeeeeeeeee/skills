#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import socket
import subprocess
import textwrap
import time
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE_URL = "https://arxiv.org"
SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
SKILL_ROOT = SCRIPT_DIR.parent
DEFAULT_RUNTIME_ROOT = Path.home() / ".codex" / "data" / "arxiv-cs-digest"
RUNTIME_ROOT = Path(os.environ.get("ARXIV_CS_DIGEST_HOME", DEFAULT_RUNTIME_ROOT)).expanduser()
DATA_DIR = RUNTIME_ROOT / "data"
CSV_DIR = DATA_DIR / "csv"
PDF_DIR = DATA_DIR / "pdf"
FIRST_PAGE_DIR = PDF_DIR / "first_pages"
MD_DIR = DATA_DIR / "md"
RAW_DIR = DATA_DIR / "raw"
STATE_FILE = RUNTIME_ROOT / "report_state.json"
PROFILE_FILE = RUNTIME_ROOT / "profile.toml"
DESKTOP_DIR = Path.home() / "Desktop"
DATE_RE = re.compile(r"^\d{6}$")
CSV_RE = re.compile(r"^arxiv_cs_(\d{6})\.csv$")
PDF_RE = re.compile(r"^(\d{6})_(.+)\.pdf$")
RAW_RE = re.compile(r"^arxiv_cs_(\d{6})_list_(\d{4})\.html$")
FAILURE_RE = re.compile(r"^arxiv_cs_(\d{6})_failure\.md$")
SHOW_PER_PAGE = 2000
USER_AGENT = "Codex arXiv CS Digest/1.0"
REQUEST_RETRIES = 3
REQUEST_BACKOFF_SECONDS = 2.0

FULL_FIELDS = [
    "crawl_date",
    "source_date",
    "section",
    "section_order",
    "global_order",
    "arxiv_id",
    "abs_url",
    "pdf_url",
    "html_url",
    "title",
    "authors",
    "abstract",
    "subjects",
    "primary_subject",
    "comments",
]

CANDIDATE_FIELDS = FULL_FIELDS + [
    "novelty_bucket",
    "previously_reported",
    "heuristic_score",
    "heuristic_reasons",
]

TERM_GLOSSARY = {
    "llm": "LLM，Large Language Model，大语言模型。",
    "large language model": "Large Language Model，大语言模型，通常指基于大规模语料训练、可进行生成和推理的语言模型。",
    "rag": "RAG，Retrieval-Augmented Generation，先检索外部信息，再把检索结果送入模型生成答案。",
    "retrieval-augmented generation": "Retrieval-Augmented Generation，检索增强生成，目标是让模型回答时利用外部知识而不只依赖参数记忆。",
    "ground truth": "Ground Truth，人工标注或原始事实依据，用来衡量系统是否保留了正确内容。",
    "mcp": "MCP，Model Context Protocol，一种把工具、数据源和模型连接起来的标准化接口思路。",
    "agent": "Agent，能调用工具、执行多步规划并与环境交互的模型系统。",
    "tool use": "Tool use，模型不只生成文字，还会调用搜索、代码、数据库等外部工具。",
    "client-server": "Client-server，客户端与后端服务分离的系统架构，前者发请求，后者存储或计算并返回结果。",
    "react": "ReAct，Reason + Act，一种让模型交替生成推理步骤与工具动作的 agent scaffold。",
    "in-context learning": "In-context learning，模型通过 prompt 中给出的示例直接适应任务，而不更新参数。",
    "lora": "LoRA，Low-Rank Adaptation，低秩适配，用少量可训练参数微调大模型。",
    "sft": "SFT，Supervised Fine-Tuning，监督微调。",
    "test-time scaling": "Test-time scaling，在推理阶段增加采样、搜索、思考步数或工具调用，换取更强性能。",
    "world model": "World model，试图学习环境状态、动态和可行动后果的内部模型。",
    "sae": "SAE，Sparse Autoencoder，常用于机械可解释性中抽取更可分解的内部特征。",
    "model editing": "Model editing，在不重新完整训练模型的情况下，定向修改模型知识或行为。",
    "repe": "RepE，Representation Engineering，通过操控模型内部表征来控制输出行为。",
    "causal abstraction": "Causal abstraction，用因果图或因果层级描述模型内部机制与高层行为之间的对应关系。",
    "circuit analysis": "Circuit analysis，研究模型内部哪些子结构或路径在完成具体能力时起关键作用。",
    "alignment": "Alignment，对齐，指让模型行为更符合人类目标、偏好或安全约束。",
    "preference optimization": "Preference optimization，利用偏好数据优化模型输出，例如 DPO 一类方法。",
    "audio-language model": "Audio-language model，能联合处理音频和文本的模型。",
    "speech-to-speech": "Speech-to-speech，直接从语音输入生成语音输出，不必显式经过文本中间层。",
    "long context": "Long context，超长上下文输入能力，通常涉及检索、压缩、记忆或外部存储问题。",
    "information retrieval": "Information Retrieval，信息检索，研究如何从大规模文档或数据集合中定位相关信息。",
    "recommender system": "Recommender System，推荐系统，根据用户、内容和交互信号进行排序与推荐。",
    "mrr": "MRR，Mean Reciprocal Rank，倒数排名均值，越高表示正确答案通常排得越靠前。",
    "hit rate": "Hit Rate，命中率，关注正确项是否出现在前 k 个候选里。",
    "top-1 accuracy": "Top-1 Accuracy，第一候选正确的比例。",
    "precision@1": "Precision@1，第一名结果为正确项的比例。",
    "pass@k": "Pass@k，进行 k 次独立尝试时至少成功一次的概率。",
    "avg@4": "Avg@4，四次独立运行结果的平均成功率。",
    "exact match": "Exact Match（EM），答案与标准答案完全一致的比例。",
    "f1": "F1，综合 precision 和 recall 的调和平均，常用于问答与检索评测。",
    "precision": "Precision，精确率，预测为正的结果里有多少是真的。",
    "recall": "Recall，召回率，所有真实相关项里有多少被找回。",
    "accuracy": "Accuracy，准确率，整体预测正确的比例。",
    "reranker": "Reranker，重排器，对召回候选再做一轮更精细的排序。",
    "cross-encoder": "Cross-encoder，把 query 和 candidate 拼接后联合编码打分的排序模型。",
    "credit assignment": "Credit assignment，奖励或标签应归因到哪一步决策的问题。",
    "ppo": "PPO，Proximal Policy Optimization，一种常见的强化学习策略优化方法。",
    "grpo": "GRPO，Group Relative Policy Optimization，一类按组比较候选输出优劣的强化学习方法。",
    "theory of mind": "Theory of Mind（ToM），心智理论，指建模他人信念、意图与策略的能力。",
    "cliff's delta": "Cliff's delta，一种非参数效应量，衡量两组样本分布分离程度。",
    "cohen's kappa": "Cohen's kappa，一种标注一致性指标，越高表示不同评审者越一致。",
    "fisher's exact": "Fisher's exact test，适合小样本列联表的显著性检验。",
    "mann-whitney u": "Mann-Whitney U，一种比较两组样本排序差异的非参数检验。",
    "asan": "ASan，AddressSanitizer，用于发现越界访问、use-after-free 等内存错误的运行时工具。",
    "poc": "PoC，Proof of Concept，用于稳定复现 bug 或漏洞的最小触发样例。",
    "scaffold": "Scaffold，包在模型外侧的控制循环、工具接口、状态管理与上下文策略。",
    "mcts": "MCTS，Monte Carlo Tree Search，蒙特卡洛树搜索。",
    "task vector": "Task vector，任务向量，通常指某个任务微调后参数与基座参数的差分。",
    "svd": "SVD，Singular Value Decomposition，奇异值分解，常用于子空间分析。",
    "model steering": "Model steering，在尽量保留原能力的同时，把模型行为推向特定目标或约束。",
    "machine unlearning": "Machine unlearning，让模型有选择地去除某类知识或能力。",
    "locomo": "LoCoMo，多会话长程对话记忆 benchmark，关注跨时间的事实、时序与多跳记忆能力。",
    "longmemeval": "LongMemEval，长程记忆评测集合，覆盖信息抽取、多会话推理、时序推理、知识更新与 abstention。",
    "epbench": "EpBench，episodic memory benchmark，常用来测超长上下文里的事件级记忆与检索。",
    "hotpotqa": "HotpotQA，多跳问答 benchmark，需要跨多条证据拼接答案。",
    "wikimultihop": "WikiMultiHop，多跳问答 benchmark，强调跨文档证据链拼接。",
    "2wikimultihopqa": "2WikiMultihopQA，基于 Wikipedia 的多跳问答 benchmark。",
    "nq": "NQ，Natural Questions，来自真实搜索查询的问答 benchmark。",
    "bamboogle": "Bamboogle，面向搜索增强问答的 benchmark，问题设计强调需要外部检索。",
    "musique": "MuSiQue，多跳问答 benchmark，强调组合式推理和证据整合。",
    "appworld": "AppWorld，长程 agent benchmark，任务涉及多应用、多工具与用户交互。",
    "bfcl": "BFCL，一类工具调用 / function calling benchmark，这里使用的是 BFCL-v3。",
    "tau2-bench": "τ²-Bench，长程工具使用与用户交互场景下的 agent benchmark。",
    "secbench": "SEC-bench，真实 C/C++ 安全漏洞自动修复 benchmark，给定代码库、报告和 PoC 后要求合成补丁。",
    "digital humanities": "Digital Humanities，数字人文，用计算方法研究历史、文学、文化与档案材料。",
    "digital library": "Digital Library，数字图书馆，强调文献、档案和知识资源的组织、检索与服务。",
    "library science": "Library Science，图书馆学，研究信息组织、编目、检索与知识服务体系。",
}

# Normalize a small number of recurring institution variants to stable English names.
# Final Chinese rendering belongs to the report-writing step, not this extractor.
UNIT_CANONICAL_ALIASES = {
    "University of Illinois at Urbana-Champaign": "University of Illinois Urbana-Champaign",
    "UIUC": "University of Illinois Urbana-Champaign",
    "National Taiwan University, Taiwan": "National Taiwan University",
    "The Ohio State University": "Ohio State University",
}


@dataclass
class Paper:
    crawl_date: str
    source_date: str
    section: str
    section_order: int
    global_order: int
    arxiv_id: str
    abs_url: str
    pdf_url: str
    html_url: str
    title: str
    authors: str
    abstract: str
    subjects: str
    primary_subject: str
    comments: str

    def to_row(self) -> dict[str, str]:
        return {
            "crawl_date": self.crawl_date,
            "source_date": self.source_date,
            "section": self.section,
            "section_order": str(self.section_order),
            "global_order": str(self.global_order),
            "arxiv_id": self.arxiv_id,
            "abs_url": self.abs_url,
            "pdf_url": self.pdf_url,
            "html_url": self.html_url,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "subjects": self.subjects,
            "primary_subject": self.primary_subject,
            "comments": self.comments,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily arXiv CS digest workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Fetch today's arXiv CS listing and build candidate files")
    prepare.add_argument("--date", dest="date_slug", default=current_date_slug())

    materialize = subparsers.add_parser("materialize", help="Download/reuse PDFs and build materialized notes")
    materialize.add_argument("--date", dest="date_slug", default=current_date_slug())
    materialize.add_argument("--ids", nargs="*", default=[])

    finalize = subparsers.add_parser("finalize", help="Sync selected ids from the report, copy it to Desktop, and purge old cache")
    finalize.add_argument("--date", dest="date_slug", default=current_date_slug())

    status = subparsers.add_parser("status", help="Check whether today's digest is already complete")
    status.add_argument("--date", dest="date_slug", default=current_date_slug())

    sync_selection = subparsers.add_parser("sync-selection", help="Sync selected ids from the final report headings")
    sync_selection.add_argument("--date", dest="date_slug", default=current_date_slug())

    daily = subparsers.add_parser("daily", help="Run grouped daily phases with fewer process startups")
    daily.add_argument("--date", dest="date_slug", default=current_date_slug())
    daily.add_argument("--phase", choices=["prep", "materialize", "finalize"], required=True)
    daily.add_argument("--ids", nargs="*", default=[])
    daily.add_argument("--force", action="store_true", help="Rerun the prep phase even if today's digest already looks complete")

    prepare_from_raw = subparsers.add_parser("prepare-from-raw", help="Build candidate files from previously saved listing HTML")
    prepare_from_raw.add_argument("--date", dest="date_slug", default=current_date_slug())

    doctor = subparsers.add_parser("doctor", help="Check manual-run prerequisites such as write access and arXiv connectivity")
    doctor.add_argument("--date", dest="date_slug", default=current_date_slug())

    subparsers.add_parser("smoke-test", help="Run lightweight parser and selection smoke tests")

    args = parser.parse_args()
    if hasattr(args, "date_slug"):
        validate_date_slug(args.date_slug)
    ensure_layout()

    try:
        if args.command == "prepare":
            run_prepare(args.date_slug)
            return 0
        if args.command == "materialize":
            if not args.ids:
                raise SystemExit("materialize requires at least one arXiv id via --ids")
            run_materialize(args.date_slug, args.ids)
            return 0
        if args.command == "finalize":
            run_finalize(args.date_slug)
            return 0
        if args.command == "status":
            run_status(args.date_slug)
            return 0
        if args.command == "sync-selection":
            synced = sync_selected_ids_from_report(args.date_slug)
            print(json.dumps({"date": args.date_slug, "selected_ids": synced}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "daily":
            run_daily(args.date_slug, args.phase, args.ids, args.force)
            return 0
        if args.command == "prepare-from-raw":
            run_prepare_from_raw(args.date_slug)
            return 0
        if args.command == "doctor":
            run_doctor(args.date_slug)
            return 0
        if args.command == "smoke-test":
            run_smoke_test()
            return 0
        raise SystemExit(f"unknown command: {args.command}")
    except KeyboardInterrupt:
        raise
    except SystemExit as exc:
        if getattr(args, "command", "") not in {"status", "smoke-test", "doctor"} and getattr(args, "date_slug", "") and exc.code not in (0, None):
            write_failure_note(args.date_slug, args.command, format_error(exc))
        raise
    except Exception as exc:
        if getattr(args, "command", "") not in {"status", "smoke-test", "doctor"} and getattr(args, "date_slug", ""):
            write_failure_note(args.date_slug, args.command, format_error(exc))
        raise

def current_date_slug() -> str:
    tz_name = load_profile().get("timezone", "Asia/Shanghai")
    now = datetime.now(ZoneInfo(tz_name))
    return now.strftime("%y%m%d")


def validate_date_slug(value: str) -> None:
    if not DATE_RE.match(value):
        raise SystemExit(f"expected YYMMDD date, got: {value}")


def ensure_layout() -> None:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    FIRST_PAGE_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

def load_profile() -> dict:
    with PROFILE_FILE.open("rb") as handle:
        return tomllib.load(handle)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"reported_ids": {}, "reported_by_day": {}}
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def http_get(s: requests.Session, url: str, timeout: int) -> requests.Response:
    last_error = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = s.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == REQUEST_RETRIES - 1:
                raise
            time.sleep(REQUEST_BACKOFF_SECONDS * (attempt + 1))
    raise last_error or RuntimeError(f"request failed: {url}")


def write_prepare_outputs(date_slug: str, papers: list[Paper]) -> None:
    profile = load_profile()
    state = load_state()
    write_csv(full_csv_path(date_slug), FULL_FIELDS, (paper.to_row() for paper in papers))

    previous_date = previous_full_date(date_slug)
    previous_rows = read_csv_rows(full_csv_path(previous_date)) if previous_date else []
    previous_ids = {row["arxiv_id"] for row in previous_rows}
    reported_ids = set(state.get("reported_ids", {}))
    carryovers = build_carryovers(previous_rows, reported_ids, profile.get("carryover_cap", 30), date_slug)
    candidates = build_candidates(papers, previous_ids, reported_ids, carryovers, profile)

    write_csv(candidate_csv_path(date_slug), CANDIDATE_FIELDS, candidates)
    write_context_md(date_slug, papers, candidates, previous_date, state)


def load_cached_listing(date_slug: str) -> list[Paper]:
    raw_paths = sorted(RAW_DIR.glob(f"arxiv_cs_{date_slug}_list_*.html"))
    if not raw_paths:
        raise SystemExit(f"no cached listing HTML files found for {date_slug} under {RAW_DIR}")

    papers: list[Paper] = []
    seen_ids: set[str] = set()
    global_order = 0
    total_entries = None

    for raw_path in raw_paths:
        match = RAW_RE.match(raw_path.name)
        offset = int(match.group(2)) if match else 0
        html = raw_path.read_text(encoding="utf-8")
        page_total, page_papers = parse_listing_page(html, date_slug, offset, global_order)
        if total_entries is None:
            total_entries = page_total
        global_order += len(page_papers)
        for paper in page_papers:
            if paper.arxiv_id in seen_ids:
                continue
            seen_ids.add(paper.arxiv_id)
            papers.append(paper)

    if not papers:
        raise SystemExit(f"cached listing HTML exists for {date_slug} but no papers were parsed")
    return papers


def check_directory_writable(path: Path) -> dict[str, str | bool]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"ok": True, "error": ""}
    except Exception as exc:
        return {"ok": False, "error": format_error(exc)}


def check_arxiv_connectivity() -> dict[str, str | bool | int]:
    payload: dict[str, str | bool | int] = {
        "dns_ok": False,
        "https_ok": False,
        "resolved_ip": "",
        "dns_error": "",
        "http_error": "",
        "status_code": 0,
    }
    try:
        infos = socket.getaddrinfo("arxiv.org", 443, type=socket.SOCK_STREAM)
        if infos:
            payload["dns_ok"] = True
            payload["resolved_ip"] = infos[0][4][0]
    except OSError as exc:
        payload["dns_error"] = format_error(exc)

    try:
        with session() as s:
            response = http_get(s, f"{BASE_URL}/list/cs/new?skip=0&show=25", timeout=20)
        payload["https_ok"] = True
        payload["status_code"] = response.status_code
    except Exception as exc:
        payload["http_error"] = format_error(exc)

    return payload


def run_doctor(date_slug: str) -> None:
    writable = {
        "runtime_root": check_directory_writable(RUNTIME_ROOT),
        "data": check_directory_writable(DATA_DIR),
        "csv": check_directory_writable(CSV_DIR),
        "pdf": check_directory_writable(PDF_DIR),
        "first_page": check_directory_writable(FIRST_PAGE_DIR),
        "md": check_directory_writable(MD_DIR),
        "raw": check_directory_writable(RAW_DIR),
    }
    cached_raw = [path.name for path in sorted(RAW_DIR.glob(f"arxiv_cs_{date_slug}_list_*.html"))[:5]]
    payload = {
        "date": date_slug,
        "skill_root": str(SKILL_ROOT),
        "script_path": str(SCRIPT_PATH),
        "runtime_root": str(RUNTIME_ROOT),
        "status": build_status(date_slug),
        "writable": writable,
        "network": check_arxiv_connectivity(),
        "render_tools": {
            "qlmanage": bool(shutil.which("qlmanage")),
            "sips": bool(shutil.which("sips")),
        },
        "cached_raw_listing_files": cached_raw,
        "failure_note_exists": failure_note_path(date_slug).exists(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_prepare(date_slug: str) -> None:
    papers = fetch_full_listing(date_slug)
    write_prepare_outputs(date_slug, papers)


def run_prepare_from_raw(date_slug: str) -> None:
    papers = load_cached_listing(date_slug)
    write_prepare_outputs(date_slug, papers)
    payload = {
        "phase": "prepare-from-raw",
        "status": "prepared_from_raw",
        "date": date_slug,
        "full_csv_path": str(full_csv_path(date_slug)),
        "candidate_csv_path": str(candidate_csv_path(date_slug)),
        "context_md_path": str(context_md_path(date_slug)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_materialize(date_slug: str, ids: list[str]) -> None:
    chosen_ids = dedupe_preserve(ids)
    candidate_rows = {row["arxiv_id"]: row for row in read_csv_rows(candidate_csv_path(date_slug))}
    all_rows = load_recent_full_rows()

    materialized_entries = []
    with session() as s:
        for arxiv_id in chosen_ids:
            row = candidate_rows.get(arxiv_id) or all_rows.get(arxiv_id)
            if not row:
                raise SystemExit(f"arXiv id not found in candidate or recent full CSV files: {arxiv_id}")
            pdf_path = ensure_pdf_for_id(s, date_slug, row)
            first_page_image_path = ensure_first_page_image(pdf_path)
            pdf_analysis = extract_pdf_analysis(pdf_path, row.get("authors", ""))
            html_analysis = fetch_html_analysis(s, row.get("html_url", ""))
            merged_author_meta = merge_author_metadata(html_analysis, pdf_analysis)
            method_excerpt = prefer_excerpt(html_analysis["method_excerpt"], pdf_analysis["method_excerpt"])
            results_excerpt = prefer_excerpt(html_analysis["results_excerpt"], pdf_analysis["results_excerpt"])
            limitations_excerpt = prefer_excerpt(html_analysis["limitations_excerpt"], pdf_analysis["limitations_excerpt"])
            numeric_results = merge_result_bullets(html_analysis["numeric_results"], pdf_analysis["numeric_results"])
            source_verification = build_source_verification(
                row.get("abstract", ""),
                row.get("html_url", ""),
                html_analysis,
                pdf_analysis,
                str(first_page_image_path),
            )
            term_notes = detect_term_notes(
                row.get("title", ""),
                " ".join(
                    [
                        row.get("title", ""),
                        row.get("abstract", ""),
                        html_analysis["section_titles"],
                        html_analysis["intro_excerpt"],
                        method_excerpt,
                        results_excerpt,
                        limitations_excerpt,
                        numeric_results,
                        pdf_analysis["first_page_snippet"],
                    ]
                )
            )
            materialized_entries.append(
                {
                    "arxiv_id": arxiv_id,
                    "title": row["title"],
                    "novelty_bucket": row.get("novelty_bucket", ""),
                    "authors_csv": row["authors"],
                    "abs_url": row["abs_url"],
                    "pdf_url": row["pdf_url"],
                    "html_url": row["html_url"],
                    "pdf_path": str(pdf_path),
                    "first_page_image_path": str(first_page_image_path),
                    "heuristic_score": row.get("heuristic_score", ""),
                    "heuristic_reasons": row.get("heuristic_reasons", ""),
                    "html_author_block_text": html_analysis["author_block_text"],
                    "html_author_notes_text": html_analysis["author_notes_text"],
                    "html_unit_candidates": html_analysis["unit_text"],
                    "pdf_unit_candidates": pdf_analysis["unit_text"],
                    "machine_cofirst_candidates": merged_author_meta["cofirst_authors"],
                    "machine_corresponding_candidates": merged_author_meta["corresponding_authors"],
                    "machine_group_candidates": merged_author_meta["group_pi_text"],
                    "machine_role_notes": merged_author_meta["author_role_notes"],
                    "list_abstract": row.get("abstract", ""),
                    "html_section_titles": html_analysis["section_titles"],
                    "html_intro_excerpt": html_analysis["intro_excerpt"],
                    "method_excerpt": method_excerpt,
                    "results_excerpt": results_excerpt,
                    "limitations_excerpt": limitations_excerpt,
                    "numeric_results": numeric_results,
                    "first_page_snippet": pdf_analysis["first_page_snippet"],
                    "first_page_text_excerpt": pdf_analysis["first_page_text_excerpt"],
                    "source_verification": source_verification,
                    "term_notes": term_notes,
                }
            )

    write_materialized_md(date_slug, materialized_entries)

def run_finalize(date_slug: str) -> None:
    report_path = report_md_path(date_slug)
    if not report_path.exists():
        raise SystemExit(f"missing report file: {report_path}")

    selected_ids = sync_selected_ids_from_report(date_slug)
    state = load_state()
    for arxiv_id in selected_ids:
        state.setdefault("reported_ids", {})[arxiv_id] = date_slug
    state.setdefault("reported_by_day", {})[date_slug] = selected_ids
    save_state(state)

    desktop_report = DESKTOP_DIR / report_path.name
    shutil.copy2(report_path, desktop_report)
    clear_failure_note(date_slug)
    purge_oldest_day_once()

def run_status(date_slug: str) -> None:
    print(json.dumps(build_status(date_slug), ensure_ascii=False, indent=2))


def run_daily(date_slug: str, phase: str, ids: list[str], force: bool = False) -> None:
    if phase == "prep":
        status = build_status(date_slug)
        if status["done"] and not force:
            payload = {"phase": "prep", "status": "already_done", **status}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        run_smoke_test()
        run_prepare(date_slug)
        payload = {
            "phase": "prep",
            "status": "prepared",
            "date": date_slug,
            "full_csv_path": str(full_csv_path(date_slug)),
            "candidate_csv_path": str(candidate_csv_path(date_slug)),
            "context_md_path": str(context_md_path(date_slug)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if phase == "materialize":
        if not ids:
            raise SystemExit("daily materialize requires at least one arXiv id via --ids")
        run_materialize(date_slug, ids)
        payload = {
            "phase": "materialize",
            "status": "materialized",
            "date": date_slug,
            "selected_count": len(dedupe_preserve(ids)),
            "materialized_md_path": str(materialized_md_path(date_slug)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if phase == "finalize":
        run_finalize(date_slug)
        payload = {"phase": "finalize", "status": "done", **build_status(date_slug)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    raise SystemExit(f"unknown daily phase: {phase}")


def find_total_entries(soup: BeautifulSoup) -> int:
    page_text = clean_text(soup.get_text(" ", strip=True))
    match = re.search(r"Total of\s+(\d+)\s+entries", page_text)
    if not match:
        raise SystemExit("failed to parse arXiv entry count from listing page; inspect data/raw for the saved HTML snapshot")
    return int(match.group(1))


def find_articles_container(soup: BeautifulSoup):
    articles = soup.find("dl", id="articles")
    if articles is not None:
        return articles
    for candidate in soup.find_all("dl"):
        if candidate.find("a", href=re.compile(r"^/abs/")):
            return candidate
    raise SystemExit("failed to locate arXiv articles listing; inspect data/raw for the saved HTML snapshot and patch the parser")

def fetch_full_listing(date_slug: str) -> list[Paper]:
    papers: list[Paper] = []
    total_entries = None
    offset = 0
    global_order = 0

    with session() as s:
        while total_entries is None or offset < total_entries:
            url = f"{BASE_URL}/list/cs/new?skip={offset}&show={SHOW_PER_PAGE}"
            response = http_get(s, url, timeout=60)
            raw_listing_path(date_slug, offset).write_text(response.text, encoding="utf-8")
            page_total, page_papers = parse_listing_page(response.text, date_slug, offset, global_order)
            if total_entries is None:
                total_entries = page_total
            if not page_papers:
                break
            papers.extend(page_papers)
            global_order += len(page_papers)
            offset += SHOW_PER_PAGE
            if len(page_papers) < SHOW_PER_PAGE and total_entries <= offset:
                break

    return papers

def parse_listing_page(html: str, date_slug: str, offset: int, global_order_start: int) -> tuple[int, list[Paper]]:
    soup = BeautifulSoup(html, "html.parser")
    total_entries = find_total_entries(soup)
    articles = find_articles_container(soup)

    current_section = "Unknown"
    section_order = Counter()
    papers: list[Paper] = []
    pending_dt = None
    global_order = global_order_start

    for child in articles.children:
        if getattr(child, "name", None) == "h3":
            current_section = clean_text(child.get_text(" ", strip=True))
            continue
        if getattr(child, "name", None) == "dt":
            pending_dt = child
            continue
        if getattr(child, "name", None) != "dd" or pending_dt is None:
            continue
        section_order[current_section] += 1
        global_order += 1
        papers.append(parse_entry(pending_dt, child, date_slug, current_section, section_order[current_section], global_order))
        pending_dt = None

    return total_entries, papers

def parse_entry(dt_node, dd_node, date_slug: str, section: str, section_order: int, global_order: int) -> Paper:
    abs_link = dt_node.find("a", href=re.compile(r"^/abs/"))
    pdf_link = dt_node.find("a", href=re.compile(r"^/pdf/"))
    html_link = dt_node.find("a", href=re.compile(r"^(?:/html/|https://arxiv.org/html/)"))

    if abs_link is None or pdf_link is None:
        raise SystemExit("listing entry missing abs or pdf link")

    title_node = dd_node.find("div", class_="list-title")
    authors_node = dd_node.find("div", class_="list-authors")
    abstract_node = dd_node.find("p", class_="mathjax")
    subjects_node = dd_node.find("div", class_="list-subjects")
    comments_node = dd_node.find("div", class_="list-comments")

    title = clean_labelled_text(title_node, "Title:")
    authors = ", ".join(clean_text(a.get_text(" ", strip=True)) for a in authors_node.find_all("a")) if authors_node else ""
    abstract = clean_text(abstract_node.get_text(" ", strip=True)) if abstract_node else ""
    subjects = clean_labelled_text(subjects_node, "Subjects:")
    primary_subject_node = subjects_node.find("span", class_="primary-subject") if subjects_node else None
    primary_subject = clean_text(primary_subject_node.get_text(" ", strip=True)) if primary_subject_node else ""
    comments = clean_labelled_text(comments_node, "Comments:")

    abs_path = abs_link.get("href", "")
    pdf_path = pdf_link.get("href", "")
    html_path = html_link.get("href", "") if html_link else ""
    arxiv_id = clean_text(abs_link.get_text(" ", strip=True)).replace("arXiv:", "")

    return Paper(
        crawl_date=date_slug,
        source_date=date_slug,
        section=section,
        section_order=section_order,
        global_order=global_order,
        arxiv_id=arxiv_id,
        abs_url=urljoin(BASE_URL, abs_path),
        pdf_url=urljoin(BASE_URL, pdf_path),
        html_url=urljoin(BASE_URL, html_path) if html_path else "",
        title=title,
        authors=authors,
        abstract=abstract,
        subjects=subjects,
        primary_subject=primary_subject,
        comments=comments,
    )

def build_carryovers(previous_rows: list[dict[str, str]], reported_ids: set[str], carryover_cap: int, current_date: str) -> list[dict[str, str]]:
    carryovers = []
    for row in previous_rows:
        arxiv_id = row["arxiv_id"]
        if arxiv_id in reported_ids:
            continue
        candidate = dict(row)
        candidate["source_date"] = row.get("crawl_date") or current_date
        carryovers.append(candidate)
    return carryovers[:carryover_cap]


def build_candidates(
    papers: list[Paper],
    previous_ids: set[str],
    reported_ids: set[str],
    carryovers: list[dict[str, str]],
    profile: dict,
) -> list[dict[str, str]]:
    candidates = []
    for paper in papers:
        row = paper.to_row()
        bucket = "today_new" if paper.arxiv_id not in previous_ids else "today_seen_before"
        score, reasons = score_row(row, bucket, profile)
        row.update(
            {
                "novelty_bucket": bucket,
                "previously_reported": "yes" if paper.arxiv_id in reported_ids else "no",
                "heuristic_score": f"{score:.2f}",
                "heuristic_reasons": reasons,
            }
        )
        if score > 0 and paper.arxiv_id not in reported_ids:
            candidates.append(row)

    if not candidates:
        for carryover in carryovers:
            score, reasons = score_row(carryover, "carryover_unreported", profile)
            enriched = dict(carryover)
            enriched.update(
                {
                    "novelty_bucket": "carryover_unreported",
                    "previously_reported": "no",
                    "heuristic_score": f"{score:.2f}",
                    "heuristic_reasons": reasons,
                }
            )
            candidates.append(enriched)
    else:
        scored_carryovers = []
        for carryover in carryovers:
            score, reasons = score_row(carryover, "carryover_unreported", profile)
            if score <= 0:
                continue
            enriched = dict(carryover)
            enriched.update(
                {
                    "novelty_bucket": "carryover_unreported",
                    "previously_reported": "no",
                    "heuristic_score": f"{score:.2f}",
                    "heuristic_reasons": reasons,
                }
            )
            scored_carryovers.append(enriched)
        candidates.extend(scored_carryovers)

    candidates.sort(key=lambda row: (-float(row["heuristic_score"]), novelty_rank(row["novelty_bucket"]), int(row["global_order"])))
    cap = int(profile.get("candidate_cap", 120))
    return candidates[:cap]


def novelty_rank(bucket: str) -> int:
    order = {
        "today_new": 0,
        "today_seen_before": 1,
        "carryover_unreported": 2,
    }
    return order.get(bucket, 9)


def score_row(row: dict[str, str], novelty_bucket: str, profile: dict) -> tuple[float, str]:
    text = " ".join(
        [
            row.get("title", ""),
            row.get("abstract", ""),
            row.get("subjects", ""),
            row.get("primary_subject", ""),
            row.get("comments", ""),
        ]
    ).lower()

    score = 0.0
    reasons = []
    positive_keywords = [kw.lower() for kw in profile.get("positive_keywords", [])]
    negative_keywords = [kw.lower() for kw in profile.get("negative_keywords", [])]

    matched_positive = [kw for kw in positive_keywords if keyword_in_text(text, kw)]
    matched_negative = [kw for kw in negative_keywords if keyword_in_text(text, kw)]

    if matched_positive:
        score += min(18.0, 2.0 * len(matched_positive))
        reasons.append(f"+topics:{', '.join(matched_positive[:6])}")
    if matched_negative:
        score -= min(14.0, 3.0 * len(matched_negative))
        reasons.append(f"-topics:{', '.join(matched_negative[:6])}")

    if "large language model" in text or "language model" in text or " llm" in f" {text}":
        score += 5.0
        reasons.append("+llm")
    if "agent" in text:
        score += 3.5
        reasons.append("+agent")
    if "retrieval" in text or "rag" in text:
        score += 3.0
        reasons.append("+retrieval")
    if "audio" in text or "speech" in text:
        score += 2.5
        reasons.append("+audio")
    if "neuroscience" in text or "cognition" in text:
        score += 2.5
        reasons.append("+neuro")
    if "human-computer interaction" in row.get("subjects", "").lower() and ("llm" in text or "language model" in text):
        score += 2.0
        reasons.append("+human-llm")
    if row.get("section", "").lower().startswith("new submissions"):
        score += 2.5
        reasons.append("+new")
    elif row.get("section", "").lower().startswith("cross-lists"):
        score += 0.5
    elif row.get("section", "").lower().startswith("replacements"):
        score -= 2.0

    if novelty_bucket == "today_new":
        score += 3.0
    elif novelty_bucket == "carryover_unreported":
        score += 1.0

    if "computer vision and pattern recognition" in row.get("subjects", "").lower() and not (
        "llm" in text or "language model" in text or "agent" in text
    ):
        score -= 4.0
        reasons.append("-cv-heavy")

    if "robot" in text and "language model" not in text and "llm" not in text:
        score -= 5.0

    return score, "; ".join(reasons[:8])


def keyword_in_text(text: str, keyword: str) -> bool:
    if not keyword:
        return False
    if re.fullmatch(r"[a-z0-9]+", keyword):
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def ensure_pdf_for_id(s: requests.Session, date_slug: str, row: dict[str, str]) -> Path:
    target = PDF_DIR / f"{date_slug}_{row['arxiv_id']}.pdf"
    if target.exists():
        return target

    existing = sorted(PDF_DIR.glob(f"*_{row['arxiv_id']}.pdf"))
    if existing:
        existing[0].rename(target)
        return target

    response = http_get(s, row["pdf_url"], timeout=120)
    target.write_bytes(response.content)
    return target


def ensure_first_page_image(pdf_path: Path) -> Path:
    target = FIRST_PAGE_DIR / f"{pdf_path.stem}.png"
    if target.exists():
        return target

    arxiv_id = pdf_path.stem.split("_", 1)[-1]
    existing = sorted(FIRST_PAGE_DIR.glob(f"*_{arxiv_id}.png"))
    if existing:
        existing[0].rename(target)
        return target

    render_pdf_first_page_image(pdf_path, target)
    return target


def render_pdf_first_page_image(pdf_path: Path, target: Path) -> None:
    tmp_dir = FIRST_PAGE_DIR / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    qlmanage = shutil.which("qlmanage")
    if qlmanage:
        cmd = [qlmanage, "-t", "-s", "2000", "-o", str(tmp_dir), str(pdf_path)]
        completed = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        rendered = tmp_dir / f"{pdf_path.name}.png"
        if completed.returncode == 0 and rendered.exists():
            rendered.replace(target)
            return

    sips = shutil.which("sips")
    if sips:
        cmd = [sips, "-s", "format", "png", str(pdf_path), "--out", str(target)]
        completed = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if completed.returncode == 0 and target.exists():
            return

    raise SystemExit(f"failed to render first-page image for {pdf_path}")


def extract_pdf_analysis(pdf_path: Path, authors_line: str = "") -> dict[str, str]:
    try:
        reader = PdfReader(str(pdf_path))
        pages = reader.pages[: min(len(reader.pages), 12)]
        page_texts = [(page.extract_text() or "") for page in pages]
        raw_text = "\n\n".join(page_texts)
    except Exception:
        return empty_pdf_analysis()

    first_page_text = page_texts[0] if page_texts else raw_text
    institution_hints = find_institution_hints(first_page_text)
    author_meta = extract_author_metadata(first_page_text, authors_line)
    snippet = textwrap.shorten(clean_text("\n".join(page_texts[:2])), width=500, placeholder=" ...")
    first_page_excerpt = textwrap.shorten(clean_text(first_page_text), width=1400, placeholder=" ...")
    return {
        "unit_text": institution_hints,
        "group_pi_text": author_meta["group_pi_text"],
        "cofirst_authors": author_meta["cofirst_authors"],
        "corresponding_authors": author_meta["corresponding_authors"],
        "author_role_notes": author_meta["author_role_notes"],
        "first_page_snippet": snippet,
        "first_page_text_excerpt": first_page_excerpt,
        "method_excerpt": extract_pdf_section_excerpt(
            raw_text,
            ["method", "methods", "approach", "framework", "architecture", "system overview", "algorithm"],
        ),
        "results_excerpt": extract_pdf_section_excerpt(
            raw_text,
            ["experiment", "experiments", "evaluation", "results", "benchmark", "benchmarks"],
        ),
        "limitations_excerpt": extract_pdf_section_excerpt(
            raw_text,
            ["limitations", "discussion", "conclusion", "future work"],
        ),
        "numeric_results": extract_numeric_sentences(clean_text(raw_text)),
    }

def fetch_html_analysis(s: requests.Session, html_url: str) -> dict[str, str]:
    if not html_url:
        return empty_html_analysis()
    try:
        response = http_get(s, html_url, timeout=60)
    except Exception:
        return empty_html_analysis()

    soup = BeautifulSoup(response.text, "html.parser")
    author_meta = extract_html_author_metadata(soup)
    heading_texts = []
    for tag in soup.find_all(["h2", "h3"]):
        text = clean_text(tag.get_text(" ", strip=True))
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"references", "acknowledgements", "appendix"}:
            continue
        if text not in heading_texts:
            heading_texts.append(text)
        if len(heading_texts) >= 8:
            break

    section_map = extract_html_sections(soup)
    paragraphs = []
    for tag in soup.find_all("p"):
        paragraph_text = clean_text(tag.get_text(" ", strip=True))
        if not paragraph_text or len(paragraph_text) < 80:
            continue
        paragraphs.append(paragraph_text)
        if sum(len(p) for p in paragraphs) >= 1800:
            break

    joined_paragraphs = " ".join(paragraphs[:4])
    all_text = " ".join(section_text for _, section_text in section_map)
    return {
        "section_titles": " | ".join(heading_texts[:6]),
        "intro_excerpt": textwrap.shorten(joined_paragraphs, width=1200, placeholder=" ..."),
        "method_excerpt": excerpt_from_sections(section_map, ["method", "approach", "framework", "architecture", "algorithm"]),
        "results_excerpt": excerpt_from_sections(section_map, ["experiment", "result", "evaluation", "benchmark", "analysis"]),
        "limitations_excerpt": excerpt_from_sections(section_map, ["limitation", "discussion", "future work", "conclusion"]),
        "numeric_results": extract_numeric_sentences(all_text),
        "author_block_text": author_meta["author_block_text"],
        "author_notes_text": author_meta["author_notes_text"],
        "unit_text": author_meta["unit_text"],
        "group_pi_text": author_meta["group_pi_text"],
        "cofirst_authors": author_meta["cofirst_authors"],
        "corresponding_authors": author_meta["corresponding_authors"],
        "author_role_notes": author_meta["author_role_notes"],
    }

def empty_html_analysis() -> dict[str, str]:
    return {
        "section_titles": "",
        "intro_excerpt": "",
        "method_excerpt": "",
        "results_excerpt": "",
        "limitations_excerpt": "",
        "numeric_results": "",
        "author_block_text": "",
        "author_notes_text": "",
        "unit_text": "",
        "group_pi_text": "",
        "cofirst_authors": "",
        "corresponding_authors": "",
        "author_role_notes": "",
    }

def empty_pdf_analysis() -> dict[str, str]:
    return {
        "unit_text": "",
        "group_pi_text": "",
        "cofirst_authors": "",
        "corresponding_authors": "",
        "author_role_notes": "",
        "first_page_snippet": "",
        "first_page_text_excerpt": "",
        "method_excerpt": "",
        "results_excerpt": "",
        "limitations_excerpt": "",
        "numeric_results": "",
    }

def extract_html_sections(soup: BeautifulSoup) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    for heading in soup.find_all(["h2", "h3"]):
        heading_text = clean_text(heading.get_text(" ", strip=True))
        if not heading_text:
            continue
        body_chunks = []
        for element in heading.next_elements:
            if element is heading:
                continue
            name = getattr(element, "name", None)
            if name in {"h2", "h3"}:
                break
            if name != "p":
                continue
            text = clean_text(element.get_text(" ", strip=True))
            if len(text) < 60:
                continue
            body_chunks.append(text)
            if sum(len(chunk) for chunk in body_chunks) >= 1200:
                break
        if body_chunks:
            sections.append((heading_text, " ".join(body_chunks)))
    return sections


def excerpt_from_sections(section_map: list[tuple[str, str]], keywords: list[str]) -> str:
    matched = []
    for heading, body in section_map:
        lowered = heading.lower()
        if any(keyword in lowered for keyword in keywords):
            matched.append(f"[{heading}] {body}")
        if len(matched) >= 2:
            break
    if not matched:
        return ""
    return textwrap.shorten(" ".join(matched), width=1400, placeholder=" ...")


def extract_pdf_section_excerpt(raw_text: str, keywords: list[str]) -> str:
    if not raw_text:
        return ""
    for keyword in keywords:
        pattern = re.compile(
            rf"(?is)(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*(?:[A-Z][^\n]{{0,30}}\s+)?{re.escape(keyword)}\b"
        )
        match = pattern.search(raw_text)
        if not match:
            continue
        start = match.start()
        window = raw_text[start : start + 2600]
        cleaned = clean_text(window)
        if len(cleaned) < 80:
            continue
        return textwrap.shorten(cleaned, width=1400, placeholder=" ...")
    return ""


def extract_numeric_sentences(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", clean_text(text))
    picked = []
    for sentence in sentences:
        if len(sentence) < 40:
            continue
        if not re.search(r"\d", sentence):
            continue
        if re.search(r"%|percent|points|point|f1|accuracy|em|wer|latency|seconds|xspeedup|pass@|top-k|top k", sentence, re.IGNORECASE):
            picked.append(sentence)
        if len(picked) >= 4:
            break
    return "\n".join(f"- {textwrap.shorten(sentence, width=260, placeholder=' ...')}" for sentence in picked)


def prefer_excerpt(primary: str, fallback: str) -> str:
    return primary or fallback


def merge_result_bullets(primary: str, fallback: str) -> str:
    lines = []
    seen = set()
    for block in (primary, fallback):
        for raw_line in block.splitlines():
            line = clean_text(raw_line)
            if not line:
                continue
            if line in seen:
                continue
            seen.add(line)
            lines.append(line)
            if len(lines) >= 6:
                return "\n".join(lines)
    return "\n".join(lines)


def merge_author_metadata(primary: dict[str, str], fallback: dict[str, str]) -> dict[str, str]:
    return {
        "unit_text": merge_pipe_values(primary.get("unit_text", ""), fallback.get("unit_text", "")),
        "group_pi_text": merge_pipe_values(primary.get("group_pi_text", ""), fallback.get("group_pi_text", "")),
        "cofirst_authors": merge_list_values(primary.get("cofirst_authors", ""), fallback.get("cofirst_authors", "")),
        "corresponding_authors": merge_list_values(
            primary.get("corresponding_authors", ""),
            fallback.get("corresponding_authors", ""),
        ),
        "author_role_notes": merge_pipe_values(
            primary.get("author_role_notes", ""),
            fallback.get("author_role_notes", ""),
        ),
    }


def merge_pipe_values(*values: str) -> str:
    parts = []
    seen = set()
    for value in values:
        for raw_part in value.split("|"):
            part = clean_text(raw_part.strip())
            if not part:
                continue
            if part in seen:
                continue
            seen.add(part)
            parts.append(part)
    return " | ".join(parts)


def merge_list_values(*values: str) -> str:
    parts = []
    seen = set()
    for value in values:
        for raw_part in re.split(r"[、|]", value):
            part = clean_text(raw_part)
            if not part:
                continue
            if part in seen:
                continue
            seen.add(part)
            parts.append(part)
    return "、".join(parts)


def detect_term_notes(title: str, text: str) -> str:
    found = []
    seen = set()

    for term, explanation in extract_defined_terms(title, text):
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(f"- {term}：{explanation}")
        if len(found) >= 3:
            break

    lowered = text.lower()
    for term, explanation in TERM_GLOSSARY.items():
        key = term.lower()
        if key in seen:
            continue
        if keyword_in_text(lowered, key):
            seen.add(key)
            found.append(f"- {term}: {explanation}")
        if len(found) >= 8:
            break
    return "\n".join(found)


def extract_defined_terms(title: str, text: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen = set()

    title_prefix = title.split(":", 1)[0].strip() if ":" in title else ""
    if title_prefix and looks_like_named_method(title_prefix):
        candidates.append((title_prefix, "标题中的方法或系统名称。"))
        seen.add(title_prefix.lower())

    for term, explanation in extract_named_components(text):
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append((term, explanation))
        if len(candidates) >= 4:
            return candidates

    patterns = [
        re.compile(r"\b([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{2,80}?)\s+\(([A-Z][A-Z0-9\-]{1,12})\)"),
        re.compile(r"\b([A-Z][A-Z0-9\-]{1,12})\s+\(([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{2,80}?)\)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            left = clean_text(match.group(1))
            right = clean_text(match.group(2))
            if pattern is patterns[0]:
                full_name, short_name = left, right
            else:
                short_name, full_name = left, right
            if len(short_name) < 2 or len(full_name) < 4:
                continue
            if short_name.lower() in seen:
                continue
            if not looks_like_term_name(short_name):
                continue
            seen.add(short_name.lower())
            candidates.append((short_name, f"{full_name}，文中定义的术语或方法名。"))
            if len(candidates) >= 4:
                return candidates

    for dataset in extract_dataset_terms(text):
        key = dataset.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append((dataset, "文中使用的 benchmark、数据集或评测集合。"))
        if len(candidates) >= 5:
            break
    return candidates


def extract_named_components(text: str) -> list[tuple[str, str]]:
    candidates = []
    seen = set()
    patterns = [
        re.compile(
            r"\b([A-Z][A-Za-z0-9\-]+(?:\s+[A-Z][A-Za-z0-9\-]+){1,4}\s+(?:Framework|Strategy|Scoring|Search|Retriever|Merging|Masking|Checker|Benchmark|Navigator|Copilot))\b"
        ),
        re.compile(r"\b(?:propose|introduce|present)\s+([A-Z][A-Za-z0-9\-]{1,20})\b", re.IGNORECASE),
    ]
    banned = {
        "Large Language Models",
        "Model Context Protocol",
        "Mean Reciprocal Rank",
        "Exact Match",
        "Human Computer Interaction",
        "study",
        "Study",
    }
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = clean_text(match.group(1))
            if len(value) < 3 or value in banned:
                continue
            key = value.lower()
            if key in seen:
                continue
            if any(key in existing for existing in seen):
                continue
            seen.add(key)
            candidates.append((value, "文中提出的模块、策略、框架或方法名。"))
            if len(candidates) >= 4:
                return candidates
    return candidates


def looks_like_named_method(text: str) -> bool:
    if not text or len(text.split()) > 6:
        return False
    return bool(re.search(r"[A-Z]", text)) and (
        bool(re.search(r"[a-z][A-Z]", text))
        or bool(re.fullmatch(r"[A-Z][A-Z0-9\-]{1,12}", text))
        or len(text.split()) <= 3
    )


def looks_like_term_name(text: str) -> bool:
    if not text:
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9\-]{1,12}", text))


def extract_dataset_terms(text: str) -> list[str]:
    candidates = []
    banned = {"for", "and", "our", "with", "the", "this", "that", "these", "those", "dataset", "benchmark"}
    for pattern in [
        r"\b(?:benchmark|dataset|datasets|benchmarks)\s+(?:called|named)?\s*([A-Z][A-Za-z0-9\-/]+)\b",
        r"\b([A-Z][A-Za-z0-9\-/]+)\s+(?:benchmark|dataset)\b",
        r"\b(?:evaluate on|evaluated on|results on|tested on)\s+([A-Z][A-Za-z0-9\-/]+)\b",
    ]:
        for match in re.finditer(pattern, text):
            value = clean_text(match.group(1))
            if len(value) < 3 or value.lower() in banned:
                continue
            if not re.search(r"[A-Z0-9\-]", value):
                continue
            if value not in candidates:
                candidates.append(value)
            if len(candidates) >= 3:
                return candidates
    return candidates


def extract_html_author_metadata(soup: BeautifulSoup) -> dict[str, str]:
    authors_node = soup.select_one(".ltx_authors")
    if authors_node is None:
        return {
            "author_block_text": "",
            "author_notes_text": "",
            "unit_text": "",
            "group_pi_text": "",
            "cofirst_authors": "",
            "corresponding_authors": "",
            "author_role_notes": "",
        }

    author_text = clean_text(authors_node.get_text(" ", strip=True))
    note_text = " ".join(clean_text(node.get_text(" ", strip=True)) for node in soup.select(".ltx_author_notes, .ltx_note_content, .ltx_note"))
    address_texts = []
    for node in soup.select(".ltx_contact.ltx_role_address, .ltx_role_address"):
        text = sanitize_unit_hint(clean_text(node.get_text(" ", strip=True)))
        if not text:
            continue
        address_texts.append(normalize_unit_name(text))
    unit_text = merge_pipe_values(" | ".join(address_texts), find_institution_hints(author_text))
    group_lines = extract_group_lines(author_text + "\n" + note_text)
    group_pi_text = "课题组/中心：" + " | ".join(group_lines[:2]) if group_lines else ""
    role_notes = []
    lowered = (author_text + " " + note_text).lower()
    if "equal contribution" in lowered or "contributed equally" in lowered or "co-first" in lowered:
        role_notes.append("HTML 作者注释提到共同一作")
    if "corresponding author" in lowered or "correspondence to" in lowered:
        role_notes.append("HTML 作者注释提到通讯作者")
    return {
        "author_block_text": author_text,
        "author_notes_text": note_text,
        "unit_text": unit_text,
        "group_pi_text": group_pi_text,
        "cofirst_authors": "",
        "corresponding_authors": "",
        "author_role_notes": "；".join(role_notes),
    }

def find_institution_hints(text: str) -> str:
    lines = text.splitlines()
    hints = []
    pattern = re.compile(
        r"\b(University|Institute|Laboratory|Laboratories|Lab|School|College|OpenAI|Anthropic|Google|Microsoft|Meta|NVIDIA|DeepMind|AI Research)\b",
        re.IGNORECASE,
    )
    for line in lines[:40]:
        line = clean_text(line)
        if not line or len(line) > 160:
            continue
        if len(line.split()) > 12:
            continue
        if not pattern.search(line):
            continue
        line = sanitize_unit_hint(line)
        if not line:
            continue
        if not looks_like_unit_line(line):
            continue
        hints.append(line)
        if len(hints) >= 4:
            break
    unique = []
    for item in hints:
        if item not in unique:
            unique.append(item)
    return " | ".join(normalize_unit_name(item) for item in unique[:3])


def sanitize_unit_hint(line: str) -> str:
    line = re.sub(r"^[\d.*\s]+", "", line)
    line = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b.*", "", line)
    line = re.split(
        r"\b(Abstract|ABSTRACT|Code, model|Code is available|available at|We evaluate|evaluation|Published as|Preprint|arXiv:|Keywords|Corresponding author)\b",
        line,
        1,
    )[0]
    line = clean_text(line.strip(" |,;:-"))
    return line


def extract_author_metadata(first_page_text: str, authors_line: str = "") -> dict[str, str]:
    normalized_text = normalize_pdf_text(first_page_text)
    authors = split_authors_from_csv(authors_line) or split_authors_from_header(normalized_text)
    author_marks = match_author_markers(normalized_text, authors)
    lower = normalized_text.lower()

    cofirst = []
    corresponding = []
    role_notes = []

    equal_contrib = "contributed equally" in lower or "equal contribution" in lower or "co-first" in lower
    if equal_contrib:
        for author, marks in author_marks.items():
            if has_single_star(marks):
                cofirst.append(author)

    corr_match = re.search(r"Correspondence to:\s*([^<\n.]+)", normalized_text, re.IGNORECASE)
    if corr_match:
        corr_name = clean_text(corr_match.group(1))
        matched_name = closest_author_name(corr_name, authors)
        corresponding.append(matched_name or corr_name)

    if ("corresponding authors" in lower or "corresponding author" in lower) and not corresponding:
        for author, marks in author_marks.items():
            if has_double_star(marks):
                corresponding.append(author)

    if "equal advising" in lower or "equal advisor" in lower:
        advising = [author for author, marks in author_marks.items() if has_single_star(marks)]
        if advising:
            role_notes.append("作者脚注含 Equal Advising：" + "、".join(advising))
            if not equal_contrib:
                cofirst = []

    cofirst = dedupe_preserve(cofirst)
    corresponding = dedupe_preserve(corresponding)

    group_lines = extract_group_lines(normalized_text)
    return {
        "cofirst_authors": "、".join(cofirst),
        "corresponding_authors": "、".join(corresponding),
        "author_role_notes": "；".join(role_notes),
        "group_pi_text": "课题组/中心：" + " | ".join(group_lines[:2]) if group_lines else "",
    }


def split_authors_from_header(text: str) -> list[str]:
    header = text.split("Abstract", 1)[0]
    lines = [clean_text(line) for line in header.splitlines() if clean_text(line)]
    authors = []
    for line in lines[:8]:
        if re.search(r"University|Institute|Laboratory|School|College|Department|@|Abstract", line, re.IGNORECASE):
            continue
        if line.count(",") == 0:
            continue
        if len(line) > 240:
            continue
        parts = [clean_text(part.strip(" *∗†‡0123456789")) for part in line.split(",")]
        filtered = [part for part in parts if looks_like_person_name(part)]
        if len(filtered) >= 2:
            authors = filtered
            break
    return authors


def split_authors_from_csv(text: str) -> list[str]:
    return [clean_text(part) for part in text.split(",") if clean_text(part)]


def looks_like_person_name(text: str) -> bool:
    if not text:
        return False
    words = text.split()
    if len(words) < 2 or len(words) > 5:
        return False
    return all(re.match(r"^[A-Z][A-Za-z'.\-]+$", word) for word in words)


def match_author_markers(text: str, authors: list[str]) -> dict[str, str]:
    marks = {}
    for author in authors:
        pattern = re.compile(rf"{author_name_regex(author)}\s*([*∗†‡, ]{{0,8}})")
        match = pattern.search(text)
        if match:
            marks[author] = match.group(1)
    return marks


def author_name_regex(name: str) -> str:
    return r"\s+".join(re.escape(part) for part in name.split())


def has_single_star(marks: str) -> bool:
    return bool(re.search(r"(^|[,\s])(?:\*|∗)(?=($|[,\s]))", marks))


def has_double_star(marks: str) -> bool:
    return "**" in marks or "∗∗" in marks


def closest_author_name(name: str, authors: list[str]) -> str:
    target = normalize_name(name)
    for author in authors:
        if normalize_name(author) == target:
            return author
    for author in authors:
        if target and target in normalize_name(author):
            return author
    return ""


def normalize_name(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def normalize_pdf_text(text: str) -> str:
    normalized = re.sub(r"([A-Za-z])-\s+([A-Za-z])", r"\1\2", text)
    return normalized


def extract_group_lines(first_page_text: str) -> list[str]:
    candidates = []
    pattern = re.compile(r"\b(Lab(?:oratory)?|Group|Center|Centre|Program|Programme|Institute|School of|Department of)\b", re.IGNORECASE)
    for raw_line in first_page_text.splitlines()[:20]:
        line = sanitize_unit_hint(clean_text(raw_line))
        if not line or len(line) > 180:
            continue
        if len(line.split()) > 14:
            continue
        if not pattern.search(line):
            continue
        if line not in candidates:
            candidates.append(normalize_unit_name(line))
        if len(candidates) >= 2:
            break
    return candidates


def build_source_verification(
    abstract_text: str,
    html_url: str,
    html_analysis: dict[str, str],
    pdf_analysis: dict[str, str],
    first_page_image_path: str,
) -> str:
    parts = []
    parts.append("摘要：已抓取" if abstract_text else "摘要：缺失")
    html_ok = bool(html_url and (html_analysis["section_titles"] or html_analysis["intro_excerpt"]))
    parts.append("HTML：已核对" if html_ok else "HTML：缺失或无可用正文")
    pdf_ok = bool(pdf_analysis["first_page_snippet"])
    parts.append("PDF：已核对" if pdf_ok else "PDF：缺失")
    parts.append("PDF 首页图：已生成" if first_page_image_path else "PDF 首页图：缺失")
    parts.append("作者信息来源：" + author_source_label(html_analysis, pdf_analysis))
    parts.append("方法来源：" + source_label(html_analysis["method_excerpt"], pdf_analysis["method_excerpt"]))
    parts.append("结果来源：" + source_label(html_analysis["results_excerpt"], pdf_analysis["results_excerpt"]))
    parts.append("局限性来源：" + source_label(html_analysis["limitations_excerpt"], pdf_analysis["limitations_excerpt"]))
    return " | ".join(parts)


def author_source_label(html_analysis: dict[str, str], pdf_analysis: dict[str, str]) -> str:
    html_ok = bool(
        html_analysis.get("unit_text")
        or html_analysis.get("group_pi_text")
        or html_analysis.get("author_role_notes")
    )
    pdf_ok = bool(
        pdf_analysis.get("unit_text")
        or pdf_analysis.get("group_pi_text")
        or pdf_analysis.get("cofirst_authors")
        or pdf_analysis.get("corresponding_authors")
        or pdf_analysis.get("author_role_notes")
    )
    if html_ok and pdf_ok:
        return "HTML + PDF"
    if html_ok:
        return "HTML"
    if pdf_ok:
        return "PDF"
    return "未可靠定位"


def source_label(html_text: str, pdf_text: str) -> str:
    if html_text:
        return "HTML"
    if pdf_text:
        return "PDF"
    return "未可靠定位"


def looks_like_unit_line(line: str) -> bool:
    lowered = line.lower()
    banned = [
        "repository",
        "benchmark",
        "abstract",
        "https",
        "providing all available tools",
        "available at this",
        "gives rise to",
        "code is available",
    ]
    if any(token in lowered for token in banned):
        return False
    return True


def normalize_unit_name(line: str) -> str:
    normalized = clean_text(line)
    for alias, canonical in UNIT_CANONICAL_ALIASES.items():
        normalized = normalized.replace(alias, canonical)
    return normalized

def split_name_list(text: str) -> list[str]:
    return [clean_text(part) for part in re.split(r"[、|,;/]", text or "") if clean_text(part)]


def write_context_md(date_slug: str, papers: list[Paper], candidates: list[dict[str, str]], previous_date: str | None, state: dict) -> None:
    sections = Counter(p.section for p in papers)
    buckets = Counter(row["novelty_bucket"] for row in candidates)
    top_candidates = candidates[:40]

    lines = [
        f"# arXiv CS 上下文 {date_slug}",
        "",
        f"- 今日抓取总数：{len(papers)}",
        f"- 前一天完整 CSV：{previous_date or '无'}",
        f"- 已经写进日报状态的论文数：{len(state.get('reported_ids', {}))}",
        "",
        "## 分区统计",
        "",
    ]
    for section, count in sections.items():
        lines.append(f"- {section}: {count}")

    lines.extend(["", "## 候选分桶", ""])
    for bucket, count in buckets.items():
        lines.append(f"- {bucket}: {count}")

    lines.extend(["", "## 重点候选", ""])
    for row in top_candidates:
        lines.append(
            f"- {row['arxiv_id']} | {row['novelty_bucket']} | score={row['heuristic_score']} | {row['title']}"
        )
        lines.append(f"  subjects: {row['subjects']}")
        lines.append(f"  reasons: {row['heuristic_reasons']}")

    context_md_path(date_slug).write_text("\n".join(lines) + "\n")


def write_materialized_md(date_slug: str, entries: list[dict[str, str]]) -> None:
    lines = [f"# 深读素材 {date_slug}", ""]
    for entry in entries:
        lines.append(f"## {entry['title']} ({entry['arxiv_id']})")
        lines.append("")
        lines.append(f"- 新鲜度：{entry['novelty_bucket']}")
        lines.append(f"- 原始作者列表（来自 list 页面）：{entry['authors_csv'] or '无'}")
        lines.append(f"- abs：{entry['abs_url']}")
        lines.append(f"- pdf：{entry['pdf_path']}")
        lines.append(f"- pdf 首页图：{entry['first_page_image_path']}")
        lines.append(f"- html：{entry['html_url'] or '无'}")
        lines.append(f"- 启发式分数：{entry['heuristic_score']} | {entry['heuristic_reasons']}")
        lines.append(f"- 来源校验：{entry['source_verification']}")
        lines.append("")
        lines.append("### 作者与单位证据（请据此做最终判断）")
        lines.append("")
        lines.append(f"- HTML 作者区块原文：{entry['html_author_block_text'] or '无'}")
        lines.append(f"- HTML 作者注释原文：{entry['html_author_notes_text'] or '无'}")
        lines.append(f"- HTML 单位候选：{entry['html_unit_candidates'] or '无'}")
        lines.append(f"- PDF 首页单位候选：{entry['pdf_unit_candidates'] or '无'}")
        lines.append(f"- PDF 首页文本摘录：{entry['first_page_text_excerpt'] or '无'}")
        lines.append(f"- 机器辅助线索（仅供核对）：共一候选={entry['machine_cofirst_candidates'] or '无'}；通讯候选={entry['machine_corresponding_candidates'] or '无'}；组/PI 候选={entry['machine_group_candidates'] or '无'}；脚注补充={entry['machine_role_notes'] or '无'}")
        lines.append("")
        lines.append("### 首页摘要")
        lines.append("")
        lines.append(entry["list_abstract"] or "无")
        lines.append("")
        lines.append("### HTML 章节")
        lines.append("")
        lines.append(entry["html_section_titles"] or "无")
        lines.append("")
        lines.append("### HTML 导读摘录")
        lines.append("")
        lines.append(entry["html_intro_excerpt"] or "无")
        lines.append("")
        lines.append("### 方法机制摘录（优先 HTML，缺失时回退 PDF）")
        lines.append("")
        lines.append(entry["method_excerpt"] or "无")
        lines.append("")
        lines.append("### 结果摘录（优先 HTML，缺失时回退 PDF）")
        lines.append("")
        lines.append(entry["results_excerpt"] or "无")
        lines.append("")
        lines.append("### 数字结果候选（合并 HTML / PDF）")
        lines.append("")
        lines.append(entry["numeric_results"] or "无")
        lines.append("")
        lines.append("### 局限性摘录（优先 HTML，缺失时回退 PDF）")
        lines.append("")
        lines.append(entry["limitations_excerpt"] or "无")
        lines.append("")
        lines.append("### PDF 首段摘录")
        lines.append("")
        lines.append(entry["first_page_snippet"] or "无")
        lines.append("")
        lines.append("### 术语解释候选")
        lines.append("")
        lines.append(entry["term_notes"] or "无")
        lines.append("")
    materialized_md_path(date_slug).write_text("\n".join(lines))

REPORT_HEADING_ID_RE = re.compile(r"^### .*?`(\d{4}\.\d{4,5}(?:v\d+)?)`", re.MULTILINE)
GENERIC_ID_RE = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")


def normalize_arxiv_id(value: str) -> str:
    return re.sub(r"v\d+$", "", clean_text(value))


def extract_report_selected_ids(report_text: str) -> list[str]:
    heading_ids = [normalize_arxiv_id(match.group(1)) for match in REPORT_HEADING_ID_RE.finditer(report_text)]
    if heading_ids:
        return dedupe_preserve(heading_ids)
    fallback_ids = [normalize_arxiv_id(match.group(0)) for match in GENERIC_ID_RE.finditer(report_text)]
    return dedupe_preserve(fallback_ids)


def sync_selected_ids_from_report(date_slug: str) -> list[str]:
    report_path = report_md_path(date_slug)
    if not report_path.exists():
        raise SystemExit(f"missing report file: {report_path}")
    selected_ids = extract_report_selected_ids(report_path.read_text())
    if not selected_ids:
        raise SystemExit("failed to extract any arXiv ids from the report headings")
    selected_ids_path(date_slug).write_text("\n".join(selected_ids) + "\n")
    return selected_ids


def failure_note_path(date_slug: str) -> Path:
    return MD_DIR / f"arxiv_cs_{date_slug}_failure.md"


def write_failure_note(date_slug: str, step: str, error_text: str) -> None:
    raw_files = sorted(path.name for path in RAW_DIR.glob(f"arxiv_cs_{date_slug}_list_*.html"))
    lines = [
        f"# arXiv CS Digest Failure {date_slug}",
        "",
        f"- step: {step}",
        f"- error: {error_text}",
    ]
    if raw_files:
        lines.append(f"- raw listing html: {', '.join(raw_files[:3])}")
    failure_note_path(date_slug).write_text("\n".join(lines) + "\n")


def clear_failure_note(date_slug: str) -> None:
    path = failure_note_path(date_slug)
    if path.exists():
        path.unlink()


def format_error(exc: BaseException) -> str:
    message = clean_text(str(exc))
    if message:
        return message
    return exc.__class__.__name__


def smoke_fixture_path() -> Path:
    return SKILL_ROOT / "tests" / "fixtures" / "arxiv_list_sample.html"


def run_smoke_test() -> None:
    fixture = smoke_fixture_path()
    if not fixture.exists():
        raise SystemExit(f"missing smoke-test fixture: {fixture}")
    total_entries, papers = parse_listing_page(fixture.read_text(encoding="utf-8"), "000000", 0, 0)
    if total_entries != 1 or len(papers) != 1:
        raise SystemExit("smoke-test failed: listing parser did not recover the expected number of papers")
    if papers[0].arxiv_id != "2603.12345":
        raise SystemExit("smoke-test failed: parsed arXiv id does not match fixture")
    synced_ids = extract_report_selected_ids(
        "### 1. Alpha (`2603.12345`)\n\n### 2. Beta (`2603.54321v2`)\n"
    )
    if synced_ids != ["2603.12345", "2603.54321"]:
        raise SystemExit("smoke-test failed: report selection sync logic is broken")
    if normalize_unit_name("UIUC") != "University of Illinois Urbana-Champaign":
        raise SystemExit("smoke-test failed: unit alias normalization is broken")
    if normalize_unit_name("Example Institute of Interesting Studies") != "Example Institute of Interesting Studies":
        raise SystemExit("smoke-test failed: unknown units should remain unchanged")
    print(json.dumps({
        "listing_total": total_entries,
        "fixture_paper_id": papers[0].arxiv_id,
        "synced_ids": synced_ids,
        "unit_normalization": "ok",
    }, ensure_ascii=False, indent=2))

def build_status(date_slug: str) -> dict[str, object]:
    report_exists = report_md_path(date_slug).exists()
    selected_exists = selected_ids_path(date_slug).exists()
    desktop_exists = (DESKTOP_DIR / report_md_path(date_slug).name).exists()
    state = load_state()
    reported_today = state.get("reported_by_day", {}).get(date_slug, [])
    done = report_exists and selected_exists and desktop_exists and bool(reported_today)
    return {
        "date": date_slug,
        "done": done,
        "report_exists": report_exists,
        "selected_ids_exists": selected_exists,
        "desktop_report_exists": desktop_exists,
        "reported_by_day_count": len(reported_today),
    }


def purge_oldest_day_once() -> None:
    dates = sorted(available_day_slugs())
    if len(dates) <= 7:
        return
    oldest = dates[0]
    paths = [
        full_csv_path(oldest),
        candidate_csv_path(oldest),
        context_md_path(oldest),
        materialized_md_path(oldest),
        report_md_path(oldest),
        selected_ids_path(oldest),
        failure_note_path(oldest),
    ]
    for path in paths:
        if path.exists():
            path.unlink()
    for pdf in PDF_DIR.glob(f"{oldest}_*.pdf"):
        pdf.unlink()
    for image in FIRST_PAGE_DIR.glob(f"{oldest}_*.png"):
        image.unlink()
    for raw_file in RAW_DIR.glob(f"arxiv_cs_{oldest}_list_*.html"):
        raw_file.unlink()

def available_day_slugs() -> set[str]:
    dates = set()
    for path in CSV_DIR.glob("arxiv_cs_*.csv"):
        match = CSV_RE.match(path.name)
        if match:
            dates.add(match.group(1))
    for path in MD_DIR.glob("arxiv_cs_*.md"):
        match = re.match(r"^arxiv_cs_(\d{6})(?:_context|_materialized|_failure)?\.md$", path.name)
        if match:
            dates.add(match.group(1))
    for path in PDF_DIR.glob("*.pdf"):
        match = PDF_RE.match(path.name)
        if match:
            dates.add(match.group(1))
    for path in RAW_DIR.glob("arxiv_cs_*.html"):
        match = RAW_RE.match(path.name)
        if match:
            dates.add(match.group(1))
    return dates

def load_recent_full_rows() -> dict[str, dict[str, str]]:
    rows = {}
    for date_slug in sorted(available_day_slugs(), reverse=True):
        path = full_csv_path(date_slug)
        if not path.exists():
            continue
        for row in read_csv_rows(path):
            rows.setdefault(row["arxiv_id"], row)
    return rows


def previous_full_date(current_date: str) -> str | None:
    candidates = []
    for path in CSV_DIR.glob("arxiv_cs_*.csv"):
        match = CSV_RE.match(path.name)
        if not match:
            continue
        date_slug = match.group(1)
        if date_slug < current_date:
            candidates.append(date_slug)
    return sorted(candidates)[-1] if candidates else None


def dedupe_preserve(values: Iterable[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def clean_labelled_text(node, label: str) -> str:
    if node is None:
        return ""
    text = clean_text(node.get_text(" ", strip=True))
    return text.replace(label, "", 1).strip()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def raw_listing_path(date_slug: str, offset: int) -> Path:
    return RAW_DIR / f"arxiv_cs_{date_slug}_list_{offset:04d}.html"

def full_csv_path(date_slug: str) -> Path:
    return CSV_DIR / f"arxiv_cs_{date_slug}.csv"


def candidate_csv_path(date_slug: str) -> Path:
    return CSV_DIR / f"arxiv_cs_{date_slug}_candidates.csv"


def context_md_path(date_slug: str) -> Path:
    return MD_DIR / f"arxiv_cs_{date_slug}_context.md"


def materialized_md_path(date_slug: str) -> Path:
    return MD_DIR / f"arxiv_cs_{date_slug}_materialized.md"


def report_md_path(date_slug: str) -> Path:
    return MD_DIR / f"arxiv_cs_{date_slug}.md"


def selected_ids_path(date_slug: str) -> Path:
    return MD_DIR / f"arxiv_cs_{date_slug}_selected_ids.txt"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
