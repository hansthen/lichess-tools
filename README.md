# Lichess Tools

A small Streamlit app that downloads your games from Lichess and charts your results by opening.

- **Download page**: fetch games with filters (game type, rated, colour, dates) and save them as PGN.
- **Analysis page**: stacked bar chart of wins, draws and losses per opening, plus a table with win rates. Works on downloaded games or an uploaded PGN file.
- Optional "Log in with Lichess" (OAuth2 + PKCE) for higher rate limits and private games.

## Run

```
pip install -r requirements.txt
streamlit run app.py
```

If you host the app somewhere other than `http://localhost:8501`, set:

```
LICHESS_REDIRECT_URI=https://your-app-url
LICHESS_CLIENT_ID=your-unique-app-name
```
