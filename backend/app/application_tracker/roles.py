"""Narrow identity aliases for application-list preference badges."""

import re
from urllib.parse import urlsplit, urlunsplit


def role_key(role: str) -> str:
    # Remove only an explicit terminal preference badge, never job levels,
    # departments, cohort years or internal words in the actual title.
    cleaned = re.sub(r"\s*(?:第\s*[0-9一二三四五六七八九十]+\s*志愿|志愿\s*[0-9一二三四五六七八九十]+)\s*$", "", role).strip()
    return cleaned or role.strip()


def select_role(items, role):
    exact = [item for item in items if item.role == role]
    if exact:
        return exact[0] if len(exact) == 1 else None
    matches = [item for item in items if role_key(item.role) == role_key(role)]
    return matches[0] if len(matches) == 1 else None


def application_page_url(url):
    parts = urlsplit(url)
    fragment = "&".join(part for part in parts.fragment.split("&") if not part.startswith("jobscout-role="))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, fragment))
