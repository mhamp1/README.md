import base58
from cryptography.fernet import Fernet
from solders.keypair import Keypair

kp = Keypair()  # New random keypair
fernet_key = Fernet.generate_key()
encrypted = Fernet(fernet_key).encrypt(base58.b58encode(kp.to_bytes()))

fernet_str = fernet_key.decode()
print(f"FERNET_SECRET length: {len(fernet_str)} (should be 44)")
print(f"FERNET_SECRET={fernet_str}")
print(f"ENCRYPTED_SOL_KEY={encrypted.decode()}")
print(f"\nYour new public key (for Explorer): {kp.pubkey()}")
print("Fund this address with SOL/USDC on mainnet/devnet.")
