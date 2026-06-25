"""MongoDB-backed JSON document store for persistent bot state."""

from __future__ import annotations

from typing import Any, Dict



class MongoStore:
    """Persist one logical JSON document in MongoDB with a JsonStore-like API."""

    def __init__(self, mongo_url: str, document_key: str, database_name: str = "telegram_hosting_bot"):
        from pymongo import MongoClient

        self.client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        self.collection = self.client[database_name]["bot_state"]
        self.document_key = document_key
        self.client.admin.command("ping")

    def read(self, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
        item = self.collection.find_one({"_id": self.document_key})
        if not item:
            return default or {}
        data = item.get("data", {})
        return data if isinstance(data, dict) else (default or {})

    def write(self, data: Dict[str, Any]) -> None:
        self.collection.update_one(
            {"_id": self.document_key},
            {"$set": {"data": data}},
            upsert=True,
        )
