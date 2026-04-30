#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import mistune


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "mdspec"


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
    label: str = ""
    emit_reference: bool = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a Markdown spec to a printable PDF via Typst.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth", help="Run the Google OAuth consent flow and cache the token")
    auth.add_argument(
        "--credentials",
        type=Path,
        help="OAuth client secrets JSON (default: $MDSPEC_GOOGLE_CREDENTIALS or ~/.gt-headroom-mcp/gcp-oauth.keys.json)",
    )
    auth.add_argument("--force", action="store_true", help="Re-authenticate even if a valid token exists")

    convert = subparsers.add_parser("convert", help="Convert a Google Doc URL or local Markdown file to PDF")
    convert.add_argument("source", help="Google Doc URL/id or local Markdown file path")
    convert.add_argument(
        "--credentials",
        type=Path,
        help="OAuth client secrets JSON (default: $MDSPEC_GOOGLE_CREDENTIALS or ~/.gt-headroom-mcp/gcp-oauth.keys.json)",
    )
    convert.add_argument("-o", "--output", type=Path, help="Output PDF path (default: <snake-cased-title>.pdf in CWD)")

    return parser.parse_args()


def snake_case(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


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
            link_text = text_of(ast_inlines_to_ir(node.get("children", [])))
            if link_text:
                result.append(Inline("link", link_text))
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
            list_block = list_to_ir(node)
            if list_block.children:
                blocks.append(list_block)
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
        if item_blocks:
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
    previous_heading: tuple[int, str] | None = None
    skipped_heading_level: int | None = None

    for block in blocks:
        if block.kind == "horizontal_rule":
            continue

        if skipped_heading_level is not None:
            if block.kind == "heading" and (block.level or 1) <= skipped_heading_level:
                skipped_heading_level = None
            else:
                continue

        if block.kind == "heading":
            block.inlines = clean_heading_inlines(block.inlines)
            heading_text = normalize_text(text_of(block.inlines))
            heading_key = (block.level or 1, heading_text)
            if not heading_text or heading_text == "---":
                previous_heading = None
                continue
            if previous_heading == heading_key:
                continue
            if is_ignored_print_section(block):
                skipped_heading_level = block.level or 1
                previous_heading = None
                continue
            previous_heading = heading_key
        else:
            previous_heading = None

        if block.kind == "table" and is_review_table(block):
            continue

        if block.kind in {"paragraph", "heading"}:
            block.inlines = drop_link_only_parentheticals(block.inlines)

        if block.kind == "list":
            block = normalize_list(block)

        normalized.append(block)

    label_floats(normalized)
    normalized = split_embedded_floats(normalized)
    attach_float_references(normalized)
    return normalized


def is_ignored_print_section(block: Block) -> bool:
    if block.kind != "heading" or block.level != 1:
        return False
    return normalize_text(text_of(block.inlines)).casefold() == "notes"


def attach_float_references(blocks: list[Block]) -> None:
    previous_text_block: Block | None = None
    for block in blocks:
        if block.kind in {"paragraph", "list"}:
            previous_text_block = block
            continue

        if block.kind in {"code", "table"} and block.emit_reference and previous_text_block:
            if attach_float_reference([previous_text_block], block):
                block.emit_reference = False
            continue

        if block.kind == "heading":
            previous_text_block = None


def split_embedded_floats(blocks: list[Block]) -> list[Block]:
    result: list[Block] = []
    for block in blocks:
        if block.kind == "list":
            result.extend(split_list_at_floats(block))
        else:
            result.append(block)
    return result


def split_list_at_floats(block: Block) -> list[Block]:
    result: list[Block] = []
    current_items: list[Block] = []

    def flush_items() -> None:
        nonlocal current_items
        if current_items:
            result.append(Block("list", children=current_items, ordered=block.ordered))
            current_items = []

    for item in block.children:
        kept_children: list[Block] = []
        floats: list[Block] = []
        for child in split_embedded_floats(item.children):
            if child.kind in {"code", "table"}:
                if attach_float_reference(kept_children, child):
                    child.emit_reference = False
                floats.append(child)
            else:
                kept_children.append(child)

        current_items.append(Block("list_item", children=kept_children))
        if floats:
            flush_items()
            result.extend(floats)

    flush_items()
    return result


def attach_float_reference(blocks: list[Block], floating: Block) -> bool:
    if not blocks:
        return False

    reference = Inline("text", " " + float_reference_text(floating.label or fallback_float_label(floating)))
    target = blocks[-1]
    if target.kind == "paragraph":
        target.inlines = compact_inlines([*target.inlines, reference])
        return True
    if target.kind == "list":
        for item in reversed(target.children):
            if attach_float_reference(item.children, floating):
                return True
    return False


def label_floats(blocks: list[Block]) -> None:
    counters = {"code": 0, "table": 0}

    def visit(items: list[Block]) -> None:
        for item in items:
            if item.kind == "code":
                counters["code"] += 1
                item.label = f"Listing {counters['code']}"
            elif item.kind == "table":
                counters["table"] += 1
                item.label = f"Table {counters['table']}"
            elif item.kind == "list":
                for child in item.children:
                    visit(child.children)

    visit(blocks)


def fallback_float_label(block: Block) -> str:
    return "Listing" if block.kind == "code" else "Table"


def normalize_list(block: Block) -> Block:
    for item in block.children:
        normalized_children: list[Block] = []
        for child in item.children:
            if child.kind == "paragraph":
                child.inlines = drop_link_only_parentheticals(child.inlines)
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


def drop_link_only_parentheticals(inlines: list[Inline]) -> list[Inline]:
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
        elif inline.kind == "link":
            parts.append(f"#underline[{escape_typst_text(inline.text)}]")
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
        label = block.label or "Listing"
        reference = f"{render_float_reference(label)}\n\n" if block.emit_reference else ""
        return f"{reference}#code-block({typst_string(block.code)}, caption: {typst_string(label + '.')})"
    return ""


def render_list(block: Block) -> str:
    function = "enum" if block.ordered else "list"
    items = ",\n".join(render_list_item(item) for item in block.children)
    return f"#{function}(\n{items},\n)"


def render_list_item(item: Block) -> str:
    rendered: list[str] = []
    for child in item.children:
        rendered_child = render_block(child)
        if rendered_child:
            rendered.append(rendered_child)
    body = "\n\n".join(rendered)
    return "[\n" + body + "\n]"


def render_table(block: Block) -> str:
    rows = [block.header] + block.rows if block.header else block.rows
    rendered_header = "(" + ", ".join(f"[{render_inlines(cell)}]" for cell in block.header) + ")"
    rendered_rows = [
        "(" + ", ".join(f"[{render_inlines(cell)}]" for cell in row) + ")"
        for row in block.rows
    ]
    column_count = max((len(row) for row in rows), default=0)
    label = block.label or "Table"
    reference = f"{render_float_reference(label)}\n\n" if block.emit_reference else ""
    return (
        reference
        +
        "#wide-table(\n"
        + f"  columns: {column_count},\n"
        + f"  caption: {typst_string(label + '.')},\n"
        + f"  header: {rendered_header},\n"
        + "  rows: (\n    "
        + ",\n    ".join(rendered_rows)
        + ",\n  ),\n)"
    )


def render_float_reference(label: str) -> str:
    return escape_typst_text(float_reference_text(label))


def float_reference_text(label: str) -> str:
    return f"See {label}."


def write_template(path: Path) -> None:
    path.write_text(
        """#let doc(title: none, body) = {
  set document(title: title)
  set page(
    paper: "a4",
    margin: (inside: 18mm, outside: 7mm, y: 9mm),
    columns: 2,
    numbering: "1",
  )
  set text(font: "Libertinus Serif", size: 8.35pt)
  set par(justify: false, leading: 0.52em, spacing: 0.62em)
  set columns(gutter: 7mm)
  set list(spacing: 0.58em)
  set enum(spacing: 0.58em)

  show raw.where(block: false): set text(font: "Fira Code", size: 0.78em)

  show heading.where(level: 1): it => block(above: 11pt, below: 8pt, breakable: false)[
    #set text(size: 15pt, weight: "bold")
    #it
  ]
  show heading.where(level: 2): it => block(above: 9pt, below: 6pt, breakable: false)[
    #set text(size: 11.2pt, weight: "bold")
    #it
  ]
  show heading.where(level: 3): it => block(above: 7pt, below: 4.8pt, breakable: false)[
    #set text(size: 9.5pt, weight: "bold")
    #it
  ]
  show heading.where(level: 4): it => block(above: 6pt, below: 4pt, breakable: false)[
    #set text(size: 8.8pt, weight: "bold")
    #it
  ]

  body
}

#let float-caption(caption) = if caption != none {
  block(width: 100%, above: 2.6pt, below: 0pt, breakable: false)[
    #set text(size: 6.2pt, style: "italic")
    #caption
  ]
}

#let code-block(source, caption: none) = place(
  auto,
  float: true,
  scope: "parent",
  clearance: 5pt,
  block(
    width: 100%,
    breakable: false,
  )[
    #align(left)[
      #block(
        width: 100%,
        fill: luma(245),
        stroke: (left: 1.5pt + black),
        inset: (x: 7pt, y: 6pt),
        radius: 1pt,
        breakable: false,
      )[
        #set text(font: "Fira Code", size: 6.8pt)
        #raw(source, block: true)
      ]
      #float-caption(caption)
    ]
  ],
)

#let wide-table(columns: 0, caption: none, header: (), rows: ()) = place(
  auto,
  float: true,
  scope: "parent",
  clearance: 5pt,
  block(width: 100%, above: 3pt, below: 5pt, breakable: false)[
    #align(left)[
      #set text(size: if columns >= 5 { 5.7pt } else { 6.4pt })
      #set par(justify: false, leading: 0.48em, spacing: 0pt)
      #let column-widths = if columns == 3 {
        (1.05fr, 1.55fr, 1.4fr)
      } else {
        columns
      }
      #table(
        columns: column-widths,
        inset: (x: 3.6pt, y: 3.2pt),
        stroke: (x, y) => if y == 0 {
          none
        } else if y == 1 {
          (bottom: 0.75pt + black)
        } else {
          (bottom: 0.25pt + luma(205))
        },
        fill: (x, y) => if y == 0 {
          luma(232)
        } else if calc.rem(y, 2) == 0 {
          luma(248)
        } else {
          none
        },
        table.header(repeat: true, ..header.map(cell => strong(cell))),
        ..rows.flatten()
      )
      #float-caption(caption)
    ]
  ],
)
""",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    if args.command == "auth":
        cmd_auth(args)
    elif args.command == "convert":
        cmd_convert(args)


def cmd_auth(args: argparse.Namespace) -> None:
    from mdspec.google import credentials_path, run_consent, token_path

    creds_file = credentials_path(args.credentials)
    run_consent(creds_file, force=args.force)
    print(f"authenticated; token cached at {token_path()}")


def cmd_convert(args: argparse.Namespace) -> None:
    if shutil.which("typst") is None:
        sys.exit("error: `typst` not found on PATH; install it from https://github.com/typst/typst")

    src = args.source
    src_path = Path(src)
    if src_path.is_file():
        source = src_path.read_text(encoding="utf-8")
        stem = src_path.stem
        title_fallback = src_path
    else:
        from mdspec.google import credentials_path, fetch_doc_with_comments

        creds_file = credentials_path(args.credentials)
        doc_id, drive_name, source = fetch_doc_with_comments(src, creds_file)
        stem = doc_id
        title_fallback = Path(drive_name)

    ast = parse_markdown(source)
    blocks = normalize_document(ast_blocks_to_ir(ast))
    title = document_title(blocks, title_fallback)

    if args.output:
        output_pdf = args.output
    else:
        slug = snake_case(title) or stem
        output_pdf = Path.cwd() / f"{slug}.pdf"

    cache = cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    typ_file = cache / f"{stem}.typ"
    write_template(cache / "template.typ")
    typ_file.write_text(render_typst(blocks, title), encoding="utf-8")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["typst", "compile", str(typ_file), str(output_pdf)], check=True)
    print(f"wrote {output_pdf}")


def document_title(blocks: list[Block], source: Path) -> str:
    for block in blocks:
        if block.kind == "heading" and block.level == 1:
            title = text_of(block.inlines).strip()
            if title:
                return title
    return source.stem


if __name__ == "__main__":
    main()
