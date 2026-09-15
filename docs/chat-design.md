# Chat interface design

MarianaBot should feel like a conversation at a research desk. The primary action is
writing a problem; the research team remains visible without taking over the page.

- **Palette:** slate background (`#171d24`), raised writing surface (`#202932`),
  paper text (`#d9e0e4`), pencil secondary text (`#a6afb8`), muted sea glass
  focus (`#92c9cb`), amber attention (`#dfba79`). Both providers have equal visual
  weight; amber marks waiting or stale allowance information.
- **Type:** the user's terminal font, with weight and space separating speaker,
  message and activity. No decorative ASCII banner that displaces the conversation.
- **Layout:** one-line masthead and status, full-width conversation, fixed usage
  ledger above an editor that grows with the draft. Five quiet ledger rows show
  both subscriptions even at 80×24. No sidebar, decorative tagline, fake greeting,
  oversized buttons or duplicated team diagram.
- **Signature:** two aligned provider entries connect the conversation to the
  resources doing the work: MB + RB share ChatGPT, JB uses Claude. Reported input
  and output sit above allowance windows and their age. This is a working research
  notebook, with the account ledger always in the same place.
- **Input:** Enter sends; Alt+Enter or Ctrl+J adds a newline. Multiline paste is one
  editable draft. Slash suggestions and F1 help make commands discoverable.
- **Continuity:** local transcript and per-session drafts persist. A separate process
  owns the research lock. Closing chat detaches; pausing is an explicit command.
- **Accessibility:** keyboard navigation, readable contrast, visible focus, no
  continuous decorative animation, and a working 80-column layout. Plain labels
  and whitespace carry hierarchy; terminal font choice belongs to the user.

The September refinement removes the earlier generic chatbot framing. A sidebar
and decorative court diagram competed with the actual conversation. The compact
ledger expresses the real two-provider relationship while leaving room to read
and write. Wide and narrow layouts use the same order, with fewer suggestions on
short terminals so the transcript remains visible.

Layout references: Zellij's [full-width status bar](https://zellij.dev/documentation/status-bar-alias.html)
and [compact layout](https://zellij.dev/tutorials/layouts/#adding-the-zellij-ui-plugins)
demonstrate keeping state in a reserved terminal area. MarianaBot uses that
principle with its own account information and conversation layout.

The web UI skill's commerce layout suggestions are inapplicable to a terminal chat.
The implementation uses Textual's native layout and input widgets. Interaction tests
use its headless Pilot; rendered terminal screenshots are reviewed at wide and narrow
sizes. Existing command-line automation stays available.

Implementation references: [TextArea](https://textual.textualize.io/widgets/text_area/)
and [headless testing](https://textual.textualize.io/guide/testing/).
