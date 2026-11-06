@echo off
chcp 65001 >nul
schtasks /Delete /TN "Smart Basket daily sync" /F
py -3.12 -c "import keyring; keyring.delete_password('smart-basket-github','itayyaari123-cmyk/smart-market')" 2>nul
echo Daily sync removed.
pause
