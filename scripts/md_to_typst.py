#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import mistune


@dataclass
class Inline:
    kind: str
    text: str


@dataclass
class Block:
    kind: str
    level: int | None = None
    inlines: list[Inline] = field(default_factory=list)
    children: list["Block"] = field(default_factory=list)
    ordered: bool = False
    rows: list[list[list[Inline]]] = field(default_factory=list)
    header: list[list[Inline]] = field(default_factory=list)
    code: str = ""
    language: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a GBT Markdown spec to printable Typst.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", "-o", type=Path, required=True)
    return parser.parse_args()


def parse_markdown(text: str) -> list[dict]:
    markdown = mistune.create_markdown(renderer="ast", plugins=["table"])
    return markdown(text)


def text_of(inlines: Iterable[Inline]) -> str:
    return "".join(inline.text for inline in inlines)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def ast_inlines_to_ir(nodes: list[dict]) -> list[Inline]:
    result: list[Inline] = []
    for node in nodes:
        kind = node["type"]
        if kind == "text":
            result.append(Inline("text", node.get("raw", "")))
        elif kind == "codespan":
            result.append(Inline("code", normalize_text(node.get("raw", ""))))
        elif kind in {"strong", "emphasis", "strikethrough", "superscript", "subscript", "span"}:
            result.extend(ast_inlines_to_ir(node.get("children", [])))
        elif kind == "link":
            result.extend(ast_inlines_to_ir(node.get("children", [])))
        elif kind in {"softbreak", "linebreak"}:
            result.append(Inline("text", " "))
        elif kind == "blank_line":
            continue
        else:
            raw = node.get("raw")
            if raw:
                result.append(Inline("text", raw))
            else:
                result.extend(ast_inlines_to_ir(node.get("children", [])))
    return compact_inlines(result)


def compact_inlines(inlines: list[Inline]) -> list[Inline]:
    compacted: list[Inline] = []
    for inline in inlines:
        text = re.sub(r"\s+", " ", inline.text)
        if not text:
            continue
        if compacted and compacted[-1].kind == inline.kind == "text":
            compacted[-1].text += text
        elif compacted and compacted[-1].kind == "code" and inline.kind == "text":
            compacted.append(Inline(inline.kind, text))
        else:
            compacted.append(Inline(inline.kind, text))
    if compacted and compacted[0].kind == "text":
        compacted[0].text = compacted[0].text.lstrip()
    if compacted and compacted[-1].kind == "text":
        compacted[-1].text = compacted[-1].text.rstrip()
    for index, inline in enumerate(compacted[:-1]):
        next_inline = compacted[index + 1]
        if inline.kind == "code" and next_inline.kind == "text" and not next_inline.text.startswith((" ", ".", ",", ":", ";", ")", "]")):
            next_inline.text = " " + next_inline.text
    return [inline for inline in compacted if inline.text]


def ast_blocks_to_ir(nodes: list[dict]) -> list[Block]:
    blocks: list[Block] = []
    for node in nodes:
        kind = node["type"]
        if kind == "blank_line":
            continue
        if kind == "heading":
            blocks.append(Block("heading", level=node["attrs"]["level"], inlines=ast_inlines_to_ir(node["children"])))
        elif kind == "paragraph":
            blocks.append(Block("paragraph", inlines=ast_inlines_to_ir(node.get("children", []))))
        elif kind == "block_text":
            blocks.append(Block("paragraph", inlines=ast_inlines_to_ir(node.get("children", []))))
        elif kind == "list":
            blocks.append(list_to_ir(node))
        elif kind == "block_code":
            attrs = node.get("attrs") or {}
            blocks.append(Block("code", code=clean_code(node.get("raw", "")), language=attrs.get("info", "")))
        elif kind == "table":
            blocks.append(table_to_ir(node))
        elif kind == "thematic_break":
            blocks.append(Block("horizontal_rule"))
        elif kind in {"block_html", "raw_html"}:
            continue
        else:
            children = node.get("children", [])
            if children:
                blocks.extend(ast_blocks_to_ir(children))
    return blocks


def list_to_ir(node: dict) -> Block:
    attrs = node.get("attrs") or {}
    items: list[Block] = []
    for item in node.get("children", []):
        item_blocks = ast_blocks_to_ir(item.get("children", []))
        items.append(Block("list_item", children=item_blocks))
    return Block("list", children=items, ordered=bool(attrs.get("ordered")))


def table_to_ir(node: dict) -> Block:
    header: list[list[Inline]] = []
    rows: list[list[list[Inline]]] = []

    for child in node.get("children", []):
        if child["type"] == "table_head":
            header = [ast_inlines_to_ir(cell.get("children", [])) for cell in child.get("children", [])]
        elif child["type"] == "table_body":
            for row in child.get("children", []):
                rows.append([ast_inlines_to_ir(cell.get("children", [])) for cell in row.get("children", [])])

    return Block("table", header=header, rows=rows)


def clean_code(code: str) -> str:
    code = code.replace("\v", "\n").replace("\f", "\n")
    code = re.sub(r"\n{3,}", "\n\n", code)
    return code.rstrip("\n")


def normalize_document(blocks: list[Block]) -> list[Block]:
    normalized: list[Block] = []
    seen_h1: set[str] = set()

    for block in blocks:
        if block.kind == "horizontal_rule":
            continue

        if block.kind == "heading" and block.level == 1:
            heading_text = text_of(block.inlines)
            if heading_text in seen_h1:
                continue
            seen_h1.add(heading_text)

        if block.kind == "heading":
            block.inlines = clean_heading_inlines(block.inlines)
            if not text_of(block.inlines).strip() or text_of(block.inlines).strip() == "---":
                continue

        if block.kind == "table" and is_review_table(block):
            continue

        if block.kind in {"paragraph", "heading"}:
            block.inlines = remove_reference_parentheticals(block.inlines)

        if block.kind == "list":
            block = normalize_list(block)

        normalized.append(block)

    return normalized


def normalize_list(block: Block) -> Block:
    for item in block.children:
        normalized_children: list[Block] = []
        for child in item.children:
            if child.kind == "paragraph":
                child.inlines = remove_reference_parentheticals(child.inlines)
            elif child.kind == "list":
                child = normalize_list(child)
            normalized_children.append(child)
        item.children = normalized_children
    return block


def clean_heading_inlines(inlines: list[Inline]) -> list[Inline]:
    cleaned = compact_inlines(inlines)
    if cleaned and cleaned[-1].kind == "text":
        cleaned[-1].text = re.sub(r"\s*\{#[^}]+\}\s*$", "", cleaned[-1].text).strip()
    return [inline for inline in cleaned if inline.text]


def is_review_table(block: Block) -> bool:
    if block.kind != "table" or block.rows:
        return False
    header = " ".join(text_of(cell) for cell in block.header)
    return "Reviewed" in header and "Approved" in header and "Needs changes" in header


def remove_reference_parentheticals(inlines: list[Inline]) -> list[Inline]:
    out: list[Inline] = []
    i = 0
    while i < len(inlines):
        inline = inlines[i]
        if inline.kind == "text":
            text = inline.text
            cleaned_text = re.sub(r"\s*\((?:https?://|[\w./-]+\.html#|#[-\w.]+)[^)]+\)", "", text)
            if cleaned_text != text:
                if cleaned_text:
                    out.append(Inline("text", cleaned_text))
                i += 1
                continue

            if "(" in text:
                prefix, _, suffix = text.rpartition("(")
                collected: list[Inline] = []
                j = i + 1

                while j < len(inlines):
                    candidate = inlines[j]
                    if candidate.kind == "text" and ")" in candidate.text:
                        before_close, _, after_close = candidate.text.partition(")")
                        collected.append(Inline(candidate.kind, before_close))
                        label = text_of(collected).strip()
                        if is_reference_label(label):
                            if prefix.strip():
                                out.append(Inline("text", prefix.rstrip()))
                            if after_close.strip():
                                out.append(Inline("text", after_close.lstrip()))
                            i = j + 1
                            break
                        out.append(inline)
                        i += 1
                        break
                    collected.append(candidate)
                    j += 1
                else:
                    out.append(inline)
                    i += 1
                continue

            out.append(inline)
        else:
            out.append(inline)
        i += 1

    return compact_inlines(out)


def is_reference_label(text: str) -> bool:
    return bool(
        re.match(r"^(?:https?://|[\w./-]+\.html#|#[-\w.]+)", text)
        or re.match(r"^[\w./-]+#[\w.-]+", text)
    )


def escape_typst_text(text: str) -> str:
    replacements = {
        "\\": "\\\\",
        "#": "\\#",
        "$": "\\$",
        "%": "\\%",
        "&": "\\&",
        "_": "\\_",
        '"': '\\"',
        "{": "\\{",
        "}": "\\}",
        "[": "\\[",
        "]": "\\]",
        "<": "\\<",
        ">": "\\>",
        "@": "\\@",
        "*": "\\*",
        "`": "\\`",
    }
    return "".join(replacements.get(char, char) for char in text)


def typst_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def render_inlines(inlines: list[Inline]) -> str:
    parts: list[str] = []
    for inline in inlines:
        if inline.kind == "code":
            parts.append(f"#raw({typst_string(inline.text)})")
        else:
            parts.append(escape_typst_text(inline.text))
    return "".join(parts)


def render_typst(blocks: list[Block], title: str) -> str:
    body = "\n\n".join(render_block(block) for block in blocks)
    return f"""#import \"template.typ\": doc, wide-table, code-block

#show: doc.with(title: {typst_string(title)})

{body}
"""


def render_block(block: Block) -> str:
    if block.kind == "heading":
        level = "=" * min(block.level or 1, 5)
        return f"{level} {render_inlines(block.inlines)}"
    if block.kind == "paragraph":
        return render_inlines(block.inlines)
    if block.kind == "list":
        return render_list(block)
    if block.kind == "table":
        return render_table(block)
    if block.kind == "code":
        return f"#code-block({typst_string(block.code)})"
    return ""


def render_list(block: Block) -> str:
    marker = "+" if block.ordered else "-"
    lines: list[str] = []
    for item in block.children:
        item_lines = render_list_item(item).splitlines() or [""]
        lines.append(f"{marker} {item_lines[0]}")
        for continuation in item_lines[1:]:
            lines.append(f"  {continuation}")
    return "\n".join(lines)


def render_list_item(item: Block) -> str:
    rendered: list[str] = []
    for child in item.children:
        rendered_child = render_block(child)
        if rendered_child:
            rendered.append(rendered_child)
    return "\n".join(rendered)


def render_table(block: Block) -> str:
    rows = [block.header] + block.rows
    rendered_rows = [
        "(" + ", ".join(f"[{render_inlines(cell)}]" for cell in row) + ")"
        for row in rows
    ]
    column_count = max((len(row) for row in rows), default=0)
    return "#wide-table(\n" + f"  columns: {column_count},\n" + "  rows: (\n    " + ",\n    ".join(rendered_rows) + ",\n  ),\n)"


def write_template(path: Path) -> None:
    path.write_text(
        """#let doc(title: none, body) = {
  set document(title: title)
  set page(
    paper: "a4",
    margin: (x: 8mm, y: 9mm),
    columns: 2,
    numbering: "1",
  )
  set text(font: "Libertinus Serif", size: 8.35pt)
  set par(justify: false, leading: 0.52em, spacing: 0.62em)
  set columns(gutter: 7mm)
  set list(spacing: 0.58em)
  set enum(spacing: 0.58em)

  show raw.where(block: false): set text(font: "Fira Code", size: 0.78em)

  show heading.where(level: 1): it => block(above: 11pt, below: 6pt, breakable: false)[
    #set text(size: 15pt, weight: "bold")
    #it
  ]
  show heading.where(level: 2): it => block(above: 9pt, below: 4.5pt, breakable: false)[
    #set text(size: 11.2pt, weight: "bold")
    #it
  ]
  show heading.where(level: 3): it => block(above: 7pt, below: 3.5pt, breakable: false)[
    #set text(size: 9.5pt, weight: "bold")
    #it
  ]
  show heading.where(level: 4): it => block(above: 6pt, below: 3pt, breakable: false)[
    #set text(size: 8.8pt, weight: "bold")
    #it
  ]

  body
}

#let code-block(source) = place(
  top,
  float: true,
  scope: "parent",
  block(
    width: 100%,
    fill: luma(245),
    stroke: (left: 1.5pt + black),
    inset: (x: 7pt, y: 6pt),
    radius: 1pt,
    breakable: false,
  )[
    #set text(font: "Fira Code", size: 6.8pt)
    #raw(source, block: true)
  ],
)

#let wide-table(columns: 0, rows: ()) = place(
  top,
  float: true,
  scope: "parent",
  block(width: 100%)[
    #set text(size: if columns >= 5 { 5.7pt } else { 6.8pt })
    #table(
      columns: columns,
      inset: (x: 2.8pt, y: 1.7pt),
      stroke: none,
      ..rows.flatten()
    )
  ],
)
""",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    source = args.source.read_text(encoding="utf-8")
    ast = parse_markdown(source)
    blocks = normalize_document(ast_blocks_to_ir(ast))
    title = document_title(blocks, args.source)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_template(args.output.parent / "template.typ")
    args.output.write_text(render_typst(blocks, title), encoding="utf-8")


def document_title(blocks: list[Block], source: Path) -> str:
    for block in blocks:
        if block.kind == "heading" and block.level == 1:
            title = text_of(block.inlines).strip()
            if title:
                return title
    return source.stem


if __name__ == "__main__":
    main()
