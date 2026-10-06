import re

import altair as alt
import pandas as pd
import streamlit as st

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
    if not df.empty and color_filter != "Both":
        df = df[df["Color"] == color_filter]
    if df.empty:
        st.info(
            "No finished games to chart with these settings. "
            "Check that the username matches a player in the games."
        )
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
    st.altair_chart(chart, width="stretch")

    table = (
        counts.pivot_table(index="Opening", columns="Result", values="Games",
                           aggfunc="sum", fill_value=0)
        .reindex(columns=order, fill_value=0)
        .loc[top]
    )
    table["Games"] = table[order].sum(axis=1)
    table["Win %"] = (table["Win"] / table["Games"] * 100).round(1)
    st.dataframe(table, width="stretch")


# --- Page ---------------------------------------------------------------------
st.title("📊 Opening analysis")

pgn = st.session_state.get("pgn")
name = st.session_state.get("pgn_user", "")

with st.expander("Or analyse a PGN file from your computer", expanded=pgn is None):
    uploaded = st.file_uploader("PGN file", type=["pgn", "txt"])
    upload_name = st.text_input("Your username in these games")
    if uploaded is not None:
        pgn = uploaded.getvalue().decode("utf-8", errors="replace")
        name = upload_name.strip()

if pgn is None:
    st.info("No games loaded yet. Download some first, or upload a PGN file above.")
    st.page_link("download_page.py", label="Go to the Download page", icon="⬇️")
    st.stop()

if not name:
    st.warning("Enter your username above so results can be counted from your side.")
    st.stop()

st.caption(f"Analysing {pgn.count('[Event ')} games for **{name}**")
render_opening_chart(pgn, name)
