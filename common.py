"""Shared helpers: Lichess OAuth2 (Authorization Code + PKCE) and session handling."""
import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode

import requests
import streamlit as st

# Lichess needs no client registration: the client_id is any unique string
# that identifies your app, and the redirect URI can be any URL you control.
CLIENT_ID = os.environ.get("LICHESS_CLIENT_ID", "streamlit-lichess-downloader")
REDIRECT_URI = os.environ.get("LICHESS_REDIRECT_URI", "http://localhost:8501")
SCOPES = ""  # Downloading games needs no special scope.

AUTH_URL = "https://lichess.org/oauth"
TOKEN_URL = "https://lichess.org/api/token"
ACCOUNT_URL = "https://lichess.org/api/account"


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
    now = time.time()
    for key in [k for k, (_, t) in store.items() if now - t > 600]:
        store.pop(key, None)  # drop stale entries

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
        try:  # revoke the token on Lichess's side
            requests.delete(
                TOKEN_URL, headers={"Authorization": f"Bearer {token}"}, timeout=15
            )
        except requests.RequestException:
            pass


def sidebar_account():
    """Login / logout widget shown in the sidebar on every page."""
    with st.sidebar:
        st.header("Account")
        if st.session_state.get("token"):
            st.success(f"Logged in as **{st.session_state.get('me', '?')}**")
            st.button("Log out", on_click=logout)
        else:
            st.caption(
                "Logging in is optional. It gives higher rate limits and lets "
                "you download your own private games."
            )
            st.link_button("Log in with Lichess", build_login_url())
