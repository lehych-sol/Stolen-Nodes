from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import numpy as np
import torch
from PIL import Image

import folder_paths

try:
    import requests
except ImportError:
    requests = None

try:
    from ._version import VERSION as PACKAGE_VERSION
except ImportError:
    PACKAGE_VERSION = "0.0.1"


PACKAGE_NAME = "darkHUB Seedream 4.5"
NODE_CATEGORY = "darkHUB"
OUTPUT_SUBFOLDER = "darkHUB-Seedream-4.5"
USER_AGENT = f"darkHUB-Seedream-4.5/{PACKAGE_VERSION}"

API_KEY_ENV_VARS = ("FREEPIK_API_KEY", "X_FREEPIK_API_KEY", "FREEPIK_KEY")
FAILED_STATUSES = {"FAILED", "ERROR", "CANCELLED", "REJECTED"}
REQUEST_RETRY_ATTEMPTS = 3
REQUEST_RETRY_BACKOFF_SECONDS = 1.5
DEFAULT_TIMEOUT_SECONDS = 240
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
MAX_REFERENCE_IMAGES = 5
MIN_REFERENCE_SIDE = 256
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_TMPFILES_UPLOAD_BYTES = 200 * 1024 * 1024
ADMIN_SYNC_TIMEOUT_SECONDS = 20
ADMIN_SYNC_PREVIEW_MAX_SIDE = 448
ADMIN_SYNC_CONFIG_PATH = Path(__file__).resolve().parent / "darken" / "node-sync.json"
FIREBASE_AUTH_SIGNUP_ENDPOINT = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"

GENERATE_ENDPOINT = "https://api.freepik.com/v1/ai/text-to-image/seedream-v4-5"
EDIT_ENDPOINT = "https://api.freepik.com/v1/ai/text-to-image/seedream-v4-5-edit"

MODE_GENERATE = "Seedream v4.5 Generate"
MODE_EDIT = "Seedream v4.5 Edit"
MODE_CHOICES = (MODE_EDIT, MODE_GENERATE)

ASPECT_RATIO_TO_API = {
    "Square: 1:1": "square_1_1",
    "Widescreen: 16:9": "widescreen_16_9",
    "Story: 9:16": "social_story_9_16",
    "Portrait: 2:3": "portrait_2_3",
    "Traditional: 3:4": "traditional_3_4",
    "Standard: 3:2": "standard_3_2",
    "Classic: 4:3": "classic_4_3",
    "Cinematic: 21:9": "cinematic_21_9",
}
ASPECT_RATIO_CHOICES = tuple(ASPECT_RATIO_TO_API.keys())

FILENAME_CLEANUP_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class FreepikTaskFailedError(RuntimeError):
    def __init__(self, task_id: str, status: str, task_data: dict[str, Any]):
        self.task_id = task_id
        self.status = status
        self.task_data = task_data
        super().__init__(f"Freepik task {task_id} failed with status `{status}`: {_extract_error_message(task_data)}")


def _log(message: str) -> None:
    print(f"[{PACKAGE_NAME} v{PACKAGE_VERSION}] {message}")


def _require_requests() -> None:
    if requests is None:
        raise RuntimeError(
            "The 'requests' package is not installed. Run `pip install -r requirements.txt` inside this custom node folder."
        )


def _resolve_api_key(api_key: str) -> str:
    candidate = (api_key or "").strip()
    if candidate:
        return candidate
    for env_name in API_KEY_ENV_VARS:
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value
    raise ValueError(
        "Freepik API key not found. Fill the `api_key` input or set one of these env vars: "
        + ", ".join(API_KEY_ENV_VARS)
    )


def _create_session(api_key: str):
    _require_requests()
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "x-freepik-api-key": _resolve_api_key(api_key),
        }
    )
    return session


def _extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("errors", "details", "detail", "message", "error"):
            value = payload.get(key)
            if value:
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False)
                return str(value)
        return json.dumps(payload, ensure_ascii=False)
    if isinstance(payload, list):
        return json.dumps(payload, ensure_ascii=False)
    return str(payload)


def _is_validation_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "http 400" in text and ("validation" in text or "request fields" in text)


def _request_json(
    session,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: int = 60,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    last_network_exc: Exception | None = None
    response = None
    for attempt_index in range(REQUEST_RETRY_ATTEMPTS):
        try:
            response = session.request(
                method=method.upper(),
                url=url,
                json=json_body,
                timeout=timeout,
                headers=extra_headers,
            )
            break
        except requests.exceptions.RequestException as exc:
            last_network_exc = exc
            if attempt_index >= REQUEST_RETRY_ATTEMPTS - 1:
                raise
            sleep_seconds = REQUEST_RETRY_BACKOFF_SECONDS * (attempt_index + 1)
            _log(f"Network retry {attempt_index + 1}/{REQUEST_RETRY_ATTEMPTS - 1}: {exc}")
            time.sleep(sleep_seconds)

    if response is None:
        assert last_network_exc is not None
        raise last_network_exc

    try:
        payload = response.json()
    except Exception:
        body = response.text.strip()
        if response.ok:
            raise RuntimeError(f"Freepik returned a non-JSON response: {body[:500]}")
        raise RuntimeError(f"Freepik API HTTP {response.status_code}: {body[:500]}")

    if not response.ok:
        error_message = _extract_error_message(payload)
        if json_body:
            request_keys = ", ".join(sorted(json_body.keys()))
            raise RuntimeError(
                f"Freepik API HTTP {response.status_code}: {error_message} | Request fields: {request_keys}"
            )
        raise RuntimeError(f"Freepik API HTTP {response.status_code}: {error_message}")

    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return data
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Unexpected Freepik response payload: {payload!r}")


def _request_bytes(session, url: str, *, timeout: int = 300) -> tuple[bytes, dict[str, str]]:
    last_network_exc: Exception | None = None
    for attempt_index in range(REQUEST_RETRY_ATTEMPTS):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            headers = {str(key): str(value) for key, value in response.headers.items()}
            return response.content, headers
        except requests.exceptions.RequestException as exc:
            last_network_exc = exc
            if attempt_index >= REQUEST_RETRY_ATTEMPTS - 1:
                raise
            sleep_seconds = REQUEST_RETRY_BACKOFF_SECONDS * (attempt_index + 1)
            _log(f"Download retry {attempt_index + 1}/{REQUEST_RETRY_ATTEMPTS - 1} for {url}: {exc}")
            time.sleep(sleep_seconds)
    assert last_network_exc is not None
    raise last_network_exc


def _submit_task(session, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _request_json(
        session,
        "POST",
        endpoint,
        json_body=payload,
        timeout=max(DEFAULT_TIMEOUT_SECONDS, 120),
        extra_headers={"Content-Type": "application/json"},
    )


def _poll_task(session, endpoint: str, task_id: str, timeout_seconds: int, poll_interval_seconds: float) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while True:
        data = _request_json(session, "GET", f"{endpoint}/{task_id}", timeout=60)
        status = str(data.get("status") or "UNKNOWN").upper()
        if status == "COMPLETED":
            return data
        if status in FAILED_STATUSES:
            raise FreepikTaskFailedError(task_id=task_id, status=status, task_data=data)
        if time.time() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for Freepik task {task_id} after {timeout_seconds} seconds. Last status: {status}"
            )
        time.sleep(max(0.25, poll_interval_seconds))


def _truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max(0, max_length - 3)].rstrip() + "..."


def _compose_prompt(prompt: str, negative_prompt: str | None, *, max_length: int = 4096) -> tuple[str, dict[str, Any]]:
    cleaned_prompt = (prompt or "").strip()
    if not cleaned_prompt:
        raise ValueError("`prompt` cannot be empty.")

    cleaned_negative = (negative_prompt or "").strip()
    if not cleaned_negative:
        effective = _truncate_text(cleaned_prompt, max_length)
        return effective, {
            "prompt_strategy": "plain",
            "negative_prompt_used": False,
            "prompt_was_truncated": effective != cleaned_prompt,
            "original_prompt_length": len(cleaned_prompt),
            "original_negative_prompt_length": 0,
        }

    prefix = "\n\nAvoid: "
    positive_budget = max(1, max_length - len(prefix))
    prompt_part = _truncate_text(cleaned_prompt, positive_budget)
    negative_budget = max(0, max_length - len(prompt_part) - len(prefix))
    negative_part = _truncate_text(cleaned_negative, negative_budget)
    effective = f"{prompt_part}{prefix}{negative_part}" if negative_part else _truncate_text(cleaned_prompt, max_length)
    return effective, {
        "prompt_strategy": "avoid_append",
        "negative_prompt_used": bool(negative_part),
        "prompt_was_truncated": prompt_part != cleaned_prompt or negative_part != cleaned_negative,
        "original_prompt_length": len(cleaned_prompt),
        "original_negative_prompt_length": len(cleaned_negative),
    }


def _normalize_aspect_ratio(value: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned in ASPECT_RATIO_TO_API:
        return ASPECT_RATIO_TO_API[cleaned]
    if cleaned in ASPECT_RATIO_TO_API.values():
        return cleaned
    return ASPECT_RATIO_TO_API["Traditional: 3:4"]


def _normalize_seed(seed: int) -> int | None:
    if seed is None:
        return None
    return max(0, min(int(seed), 4294967295))


def _clean_filename_prefix(value: str | None, default: str) -> str:
    cleaned = FILENAME_CLEANUP_PATTERN.sub("_", (value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._ ")
    return cleaned or default


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        payload = json.loads(raw)
    except Exception as exc:
        _log(f"Admin sync config ignored because JSON is invalid at {path}: {exc}")
        return {}
    if isinstance(payload, dict):
        return payload
    _log(f"Admin sync config ignored because top-level JSON is not an object at {path}")
    return {}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_admin_sync_settings() -> dict[str, Any]:
    file_config = _read_json_file(ADMIN_SYNC_CONFIG_PATH)
    provider = (
        os.getenv("DARKHUB_ADMIN_SYNC_PROVIDER")
        or str(file_config.get("provider") or "").strip()
    )
    env_enabled = os.getenv("DARKHUB_ADMIN_SYNC_ENABLED")
    enabled = _coerce_bool(
        env_enabled if env_enabled is not None else file_config.get("enabled"),
        default=False,
    )
    endpoint = os.getenv("DARKHUB_ADMIN_SYNC_ENDPOINT") or str(file_config.get("endpoint") or "").strip()
    token = os.getenv("DARKHUB_ADMIN_SYNC_TOKEN") or str(file_config.get("token") or "").strip()
    client_label = (
        os.getenv("DARKHUB_ADMIN_SYNC_CLIENT_LABEL") or str(file_config.get("client_label") or "").strip()
    )
    firebase_api_key = (
        os.getenv("DARKHUB_ADMIN_SYNC_FIREBASE_API_KEY")
        or str(file_config.get("firebase_api_key") or "").strip()
    )
    firebase_project_id = (
        os.getenv("DARKHUB_ADMIN_SYNC_FIREBASE_PROJECT_ID")
        or str(file_config.get("firebase_project_id") or "").strip()
    )
    send_failures = _coerce_bool(
        os.getenv("DARKHUB_ADMIN_SYNC_SEND_FAILURES"),
        default=_coerce_bool(file_config.get("send_failures"), default=True),
    )
    send_preview = _coerce_bool(
        os.getenv("DARKHUB_ADMIN_SYNC_SEND_PREVIEW"),
        default=_coerce_bool(file_config.get("send_preview"), default=True),
    )
    resolved_provider = provider
    if not resolved_provider:
        if firebase_api_key and firebase_project_id:
            resolved_provider = "firebase_spark"
        elif endpoint:
            resolved_provider = "webhook"
    provider_ready = False
    if resolved_provider == "firebase_spark":
        provider_ready = bool(firebase_api_key and firebase_project_id)
    elif resolved_provider == "webhook":
        provider_ready = bool(endpoint)
    return {
        "enabled": enabled and provider_ready,
        "provider": resolved_provider,
        "endpoint": endpoint,
        "token": token,
        "client_label": client_label,
        "firebase_api_key": firebase_api_key,
        "firebase_project_id": firebase_project_id,
        "send_failures": send_failures,
        "send_preview": send_preview,
        "config_path": str(ADMIN_SYNC_CONFIG_PATH),
    }


def _get_output_dir() -> Path:
    return Path(folder_paths.get_output_directory())


def _get_model_output_dir(model_key: str) -> Path:
    output_dir = _get_output_dir() / OUTPUT_SUBFOLDER / model_key
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _asset_extension_from_response(url: str, headers: dict[str, str]) -> tuple[str, str]:
    content_type = (headers.get("Content-Type") or headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type.startswith("image/"):
        return mimetypes.guess_extension(content_type) or ".png", "image"
    path_ext = Path(urlparse(url).path).suffix.lower()
    if path_ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return path_ext, "image"
    return ".png", "image"


def _pil_to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB")).astype(np.float32) / 255.0


def _pil_images_to_tensor(images: list[Image.Image]):
    if not images:
        return _empty_image_tensor()
    arrays = [_pil_to_array(image) for image in images]
    return torch.from_numpy(np.stack(arrays, axis=0))


def _empty_image_tensor():
    return torch.zeros((1, 1, 1, 3), dtype=torch.float32)


def _tensor_to_pil(image_tensor) -> Image.Image:
    if image_tensor is None:
        raise ValueError("Expected an IMAGE input but got None.")
    array = np.asarray(image_tensor)
    if array.ndim == 4:
        array = array[0]
    array = np.clip(array * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(array)


def _normalize_reference_image(image: Image.Image) -> Image.Image:
    normalized = image.convert("RGB")
    min_side = min(normalized.size)
    if min_side >= MIN_REFERENCE_SIDE:
        return normalized
    scale = MIN_REFERENCE_SIDE / float(min_side)
    target_size = (
        max(MIN_REFERENCE_SIDE, int(round(normalized.size[0] * scale))),
        max(MIN_REFERENCE_SIDE, int(round(normalized.size[1] * scale))),
    )
    return normalized.resize(target_size, Image.LANCZOS)


def _build_preview_data_url(image: Image.Image, *, max_side: int = ADMIN_SYNC_PREVIEW_MAX_SIDE) -> str:
    preview = image.convert("RGB")
    width, height = preview.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / float(longest)
        preview = preview.resize(
            (
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale))),
            ),
            Image.LANCZOS,
        )
    buffer = io.BytesIO()
    preview.save(buffer, format="JPEG", quality=82, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _encode_reference_image(image: Image.Image, *, prefer_name: str) -> dict[str, Any]:
    normalized = _normalize_reference_image(image)
    png_buffer = io.BytesIO()
    normalized.save(png_buffer, format="PNG", optimize=True)
    png_bytes = png_buffer.getvalue()

    if len(png_bytes) <= MAX_REFERENCE_BYTES:
        chosen_bytes = png_bytes
        mime_type = "image/png"
        extension = ".png"
    else:
        jpg_buffer = io.BytesIO()
        normalized.save(jpg_buffer, format="JPEG", quality=95, optimize=True)
        jpg_bytes = jpg_buffer.getvalue()
        if len(jpg_bytes) > MAX_REFERENCE_BYTES:
            raise ValueError(f"Reference image is too large after JPEG conversion ({len(jpg_bytes)} bytes).")
        chosen_bytes = jpg_bytes
        mime_type = "image/jpeg"
        extension = ".jpg"

    raw_base64 = base64.b64encode(chosen_bytes).decode("ascii")
    return {
        "binary": chosen_bytes,
        "bytes": len(chosen_bytes),
        "raw_base64": raw_base64,
        "data_uri": f"data:{mime_type};base64,{raw_base64}",
        "mime_type": mime_type,
        "extension": extension,
        "filename": f"{prefer_name}{extension}",
        "width": normalized.size[0],
        "height": normalized.size[1],
    }


def _tensor_batch_to_reference_images(images, max_items: int, *, prefix: str) -> list[dict[str, Any]]:
    if images is None:
        return []
    batch = np.asarray(images)
    if batch.ndim == 3:
        batch = np.expand_dims(batch, axis=0)
    collected: list[dict[str, Any]] = []
    for index, item in enumerate(batch[:max_items], start=1):
        pil_image = _tensor_to_pil(np.expand_dims(item, axis=0))
        collected.append(_encode_reference_image(pil_image, prefer_name=f"{prefix}_{index:02d}"))
    return collected


def _collect_reference_images(*image_batches) -> tuple[list[dict[str, Any]], int]:
    collected: list[dict[str, Any]] = []
    connected_inputs = 0
    for socket_index, batch in enumerate(image_batches, start=1):
        if batch is None:
            continue
        connected_inputs += 1
        remaining = MAX_REFERENCE_IMAGES - len(collected)
        if remaining <= 0:
            break
        collected.extend(_tensor_batch_to_reference_images(batch, remaining, prefix=f"ref_{socket_index:02d}"))
    return collected, connected_inputs


def _to_tmpfiles_direct_url(url: str) -> str:
    cleaned = (url or "").strip()
    if cleaned.startswith("https://tmpfiles.org/") and "/dl/" not in cleaned:
        return cleaned.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/", 1)
    return cleaned


def _upload_tmpfile(binary: bytes, filename: str, mime_type: str) -> str:
    _require_requests()
    if len(binary) > MAX_TMPFILES_UPLOAD_BYTES:
        raise ValueError(f"Temporary upload does not support files larger than {MAX_TMPFILES_UPLOAD_BYTES} bytes.")
    response = requests.post(
        "https://tmpfiles.org/api/v1/upload",
        files={"file": (filename, binary, mime_type)},
        timeout=180,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    url = str(payload.get("data", {}).get("url") or payload.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"Unexpected tmpfiles upload response: {payload!r}")
    return _to_tmpfiles_direct_url(url)


def _upload_reference_images_to_tmpfiles(reference_images: list[dict[str, Any]]) -> list[str]:
    return [_upload_tmpfile(item["binary"], item["filename"], item["mime_type"]) for item in reference_images]


def _remove_payload_field(payload: dict[str, Any], field: str) -> dict[str, Any]:
    updated = dict(payload)
    updated.pop(field, None)
    return updated


def _seedream_create_attempts(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    without_seed = _remove_payload_field(payload, "seed")
    without_safety = _remove_payload_field(payload, "enable_safety_checker")
    without_seed_or_safety = _remove_payload_field(without_safety, "seed")
    return [
        ("default", payload),
        ("without_seed", without_seed),
        ("without_safety_checker", without_safety),
        ("without_seed_or_safety_checker", without_seed_or_safety),
    ]


def _seedream_edit_attempts(
    payload: dict[str, Any],
    reference_images: list[dict[str, Any]],
) -> tuple[list[tuple[str, dict[str, Any]]], Callable[[], list[tuple[str, dict[str, Any]]]]]:
    raw_payload = dict(payload)
    raw_payload["reference_images"] = [item["raw_base64"] for item in reference_images]

    data_uri_payload = dict(payload)
    data_uri_payload["reference_images"] = [item["data_uri"] for item in reference_images]

    attempts = [
        ("raw_base64", raw_payload),
        ("raw_base64_without_seed", _remove_payload_field(raw_payload, "seed")),
        ("data_uri", data_uri_payload),
        ("data_uri_without_seed", _remove_payload_field(data_uri_payload, "seed")),
        ("raw_base64_without_safety_checker", _remove_payload_field(raw_payload, "enable_safety_checker")),
        (
            "raw_base64_without_seed_or_safety_checker",
            _remove_payload_field(_remove_payload_field(raw_payload, "enable_safety_checker"), "seed"),
        ),
        ("data_uri_without_safety_checker", _remove_payload_field(data_uri_payload, "enable_safety_checker")),
        (
            "data_uri_without_seed_or_safety_checker",
            _remove_payload_field(_remove_payload_field(data_uri_payload, "enable_safety_checker"), "seed"),
        ),
    ]

    def deferred_public_url_attempts() -> list[tuple[str, dict[str, Any]]]:
        _log("Direct reference image payloads failed validation. Uploading temporary public URLs as fallback.")
        public_urls = _upload_reference_images_to_tmpfiles(reference_images)
        url_payload = dict(payload)
        url_payload["reference_images"] = public_urls
        return [
            ("public_url_tmpfiles", url_payload),
            ("public_url_tmpfiles_without_seed", _remove_payload_field(url_payload, "seed")),
            ("public_url_tmpfiles_without_safety_checker", _remove_payload_field(url_payload, "enable_safety_checker")),
            (
                "public_url_tmpfiles_without_seed_or_safety_checker",
                _remove_payload_field(_remove_payload_field(url_payload, "enable_safety_checker"), "seed"),
            ),
        ]

    return attempts, deferred_public_url_attempts


def _download_generated_assets(
    session,
    asset_urls: list[str],
    *,
    model_key: str,
    filename_prefix: str,
    task_id: str,
) -> tuple[list[Image.Image], list[Path], list[dict[str, Any]]]:
    output_dir = _get_model_output_dir(model_key)
    downloaded_images: list[Image.Image] = []
    saved_paths: list[Path] = []
    asset_manifest: list[dict[str, Any]] = []

    for index, url in enumerate(asset_urls, start=1):
        binary, headers = _request_bytes(session, url)
        extension, asset_kind = _asset_extension_from_response(url, headers)
        target_path = output_dir / f"{filename_prefix}_{task_id}_{index:02d}{extension}"
        target_path.write_bytes(binary)
        saved_paths.append(target_path)

        entry = {
            "url": url,
            "saved_path": str(target_path),
            "kind": asset_kind,
            "content_type": headers.get("Content-Type") or headers.get("content-type") or "",
        }
        image = Image.open(io.BytesIO(binary)).convert("RGB")
        downloaded_images.append(image)
        entry["width"] = image.size[0]
        entry["height"] = image.size[1]
        asset_manifest.append(entry)

    return downloaded_images, saved_paths, asset_manifest


def _save_metadata(
    *,
    model_key: str,
    filename_prefix: str,
    task_id: str,
    endpoint: str,
    request_summary: dict[str, Any],
    task_data: dict[str, Any],
    saved_paths: list[Path],
    asset_manifest: list[dict[str, Any]],
) -> Path:
    metadata_path = _get_model_output_dir(model_key) / f"{filename_prefix}_{task_id}_metadata.json"
    _save_json(
        metadata_path,
        {
            "package_name": PACKAGE_NAME,
            "package_version": PACKAGE_VERSION,
            "model_key": model_key,
            "endpoint": endpoint,
            "request_summary": request_summary,
            "task": task_data,
            "saved_paths": [str(path) for path in saved_paths],
            "assets": asset_manifest,
        },
    )
    return metadata_path


def _build_ui_payload(
    *,
    summary: str,
    task_id: str,
    status: str,
    asset_urls: list[str],
    saved_paths: list[Path],
    task_data: dict[str, Any],
    metadata_path: Path,
    image_tensor=None,
):
    asset_urls_json = json.dumps(asset_urls, ensure_ascii=False, indent=2)
    saved_paths_json = json.dumps([str(path) for path in saved_paths], ensure_ascii=False, indent=2)
    task_json = json.dumps(task_data, ensure_ascii=False, indent=2)
    return {
        "ui": {
            "text": [summary],
            "status": [status],
            "task_id": [task_id],
            "image_urls_json": [asset_urls_json],
            "saved_paths_json": [saved_paths_json],
            "task_json": [task_json],
            "metadata_path": [str(metadata_path)],
        },
        "result": (
            image_tensor if image_tensor is not None else _empty_image_tensor(),
            task_id,
            status,
            asset_urls_json,
            saved_paths_json,
            task_json,
            str(metadata_path),
            summary,
        ),
    }


def _post_admin_sync(session, settings: dict[str, Any], payload: dict[str, Any]) -> None:
    if not settings.get("enabled"):
        return

    provider = str(settings.get("provider") or "").strip().lower()
    if provider == "firebase_spark":
        _post_admin_sync_to_firebase(settings, payload)
        return

    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    token = str(settings.get("token") or "").strip()
    if token:
        headers["x-darkhub-ingest-token"] = token

    response = session.post(
        str(settings.get("endpoint") or "").strip(),
        json=payload,
        timeout=ADMIN_SYNC_TIMEOUT_SECONDS,
        headers=headers,
    )
    if response.status_code >= 400:
        body = response.text.strip()
        raise RuntimeError(f"Admin sync HTTP {response.status_code}: {body[:500]}")


def _firestore_string(value: Any) -> dict[str, Any]:
    return {"stringValue": str(value or "")}


def _firestore_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        if not value:
            return {"arrayValue": {}}
        return {"arrayValue": {"values": [_firestore_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {str(key): _firestore_value(item) for key, item in value.items()}}}
    return {"stringValue": str(value)}


def _sign_in_anonymously_to_firebase(api_key: str) -> str:
    _require_requests()
    response = requests.post(
        f"{FIREBASE_AUTH_SIGNUP_ENDPOINT}?key={api_key}",
        json={"returnSecureToken": True},
        timeout=ADMIN_SYNC_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        payload = response.json()
    except Exception:
        payload = response.text.strip()

    if not response.ok:
        raise RuntimeError(f"Firebase anonymous auth failed: {_extract_error_message(payload)}")

    id_token = str(payload.get("idToken") or "").strip() if isinstance(payload, dict) else ""
    if not id_token:
        raise RuntimeError(f"Firebase anonymous auth response is missing idToken: {payload!r}")
    return id_token


def _post_admin_sync_to_firebase(settings: dict[str, Any], payload: dict[str, Any]) -> None:
    _require_requests()
    project_id = str(settings.get("firebase_project_id") or "").strip()
    api_key = str(settings.get("firebase_api_key") or "").strip()
    if not project_id or not api_key:
        raise RuntimeError("Firebase Spark admin sync requires firebase_project_id and firebase_api_key.")

    id_token = _sign_in_anonymously_to_firebase(api_key)
    document = {
        "eventVersion": payload.get("event_version", 1),
        "packageName": payload.get("package_name", ""),
        "packageVersion": payload.get("package_version", ""),
        "taskId": payload.get("task_id", ""),
        "status": payload.get("status", ""),
        "summary": payload.get("summary", ""),
        "failureMessage": payload.get("failure_message", ""),
        "mode": payload.get("mode", ""),
        "modelKey": payload.get("model_key", ""),
        "endpoint": payload.get("endpoint", ""),
        "prompt": payload.get("prompt", ""),
        "negativePrompt": payload.get("negative_prompt", ""),
        "effectivePrompt": payload.get("effective_prompt", ""),
        "promptStrategy": payload.get("prompt_strategy", ""),
        "seed": payload.get("seed"),
        "aspectRatio": payload.get("aspect_ratio", ""),
        "aspectRatioLabel": payload.get("aspect_ratio_label", ""),
        "enableSafetyChecker": bool(payload.get("enable_safety_checker")),
        "referenceImagesCount": int(payload.get("reference_images_count") or 0),
        "clientLabel": payload.get("client_label", ""),
        "machineName": payload.get("machine_name", ""),
        "savedPaths": payload.get("saved_paths", []),
        "imageUrls": payload.get("image_urls", []),
        "metadataPath": payload.get("metadata_path", ""),
        "previewDataUrl": payload.get("preview_data_url", ""),
        "createdAtMs": int(time.time() * 1000),
        "source": "darkhub-seedream45",
    }
    firestore_payload = {
        "fields": {key: _firestore_value(value) for key, value in document.items()}
    }
    endpoint = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/generations"
    response = requests.post(
        endpoint,
        json=firestore_payload,
        timeout=ADMIN_SYNC_TIMEOUT_SECONDS,
        headers={
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        response_payload = response.json()
    except Exception:
        response_payload = response.text.strip()

    if not response.ok:
        raise RuntimeError(f"Firebase Firestore write failed: {_extract_error_message(response_payload)}")


def _sync_generation_event(
    session,
    *,
    settings: dict[str, Any],
    request_summary: dict[str, Any],
    task_id: str,
    status: str,
    summary: str,
    saved_paths: list[Path],
    asset_urls: list[str],
    metadata_path: Path,
    preview_image: Image.Image | None,
    failure_message: str | None = None,
) -> None:
    if not settings.get("enabled"):
        return
    if status in FAILED_STATUSES and not settings.get("send_failures", True):
        return

    preview_data_url = ""
    if preview_image is not None and settings.get("send_preview", True):
        try:
            preview_data_url = _build_preview_data_url(preview_image)
        except Exception as exc:
            _log(f"Admin sync preview skipped: {exc}")

    payload = {
        "event_version": 1,
        "package_name": PACKAGE_NAME,
        "package_version": PACKAGE_VERSION,
        "task_id": task_id,
        "status": status,
        "summary": summary,
        "failure_message": failure_message or "",
        "mode": request_summary.get("mode") or "",
        "model_key": request_summary.get("model_key") or "",
        "endpoint": request_summary.get("endpoint") or "",
        "prompt": request_summary.get("prompt_input") or "",
        "negative_prompt": request_summary.get("negative_prompt_input") or "",
        "effective_prompt": request_summary.get("effective_prompt") or "",
        "prompt_strategy": request_summary.get("prompt_strategy") or "",
        "seed": request_summary.get("seed"),
        "aspect_ratio": request_summary.get("aspect_ratio") or "",
        "aspect_ratio_label": request_summary.get("aspect_ratio_label") or "",
        "enable_safety_checker": bool(request_summary.get("enable_safety_checker")),
        "reference_images_count": int(request_summary.get("reference_images_count") or 0),
        "client_label": settings.get("client_label") or os.getenv("COMPUTERNAME", "").strip(),
        "machine_name": os.getenv("COMPUTERNAME", "").strip(),
        "saved_paths": [str(path) for path in saved_paths],
        "image_urls": asset_urls,
        "metadata_path": str(metadata_path),
        "preview_data_url": preview_data_url,
    }
    _post_admin_sync(session, settings, payload)


def _submit_with_attempts(
    session,
    endpoint: str,
    attempts: list[tuple[str, dict[str, Any]]],
    deferred_attempts_factory: Callable[[], list[tuple[str, dict[str, Any]]]] | None,
    request_summary: dict[str, Any],
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt_name, attempt_payload in attempts:
        try:
            if attempt_name != "default":
                _log(f"Retrying submission with payload variant: {attempt_name}")
            data = _submit_task(session, endpoint, attempt_payload)
            request_summary["submitted_payload_variant"] = attempt_name
            return data
        except Exception as exc:
            last_exc = exc
            if not _is_validation_error(exc):
                raise

    if deferred_attempts_factory is not None:
        for attempt_name, attempt_payload in deferred_attempts_factory():
            try:
                _log(f"Retrying submission with payload variant: {attempt_name}")
                data = _submit_task(session, endpoint, attempt_payload)
                request_summary["submitted_payload_variant"] = attempt_name
                return data
            except Exception as exc:
                last_exc = exc
                if not _is_validation_error(exc):
                    raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("No Freepik payload attempts were available.")


def _run_task(
    *,
    api_key: str,
    endpoint: str,
    model_key: str,
    payload_attempts: list[tuple[str, dict[str, Any]]],
    request_summary: dict[str, Any],
    filename_prefix: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    admin_sync_settings: dict[str, Any] | None = None,
    deferred_payload_attempts_factory: Callable[[], list[tuple[str, dict[str, Any]]]] | None = None,
):
    session = _create_session(api_key)
    create_data = _submit_with_attempts(
        session,
        endpoint,
        payload_attempts,
        deferred_payload_attempts_factory,
        request_summary,
    )
    task_id = str(create_data.get("task_id") or create_data.get("id") or "").strip()
    if not task_id:
        raise RuntimeError(f"Freepik create-task response is missing `task_id`: {create_data!r}")

    _log(f"Submitted {model_key} task {task_id}")
    try:
        task_data = _poll_task(session, endpoint, task_id, timeout_seconds, poll_interval_seconds)
    except FreepikTaskFailedError as exc:
        request_summary["failure_status"] = exc.status
        request_summary["failure_message"] = _extract_error_message(exc.task_data)
        metadata_path = _save_metadata(
            model_key=model_key,
            filename_prefix=filename_prefix,
            task_id=task_id,
            endpoint=endpoint,
            request_summary=request_summary,
            task_data=exc.task_data,
            saved_paths=[],
            asset_manifest=[],
        )
        summary = (
            f"{model_key} failed provider-side | Task: {task_id} | Status: {exc.status} | "
            f"Reason: {_extract_error_message(exc.task_data)} | Metadata: {metadata_path}"
        )
        _log(summary)
        if admin_sync_settings:
            try:
                _sync_generation_event(
                    session,
                    settings=admin_sync_settings,
                    request_summary=request_summary,
                    task_id=task_id,
                    status=exc.status,
                    summary=summary,
                    saved_paths=[],
                    asset_urls=[],
                    metadata_path=metadata_path,
                    preview_image=None,
                    failure_message=_extract_error_message(exc.task_data),
                )
            except Exception as sync_exc:
                _log(f"Admin sync skipped after provider failure: {sync_exc}")
        return _build_ui_payload(
            summary=summary,
            task_id=task_id,
            status=exc.status,
            asset_urls=[],
            saved_paths=[],
            task_data=exc.task_data,
            metadata_path=metadata_path,
        )

    status = str(task_data.get("status") or "COMPLETED")
    asset_urls = [str(item) for item in (task_data.get("generated") or []) if str(item).strip()]
    downloaded_images, saved_paths, asset_manifest = _download_generated_assets(
        session,
        asset_urls,
        model_key=model_key,
        filename_prefix=filename_prefix,
        task_id=task_id,
    )
    image_tensor = _pil_images_to_tensor(downloaded_images)
    metadata_path = _save_metadata(
        model_key=model_key,
        filename_prefix=filename_prefix,
        task_id=task_id,
        endpoint=endpoint,
        request_summary=request_summary,
        task_data=task_data,
        saved_paths=saved_paths,
        asset_manifest=asset_manifest,
    )
    summary = (
        f"{model_key} completed | Task: {task_id} | Status: {status} | "
        f"Images: {len(asset_urls)} | Saved to: {OUTPUT_SUBFOLDER}"
    )
    _log(summary)
    if admin_sync_settings:
        try:
            _sync_generation_event(
                session,
                settings=admin_sync_settings,
                request_summary=request_summary,
                task_id=task_id,
                status=status,
                summary=summary,
                saved_paths=saved_paths,
                asset_urls=asset_urls,
                metadata_path=metadata_path,
                preview_image=downloaded_images[0] if downloaded_images else None,
            )
        except Exception as sync_exc:
            _log(f"Admin sync skipped because upload failed: {sync_exc}")
    return _build_ui_payload(
        summary=summary,
        task_id=task_id,
        status=status,
        asset_urls=asset_urls,
        saved_paths=saved_paths,
        task_data=task_data,
        metadata_path=metadata_path,
        image_tensor=image_tensor,
    )


class DarkHubFreepikStudio:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (MODE_CHOICES, {"default": MODE_EDIT}),
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "Improve this image with refined lighting, clean composition, premium commercial finish.",
                    },
                ),
                "negative_prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "watermark, logo, signature, random text, blur, low quality, artifacts",
                    },
                ),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "aspect_ratio": (ASPECT_RATIO_CHOICES, {"default": "Traditional: 3:4"}),
                "seed": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 0xFFFFFFFFFFFFFFFF,
                        "step": 1,
                        "control_after_generate": True,
                    },
                ),
                "enable_safety_checker": ("BOOLEAN", {"default": True}),
                "timeout_seconds": ("INT", {"default": DEFAULT_TIMEOUT_SECONDS, "min": 15, "max": 14400}),
                "poll_interval_seconds": (
                    "FLOAT",
                    {"default": DEFAULT_POLL_INTERVAL_SECONDS, "min": 0.25, "max": 60.0, "step": 0.25},
                ),
                "filename_prefix": ("STRING", {"default": "darkhub_seedream45"}),
                "webhook_url": ("STRING", {"default": "", "multiline": False}),
            },
            "optional": {
                "reference_image_1": ("IMAGE",),
                "reference_image_2": ("IMAGE",),
                "reference_image_3": ("IMAGE",),
                "reference_image_4": ("IMAGE",),
                "reference_image_5": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "images",
        "task_id",
        "status",
        "image_urls_json",
        "saved_paths_json",
        "task_json",
        "metadata_path",
        "summary",
    )
    FUNCTION = "run"
    CATEGORY = NODE_CATEGORY
    OUTPUT_NODE = True

    def run(
        self,
        mode,
        prompt,
        negative_prompt,
        api_key,
        aspect_ratio,
        seed,
        enable_safety_checker,
        timeout_seconds,
        poll_interval_seconds,
        filename_prefix,
        webhook_url,
        reference_image_1=None,
        reference_image_2=None,
        reference_image_3=None,
        reference_image_4=None,
        reference_image_5=None,
    ):
        effective_prompt, prompt_summary = _compose_prompt(prompt, negative_prompt, max_length=4096)
        normalized_seed = _normalize_seed(seed)
        aspect_ratio_value = _normalize_aspect_ratio(aspect_ratio)
        is_edit = mode == MODE_EDIT
        model_key = "seedream_v4_5_edit" if is_edit else "seedream_v4_5"
        endpoint = EDIT_ENDPOINT if is_edit else GENERATE_ENDPOINT

        request_summary = {
            "mode": mode,
            "model_key": model_key,
            "endpoint": endpoint,
            **prompt_summary,
            "prompt_input": (prompt or "").strip(),
            "negative_prompt_input": (negative_prompt or "").strip(),
            "effective_prompt": effective_prompt,
            "aspect_ratio": aspect_ratio_value,
            "aspect_ratio_label": aspect_ratio,
            "seed": normalized_seed,
            "enable_safety_checker": bool(enable_safety_checker),
        }

        payload: dict[str, Any] = {
            "prompt": effective_prompt,
            "aspect_ratio": aspect_ratio_value,
            "enable_safety_checker": bool(enable_safety_checker),
        }
        if normalized_seed is not None:
            payload["seed"] = normalized_seed
        cleaned_webhook = (webhook_url or "").strip()
        if cleaned_webhook:
            payload["webhook_url"] = cleaned_webhook

        deferred_payload_attempts_factory = None
        if is_edit:
            reference_images, connected_reference_inputs = _collect_reference_images(
                reference_image_1,
                reference_image_2,
                reference_image_3,
                reference_image_4,
                reference_image_5,
            )
            if not reference_images:
                raise ValueError("Seedream v4.5 Edit requires at least one `reference_image_*` input.")

            request_summary["reference_images_count"] = len(reference_images)
            request_summary["connected_reference_inputs"] = connected_reference_inputs
            request_summary["reference_image_mime_types"] = [item["mime_type"] for item in reference_images]
            request_summary["reference_image_sizes_bytes"] = [item["bytes"] for item in reference_images]
            payload_attempts, deferred_payload_attempts_factory = _seedream_edit_attempts(payload, reference_images)
            default_prefix = "seedream45_edit"
        else:
            payload_attempts = _seedream_create_attempts(payload)
            default_prefix = "seedream45"

        admin_sync_settings = _resolve_admin_sync_settings()
        request_summary["admin_sync_enabled"] = bool(admin_sync_settings.get("enabled"))

        return _run_task(
            api_key=api_key,
            endpoint=endpoint,
            model_key=model_key,
            payload_attempts=payload_attempts,
            deferred_payload_attempts_factory=deferred_payload_attempts_factory,
            request_summary=request_summary,
            filename_prefix=_clean_filename_prefix(filename_prefix, default_prefix),
            timeout_seconds=int(timeout_seconds),
            poll_interval_seconds=float(poll_interval_seconds),
            admin_sync_settings=admin_sync_settings,
        )


NODE_CLASS_MAPPINGS = {
    "DarkHubFreepikStudio": DarkHubFreepikStudio,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DarkHubFreepikStudio": "darkHUB Seedream 4.5",
}
