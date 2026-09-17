"""
Screenshot handling and the Apps Script webhook client.

Screenshot modes (SCREENSHOT_MODE):
  apps_script  (default) compress to JPEG and send base64 inside the webhook payload; the
               Apps Script saves it to Drive and stores the viewer link. No extra credentials.
  drive_api    upload directly with a service account (GDRIVE_SA_JSON + GDRIVE_FOLDER_ID) and
               send only the link. Use when payloads must stay small or Drive lives elsewhere.
  none         send no screenshot.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from typing import Dict, List, Optional

import requests
from PIL import Image

log = logging.getLogger("kkm.uploader")

from scraper import env_str  # shared .env tolerance


# ---------------------------------------------------------------------------
# image compression
# ---------------------------------------------------------------------------
def compress_screenshot(path: str, max_width: int = 1200, quality: int = 80) -> bytes:
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > max_width:
            ratio = max_width / im.width
            im = im.resize((max_width, int(im.height * ratio)), Image.LANCZOS)
        # cap the height so a very long page does not produce a 10 MB JPEG
        if im.height > 2400:
            im = im.crop((0, 0, im.width, 2400))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Google Drive via service account (optional)
# ---------------------------------------------------------------------------
class DriveUploader:
    def __init__(self):
        raw = env_str("GDRIVE_SA_JSON")
        self.folder_id = env_str("GDRIVE_FOLDER_ID")
        if not raw or not self.folder_id:
            raise RuntimeError("GDRIVE_SA_JSON and GDRIVE_FOLDER_ID are required for SCREENSHOT_MODE=drive_api")
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            info = json.loads(base64.b64decode(raw))
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"])
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    def upload(self, data: bytes, filename: str, mime: str = "image/jpeg") -> str:
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        meta = {"name": filename, "parents": [self.folder_id]}
        f = self.svc.files().create(body=meta, media_body=media, fields="id", supportsAllDrives=True).execute()
        fid = f["id"]
        try:
            self.svc.permissions().create(fileId=fid, body={"role": "reader", "type": "anyone"},
                                          supportsAllDrives=True).execute()
        except Exception as e:
            log.warning("could not set anyone-with-link on %s: %s", fid, e)
        return f"https://drive.google.com/file/d/{fid}/view"


# ---------------------------------------------------------------------------
# Apps Script webhook client
# ---------------------------------------------------------------------------
def _diagnose(r) -> str:
    """
    Apps Script answers a misconfigured deployment with a web page, not JSON, so the failure
    used to surface as `Expecting value: line 1 column 1 (char 0)` - true, and useless. The
    page it serves says which of the handful of causes it is, so read it and say so.
    """
    body = (r.text or "")[:4000]
    low = body.lower()
    final = (r.url or "").lower()
    if "accounts.google.com" in final or "signin/v2" in low or "sign in to continue" in low:
        return ("the deployment is asking for a Google sign-in, so its access is not set to "
                "Anyone. Deploy > Manage deployments > edit > Who has access: Anyone")
    if "unable to open the file" in low or "page not found" in low or "no item with the given id" in low:
        return ("the /exec URL no longer resolves to a deployment. Deploy > Manage deployments "
                "and copy the current Web app URL into APPS_SCRIPT_WEBHOOK_URL")
    if "script function not found" in low:
        return ("the deployment has no doGet/doPost, so it was not deployed as a Web app. "
                "Deploy > New deployment > type Web app")
    if "authorization is required" in low or "authorisation is required" in low:
        return ("the script has not been authorised since its scopes changed. Open the script "
                "and run any function once to re-accept the permissions")
    if "exceeded maximum execution time" in low:
        return "the script hit the Apps Script execution time limit"
    if "<html" in low or "<!doctype" in low:
        return ("Apps Script served a web page instead of JSON, which almost always means the "
                "deployment is stale: Deploy > Manage deployments > New version")
    return "the response was not JSON"


def _check_url_shape(url: str) -> None:
    """A wrong URL shape is the single most common cause and costs a whole run to discover."""
    if "/macros/s/" not in url or not url.rstrip("/").endswith("/exec"):
        log.warning("APPS_SCRIPT_WEBHOOK_URL does not look like a deployed Web app URL "
                    "(expected https://script.google.com/macros/s/<id>/exec): %s", url)


class AppsScriptClient:
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, timeout: int = 120):
        self.url = url or env_str("APPS_SCRIPT_WEBHOOK_URL")
        self.token = token or env_str("APPS_SCRIPT_API_TOKEN")
        self.timeout = timeout
        if not self.url or not self.token:
            raise RuntimeError("APPS_SCRIPT_WEBHOOK_URL and APPS_SCRIPT_API_TOKEN are required")
        _check_url_shape(self.url)
        self.session = requests.Session()

    def _post(self, payload: dict, retries: int = 4) -> dict:
        payload = dict(payload, token=self.token)
        delay = 2
        last_err: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                # Apps Script answers with a 302 to a googleusercontent URL; requests follows it.
                r = self.session.post(self.url, json=payload, timeout=self.timeout, allow_redirects=True)
                if r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code} from Apps Script")
                try:
                    data = r.json()
                except ValueError:
                    raise requests.HTTPError(
                        f"non-JSON response ({r.status_code}): {_diagnose(r)}. "
                        f"First bytes: {(r.text or '')[:160]!r}")
                if not data.get("ok"):
                    raise RuntimeError(data.get("error", "unknown error"))
                return data
            except (requests.RequestException, RuntimeError) as e:
                last_err = e
                log.warning("webhook attempt %d/%d failed: %s", attempt, retries, e)
                if attempt < retries:
                    time.sleep(delay)
                    delay *= 2
        raise RuntimeError(f"webhook failed after {retries} attempts: {last_err}")

    def ping(self) -> dict:
        r = self.session.get(self.url, params={"action": "ping"}, timeout=self.timeout)
        try:
            return r.json()
        except ValueError:
            # This is the first call a live run makes, so its message is the one Wan reads when
            # the whole run dies thirteen seconds in. Make it name the fix.
            raise RuntimeError(f"webhook did not return JSON ({r.status_code}): {_diagnose(r)}. "
                               f"First bytes: {(r.text or '')[:160]!r}")

    def targets(self) -> List[Dict]:
        """Brands + handles from the sheet's Targets tab (action=targets)."""
        data = self._post({"action": "targets"})
        return data.get("targets", [])

    def known_urls(self) -> List[str]:
        data = self._post({"action": "known_urls"})
        return data.get("urls", [])

    def insert(self, records: List[Dict], batch_size: int = 10) -> dict:
        """Insert in small batches so one oversized payload cannot sink the whole run."""
        totals = {"inserted": 0, "duplicates": [], "errors": [], "ids": []}
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            data = self._post({"action": "insert", "records": batch})
            totals["inserted"] += data.get("inserted", 0)
            totals["duplicates"] += data.get("duplicates", [])
            totals["errors"] += data.get("errors", [])
            totals["ids"] += data.get("ids", [])
            log.info("batch %d: inserted=%s dup=%s err=%s", i // batch_size + 1,
                     data.get("inserted"), len(data.get("duplicates", [])), len(data.get("errors", [])))
        return totals

    def update_status(self, record_id: str, status: str, remarks: str = "") -> dict:
        return self._post({"action": "update_status", "id": record_id, "status": status, "remarks": remarks})


# ---------------------------------------------------------------------------
# glue
# ---------------------------------------------------------------------------
def attach_screenshot(record: dict, screenshot_path: Optional[str], run_cfg: dict,
                      drive: Optional[DriveUploader], filename: str) -> None:
    """Mutates `record` in place with either screenshot_base64 or screenshot_link."""
    mode = env_str("SCREENSHOT_MODE", "apps_script").lower()
    if mode == "none" or not screenshot_path or not os.path.exists(screenshot_path):
        return
    try:
        data = compress_screenshot(screenshot_path,
                                   int(run_cfg.get("screenshot_max_width", 1200)),
                                   int(run_cfg.get("screenshot_jpeg_quality", 80)))
    except Exception as e:
        log.warning("compress failed for %s: %s", screenshot_path, e)
        return
    if mode == "drive_api" and drive:
        try:
            record["screenshot_link"] = drive.upload(data, filename)
            return
        except Exception as e:
            log.warning("Drive upload failed (%s); falling back to payload upload", e)
    record["screenshot_base64"] = base64.b64encode(data).decode("ascii")
    record["screenshot_mime"] = "image/jpeg"
