# Thunder/server/exceptions.py

class InvalidHash(Exception):
    """Exception raised for an invalid hash."""
    message = "Invalid hash"


class FileNotFound(Exception):
    """Exception raised when a file is not found."""
    message = "File not found"
