"""Small text boundary helpers shared by local material and sync writers."""


def strip_nul(value):
    """Remove invisible database-invalid NUL characters from JSON-like data."""
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, dict):
        return {key: strip_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_nul(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_nul(item) for item in value)
    return value
