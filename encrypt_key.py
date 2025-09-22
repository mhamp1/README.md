from cryptography.fernet import Fernet

# Replace with your base58 private key from Step 2
private_key = b"2mN6tzurkK7HHe5C7Ziqv8Q8YLDHZd5ERfT2qAHxhHQuHijMwrK3S3NkYrhkXhsJNVceFc6WmfoEKf4aKXTyeEo3"  # e.g., b"2zQ..."

key = Fernet.generate_key()
f = Fernet(key)
encrypted = f.encrypt(private_key)
print("FERNET_SECRET:", key.decode())
print("ENCRYPTED_SOL_KEY:", encrypted.decode())
