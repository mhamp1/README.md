from cryptography.fernet import Fernet
private_key = input("Enter your base58 private key: ")
key = Fernet.generate_key()
f = Fernet(key)
encrypted = f.encrypt(private_key.encode())
print("FERNET_SECRET:", key.decode())
print("ENCRYPTED_SOL_KEY:", encrypted.decode())
