"""Shared fixture adapter for focused Ticket-07 CLI test modules."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))


class TopicDependencyScenarioTest(unittest.TestCase):
    """Shared isolated legacy fixture for focused CLI test bodies."""

    def setUp(self) -> None:
        from test_discussion_protocol import DiscussionProtocolEvolutionTests
        fixture_class = DiscussionProtocolEvolutionTests
        self.fixture = fixture_class("runTest")
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()
