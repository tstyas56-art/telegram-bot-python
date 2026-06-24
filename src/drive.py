#!/usr/bin/env python3
"""
Google Drive operations
"""

import os
import json
import logging
import io
from typing import Optional, Dict, List
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)


class GoogleDriveManager:
    def __init__(self):
        self.service = None
        self.credentials = None

    def set_credentials(self, credentials: Credentials):
        """Set user credentials"""
        self.credentials = credentials
        try:
            self.service = build('drive', 'v3', credentials=credentials)
        except Exception as e:
            logger.error(f"Failed to create Drive service: {e}")
            self.service = None

    def load_credentials_from_file(self, user_id: str) -> bool:
        """Load credentials from file"""
        token_file = f"token_{user_id}.json"
        if not os.path.exists(token_file):
            return False

        try:
            with open(token_file, 'r') as f:
                creds_data = json.load(f)

            credentials = Credentials.from_authorized_user_info(creds_data)

            # Refresh if needed
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                with open(token_file, 'w') as f:
                    f.write(credentials.to_json())

            self.set_credentials(credentials)
            return True
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return False

    def get_storage_info(self) -> Optional[Dict]:
        """Get storage information"""
        if not self.service:
            return None

        try:
            about = self.service.about().get(fields='storageQuota').execute()
            return about.get('storageQuota', {})
        except HttpError as e:
            logger.error(f"Failed to get storage info: {e}")
            return None

    def list_files(self, limit: int = 10) -> List[Dict]:
        """List recent files"""
        if not self.service:
            return []

        try:
            results = self.service.files().list(
                pageSize=limit,
                fields="nextPageToken, files(id, name, size, createdTime, mimeType)"
            ).execute()
            return results.get('files', [])
        except HttpError as e:
            logger.error(f"Failed to list files: {e}")
            return []

    def upload_file(self, file_path: str, file_name: str, chunked: bool = False) -> Optional[str]:
        """Upload file to Google Drive"""
        if not self.service:
            return None

        try:
            file_metadata = {
                'name': file_name,
                'parents': ['root']
            }

            if chunked:
                media = MediaFileUpload(
                    file_path,
                    resumable=True,
                    chunksize=1024*1024  # 1MB chunks
                )
            else:
                media = MediaFileUpload(file_path)

            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()

            return file.get('id')
        except HttpError as e:
            logger.error(f"Failed to upload file: {e}")
            return None

    def upload_file_chunked(self, file_path: str, file_name: str, progress_callback=None) -> Optional[str]:
        """Upload large file with progress tracking"""
        if not self.service:
            return None

        try:
            file_metadata = {
                'name': file_name,
                'parents': ['root']
            }

            media = MediaFileUpload(
                file_path,
                resumable=True,
                chunksize=1024*1024  # 1MB chunks
            )

            request = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status and progress_callback:
                    progress = int(status.progress() * 100)
                    progress_callback(progress)

            if response:
                return response.get('id')
        except HttpError as e:
            logger.error(f"Failed to upload file chunked: {e}")
            return None

        return None

    def rename_file(self, file_id: str, new_name: str) -> bool:
        """Rename a file"""
        if not self.service:
            return False

        try:
            file_metadata = {'name': new_name}
            self.service.files().update(fileId=file_id, body=file_metadata).execute()
            return True
        except HttpError as e:
            logger.error(f"Failed to rename file: {e}")
            return False

    def delete_file(self, file_id: str) -> bool:
        """Delete a file"""
        if not self.service:
            return False

        try:
            self.service.files().delete(fileId=file_id).execute()
            return True
        except HttpError as e:
            logger.error(f"Failed to delete file: {e}")
            return False

    def get_file_info(self, file_id: str) -> Optional[Dict]:
        """Get file information"""
        if not self.service:
            return None

        try:
            file = self.service.files().get(
                fileId=file_id,
                fields='id, name, size, createdTime, mimeType'
            ).execute()
            return file
        except HttpError as e:
            logger.error(f"Failed to get file info: {e}")
            return None

    def upsert_file(self, file_path: str, file_name: str, file_id: Optional[str] = None) -> Optional[str]:
        """Create or update a Drive file and return its id."""
        if not self.service:
            return None
        try:
            media = MediaFileUpload(file_path, resumable=True)
            if file_id:
                updated = self.service.files().update(
                    fileId=file_id, media_body=media, fields='id'
                ).execute()
                return updated.get('id')
            return self.upload_file(file_path, file_name, chunked=True)
        except HttpError as e:
            logger.error(f"Failed to upsert file: {e}")
            return None

    def find_file_by_name(self, file_name: str) -> Optional[str]:
        """Find the newest non-trashed Drive file by exact name."""
        if not self.service:
            return None
        try:
            safe_name = file_name.replace("'", "\\'")
            results = self.service.files().list(
                q=f"name='{safe_name}' and trashed=false",
                orderBy='modifiedTime desc',
                pageSize=1,
                fields='files(id, name)'
            ).execute()
            files = results.get('files', [])
            return files[0]['id'] if files else None
        except HttpError as e:
            logger.error(f"Failed to find file by name: {e}")
            return None

    def download_file(self, file_id: str, destination_path: str) -> bool:
        """Download a Drive file to a local path."""
        if not self.service:
            return False
        try:
            os.makedirs(os.path.dirname(destination_path) or '.', exist_ok=True)
            request = self.service.files().get_media(fileId=file_id)
            with open(destination_path, 'wb') as handle:
                downloader = MediaIoBaseDownload(handle, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return True
        except HttpError as e:
            logger.error(f"Failed to download file: {e}")
            return False
