# Oracle Cloud Always Free deployment

This deployment runs the Polymarket Edge Scanner continuously on an Oracle Cloud Compute VM with `systemd` auto-restart and a persistent SQLite database stored outside the Git checkout.

## Recommended OCI VM

- Image: current Canonical Ubuntu image
- Shape: `VM.Standard.A1.Flex` (Always Free eligible)
- OCPU: `1`
- Memory: `6 GB`
- Public IPv4: enabled, for SSH administration only
- Do not open application port 8000 to the internet; the bot only needs outbound internet access. OCI's normal SSH rule on TCP 22 is enough.

Oracle's current Always Free limit for Ampere A1 is up to 2 OCPUs and 12 GB RAM total across Always Free A1 instances, so 1 OCPU / 6 GB stays inside the allowance.

## OCI Console steps

1. Create/sign in to Oracle Cloud Free Tier.
2. Choose the home region carefully. Always Free compute resources must be provisioned in the home region.
3. Open **Compute > Instances > Create instance**.
4. Name: `polymarket-edge-scanner`.
5. Change image to the current **Canonical Ubuntu** platform image.
6. Change shape: **Ampere > VM.Standard.A1.Flex**.
7. Set **1 OCPU** and **6 GB memory**.
8. Networking: create/select a public subnet and enable **Assign public IPv4 address**.
9. Under SSH keys choose **Generate a key pair for me** and download the private key. Keep it private.
10. Click **Create**.

## Connect from your computer

OCI Ubuntu images use the `ubuntu` SSH username.

Linux/macOS example:

```bash
chmod 600 ~/Downloads/ssh-key.key
ssh -i ~/Downloads/ssh-key.key ubuntu@YOUR_PUBLIC_IP
```

On Windows, use Windows Terminal / PowerShell with the downloaded private key:

```powershell
ssh -i "C:\path\to\ssh-key.key" ubuntu@YOUR_PUBLIC_IP
```

## Install the scanner

Once connected by SSH, run exactly:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/heroo2123/Alpha/main/deploy/oracle/install.sh)
```

The installer will:

- install Python/Git dependencies;
- clone `heroo2123/Alpha`;
- build a Python virtual environment;
- ask for the Telegram BotFather token with hidden input;
- optionally ask for the Telegram chat ID;
- create persistent data at `~/.polymarket-edge-scanner/data/signals.db`;
- create and enable `polymarket-edge-scanner.service`;
- start the scanner and configure automatic restart after crashes/reboots.

## If you do not know the Telegram chat ID yet

Leave the chat-ID prompt blank during installation. After the scanner starts, open the bot in Telegram and send:

```text
/whoami
```

Then on the Oracle VM run:

```bash
~/polymarket-edge-scanner/deploy/oracle/set-chat-id.sh YOUR_CHAT_ID
```

The service restarts with the new chat ID. Send `/help` to verify Telegram.

## Administration

Status:

```bash
sudo systemctl status polymarket-edge-scanner
```

Live logs:

```bash
sudo journalctl -u polymarket-edge-scanner -f
```

Local health check:

```bash
curl http://127.0.0.1:8000/health
```

Update to the latest GitHub `main`:

```bash
~/polymarket-edge-scanner/deploy/oracle/update.sh
```

Restart:

```bash
sudo systemctl restart polymarket-edge-scanner
```

## Security

- Never commit or paste the Telegram bot token into GitHub.
- The installer stores it in `~/.polymarket-edge-scanner/bot.env` with permissions `600`.
- The application listens only on `127.0.0.1:8000`; there is no reason to expose port 8000 publicly.
- Keep the downloaded SSH private key private.
- The bot does not require a Polymarket wallet private key because it does not place orders.
