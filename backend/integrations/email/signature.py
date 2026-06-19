"""Branded SGC TECH AI email signature (HTML + plain-text).

The banner is served from Cloudinary with on-the-fly optimisation
(f_auto,q_auto,w_600) so the email stays light (~50KB) instead of the
2.7MB original. Override the image via EMAIL_SIGNATURE_IMAGE_URL.
"""
from __future__ import annotations

import os

_DEFAULT_IMAGE_URL = (
    "https://res.cloudinary.com/dsl5fhclj/image/upload/"
    "f_auto,q_auto,w_600/v1780653942/tnwfcvuwekynwogxpk5e.png"
)
_SITE_URL = "https://www.sgctech.ai"


def signature_image_url() -> str:
    return os.getenv("EMAIL_SIGNATURE_IMAGE_URL", _DEFAULT_IMAGE_URL)


def signature_html() -> str:
    url = signature_image_url()
    return (
        '<div style="margin-top:24px;">'
        f'<a href="{_SITE_URL}" target="_blank" rel="noopener noreferrer" '
        'style="text-decoration:none;border:0;">'
        f'<img src="{url}" '
        'alt="SGC TECH AI - Build a More Resilient Business. Clarity. Control. Confidence." '
        'width="600" style="width:100%;max-width:600px;height:auto;display:block;border:0;outline:none;" />'
        "</a></div>"
    )


def signature_text() -> str:
    return (
        "\n\n--\n"
        "SGC TECH AI - Build a More Resilient Business\n"
        "Clarity. Control. Confidence.\n"
        "info@sgctech.ai | +971 52 198 5231 | www.sgctech.ai"
    )
