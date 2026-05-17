# mdspec

Compress long Markdown specs and Google Docs into tight, two-column
scientific-paper-style PDFs that are actually pleasant to print and read on
paper, via [Typst](https://typst.app).

A sprawling design doc that runs 30 screens in the browser typically collapses
to a handful of A4 pages: dense type, two columns, code listings and tables
floated with cross-references back to the text — the same conventions academic
papers use to fit a lot of content into a small page budget without feeling
cramped. The intent is to turn a hard-to-skim long document into something you
can mark up with a pen.

Input is a Markdown file or a Google Doc URL. For Google Docs, comment threads
are pulled in as numbered references with page links back to the anchored
quote, and multi-tab docs render each tab as its own top-level section.

## Install

You'll need [Typst](https://github.com/typst/typst) on your `PATH` and Python
3.10+. Then:

```sh
uv tool install git+https://github.com/hegyibalint/mdspec
```

(or `pipx install git+https://github.com/hegyibalint/mdspec`).

## Usage

Convert a local Markdown file:

```sh
mdspec convert path/to/spec.md
```

Convert a Google Doc by URL or ID:

```sh
mdspec convert "https://docs.google.com/document/d/<id>/edit"
```

The PDF lands in the current directory, named after the document's title. Use
`-o path/to/out.pdf` to override.

### Google authentication

First-time Google Doc conversion runs the OAuth consent flow in a browser; the
token is cached under `~/.cache/mdspec/`. You can also run it explicitly:

```sh
mdspec auth
```

You need an OAuth client config (a `gcp-oauth.keys.json` from a Google Cloud
project with the Drive and Docs APIs enabled). By default mdspec looks at
`~/.gt-headroom-mcp/gcp-oauth.keys.json`; override with
`--credentials path/to/keys.json` or the `MDSPEC_GOOGLE_CREDENTIALS` env var.

## License

Apache 2.0. See [LICENSE](LICENSE).
