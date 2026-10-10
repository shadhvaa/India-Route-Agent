import json
import os

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from agent import run_agent

load_dotenv()
API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

st.set_page_config(page_title="India Route Planner Agent", page_icon="🧭")
st.title("🧭 India Route Planner Agent")
st.caption("An agent that picks its own tools: routing, halts, weather and travel-mode comparison.")

if not API_KEY:
    st.error("LLM_API_KEY is missing. Copy .env.example to .env and add your key.")
    st.stop()

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

if "chat" not in st.session_state:
    st.session_state.chat = []

EXAMPLES = [
    "Plan a trip from Chennai to Coimbatore for 2 people. What's the best way to go?",
    "I want to drive from Bengaluru to Goa. Suggest halts and tell me the weather there.",
    "Cheapest way from Delhi to Jaipur for 4 people?",
]
with st.sidebar:
    st.subheader("Try an example")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state.pending = ex
    st.divider()
    st.caption(f"Model: `{MODEL}`")
    if st.button("Clear chat"):
        st.session_state.chat = []
        st.rerun()


def show_trace(trace):
    with st.expander(f"🔧 Agent used {len(trace)} tool call(s)"):
        for i, step in enumerate(trace, 1):
            st.markdown(f"**Step {i}: `{step['tool']}`**")
            st.code(json.dumps(step["args"], indent=2, ensure_ascii=False), language="json")
            st.code(json.dumps(step["result"], indent=2, ensure_ascii=False)[:1500], language="json")


for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("trace"):
            show_trace(m["trace"])

prompt = st.chat_input("Where do you want to go in India?") or st.session_state.pop("pending", None)

if prompt:
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat]
    st.session_state.chat.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Agent is thinking and calling tools..."):
            try:
                answer, trace = run_agent(prompt, history, client, MODEL)
            except Exception as e:  # noqa: BLE001
                answer, trace = f"Something went wrong: {e}", []
        st.markdown(answer)
        if trace:
            show_trace(trace)
    st.session_state.chat.append({"role": "assistant", "content": answer, "trace": trace})
