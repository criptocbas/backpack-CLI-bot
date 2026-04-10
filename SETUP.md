# Setup Guide

## Prerequisites

- Python 3.8 or higher
- Backpack Exchange account with API credentials
- pip (Python package manager)

## Installation Steps

### 1. Clone the repository

```bash
git clone https://github.com/criptocbas/backpack-CLI-bot.git
cd backpack-CLI-bot
```

### 2. Install dependencies

Using a virtual environment (recommended):

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure API credentials

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` and add your Backpack API credentials:

```env
BACKPACK_API_KEY=your_api_key_here
BACKPACK_API_SECRET=your_api_secret_here
```

### 4. Getting Backpack API Credentials

1. Log in to your Backpack Exchange account
2. Navigate to Account Settings > API Management
3. Click "Create New API Key"
4. Set permissions (you'll need trading permissions)
5. Copy the API Key and Secret
6. **IMPORTANT**: Save the secret securely - you won't be able to see it again

### 5. Test the connection

Verify your credentials without placing any orders:

```bash
python test_connection.py
```

All four checks (config, public ticker, authenticated account, order book)
must pass. Then launch the bot:

```bash
python main.py
```

If everything is configured correctly, you should see the trading dashboard.

## Configuration Options

Edit `.env` to customize:

- `DEFAULT_SYMBOL` - Default trading pair (e.g., SOL_USDC, BTC_USDC)
- `DEFAULT_LEVERAGE` - Default leverage level
- `MAX_POSITION_SIZE` - Maximum position size in quote currency
- `RISK_PERCENTAGE` - Maximum risk per trade as percentage

## Troubleshooting

### Authentication errors

- Verify your API key and secret are correct
- Check that your API key has trading permissions enabled
- Ensure there are no extra spaces in the .env file

### Connection errors

- Check your internet connection
- Verify Backpack API is accessible (check status page)
- Try pinging the API endpoint

### Module not found errors

- Ensure all dependencies are installed: `pip install -r requirements.txt`
- Verify you're using the correct Python version (3.8+)

## Security Best Practices

1. **Never share your API secret**
2. **Use API key restrictions** - Limit by IP if possible
3. **Start with small amounts** - Test with minimal funds first
4. **Enable 2FA** on your Backpack account
5. **Regularly rotate API keys**
6. **Keep .env in .gitignore** - Never commit credentials

## Next Steps

Once setup is complete, see [README.md](README.md) for usage instructions.
