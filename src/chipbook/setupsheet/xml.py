"""Reading a setup sheet saved as XML.

The file is walked element by element and turned into blocks of the shape
the rest of the package expects. Two fuses stand in the way of an enormous
or hostile file: a cap on the number of elements and a cap on the depth.

Note on the name: `import xml.etree.ElementTree` below reaches the STANDARD
LIBRARY, not this module - inside a package, a plain import is absolute.
"""

import xml.etree.ElementTree as ET

from .render import MIN_TABLE_ROWS, _build_table, _drop_empty

MAX_XML_BYTES = 8 * 1024 * 1024   # beyond this we do not parse - the view must be quick
MAX_ELEMENTS = 4000               # a fuse against enormous or hostile XML
MAX_DEPTH = 8


def _describe_xml(path, name):
    try:
        with open(path, "rb") as file:
            raw = file.read()
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        return {"kind": "error_message", "name": name,
                "notice": "This is not valid XML (%s). The attachment "
                         "is kept untouched - open it with the button." % error}
    except (OSError, ValueError) as error:
        return {"kind": "error_message", "name": name,
                "notice": "Could not read the file: " + str(error)}

    state = {"counter": 0, "truncated": False}
    blocks = []
    _walk_element(root, 0, blocks, state, _clean(root.tag))
    result = {"kind": "xml", "name": name, "root": _clean(root.tag),
             "blocks": blocks}
    if state["truncated"]:
        result["notice"] = ("This file is very large - showing the first "
                          "%d elements. Open the whole file with the button."
                          % MAX_ELEMENTS)
    return result


def _clean(tag):
    """Strip the namespace from an element name: {ns}Operation -> Operation."""
    tag = str(tag)
    return tag.split("}")[-1] if "}" in tag else tag


def _simple_children(element):
    """A simple child = no children of its own and no attributes, just text.

    Those are treated as descriptive fields of the parent, not as sections.
    """
    return [d for d in element if not list(d) and not d.attrib]


def _complex_children(element):
    simple = _simple_children(element)
    return [d for d in element if d not in simple]


def _element_pairs(element):
    """Descriptive fields of an element: attributes, own text, simple children.

    A PAIR IS A LIST, NOT A TUPLE, and that is not a matter of taste. The
    rules applied afterwards to the finished structure - the stock size
    among them - correct a value IN PLACE, so that they can be written once
    and behave the same for PDF and for XML. The PDF reader builds lists;
    this one used to build tuples, and opening a setup sheet with a STOCK
    section ended in "'tuple' object does not support item assignment"
    instead of a preview.
    """
    pairs = [[_clean(k), v] for k, v in element.attrib.items()]
    own_text = (element.text or "").strip()
    if own_text and not list(element):
        pairs.append(["value", own_text])
    for d in _simple_children(element):
        content = (d.text or "").strip()
        if content:
            pairs.append([_clean(d.tag), content])
    return pairs


def _walk_element(element, level, blocks, state, title):
    if state["counter"] >= MAX_ELEMENTS or level > MAX_DEPTH:
        state["truncated"] = True
        return
    state["counter"] += 1

    pairs, empty = _drop_empty(_element_pairs(element))
    if pairs:
        blocks.append({"kind": "pairs", "title": title, "level": level,
                      "pairs": pairs, "empty_fields": empty})

    groups, order = {}, []
    for d in _complex_children(element):
        key = _clean(d.tag)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(d)

    for key in order:
        items = groups[key]
        # Repeated siblings under the same name make a table.
        # An earlier rule took columns from ATTRIBUTE names and was dead on a real
        # CAM file - measured: 310 elements, ZERO attributes, 0 tables, 42 flat
        # sections. Columns now come from labels, so attributes and tags behave
        # identically.
        table = None
        if len(items) >= MIN_TABLE_ROWS:
            if state["counter"] + len(items) > MAX_ELEMENTS:
                state["truncated"] = True
                items = items[:max(0, MAX_ELEMENTS - state["counter"])]
            table = _build_table([_element_pairs(d) for d in items],
                                  key, level + 1)
        if table:
            state["counter"] += len(items)
            blocks.extend(table)
            # THE INSIDE IS NOT LOST: a table takes only simple fields, and anything
            # with children of its own carries on separately. Otherwise an operation
            # would be reduced to one row and its tool and coordinate system would go.
            for number, d in enumerate(items, 1):
                for child in _complex_children(d):
                    _walk_element(child, level + 2, blocks, state,
                            "%s %d > %s" % (key, number, _clean(child.tag)))
            continue
        for number, d in enumerate(items, 1):
            subtitle = key if len(items) == 1 else "%s %d" % (key, number)
            _walk_element(d, level + 1, blocks, state, subtitle)
