# Personality lifecycle controls

All endpoints require the normal monGARS bearer token. Reset and deletion only create protected tasks;
no mutation occurs until the exact task payload is approved and executed by the adaptation worker.

```bash
read -r MONGARS_TOKEN < secrets/api_token.txt
MONGARS_ORIGIN=http://127.0.0.1:8000
```

## Export

```bash
curl -fsS \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -o mongars-personality.json \
  "${MONGARS_ORIGIN}/v1/adaptation/profile/export"
```

The export includes private feedback payloads. Store it as sensitive owner data.

## Reset

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/adaptation/profile/reset" \
  | python3 -m json.tool
```

Reset produces a new positive revision whose source is `approved_profile`, whose preference list is
empty, and whose digest is the canonical empty-profile digest. Preference and feedback history are
retained.

## Delete

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/adaptation/profile/delete" \
  | python3 -m json.tool
```

The reviewed delete payload binds both the current profile revision/digest and a digest of all
personality data that will be removed. Any new feedback or task payload after review invalidates the
action. Successful execution removes profile state, private feedback, revisions, prior lifecycle
receipts, old personality task payloads, and their events. One privacy-safe deletion receipt remains.

Use the normal task detail, payload-page, and approval endpoints to review and approve either action.
The web controls are available at `/personality`; the Expo client exposes the same operations in its
Profile tab.
