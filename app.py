import streamlit as st

from common import handle_oauth_callback, sidebar_account

st.set_page_config(page_title="Lichess Tools", page_icon="♞")

# Must run before navigation: finishes the login if Lichess just redirected back.
handle_oauth_callback()

pages = [
    st.Page("download_page.py", title="Download", icon="⬇️", default=True),
    st.Page("analysis_page.py", title="Analysis", icon="📊"),
]
nav = st.navigation(pages)
sidebar_account()
nav.run()
