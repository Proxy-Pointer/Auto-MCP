import os
from graph import app

def cleanup_db():
    """Reset test.db and remove stray database.db created by SQLite MCP default."""
    for f in ["test.db", "database.db"]:
        if os.path.exists(f):
            os.remove(f)
    open("test.db", "w").close()  # Create a fresh empty test.db

# ─── Select which scenario to run (1-5) ──────────────────────────────────────
SCENARIO = 3

SCENARIOS = {
    1: {
        "name": "India Trip Planner",
        "task": (
            "Plan a 5-day trip across India covering an off-the-beaten-path beach, a remote Himalayan mountain destination (NOT Manali), "
            "and a lesser-known desert town. For each location search for the top 3 attractions and the "
            "best local dish to try. Then check the current weather at each location. "
            "Finally, compile everything into a formatted travel itinerary"
        ),
    },
    2: {
        "name": "AI Startup Competitive Intelligence Brief",
        "task": (
            "Research the 3 leading AI coding assistant startups: Cursor, Windsurf, and GitHub Copilot. "
            "For each, search for their latest product update or funding news, then fetch the HTML content "
            "of their official homepage. "
            "Compile a one-paragraph competitive summary for each and save the full brief to ai_coding_brief_2.md"
        ),
    },
    3: {
        "name": "GitHub Trending AI Tools Digest",
        "task": (
            "Search GitHub for 3 popular open-source AI repositories from 2024. "
            "For each repository, fetch its README to extract what it does and how to install it. "
            "Save a curated 'What is Hot in AI this Week' digest to trending_ai_tools_3.md"
        ),
    },
    4: {
        "name": "Weather-Aware Mumbai Weekend Activity Planner",
        "task": (
            "Get the current weather and 3-day forecast for Mumbai, India. "
            "Based on the forecasted conditions, search for the best indoor activities "
            "for rainy days and outdoor activities for clear days in Mumbai. "
            "Save a day-by-day weekend activity plan to mumbai_weekend_4.md"
        ),
    },
    5: {
        "name": "Tech Stock Market Briefing",
        "task": (
            "You are a market analyst. I need a comprehensive daily briefing for five companies: Apple (AAPL), Google (GOOGL), Microsoft (MSFT), NVIDIA (NVDA), and Meta (META). "
            "For each company, fetch the current stock price and today's percentage change, and search for the latest financial news or analyst ratings published today. "
            "Compile all the collected data into a concise markdown report and save it to market_briefing_5.md"
        ),
    },
    6: {
        "name": "Mumbai Weather SQLite Auto-Heal Test",
        "task": (
            "Fetch the current weather in Mumbai. Then, insert the temperature and conditions into a SQLite database named test.db. "
            "Use a table named 'weather' for the insertion."
        ),
    },
}

if __name__ == "__main__":
    scenario = SCENARIOS[SCENARIO]
    print(f"Starting Scenario {SCENARIO}: {scenario['name']}")
    print("-" * 60)

    # Clean slate before every run
    cleanup_db()

    initial_state = {"task": scenario["task"], "scenario_number": SCENARIO}

    # Stream the graph and print node-level output
    for event in app.stream(initial_state):
        for key, value in event.items():
            print(f"\n--- Output from Node '{key}' ---")
            print(f"Status: {value.get('status')}")
            print(f"Current Step: {value.get('current_step')} / {len(value.get('plan', []))}")
            if value.get('errors'):
                print(f"Active Errors: {value['errors']}")

    # Clean up stray default database.db created by mcp-server-sqlite npx cache init
    if os.path.exists("database.db"):
        os.remove("database.db")

    print("\nWorkflow Execution Complete.")
