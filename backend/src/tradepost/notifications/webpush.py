"""Web Push without a paid service: message encryption (RFC 8291) and VAPID (RFC 8292).

The browser's push service (Google, Mozilla, Apple) only relays an encrypted blob to the device;
it cannot read the content. The server is identified by a VAPID key pair, configured as one
base64url private key; the public key the browser needs is derived from it.
"""

import base64
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
# Push services keep an undelivered message this long (the phone may be off).
TTL_SECONDS = 24 * 3600


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def private_key_from(value: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64url_decode(value), "big"), ec.SECP256R1())


def generate_vapid_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return b64url_encode(key.private_numbers().private_value.to_bytes(32, "big"))


def application_server_key(private_key: str) -> str:
    """The public key, as the browser's ``pushManager.subscribe`` expects it."""
    return b64url_encode(_public_bytes(private_key_from(private_key).public_key()))


def encrypt(
    payload: bytes,
    receiver_public: bytes,
    auth_secret: bytes,
    *,
    sender: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """One aes128gcm record for the receiver (RFC 8291 section 3). ``sender`` and ``salt`` are
    fixed only by tests; each message otherwise gets a fresh key pair and salt."""
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    sender_public = _public_bytes(sender.public_key())
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_public)
    shared = sender.exchange(ec.ECDH(), receiver)

    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + receiver_public + sender_public, 32)
    key = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    # A single record: the content, then the last-record delimiter (no padding).
    if len(payload) + 1 + 16 > RECORD_SIZE - 86:
        raise ValueError("push payload too large")
    ciphertext = AESGCM(key).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_public)]) + sender_public
    return header + ciphertext


def vapid_authorization(
    endpoint: str, private_key: str, subject: str, now: float | None = None
) -> str:
    """``Authorization`` header for one push service (RFC 8292): an ES256 JWT for its origin."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now or time.time()) + 12 * 3600),
        "sub": subject,
    }
    header = b64url_encode(
        json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()
    )
    body = b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    key = private_key_from(private_key)
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    public = application_server_key(private_key)
    return f"vapid t={header}.{body}.{signature}, k={public}"


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: str
    auth: str


class PushGone(Exception):
    """The subscription no longer exists (the browser unsubscribed or the app was removed)."""


class PushFailed(Exception):
    """The push service refused or did not answer; worth trying again later."""


class WebPush:
    def __init__(self, private_key: str, subject: str, client: httpx.AsyncClient | None = None):
        self.private_key = private_key
        self.subject = subject
        self.public_key = application_server_key(private_key)
        self._client = client

    async def send(self, subscription: Subscription, message: dict[str, object]) -> None:
        body = encrypt(
            json.dumps(message, ensure_ascii=False).encode(),
            b64url_decode(subscription.p256dh),
            b64url_decode(subscription.auth),
        )
        headers = {
            "Authorization": vapid_authorization(
                subscription.endpoint, self.private_key, self.subject
            ),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SECONDS),
            "Urgency": "normal",
        }
        try:
            if self._client is not None:
                response = await self._client.post(
                    subscription.endpoint, content=body, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        subscription.endpoint, content=body, headers=headers
                    )
        except httpx.HTTPError as exc:
            raise PushFailed(type(exc).__name__) from exc
        if response.status_code in (404, 410):
            raise PushGone(str(response.status_code))
        if response.status_code >= 400:
            raise PushFailed(f"HTTP {response.status_code}")
