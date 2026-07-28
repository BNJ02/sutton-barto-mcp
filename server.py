"""
MCP Server for "Reinforcement Learning: An Introduction" (2nd ed.)
by Richard S. Sutton and Andrew G. Barto.
"""

import os
import re
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP(
    "Reinforcement Learning: An Introduction",
    instructions=(
        "This server provides access to the full text of "
        "'Reinforcement Learning: An Introduction' (2nd edition, 2018/2020) "
        "by Sutton & Barto. The book contains LaTeX math notation. "
        "Use the tools to search, browse by page, or browse by chapter."
    ),
)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

PAGES_DIR = Path(__file__).parent / "pages_corrected"

# Chapter structure: (title, start_page, end_page)
CHAPTERS = [
    ("Preface to the Second Edition", 13, 16),
    ("Preface to the First Edition", 17, 18),
    ("Summary of Notation", 19, 22),
    ("1 – Introduction", 23, 44),
    ("Part I: Tabular Solution Methods", 45, 46),
    ("2 – Multi-armed Bandits", 47, 68),
    ("3 – Finite Markov Decision Processes", 69, 94),
    ("4 – Dynamic Programming", 95, 112),
    ("5 – Monte Carlo Methods", 113, 140),
    ("6 – Temporal-Difference Learning", 141, 162),
    ("7 – n-step Bootstrapping", 163, 180),
    ("8 – Planning and Learning with Tabular Methods", 181, 216),
    ("Part II: Approximate Solution Methods", 217, 218),
    ("9 – On-policy Prediction with Approximation", 219, 264),
    ("10 – On-policy Control with Approximation", 265, 278),
    ("11 – *Off-policy Methods with Approximation", 279, 308),
    ("12 – Eligibility Traces", 309, 342),
    ("13 – Policy Gradient Methods", 343, 360),
    ("Part III: Looking Deeper", 361, 362),
    ("14 – Psychology", 363, 398),
    ("15 – Neuroscience", 399, 442),
    ("16 – Applications and Case Studies", 443, 480),
    ("17 – Frontiers", 481, 502),
    ("References", 503, 540),
    ("Index", 541, 548),
]

MAX_PAGE = 548


def _load_all_pages() -> dict[int, str]:
    """Load all page files and return a dict mapping page number -> text."""
    pages: dict[int, str] = {}
    for filepath in sorted(PAGES_DIR.glob("pages_*.md")):
        content = filepath.read_text(encoding="utf-8")
        parts = re.split(r"--- [Pp][Aa][Gg][Ee] (\d+) ---\n?", content)
        for i in range(1, len(parts) - 1, 2):
            page_num = int(parts[i])
            page_text = parts[i + 1].strip()
            pages[page_num] = page_text
    return pages


PAGES = _load_all_pages()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
def get_page(page_number: int) -> str:
    """Retrieve the content of a specific page from the book (1 to 548).

    Args:
        page_number: Page number to retrieve (between 1 and 548).
    """
    if page_number < 1 or page_number > MAX_PAGE:
        return f"Error: page number must be between 1 and {MAX_PAGE} (received: {page_number})."
    text = PAGES.get(page_number)
    if text is None:
        return f"Page {page_number} not found."
    return f"--- PAGE {page_number} ---\n\n{text}"


@mcp.tool(annotations={"readOnlyHint": True})
def get_chapter(chapter_number: int) -> str:
    """Retrieve the full content of a chapter.

    Available chapters:
    0: Preface to the Second Edition, 1: Preface to the First Edition,
    2: Summary of Notation,
    3: Ch 1 – Introduction,
    4: Part I: Tabular Solution Methods,
    5: Ch 2 – Multi-armed Bandits,
    6: Ch 3 – Finite Markov Decision Processes,
    7: Ch 4 – Dynamic Programming,
    8: Ch 5 – Monte Carlo Methods,
    9: Ch 6 – Temporal-Difference Learning,
    10: Ch 7 – n-step Bootstrapping,
    11: Ch 8 – Planning and Learning with Tabular Methods,
    12: Part II: Approximate Solution Methods,
    13: Ch 9 – On-policy Prediction with Approximation,
    14: Ch 10 – On-policy Control with Approximation,
    15: Ch 11 – *Off-policy Methods with Approximation,
    16: Ch 12 – Eligibility Traces,
    17: Ch 13 – Policy Gradient Methods,
    18: Part III: Looking Deeper,
    19: Ch 14 – Psychology,
    20: Ch 15 – Neuroscience,
    21: Ch 16 – Applications and Case Studies,
    22: Ch 17 – Frontiers,
    23: References, 24: Index.

    Args:
        chapter_number: Chapter index (0 to 24).
    """
    if chapter_number < 0 or chapter_number >= len(CHAPTERS):
        chapter_list = "\n".join(
            f"  {i}: {title}" for i, (title, _, _) in enumerate(CHAPTERS)
        )
        return f"Error: invalid chapter number. Available chapters:\n{chapter_list}"

    title, start, end = CHAPTERS[chapter_number]
    parts = []
    parts.append(f"# {title} (pages {start}–{end})\n")
    for p in range(start, end + 1):
        text = PAGES.get(p)
        if text:
            parts.append(f"--- PAGE {p} ---\n{text}\n")
    return "\n".join(parts)


@mcp.tool(annotations={"readOnlyHint": True})
def search(query: str, max_results: int = 5) -> str:
    """Search the book for pages containing the given terms.

    Case-insensitive search. Returns the most relevant pages with a
    contextual excerpt.

    Args:
        query: Search terms to look for in the book.
        max_results: Maximum number of results to return (default: 5, max: 20).
    """
    if not query or not query.strip():
        return "Error: search query cannot be empty."

    max_results = min(max(1, max_results), 20)
    query_lower = query.lower()
    terms = query_lower.split()

    scored_pages: list[tuple[int, int, str]] = []

    for page_num, text in PAGES.items():
        text_lower = text.lower()
        score = sum(text_lower.count(term) for term in terms)
        if score > 0:
            scored_pages.append((score, page_num, text))

    if not scored_pages:
        return f"No results found for '{query}'."

    scored_pages.sort(key=lambda x: (-x[0], x[1]))
    results = scored_pages[:max_results]

    def _chapter_for_page(pnum: int) -> str:
        for title, start, end in CHAPTERS:
            if start <= pnum <= end:
                return title
        return "?"

    output_parts = [
        f"Results for '{query}' ({len(scored_pages)} pages found, "
        f"{len(results)} shown):\n"
    ]

    for score, page_num, text in results:
        chapter = _chapter_for_page(page_num)
        idx = text.lower().find(terms[0])
        start = max(0, idx - 100)
        end = min(len(text), idx + 200)
        excerpt = text[start:end].replace("\n", " ")
        if start > 0:
            excerpt = "..." + excerpt
        if end < len(text):
            excerpt = excerpt + "..."

        output_parts.append(
            f"Page {page_num} | {chapter}\n"
            f"   Score: {score} | Excerpt: {excerpt}\n"
        )

    return "\n".join(output_parts)


@mcp.tool(annotations={"readOnlyHint": True})
def list_chapters() -> str:
    """List all chapters of the book with their page ranges."""
    lines = ["Chapters of 'Reinforcement Learning: An Introduction':\n"]
    for i, (title, start, end) in enumerate(CHAPTERS):
        lines.append(f"  {i:2d}. {title} (pages {start}–{end})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if "--http" in sys.argv:
        port = 8003
        for arg in sys.argv:
            if arg.startswith("--port="):
                port = int(arg.split("=")[1])

        client_id = os.environ.get("MCP_CLIENT_ID", "mcp-rlbook")
        client_secret = os.environ.get("MCP_CLIENT_SECRET")
        base_url = os.environ.get("MCP_BASE_URL", f"http://0.0.0.0:{port}")

        if not client_secret:
            sys.exit(
                "MCP_CLIENT_SECRET is not set. HTTP mode registers an OAuth "
                "client and refuses to do so without a secret. See .env.example."
            )

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from persistent_oauth import PersistentOAuthProvider, register_pin_routes
        from mcp.shared.auth import OAuthClientInformationFull
        from mcp.server.auth.settings import ClientRegistrationOptions

        auth = PersistentOAuthProvider(
            base_url=base_url,
            db_path=str(Path(__file__).resolve().parent / "oauth_state.db"),
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )

        import asyncio

        client = OAuthClientInformationFull(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=[
                "http://localhost",
                "http://127.0.0.1",
                "https://claude.ai/api/mcp/auth_callback",
            ],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )
        asyncio.run(auth.register_client(client))

        register_pin_routes(mcp, auth)
        mcp.auth = auth
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run()
