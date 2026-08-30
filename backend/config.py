"""
config.py — Central configuration for the Auto-Heal MCP Orchestrator.
All environment-dependent paths, model names, API endpoints, and 
marketplace hints live here. Update this file to change behaviour 
without touching any orchestration logic.
"""

import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

# Root of the project (the directory containing this file)
PROJECT_ROOT = Path(__file__).parent.resolve()

# .env file is in the root of the project
DOTENV_PATH = PROJECT_ROOT / ".env"

# Default local Python venv (used to resolve uvx)
VENV_UVX_PATH = str(PROJECT_ROOT / ".venv" / "Scripts" / "uvx.exe")

# ─── LLM ──────────────────────────────────────────────────────────────────────

# Gemini model to use for all LLM calls (planning, discovery, execution)
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-3.1-flash-lite")

# Gemini API base URL (filled with model and key at call time)
GEMINI_API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)

# ─── GitHub Marketplace ───────────────────────────────────────────────────────

# GitHub repository search API
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

# How many GitHub results to consider per step
GITHUB_MAX_RESULTS = 10

# How many characters of a README to include in discovery prompts
README_SNIPPET_LENGTH = 1500

# Servers to completely ignore during discovery (by repo name or package name)
BLACKLISTED_SERVERS = [
   "finance-mcp",
   "FinanceMCP",
    "alex2yang97/yahoo-finance-mcp",
    "fast-filesystem-mcp"
]



# ─── Artifacts ────────────────────────────────────────────────────────────────

DEFAULT_ARTIFACT_DIR = str(PROJECT_ROOT / "artifacts")

# Directory where final task output files are saved
OUTPUT_DIR = str(PROJECT_ROOT / "output")


# ─── Result Storage ───────────────────────────────────────────────────────────

# Maximum characters of a tool result to pass to the LLM extractor
RESULT_MAX_CHARS = 8000


# ─── Recovery ─────────────────────────────────────────────────────────────────

# Maximum number of planner-based recovery retries per step before aborting
MAX_RECOVERY_RETRIES = 1

# Maximum number of alternate candidate servers to try before aborting
MAX_CANDIDATE_RETRIES = 3

# ─── Well-Known Servers ───────────────────────────────────────────────────────

# Pre-defined servers that are preferred if they match the step capability
WELL_KNOWN_SERVERS = [
   {
       "name": "yfinance-mcp",
       "description": "Stock market data and financial news via yfinance/Yahoo Finance. API key free. Provides stock prices, percentage changes, news, financials, analyst ratings.",
       "keywords": ["finance", "stock", "yfinance", "market", "stocks", "price", "ticker", "financial", "news"],
       "server": {
           "name": "yfinance-mcp",
           "command": VENV_UVX_PATH,
           "args": ["yfinance-mcp-server"],
           "source_link": "https://pypi.org/project/yfinance-mcp-server/"
       }
   },
    {
        "name": "sqlite-mcp",
        "description": "Official MCP server for SQLite databases. Allows querying, reading, and inserting data into local SQLite databases.",
        "keywords": ["sqlite", "sql", "database", "db", "table", "insert", "query"],
        "server": {
            "name": "sqlite-mcp",
            "command": VENV_UVX_PATH,
            "args": ["--with", "mcp<2", "mcp-server-sqlite", "--db-path", "test.db"],
            "source_link": "https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite"
        }
    },
    {
        "name": "github-mcp-server",
        "description": "Official MCP server for GitHub. Allows searching for repositories, fetching file contents (like READMEs), creating issues, and reading PRs.",
        "keywords": ["github", "repo", "repository", "code", "trending", "search", "readme", "pull request"],
        "server": {
            "name": "github-mcp-server",
            "command": "npx.cmd",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "source_link": "https://github.com/modelcontextprotocol/servers/tree/main/src/github"
        }
    }
]
