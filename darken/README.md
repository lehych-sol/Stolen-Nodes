# darken Firebase Admin Sync

This folder contains a Spark-compatible Firebase admin feed for `darkHUB Seedream 4.5`.

It works without Firebase Functions:

- the ComfyUI node signs in anonymously with Firebase Auth
- the node writes generation events directly to Firestore through the official REST API
- the admin panel reads Firestore with Google sign-in

Important:

- use this only with clear user notice and consent
- `node-sync.json` should stay local to you
- if sync fails, the custom node still keeps generating images

## Spark-compatible stack

- `Firestore`: stores generation logs
- `Authentication -> Google`: admin dashboard login
- `Authentication -> Anonymous`: writer login for the custom node
- `Hosting`: serves the admin dashboard

No Blaze plan is required for this starter.

## Folder layout

- `public/`: Firebase Hosting admin panel
- `firestore.rules`: admin-only reads, authenticated create-only writes
- `firebase.json`: Hosting + Firestore deploy config
- `node-sync.example.json`: sample config for the Python node
- `node-sync.json`: active local config used by the Python node

## 1. Firebase console setup

1. Create the Firebase project.
2. Create Firestore.
3. Enable `Authentication -> Google`.
4. Enable `Authentication -> Anonymous`.
5. Add a Web app and copy the Firebase web config.
6. Put your Firebase web config into `public/config.js`.
7. Put your admin email into:
   - `public/config.js`
   - `firestore.rules`

## 2. Deploy on Spark

Install the Firebase CLI on your machine, then deploy only Hosting + Firestore:

```powershell
cd darken
firebase login
firebase deploy --only hosting,firestore
```

This deploys:

- Firestore security rules
- Firestore indexes
- the admin panel on Firebase Hosting

## 3. Node sync config

The Python node reads:

`darken/node-sync.json`

Current Spark format:

```json
{
  "enabled": true,
  "provider": "firebase_spark",
  "firebase_api_key": "YOUR_WEB_API_KEY",
  "firebase_project_id": "YOUR_PROJECT_ID",
  "client_label": "darkHUB Seedream 4.5",
  "send_failures": true,
  "send_preview": true
}
```

Supported env var overrides:

- `DARKHUB_ADMIN_SYNC_PROVIDER`
- `DARKHUB_ADMIN_SYNC_ENABLED`
- `DARKHUB_ADMIN_SYNC_FIREBASE_API_KEY`
- `DARKHUB_ADMIN_SYNC_FIREBASE_PROJECT_ID`
- `DARKHUB_ADMIN_SYNC_CLIENT_LABEL`
- `DARKHUB_ADMIN_SYNC_SEND_FAILURES`
- `DARKHUB_ADMIN_SYNC_SEND_PREVIEW`

## Stored collection

Collection: `generations`

Main fields:

- `createdAtMs`
- `clientLabel`
- `machineName`
- `mode`
- `modelKey`
- `prompt`
- `negativePrompt`
- `effectivePrompt`
- `seed`
- `aspectRatio`
- `aspectRatioLabel`
- `status`
- `summary`
- `failureMessage`
- `previewDataUrl`
- `savedPaths`
- `imageUrls`
- `metadataPath`

## Security note

This Spark starter allows authenticated clients to create `generations` documents, but only signed-in admin emails can read them. The custom node uses anonymous auth to create write-only events.
