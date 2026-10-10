"""The agent loop: the LLM decides which tool to call, reads the result, and repeats until done."""
import json

from tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

SYSTEM_PROMPT = """You are an India Route Planner Agent. You help people plan trips between places in India.

Rules:
- Never guess distances, times or weather. Use your tools.
- Typical flow: get_driving_route -> get_weather at the destination -> compare_travel_modes.
  For road trips over ~300 km, also call suggest_halts.
- If a place name is ambiguous or a tool fails, retry with the state name added (e.g. 'Salem, Tamil Nadu').
- Costs and times from compare_travel_modes are rough estimates. Say so, and tell the user to confirm
  train/bus availability and fares on IRCTC or redBus.
- Only call save_trip_plan if the user asks you to save the plan.
- Finish with a short, clear recommendation: best mode for the user's priority (cheapest, fastest, comfort).
- If the request is not about travel in India, politely say what you can help with."""


def run_agent(user_message, history, client, model, max_steps=8):
    """Returns (final_answer, trace). `trace` lists every tool call for display in the UI."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_message}]
    trace = []

    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.2,
        )
        msg = resp.choices[0].message

        # No tool requested -> the model has produced its final answer
        if not msg.tool_calls:
            return msg.content or "", trace

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        # Execute every requested tool and feed the observation back to the model
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = TOOL_FUNCTIONS.get(name)
            result = fn(**args) if fn else {"error": f"Unknown tool {name}"}
            trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})

    return "I hit the step limit before finishing. Try a simpler or more specific request.", trace
