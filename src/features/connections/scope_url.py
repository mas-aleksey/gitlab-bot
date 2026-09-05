"""URL parser that splits a GitLab web URL into ``base_url`` + ``scope_path``.

Accepts anything the user is likely to paste from the address bar:

    https://gitlab.com/mygroup
    https://gitlab.com/mygroup/subgroup
    https://gitlab.com/mygroup/subgroup/project
    https://gitlab.example.com/mygroup/subgroup/project
    https://gitlab.example.com/mygroup/subgroup/project.git
    https://gitlab.com/mygroup/project/-/merge_requests
    https://gitlab.com/mygroup/project/-/tree/main

Everything after ``/-/`` is a GitLab UI section and belongs to the
*project*, not the scope path — so we truncate there. A trailing ``.git``
is stripped because it's a common copy-paste artefact from the "Clone"
widget.
"""

from urllib.parse import urlparse


class ScopeUrlParseError(ValueError):
    """User-facing message is the exception's ``args[0]``."""


def parse_scope_url(raw: str) -> tuple[str, str]:
    """Return ``(base_url, scope_path)`` or raise :class:`ScopeUrlParseError`.

    ``base_url`` is ``scheme://host[:port]`` (no trailing slash).
    ``scope_path`` is the group- or project-path with no leading/trailing
    slash and no GitLab UI suffix.
    """
    text = raw.strip()
    if not text:
        raise ScopeUrlParseError("Пустая ссылка.")

    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise ScopeUrlParseError("Ссылка должна начинаться с http:// или https://.")
    if not parsed.netloc:
        raise ScopeUrlParseError("В ссылке нет хоста.")

    base_url = f"{parsed.scheme}://{parsed.netloc}"

    path = parsed.path
    marker = path.find("/-/")
    if marker != -1:
        path = path[:marker]

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]

    if not path:
        raise ScopeUrlParseError(
            "В ссылке не указана группа или проект — после хоста должен быть путь."
        )

    return base_url, path
