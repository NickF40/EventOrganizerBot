from app.localization import DEFAULT_LOCALE, Localizer, get_localizer


def test_localizer_returns_known_strings():
    localizer = get_localizer(DEFAULT_LOCALE)
    assert localizer.get("buttons.attend") == "Attend the event"
    formatted = localizer.format("start.returning", name="Alex", summary="• Attendee: Approved")
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


def test_localizer_handles_dict_fallback_and_non_string_values():
    fallback = Localizer({"group": "Fallback value", "value": 123})
    localizer = Localizer({"group": {"nested": "value"}}, fallback)

    assert localizer.get("group") == "Fallback value"
    assert localizer.get("value") == "123"


def test_localizer_format_ignores_missing_placeholders():
    localizer = Localizer({"template": "Hello {name} {missing}"})

    assert localizer.format("template", name="Taylor") == "Hello {name} {missing}"
