# Submit Draft for Manager Review

Sends a ship-ready draft to the manager's Lark review group, where a reviewer AI
agent scores it against **its own** system and replies with free-text feedback.
This command runs the **revise → re-eval → resubmit** loop automatically until
the reviewer approves, or escalates to a human after `MAX_ROUNDS` (4).

This is the *external* gate. It runs only **after** our internal eval gate passes
— we never ship a draft to the manager that our own scorecard would block.

## Usage
`/submit-for-review [filename]`

### Example
```
/submit-for-review drafts/best-walking-shoes-2026-04-16.md
```

## Prerequisites

- `LARK_REVIEW_CHAT_ID` set in `data_sources/config/.env` (the review group's
  `oc_…` id). Find it with:
  `lark-cli im +chat-search --query "<group name>" --as bot`
- The **bot must be a member** of that group (it posts and reads as the bot).
- `lark-cli auth login` is current (token not expired).
- Optional: `LARK_REVIEWER_OPEN_ID` — the reviewer agent's open_id, to listen
  only for its replies (otherwise we take the first non-self reply).

## The Loop (what you, the agent, do)

Set `FILE` = the draft path, `ROUND = 1`, `MAX_ROUNDS = 4`. Read
`LARK_REVIEW_CHAT_ID` (and optional `LARK_REVIEWER_OPEN_ID`) from
`data_sources/config/.env`. Extract the `slug`, `cluster`, and `target_keyword`
from the draft front-matter for logging.

### Step 0 — Internal gate (precondition, must pass)
```bash
python3 -m data_sources.modules.eval.eval_runner "$FILE" --step publish
```
If this exits non-zero (2 = regenerate, 3 = block), **stop**: fix the draft via
the normal write/optimize loop first. Do not submit a draft our own gate rejects.
Record the internal total — you'll log it with each round.

### Step 1 — Post to the review group
```bash
python3 -m data_sources.modules.review_loop post --chat "$CHAT" --file "$FILE" --as bot
```
Capture `message_id`, `self_sender`, and `sent_at_ms` from the JSON output.

### Step 2 — Wait for the reviewer's reply (background)
Run this **in the background** so the session does not block. It polls every 60s
and exits the moment a qualifying reply lands (or after 30 min):
```bash
python3 -m data_sources.modules.review_loop wait \
  --chat "$CHAT" --since-ms "$SENT_AT_MS" \
  --exclude-message "$MESSAGE_ID" --exclude-sender "$SELF_SENDER" \
  [--reviewer "$REVIEWER_OPEN_ID"] --timeout 1800 --poll 60 \
  --out scratch/review-reply-r$ROUND.json
```
When it returns: if it timed out (exit 124 / `{"timeout": true}`), **escalate** —
tell the user no reply came within the window and stop.

### Step 3 — Read and interpret the reply
Run the parser for a structured first read **and** read the raw reply text
yourself:
```bash
python3 -m data_sources.modules.review_loop parse --file scratch/review-reply-r$ROUND.json
```
- If `approved: true` → go to **Step 6 (Approved)**.
- If `approved: false` → go to **Step 4 (Revise)**.
- If `approved: null` (ambiguous) → **you** decide from the raw `text`. When
  genuinely unclear whether it's an approval, treat it as *not approved* and
  revise; never assume approval. If it's clearly just a question, answer it in
  the group and wait again (does not count as a round).

### Step 4 — Revise per feedback
Apply the reviewer's feedback to `$FILE` directly (edit the draft). Make the
specific changes asked for; keep brand voice, YMYL compliance, and our internal
standards intact. Note concisely what you changed (for the round log).

### Step 5 — Re-run internal gate, then resubmit
```bash
python3 -m data_sources.modules.eval.eval_runner "$FILE" --step publish
```
Must pass again before resubmitting. Then log the round and loop:
```bash
python3 -m data_sources.modules.review_loop log --slug "$SLUG" --round $ROUND \
  --internal-score "$INTERNAL" --manager-score "$MANAGER_SCORE" \
  --approved false --reply-file scratch/review-reply-r$ROUND.json \
  --changes "change one|change two" --cluster "$CLUSTER" --keyword "$KW"
```
Increment `ROUND`. If `ROUND > MAX_ROUNDS`, **escalate** (Step 7). Otherwise go
back to Step 1 (re-post the revised draft; `sent_at_ms` updates each round).

### Step 6 — Approved
Log the approving round (`--approved true`), then tell the user it's approved and
ask whether to publish now. On confirmation, run `/publish-draft "$FILE"` (which
runs the pre-publish gate again as the final barrier). Do **not** auto-publish.

### Step 7 — Escalate (max rounds or timeout)
Stop the loop and post a concise summary to the user:
- rounds used, the latest manager score vs. their threshold,
- the outstanding feedback we could not satisfy,
- a short diff of what changed across rounds (from the round ledger:
  `python3 -m data_sources.modules.review_loop` → `read_rounds(slug)`).
Let the human decide: push back to the reviewer, override, or shelve the draft.

## Notes
- Identity is the **bot** for both posting and reading; the bot must be in the
  group and able to see the reviewer agent's messages.
- The reviewer's scoring system is independent of ours. We log both each round in
  `data_sources/ledger/review_rounds.jsonl` so calibration can later check
  whether our internal score predicts their verdict.
- Hard cap: `MAX_ROUNDS = 4` and a 30-min per-round wait keep the whole loop
  inside ~2 hours. Tune `MAX_ROUNDS` / `--timeout` in
  `data_sources/modules/review_loop.py` if needed.
- Ambiguity is never treated as approval — when unsure, revise or ask.
