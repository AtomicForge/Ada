"""Safe Markdown subset for model answers.

parse() turns text into (text, tag) pieces. A tag only means "draw this piece in a style"
(bold, italic, code, code block, h1-h3); the text itself is never changed except for the
removed markers. Links, images and HTML are not interpreted: they stay plain text."""
import re

_INLINE = re.compile(
    r"\*\*(?!\s)(.+?)(?<!\s)\*\*"                     # **bold**
    r"|`([^`\n]+)`"                                   # `code`
    r"|(?<![\w*])\*(?!\s)([^*\n]*?[^*\s])\*(?![\w*])"  # *italic*
)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+(.*)$")
_TAGS = ("bold", "italic", "code", "codeblock", "h1", "h2", "h3")


def _inline(line):
    pieces, pos = [], 0
    for m in _INLINE.finditer(line):
        if m.start() > pos:
            pieces.append((line[pos:m.start()], None))
        if m.group(1) is not None:
            pieces.append((m.group(1), "bold"))
        elif m.group(2) is not None:
            pieces.append((m.group(2), "code"))
        else:
            pieces.append((m.group(3), "italic"))
        pos = m.end()
    if pos < len(line):
        pieces.append((line[pos:], None))
    return pieces


def parse(text):
    """Return [(text, tag or None), ...]. Joining the texts gives the answer without markers."""
    out, in_code = [], False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        last = i == len(lines) - 1
        if line.strip().startswith("```"):
            in_code = not in_code
            continue  # the fence line is dropped, including its language label
        if in_code:
            out.append((line, "codeblock"))
        elif m := _HEADING.match(line):
            out.append((m.group(2), "h%d" % min(len(m.group(1)), 3)))
        elif m := _BULLET.match(line):
            out.append((m.group(1) + "• ", None))
            out.extend(_inline(m.group(2)))
        else:
            out.extend(_inline(line))
        if not last:
            out.append(("\n", None))
    return out
