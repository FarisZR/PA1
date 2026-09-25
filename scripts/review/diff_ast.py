#!/usr/bin/env python3
"""Build a review document that shows how the paper changed in a pull request.

Inputs are Pandoc ASTs written by scripts/review/dump_ast.lua for three
versions of the paper: the PR base, the state before the latest commit
(the head's first parent), and the PR head. The output is the head document
with the differences marked in place:

* text added by the PR is highlighted and removed text is struck through;
* additions and removals made by the latest commit use a second colour pair;
* every changed block gets a margin bar with a label, so it is visible where
  each change starts and ends;
* figures whose rendered image changed, or that are new, get a frame.

The diff runs on the executed AST, so generated tables and figures are compared
by their rendered content rather than by the code that produces them.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import difflib
import hashlib
import json
from pathlib import Path
import re
from typing import Any

Node = dict[str, Any]

SPACE_TYPES = {"Space", "SoftBreak", "LineBreak"}
INLINE_WRAPPERS = {
    "Emph",
    "Underline",
    "Strong",
    "Strikeout",
    "Superscript",
    "Subscript",
    "SmallCaps",
}
CROSSREF_PREFIXES = ("fig-", "tbl-", "sec-", "eq-", "lst-")
# Code-cell caption options stay Markdown strings in the Div's attributes
# until Quarto parses them, so they are diffed word by word as text.
CAPTION_ATTRS = ("tbl-cap", "fig-cap")
# Minimum word similarity for treating two blocks as an edited version of the
# same block instead of one removal plus one addition.
PAIR_THRESHOLD = 0.4

EQ = "eq"  # the node is identical in both versions
ALL_INS = "all-ins"  # the node does not exist in the older version


# ---------------------------------------------------------------------------
# Pandoc AST structure


def slots(node: Node) -> list[tuple[str, list[Node]]]:
    """Child sequences of a node as (kind, items); kind is block/inline/item/row/cell."""

    t, c = node["t"], node.get("c")
    if t in ("Para", "Plain"):
        return [("inline", c)]
    if t == "Header":
        return [("inline", c[2])]
    if t == "Div":
        return [("block", c[1])]
    if t in ("BlockQuote", "_Item", "Note"):
        return [("block", c)]
    if t == "BulletList":
        return [("item", [{"t": "_Item", "c": item} for item in c])]
    if t == "OrderedList":
        return [("item", [{"t": "_Item", "c": item} for item in c[1]])]
    if t == "Figure":
        return [("block", c[1][1]), ("block", c[2])]
    if t == "Table":
        rows = lambda items: [{"t": "_Row", "c": row} for row in items]  # noqa: E731
        result = [("block", c[1][1]), ("row", rows(c[3][1]))]
        result += [("row", rows(body[3])) for body in c[4]]
        result.append(("row", rows(c[5][1])))
        return result
    if t == "_Row":
        return [("cell", [{"t": "_Cell", "c": cell} for cell in c[1]])]
    if t == "_Cell":
        return [("block", c[4])]
    if t in INLINE_WRAPPERS:
        return [("inline", c)]
    if t in ("Span", "Link", "Quoted"):
        return [("inline", c[1])]
    return []


def rebuild(node: Node, seqs: list[list[Node]]) -> Node:
    """Return a copy of node with its child sequences replaced."""

    t, c = node["t"], node.get("c")
    unwrap = lambda items: [item["c"] for item in items]  # noqa: E731
    if t in ("Para", "Plain", "BlockQuote", "_Item", "Note") or t in INLINE_WRAPPERS:
        return {"t": t, "c": seqs[0]}
    if t == "Header":
        return {"t": t, "c": [c[0], c[1], seqs[0]]}
    if t == "Div":
        return {"t": t, "c": [c[0], seqs[0]]}
    if t == "BulletList":
        return {"t": t, "c": unwrap(seqs[0])}
    if t == "OrderedList":
        return {"t": t, "c": [c[0], unwrap(seqs[0])]}
    if t == "Figure":
        return {"t": t, "c": [c[0], [c[1][0], seqs[0]], seqs[1]]}
    if t == "Table":
        bodies = [
            [body[0], body[1], body[2], unwrap(seqs[2 + index])]
            for index, body in enumerate(c[4])
        ]
        head = [c[3][0], unwrap(seqs[1])]
        foot = [c[5][0], unwrap(seqs[-1])]
        return {"t": t, "c": [c[0], [c[1][0], seqs[0]], c[2], head, bodies, foot]}
    if t == "_Row":
        return {"t": t, "c": [c[0], unwrap(seqs[0])]}
    if t == "_Cell":
        return {"t": t, "c": [*c[:4], seqs[0]]}
    if t == "Span":
        return {"t": t, "c": [c[0], seqs[0]]}
    if t == "Link":
        return {"t": t, "c": [c[0], seqs[0], c[2]]}
    if t == "Quoted":
        return {"t": t, "c": [c[0], seqs[0]]}
    raise ValueError(f"cannot rebuild {t}")


def signature(node: Node) -> str | None:
    """Nodes with equal signatures may be paired and diffed recursively."""

    t, c = node["t"], node.get("c")
    if t == "Image":
        return "Image"
    if not slots(node):
        return None
    if t == "Header":
        return f"Header{c[0]}"
    if t == "Div":
        return "Div" + json.dumps([c[0][0], c[0][1]])
    if t == "Figure":
        return "Figure" + c[0][0]
    if t == "Table":
        return f"Table{len(c[2])}"
    if t == "_Row":
        return f"_Row{len(c[1])}"
    if t == "Span":
        return "Span" + json.dumps(c[0])
    if t == "Link":
        return "Link" + json.dumps(c[2])
    if t == "Quoted":
        return "Quoted" + json.dumps(c[0])
    return t


def text_of(node: Any) -> list[str]:
    words: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("t") == "Str":
                words.append(value["c"])
            elif value.get("t") in ("Code", "Math"):
                words.append(value["c"][-1])
            else:
                walk(value.get("c"))
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return words


def attr_value(node: Node, name: str) -> str | None:
    return dict(node["c"][0][2]).get(name)


def caption_words(text: str) -> list[Node]:
    return [
        {"t": "Space"} if token.isspace() else {"t": "Str", "c": token}
        for token in re.split(r"(\s+)", text)
        if token
    ]


def caption_markdown(inlines: list[Node]) -> str:
    parts = []
    for inline in inlines:
        if inline["t"] == "RawInline":
            parts.append(f"`{inline['c'][1]}`{{=typst}}")
        else:
            parts.append(" " if inline["t"] in SPACE_TYPES else inline["c"])
    return "".join(parts)


def contains(node: Any, node_type: str) -> bool:
    if isinstance(node, dict):
        return node.get("t") == node_type or contains(node.get("c"), node_type)
    if isinstance(node, list):
        return any(contains(item, node_type) for item in node)
    return False


# ---------------------------------------------------------------------------
# Comparison keys


class Keys:
    """Normalised comparison keys; images are compared by rendered pixels."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.image_hashes: dict[str, str] = {}
        self.cache: dict[int, tuple[Node, str]] = {}

    def image_hash(self, src: str) -> str:
        if src not in self.image_hashes:
            path = self.root / src
            try:
                import pymupdf

                with pymupdf.open(path) as document:
                    pixmap = document[0].get_pixmap(dpi=96, alpha=False)
                    digest = hashlib.sha256(pixmap.samples).hexdigest()
            except Exception:  # missing file or unsupported format
                digest = hashlib.sha256(
                    path.read_bytes() if path.exists() else src.encode()
                ).hexdigest()
            self.image_hashes[src] = digest
        return self.image_hashes[src]

    def normalise(self, value: Any) -> Any:
        if isinstance(value, dict):
            t = value.get("t")
            if t in SPACE_TYPES:
                return " "
            if t == "Str":
                return value["c"]
            if t == "RawBlock" and value["c"][0] == "html":
                return "<!-- -->"
            if t == "Image":
                return ["Image", self.normalise(value["c"][1]), self.image_hash(value["c"][2][0])]
            if t == "Cite":
                return [
                    "Cite",
                    [
                        [item["citationId"], item["citationMode"], self.normalise(item["citationPrefix"]), self.normalise(item["citationSuffix"])]
                        for item in value["c"][0]
                    ],
                ]
            return [t, self.normalise(value.get("c"))]
        if isinstance(value, list):
            # Attr triples: drop Jupyter's execution counter, which changes on
            # every edit above a cell without changing the rendered output.
            if (
                len(value) == 3
                and isinstance(value[0], str)
                and isinstance(value[1], list)
                and isinstance(value[2], list)
                and all(isinstance(item, list) and len(item) == 2 for item in value[2])
            ):
                attrs = [item for item in value[2] if item[0] != "execution_count"]
                return [value[0], value[1], attrs]
            return [self.normalise(item) for item in value]
        return value

    def key(self, node: Node) -> str:
        cached = self.cache.get(id(node))
        if cached is None:
            normalised = self.normalise(node)
            key = normalised if isinstance(normalised, str) else json.dumps(normalised)
            # Keep the node alive so its id() cannot be reused by another object.
            cached = self.cache[id(node)] = (node, key)
        return cached[1]


# ---------------------------------------------------------------------------
# Structural diff


@dataclass
class SeqDiff:
    status: list[str]  # per new item: "eq", "pair" or "ins"
    sub: dict[int, Any] = field(default_factory=dict)  # new index -> Script | EQ
    gaps: dict[int, list[Node]] = field(default_factory=dict)  # removed before index


@dataclass
class Script:
    seqs: list[SeqDiff]
    image_changed: bool = False
    captions: dict[str, SeqDiff] = field(default_factory=dict)


class Differ:
    def __init__(self, keys: Keys) -> None:
        self.keys = keys

    def similarity(self, old: Node, new: Node) -> float:
        sig = signature(new)
        if sig is None or sig != signature(old):
            return 0.0
        if new["t"] in ("Image", "_Cell"):
            return 1.0
        if new["t"] in ("Div", "Figure", "Table"):
            # Labelled floats and code cells are the same object if they
            # carry the same cross-reference label, whatever their content.
            old_ids, new_ids = collect_ids(old, set()), collect_ids(new, set())
            if old_ids and new_ids:
                return 1.0 if old_ids & new_ids else 0.0
        old_words, new_words = text_of(old), text_of(new)
        if not old_words and not new_words:
            return 0.5
        return difflib.SequenceMatcher(None, old_words, new_words, autojunk=False).ratio()

    def node(self, old: Node, new: Node) -> Script | str:
        if self.keys.key(old) == self.keys.key(new):
            return EQ
        if new["t"] == "Image":
            changed = self.keys.image_hash(old["c"][2][0]) != self.keys.image_hash(new["c"][2][0])
            return Script([], image_changed=changed)
        old_slots, new_slots = slots(old), slots(new)
        script = Script(
            [self.seq(a[1], b[1], b[0]) for a, b in zip(old_slots, new_slots)]
        )
        if new["t"] == "Div":
            for name in CAPTION_ATTRS:
                before, after = attr_value(old, name), attr_value(new, name)
                if after is not None and before != after:
                    script.captions[name] = self.seq(
                        caption_words(before or ""), caption_words(after), "inline"
                    )
        return script

    def seq(self, old: list[Node], new: list[Node], kind: str) -> SeqDiff:
        result = SeqDiff(status=["eq"] * len(new))

        if kind == "cell":  # rows are only paired when their cell counts match
            for index, (a, b) in enumerate(zip(old, new)):
                sub = self.node(a, b)
                if sub != EQ:
                    result.status[index] = "pair"
                    result.sub[index] = sub
            return result

        old_keys = [self.keys.key(item) for item in old]
        new_keys = [self.keys.key(item) for item in new]
        matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
        opcodes = matcher.get_opcodes()
        if kind == "inline":
            opcodes = absorb_small_islands(opcodes, old_keys)

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                continue
            pending: list[Node] = []
            for old_index, new_index in self.align(old[i1:i2], new[j1:j2]):
                if new_index is None:
                    pending.append(old[i1 + old_index])
                    continue
                index = j1 + new_index
                if pending:
                    result.gaps.setdefault(index, []).extend(pending)
                    pending = []
                if old_index is None:
                    result.status[index] = "ins"
                else:
                    sub = self.node(old[i1 + old_index], new[index])
                    if sub != EQ:
                        result.status[index] = "pair"
                        result.sub[index] = sub
            if pending:
                result.gaps.setdefault(j2, []).extend(pending)
        return result

    def align(self, old: list[Node], new: list[Node]) -> list[tuple[int | None, int | None]]:
        """Order-preserving pairing of similar nodes in a changed region."""

        rows, cols = len(old), len(new)
        sims = [[0.0] * cols for _ in range(rows)]
        for i in range(rows):
            for j in range(cols):
                sims[i][j] = self.similarity(old[i], new[j])

        score = [[0.0] * (cols + 1) for _ in range(rows + 1)]
        for i in range(1, rows + 1):
            for j in range(1, cols + 1):
                best = max(score[i - 1][j], score[i][j - 1])
                if sims[i - 1][j - 1] >= PAIR_THRESHOLD:
                    best = max(best, score[i - 1][j - 1] + sims[i - 1][j - 1])
                score[i][j] = best

        pairs: list[tuple[int, int]] = []
        i, j = rows, cols
        while i and j:
            sim = sims[i - 1][j - 1]
            if sim >= PAIR_THRESHOLD and score[i][j] == score[i - 1][j - 1] + sim:
                pairs.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            elif score[i][j] == score[i - 1][j]:
                i -= 1
            else:
                j -= 1
        pairs.reverse()

        # Removed items come before added items within each interval.
        result: list[tuple[int | None, int | None]] = []
        last_old = last_new = 0
        for pair_old, pair_new in [*pairs, (rows, cols)]:
            result += [(k, None) for k in range(last_old, pair_old)]
            result += [(None, k) for k in range(last_new, pair_new)]
            if pair_old < rows or pair_new < cols:
                result.append((pair_old, pair_new))
            last_old, last_new = pair_old + 1, pair_new + 1
        return result


def absorb_small_islands(opcodes: list[tuple], old_keys: list[str]) -> list[tuple]:
    """Merge short unchanged runs between two edits so rewrites read as one edit."""

    ops = [list(op) for op in opcodes]
    changed = True
    while changed:
        changed = False
        for index in range(1, len(ops) - 1):
            tag, i1, i2, _, _ = ops[index]
            if tag != "equal" or ops[index - 1][0] == "equal" or ops[index + 1][0] == "equal":
                continue
            words = [key for key in old_keys[i1:i2] if key != " "]
            if len(words) <= 1 and all(len(word) <= 3 for word in words):
                before, after = ops[index - 1], ops[index + 1]
                ops[index - 1 : index + 2] = [["replace", before[1], after[2], before[3], after[4]]]
                changed = True
                break
    return [tuple(op) for op in ops]


# ---------------------------------------------------------------------------
# Rendering the merged document


def typst_bool(value: bool) -> str:
    return "true" if value else "false"


def raw_inline(text: str) -> Node:
    return {"t": "RawInline", "c": ["typst", text]}


def raw_block(text: str) -> Node:
    return {"t": "RawBlock", "c": ["typst", text]}


def strip_attr(attr: list) -> list:
    keep = [kv for kv in attr[2] if kv[0] not in ("label", "fig-cap", "tbl-cap", "execution_count")]
    return ["", attr[1], keep]


def is_float_container(node: Node) -> bool:
    if node["t"] in ("Figure", "Table", "_Row", "_Cell", "Note"):
        return True
    if node["t"] == "Div":
        ident, classes = node["c"][0][0], node["c"][0][1]
        return "cell" in classes or ident.startswith(CROSSREF_PREFIXES)
    return False


@dataclass
class Marker:
    content: list[Node]
    kind: str
    latest: bool
    nested: bool
    touched: bool
    section: str = ""


@dataclass
class Info:
    changed: bool = False
    latest: bool = False
    figure: str | None = None  # "changed" when a rendered image differs
    figure_latest: bool = False

    def merge(self, other: "Info") -> None:
        self.changed |= other.changed
        self.latest |= other.latest
        if other.figure and not self.figure:
            self.figure = other.figure
        self.figure_latest |= other.figure_latest


def as_seq(diff: Any, index: int, length: int) -> SeqDiff:
    if diff == EQ:
        return SeqDiff(status=["eq"] * length)
    if diff == ALL_INS:
        return SeqDiff(status=["ins"] * length)
    return diff.seqs[index]


def item_diff(seq: SeqDiff, index: int) -> Any:
    status = seq.status[index]
    if status == "eq":
        return EQ
    if status == "ins":
        return ALL_INS
    return seq.sub[index]


class Renderer:
    def __init__(self, keys: Keys, head_ids: set[str]) -> None:
        self.keys = keys
        self.head_ids = head_ids
        self.entries: list[dict[str, Any]] = []
        self.section = ""

    # -- removed content ----------------------------------------------------

    def sanitise(self, node: Node) -> Node | None:
        """Make removed content safe to render next to the head version."""

        t, c = node["t"], node.get("c")
        if t == "RawBlock":
            return None
        if t == "Header":
            return {"t": "Para", "c": [{"t": "Strong", "c": self.sanitise_inlines(c[2])}]}
        if t == "Figure":
            caption = [
                inline
                for block in c[1][1]
                for inline in (block["c"] if block["t"] in ("Para", "Plain") else [])
            ]
            blocks = self.sanitise_blocks(c[2])
            if caption:
                blocks.append({"t": "Para", "c": self.sanitise_inlines(caption)})
            return {"t": "Div", "c": [strip_attr(c[0]), blocks]}
        if t == "Cite":
            ids = [item["citationId"] for item in c[0]]
            if any(i.startswith(CROSSREF_PREFIXES) and i not in self.head_ids for i in ids):
                return {"t": "Str", "c": "".join(text_of(c[1])) or "@" + ids[0]}
            return node
        if t == "Image":
            return {"t": "Image", "c": [strip_attr(c[0]), c[1], c[2]]}
        if t in ("Div", "Span", "Header", "Table", "Code", "CodeBlock", "Link"):
            node = {"t": t, "c": [strip_attr(c[0]), *c[1:]]}
        node_slots = slots(node)
        if not node_slots:
            return node
        seqs = []
        for kind, items in node_slots:
            if kind == "inline":
                seqs.append(self.sanitise_inlines(items))
            else:
                seqs.append([s for s in (self.sanitise(item) for item in items) if s is not None])
        return rebuild(node, seqs)

    def sanitise_blocks(self, blocks: list[Node]) -> list[Node]:
        return [s for s in (self.sanitise(block) for block in blocks) if s is not None]

    def sanitise_inlines(self, inlines: list[Node]) -> list[Node]:
        return [s for s in (self.sanitise(inline) for inline in inlines) if s is not None]

    def mark_all_inlines(self, node: Node, mark: str, latest: bool) -> Node:
        """Wrap every inline run inside node in the given mark."""

        node_slots = slots(node)
        if not node_slots:
            return node
        seqs = []
        for kind, items in node_slots:
            if kind == "inline":
                seqs.append(self.coalesce([(item, (mark, latest)) for item in items]))
            else:
                seqs.append([self.mark_all_inlines(item, mark, latest) for item in items])
        return rebuild(node, seqs)

    # -- wrappers -----------------------------------------------------------

    def wrap_block(
        self,
        content: list[Node],
        kind: str,
        latest: bool,
        nested: bool = False,
        touched: bool | None = None,
    ) -> list[Any]:
        """Mark blocks; latest picks the colour, touched the summary flag.

        Returns a placeholder so that runs of added or removed blocks can be
        merged into one marker by materialise().
        """

        touched = latest if touched is None else touched
        return [Marker(content, kind, latest, nested, touched, self.section)]

    def materialise(self, items: list[Any]) -> list[Node]:
        merged: list[Any] = []
        for item in items:
            previous = merged[-1] if merged else None
            if (
                isinstance(item, Marker)
                and isinstance(previous, Marker)
                and item.kind in ("ins", "del")
                and (item.kind, item.latest, item.nested) == (previous.kind, previous.latest, previous.nested)
            ):
                previous.content.extend(item.content)
                previous.touched |= item.touched
            else:
                merged.append(item)

        result: list[Node] = []
        for item in merged:
            if not isinstance(item, Marker):
                result.append(item)
                continue
            options = f'kind: "{item.kind}", latest: {typst_bool(item.latest)}'
            if item.nested:  # inside an already marked block: tint only, no bar or entry
                opening = f"#rv-block({options}, nested: true)["
            else:
                ident = f"rv-{len(self.entries) + 1}"
                self.entries.append(
                    {"id": ident, "kind": item.kind, "latest": item.touched, "section": item.section}
                )
                opening = f"#rv-block({options})[#metadata(none) <{ident}>"
            result += [raw_block(opening), *item.content, raw_block("];")]
        return result

    def coalesce(self, marked: list[tuple[Node, tuple[str, bool] | None]]) -> list[Node]:
        # Unmarked spaces between two runs with the same mark join the runs.
        for index in range(1, len(marked) - 1):
            node, mark = marked[index]
            if mark is None and node["t"] in SPACE_TYPES:
                before, after = marked[index - 1][1], marked[index + 1][1]
                if before is not None and before == after:
                    marked[index] = (node, before)

        result: list[Node] = []
        index = 0
        while index < len(marked):
            node, mark = marked[index]
            if mark is None:
                result.append(node)
                index += 1
                continue
            run = []
            while index < len(marked) and marked[index][1] == mark:
                run.append(marked[index][0])
                index += 1
            kind, latest = mark
            result.append(raw_inline(f"#rv-{kind}(latest: {typst_bool(latest)})["))
            result.extend(run)
            result.append(raw_inline("];"))
        return result

    # -- traversal ----------------------------------------------------------

    def gap(self, seq_b: SeqDiff, seq_p: SeqDiff, index: int) -> list[tuple[Node, bool]]:
        """Removed nodes before index, flagged when the latest commit removed them."""

        from_base = seq_b.gaps.get(index, [])
        from_prev = list(seq_p.gaps.get(index, []))
        prev_keys = [self.keys.key(item) for item in from_prev]
        used: set[int] = set()
        result = []
        for item in from_base:
            key = self.keys.key(item)
            match = next((k for k, pk in enumerate(prev_keys) if pk == key and k not in used), None)
            if match is not None:
                used.add(match)
            result.append((item, match is not None))
        # Text added earlier in the PR and removed again by the latest commit.
        result += [(item, True) for k, item in enumerate(from_prev) if k not in used]
        return result

    def node(self, node: Node, diff_b: Any, diff_p: Any, wrap: bool, wrapped: bool) -> tuple[Node, Info]:
        info = Info()
        if diff_b == EQ:
            return node, info
        if node["t"] == "Image":
            if diff_b == ALL_INS or diff_b.image_changed:
                info.changed = True
                info.figure = "changed"
                info.figure_latest = diff_p == ALL_INS or (
                    isinstance(diff_p, Script) and diff_p.image_changed
                )
                info.latest |= info.figure_latest
            return node, info

        if isinstance(diff_b, Script) and diff_b.captions:
            node, caption_info = self.captions(node, diff_b, diff_p)
            info.merge(caption_info)

        if not slots(node):
            # A leaf such as an added code or raw block has no children to mark.
            # Raw blocks are format markup, not paper content, so they count as unchanged.
            info.changed = node["t"] != "RawBlock"
            info.latest = info.changed and diff_p == ALL_INS
            return node, info

        wrap = wrap and not is_float_container(node)
        seqs = []
        for index, (kind, items) in enumerate(slots(node)):
            seq_b = as_seq(diff_b, index, len(items))
            seq_p = as_seq(diff_p, index, len(items))
            new_items, seq_info = self.seq(items, kind, seq_b, seq_p, wrap, wrapped)
            seqs.append(new_items)
            info.merge(seq_info)
        return rebuild(node, seqs), info

    def captions(self, node: Node, diff_b: Script, diff_p: Any) -> tuple[Node, Info]:
        info = Info()
        attr = node["c"][0]
        values = []
        for name, value in attr[2]:
            if name in diff_b.captions:
                words = caption_words(value)
                if isinstance(diff_p, Script):
                    seq_p = diff_p.captions.get(name) or as_seq(EQ, 0, len(words))
                else:
                    seq_p = as_seq(diff_p, 0, len(words))
                marked, caption_info = self.seq(words, "inline", diff_b.captions[name], seq_p, False, True)
                value = caption_markdown(marked)
                info.merge(caption_info)
            values.append([name, value])
        return {"t": "Div", "c": [[attr[0], attr[1], values], node["c"][1]]}, info

    def seq(
        self,
        items: list[Node],
        kind: str,
        seq_b: SeqDiff,
        seq_p: SeqDiff,
        wrap: bool,
        wrapped: bool,
        top: bool = False,
    ) -> tuple[list[Node], Info]:
        info = Info()
        if kind == "inline":
            marked: list[tuple[Node, tuple[str, bool] | None]] = []
            for index in range(len(items) + 1):
                for removed, latest in self.gap(seq_b, seq_p, index):
                    clean = self.sanitise(removed)
                    if clean is not None:
                        marked.append((clean, ("del", latest)))
                        info.changed = True
                        info.latest |= latest
                if index == len(items):
                    break
                item = items[index]
                status = seq_b.status[index]
                diff_p = item_diff(seq_p, index)
                if status == "eq":
                    marked.append((item, None))
                elif status == "ins" and not (isinstance(diff_p, Script) and slots(item)):
                    latest = diff_p == ALL_INS
                    marked.append((item, ("ins", latest)))
                    info.changed = True
                    info.latest |= latest
                else:
                    diff_b = ALL_INS if status == "ins" else seq_b.sub[index]
                    new_item, item_info = self.node(item, diff_b, diff_p, False, True)
                    marked.append((new_item, None))
                    info.merge(item_info)
            return self.coalesce(marked), info

        result: list[Node] = []
        for index in range(len(items) + 1):
            for removed, latest in self.gap(seq_b, seq_p, index):
                result.extend(self.removed(removed, kind, latest, wrap, wrapped))
                info.changed = True
                info.latest |= latest
            if index == len(items):
                break
            item = items[index]
            if top and item["t"] == "Header":
                self.section = " ".join(text_of(item["c"][2]))
            status = seq_b.status[index]
            diff_p = item_diff(seq_p, index)
            if status == "eq":
                result.append(item)
                continue

            if status == "ins" and kind == "block" and wrap and item["t"] != "RawBlock":
                # A whole new block: tint it rather than highlighting every word,
                # and show only the latest commit's own edits inside it.
                latest = diff_p == ALL_INS
                inner_b = EQ if diff_p in (EQ, ALL_INS) else diff_p
                new_item, inner = self.node(item, inner_b, diff_p, False, True)
                if contains(item, "Image"):
                    block_kind, colour_latest = "fig-added", latest or inner.figure_latest
                else:
                    block_kind, colour_latest = "ins", latest
                result.extend(
                    self.wrap_block(
                        [new_item],
                        block_kind,
                        colour_latest,
                        nested=wrapped,
                        touched=latest or inner.latest,
                    )
                )
                info.changed = True
                info.latest |= latest or inner.latest
                continue

            diff_b = ALL_INS if status == "ins" else seq_b.sub[index]
            new_item, item_info = self.node(item, diff_b, diff_p, wrap, wrapped or (wrap and kind == "block"))
            info.merge(item_info)
            if kind == "block" and wrap and not wrapped and item_info.changed and item["t"] != "RawBlock":
                if item_info.figure:
                    result.extend(self.wrap_block([new_item], "fig-changed", item_info.figure_latest))
                else:
                    result.extend(self.wrap_block([new_item], "mod", item_info.latest))
            else:
                result.append(new_item)
        return self.materialise(result), info

    def removed(self, node: Node, kind: str, latest: bool, wrap: bool, wrapped: bool) -> list[Node]:
        clean = self.sanitise(node)
        if clean is None:
            return []
        if kind == "block" and wrap and not is_float_container(node):
            marked = self.mark_all_inlines(clean, "del", latest)
            return self.wrap_block([marked], "del", latest, nested=wrapped)
        if kind == "block" and wrap:  # a removed figure or table
            marked = self.mark_all_inlines(clean, "del", latest)
            return self.wrap_block([marked], "del", latest, nested=wrapped)
        return [self.mark_all_inlines(clean, "del", latest)]


# ---------------------------------------------------------------------------


def collect_ids(value: Any, ids: set[str]) -> set[str]:
    if isinstance(value, dict):
        c = value.get("c")
        if value.get("t") in ("Div", "Span", "Header", "Figure", "Table", "Image", "CodeBlock"):
            attr = c[1] if value["t"] == "Header" else c[0]
            if attr[0]:
                ids.add(attr[0])
        collect_ids(c, ids)
    elif isinstance(value, list):
        for item in value:
            collect_ids(item, ids)
    return ids


def summary_block(entries: list[dict[str, Any]], base: str, prev: str | None, head: str) -> Node:
    rows = ",\n".join(
        f'  (id: <{e["id"]}>, kind: "{e["kind"]}", latest: {typst_bool(e["latest"])}, '
        f"section: {json.dumps(e['section'])})"
        for e in entries
    )
    rows += "," if entries else ""  # a one-element Typst array needs the comma
    return raw_block(
        "#pagebreak(weak: true)\n"
        f"#rv-summary(base: {json.dumps(base)}, prev: {json.dumps(prev or '')}, "
        f"head: {json.dumps(head)}, (\n{rows}\n))"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, type=Path, help="AST JSON of the PR base")
    parser.add_argument("--prev", type=Path, help="AST JSON before the latest commit")
    parser.add_argument("--head", required=True, type=Path, help="AST JSON of the PR head")
    parser.add_argument("--root", type=Path, default=Path("."), help="directory image paths are relative to")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--base-label", default="base")
    parser.add_argument("--prev-label", default="")
    parser.add_argument("--head-label", default="head")
    args = parser.parse_args()

    base = json.loads(args.base.read_text(encoding="utf-8"))
    head = json.loads(args.head.read_text(encoding="utf-8"))
    prev = json.loads(args.prev.read_text(encoding="utf-8")) if args.prev else base

    keys = Keys(args.root)
    differ = Differ(keys)
    diff_b = differ.seq(base["blocks"], head["blocks"], "block")
    diff_p = differ.seq(prev["blocks"], head["blocks"], "block")

    renderer = Renderer(keys, collect_ids(head["blocks"], set()))
    blocks, _ = renderer.seq(head["blocks"], "block", diff_b, diff_p, True, False, top=True)
    blocks.insert(0, summary_block(renderer.entries, args.base_label, args.prev_label, args.head_label))

    head["blocks"] = blocks
    args.output.write_text(json.dumps(head), encoding="utf-8")

    if args.manifest:
        manifest = {
            "base": args.base_label,
            "prev": args.prev_label,
            "head": args.head_label,
            "changes": renderer.entries,
            "change_count": len(renderer.entries),
            "latest_change_count": sum(e["latest"] for e in renderer.entries),
            "figure_changes": sum(e["kind"].startswith("fig-") for e in renderer.entries),
        }
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"Marked {len(renderer.entries)} changed block(s), "
        f"{sum(e['latest'] for e in renderer.entries)} touched by the latest commit."
    )


if __name__ == "__main__":
    main()
