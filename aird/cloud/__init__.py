"""Cloud storage provider integrations for Aird."""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import BinaryIO, Iterator, List, Optional

from urllib.parse import quote

import requests

__all__ = [
    "CloudManager",
    "CloudProvider",
    "CloudProviderError",
    "CloudFile",
    "CloudDownload",
    "GoogleDriveProvider",
    "OneDriveProvider",
    "encode_identifier",
    "decode_identifier",
]


class CloudProviderError(Exception):
    """Raised when a cloud provider encounters an operational error."""


@dataclass(slots=True)
class CloudFile:
    """Representation of a file or folder within a cloud provider."""

    id: str
    name: str
    is_dir: bool
    size: Optional[int] = None
    modified: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "is_dir": self.is_dir,
            "size": self.size,
            "modified": self.modified,
        }


class CloudDownload:
    """Wrapper for streaming downloads from cloud providers."""

    def __init__(
        self,
        name: str,
        response: requests.Response,
        *,
        content_type: Optional[str] = None,
        content_length: Optional[int] = None,
    ) -> None:
        self.name = name
        self._response = response
        self.content_type = content_type or response.headers.get("Content-Type")
        length = content_length or response.headers.get("Content-Length")
        try:
            self.content_length = int(length) if length is not None else None
        except (TypeError, ValueError):
            self.content_length = None

    def iter_chunks(self, chunk_size: int = 65536) -> Iterator[bytes]:
        for chunk in self._response.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk

    def close(self) -> None:
        self._response.close()


class CloudProvider:
    """Base class for cloud storage providers."""

    name: str = ""
    label: str = ""

    def metadata(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
        }

    @property
    def root_identifier(self) -> str:
        return "root"

    def list_files(self, folder_id: str | None = None) -> List[CloudFile]:  # pragma: no cover - interface
        raise NotImplementedError

    def download_file(self, file_id: str) -> CloudDownload:  # pragma: no cover - interface
        raise NotImplementedError

    def upload_file(
        self,
        stream: BinaryIO,
        *,
        name: str,
        parent_id: Optional[str] = None,
        size: Optional[int] = None,
        content_type: Optional[str] = None,
    ) -> CloudFile:  # pragma: no cover - interface
        raise NotImplementedError


class CloudManager:
    """Registry for configured cloud providers."""

    def __init__(self) -> None:
        self._providers: dict[str, CloudProvider] = {}

    def reset(self) -> None:
        self._providers.clear()

    def register(self, provider: CloudProvider) -> None:
        if not provider or not provider.name:
            raise ValueError("Provider must define a name")
        self._providers[provider.name] = provider
        logging.getLogger(__name__).info("Enabled cloud provider: %s", provider.name)

    def get(self, name: str) -> Optional[CloudProvider]:
        return self._providers.get(name)

    def list_providers(self) -> List[CloudProvider]:
        return list(self._providers.values())

    def has_providers(self) -> bool:
        return bool(self._providers)


class GoogleDriveProvider(CloudProvider):
    name = "gdrive"
    label = "Google Drive"

    def __init__(self, access_token: str, *, root_id: str = "root", include_shared_drives: bool = True) -> None:
        if not access_token:
            raise CloudProviderError("Google Drive access token is required")
        self._token = access_token
        self._root_id = root_id or "root"
        self._include_shared_drives = include_shared_drives
        self._base_url = "https://www.googleapis.com/drive/v3"

    @property
    def root_identifier(self) -> str:
        return self._root_id

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def list_files(self, folder_id: str | None = None) -> List[CloudFile]:
        folder = folder_id or self.root_identifier
        params = {
            "q": f"'{folder}' in parents and trashed=false",
            "fields": "files(id,name,mimeType,modifiedTime,size)",
            "pageSize": 1000,
        }
        if self._include_shared_drives:
            params.update({
                "corpora": "allDrives",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            })
        try:
            response = requests.get(
                f"{self._base_url}/files",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(f"Google Drive request failed: {exc}") from exc
        if response.status_code != 200:
            raise CloudProviderError(
                f"Google Drive list failed ({response.status_code}): {response.text[:200]}"
            )
        payload = response.json()
        files = []
        for item in payload.get("files", []):
            mime = item.get("mimeType", "")
            is_dir = mime == "application/vnd.google-apps.folder"
            size = None
            try:
                size = int(item.get("size")) if item.get("size") is not None else None
            except (TypeError, ValueError):
                size = None
            files.append(
                CloudFile(
                    id=item.get("id", ""),
                    name=item.get("name", "Unnamed"),
                    is_dir=is_dir,
                    size=size,
                    modified=item.get("modifiedTime"),
                )
            )
        return files

    def download_file(self, file_id: str) -> CloudDownload:
        try:
            meta_resp = requests.get(
                f"{self._base_url}/files/{file_id}",
                headers=self._headers(),
                params={"fields": "id,name,mimeType,size"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(f"Google Drive request failed: {exc}") from exc
        if meta_resp.status_code != 200:
            raise CloudProviderError(
                f"Google Drive metadata fetch failed ({meta_resp.status_code})"
            )
        metadata = meta_resp.json()
        mime = metadata.get("mimeType", "")
        if mime == "application/vnd.google-apps.folder":
            raise CloudProviderError("Folders cannot be downloaded from Google Drive")
        if mime.startswith("application/vnd.google-apps."):
            raise CloudProviderError("Google Docs formats are not supported for direct download")

        try:
            download_resp = requests.get(
                f"{self._base_url}/files/{file_id}",
                headers=self._headers(),
                params={"alt": "media"},
                stream=True,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(f"Google Drive request failed: {exc}") from exc
        if download_resp.status_code not in (200, 206):
            raise CloudProviderError(
                f"Google Drive download failed ({download_resp.status_code})"
            )
        size = None
        try:
            size = int(metadata.get("size")) if metadata.get("size") is not None else None
        except (TypeError, ValueError):
            size = None
        return CloudDownload(
            metadata.get("name", f"gdrive-{file_id}"),
            download_resp,
            content_type=mime or download_resp.headers.get("Content-Type"),
            content_length=size,
        )

    def upload_file(
        self,
        stream: BinaryIO,
        *,
        name: str,
        parent_id: Optional[str] = None,
        size: Optional[int] = None,
        content_type: Optional[str] = None,
    ) -> CloudFile:
        if not name:
            raise CloudProviderError("A file name is required for Google Drive uploads")

        if size is None:
            try:
                current = stream.tell()
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(current)
            except Exception:
                raise CloudProviderError("Unable to determine upload size for Google Drive")

        if size < 0:
            raise CloudProviderError("Invalid upload size for Google Drive")

        stream.seek(0)
        metadata: dict[str, object] = {"name": name}
        if parent_id:
            metadata["parents"] = [parent_id]

        headers = self._headers()
        mime_type = content_type or "application/octet-stream"

        # Small files (<=5MB) can use multipart upload for simplicity
        simple_limit = 5 * 1024 * 1024
        if size <= simple_limit:
            try:
                stream.seek(0)
                files = {
                    "metadata": (
                        "metadata",
                        json.dumps(metadata),
                        "application/json; charset=UTF-8",
                    ),
                    "file": (
                        name,
                        stream.read(),
                        mime_type,
                    ),
                }
                response = requests.post(
                    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                    headers=headers,
                    files=files,
                    timeout=120,
                )
            except requests.RequestException as exc:
                raise CloudProviderError(f"Google Drive upload failed: {exc}") from exc

            if response.status_code not in (200, 201):
                raise CloudProviderError(
                    f"Google Drive upload failed ({response.status_code}): {response.text[:200]}"
                )

            payload = response.json()
            return CloudFile(
                id=payload.get("id", ""),
                name=payload.get("name", name),
                is_dir=False,
                size=_safe_int(payload.get("size")),
                modified=payload.get("modifiedTime"),
            )

        # Resumable upload for larger files
        init_headers = headers.copy()
        init_headers.update(
            {
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(size),
            }
        )

        try:
            init_resp = requests.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
                headers=init_headers,
                json=metadata,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(f"Google Drive upload init failed: {exc}") from exc

        if init_resp.status_code not in (200, 201):
            raise CloudProviderError(
                f"Google Drive upload init failed ({init_resp.status_code}): {init_resp.text[:200]}"
            )

        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            raise CloudProviderError("Google Drive did not provide an upload URL")

        chunk_size = 256 * 1024  # 256 KiB chunks
        offset = 0

        while offset < size:
            stream.seek(offset)
            chunk = stream.read(min(chunk_size, size - offset))
            if not chunk:
                break

            end = offset + len(chunk) - 1
            chunk_headers = {
                "Content-Length": str(len(chunk)),
                "Content-Type": mime_type,
                "Content-Range": f"bytes {offset}-{end}/{size}",
            }

            try:
                upload_resp = requests.put(
                    upload_url,
                    headers=chunk_headers,
                    data=chunk,
                    timeout=120,
                )
            except requests.RequestException as exc:
                raise CloudProviderError(f"Google Drive chunk upload failed: {exc}") from exc

            if upload_resp.status_code in (200, 201):
                payload = upload_resp.json()
                return CloudFile(
                    id=payload.get("id", ""),
                    name=payload.get("name", name),
                    is_dir=False,
                    size=_safe_int(payload.get("size")),
                    modified=payload.get("modifiedTime"),
                )
            if upload_resp.status_code == 308:
                offset = end + 1
                continue

            raise CloudProviderError(
                f"Google Drive upload failed ({upload_resp.status_code}): {upload_resp.text[:200]}"
            )

        raise CloudProviderError("Google Drive upload did not complete successfully")


class OneDriveProvider(CloudProvider):
    name = "onedrive"
    label = "OneDrive"

    def __init__(self, access_token: str, *, drive_id: Optional[str] = None) -> None:
        if not access_token:
            raise CloudProviderError("OneDrive access token is required")
        self._token = access_token
        if drive_id:
            self._base_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
        else:
            self._base_url = "https://graph.microsoft.com/v1.0/me/drive"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def list_files(self, folder_id: str | None = None) -> List[CloudFile]:
        if not folder_id or folder_id == "root":
            url = f"{self._base_url}/root/children"
        else:
            url = f"{self._base_url}/items/{folder_id}/children"
        try:
            response = requests.get(
                url,
                headers=self._headers(),
                params={"$top": 200},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(f"OneDrive request failed: {exc}") from exc
        if response.status_code != 200:
            raise CloudProviderError(
                f"OneDrive list failed ({response.status_code}): {response.text[:200]}"
            )
        payload = response.json()
        files = []
        for item in payload.get("value", []):
            is_dir = "folder" in item
            size = item.get("size")
            try:
                size = int(size) if size is not None else None
            except (TypeError, ValueError):
                size = None
            files.append(
                CloudFile(
                    id=item.get("id", ""),
                    name=item.get("name", "Unnamed"),
                    is_dir=is_dir,
                    size=size,
                    modified=item.get("lastModifiedDateTime"),
                )
            )
        return files

    def download_file(self, file_id: str) -> CloudDownload:
        try:
            meta_resp = requests.get(
                f"{self._base_url}/items/{file_id}",
                headers=self._headers(),
                params={"$select": "name,size,@microsoft.graph.downloadUrl,file"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(f"OneDrive request failed: {exc}") from exc
        if meta_resp.status_code != 200:
            raise CloudProviderError(
                f"OneDrive metadata fetch failed ({meta_resp.status_code})"
            )
        metadata = meta_resp.json()
        if "folder" in metadata:
            raise CloudProviderError("Folders cannot be downloaded from OneDrive")
        download_url = metadata.get("@microsoft.graph.downloadUrl")
        if not download_url:
            raise CloudProviderError("Download URL not available for this OneDrive item")
        try:
            response = requests.get(download_url, stream=True, timeout=60)
        except requests.RequestException as exc:
            raise CloudProviderError(f"OneDrive request failed: {exc}") from exc
        if response.status_code not in (200, 206):
            raise CloudProviderError(
                f"OneDrive download failed ({response.status_code})"
            )
        size = metadata.get("size")
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        return CloudDownload(
            metadata.get("name", f"onedrive-{file_id}"),
            response,
            content_type=response.headers.get("Content-Type"),
            content_length=size,
        )

    def upload_file(
        self,
        stream: BinaryIO,
        *,
        name: str,
        parent_id: Optional[str] = None,
        size: Optional[int] = None,
        content_type: Optional[str] = None,
    ) -> CloudFile:
        if not name:
            raise CloudProviderError("A file name is required for OneDrive uploads")

        if size is None:
            try:
                current = stream.tell()
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(current)
            except Exception:
                raise CloudProviderError("Unable to determine upload size for OneDrive")

        if size < 0:
            raise CloudProviderError("Invalid upload size for OneDrive")

        stream.seek(0)
        mime_type = content_type or "application/octet-stream"

        # Simple uploads are limited to 4 MiB
        simple_limit = 4 * 1024 * 1024
        if size <= simple_limit:
            safe_name = quote(name, safe="")
            target_url = (
                f"{self._base_url}/root:/{safe_name}:/content"
                if not parent_id or parent_id == "root"
                else f"{self._base_url}/items/{parent_id}:/{safe_name}:/content"
            )

            headers = self._headers().copy()
            headers["Content-Type"] = mime_type

            try:
                stream.seek(0)
                response = requests.put(
                    target_url,
                    headers=headers,
                    data=stream.read(),
                    timeout=120,
                )
            except requests.RequestException as exc:
                raise CloudProviderError(f"OneDrive upload failed: {exc}") from exc

            if response.status_code not in (200, 201):
                raise CloudProviderError(
                    f"OneDrive upload failed ({response.status_code}): {response.text[:200]}"
                )

            payload = response.json()
            return CloudFile(
                id=payload.get("id", ""),
                name=payload.get("name", name),
                is_dir="folder" in payload,
                size=_safe_int(payload.get("size")),
                modified=payload.get("lastModifiedDateTime"),
            )

        # Larger uploads require an upload session
        safe_name = quote(name, safe="")
        if not parent_id or parent_id == "root":
            session_url = f"{self._base_url}/root:/{safe_name}:/createUploadSession"
        else:
            session_url = f"{self._base_url}/items/{parent_id}:/{safe_name}:/createUploadSession"

        try:
            session_resp = requests.post(
                session_url,
                headers=self._headers(),
                json={"item": {"@microsoft.graph.conflictBehavior": "rename"}},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(f"OneDrive upload session failed: {exc}") from exc

        if session_resp.status_code not in (200, 201):
            raise CloudProviderError(
                f"OneDrive upload session failed ({session_resp.status_code}): {session_resp.text[:200]}"
            )

        upload_url = session_resp.json().get("uploadUrl")
        if not upload_url:
            raise CloudProviderError("OneDrive did not provide an upload URL")

        chunk_size = 327680 * 10  # 3.125 MiB
        offset = 0

        while offset < size:
            stream.seek(offset)
            chunk = stream.read(min(chunk_size, size - offset))
            if not chunk:
                break

            end = offset + len(chunk) - 1
            chunk_headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{end}/{size}",
                "Content-Type": mime_type,
            }

            try:
                upload_resp = requests.put(
                    upload_url,
                    headers=chunk_headers,
                    data=chunk,
                    timeout=120,
                )
            except requests.RequestException as exc:
                raise CloudProviderError(f"OneDrive chunk upload failed: {exc}") from exc

            if upload_resp.status_code in (200, 201):
                payload = upload_resp.json()
                return CloudFile(
                    id=payload.get("id", ""),
                    name=payload.get("name", name),
                    is_dir="folder" in payload,
                    size=_safe_int(payload.get("size")),
                    modified=payload.get("lastModifiedDateTime"),
                )

            if upload_resp.status_code in (202, 204):
                offset = end + 1
                continue

            raise CloudProviderError(
                f"OneDrive upload failed ({upload_resp.status_code}): {upload_resp.text[:200]}"
            )

        raise CloudProviderError("OneDrive upload did not complete successfully")


def encode_identifier(identifier: str) -> str:
    """Encode identifiers for safe transport in URLs."""
    raw = identifier.encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    return encoded.rstrip("=")


def decode_identifier(encoded: str) -> str:
    """Decode identifiers encoded with :func:`encode_identifier`."""
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    data = base64.urlsafe_b64decode(encoded + padding)
    return data.decode("utf-8")


def _safe_int(value: object) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
