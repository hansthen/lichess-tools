import datetime as dt

import requests
import streamlit as st

GAMES_URL = "https://lichess.org/api/games/user/{username}"
PERF_TYPES = [
    "ultraBullet", "bullet", "blitz", "rapid", "classical",
    "correspondence", "chess960", "crazyhouse", "antichess",
    "atomic", "horde", "kingOfTheHill", "racingKings", "threeCheck",
]

# Widget defaults live in session_state (no `value=` args) so the filters
# are remembered when you switch to the Analysis page and back.
DEFAULTS = {
    "max_games": 100,
    "perf_types": [],
    "rated": "Any",
    "color": "Any",
    "use_dates": False,
    "date_from": dt.date.today() - dt.timedelta(days=30),
    "date_to": dt.date.today(),
    "clocks": False,
    "evals": False,
    "username_input": "",
}
for key, default in DEFAULTS.items():
    st.session_state.setdefault(key, default)
    st.session_state[key] = st.session_state[key]  # keep value across pages

# Prefill the username with the logged-in account
if not st.session_state["username_input"] and st.session_state.get("me"):
    st.session_state["username_input"] = st.session_state["me"]

st.title("⬇️ Download games")
st.write("Download a player's games from Lichess as a PGN file.")

username = st.text_input("Lichess username", key="username_input")

with st.expander("Filters", expanded=True):
    c1, c2, c3 = st.columns(3)
    c1.number_input("Max games", min_value=1, max_value=10000, step=50, key="max_games")
    c2.selectbox("Rated", ["Any", "Rated only", "Casual only"], key="rated")
    c3.selectbox("Played as", ["Any", "white", "black"], key="color")
    st.multiselect("Game types (empty = all)", PERF_TYPES, key="perf_types")

    st.checkbox("Filter by date", key="use_dates")
    if st.session_state["use_dates"]:
        d1, d2 = st.columns(2)
        d1.date_input("From", key="date_from")
        d2.date_input("To", key="date_to")

    o1, o2 = st.columns(2)
    o1.checkbox("Include clock times", key="clocks")
    o2.checkbox("Include analysis evals (if available)", key="evals")


def build_params():
    ss = st.session_state
    params = {
        "max": int(ss["max_games"]),
        "clocks": str(ss["clocks"]).lower(),
        "evals": str(ss["evals"]).lower(),
        "opening": "true",  # needed for the Analysis page
    }
    if ss["perf_types"]:
        params["perfType"] = ",".join(ss["perf_types"])
    if ss["rated"] == "Rated only":
        params["rated"] = "true"
    elif ss["rated"] == "Casual only":
        params["rated"] = "false"
    if ss["color"] != "Any":
        params["color"] = ss["color"]
    if ss["use_dates"]:
        since = dt.datetime.combine(ss["date_from"], dt.time.min, tzinfo=dt.timezone.utc)
        until = dt.datetime.combine(ss["date_to"], dt.time.max, tzinfo=dt.timezone.utc)
        params["since"] = int(since.timestamp() * 1000)
        params["until"] = int(until.timestamp() * 1000)
    return params


def fetch_games(name):
    from common import logout  # local import keeps the page self-explanatory

    headers = {"Accept": "application/x-chess-pgn"}
    token = st.session_state.get("token")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    chunks = []
    progress = st.empty()
    with requests.get(
        GAMES_URL.format(username=name),
        params=build_params(),
        headers=headers,
        stream=True,
        timeout=60,
    ) as resp:
        if resp.status_code == 401:
            logout()
            raise RuntimeError("Your login expired. Please log in again.")
        if resp.status_code == 404:
            raise ValueError(f"User '{name}' was not found.")
        if resp.status_code == 429:
            raise RuntimeError("Rate limited by Lichess. Wait a minute and try again.")
        resp.raise_for_status()

        count = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                chunks.append(chunk)
                count += chunk.count(b"[Event ")
                progress.info(f"Downloading... {count} games so far")
    progress.empty()
    return b"".join(chunks).decode("utf-8")


if st.button("Download games", type="primary", disabled=not username.strip()):
    name = username.strip()
    try:
        with st.spinner("Contacting Lichess..."):
            pgn = fetch_games(name)
        st.session_state["pgn"] = pgn
        st.session_state["pgn_user"] = name
    except (ValueError, RuntimeError) as e:
        st.error(str(e))
    except requests.RequestException as e:
        st.error(f"Network error: {e}")

if "pgn" in st.session_state:
    pgn = st.session_state["pgn"]
    n = pgn.count("[Event ")
    if n == 0:
        st.warning("No games found with these filters.")
    else:
        st.success(f"Downloaded {n} games for {st.session_state['pgn_user']}.")
        st.download_button(
            "Save PGN file",
            data=pgn,
            file_name=f"lichess_{st.session_state['pgn_user']}.pgn",
            mime="application/x-chess-pgn",
        )
        st.page_link("analysis_page.py", label="Analyse these games", icon="📊")
        with st.expander("Preview (first 3000 characters)"):
            st.code(pgn[:3000], language=None)
