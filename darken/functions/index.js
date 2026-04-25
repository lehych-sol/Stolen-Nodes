const admin = require("firebase-admin");
const { defineSecret } = require("firebase-functions/params");
const { onRequest } = require("firebase-functions/v2/https");
const { setGlobalOptions } = require("firebase-functions/v2/options");

admin.initializeApp();
setGlobalOptions({ region: "us-central1", maxInstances: 10 });

const ingestToken = defineSecret("DARKEN_INGEST_TOKEN");

function toText(value, maxLength = 4000) {
  const text = `${value ?? ""}`.trim();
  return text.length > maxLength ? text.slice(0, maxLength) : text;
}

function toNullableNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function toStringArray(value, maxItems = 10, maxLength = 500) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, maxItems).map((item) => toText(item, maxLength)).filter(Boolean);
}

function toPreview(value) {
  const text = toText(value, 900000);
  return text.startsWith("data:image/") ? text : "";
}

function buildCorsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, x-darkhub-ingest-token",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
  };
}

exports.health = onRequest({ cors: true }, async (req, res) => {
  res.status(200).json({ ok: true, service: "darken-seedream-admin-sync" });
});

exports.ingestGeneration = onRequest({ cors: true, secrets: [ingestToken] }, async (req, res) => {
  const corsHeaders = buildCorsHeaders();
  Object.entries(corsHeaders).forEach(([key, value]) => res.setHeader(key, value));

  if (req.method === "OPTIONS") {
    res.status(204).send("");
    return;
  }

  if (req.method !== "POST") {
    res.status(405).json({ ok: false, error: "method_not_allowed" });
    return;
  }

  const expectedToken = toText(ingestToken.value(), 512);
  const providedToken = toText(req.get("x-darkhub-ingest-token"), 512);

  if (!expectedToken || providedToken !== expectedToken) {
    res.status(401).json({ ok: false, error: "invalid_token" });
    return;
  }

  const body = req.body && typeof req.body === "object" ? req.body : {};
  const now = Date.now();

  const doc = {
    eventVersion: toNullableNumber(body.event_version) ?? 1,
    packageName: toText(body.package_name, 120),
    packageVersion: toText(body.package_version, 40),
    taskId: toText(body.task_id, 120),
    status: toText(body.status, 40),
    summary: toText(body.summary, 2000),
    failureMessage: toText(body.failure_message, 2000),
    mode: toText(body.mode, 120),
    modelKey: toText(body.model_key, 120),
    endpoint: toText(body.endpoint, 300),
    prompt: toText(body.prompt, 6000),
    negativePrompt: toText(body.negative_prompt, 6000),
    effectivePrompt: toText(body.effective_prompt, 6000),
    promptStrategy: toText(body.prompt_strategy, 120),
    seed: toNullableNumber(body.seed),
    aspectRatio: toText(body.aspect_ratio, 120),
    aspectRatioLabel: toText(body.aspect_ratio_label, 120),
    enableSafetyChecker: Boolean(body.enable_safety_checker),
    referenceImagesCount: toNullableNumber(body.reference_images_count) ?? 0,
    clientLabel: toText(body.client_label, 120),
    machineName: toText(body.machine_name, 120),
    savedPaths: toStringArray(body.saved_paths, 20, 500),
    imageUrls: toStringArray(body.image_urls, 10, 1000),
    metadataPath: toText(body.metadata_path, 500),
    previewDataUrl: toPreview(body.preview_data_url),
    createdAt: admin.firestore.FieldValue.serverTimestamp(),
    createdAtMs: now,
    source: "darkhub-seedream45",
  };

  try {
    const ref = await admin.firestore().collection("generations").add(doc);
    res.status(200).json({ ok: true, id: ref.id });
  } catch (error) {
    console.error("ingestGeneration failed", error);
    res.status(500).json({ ok: false, error: "write_failed" });
  }
});
