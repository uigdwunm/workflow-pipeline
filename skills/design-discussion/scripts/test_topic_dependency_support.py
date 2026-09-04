"""Shared fixture adapter for focused Ticket-07 CLI test modules."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))


class TopicDependencyScenarioTest(unittest.TestCase):
    """Run one legacy scenario body without inheriting its discovered tests."""

    def setUp(self) -> None:
        fixture_class = importlib.import_module("test_discussion_protocol").DiscussionProtocolEvolutionTests
        self.fixture = fixture_class("runTest")
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def run_scenario(self, name: str) -> None:
        getattr(self.fixture, f"_scenario_{name}")()
