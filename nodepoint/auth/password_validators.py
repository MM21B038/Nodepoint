from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from typing import List


class NodepointPasswordValidator:
    """Min 8 chars with uppercase, lowercase, digit, and special character."""

    MIN_LENGTH = 8

    def validate(self, password, user=None):
        errors: List[str] = []
        if len(password) < self.MIN_LENGTH:
            errors.append(
                _(f"Password must be at least {self.MIN_LENGTH} characters long.")
            )
        if not re.search(r"[a-z]", password):
            errors.append(_("Password must include at least one lowercase letter."))
        if not re.search(r"[A-Z]", password):
            errors.append(_("Password must include at least one uppercase letter."))
        if not re.search(r"\d", password):
            errors.append(_("Password must include at least one number."))
        if not re.search(r"[^A-Za-z0-9]", password):
            errors.append(
                _("Password must include at least one special character.")
            )
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return _(
            "Your password must be at least 8 characters and include uppercase, "
            "lowercase, a number, and a special character."
        )
