from io import BytesIO
from os import path, getenv
import json
import logging
import queue
import re
import threading
from urllib.request import Request, urlopen
from flask import Flask, Response, request
from PIL import Image, ImageFont
from dotenv import load_dotenv
from brother_ql.labels import ALL_LABELS, Color
from brother_ql import BrotherQLRaster, create_label
from brother_ql.backends import guess_backend, backend_factory
from app.imaging import createBarcode, createLabelImage

load_dotenv()

LABEL_SIZE = getenv("LABEL_SIZE", "62x29")
PRINTER_MODEL = getenv("PRINTER_MODEL", "QL-500")
PRINTER_PATH = getenv("PRINTER_PATH", "file:///dev/usb/lp1")
BARCODE_FORMAT = getenv("BARCODE_FORMAT", "Datamatrix")
NAME_FONT = getenv("NAME_FONT", "NotoSans-Regular.ttf")
# the name is auto-sized: the biggest size in [MIN, MAX] whose wrapped text
# fits the label wins; only the minimum size may truncate with an ellipsis
NAME_FONT_SIZE = int(getenv("NAME_FONT_SIZE", "84"))
NAME_MIN_FONT_SIZE = int(getenv("NAME_MIN_FONT_SIZE", "30"))
NAME_MAX_LINES = int(getenv("NAME_MAX_LINES", "4"))
DUE_DATE_FONT = getenv("DUE_DATE_FONT", "NotoSans-Regular.ttf")
DUE_DATE_FONT_SIZE = int(getenv("DUE_DATE_FONT_SIZE", "30"))
ENDLESS_MARGIN = int(getenv("ENDLESS_MARGIN", "10"))
# how long the queue must stay idle before a batch is printed and cut
BATCH_IDLE_SECONDS = float(getenv("BATCH_IDLE_SECONDS", "3"))
# how long to wait between print attempts while the printer is unreachable
# (e.g. auto-powered-off): labels are held and retried, never dropped
RETRY_INTERVAL_SECONDS = float(getenv("RETRY_INTERVAL_SECONDS", "30"))
# optional Grocy API access: stock entry labels then show the package size
# (the entry note, e.g. "10 oz") or piece count next to the due date
GROCY_URL = getenv("GROCY_URL", "").rstrip("/")
GROCY_API_KEY = getenv("GROCY_API_KEY", "")

selected_backend = guess_backend(PRINTER_PATH)
BACKEND_CLASS = backend_factory(selected_backend)['backend_class']

label_spec = next(x for x in ALL_LABELS if x.identifier == LABEL_SIZE)

thisDir = path.dirname(path.abspath(__file__))
nameFontPath = path.join(thisDir, "..", "fonts", NAME_FONT)
ddFont = ImageFont.truetype(path.join(thisDir, "..", "fonts", DUE_DATE_FONT), DUE_DATE_FONT_SIZE)

app = Flask(__name__)
log = logging.getLogger("gunicorn.error")

@app.route("/")
def home_route():
    return "Label %s, %s, %d label(s) pending"%(label_spec.identifier, label_spec.name, pendingCount)

def get_params():
    source = request.form if request.method == "POST" else request.args

    name = ""
    if 'product' in source:
        name = source['product']
    if 'battery' in request.form:
        name = source['battery']
    if 'chore' in request.form:
        name = source['chore']
    if 'recipe' in request.form:
        name = source['recipe']

    barcode = source['grocycode'] if 'grocycode' in source else ''
    dueDate = source['due_date'] if 'due_date' in source else ''

    return (name, barcode, dueDate)

def grocyGet(apiPath):
    req = Request(GROCY_URL + "/api" + apiPath, headers={"GROCY-API-KEY": GROCY_API_KEY})
    with urlopen(req, timeout=5) as resp:
        return json.load(resp)

GROCYCODE_ENTRY = re.compile(r"^grcy:p:(\d+):(.+)$")

def fetchSizeText(barcode):
    """Best-effort size/count for a stock entry label: the entry's note
    (grocery-snap stores the package size there, e.g. "10 oz" / "20 ct"),
    else amount + stock unit for multi-piece entries ("20 Pieces"). ""
    when Grocy access is not configured, the code is not a stock entry
    grocycode, or anything fails — the label then just has no size line."""
    if not GROCY_URL or not GROCY_API_KEY:
        return ""
    m = GROCYCODE_ENTRY.match(barcode)
    if not m:
        return ""
    try:
        (productId, stockId) = m.groups()
        entries = grocyGet("/stock/products/%s/entries" % productId)
        entry = next((e for e in entries if e.get("stock_id") == stockId), None)
        if entry is None:
            return ""
        note = (entry.get("note") or "").strip()
        if note:
            return note
        amount = float(entry.get("amount") or 0)
        if amount == 1:
            return ""
        product = grocyGet("/objects/products/%s" % productId)
        unit = grocyGet("/objects/quantity_units/%s" % product["qu_id_stock"])
        unitName = (unit.get("name_plural") if amount != 1 else "") or unit.get("name") or ""
        return ("%g %s" % (amount, unitName)).strip()
    except Exception:
        log.exception("fetching label details for %s failed - printing without", barcode)
        return ""

def detailLine(size, dueDate):
    # keep the bottom line to one modest length so it can't collide with the
    # barcode; the size is the expendable part
    if len(size) > 20:
        size = size[:19] + "…"
    if size and dueDate:
        return "%s · %s" % (size, dueDate)
    return size or dueDate

def renderLabel(name, barcode, dueDate):
    bottomLine = detailLine(fetchSizeText(barcode), dueDate)
    return createLabelImage(label_spec.dots_printable, ENDLESS_MARGIN, name, nameFontPath, NAME_FONT_SIZE, NAME_MIN_FONT_SIZE, NAME_MAX_LINES, createBarcode(barcode, BARCODE_FORMAT), bottomLine, ddFont)

# Labels queue up and a single worker prints them: a burst of webhooks
# (Grocy sends one per unit) becomes one print job, cut once at the end.
# A batch that cannot be printed (printer unreachable, e.g. powered off) is
# held and retried until the printer is back; labels arriving in the
# meantime join the pending batch, so everything comes out as one strip.
# Rendering happens in the worker too (not in the webhook request), which
# keeps /print instant and lets the size lookup see the committed booking.
labelQueue = queue.Queue()
pendingCount = 0  # labels held by the worker, exposed on the home route

def renderPending(item):
    """Render a queued label once, caching the image on the item so retries
    don't re-render. A label that cannot be rendered at all is dropped with a
    log instead of wedging the queue forever."""
    if item["image"] is None:
        try:
            item["image"] = renderLabel(*item["params"])
        except Exception:
            log.exception("rendering label %r failed - dropping it", item["params"])
            item["image"] = False
    return item["image"]

def printWorker():
    global pendingCount
    pending = []
    while True:
        if not pending:
            pending.append(labelQueue.get())
        # drain the burst until the queue stays idle for a moment
        while True:
            try:
                pending.append(labelQueue.get(timeout=BATCH_IDLE_SECONDS))
            except queue.Empty:
                break
        pendingCount = len(pending)
        try:
            images = [img for item in pending if (img := renderPending(item))]
            if images:
                sendToPrinter(images)
                log.info("printed batch of %d label(s)", len(images))
            pending = []
            pendingCount = 0
        except Exception:
            log.exception("printing batch of %d label(s) failed - holding them, retrying in %gs",
                          len(pending), RETRY_INTERVAL_SECONDS)
            # wait out the retry interval, but keep collecting new labels
            try:
                pending.append(labelQueue.get(timeout=RETRY_INTERVAL_SECONDS))
                pendingCount = len(pending)
            except queue.Empty:
                pass

threading.Thread(target=printWorker, daemon=True).start()

@app.route("/print", methods=["GET", "POST"])
def print_route():
    labelQueue.put({"params": get_params(), "image": None})
    return Response("OK", 200)

@app.route("/image")
def test():
    img = renderLabel(*get_params())
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return Response(buf, 200, mimetype="image/png")

def sendToPrinter(images):
    bql = BrotherQLRaster(PRINTER_MODEL)

    redLabel = label_spec.color == Color.BLACK_RED_WHITE

    for i, image in enumerate(images):
        create_label(
            bql,
            image,
            LABEL_SIZE,
            red=redLabel,
            cut=(i == len(images) - 1)
        )

    be = BACKEND_CLASS(PRINTER_PATH)
    be.write(bql.data)
    del be
