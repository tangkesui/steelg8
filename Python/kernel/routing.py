from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Pattern
from urllib.parse import unquote


@dataclass(frozen=True)
class RouteMatch:
    handler_name: str
    params: dict[str, str]
    auth_required: bool


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    handler_name: str
    auth_required: bool = True
    _regex: Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_regex", re.compile(_compile_pattern(self.pattern)))

    def match(self, method: str, path: str) -> RouteMatch | None:
        if method.upper() != self.method.upper():
            return None
        matched = self._regex.match(path)
        if not matched:
            return None
        params = {key: unquote(value) for key, value in matched.groupdict().items()}
        return RouteMatch(
            handler_name=self.handler_name,
            params=params,
            auth_required=self.auth_required,
        )


def resolve(method: str, path: str, routes: tuple[Route, ...]) -> RouteMatch | None:
    for route in routes:
        match = route.match(method, path)
        if match is not None:
            return match
    return None


def _compile_pattern(pattern: str) -> str:
    if not pattern.startswith("/"):
        raise ValueError(f"route pattern must start with /: {pattern}")
    if pattern == "/":
        return r"^/$"

    parts: list[str] = []
    raw_segments = pattern.strip("/").split("/")
    for index, segment in enumerate(raw_segments):
        if segment.startswith("{") and segment.endswith("}"):
            name, _, kind = segment[1:-1].partition(":")
            if not name.isidentifier():
                raise ValueError(f"invalid route parameter: {segment}")
            if kind == "path":
                if index != len(raw_segments) - 1:
                    raise ValueError("path route parameter must be the final segment")
                parts.append(f"(?P<{name}>.+)")
            else:
                parts.append(f"(?P<{name}>[^/]+)")
            continue
        parts.append(re.escape(segment))
    return "^/" + "/".join(parts) + "$"
