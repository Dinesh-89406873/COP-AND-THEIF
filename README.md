# ARASAN Online Game

A Flask + Socket.IO browser game based on the ARASAN royal secret-character game.

## Features
- Username/email login and registration
- Create and join rooms with 6-character room codes
- 4 mandatory characters: King, Queen, Police, Thief
- Optional characters up to 10 total
- Online friends can join a room
- System players can fill missing slots
- Secret character sheet with "Click to View"
- Police guesses the Thief
- Correct Police guess gives Police +500
- Wrong Police guess gives Thief +500
- Automatic base points for other characters
- Ranking and score table

## Run locally

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

## Production
Use a production WSGI server such as Gunicorn with the included Procfile.

## Important security note
This demo stores passwords as plain text for simplicity. For a real deployment, replace this with password hashing (Werkzeug) and use HTTPS.

## Updated game rules
- Minimum 4 players; any number from 4 to 10 can play.
- If fewer than 4 humans join, System players are added automatically.
- If more humans join than the initially selected character count, optional characters are added automatically until every player has one character.
- Only the Police player gets the suspect selector. The selector lists every other player.
- The Police result is shown immediately as RIGHT or WRONG.
- The host can click CLOSE GAME to show the final rank and grade.


## Latest Game Rules

- The Points Table is hidden during play and becomes visible to everyone only after CLOSE GAME.
- Points are read-only; players cannot edit them.
- Every player has a profile and can optionally upload a profile picture.
- When CLOSE GAME is confirmed, the #1 player receives 1 persistent KINT credit (human players only).
- KINT carries to the player's next games through the user account.
- KINT is optional and can be activated by the Police for one round.
- While KINT is active, each wrong Police lock consumes 1 KINT credit and allows another guess in the same round.
- A correct Police guess ends the round immediately. If KINT is not used, the first locked wrong guess ends the round and awards +500 to the Thief.
- KINT is reset after the round and must be activated again in another round if credits remain.

## ARASAN gameplay updates
- Police timer starts only after the Police opens their private sheet.
- Police has 60 seconds from sheet-open time to lock a player.
- If Police does not lock in time, the Thief receives the +500 round bonus; Police receives no bonus.
- A Police lock is checked immediately: correct => Police +500, wrong => Thief +500.
- System Police opens its sheet automatically and behaves like a normal player before deciding.
- A System King waits exactly 15 seconds after a completed round before starting the next round.
- Private sheets use a closed-scroll/opening-scroll animation.
- Main game controls use a raised royal/game-button treatment with stronger typography and responsive mobile layout.


## Render deployment
Use the repository root that contains `app.py`, `templates/`, and `static/`. Start command:
`gunicorn --worker-class gthread --threads 100 --bind 0.0.0.0:$PORT app:app`

This build includes a full CSS fallback at `/assets/css/style.css` plus critical inline CSS in every page, so the UI remains styled even if the primary static URL is cached or unavailable.
