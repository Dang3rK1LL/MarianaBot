$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$projectRoot = Split-Path -Parent $PSScriptRoot
$serverProfile = Get-Content -LiteralPath (Join-Path $projectRoot 'mariana-server.json') -Raw | ConvertFrom-Json
if ($serverProfile.host -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]*$' -or $serverProfile.user -notmatch '^[a-z_][a-z0-9_-]*$') {
    throw 'Invalid server connection profile.'
}
$keyPath = [System.IO.Path]::GetFullPath($serverProfile.identity_file)
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf) -or $keyPath.Contains('"')) {
    throw 'The SSH key in mariana-server.json was not found.'
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'MarianaBot Discord setup'
$form.ClientSize = New-Object System.Drawing.Size(550, 190)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.TopMost = $true

$label = New-Object System.Windows.Forms.Label
$label.Text = 'Paste your Discord Bot token. It will be saved privately on Ubuntu.'
$label.SetBounds(20, 18, 515, 25)
$form.Controls.Add($label)

$tokenBox = New-Object System.Windows.Forms.TextBox
$tokenBox.UseSystemPasswordChar = $true
$tokenBox.ShortcutsEnabled = $true
$tokenBox.SetBounds(20, 50, 420, 26)
$form.Controls.Add($tokenBox)

$paste = New-Object System.Windows.Forms.Button
$paste.Text = 'Paste'
$paste.SetBounds(450, 48, 80, 29)
$paste.Add_Click({ $tokenBox.Paste(); $tokenBox.Focus() })
$form.Controls.Add($paste)

$status = New-Object System.Windows.Forms.Label
$status.Text = 'Use the Paste button or Ctrl+V. Pasted characters appear masked.'
$status.SetBounds(20, 90, 510, 44)
$form.Controls.Add($status)

$connect = New-Object System.Windows.Forms.Button
$connect.Text = 'Connect'
$connect.SetBounds(355, 140, 85, 30)
$form.Controls.Add($connect)
$form.AcceptButton = $connect

$close = New-Object System.Windows.Forms.Button
$close.Text = 'Close'
$close.SetBounds(450, 140, 80, 30)
$close.Add_Click({ $form.Close() })
$form.Controls.Add($close)

$connect.Add_Click({
    $value = $tokenBox.Text.Trim()
    if ($value.Length -lt 20 -or $value -match '[^A-Za-z0-9._-]') {
        $status.Text = 'Paste the complete Bot token, then select Connect.'
        return
    }
    $connect.Enabled = $false
    $paste.Enabled = $false
    $status.Text = 'Checking Discord authentication and saving the token on Ubuntu...'
    [System.Windows.Forms.Application]::DoEvents()
    try {
        $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($value))
        $remoteScript = @'
import asyncio, base64, json, pathlib, subprocess, uuid
import aiohttp
from marianabot.discord_config import load_discord, write_private

async def main():
    value = base64.b64decode('__TOKEN__').decode()
    repo = pathlib.Path.home() / 'MarianaBot'
    settings = load_discord(repo / 'discord.toml')
    async with aiohttp.ClientSession(headers={'Authorization': 'Bot ' + value}, timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get('https://discord.com/api/v10/users/@me') as response:
            if response.status != 200:
                print(json.dumps({'ok': False, 'message': 'Discord rejected this token. Copy a fresh token from your application\'s Bot page.'}))
                return
            bot = await response.json()
            if not bot.get('bot'):
                print(json.dumps({'ok': False, 'message': 'Use a Discord Bot token.'}))
                return
        async with session.get('https://discord.com/api/v10/channels/' + str(settings.channel_id)) as response:
            if response.status != 200:
                print(json.dumps({'ok': False, 'message': 'The bot needs View Channel permission in #marianabot.'}))
                return
            channel = await response.json()
            if channel.get('guild_id') != str(settings.guild_id) or channel.get('type') != 0:
                print(json.dumps({'ok': False, 'message': 'The destination does not match the configured server text channel.'}))
                return
    temporary = settings.token_file.with_name('.discord-token-' + uuid.uuid4().hex + '.token')
    try:
        write_private(temporary, value + '\n')
        temporary.replace(settings.token_file)
    finally:
        temporary.unlink(missing_ok=True)
    subprocess.run(['systemctl', '--user', 'reset-failed', 'marianabot-discord.service'], check=True, capture_output=True)
    subprocess.run(['systemctl', '--user', 'restart', 'marianabot-discord.service'], check=True, capture_output=True)
    print(json.dumps({'ok': True, 'message': 'Connected as ' + bot['username'] + '. Token saved privately on Ubuntu.'}))

try:
    asyncio.run(main())
except Exception:
    print(json.dumps({'ok': False, 'message': 'Connection failed. Check the server and network, then try again.'}))
'@
        $remoteScript = $remoteScript.Replace('__TOKEN__', $encoded)
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName = 'ssh.exe'
        $start.Arguments = '-i "' + $keyPath + '" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes ' + $serverProfile.user + '@' + $serverProfile.host + ' "/home/ubuntu/MarianaBot/.venv/bin/python -"'
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $start
        [void]$process.Start()
        $process.StandardInput.WriteLine($remoteScript)
        $process.StandardInput.Close()
        $output = $process.StandardOutput.ReadToEnd()
        $diagnostic = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
            $status.Text = 'SSH connection failed. Check mariana-server.json and try again.'
        } else {
            $result = $output | ConvertFrom-Json
            $status.Text = $result.message
            if ($result.ok) { $tokenBox.Clear() }
        }
    } catch {
        $status.Text = 'Setup could not finish. Check the server connection, then try again.'
    } finally {
        $value = $null
        $encoded = $null
        $remoteScript = $null
        $connect.Enabled = $true
        $paste.Enabled = $true
    }
})

$form.Add_Shown({ $tokenBox.Focus() })
[void]$form.ShowDialog()
