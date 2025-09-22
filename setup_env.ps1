# setup_env.ps1
Set-StrictMode -Version Latest

Write-Host "Creating virtual environment .venv"
python -m venv .venv

Write-Host "Activating virtual environment"
# For PowerShell
.\.venv\Scripts\Activate.ps1

Write-Host "Upgrading pip, setuptools, wheel"
python -m pip install --upgrade pip setuptools wheel

Write-Host "Installing requirements from requirements.txt"
python -m pip install -r requirements.txt

Write-Host "Verifying critical imports"
python - <<'PY'
import sys, traceback
ok = True
try:
    import solders.spl.token.constants as c
    print("solders.spl import OK:", c.__name__)
except Exception:
    print("solders.spl import FAILED")
    traceback.print_exc()
    ok = False
try:
    import solana, anchorpy
    print("solana and anchorpy available")
except Exception:
    print("solana/anchorpy import FAILED")
    traceback.print_exc()
    ok = False
print("ENV_READY" if ok else "ENV_ISSUES")
PY

Write-Host "Setup script finished. If verification printed ENV_READY, you're good to run the bot in this venv."