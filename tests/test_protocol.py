"""Protocol serialization preserves binary data and validates message envelopes."""

import msgpack
import pytest

from protocol import body_value, message, pack_message, unpack_message


@pytest.mark.parametrize('body', [b'', b'\x00\xffpayload'])
def test_envelope_round_trip(body):
    value = message(1, 'shell', 'session', body, {'width': 80})
    assert unpack_message(pack_message(value)) == value
    assert value['hdr'] == {'proto': 1, 'typ': 'shell', 'sid': 'session', 'props': {'width': 80}}
    assert ('body' in value) == bool(body)


def test_empty_optional_fields_are_omitted():
    assert message(3, '') == {'hdr': {'proto': 3}}


@pytest.mark.parametrize('value', [[], None, {'body': b'x'}, {'hdr': []}])
def test_invalid_envelopes(value):
    with pytest.raises(ValueError, match='invalid Mender protocol'):
        unpack_message(msgpack.packb(value))


@pytest.mark.parametrize(('value', 'expected'), [({}, {}), ({'body': b''}, {}),
    ({'body': {'error': 'failure'}}, {'error': 'failure'}),
    ({'body': msgpack.packb({'versions': [1]})}, {'versions': [1]})])
def test_body_decoding(value, expected):
    assert body_value(value) == expected


def test_corrupted_message_is_rejected():
    with pytest.raises((ValueError, msgpack.UnpackException)):
        unpack_message(b'\xc1')
