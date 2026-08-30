import os
import json
import datetime
import requests
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

from docker_manager import DockerPackageManager
from mcp_client import sync_dynamic_mcp_execute
from config import (
    DOTENV_PATH, LLM_MODEL, GEMINI_API_URL_TEMPLATE,
    GITHUB_SEARCH_URL, GITHUB_MAX_RESULTS, README_SNIPPET_LENGTH,
    DEFAULT_ARTIFACT_DIR, MAX_RECOVERY_RETRIES, MAX_CANDIDATE_RETRIES, VENV_UVX_PATH,
    WELL_KNOWN_SERVERS, BLACKLISTED_SERVERS, RESULT_MAX_CHARS, OUTPUT_DIR
)

load_dotenv(DOTENV_PATH)
API_KEY = os.environ.get("GOOGLE_API_KEY")

class AgentState(TypedDict, total=False):
    task: str
    plan: List[str]
    current_step: int
    required_capabilities: List[str]
    candidate_servers: List[Dict[str, Any]]
    current_candidate_index: int
    execution_results: List[Any]
    errors: List[str]
    status: str 
    summary: str
    tool_cache: Dict[str, Dict[str, Any]]
    current_keyword: str
    retried: bool
    artifact_dir: str
    retry_count: int
    in_recovery: bool
    scenario_number: int
    log_queue: Any

def write_log(state: AgentState, message: str):
    out_dir = state.get("artifact_dir", "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "execution_log.md"), "a", encoding="utf-8") as f:
        f.write(message + "\n\n")
    if "log_queue" in state:
        state["log_queue"].put({"type": "log", "message": message})

def llm_generate(prompt: str) -> str:
    url = GEMINI_API_URL_TEMPLATE.format(model=LLM_MODEL, api_key=API_KEY)
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    raise Exception(f"LLM API Error: {response.text}")

def planner_node(state: AgentState) -> AgentState:
    # Create timestamped markdown log
    base_dir = state.get("artifact_dir", DEFAULT_ARTIFACT_DIR)
    if "run_" not in base_dir:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        scenario_num = state.get("scenario_number", "X")
        out_dir = os.path.join(base_dir, f"run_scenario{scenario_num}_{timestamp}")
        state["artifact_dir"] = out_dir
    else:
        out_dir = base_dir
        
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "execution_log.md")
    
    msg = f"# 🚀 Auto-Heal MCP Orchestrator Run\n\n**Task:** `{state.get('task')}`\n\n## 📝 Planning Phase\nBreaking down task..."
    
    is_recovering = bool(state.get("errors"))
    
    if is_recovering:
        msg_rec = f"## 📝 Recovery Planning Phase\nAnalyzing error to modify plan..."
        print(f"[planner_node] Analyzing error to modify plan")
        write_log(state, msg_rec)
        try:
            current_step_idx = state.get("current_step", 0)
            failed_step = state.get("plan", [])[current_step_idx] if state.get("plan") else "Unknown"
            last_error = state["errors"][-1]
            remaining_plan_json = json.dumps(state.get("plan", [])[current_step_idx:])
            
            prompt = f"""You are a Recovery Planner Agent.
The system is trying to accomplish: {state.get('task')}

The current remaining plan is:
{remaining_plan_json}

The step '{failed_step}' just failed with this error:
{last_error}

Create a NEW REMAINING plan (as a JSON array of strings) that prepends any necessary recovery steps (e.g. creating a missing resource or database table) before retrying the failed step or proceeding.
Output ONLY a JSON array of strings representing the new remaining steps.
"""
            plan_text = llm_generate(prompt)
            
            start_idx = plan_text.find('[')
            end_idx = plan_text.rfind(']')
            if start_idx != -1 and end_idx != -1:
                plan_text = plan_text[start_idx:end_idx+1]
                
            remaining_steps = json.loads(plan_text)
            past_steps = state.get("plan", [])[:current_step_idx]
            state["plan"] = past_steps + remaining_steps
            
            msg2 = "**LLM Generated Recovery Plan:**\n" + "\n".join([f"{i+1}. {step}" for i, step in enumerate(remaining_steps)])
            print(f"  -> LLM Generated Recovery Plan:\n{msg2}")
            write_log(state, msg2)
            
            state["errors"] = []
            state["in_recovery"] = True
            # Do NOT clear candidate_servers here — we want to reuse the same
            # already-discovered MCP server for all recovery steps.
            state["current_candidate_index"] = 0
            
        except Exception as e:
            msg3 = f"**LLM Error during recovery planning:** {e}"
            print(msg3)
            write_log(state, msg3)
            state["status"] = "failed"
            return state
            
    else:
        print(f"[planner_node] Breaking down task: {state.get('task')}")
        write_log(state, msg)
        try:
            prompt = f"""Break down the following task into logical steps to be completed by MCP servers: {state['task']}
Output ONLY a JSON array of strings, where each string is a distinct step.
CRITICAL: Our orchestrator executes exactly ONE tool per step and then shuts the server down. Do NOT create setup/teardown steps like 'Open database connection' or 'Close database'. Combine them into a single action step like 'Insert weather data into the local SQLite database'. Keep the total number of steps as small as possible.
CRITICAL: If the user's task involves performing actions across multiple entities (e.g. 5 companies, 3 cities, 3 startups), you MUST NOT group them into a single step. You must create separate, distinct steps for EACH action on EACH entity. (e.g. "Fetch price for AAPL", "Search news for AAPL", "Fetch price for MSFT", etc.)
CRITICAL: Do NOT create any steps for "compiling", "formatting", "summarizing", or "saving to file". The orchestrator handles final report compilation and file output natively after all data steps are complete. Only include steps that fetch or manipulate data.
Example: ["Retrieve current weather data for New York from an API", "Execute an SQL command to insert weather data into local SQLite database"]
"""
            plan_text = llm_generate(prompt)
            
            start_idx = plan_text.find('[')
            end_idx = plan_text.rfind(']')
            if start_idx != -1 and end_idx != -1:
                plan_text = plan_text[start_idx:end_idx+1]
                
            steps = json.loads(plan_text)
            state["plan"] = steps
            state["current_step"] = 0
            state["execution_results"] = []
            state["retry_count"] = 0
            state["in_recovery"] = False
            
            msg2 = "**LLM Generated Plan:**\n" + "\n".join([f"{i+1}. {step}" for i, step in enumerate(steps)])
            print(f"  -> {msg2}")
            write_log(state, msg2)
        except Exception as e:
            msg3 = f"**LLM Error during planning:** {e}"
            print(msg3)
            write_log(state, msg3)
            state["plan"] = ["Fetch Data", "Store Data"]
            state["current_step"] = 0
            state["execution_results"] = []
            state["retry_count"] = 0
            state["in_recovery"] = False
            
    state["status"] = "planning"
    return state

def discoverer_node(state: AgentState) -> AgentState:
    step_idx = state.get('current_step', 0)
    current_step_desc = state['plan'][step_idx]
    
    msg = f"## 🔍 Discovering Tools for Step {step_idx + 1}/{len(state['plan'])}: `{current_step_desc}`\nQuerying GitHub Marketplace..."
    print(f"[discoverer_node] Querying GitHub Marketplace for: {current_step_desc}")
    write_log(state, msg)
    try:
        # Check cache first using LLM evaluation
        tool_cache = state.get("tool_cache", {})
        if tool_cache:
            cache_prompt = (
                f"You need a tool for this task step: '{current_step_desc}'\n"
                f"Here are the tools we already have installed and working:\n"
            )
            for name, server in tool_cache.items():
                desc = server.get('description', 'No description')
                tool_entries = server.get('tools', [])
                cache_prompt += f"- {name}: {desc}\n"
                if tool_entries:
                    # Support both old format (list of strings) and new format (list of dicts)
                    for t in tool_entries[:20]:
                        if isinstance(t, dict):
                            t_name = t.get("name", "")
                            t_desc = t.get("description", "")
                            cache_prompt += f"    • {t_name}: {t_desc}\n"
                        else:
                            cache_prompt += f"    • {t}\n"
            cache_prompt += (
                "Should we reuse one of these tools? If yes, output ONLY the exact tool name (e.g. 'github-mcp-server').\n"
                "CRITICAL: You must be extremely strict! If the step requires a capability that the cached tool does NOT explicitly provide (e.g. if the step asks to search the web for activities, but the tool only provides weather forecasts), you MUST output 'NONE'. Do NOT reuse a tool just because it shares a related keyword.\n"
                "If none of them are a PERFECT fit for the primary action of this step, output ONLY the word 'NONE'."
            )
            llm_response = llm_generate(cache_prompt).strip()
            
            if llm_response != "NONE" and llm_response in tool_cache:
                cached_tool = tool_cache[llm_response]
                msg_cache = f"**Tool Cache Hit:** Reusing `{cached_tool['name']}`"
                print(f"  -> {msg_cache}")
                write_log(state, msg_cache)
                state["candidate_servers"] = [cached_tool]
                state["current_candidate_index"] = 0
                state["status"] = "discovering"
                return state

        # Check well-known servers using LLM evaluation
        if WELL_KNOWN_SERVERS:
            well_known_prompt = (
                f"You need a tool for this task step: '{current_step_desc}'\n"
                f"Here are some well-known, highly reliable tools we can use:\n"
            )
            for wks in WELL_KNOWN_SERVERS:
                well_known_prompt += f"- {wks['name']}: {wks['description']}\n"
            well_known_prompt += (
                "Should we use one of these well-known tools? If yes, output ONLY the exact tool name (e.g. 'yfinance-mcp').\n"
                "CRITICAL: You must be extremely strict! If the step requires a capability that the well-known tool does NOT explicitly provide, you MUST output 'NONE'.\n"
                "If none of them are a PERFECT fit for the primary action of this step, output ONLY the word 'NONE'."
            )
            llm_response = llm_generate(well_known_prompt).strip()
            
            if llm_response != "NONE":
                for wks in WELL_KNOWN_SERVERS:
                    if wks["name"] == llm_response:
                        msg_wks = f"**Well-Known Server Hit:** Preferring `{wks['name']}`"
                        print(f"  -> {msg_wks}")
                        write_log(state, msg_wks)
                        state["candidate_servers"] = [wks["server"]]
                        state["current_candidate_index"] = 0
                        state["status"] = "discovering"
                        return state

        # If cache and well-known miss or NONE, fallback to keyword extraction
        # Extract capability-focused keywords for GitHub search.
        # We ask the LLM explicitly for the TOOL TYPE, not the subject domain,
        # so "plan a trip using a search tool" yields "web-search" not "travel".
        kw_prompt = (
            f"You are helping find the right MCP server tool for a task step.\n"
            f"Extract EXACTLY ONE keyword that describes the TOOL CAPABILITY needed (not the subject domain).\n"
            f"Focus on the action verb and tool type. Examples:\n"
            f"  - 'search for attractions' -> 'web-search'\n"
            f"  - 'fetch the homepage content' -> 'web-scrape'\n"
            f"  - 'store in SQLite database' -> 'sqlite'\n"
            f"  - 'check the weather' -> 'weather'\n"
            f"  - 'write file to disk' -> 'filesystem'\n"
            f"  - 'get stock prices' -> 'finance'\n"
            f"Output ONLY the 1 single keyword. Nothing else.\n"
            f"Task Step: {current_step_desc}"
        )
        keywords_str = llm_generate(kw_prompt).strip()
        keywords = "+".join(keywords_str.split())
        state["current_keyword"] = keywords
        
        github_url = f"{GITHUB_SEARCH_URL}?q=topic:mcp-server+{keywords}&sort=stars"
        
        msg_search = f"**GitHub Search:** `topic:mcp-server {keywords}`"
        print(f"  -> {msg_search}")
        write_log(state, msg_search)
        
        headers = {"Accept": "application/vnd.github.v3+json"}
        github_token = os.environ.get("GITHUB_TOKEN")
        if github_token:
            headers["Authorization"] = f"token {github_token}"
            
        resp = requests.get(github_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"GitHub API Error: {resp.status_code} {resp.text}")
            
        data = resp.json()
        items = data.get("items", [])[:GITHUB_MAX_RESULTS]
        
        if not items:
            fallback_url = f"{GITHUB_SEARCH_URL}?q=topic:mcp-server&sort=stars"
            resp = requests.get(fallback_url, headers=headers, timeout=10)
            items = resp.json().get("items", [])[:GITHUB_MAX_RESULTS]
            
        search_results = ""
        
        for repo in items:
            full_name = repo["full_name"]
            
            if any(b.lower() in full_name.lower() or (repo.get("name") and b.lower() in repo.get("name").lower()) for b in BLACKLISTED_SERVERS):
                continue
                
            repo_desc = repo["description"]
            html_url = repo["html_url"]
            default_branch = repo.get("default_branch", "main")
            
            # Fetch README
            readme_url = f"https://raw.githubusercontent.com/{full_name}/refs/heads/{default_branch}/README.md"
            try:
                readme_resp = requests.get(readme_url, timeout=10)
                readme_snippet = readme_resp.text[:README_SNIPPET_LENGTH] if readme_resp.status_code == 200 else "No README available."
            except:
                readme_snippet = "No README available."
                
            search_results += f"Repo: {full_name}\nURL: {html_url}\nDescription: {repo_desc}\nREADME Snippet:\n{readme_snippet}\n\n---\n\n"
        
        msg_complete = f"Search complete. Asking LLM to analyze repositories..."
        print(f"  -> {msg_complete}")
        write_log(state, msg_complete)
        
        # Inject already-known candidates as context (e.g. during recovery)
        existing_candidates_context = ""
        if state.get("in_recovery") and state.get("candidate_servers"):
            existing_candidates_context = "\n\nIMPORTANT - Previously Discovered Servers (reuse if appropriate for this step):\n"
            for c in state["candidate_servers"]:
                existing_candidates_context += f"- {c.get('name')}: command='{c.get('command')}' args={c.get('args', [])}\n"
            existing_candidates_context += "If one of these already-known servers can handle this step, prefer it over discovering a new one.\n"

        prompt = f"""
You are an MCP Marketplace Discovery Agent. Based on the following GitHub repositories and their README snippets, select up to 5 BEST free Model Context Protocol servers for the user's task.
Determine the exact command to run each of them. 
- If the README mentions `npx`, `npm install`, or it is a Node.js package, use command "npx.cmd". For the args, use ["-y", "EXACT-NPM-PACKAGE-NAME"].
- If it's a Python package (or mentions `uvx` or `pip`), use command "{VENV_UVX_PATH}" and args ["EXACT-PYPI-PACKAGE-NAME"].
  IMPORTANT: Some Python packages have a different executable name than the package name. If the README shows a command like `uvx --from package-name executable-name`, you MUST use args ["--from", "package-name", "executable-name"] instead of just ["package-name"].
- CRITICAL: Finding the correct package name is essential. The GitHub repo name is often NOT the npm/pypi package name (e.g., weather-mcp is published as @dangahagan/weather-mcp). Read the README snippet carefully to find the exact 'npx ...' or 'pip install ...' or 'uvx ...' command provided by the author. Do NOT blindly use the github repo name as the package name.
- CRITICAL: If the task requires connecting to a local database (like SQLite) or a specific file path (like test.db), you MUST include the file path in the 'args' array exactly as the server's documentation specifies. (e.g., as a positional argument `test.db` or as a connection string `--dsn="sqlite:///test.db"`). Look closely at the README to see how they expect database paths.
- CRITICAL: If the server is an explicit filesystem server designed for generic file I/O (like @modelcontextprotocol/server-filesystem or @j0hanz/filesystem-mcp), you MUST include the absolute workspace path "{os.path.abspath('.')}" as a positional argument in the `args` array so it has permission to write. Do NOT add this argument to unrelated servers (e.g., search tools, GitHub tools, or code indexers).
- CRITICAL: Our orchestrator currently ONLY supports the `stdio` transport. If a server explicitly mentions it only runs over HTTP, SSE, or SSE/HTTP (e.g., using `uvicorn`, `streamable-http`, or `fastmcp run --transport sse`), do NOT select it. Only select servers that support standard stdio.
- CRITICAL: For the source_link, you MUST use the EXACT GitHub URL provided in the input.{existing_candidates_context}

Output ONLY a JSON array containing up to 5 objects:
[
  {{
    "name": "server name",
    "command": "npx.cmd or {VENV_UVX_PATH}",
    "args": ["arg1", "arg2"],
    "source_link": "https://EXACT_GITHUB_URL"
  }}
]

Task Step: {current_step_desc}
Repositories:
{search_results}
"""
        decision_text = llm_generate(prompt)
        
        # Robust JSON extraction
        try:
            start_idx = decision_text.find('[')
            end_idx = decision_text.rfind(']')
            if start_idx != -1 and end_idx != -1:
                json_str = decision_text[start_idx:end_idx+1]
                candidates = json.loads(json_str)
            else:
                raise ValueError(f"Could not find JSON array in response: {decision_text}")
        except Exception as parse_err:
            print(f"  -> JSON Parse Error. Raw response was: {decision_text}")
            raise parse_err
        
        if not candidates:
            raise Exception("No relevant free MCP servers found in GitHub Marketplace.")
            
        state["candidate_servers"] = candidates
        state["current_candidate_index"] = 0
        
        msg_disc = f"**Discovered {len(candidates)} candidates:**\n"
        for i, c in enumerate(candidates):
            msg_disc += f"{i+1}. `{c.get('name')}`\n   - **Command:** `{c.get('command')} {' '.join(c.get('args', []))}`\n   - **Source:** [Link]({c.get('source_link')})\n"
        print(f"  -> Discovered {len(candidates)} candidates.")
        write_log(state, msg_disc)
        
    except Exception as e:
        msg_err = f"**Discovery failed:** {e}"
        print(f"  -> {msg_err}")
        write_log(state, msg_err)
        state["candidate_servers"] = []
        state["current_candidate_index"] = 0
        state["errors"] = [str(e)]
        state["status"] = "failed"
        return state
        
    state["status"] = "discovering"
    return state

def installer_node(state: AgentState) -> AgentState:
    print("[installer_node] Provisioning tools... (Skipped since we use uvx/npx)")
    state["status"] = "installing"
    return state

def executor_node(state: AgentState) -> AgentState:
    step_idx = state.get('current_step', 0)
    current_step_desc = state['plan'][step_idx]
    
    msg = f"## ⚙️ Execution Phase\n**Executing Step {step_idx + 1}/{len(state['plan'])}:** `{current_step_desc}`"
    print(f"[executor_node] Executing plan step {step_idx}: {current_step_desc}")
    write_log(state, msg)
    
    candidates = state.get("candidate_servers", [])
    cand_idx = state.get("current_candidate_index", 0)
    if cand_idx >= len(candidates):
        msg_fatal = "> ❌ **Fatal:** All MCP server alternatives failed for this step."
        print("  -> Fatal: All MCP server alternatives failed for this step.")
        write_log(state, msg_fatal)
        if "errors" not in state: state["errors"] = []
        state["errors"].append("All alternatives failed.")
        state["status"] = "failed"
        
        # Write a final failure result to artifacts
        out_dir = state.get("artifact_dir", "artifacts")
        with open(os.path.join(out_dir, "final_result.txt"), "w", encoding="utf-8") as f:
            f.write("Workflow failed to complete the task.\nErrors encountered:\n" + "\n".join(state["errors"]))
        
        return state
        
    server = candidates[cand_idx]
    
    if "errors" not in state:
        state["errors"] = []
        
    # ── Native compile-and-save handler ─────────────────────────────────────
    # If this step is about compiling/saving a report, handle it natively
    # (LLM formats the markdown, Python writes the file) — no MCP tool needed.
    import re as _re
    _save_keywords = ("save", "compile", "write to file", "write the report", "generate report", "output to")
    _is_save_step = any(kw in current_step_desc.lower() for kw in _save_keywords)
    if _is_save_step:
        msg_native = "📝 **Native Handler:** Compiling report and saving file with LLM + Python (no MCP needed)."
        print(f"  -> {msg_native}")
        write_log(state, msg_native)
        try:
            result_full = "\n".join([r.get('full_result', r.get('result_snippet', '')) for r in state.get('execution_results', [])])
            compile_prompt = (
                f"You are compiling a final report for the following task:\n{state['task']}\n\n"
                f"Here is all the data collected by previous steps:\n{result_full}\n\n"
                f"Write a complete, well-formatted markdown report that fully answers the task. "
                f"Include ALL specific data values (prices, percentages, news headlines) collected. "
                f"IMPORTANT: Write entirely in English."
            )
            summary = llm_generate(compile_prompt)
            state["summary"] = summary
            
            # Extract output filename from step description (e.g. 'save it to market_briefing_5.md')
            out_dir = state.get("artifact_dir", "artifacts")
            os.makedirs(out_dir, exist_ok=True)
            filename_match = _re.search(r'[\w_-]+\.\w+', current_step_desc)
            if filename_match:
                output_filename = filename_match.group(0)
                output_path = os.path.join(os.getcwd(), output_filename)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(summary)
                print(f"  -> Saved report to {output_path}")
            with open(os.path.join(out_dir, "final_result.md"), "w", encoding="utf-8") as f:
                f.write(summary)
            msg_done = f"🎉 **Task Complete!** Report saved natively."
            print("  -> Task Complete! Saved final result to artifacts/final_result.md")
            write_log(state, msg_done)
            state["current_step"] = step_idx + 1
            state["errors"] = []
            state["status"] = "step_complete"
            state["candidate_servers"] = []
            state["current_candidate_index"] = 0
            return state
        except Exception as e:
            # Fall through to MCP execution if native handling fails
            print(f"  -> Native handler failed ({e}), falling back to MCP...")
    # ── End native handler ───────────────────────────────────────────────────

    # Actual execution
    try:
        cmd = server.get("command")
        args = server.get("args", [])
        
        msg_fetch = f"**Attempting with:** `{server.get('name')}`"
        print(msg_fetch)
        write_log(state, msg_fetch)
        
        # Collect context from previous successful steps
        past_results = ""
        for r in state.get("execution_results", []):
            past_results += f"Step {r.get('step', '?')} Action: {r.get('action', '')}\nResult:\n{r.get('full_result', r.get('result_snippet', ''))}\n\n"
            
        # Generic execution: We pass the overall task AND the specific step description to the MCP execute function
        task_prompt = f"Overall Task: {state['task']}\nCurrent Step Focus: {current_step_desc}"
        if past_results.strip():
            task_prompt += f"\n\nPast Execution Context (use this to inform your arguments, schemas, and actions):\n{past_results}"
            
        result, tools_schema = sync_dynamic_mcp_execute(cmd, args, task_prompt)
        
        if "execution_results" not in state: state["execution_results"] = []
        result_snippet = result[:100].replace('\n', ' ')
        raw_result = result[:RESULT_MAX_CHARS]

        # For large results, use the LLM to extract only what's relevant to this step.
        # This is domain-agnostic: the LLM knows what matters for a stock step,
        # a weather step, a news step, etc. — no hardcoded field lists needed.
        if len(result) > 500:
            extraction_prompt = (
                f"The following is raw output from a tool that was executed for this step:\n"
                f"Step: {current_step_desc}\n\n"
                f"Raw output:\n{raw_result}\n\n"
                f"Extract ONLY the key data points directly relevant to the step above. "
                f"Be concise. Use a short structured format (e.g. bullet points or key: value). "
                f"Do not include metadata, addresses, or irrelevant fields. "
                f"IMPORTANT: Write entirely in English."
            )
            try:
                result_context = llm_generate(extraction_prompt)
            except Exception:
                result_context = raw_result  # fallback to raw if LLM call fails
        else:
            result_context = raw_result

        state["execution_results"].append({
            "step": step_idx,
            "action": f"Executed via {server.get('name')}",
            "result_snippet": result_snippet + "...",
            "full_result": result_context
        })
        
        msg_succ = f"✅ **Success!** Step {step_idx + 1} completed via `{server.get('name')}`.\n   - **Result Snippet:** `{result_snippet}...`\n"
        print(f"  -> Success! Step {step_idx + 1} completed via {server.get('name')}.")
        write_log(state, msg_succ)
        
        # Check if we are done with all steps
        is_last_step = step_idx == len(state['plan']) - 1
        if is_last_step:
            msg_sum = "## 🏁 Finalizing\nSummarizing all results..."
            print("  -> Finalizing and summarizing results...")
            write_log(state, msg_sum)
            
            # Combine all results
            result_full = "\n".join([r.get('full_result', r.get('result_snippet', '')) for r in state.get('execution_results', [])])
            prompt = f"Summarize the following tool results to answer the user's task '{state['task']}'. IMPORTANT: The final summary MUST be written entirely in English. If the tool results contain non-English text, translate it to English in your summary.\n{result_full}"
            summary = llm_generate(prompt)
            state["summary"] = summary
            
            # Save to artifacts dir
            out_dir = state.get("artifact_dir", "artifacts")
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "final_result.md"), "w", encoding="utf-8") as f:
                f.write(summary)

            # Also write to the output folder with a meaningful filename.
            # Ask the LLM to extract the filename from the task, or invent one.
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            filename_prompt = (
                f"Given this task description, determine the output filename.\n"
                f"Task: {state.get('task', '')}\n\n"
                f"Rules:\n"
                f"- If the task explicitly mentions a filename (e.g. 'save to report.md'), return that exact filename.\n"
                f"- Otherwise, invent a short, descriptive snake_case filename with an appropriate extension (e.g. market_briefing.md, weather_report.txt).\n"
                f"- Output ONLY the filename, nothing else. No path, no explanation."
            )
            output_filename = llm_generate(filename_prompt).strip().strip('`').strip()
            # Sanitize: keep only safe filename characters
            output_filename = "".join(c for c in output_filename if c.isalnum() or c in '._-')
            if not output_filename:
                output_filename = "task_output.md"
            output_path = os.path.join(OUTPUT_DIR, output_filename)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(summary)
            print(f"  -> Also saved to {output_path}")

            msg_save = f"🎉 **Task Complete!** Saved final result to `artifacts/final_result.md`"
            print("  -> Task Complete! Saved final result to artifacts/final_result.md")
            write_log(state, msg_save)
            
        # Tool Cache Registration
        if "candidate_servers" in state and len(state["candidate_servers"]) > state.get("current_candidate_index", 0):
            tool_cache = state.get("tool_cache", {})
            current_server = state["candidate_servers"][state["current_candidate_index"]]
            server_name = current_server.get("name", "unknown")
            
            cache_entry = tool_cache.get(server_name, dict(current_server))
            
            # Dynamically deduce the tool's capability based on what it just successfully did
            try:
                desc_prompt = (
                    f"The tool '{server_name}' was just successfully used to complete the following step: '{current_step_desc}'.\n"
                    f"Based on this success, write a concise 1-sentence description of the capability this tool provides. "
                    f"Generalize the capability (e.g. if it fetched weather for 'Paris', say it can 'fetch weather for any location')."
                )
                new_capability = llm_generate(desc_prompt).strip()
                
                if "learned_capabilities" not in cache_entry:
                    cache_entry["learned_capabilities"] = [new_capability]
                    cache_entry["description"] = f"Proven capabilities: {new_capability}"
                else:
                    # Avoid appending exact duplicate descriptions
                    if new_capability not in cache_entry["learned_capabilities"]:
                        cache_entry["learned_capabilities"].append(new_capability)
                        cache_entry["description"] += f" | {new_capability}"
            except Exception:
                pass

            # Store full tool schemas (name + description) so discoverer can do richer capability matching
            cache_entry["tools"] = [
                {"name": t["name"], "description": t.get("description", "")}
                for t in tools_schema
            ]
            tool_cache[server_name] = cache_entry
            state["tool_cache"] = tool_cache
            
        state["current_step"] = step_idx + 1
        state["errors"] = [] 
        state["status"] = "step_complete"
        # Always clear candidates so the discoverer runs fresh for the next step.
        # During recovery, the discoverer will get the previous candidates as context.
        state["candidate_servers"] = []
        state["current_candidate_index"] = 0
        
    except Exception as e:
        msg_err = f"❌ **Execution failed** on `{server.get('name')}`:\n```\n{e}\n```"
        print(f"  -> Execution failed on {server.get('name')} with error: {e}")
        write_log(state, msg_err)
        state["errors"].append(str(e))
        state["status"] = "executing"
        
    return state

def supervisor_node(state: AgentState) -> AgentState:
    msg = "## 🔧 Auto-Healing Supervisor\nAnalyzing errors..."
    print("[supervisor_node] Analyzing errors...")
    write_log(state, msg)
    
    last_error = state["errors"][-1] if state.get("errors") else "Unknown"
    
    prompt = f"""Analyze the following error encountered during an automated task.
Error: {last_error}
Does this error represent a logical or environmental issue that can be fixed by performing a prerequisite step (e.g., creating a missing database table, directory, or file)? 
Respond ONLY with 'RECOVERABLE' if it can be fixed with a prerequisite step, or 'TOOL_FAILURE' if it's an unrecoverable crash, missing executable, or timeout.
"""
    try:
        classification = llm_generate(prompt).strip()
    except:
        classification = "TOOL_FAILURE"
        
    print(f"  -> Error classified as: {classification}")
    write_log(state, f"Error classified as: `{classification}`")
    
    candidates = state.get("candidate_servers", [])
    cand_idx = state.get("current_candidate_index", 0)
    
    if "RECOVERABLE" in classification:
        retry_count = state.get("retry_count", 0)
        if retry_count >= MAX_RECOVERY_RETRIES:
            msg_fatal = "> ❌ **Decision:** Recoverable error persists after retry. Terminating workflow."
            print("  [supervisor_node] Decision: Recoverable error persists after retry.")
            write_log(state, msg_fatal)
            state["status"] = "failed"
        else:
            state["retry_count"] = retry_count + 1
            msg_rec = "> 🔄 **Decision:** Routing to planner to devise a recovery plan."
            print("  [supervisor_node] Decision: Routing to planner for recovery.")
            write_log(state, msg_rec)
            state["status"] = "needs_recovery"
        return state
    
    # Uninstall the failed server
    if cand_idx < len(candidates):
        failed_server = candidates[cand_idx]
        manager = DockerPackageManager()
        manager.uninstall_mcp_server(failed_server)
        msg_un = f"- Uninstalled broken server: `{failed_server.get('name')}`"
        write_log(state, msg_un)
        
    # Increment to try next alternate
    state["current_candidate_index"] = cand_idx + 1
    state["status"] = "recovering"
    return state

def router(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "end"
    elif state.get("errors"):
        return "supervisor"
    elif state["status"] == "planning":
        # Always run the discoverer. During recovery it will receive existing
        # candidates as context so it can reuse them if appropriate.
        return "discoverer"
    elif state["status"] == "discovering":
        return "installer"
    elif state["status"] == "installing":
        return "executor"
    elif state["status"] == "step_complete":
        if state.get("current_step", 0) < len(state.get("plan", [])):
            return "discoverer"
        else:
            return "end"
    elif state["status"] == "executing" and state.get("current_step", 0) < len(state.get("plan", [])):
        return "executor"
    else:
        return "end"

def supervisor_router(state: AgentState) -> str:
    if state.get("status") == "failed":
        out_dir = state.get("artifact_dir", "artifacts")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "final_result.txt"), "w", encoding="utf-8") as f:
            f.write("Workflow failed to complete the task.\nErrors encountered:\n" + "\n".join(state.get("errors", [])))
        return "end"
        
    if state.get("status") == "needs_recovery":
        return "planner"
        
    candidates = state.get("candidate_servers", [])
    cand_idx = state.get("current_candidate_index", 0)
    
    if cand_idx >= len(candidates) or cand_idx >= MAX_CANDIDATE_RETRIES:
        msg = "> ❌ **Decision:** All alternatives failed for this step. Terminating workflow."
        print("  [supervisor_router] Decision: All alternatives failed for this step. Terminating workflow.")
        write_log(state, msg)
        
        out_dir = state.get("artifact_dir", "artifacts")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "final_result.txt"), "w", encoding="utf-8") as f:
            f.write("Workflow failed to complete the task.\nErrors encountered:\n" + "\n".join(state.get("errors", [])))
            
        return "end"
    else:
        msg = f"> 🔄 **Decision:** Trying alternative server {cand_idx + 1}/{len(candidates)}. Routing back to executor."
        print(f"  [supervisor_router] Decision: Trying alternative server {cand_idx + 1}/{len(candidates)}.")
        write_log(state, msg)
        # Route back to executor to try the next alternative for the current step
        return "executor"

workflow = StateGraph(AgentState)
workflow.add_node("planner", planner_node)
workflow.add_node("discoverer", discoverer_node)
workflow.add_node("installer", installer_node)
workflow.add_node("executor", executor_node)
workflow.add_node("supervisor", supervisor_node)
workflow.set_entry_point("planner")

workflow.add_conditional_edges("planner", router, {"discoverer": "discoverer", "end": END})
workflow.add_conditional_edges("discoverer", router, {"installer": "installer", "end": END})
workflow.add_conditional_edges("installer", router, {"executor": "executor", "end": END})
workflow.add_conditional_edges("executor", router, {"supervisor": "supervisor", "executor": "executor", "discoverer": "discoverer", "end": END})
workflow.add_conditional_edges("supervisor", supervisor_router, {"executor": "executor", "planner": "planner", "end": END})

app = workflow.compile()
