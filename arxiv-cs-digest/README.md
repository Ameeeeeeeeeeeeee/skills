# arxiv-cs-digest

Personal Codex skill for running a daily arXiv CS digest workflow and writing a Chinese markdown briefing.

## Install

Clone this repo to your Codex skills directory:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone git@github.com:Ameeeeeeeeeeeeee/codex-skill-arxiv-cs-digest.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest"
```

If you prefer HTTPS:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone https://github.com/Ameeeeeeeeeeeeee/codex-skill-arxiv-cs-digest.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest"
```

The skill writes runtime data to:

```bash
${ARXIV_CS_DIGEST_HOME:-${CODEX_HOME:-$HOME/.codex}/data/arxiv-cs-digest}
```

The final daily markdown is synced to:

```bash
$HOME/Desktop
```

## Recommended Runner

The examples in `SKILL.md` assume:

```bash
conda run -n hybrid python
```

You can use another Python launcher if you want, but it must run the same script path.

## `hybrid` Environment Requirements

Minimum Python requirement:

- `python >= 3.11`

Reason:

- `scripts/run_digest.py` uses `tomllib`, which is part of the Python standard library starting from Python 3.11.

Required third-party libraries in `hybrid`:

- `requests`
- `beautifulsoup4`
- `pypdf`

One way to prepare the environment:

```bash
conda create -n hybrid python=3.11 -y
conda run -n hybrid python -m pip install requests beautifulsoup4 pypdf
```

## Smoke Test

After cloning and preparing `hybrid`, run:

```bash
conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" doctor
conda run -n hybrid python "${CODEX_HOME:-$HOME/.codex}/skills/arxiv-cs-digest/scripts/run_digest.py" status
```

If both commands work, the skill is installed correctly.
