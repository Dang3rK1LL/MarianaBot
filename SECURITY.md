# Security and private data

Each installation uses its owner's machine, provider logins and optional SSH
profile. There is no shared backend, default remote host or central credential
store. The repository contains application code and offline fixtures.

`mariana.toml`, `mariana-server.json`, `.mariana/`, local integration settings,
environment files and common private-key files are ignored by Git. Git ignores
do not prevent deliberate `git add --force`, previously tracked files, or copies
saved under other names. Review staged changes before publishing.

Research databases, transcripts, logs, exported prompts and backups can contain
confidential material. Keep them in private folders. Official client credentials
belong in the clients' own storage; never paste them into problems, issues or chat.
Rotate an exposed credential at its provider even if a leaked file is deleted.

Optional Discord access is limited to one configured server/channel and an
explicit list of user IDs. Remote control is separately opt-in. Anyone who can
read that channel can read posted research; the allowlist restricts commands,
not Discord's channel visibility. Tokens and personal IDs are not distributed
with the code. Removing the bot stops future delivery, not previously posted
messages. See [Discord setup](docs/discord.md).

Model requests go through the installed official clients. MarianaBot removes
API-key and integration-token overrides from their environment, uses restricted
client tool configurations and passes prompts through stdin, not shell commands.
These restrictions are not a separate operating-system security boundary. Run
under a dedicated, non-root account on a VPS and keep unrelated secrets outside
its accessible files. Native client behavior and authentication remain part of
the trust boundary.

Remote chat uses SSH with a user-provided host and key path. The launcher checks
the saved SSH host key and opens no public application port. Keep the host's
firewall, SSH access, operating system and provider clients maintained. Daily
backups on the same VPS do not protect against loss of that VPS.

Report a vulnerability without including credentials or private research. If a
private reporting channel is unavailable, open a minimal issue requesting one;
do not publish an exploit against a real installation or its connection details.
