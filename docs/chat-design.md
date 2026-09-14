# Chat interface design

MarianaBot should feel like a conversation at a research desk. The primary action is
writing a problem; the research team remains visible without taking over the page.

- **Palette:** deep navy background (`#091923`), cool white text (`#e5f1f4`), seafoam
  for MB/RB (`#79dac8`), amber for JB (`#ecc38a`), slate separators (`#29424e`).
- **Type:** the user's terminal font, with weight and space separating speaker,
  message and activity. No decorative ASCII banner that displaces the conversation.
- **Layout:** quiet masthead, flexible transcript, narrow research rail, multiline
  composer anchored at the bottom. Below 100 columns the rail becomes a status line.
- **Signature:** an explicit `MB → RB ⇄ JB` court indicator. Activity is real call
  state; scores remain reviewer scores, never a simulated progress percentage.
- **Input:** Enter sends; Alt+Enter or Ctrl+J adds a newline. Multiline paste is one
  editable draft. Slash suggestions and F1 help make commands discoverable.
- **Continuity:** local transcript and per-session drafts persist. A separate process
  owns the research lock. Closing chat detaches; pausing is an explicit command.
- **Accessibility:** keyboard navigation, readable contrast, visible focus, no
  continuous decorative animation, and a working 80-column layout.

The web UI skill's commerce layout suggestions are inapplicable to a terminal chat.
The implementation uses Textual's native layout and input widgets. Interaction tests
use its headless Pilot; rendered terminal screenshots are reviewed at wide and narrow
sizes. Existing command-line automation stays available.

Implementation references: [TextArea](https://textual.textualize.io/widgets/text_area/)
and [headless testing](https://textual.textualize.io/guide/testing/).
