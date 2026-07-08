"""Generate the dev Ed25519 keypair for the JIT token broker (keys/ is gitignored)."""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    keys = Path(__file__).parent.parent / "keys"
    keys.mkdir(exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    (keys / "broker_private.pem").write_bytes(private_pem)
    (keys / "broker_private.pem").chmod(0o600)
    (keys / "broker_public.pem").write_bytes(public_pem)
    print(f"Wrote keypair to {keys}/")


if __name__ == "__main__":
    main()
