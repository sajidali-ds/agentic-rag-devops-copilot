import os
import streamlit as st
import requests
import time
import uuid
import logfire
from dotenv import load_dotenv


# Load environment variables explicitly from the root directory
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(dotenv_path=env_path)


# Initialize Logfire
try:
    token = os.getenv("LOGFIRE_TOKEN")
    if not token:
        print("WARNING: LOGFIRE_TOKEN not set — tracing disabled for the UI.")
    logfire.configure(token=token, send_to_logfire="if-token-present")
    # logfire.instrument_requests() # Disabled due to OpenTelemetry bug on Windows: MeterProvider.get_meter() got multiple values for argument 'version'
    LOGFIRE_STATUS = "Connected & Tracing"
except Exception as e:
    print(f"Logfire Init Error in UI: {e}")
    LOGFIRE_STATUS = f"Standby (Error: {e})"



# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Enterprise Agentic RAG",
    page_icon="🤖",
    layout="wide",
)

# --- AVATARS ---
AI_AVATAR = "🤖"
USER_AVATAR = "👤"


# --- SESSION MANAGEMENT ---
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
    logfire.info(f"✨ New User Session Created: {st.session_state.session_id}")

if "messages" not in st.session_state:
    st.session_state.messages = []


RISK_BADGE = {"read_only": "🟢 read-only", "unknown": "🟡 unknown", "mutating": "🟠 changes state", "destructive": "🔴 destructive"}


def _api_headers():
    api_key = os.getenv("API_KEY")
    return {"X-API-Key": api_key} if api_key else {}


def _send_feedback(meta: dict, rating: str):
    try:
        requests.post(
            f"{os.getenv('BACKEND_URL', 'http://127.0.0.1:8000')}/feedback",
            json={"request_id": meta.get("request_id", ""), "trace_id": meta.get("trace_id"),
                  "thread_id": st.session_state.session_id, "rating": rating},
            headers=_api_headers(), timeout=10,
        )
        st.toast("Thanks for the feedback!" if rating == "up" else "Feedback noted — thanks!")
    except Exception as e:  # noqa: BLE001
        st.toast(f"Could not send feedback: {e}")


def _render_meta(meta: dict, key: str = ""):
    """Badges + expandable agent trace / commands / sources for one answer."""
    cache = meta.get("cache")
    badges = []
    if cache in ("exact", "semantic"):
        badges.append(f"⚡ cache ({cache})")
    if meta.get("latency_ms") is not None:
        badges.append(f"⏱ {meta['latency_ms'] / 1000:.1f}s")
    if meta.get("steps"):
        badges.append(f"🔁 {meta['steps']} agent step(s)")
    for t in dict.fromkeys(meta.get("tools_used") or []):
        badges.append(f"🧰 {t}")
    if meta.get("grounded") is True:
        badges.append("✅ grounded")
    elif meta.get("grounded") is False:
        badges.append("⚠️ partly unverified")
    if badges:
        st.caption(" · ".join(badges))

    if meta.get("citations"):
        st.caption("Sources: " + " · ".join(meta["citations"]))
    if meta.get("command_risks"):
        with st.expander("🛡️ Command safety review"):
            for r in meta["command_risks"]:
                st.markdown(f"{RISK_BADGE.get(r['risk'], r['risk'])} &nbsp; `{r['command']}`")
    if meta.get("thought_process"):
        with st.expander("🧠 Agent trace"):
            for step in meta["thought_process"]:
                st.write(f"⚙️ {step}")
    if meta.get("sources"):
        with st.expander("📄 Retrieved context"):
            for i, source in enumerate(meta["sources"]):
                with st.expander(f"Chunk {i+1}: " + source[:100].replace("\n", " ") + "..."):
                    st.info(source)
    if meta.get("request_id"):
        c1, c2, _ = st.columns([1, 1, 8])
        if c1.button("👍", key=f"up_{key}{meta['request_id']}"):
            _send_feedback(meta, "up")
        if c2.button("👎", key=f"down_{key}{meta['request_id']}"):
            _send_feedback(meta, "down")

# --- SIDEBAR ---
with st.sidebar:
    st.title("🧠 Agent OS")
    st.markdown("---")
    st.success(f"Logfire: {LOGFIRE_STATUS}")
    st.info(f"Memory ID: {st.session_state.session_id[:8]}")

    if st.button("🗑️ Clear History & Memory", width="stretch", type="primary"):
        logfire.warn(f"🗑️ Memory Wipe Triggered for session: {st.session_state.session_id}")
        try:  # also drop the server-side conversation memory
            requests.delete(f"{os.getenv('BACKEND_URL', 'http://127.0.0.1:8000')}/conversations/{st.session_state.session_id}",
                            headers=_api_headers(), timeout=5)
        except Exception:  # noqa: BLE001
            pass
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()



# --- MAIN CHAT ---
st.title("🛠️ IT/DevOps Troubleshooting Copilot")
st.caption("Paste an error, pod status, logs or a question. The agent searches the docs, runs tools, and suggests fixes (it never executes commands).")


# Display history
for message in st.session_state.messages:
    avatar = AI_AVATAR if message["role"] == "assistant" else USER_AVATAR
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])
        if message.get("meta"):
            _render_meta(message["meta"])

# Chat Input
if prompt := st.chat_input("Describe the problem or paste logs…"):
    # START TRACE: User Interaction
    with logfire.span("💬 User Chat Interaction", user_query=prompt, session_id=st.session_state.session_id):

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(prompt)

        # Assistant Response
        with st.chat_message("assistant", avatar=AI_AVATAR):
            with st.status("🔍 Agent is thinking...", expanded=True) as status:
                try:
                    # DISTRIBUTED TRACE: Calling Backend
                    with logfire.span("📡 Calling RAG Backend"):
                        # Get backend URL from env, or default to local if not set
                        base_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
                        url = f"{base_url}/query"
                        payload = {"q": prompt, "thread_id": st.session_state.session_id}
                        api_key = os.getenv("API_KEY")
                        headers = {"X-API-Key": api_key} if api_key else {}
                        response = requests.post(url, json=payload, headers=headers, timeout=180)
                        data = response.json()
                        if not response.ok:
                            raise RuntimeError(f"Backend returned HTTP {response.status_code}: {data.get('answer') or data.get('detail')}")

                    for step in data.get("thought_process", []):
                        st.write(f"⚙️ {step}")
                    status.update(label="✅ Done" if data.get("cache") not in ("exact", "semantic") else "⚡ Served from cache", state="complete", expanded=False)
                except Exception as e:
                    logfire.error(f"❌ UI-Backend Connection Failed: {e}")
                    status.update(label="❌ Connection Failed", state="error")
                    st.error(f"Backend error: {e}")
                    st.stop()

            # Final Answer Streaming
            answer_placeholder = st.empty()
            full_answer = data.get("answer", "No response.")

            curr_text = ""
            for i in range(0, len(full_answer), 12):  # typewriter effect, chunked so long answers stay fast
                curr_text = full_answer[: i + 12]
                answer_placeholder.markdown(curr_text + "▌")
                time.sleep(0.01)

            answer_placeholder.markdown(full_answer)
            _render_meta(data, key="new")
            st.session_state.messages.append({"role": "assistant", "content": full_answer, "meta": {**data, "request_id": None}})
            logfire.info("✅ Chat cycle completed successfully.")
