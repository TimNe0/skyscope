"""Input dialogs specific to SkyScope.

The firmware ships NumberDialog, but its alphabet is "0123456789." with no
minus sign -- which makes western longitudes and southern latitudes impossible
to type. CoordDialog is the same dialog with a sign key added.
"""

from app_components.dialog import NumberDialog

COORD_ALPHABET = list("0123456789.-")


class CoordDialog(NumberDialog):
    """Numeric entry that also accepts a leading minus, for lat/lon."""

    def __init__(self, message, app, masked=False, on_complete=None, on_cancel=None):
        super().__init__(message, app, masked, on_complete, on_cancel)
        self._current_alphabet = COORD_ALPHABET
        self._default_alphabet = COORD_ALPHABET
        self._shifted_alphabet = COORD_ALPHABET
        self._update_keys()
