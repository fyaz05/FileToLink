from Thunder.vars import Var

from .client import Database

db = Database(Var.DATABASE_URL, Var.NAME)

__all__ = ["db", "Database"]
