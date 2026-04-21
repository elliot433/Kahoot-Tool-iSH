# KahootKit

Auto-Answer, Flooder & Name Spammer for Kahoot — optimized for iSH on iPhone.

## Modes

| Mode | Description |
|------|-------------|
| **Auto-Answer** | Joins the game and automatically answers every question |
| **Flood** | Fills the lobby with up to 200 bots, all answering randomly |
| **Name Spam** | Spams the lobby with random names to overflow the player list |

## Install (iSH on iPhone)

Open iSH and run:

```sh
apk add git
git clone https://github.com/elliot433/kahootkit
cd kahootkit
sh install.sh
```

## Usage

```sh
python3 kahoot.py
```

1. Enter the **Kahoot PIN**
2. The tool checks if the game exists
3. Pick a mode:
   - `1` Auto-Answer — enter your name + answer strategy (random / always first / always second)
   - `2` Flood — enter name prefix + bot count (max 200)
   - `3` Name Spam — enter prefix + spam count

## Answer Strategies (Auto-Answer mode)

| Option | Behavior |
|--------|----------|
| Random | Picks a random answer each question |
| Always first | Always picks the red button (choice 0) |
| Always second | Always picks the blue button (choice 1) |

## Requirements

- Python 3
- `requests`
- `websocket-client`
- `nodejs` (optional, improves challenge decoding accuracy)

All installed automatically via `install.sh`.

## Notes

- Only use on games you host or have permission to test on.
- The tool routes through Kahoot's standard WebSocket API.
