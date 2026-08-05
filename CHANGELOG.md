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