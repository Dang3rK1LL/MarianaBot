# Using MarianaBot on your Windows laptop

Your existing installation is ready. In PowerShell, start from the project folder:

```powershell
cd C:\Programming\Codex\MarianaBot
.\.venv\Scripts\mariana.exe new "Your business problem, constraints and desired outcome"
.\.venv\Scripts\mariana.exe run RUN_ID
```

Replace RUN_ID with the ID printed by the new command. For a detailed problem,
use new --problem-file path\to\problem.md instead. Creating a run saves it;
the run command starts model work and displays the dashboard.

## What happens

MB (Astra) writes a research brief and identifies missing information. Three RB
specialists (Astra) propose approaches, then an RB chair compares and combines
them. Three JB critics (Opus 5) challenge the plan, then a JB chair produces the
review and next prompt for RB. This cycle repeats with the previous plan and
critique as context. MB also handles your questions and changes to the brief.

The default settings use three specialists per team, scheduled one at a time.
MB and RB share your OpenAI allowance; JB uses your Claude allowance. The worker
waits at reported usage limits and resumes after the reset or its fallback wait.
It stops at 24 rounds or 72 elapsed hours, or earlier on sustained approval,
a plateau, or a need for human evidence. Scores express model judgment, not
proof that the business will succeed.

## Talk to it while it works

Open a second PowerShell window in the same project folder:

```powershell
.\.venv\Scripts\mariana.exe ask RUN_ID "What is the weakest assumption?"
.\.venv\Scripts\mariana.exe messages RUN_ID
.\.venv\Scripts\mariana.exe steer RUN_ID "Limit the pilot budget to EUR 500."
.\.venv\Scripts\mariana.exe export RUN_ID
```

Ask queues a question for MB; messages shows the brief and answers. Steer changes
the brief at the next round boundary. Export writes the current report under
.mariana\exports\RUN_ID\report.md. Reports are also written when the worker exits.

## Pause, sleep and resume

Press Ctrl+C in the worker window to pause. Continue later with:

```powershell
.\.venv\Scripts\mariana.exe resume RUN_ID
```

Keep the worker terminal open, the laptop awake, and its internet connection
available while researching. Turning off the screen is fine. Laptop sleep
suspends work; closing the lid may trigger sleep. Pause before planned sleep or
shutdown. Saved results survive restarts, although an interrupted request may
need to be repeated. The 72-hour deadline includes time spent asleep or paused.
MarianaBot does not change your Windows power settings or install a background
service for laptop use.

## Local configuration

After your confirmation that usage credits are off, your local mariana.toml has
subscription.overage_disabled = true. This enables new live runs. It records
your confirmation; it does not toggle or independently verify provider billing.
The setting and research data stay outside Git. Default templates still require
each installation to confirm its own billing settings.

For an older paused run created before this change, load the updated settings:

```powershell
.\.venv\Scripts\mariana.exe resume RUN_ID --config mariana.toml
```

No Raspberry Pi or systemd setup is needed. See [CLI operations](operations.md)
for the remaining commands and troubleshooting.
