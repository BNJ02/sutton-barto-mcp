# sutton-barto-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes the full text of
*Reinforcement Learning: An Introduction* (2nd edition) by Richard S. Sutton and
Andrew G. Barto, so an LLM can search it, pull a specific page, or read a whole
chapter instead of guessing from memory.

Built with [FastMCP](https://github.com/jlowin/fastmcp). Runs locally over stdio,
or as a remote HTTP server with OAuth for clients like claude.ai.

> **Licensing:** the code is MIT. The book text under `pages_corrected/` and
> `ocr_raw/` is **not** — it belongs to the authors and MIT Press. Read
> [`NOTICE.md`](NOTICE.md) before you reuse anything from those directories.

## Tools

| Tool | What it does |
|---|---|
| `search(query, max_results=5)` | Case-insensitive term search across all 548 pages. Returns the best-scoring pages with a surrounding excerpt and the chapter each one falls in. |
| `get_page(page_number)` | One page verbatim, 1–548. Page numbers are PDF page numbers, not the printed body numbering. |
| `get_chapter(chapter_number)` | A whole chapter, 0–24. Index `0` is the second-edition preface; `3` is Chapter 1. Call `list_chapters` for the mapping. |
| `list_chapters()` | All 25 sections with their page ranges. |

Math is returned as LaTeX, as it appears in the source text.

## Install

Python 3.10 or newer.

```bash
git clone https://github.com/BNJ02/sutton-barto-mcp.git
cd sutton-barto-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

### Local (stdio)

This is the default and needs no configuration:

```bash
python server.py
```

To register it with Claude Code:

```bash
claude mcp add rlbook -- /absolute/path/to/.venv/bin/python /absolute/path/to/server.py
```

Or, for any client that reads a JSON config:

```json
{
  "mcpServers": {
    "rlbook": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

### Local editor integrations (Continue, Zed, ...)

Most editors speak stdio, so this involves no authentication at all.

The shortest path is [`continue-mcp.yaml`](continue-mcp.yaml) in this repo: copy
it to `.continue/mcpServers/rlbook.yaml` at your workspace root (or
`~/.continue/mcpServers/rlbook.yaml` to get it in every workspace), edit the one
path in it, and reload the window.

```yaml
name: rlbook
version: 0.0.1
schema: v1

mcpServers:
  - name: rlbook
    command: /absolute/path/to/sutton-barto-mcp/.venv/bin/python
    args:
      - /absolute/path/to/sutton-barto-mcp/server.py
```

On Windows the interpreter is `.venv\Scripts\python.exe`:

```yaml
mcpServers:
  - name: rlbook
    command: 'C:\Users\you\Documents\sutton-barto-mcp\.venv\Scripts\python.exe'
    args:
      - 'C:\Users\you\Documents\sutton-barto-mcp\server.py'
```

If you have [uv](https://docs.astral.sh/uv/), `command: uv` with
`args: [run, --directory, <repo>, server.py]` skips the install step entirely —
it builds the environment from `pyproject.toml` on first launch. The commented
variants are all in [`continue-mcp.yaml`](continue-mcp.yaml).

Four things that bite:

- MCP tools only show up in Continue's **agent** mode, not chat or edit.
- `name`, `version` and `schema` are mandatory at the top of a Continue block.
  Without them the file is ignored silently.
- **Never double-quote a Windows path in YAML.** `"C:\Users\..."` fails to
  parse, because `\U` is read as an escape sequence. Use single quotes, no
  quotes, or forward slashes.
- `cwd:` is unnecessary: `server.py` finds `pages_corrected/` relative to its
  own location, not the working directory.

If you would rather run one long-lived server and point several workspaces at a
URL, there is an unauthenticated HTTP mode:

```bash
python server.py --http --no-auth --port=8003
```

```yaml
mcpServers:
  - name: rlbook
    type: streamable-http
    url: http://127.0.0.1:8003/mcp
```

`--no-auth` binds to `127.0.0.1` and refuses any other host, because a server
with no authentication on a routable address hands all its tools to anyone who
can reach the port. Do not put this mode behind a tunnel or reverse proxy — use
the OAuth mode below for anything off-machine.

### Remote (HTTP + OAuth)

For clients that connect over the network. Copy `.env.example` to `.env` and
fill it in first — the server exits if `MCP_CLIENT_SECRET` is missing:

```bash
cp .env.example .env
# generate a secret and a PIN, then edit .env
openssl rand -hex 32
```

```bash
set -a && source .env && set +a
python server.py --http --port=8003
```

`MCP_BASE_URL` must be the public HTTPS URL the client will reach, since it is
used to build the OAuth redirect URIs. Put the server behind a reverse proxy or
a tunnel that terminates TLS.

Authorization flow: the client is redirected to a page served by this app, you
enter `MCP_ACCESS_PIN`, and the authorization code is issued. Registered clients
and issued tokens are persisted in `oauth_state.db`, which is gitignored — it
holds live credentials, so keep it out of version control and off backups you
share.

## Repository layout

```
server.py             MCP server: tools, chapter map, page loader
persistent_oauth.py   SQLite-backed OAuth provider + PIN approval routes
pages_corrected/      Extracted book text, 10 pages per file (see NOTICE.md)
ocr_raw/              Raw OCR output, kept for diffing corrections
  nougat/             Nougat output — good at math, occasionally hallucinates
  pymupdf/            PyMuPDF text layer — literal, poor at math
```

`pages_corrected/` is what the server actually reads. Each file holds
`--- Page N ---` delimiters that `_load_all_pages()` splits on, so any new page
file has to keep that marker format.

`ocr_raw/` is not used at runtime. It's there because the two extractors fail in
different ways — Nougat reconstructs equations well but drifts on dense pages,
PyMuPDF is faithful but flattens math — and having both makes it possible to
check a suspicious passage in `pages_corrected/` against them.

## The source PDF

Not included: it's ~70 MB and copyrighted. Get it from the authors:
<http://incompleteideas.net/book/the-book.html>

You only need it if you want to re-extract or fix pages. The server runs from
`pages_corrected/` alone.

## Known limitations

- **Page numbers are PDF-relative.** Page 23 of the PDF is the first page of
  Chapter 1. Printed page numbers in the book differ.
- **Search is literal.** It counts term occurrences; no stemming, no synonyms,
  no semantic ranking. `"eligibility traces"` scores pages containing either
  word, not the phrase.
- **All pages are held in memory.** Roughly 1.6 MB of text is loaded at import.
  Fine, but it means startup does real work.
- **OCR is imperfect.** Complex equations, figures, and tables are the weak
  spots. Check anything surprising against the PDF.

## Credits

The book is by Richard S. Sutton and Andrew G. Barto, published by The MIT
Press. This repository only wraps its text in an MCP interface. See
[`NOTICE.md`](NOTICE.md).
