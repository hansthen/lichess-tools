import base64
import datetime as dt
import hashlib
import os
import re
import secrets
import time
from urllib.parse import urlencode

import altair as alt
import pandas as pd
import requests
import streamlit as st

# --- Lichess OAuth2 (Authorization Code + PKCE) ------------------------------
# Lichess needs no client registration: the client_id is any unique string
# that identifies your app, and the redirect URI can be any URL you control.
CLIENT_ID = os.environ.get("LICHESS_CLIENT_ID", "streamlit-lichess-downloader")
REDIRECT_URI = os.environ.get("LICHESS_REDIRECT_URI", "http://localhost:8501")
SCOPES = ""  # Downloading games needs no special scope. Add e.g. "preference:read" if needed.

AUTH_URL = "https://lichess.org/oauth"
TOKEN_URL = "https://lichess.org/api/token"
ACCOUNT_URL = "https://lichess.org/api/account"
GAMES_URL = "https://lichess.org/api/games/user/{username}"

PERF_TYPES = [
    "ultraBullet", "bullet", "blitz", "rapid", "classical",
    "correspondence", "chess960", "crazyhouse", "antichess",
    "atomic", "horde", "kingOfTheHill", "racingKings", "threeCheck",
]


@st.cache_resource
def pending_logins():
    """Server-side store mapping OAuth `state` -> (code_verifier, created_at).

    The browser leaves the app when going to Lichess, so session_state is lost
    on return. This store (shared across sessions) survives the round trip.
    """
    return {}


def make_pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_login_url():
    store = pending_logins()
    # Drop stale entries (older than 10 minutes)
    now = time.time()
    for key in [k for k, (_, t) in store.items() if now - t > 600]:
        store.pop(key, None)

    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(24)
    store[state] = (verifier, now)

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    if SCOPES:
        params["scope"] = SCOPES
    return f"{AUTH_URL}?{urlencode(params)}"


def handle_oauth_callback():
    """If Lichess redirected back with ?code=...&state=..., finish the login."""
    qp = st.query_params
    if "error" in qp:
        st.error(f"Login failed: {qp.get('error_description', qp['error'])}")
        st.query_params.clear()
        return
    if "code" not in qp or "state" not in qp:
        return

    code, state = qp["code"], qp["state"]
    st.query_params.clear()

    entry = pending_logins().pop(state, None)
    if entry is None:
        st.error("Login session expired or invalid. Please try logging in again.")
        return
    verifier, _ = entry

    try:
        resp = requests.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
            },
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]

        acct = requests.get(
            ACCOUNT_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        acct.raise_for_status()
        st.session_state["token"] = token
        st.session_state["me"] = acct.json().get("username", "")
    except (requests.RequestException, KeyError) as e:
        st.error(f"Could not complete login: {e}")


def logout():
    token = st.session_state.pop("token", None)
    st.session_state.pop("me", None)
    if token:
        try:  # Revoke the token on Lichess's side
            requests.delete(
                TOKEN_URL, headers={"Authorization": f"Bearer {token}"}, timeout=15
            )
        except requests.RequestException:
            pass


# --- UI -----------------------------------------------------------------------
st.set_page_config(page_title="Lichess Game Downloader", page_icon="♞")
handle_oauth_callback()

st.title("♞ Lichess Game Downloader")
st.write("Download a player's games from Lichess as a PGN file.")

token = st.session_state.get("token")

with st.sidebar:
    st.header("Account")
    if token:
        st.success(f"Logged in as **{st.session_state.get('me', '?')}**")
        st.button("Log out", on_click=logout)
    else:
        st.caption(
            "Logging in is optional. It gives higher rate limits and lets you "
            "download your own private games."
        )
        st.link_button("Log in with Lichess", build_login_url())

    st.header("Filters")
    max_games = st.number_input("Max games", min_value=1, max_value=10000, value=100, step=50)
    perf_types = st.multiselect("Game types (empty = all)", PERF_TYPES)
    rated = st.selectbox("Rated", ["Any", "Rated only", "Casual only"])
    color = st.selectbox("Played as", ["Any", "white", "black"])
    use_dates = st.checkbox("Filter by date")
    if use_dates:
        start = st.date_input("From", dt.date.today() - dt.timedelta(days=30))
        end = st.date_input("To", dt.date.today())

    st.header("PGN content")
    clocks = st.checkbox("Include clock times", value=False)
    evals = st.checkbox("Include analysis evals (if available)", value=False)

username = st.text_input("Lichess username", value=st.session_state.get("me", ""))


def build_params():
    params = {
        "max": int(max_games),
        "clocks": str(clocks).lower(),
        "evals": str(evals).lower(),
        "opening": "true",  # needed for the openings chart
    }
    if perf_types:
        params["perfType"] = ",".join(perf_types)
    if rated == "Rated only":
        params["rated"] = "true"
    elif rated == "Casual only":
        params["rated"] = "false"
    if color != "Any":
        params["color"] = color
    if use_dates:
        since = dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc)
        until = dt.datetime.combine(end, dt.time.max, tzinfo=dt.timezone.utc)
        params["since"] = int(since.timestamp() * 1000)
        params["until"] = int(until.timestamp() * 1000)
    return params


def fetch_games(name):
    headers = {"Accept": "application/x-chess-pgn"}
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
            raise RuntimeError(
                "Rate limited by Lichess. Wait a minute and try again."
            )
        resp.raise_for_status()

        count = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                chunks.append(chunk)
                count += chunk.count(b"[Event ")
                progress.info(f"Downloading... {count} games so far")
    progress.empty()
    return b"".join(chunks).decode("utf-8")


TAG_RE = re.compile(r'^\[(\w+) "(.*)"\]$')


def parse_games(pgn):
    """Read only the PGN header tags of each game into a list of dicts."""
    games, cur = [], {}
    for line in pgn.splitlines():
        m = TAG_RE.match(line.strip())
        if not m:
            continue
        key, val = m.groups()
        if key == "Event" and cur:
            games.append(cur)
            cur = {}
        cur[key] = val
    if cur:
        games.append(cur)
    return games


def opening_results(pgn, name, by_family):
    rows = []
    for g in parse_games(pgn):
        result = g.get("Result")
        if result not in ("1-0", "0-1", "1/2-1/2"):
            continue  # skip aborted / unfinished games
        is_white = g.get("White", "").lower() == name.lower()
        is_black = g.get("Black", "").lower() == name.lower()
        if not (is_white or is_black):
            continue
        if result == "1/2-1/2":
            outcome = "Draw"
        elif (result == "1-0") == is_white:
            outcome = "Win"
        else:
            outcome = "Loss"
        opening = g.get("Opening", "Unknown")
        if by_family:
            opening = opening.split(":")[0].strip()
        rows.append({
            "Opening": opening,
            "Color": "White" if is_white else "Black",
            "Result": outcome,
        })
    return pd.DataFrame(rows)


def render_opening_chart(pgn, name):
    st.subheader("Results by opening")
    c1, c2, c3 = st.columns(3)
    by_family = c1.toggle("Group by opening family", value=True,
                          help="Off = separate bars for each variation")
    color_filter = c2.selectbox("Played as", ["Both", "White", "Black"])
    top_n = c3.slider("Top N openings", 3, 30, 10)

    df = opening_results(pgn, name, by_family)
    if color_filter != "Both":
        df = df[df["Color"] == color_filter] if not df.empty else df
    if df.empty:
        st.info("No finished games to chart with these settings.")
        return

    counts = df.groupby(["Opening", "Result"]).size().reset_index(name="Games")
    totals = counts.groupby("Opening")["Games"].sum().sort_values(ascending=False)
    top = totals.head(top_n).index.tolist()
    counts = counts[counts["Opening"].isin(top)]

    order = ["Win", "Draw", "Loss"]
    chart = (
        alt.Chart(counts)
        .mark_bar()
        .encode(
            y=alt.Y("Opening:N", sort=top, title=None),
            x=alt.X("Games:Q", stack="zero", title="Games"),
            color=alt.Color(
                "Result:N",
                sort=order,
                scale=alt.Scale(domain=order, range=["#4caf50", "#9e9e9e", "#e53935"]),
            ),
            tooltip=["Opening", "Result", "Games"],
        )
        .properties(height=max(150, 32 * len(top)))
    )
    st.altair_chart(chart, use_container_width=True)

    table = (
        counts.pivot_table(index="Opening", columns="Result", values="Games",
                           aggfunc="sum", fill_value=0)
        .reindex(columns=order, fill_value=0)
        .loc[top]
    )
    table["Games"] = table[order].sum(axis=1)
    table["Win %"] = (table["Win"] / table["Games"] * 100).round(1)
    st.dataframe(table, use_container_width=True)


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
        with st.expander("Preview (first 3000 characters)"):
            st.code(pgn[:3000], language=None)

        render_opening_chart(pgn, st.session_state["pgn_user"])
