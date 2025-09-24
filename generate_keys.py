from cryptography.fernet import Fernet

# Replace with your exact plain base58 private key from Phantom export
plain_key = '5QpeYg1U7wXhLXmMpzGD7A4RBCW9qHdjEuhNzv7R7bSK6P3uUzWKA7ceq2NqJuDYvWxhfaSVWtiiMc58kKThSKSL'

key = Fernet.generate_key()
f = Fernet(key)
encrypted = f.encrypt(plain_key.encode())

print("NEW FERNET_SECRET (copy to Render env):", key.decode())
print("NEW ENCRYPTED_SOL_KEY (copy to Render env):", encrypted.decode())
print("Decrypted test (should match plain_key):", f.decrypt(encrypted).decode())
print("Your wallet pubkey (for verification):", Keypair.from_base58_string(plain_key).pubkey())  # Add this line for address
