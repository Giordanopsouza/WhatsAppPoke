"""Analytics dashboard: phone matching, query contracts, login gate."""

from __future__ import annotations

from pathlib import Path

from analytics import queries
from analytics.settings import AnalyticsSettings, normalize_excluded_phones


_LOOKBACK = "make_interval(days => :days)"

_QUERIES = (
    queries.KPIS,
    queries.DAILY_ACTIVITY,
    queries.WEEKLY_ACTIVITY,
    queries.CONTACT_SUMMARY,
    queries.HOURLY_HEATMAP,
    queries.INTEGRATION_FUNNEL,
    queries.JOB_HEALTH,
    queries.FEATURE_USAGE,
)


def test_exclude_phones_strips_plus_and_punctuation() -> None:
    assert normalize_excluded_phones("+55 11 99999-9999, 5511888888888") == [
        "5511999999999",
        "5511888888888",
    ]


def test_exclude_phones_empty_and_junk() -> None:
    assert normalize_excluded_phones("") == []
    assert normalize_excluded_phones(" , + , abc ") == []


def test_excluded_phones_property_matches_stored_format() -> None:
    s = AnalyticsSettings(
        DATABASE_URL="postgresql+asyncpg://x",
        ANALYTICS_USER="gio",
        ANALYTICS_PASSWORD="twelvechars!",
        ANALYTICS_EXCLUDE_PHONES="+5511999999999",
        _env_file=None,
    )
    assert s.excluded_phones == ["5511999999999"]


def test_every_query_respects_the_lookback_window() -> None:
    for sql in _QUERIES:
        assert _LOOKBACK in sql, sql.split("\n", 1)[0]


def test_contact_summary_groups_by_id_not_label() -> None:
    assert "GROUP BY sel.id, sel.label" in queries.CONTACT_SUMMARY
    assert "GROUP BY sel.label\n" not in queries.CONTACT_SUMMARY


def test_contact_summary_cuts_days_in_local_tz() -> None:
    assert "min((m.created_at AT TIME ZONE :tz)::date)" in queries.CONTACT_SUMMARY
    assert "(now() AT TIME ZONE :tz)::date" in queries.CONTACT_SUMMARY
    assert "min(m.created_at)::date" not in queries.CONTACT_SUMMARY


def test_fallback_label_matches_connect_page_mask() -> None:
    assert "'+' || left(c.phone, 2) || '••••' || right(c.phone, 4)" in queries._SEL


def test_login_rejects_wrong_credentials(monkeypatch) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x")
    monkeypatch.setenv("ANALYTICS_USER", "gio")
    monkeypatch.setenv("ANALYTICS_PASSWORD", "twelvechars!")

    dashboard = Path(__file__).resolve().parents[1] / "analytics" / "dashboard.py"
    at = AppTest.from_file(dashboard, default_timeout=10)
    at.run()
    assert not at.exception
    at.text_input[0].set_value("wrong-user")
    at.text_input[1].set_value("wrong-password")
    at.button[0].click().run()
    assert at.error
    assert "inválidos" in at.error[0].value
