# Poster size and aspect-ratio adaptation

Treat physical dimensions as inputs, not constants. The 100 × 75 cm source poster is a calibration reference.

## 1. Set the physical page

Update all three places:

1. `--poster-width`;
2. `--poster-height`;
3. the explicit `@page { size: WIDTH HEIGHT; }` declaration.

Use the requested unit directly, such as `cm`, `mm`, or `in`. Do not rely on a global CSS transform or browser print scaling to create the requested dimensions.

## 2. Use container-relative scaling

The bundled template sets `container-type: size` on the poster and expresses critical type and spacing with `cqmin` inside `clamp()`.

This makes the shorter poster dimension the primary scale reference while preserving readable physical floors and sensible ceilings. Keep these clamps when changing size; adjust only when visual QA shows the new viewing distance or content density requires it.

At any size:

- keep primary prose at least 16–18 pt in print;
- keep secondary labels at least 15–16 pt;
- keep section titles clearly above body copy;
- reduce content and padding before violating the readable floor.

## 3. Choose layout from aspect ratio

Compute `r = width / height`.

### Standard landscape: `1.22 ≤ r ≤ 1.50`

Use the source composition:

- three columns;
- proportions near `30.5 / 39 / 30.5`;
- method in the center;
- header near 10.5–12% of height.

### Wide landscape: `r > 1.50`

Keep three columns but widen the method region to roughly `28 / 44 / 28`, or use four columns only when the content has four genuinely independent reading groups. Do not stretch paragraphs into long lines; widen diagrams, tables, and comparisons instead.

### Near-square: `1.00 ≤ r < 1.22`

Avoid three skinny columns. Use two main columns and let the architecture span both columns in a dedicated row. Move compact metrics into the header or a full-width results strip.

### Portrait: `r < 1.00`

Use a stacked composition:

1. compact header;
2. full-width abstract and contribution strip;
3. full-width architecture;
4. two-column evidence and results area;
5. compact footer.

Do not mechanically rotate or squeeze the landscape layout.

## 4. Reallocate content after resizing

When reducing area:

1. remove repeated takeaway text;
2. shorten captions and setup detail;
3. combine closely related cards;
4. reduce card padding and gaps;
5. remove secondary results;
6. reduce fonts only after the above steps.

When increasing area, add useful experimental details, arrow annotations, definitions, and qualitative evidence before increasing decorative whitespace.

## 5. Header calibration

Start with a header height near 11% of poster height for landscape pages and 8–10% for portrait pages. Keep the title and headline metric vertically centered. Let the metadata row fill the remaining header height rather than giving it a short fixed height that leaves unused space below.

## 6. Browser QA for arbitrary sizes

- Choose a zoom that shows the entire page; do not assume 25%.
- Verify actual width-to-height appearance, not only CSS values.
- Inspect high-resolution crops of the header, densest table, architecture, and bottom edge.
- Confirm the print dialog uses the exact requested page size and 100% scale only when the user asks for PDF or print verification.
- Keep the HTML result authoritative when the user exports through Edge or another browser.
