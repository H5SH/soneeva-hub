from app.services.db.base import Database
from app.services.db.firestore import FirestoreDatabase as DB

__all__ = ["DB", "Database"]
