# Startup updates

MarianaBot checks its GitHub repository when you start chat, start or resume
research from the CLI, launch the Discord bot, or open the Windows server
shortcut. The server launcher checks the VPS installation before attaching to
its terminal, and the terminal service checks at boot before recovering research.

Checks are enabled by default. Demo mode, help, diagnostics, exports and status
commands do not check the network. Checking GitHub and installing Python packages
does not call either model or consume subscription allowance.

## What happens on startup

1. The launcher verifies that this is a clean `main` checkout with the official
   MarianaBot origin, then fetches the current `main` commit. It never pushes.
2. If the installation is idle, it builds the update in a separate virtual
   environment. Discord is included only when already installed in the launcher
   environment. The updater checks dependencies, imports, packaged interface
   files and compatibility with your application configuration.
3. After those checks pass, it advances the checkout with a fast-forward only
   and activates the prepared environment. Chat opens using the new version.

You see short progress messages during the check and installation. A download or
dependency-install failure leaves the existing checkout and environment in use;
the next startup retries. Local edits, untracked files, another branch or a
diverged history prevent installation. Changes made while the update is being
prepared are checked again before activation. The updater never resets, stashes
or merges your local work.

The original `.venv` remains the launcher. Prepared environments and their
activation record live in the gitignored `.mariana-updates/` directory. The app
selects the active environment automatically; you do not need to activate it or
change your shortcuts. Keep that directory in place: it holds installed runtime
dependencies. Existing environments are retained when an update fails.

Your research database, drafts, provider logins, `mariana.toml`, SSH profile and
Discord credentials remain where they are. The updater does not edit model
preferences or billing settings. It updates application code and its Python
dependencies; provider clients, operating-system packages and installed systemd
unit definitions remain separate maintenance tasks.

## Updates wait for an idle installation

Chat, Discord and detached workers hold process locks. An update can be found
while they are running, but it waits to install until a later startup with those
processes closed. Locks are shared across research directories in the same
installation, so another session cannot update the code beneath a running worker.
Concurrent launches are serialized during installation.

On a VPS, closing SSH only detaches from the existing terminal. To make an update
apply on your next connection, `/pause` research if necessary, wait for its worker
to exit, then `/quit` the chat. Stop the optional Discord process as well. Reopen
the **MarianaBot Server** shortcut. Do not close an active research run just to
install an update unless you want to pause it; leaving it running is supported.

The laptop checkout and VPS checkout update independently. The server shortcut
checks the local launcher and then the VPS; credentials and research are not
synchronized between them.

## Controls

```text
mariana update --check
mariana update
mariana --no-update
mariana --no-update chat
```

The first command checks without installing; the second tries to install when
idle. `--no-update` skips the startup check for that local invocation. You can
also set the environment variable `MARIANA_AUTO_UPDATE=0`, or disable automatic
checks persistently in `mariana.toml`:

```toml
[updates]
enabled = false
```

On the VPS, the configuration setting applies to both the connection launcher
and the boot service. Explicit `mariana update` remains available when automatic
checks are disabled. There is no update check inside individual model agents.

Automatic updates execute code published to this repository's `main` branch.
Disable them if you prefer to review changes before installation. Forks, custom
origins, source archives and ordinary wheel-only installations are not silently
redirected to this repository; maintain those installations with their own
package or Git workflow. For development, use a separate checkout with automatic
updates disabled. After a deliberate manual pull, reinstall with `pip install -e .`
so the original environment has the matching dependencies.

## Verification

The offline suite checks fast-forwards, modified and diverged checkouts,
untrusted remotes, simultaneous launches, live process locks, failed builds,
network errors, activation failures, private-file preservation and runtime
handoff. To exercise the real package installer in a disposable checkout:

```text
python scripts/check_updater.py
```

This separate check downloads Python dependencies and runs offline demo research.
It does not contact model providers or modify your real installation.
