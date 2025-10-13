from app.localization import DEFAULT_LOCALE, get_localizer


def test_localizer_returns_known_strings():
    localizer = get_localizer(DEFAULT_LOCALE)
    assert localizer.get("buttons.attend") == "Attend the event"
    formatted = localizer.format(
        "start.returning", name="Alex", summary="• Attendee: Approved"
    )
    assert "Alex" in formatted
    assert "Approved" in formatted


def test_localizer_falls_back_to_default_locale():
    localizer = get_localizer("does-not-exist")
    assert localizer.get("help.text") == (
        "Use /start to begin registration or /status to check your current status."
    )


def test_localizer_returns_key_for_unknown_entry():
    localizer = get_localizer(DEFAULT_LOCALE)
    assert localizer.get("nonexistent.key") == "nonexistent.key"
