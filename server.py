import os
import json
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from graph import app as graph_app
from test_scenario import SCENARIOS, cleanup_db

app = FastAPI(title="Auto-Heal Orchestrator API")

# Allow React dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/scenarios")
def list_scenarios():
    # Return scenarios as a list of dicts
    return [{"id": k, "name": v["name"], "task": v["task"]} for k, v in SCENARIOS.items()]

@app.get("/execute/{scenario_id}")
async def execute_scenario(scenario_id: int):
    scenario = SCENARIOS.get(scenario_id)
    if not scenario:
        return {"error": "Scenario not found"}

    async def event_generator():
        yield json.dumps({"type": "log", "message": f"Starting Scenario {scenario_id}: {scenario['name']}\n" + "-"*60})
        
        try:
            cleanup_db()
        except Exception as e:
            yield json.dumps({"type": "log", "message": f"Cleanup Error: {e}"})

        import queue
        import threading
        
        q = queue.Queue()

        initial_state = {
            "task": scenario["task"], 
            "scenario_number": scenario_id,
            "log_queue": q
        }
        
        def run_graph():
            try:
                for event in graph_app.stream(initial_state):
                    # If this is the last step and summary is available, push it
                    for key, value in event.items():
                        if value.get("status") == "step_complete" and value.get("summary"):
                            q.put({"type": "answer", "message": value["summary"]})
            except Exception as e:
                 q.put({"type": "log", "message": f"\nWorkflow Error: {str(e)}"})
            finally:
                q.put(None) # EOF marker
                
        # Run graph in a separate thread!
        # This fixes the `asyncio.run` crash because this new thread has no running event loop,
        threading.Thread(target=run_graph, daemon=True).start()

        while True:
            try:
                msg = q.get_nowait()
                if msg is None:
                    yield json.dumps({"type": "log", "message": "\nWorkflow Execution Complete."})
                    break
                yield json.dumps(msg)
            except queue.Empty:
                await asyncio.sleep(0.05)

    return EventSourceResponse(event_generator())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
