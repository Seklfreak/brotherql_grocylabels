from pylibdmtx.pylibdmtx import encode
import qrcode
from PIL import Image, ImageColor, ImageFont, ImageDraw

def createDatamatrix(text: str):
    encoded = encode(text.encode('utf8'), "Ascii", "ShapeAuto")
    barcode = Image.frombytes('RGB', (encoded.width, encoded.height), encoded.pixels)
    return barcode

def createQRCode(text: str):
    return qrcode.make(text, box_size = 1)

def createBarcode(text: str, type: str):
    match type:
        case "QRCode":
            return createQRCode(text)
        case "DataMatrix":
            return createDatamatrix(text)
        case _:
            return createDatamatrix(text)

# padding from the label edges (dots); die-cut labels have rounded corners
PADDING = 14
# gap between the barcode and the text area
BARCODE_GAP = 20
LINE_SPACING = 4

def createLabelImage(labelSize : tuple, endlessMargin : int, text : str, fontPath : str, maxFontSize : int, minFontSize : int, textMaxLines : int, barcode : Image, dueDate : str, dueDateFont : ImageFont):
    (width, height) = labelSize

    # for endless labels with a height of zero: size for the max font
    if height == 0:
        height = (maxFontSize + LINE_SPACING) * textMaxLines + endlessMargin * 2
        if dueDate:
            (_, _, _, ddBottom) = dueDateFont.getbbox(dueDate)
            height += ddBottom

    # scale the barcode to fill the label height (largest integer factor)
    scale = max(1, (height - 2 * PADDING) // barcode.size[1])
    barcode = barcode.resize((barcode.size[0] * scale, barcode.size[1] * scale), Image.Resampling.NEAREST)

    label = Image.new("RGB", (width, height), ImageColor.getrgb("#FFF"))
    draw = ImageDraw.Draw(label)

    # barcode on the left, vertically centered
    label.paste(barcode, (PADDING, (height - barcode.size[1]) // 2))

    textLeft = PADDING + barcode.size[0] + BARCODE_GAP
    textMaxWidth = width - textLeft - PADDING

    # the due date reserves a line at the bottom of the text area; it is
    # right-aligned across the full width, so shrink its font (down to a
    # readable floor) until it clears the barcode on the left
    ddHeight = 0
    if dueDate:
        ddFloor = max(14, (dueDateFont.size * 2) // 3)
        while dueDateFont.size > ddFloor and dueDateFont.getlength(dueDate) > textMaxWidth:
            dueDateFont = dueDateFont.font_variant(size = dueDateFont.size - 2)
        (_, _, _, ddBottom) = dueDateFont.getbbox(dueDate)
        ddHeight = ddBottom + LINE_SPACING

    # find the biggest font size whose wrapped text fits the area without
    # hyphenating words; only the minimum size may truncate or hyphenate
    nameAreaHeight = height - 2 * PADDING - ddHeight
    for fontSize in range(maxFontSize, minFontSize - 1, -2):
        font = ImageFont.truetype(fontPath, fontSize)
        (nameText, truncated, chopped) = wrapText(text, font, textMaxWidth, textMaxLines)
        bbox = draw.multiline_textbbox((0, 0), nameText, font = font, spacing = LINE_SPACING, align = "center")
        if fontSize <= minFontSize or (not truncated and not chopped and bbox[3] - bbox[1] <= nameAreaHeight):
            break

    # center the name block in the area above the due date line
    nameTop = PADDING + max(0, (nameAreaHeight - (bbox[3] - bbox[1])) // 2) - bbox[1]
    nameLeft = textLeft + max(0, (textMaxWidth - (bbox[2] - bbox[0])) // 2)
    draw.multiline_text(
        (nameLeft, nameTop),
        nameText,
        fill = ImageColor.getrgb("#000"),
        font = font,
        align = "center",
        spacing = LINE_SPACING
    )

    if dueDate:
        (_, _, ddRight, ddBottom) = dueDateFont.getbbox(dueDate)
        draw.text(
            (width - PADDING - ddRight, height - PADDING - ddBottom),
            dueDate,
            fill = ImageColor.getrgb("#000"),
            font = dueDateFont
        )

    return label

def wrapText(text : str, font : ImageFont, maxWidth : int, maxLines : int):
    parts = text.split(" ")
    parts.reverse()
    lines = []

    # break words that are too long for a single line (repeatedly — one
    # halving may not be enough at large font sizes); parts is in reversed
    # word order, so chopped pieces must land second-half-first too
    trimmedParts = []
    chopped = False
    for part in parts:
        halves = [part]
        while halves:
            piece = halves.pop()
            if font.getlength(piece) >= maxWidth and len(piece) > 2:
                # just chop in half, nothing fancy
                chopped = True
                midpoint = int(len(piece) / 2)
                halves.append(piece[0:midpoint] + '-')
                halves.append(piece[midpoint:])
            else:
                trimmedParts.append(piece)

    parts = trimmedParts

    # create lines from input
    while len(parts) > 0:
        nextLine = []

        # create this line adding words while the next word fits
        while len(parts) > 0:
            nextPart = parts.pop()

            # an over-wide part must still be taken when it starts a line, or
            # no progress is ever made
            if len(nextLine) == 0 or font.getlength(' '.join(nextLine) + ' ' + nextPart) < maxWidth:
                nextLine.append(nextPart)
            else:
                # didn't fit so put it back on the stack
                parts.append(nextPart)
                break

        # finished with the line
        if len(nextLine) > 0:
            lines.append(' '.join(nextLine))

    truncated = len(lines) > maxLines
    if truncated:
        lines = lines[0:maxLines]
        lines[-1] += '...'

    return ('\n'.join(lines), truncated, chopped)
