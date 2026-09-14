# 0.10.0

- Stock entry labels now show what the container cost, next to its package
  size and due date ("3.3 oz · $2.99 · 2028-11-26 · E4E43"). The price is the
  stock entry's own — times its amount, so an entry booked by weight shows
  what the package cost rather than the per-pound price — and an entry booked
  without a price prints as before. Needs `GROCY_URL`/`GROCY_API_KEY`, which
  the size line already required, and costs no extra Grocy call.
- The currency comes from Grocy's `CURRENCY` setting (looked up once);
  `CURRENCY_SYMBOL` overrides it, and a currency with no symbol here prints
  its code after the amount ("2.99 CHF")
- The size on the bottom line now truncates at 16 characters rather than 20,
  to leave the longer line room to stay readable

# 0.9.2

- DataMatrix codes no longer print at half size on some labels: libdmtx
  renders 5 dots per module, so scaling the raw symbol by the largest integer
  factor stepped five modules at a time and a 24x24 symbol dropped from 2x to
  1x. The symbol is now collapsed to one pixel per module before scaling, so
  every code fills 96-100% of the label height instead of 53-99%.

# 0.6.0

- Nicer label layout: the name auto-sizes to the biggest font that fits
  (`NAME_FONT_SIZE` is now the maximum, `NAME_MIN_FONT_SIZE` the floor —
  only the floor may hyphenate/truncate), the text block and barcode are
  vertically centered with proper edge padding, and the barcode scales to
  fill the label height
- Default fonts switched from NotoSerif to NotoSans
- `DUE_DATE_FONT` env var is actually read (previously it read `NAME_FONT`)
- Fixed an infinite loop in text wrapping when a word didn't fit a line at
  large font sizes

# 0.5.0

- Fork of [sam159/brotherql_grocylabels](https://github.com/sam159/brotherql_grocylabels)
- Batch printing: `/print` queues the label and returns immediately; labels
  arriving within `BATCH_IDLE_SECONDS` (default 3) of each other print as one
  job with a single cut after the last label
- Published to ghcr.io as `ghcr.io/seklfreak/brotherql_grocylabels` (amd64)

# 0.4.2

- add support for `62red` labels (and other red labels)

# 0.4.1

- publish main branch as latest docker image

# 0.4.0

- Added support for printing endless labels

# 0.3.0

- Added support for using QR codes instead of Datamatrix

# 0.2.0

- Scaling barcode by 2x or 4x space permitting
- Centered text