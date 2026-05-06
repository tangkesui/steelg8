from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402
from kernel import request as http_request  # noqa: E402
from kernel import routing as http_routing  # noqa: E402


class RouteTableTests(unittest.TestCase):
    def test_health_route_is_public(self):
        match = http_routing.resolve("GET", "/health", server.ROUTES)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.handler_name, "_get_health")
        self.assertFalse(match.auth_required)

    def test_query_string_is_removed_before_route_resolution(self):
        path = http_request.path_only("/logs?limit=20&days=2")
        match = http_routing.resolve("GET", path, server.ROUTES)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.handler_name, "_get_logs")

    def test_specific_dynamic_route_wins_before_generic_detail_route(self):
        match = http_routing.resolve("GET", "/conversations/42/messages", server.ROUTES)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.handler_name, "_get_conversation_messages")
        self.assertEqual(match.params["conversation_id"], "42")

    def test_path_parameter_decodes_template_paths(self):
        match = http_routing.resolve("DELETE", "/templates/%2Ftmp%2Fdemo.docx", server.ROUTES)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.handler_name, "_delete_template")
        self.assertEqual(match.params["path"], "/tmp/demo.docx")

    def test_unknown_route_returns_none(self):
        self.assertIsNone(http_routing.resolve("GET", "/missing", server.ROUTES))

    def test_all_route_handlers_exist(self):
        for route in server.ROUTES:
            self.assertTrue(
                hasattr(server.SteelG8Handler, route.handler_name),
                f"{route.method} {route.pattern} -> {route.handler_name}",
            )

    def test_path_parameter_must_be_final_segment(self):
        with self.assertRaises(ValueError):
            http_routing.Route("GET", "/files/{path:path}/tail", "_bad")


if __name__ == "__main__":
    unittest.main()
