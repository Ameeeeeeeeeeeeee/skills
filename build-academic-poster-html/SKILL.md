---
name: build-academic-poster-html
description: Create or refine polished, print-size academic research posters as self-contained HTML using a dense but readable blue card-and-column visual system. Use when converting a paper, preprint, abstract, figures, or experimental results into an HTML poster; adapting an existing poster to a new physical size or aspect ratio; fixing poster typography, wrapping, whitespace, section balance, tables, or architecture diagrams; or visually QAing browser-rendered poster HTML before print or PDF export.
---

# Build Academic Poster HTML

Build browser-rendered academic posters that remain information-rich, legible at viewing distance, and visually balanced. Preserve the reference style's navy-to-blue header, icon-led section titles, compact cards, central method emphasis, strong metric hierarchy, and disciplined whitespace without preserving paper-specific content.

## Load the bundled resources

- Read [references/style-system.md](references/style-system.md) before creating or substantially redesigning a poster.
- Read [references/size-adaptation.md](references/size-adaptation.md) whenever dimensions or orientation differ from the source poster, or when the user has not specified a size.
- Start new work from [assets/poster-template.html](assets/poster-template.html). Treat its bracketed copy as placeholders, not required content.
- Inspect [references/reference-poster.html](references/reference-poster.html) when high-fidelity reproduction of the finished style, CSS, or component construction is useful.
- Inspect [references/reference-poster.pdf](references/reference-poster.pdf) as the manually exported visual reference for composition, density, and browser-to-print fidelity. Its supporting image is retained under `references/assets/` so the reference HTML remains self-contained within the Skill.
- When editing an existing HTML poster, modify that file directly and use the template only as a component and styling reference.

The reference poster demonstrates the style, not reusable scientific content. Never carry its title, authors, affiliations, paper claims, measurements, dataset values, or repository URL into a different poster unless the user explicitly supplies the same information.

## Workflow

### 1. Inspect the inputs

Identify:

- target physical width, height, units, and orientation;
- authoritative source materials such as paper text, figures, tables, existing HTML, or screenshots;
- required venue, authors, affiliations, code link, QR code, acknowledgements, and footer details;
- the single primary claim, main method diagram, strongest quantitative result, and best qualitative evidence.

If dimensions are absent, infer them from an existing artifact or ask only when the choice materially affects delivery. Otherwise use 100 × 75 cm landscape as a preview baseline, clearly treating it as a default rather than a constraint.

### 2. Build the story map

Use this default narrative for a standard landscape poster, then adapt it to the paper:

| Region | Default role |
| --- | --- |
| Header | Title, one-line promise, authors/affiliations, one compact link, one headline metric |
| Left | Abstract or introduction, motivation, contributions, qualitative evidence, retrieval/use workflow |
| Center | Architecture, training-only supervision branch, representation mechanics, training data and evaluation setup |
| Right | Headline results, main table, efficiency, two-stage result, ablation or interpretation, deployment scope |

Place the architecture in the visual center even if the paper explains it earlier or later. Place interpretability near qualitative evidence when it helps visitors understand the representation before reading the results. Keep paper-order fidelity secondary to a clear poster reading path.

### 3. Establish dimensions before styling content

Set `--poster-width` and `--poster-height`, update the explicit `@page` size, choose the aspect-ratio layout, and calibrate the typography clamps before filling sections. Follow `references/size-adaptation.md`.

Do not solve a size change by applying a global browser transform. Recompute the header, body, columns, font scale, gaps, and section allocation so printed geometry remains correct.

### 4. Implement the content hierarchy

- Write a concise title and a one-line subtitle that states the retrieval or scientific promise.
- Combine abstract and introduction when space is limited.
- Separate motivation from contributions.
- Prefer short sentences over semicolon chains.
- Use tables for exact repeated metrics and cards for comparisons or ablations.
- Build architecture diagrams in HTML/CSS or SVG so labels remain sharp.
- Draw training-only teachers as side branches that align with student outputs. Never place a distillation teacher on the inference path.
- Use symmetric cards, equal internal heights, centered token grids, and annotated arrows.
- Use semantic HTML for tables, headings, superscripts, subscripts, links, and figure captions.

### 5. Fit content without making it tiny

Use automatic wrapping first. Improve copy length before forcing line breaks. Remove a few words or add useful detail so blocks fill their width naturally; avoid a full first line followed by one or two orphaned words.

Maintain these priorities:

1. Keep primary prose at the large body size.
2. Keep secondary prose at or above the readable floor in the size guide.
3. Reduce padding and redundant wording before shrinking fonts.
4. Keep each section internally compact.
5. Distribute only the gaps between sections to fill each column height.

### 6. Perform visual QA in the browser

Treat the HTML as authoritative. Do not generate or inspect a PDF unless the user asks.

1. Open or refresh the existing browser page. If the user is already viewing the file in Edge, operate that page rather than typing a new URL.
2. Fit the full poster in the window; 25% is a useful baseline for a 100 × 75 cm poster, not a universal zoom.
3. Inspect the full composition, then crop or zoom the header, architecture, densest table, bottom sections, and any recently changed detail.
4. Force reload when a local file appears cached.
5. Verify no clipping, overflow, border collision, missing arrow, broken image, or bottom truncation.
6. Verify header metadata is optically centered, section titles remain one line, cards are symmetric, and text does not create ugly short spill lines.
7. Rebalance copy or spacing and repeat until the actual rendered page is clean.

## Editing rules

- Preserve user changes in a dirty workspace and make focused edits.
- Use the existing HTML rather than editing a derived PDF.
- Keep all text, diagrams, and data editable.
- Prefer CSS variables and reusable classes over one-off inline styles.
- Use icons directly before section titles; do not place letters or numbers inside colored title badges.
- Render superscripts and subscripts smaller, lighter, and italic.
- Keep external links minimal. A GitHub icon plus repository path is enough when no QR code is required.
- Keep the center column slightly wider than the outer columns for method-heavy landscape posters.
- Remove an optional summary or takeaway block if it repeats the introduction and crowds stronger evidence.

## Deliverables

Deliver the final HTML and any local assets it needs. Report the chosen physical dimensions and the browser QA performed. Mention explicitly when PDF generation was intentionally skipped.
