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