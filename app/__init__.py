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
# (the entry note, e.g. "10 oz") or piece count, and what the container cost,
# next to the due date
GROCY_URL = getenv("GROCY_URL", "").rstrip("/")
GROCY_API_KEY = getenv("GROCY_API_KEY", "")
# symbol to print prices with; empty asks Grocy for its own CURRENCY setting
CURRENCY_SYMBOL = getenv("CURRENCY_SYMBOL", "")

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

# currencies worth a symbol in front of the amount; anything else prints its
# ISO code after it ("2.99 CHF"), which beats guessing the wrong symbol
CURRENCY_SYMBOLS = {
    "USD": "$", "CAD": "$", "AUD": "$", "NZD": "$", "EUR": "€", "GBP": "£",
    "JPY": "¥", "CNY": "¥", "INR": "₹", "KRW": "₩", "BRL": "R$", "PLN": "zł",
}

currencyCode = None  # Grocy's CURRENCY, looked up once on the first priced label

def fetchCurrencyCode():
    """Grocy's configured currency, cached. Only a successful lookup is cached,
    so a transient failure is retried on the next label rather than fixing every
    price of a whole session to no currency at all."""
    global currencyCode
    if currencyCode is None:
        try:
            currencyCode = (grocyGet("/system/config").get("CURRENCY") or "").upper()
        except Exception:
            log.exception("fetching Grocy's currency failed - printing the price without one")
            return ""
    return currencyCode

def formatPrice(value):
    """An amount as it goes on the label, e.g. "$2.99"."""
    amount = "%.2f" % value
    symbol = CURRENCY_SYMBOL
    if not symbol:
        code = fetchCurrencyCode()
        if code not in CURRENCY_SYMBOLS:
            return ("%s %s" % (amount, code)).strip()
        symbol = CURRENCY_SYMBOLS[code]
    return symbol + amount

GROCYCODE_ENTRY = re.compile(r"^grcy:p:(\d+):(.+)$")

def entryPrice(entry, amount):
    """What the container this label is for cost. Grocy stores the entry price
    per stock unit, so an entry booked by weight (1.55 lb at 1.99/lb) has to be
    multiplied back out to get the price of the thing on the shelf. "" when the
    purchase recorded no price."""
    price = entry.get("price")
    if price is None or amount <= 0:
        return ""
    total = float(price) * amount
    if total <= 0:
        return ""
    return formatPrice(total)

def fetchEntryDetails(barcode):
    """Best-effort details for a stock entry label, as (size, isPackageSize,
    price). The size is the entry's note (grocery-snap stores the package size
    there, e.g. "10 oz"), else amount + stock unit for multi-piece entries
    ("20 Pieces") — which counts containers rather than naming a package size,
    hence the flag. ("", False, "") when Grocy access is not configured, the
    code is not a stock entry grocycode, or anything fails — the label then
    just carries no detail beyond its due date."""
    if not GROCY_URL or not GROCY_API_KEY:
        return ("", False, "")
    m = GROCYCODE_ENTRY.match(barcode)
    if not m:
        return ("", False, "")
    try:
        (productId, stockId) = m.groups()
        entries = grocyGet("/stock/products/%s/entries" % productId)
        entry = next((e for e in entries if e.get("stock_id") == stockId), None)
        if entry is None:
            return ("", False, "")
        amount = float(entry.get("amount") or 0)
        price = entryPrice(entry, amount)
        note = (entry.get("note") or "").strip()
        if note:
            return (note, True, price)
        if amount == 1:
            return ("", False, price)
        product = grocyGet("/objects/products/%s" % productId)
        unit = grocyGet("/objects/quantity_units/%s" % product["qu_id_stock"])
        unitName = (unit.get("name_plural") if amount != 1 else "") or unit.get("name") or ""
        return (("%g %s" % (amount, unitName)).strip(), False, price)
    except Exception:
        log.exception("fetching label details for %s failed - printing without", barcode)
        return ("", False, "")

# Grocy product names carry their package size as a "(453 g)" suffix so size
# variants of one item are told apart (internal/productname in grocery-snap).
# A parenthesis starting with a digit is a size; a descriptive one
# ("Milk (organic)") is part of the name and stays.
SIZE_SUFFIX = re.compile(r"\s*\([0-9][^()]*\)$")

def stripSizeSuffix(name):
    return SIZE_SUFFIX.sub("", name).strip()

def shortStockCode(barcode):
    """A short, human-readable tag for one specific stock entry, taken from the
    tail of its grocycode stock id. Two labels printed for the same product
    otherwise differ only in their (unreadable) DataMatrix; this lets a person
    tell them apart and matches what a scan of the code resolves to. "" for
    non-entry codes (chores, batteries, recipes)."""
    m = GROCYCODE_ENTRY.match(barcode)
    if not m:
        return ""
    return m.group(2)[-5:].upper()

def detailLine(size, price, dueDate, code):
    # the whole line shrinks to fit the label, so keep it short enough to stay
    # readable: the size is the expendable part (package sizes are short, and a
    # truncated one still identifies the container), while the price, entry
    # code and due date stay
    if len(size) > 16:
        size = size[:15] + "…"
    return " · ".join(part for part in (size, price, dueDate, code) if part)

def renderLabel(name, barcode, dueDate):
    (size, isPackageSize, price) = fetchEntryDetails(barcode)
    # The package size has its own line, so drop it from the name — printing it
    # twice spends the label's biggest, auto-sized line on a repeat. The piece
    # count is not the package size, so it leaves the name alone.
    if isPackageSize:
        name = stripSizeSuffix(name)
    bottomLine = detailLine(size, price, dueDate, shortStockCode(barcode))
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
