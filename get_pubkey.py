import os
import base58
from cryptography.fernet import Fernet, InvalidToken
from solders.keypair import Keypair
from dotenv import load_dotenv

try:
    load_dotenv()
    fernet_key = os.getenv("FERNET_SECRET")
    encrypted_key = os.getenv("ENCRYPTED_SOL_KEY")

    if not fernet_key:
        raise ValueError("FERNET_SECRET is missing or empty in .env")
    if not encrypted_key:
        raise ValueError("ENCRYPTED_SOL_KEY is missing or empty in .env")

    print("Loaded vars successfully. Decrypting...")

    fernet = Fernet(fernet_key.encode())
    decrypted = fernet.decrypt(encrypted_key.encode())
    print("Decryption successful. Decoding base58...")

    secret_bytes = base58.b58decode(decrypted)
    keypair = Keypair.from_bytes(secret_bytes)
    print(f"Your wallet public key: {keypair.pubkey()}")

except InvalidToken:
    print("Error: Invalid Fernet token—check if FERNET_SECRET matches the one used to encrypt, or if ENCRYPTED_SOL_KEY is corrupted.")
except ValueError as ve:
    print(f"Value error: {ve} (likely invalid base58 or wrong length if related to decode)")
except Exception as e:
    print(f"Unexpected error: {type(e).__name__} - {e}")
