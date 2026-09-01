# Academic poster visual system

Use this reference to reproduce the visual character of the source poster without copying its paper-specific text.

## 1. Visual character

Aim for a serious research-poster aesthetic with:

- a navy-to-blue technical palette;
- a strong horizontal header and white body;
- three disciplined reading columns in standard landscape layouts;
- a wider, lightly ruled center column for the method;
- rounded, lightly bordered cards rather than heavy boxes;
- bright but restrained semantic accents;
- large readable prose and dense information without crowding;
- icons before section titles, with no numbered title badges.

The poster should feel engineered and precise, not decorative. Use color to explain information roles rather than to fill empty space.

## 2. Reference palette

| Token | Value | Use |
| --- | --- | --- |
| Navy | `#112f50` | Section text, strong strips, table headers |
| Navy 2 | `#1c4d79` | Header gradient and deep accents |
| Blue | `#2c72ad` | Section rules, icons, arrows, emphasis |
| Sky | `#eaf4fb` | Soft method and callout backgrounds |
| Ink | `#172433` | Main prose |
| Muted | `#526477` | Secondary prose and labels |
| Line | `#d4e0ea` | Borders and dividers |
| Panel | `#f4f8fb` | General cards |
| Red | `#bb574f` / `#fff1ef` | Limitations and dense baselines |
| Green | `#2d8666` / `#edf8f3` | Wins, efficient results, desired path |
| Gold | `#bd7a1d` / `#fff7e8` | Scope and deployment caveats |
| Purple | `#6c57a4` / `#f3effb` | Training-only supervision |
| Content | `#b8c8e7` | Content-head tokens |
| Residual | `#bde0d7` | Residual-codebook tokens |
| Caption | `#cbb7d7` | Caption-head tokens |
| Logic | `#b6ccdc` | Logic-head tokens |

Use the header gradient near `linear-gradient(122deg, #102b49 0%, #174269 50%, #2e73ab 100%)`.

## 3. Typography hierarchy

Calibrate sizes through container-relative clamps in the bundled template. At the 100 × 75 cm reference size, target approximately:

- title: 50 pt;
- subtitle: 21 pt;
- authors: 20 pt;
- affiliations: 18 pt;
- section titles: 27–28 pt;
- primary prose: 21–22 pt;
- secondary prose and card copy: 18–20 pt;
- table text: about 19 pt;
- card metrics: 28–32 pt;
- header headline metric: about 45 pt.

Use Helvetica Neue, Helvetica, Arial, or a similar clean sans serif. Use Georgia or Times New Roman only for compact equations.

Keep primary prose visibly larger than labels, but avoid a large gap between maximum and minimum sizes. At the reference size, do not let meaningful poster copy drop below 18 pt.

Set superscripts and subscripts to roughly `.66em`, normal weight, italic, and zero line height.

## 4. Header

Allocate about 11% of poster height to the header in the standard landscape composition.

- Keep the title dominant but normally on one line.
- Place a short promise directly below it.
- Put one headline metric on the right, separated by a thin vertical rule.
- Use a thin horizontal rule above the metadata row.
- Optically center authors and affiliations on the left.
- Keep the right resource link simple: GitHub icon plus repository path.
- Use asymmetric micro-padding when the visible glyphs need optical, not merely geometric, centering.
- Avoid venue lines, DOI clutter, and redundant metadata unless explicitly required.

## 5. Body composition

For standard landscape posters, start with outer/center/outer proportions near `30.5 / 39 / 30.5`. Use narrow gutters and thin vertical rules around the center column.

Make every column a flex column with compact sections and `justify-content: space-between`. Do not stretch the contents inside sections. Let only the gaps between sections absorb unused height.

Give every section title:

- a real icon;
- a one-line title;
- a dark blue bottom rule;
- about half a line of breathing room before the content.

Avoid large blank rectangles, isolated one-line sections, and decorative blocks that repeat existing content.

## 6. Core components

### Abstract or introduction

Use a cool gradient panel with a thick blue left rule. Write one compact lead paragraph with the problem, failure of prior systems, proposed representation, and retrieval consequence.

### Motivation

Use two symmetric cards for the old limitation and desired capability. Indent list text enough to reveal hierarchy. Add a one-line dark design-target strip beneath them when it fits naturally.

### Contributions

Use a two-by-two grid. Place small, optically centered numbered circles inside each contribution card, while keeping section titles icon-led and unnumbered.

### Evidence

Use a bordered figure box with `object-fit: contain`. Keep the first bold caption phrase slightly larger than the explanatory text. Never allow an image to cover its border.

### Workflow

Use three equal steps separated by arrows. Keep short labels on one line. Place two semantic usage lines beneath the steps with close vertical spacing.

### Data and evaluation

Use four consistent cards with matched heading and paragraph heights. Put training and inference setup on separate semantic lines in one compact strip.

### Results table

Center headers and values. Use a navy header, subtle zebra rows, generous but compact row height, and three short explanatory lines below the table when needed.

### Efficiency

Prefer two side-by-side comparisons. Use muted tracks with green and red bars, then a compact green highlight. Explain whether the comparison concerns index memory, query latency, or posting work.

### Two-stage retrieval

Use two slightly narrow cards with a clearly visible horizontal arrow between them. Show the before/after metric prominently and explain the reranking budget beneath.

## 7. Architecture diagram rules

Use HTML/CSS or SVG rather than a raster architecture diagram whenever practical.

- Keep the inference path vertically legible from audio to index.
- Use long, moderately thick arrows with labels placed to the arrow's right or in a left-aligned connector label.
- Give architecture block titles the maximum card-title size.
- Keep parallel branches symmetric in width, height, padding, and center alignment.
- Represent a content codebook as one row of equal tokens.
- Represent residual codebooks as multiple rows when the multiplicity matters; shorten only those token heights if necessary.
- Use sparse lexical rows with many narrow outlined token slots, a few colored activations, and a vertically centered ellipsis.
- Use opposing repeated diagonal hatching when two fused token rows need to remain distinguishable.
- Split the student area into a diagram region and a separate note region. Space heads, arrows, and vectors deliberately rather than uniformly distributing all elements.
- Place a textual teacher in a purple dashed side branch labeled `Training Only`; connect it to student targets, never through the inference path.
- Keep formulas equal-height across a mechanics row. Align multiline loss continuations after the equals sign and center the entire equation group.

## 8. Wrapping and micro-alignment

- Prefer natural wrapping and copy edits over hard `<br>` tags.
- Use explicit block lines only for semantically separate definitions, training/inference rows, or required label/value pairs.
- Avoid a second line containing only one or two words.
- Make symmetric cards carry similar line counts.
- Center token grids and table datasets deliberately.
- Use subtle borders or outer shadows on sparse token slots so zero-valued positions remain visible.
- Keep arrows, icons, numbers, and badges optically centered; geometric centering alone may not look centered.
- Inspect the rendered browser page after every group of micro-adjustments.

## 9. Final visual checklist

- Is the title readable without dominating the poster?
- Is primary prose large enough from several feet away?
- Does the main method occupy the visual center?
- Is training-only supervision clearly off the inference path?
- Are cards symmetric and token grids centered?
- Are section titles single-line and icon-led?
- Are all three columns balanced to the same lower edge?
- Are there any orphan words, broken formulas, clipped images, or borders covered by content?
- Does the full page fit at browser overview zoom with no bottom clipping?
