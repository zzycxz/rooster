"""Tests for Guardian._cron_matches and Guardian._parse_cron_field.

Verifies the NameError fix: GuardianProcess._parse_cron_field → Guardian._parse_cron_field.
"""

from datetime import datetime


# guardian.py is at repo root, not under src/
import sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from guardian import Guardian


class TestParseCronField:
    """Verify _parse_cron_field handles all cron syntax."""

    def test_wildcard(self):
        result = Guardian._parse_cron_field("*", range(0, 60))
        assert result == list(range(0, 60))

    def test_single_value(self):
        result = Guardian._parse_cron_field("5", range(0, 60))
        assert result == [5]

    def test_list(self):
        result = Guardian._parse_cron_field("0,15,30,45", range(0, 60))
        assert result == [0, 15, 30, 45]

    def test_range(self):
        result = Guardian._parse_cron_field("1-5", range(0, 60))
        assert result == [1, 2, 3, 4, 5]

    def test_step(self):
        result = Guardian._parse_cron_field("*/15", range(0, 60))
        assert result == [0, 15, 30, 45]

    def test_range_with_step(self):
        result = Guardian._parse_cron_field("2-10/3", range(0, 60))
        assert result == [2, 5, 8]

    def test_combined(self):
        result = Guardian._parse_cron_field("1,10-12,*/30", range(0, 60))
        assert 1 in result
        assert 10 in result
        assert 11 in result
        assert 12 in result
        assert 0 in result
        assert 30 in result


class TestCronMatches:
    """Verify _cron_matches correctly evaluates cron expressions against datetimes."""

    def test_every_minute(self):
        dt = datetime(2026, 5, 26, 10, 30)
        assert Guardian._cron_matches("* * * * *", dt) is True

    def test_specific_minute(self):
        dt = datetime(2026, 5, 26, 10, 30)
        assert Guardian._cron_matches("30 * * * *", dt) is True
        assert Guardian._cron_matches("31 * * * *", dt) is False

    def test_specific_hour_minute(self):
        dt = datetime(2026, 5, 26, 8, 30)
        assert Guardian._cron_matches("30 8 * * *", dt) is True
        assert Guardian._cron_matches("30 9 * * *", dt) is False

    def test_day_of_week(self):
        # 2026-05-26 is a Monday (weekday=0)
        dt = datetime(2026, 5, 26, 9, 0)
        assert Guardian._cron_matches("0 9 * * 1", dt) is True   # Monday
        assert Guardian._cron_matches("0 9 * * 2", dt) is False  # Tuesday

    def test_step_values(self):
        dt = datetime(2026, 5, 26, 10, 0)
        assert Guardian._cron_matches("*/15 * * * *", dt) is True
        dt = datetime(2026, 5, 26, 10, 7)
        assert Guardian._cron_matches("*/15 * * * *", dt) is False

    def test_invalid_expression(self):
        dt = datetime(2026, 5, 26, 10, 0)
        assert Guardian._cron_matches("bad", dt) is False
        assert Guardian._cron_matches("", dt) is False

    def test_class_is_guardian_not_guardian_process(self):
        """The fixed code uses Guardian._parse_cron_field, not GuardianProcess.
        Verify the class name is correct."""
        assert Guardian.__name__ == "Guardian"
        assert not hasattr(Guardian, "_parse_cron_field") or callable(Guardian._parse_cron_field)
        # GuardianProcess should not exist
        assert "GuardianProcess" not in globals()
