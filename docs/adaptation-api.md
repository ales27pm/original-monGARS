# Explicit feedback and personality profile API

All routes below require the normal monGARS bearer token. Direct preference feedback creates an
approval-gated task; it does not change the active profile immediately.

```bash
read -r MONGARS_TOKEN < secrets/api_token.txt
MONGARS_ORIGIN=http://127.0.0.1:8000
```

## Inspect the current profile

```bash
curl -fsS \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/adaptation/profile" \
  | python3 -m json.tool
```

An owner without an approved preference profile receives revision `0`, source `default`, and an
empty preference array.

## Record helpfulness

Helpfulness is an observation only. It never changes personality by itself.

```bash
FEEDBACK_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
RESPONSE_TRACE_ID='trc_0123456789abcdef0123456789abcdef'

curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"kind\":\"helpfulness\",\"feedback_id\":\"${FEEDBACK_ID}\",\"response_trace_id\":\"${RESPONSE_TRACE_ID}\",\"helpful\":true}" \
  "${MONGARS_ORIGIN}/v1/adaptation/feedback" \
  | python3 -m json.tool
```

## Record a correction

Correction text stays in the private owner-scoped feedback record. It is not copied into personality
revisions or autobiographical events, and it does not infer a preference.

```bash
FEEDBACK_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
RESPONSE_TRACE_ID='trc_0123456789abcdef0123456789abcdef'

curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"kind\":\"correction\",\"feedback_id\":\"${FEEDBACK_ID}\",\"response_trace_id\":\"${RESPONSE_TRACE_ID}\",\"correction_text\":\"The corrected answer is ...\"}" \
  "${MONGARS_ORIGIN}/v1/adaptation/feedback" \
  | python3 -m json.tool
```

## Propose and approve a direct preference

Supported dimensions are `brevity`, `directness`, `formality`, `humor`, `initiative`, and
`technical_depth`. Values are normalized from `0.0` to `1.0`.

```bash
FEEDBACK_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"

SUBMISSION="$(curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"kind\":\"preference\",\"feedback_id\":\"${FEEDBACK_ID}\",\"dimension\":\"technical_depth\",\"desired_value\":0.85}" \
  "${MONGARS_ORIGIN}/v1/adaptation/feedback")"

printf '%s\n' "${SUBMISSION}" | python3 -m json.tool

TASK_ID="$(printf '%s' "${SUBMISSION}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["proposal_task"]["id"])')"
```

Review the bounded task summary and every exact payload page before approval:

```bash
REVIEW="$(curl -fsS \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}")"

printf '%s\n' "${REVIEW}" | python3 -m json.tool

curl -fsS \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}/payload?page=0" \
  | python3 -m json.tool

ACTION_DIGEST="$(printf '%s' "${REVIEW}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["action_digest"])')"
```

Approve only after the payload matches the intended preference transition:

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"action_digest\":\"${ACTION_DIGEST}\"}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}/approve" \
  | python3 -m json.tool
```

Poll until the worker reports `done` or `failed`:

```bash
curl -fsS \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}" \
  | python3 -m json.tool
```

A successful result includes the applied profile revision and digest. A stale approved proposal
fails rather than overwriting a profile that changed after review.

## Inspect immutable revision history

```bash
curl -fsS \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/adaptation/profile/revisions?limit=50" \
  | python3 -m json.tool
```

Subsequent chat requests automatically load the current immutable snapshot and pass it to Cortex as
bounded, advisory, untrusted wording context.
