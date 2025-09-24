from cryptography.fernet import Fernet

# Use the NEW values from above
fernet_key = 'EV16jOlj2FyZlQZ40hyU9gpmzlLqXRYHD8kgMunfRtM='  # Replace with your NEW FERNET_SECRET
encrypted_key = 'gAAAAABo0vi2V2rGBr5qg2mP5QlpErsR_0AP95XEGA9HBorwfQXPibYMfuWAPyr64k5y-WZKSxZFEh4JN5tDwdG3jtOPraIKgp04T-18HeivBCMdIr7DukN5cL9cleAWKjre-q14chHDvVmuTgMehkU1fRCuMaZzFfzvwwD4gHxraxC2_P1y4_-Sqn9spo9EALiOp-SQqDAK'  # Replace with your NEW ENCRYPTED_SOL_KEY

try:
    f = Fernet(fernet_key.encode())
    decrypted = f.decrypt(encrypted_key.encode()).decode()
    print(f"Decryption successful: {decrypted}")
except Exception as e:
    print(f"Fail: {e}")
