"""Tests for the per-light transition cap on OnOffDevice controls."""

from types import SimpleNamespace

from emulated_hue.controllers.devices import OnOffDevice


def _control(max_transition_ms, throttle_ms=150):
    device = SimpleNamespace(
        throttle_ms=throttle_ms,
        power_state=True,
        transition_seconds=0.4,
        max_transition_ms=max_transition_ms,
    )
    return OnOffDevice.OnOffControl(device)


def test_no_cap_keeps_the_requested_transition():
    control = _control(None)
    control.set_transition_ms(500)
    assert control.control_state.transition_seconds == 0.5


def test_cap_limits_a_longer_transition():
    control = _control(100)
    control.set_transition_ms(500)
    assert control.control_state.transition_seconds == 0.1


def test_cap_does_not_stretch_a_shorter_transition():
    control = _control(300)
    control.set_transition_ms(100)
    assert control.control_state.transition_seconds == 0.1


def test_cap_of_zero_disables_fading_entirely():
    control = _control(0)
    control.set_transition_ms(400)
    assert control.control_state.transition_seconds == 0.0


def test_cap_wins_over_the_throttle_floor():
    control = _control(0, throttle_ms=150)
    control.set_transition_ms(0, respect_throttle=True)
    assert control.control_state.transition_seconds == 0.0
