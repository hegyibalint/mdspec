# mdspec

Render Markdown specs — local files or Google Docs — to printable two-column
PDFs via [Typst](https://typst.app).

Input is a Markdown file or a Google Doc URL. Output is a PDF tuned for reading
on paper: two columns, dense type, code listings and tables placed as floats
with cross-references back to the text. For Google Docs, comment threads are
pulled in as numbered references with page links to the anchored quote, and
multi-tab docs render each tab as its own top-level section.

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
