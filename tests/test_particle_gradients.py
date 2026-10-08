"""Gradient interpolation and random-colour source data survive extraction."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from core.particles import min_max_gradient


def gradient(mode=0, color_space=-1):
    return {
        "m_Mode": mode, "m_ColorSpace": color_space,
        "m_NumColorKeys": 2, "m_NumAlphaKeys": 2,
        "key0": {"r": 1.0, "g": 0.0, "b": 0.0, "a": 0.25},
        "key1": {"r": 0.0, "g": 1.0, "b": 0.0, "a": 1.0},
        "ctime0": 0, "ctime1": 65535, "atime0": 16384, "atime1": 49151,
    }


def test_random_colour_keeps_its_gradient_instead_of_the_inactive_colour():
    decoded = min_max_gradient({
        "minMaxState": 4, "maxGradient": gradient(1, 0),
        "maxColor": {"r": 0.125, "g": 0.125, "b": 0.125, "a": 0.125},
    })
    assert decoded["mode"] == "randomColor"
    assert "color" not in decoded
    assert decoded["gradient"]["interpolation"] == "fixed"
    assert decoded["gradient"]["colorSpace"] == 0
    assert decoded["gradient"]["colorKeys"][0]["color"] == [1.0, 0.0, 0.0]


def test_two_gradients_keep_independent_modes_and_colour_spaces():
    decoded = min_max_gradient({
        "minMaxState": 3, "minGradient": gradient(0, -1), "maxGradient": gradient(1, 0),
    })
    assert decoded["minGradient"]["interpolation"] == "blend"
    assert decoded["maxGradient"]["interpolation"] == "fixed"
    assert decoded["minGradient"]["colorSpace"] == -1
    assert decoded["maxGradient"]["colorSpace"] == 0
    assert decoded["maxGradient"]["alphaKeys"][0]["time"] == 16384 / 65535


def test_unknown_or_missing_gradient_metadata_is_not_invented():
    missing = gradient()
    del missing["m_Mode"]
    del missing["m_ColorSpace"]
    decoded = min_max_gradient({"minMaxState": 1, "maxGradient": missing})["gradient"]
    assert decoded["interpolation"] is None
    assert decoded["colorSpace"] is None
    unknown = min_max_gradient({"minMaxState": 1, "maxGradient": gradient(27, 19)})["gradient"]
    assert unknown["interpolation"] == 27
    assert unknown["colorSpace"] == 19


def test_perceptual_mode_is_preserved_even_before_a_consumer_admits_it():
    decoded = min_max_gradient({"minMaxState": 1, "maxGradient": gradient(2, 1)})["gradient"]
    assert decoded["interpolation"] == "perceptualBlend"
    assert decoded["colorSpace"] == 1
