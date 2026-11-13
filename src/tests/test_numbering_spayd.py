from datetime import date
from decimal import Decimal

import pytest
import zxingcpp
from django.core.exceptions import ValidationError
from PIL import Image

from conftest import iban_for
from invoicing.spayd import QrPaymentUnavailable, build_spayd, parse_spayd, qr_png_data_uri
from profiles import numbering
from profiles.validators import iban_is_valid


def test_formats():
    assert numbering.render("{YY}{N}", 57, 2026, 0) == "2657"
    assert numbering.render("{YY}{N}", 57, 2026, 3) == "26057"
    assert numbering.render("FV{YYYY}-{N}", 7, 2027, 4) == "FV2027-0007"
    assert numbering.render("{N}", 1001, 2026, 0) == "1001"
    assert numbering.period_for("{YY}{N}", 2026) == "2026"
    assert numbering.period_for("{N}", 2026) == ""


@pytest.mark.parametrize("bad", ["{YY}", "{N}{N}", "{X}{N}", "F V{N}", "{N}" + "9" * 40])
def test_invalid_formats(bad):
    with pytest.raises(ValidationError):
        numbering.validate_format(bad)


def test_iban_validation():
    assert iban_is_valid("CZ0762106701002201090240")  # vzor VF2657
    assert iban_is_valid("CZ07 6210 6701 0022 0109 0240")
    assert not iban_is_valid("CZ0862106701002201090240")
    assert iban_is_valid(iban_for("0100", "1234567899"))


def test_spayd_payload_matches_document_values():
    payload = build_spayd(iban="CZ0762106701002201090240", bic="BREXCZPP", amount=Decimal("1936.00"),
                          variable_symbol="2657", due_date=date(2026, 10, 25), message="Faktura 2657")
    assert payload == ("SPD*1.0*ACC:CZ0762106701002201090240+BREXCZPP*AM:1936.00*CC:CZK"
                       "*DT:20261025*X-VS:2657*MSG:Faktura 2657")


def test_spayd_strips_separator_from_message():
    payload = build_spayd(iban="CZ0762106701002201090240", amount=Decimal("1"), message="a*b")
    assert parse_spayd(payload)["MSG"] == "a b"


@pytest.mark.parametrize(
    "kwargs, msg",
    [
        ({"iban": "", "amount": Decimal("1")}, "IBAN"),
        ({"iban": "CZ0862106701002201090240", "amount": Decimal("1")}, "IBAN"),
        ({"iban": "CZ0762106701002201090240", "amount": Decimal("0")}, "kladná"),
        ({"iban": "CZ0762106701002201090240", "amount": Decimal("1"), "variable_symbol": "12345678901"}, "Variabilní"),
    ],
)
def test_spayd_unavailable(kwargs, msg):
    with pytest.raises(QrPaymentUnavailable, match=msg):
        build_spayd(**kwargs)


def test_qr_image_decodes_to_payload():
    import base64
    import io

    payload = build_spayd(iban="CZ0762106701002201090240", amount=Decimal("1936.00"),
                          variable_symbol="2657")
    uri = qr_png_data_uri(payload)
    image = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    results = zxingcpp.read_barcodes(image)
    assert results and results[0].text == payload
