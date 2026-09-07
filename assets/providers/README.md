# Provider brand assets

The SVG marks in this directory are vendored from [Lobe Icons](https://github.com/lobehub/lobe-icons),
an MIT-licensed AI brand icon collection. They are identification marks for the named services,
not TokenMeter logos or an assertion of affiliation. Brand names and trademarks belong to their
respective owners; the upstream software license does not grant trademark rights.

Source revision: [`4aaf4ee1fb2678a7f989ea570f0f6ce14a9abf75`](https://github.com/lobehub/lobe-icons/tree/4aaf4ee1fb2678a7f989ea570f0f6ce14a9abf75).
Retrieved 2026-09-05. The SVG files are unchanged copies from the upstream
[`packages/static-svg/icons`](https://github.com/lobehub/lobe-icons/tree/4aaf4ee1fb2678a7f989ea570f0f6ce14a9abf75/packages/static-svg/icons)
directory. The complete upstream copyright and license notice is retained in
[`LICENSE.lobe-icons`](LICENSE.lobe-icons).

| File | Displayed brand / product |
| --- | --- |
| `deepseek-color.svg` | DeepSeek |
| `xiaomimimo.svg` | Xiaomi MiMo |
| `codex.svg` | Dedicated Codex mark retained as a source asset; not currently displayed |
| `cursor.svg` | Cursor |
| `openrouter.svg` | OpenRouter |
| `kimi.svg` | Kimi API and Kimi Coding; labels distinguish the two products |
| `githubcopilot.svg` | GitHub Copilot, not Microsoft Copilot |
| `claude-color.svg` | Claude / Claude Code |
| `zai.svg` | GLM / Z.ai |
| `gemini-color.svg` | Gemini CLI / Code Assist |
| `antigravity.svg` | Antigravity CLI; retrieved 2026-09-07 from the same source revision; monochrome variant avoids unsupported SVG masks/filters in Qt |
| `minimax-color.svg` | MiniMax Token Plan |
| `elevenlabs.svg` | ElevenLabs |
| `bailian-color.svg` | Alibaba Cloud Bailian (asset prepared for future support) |
| `openai.svg` | GPT / OpenAI mark used for Codex at the user's request; also prepared for OpenAI API |

The renderer preserves the SVG viewBox proportions and literal brand colors/gradients.
Upstream monochrome `currentColor` marks use the application's light/dark foreground color.
For Kimi, the upstream monochrome variant is used because its colored variant contains a white
letter intended for a dark background. Icons are rendered at multiple pixel densities and loaded
only from local bundled files; no provider website is contacted to display them.

`generic.svg` is an original, neutral connection symbol created for TokenMeter under the project's
license. It is deliberately **not a NayutoAI brand logo**. A verifiable redistributable NayutoAI mark
was not available during this update, so NayutoAI and unknown providers use this generic symbol
with their text name. Missing or damaged files fall back to the application's generic usage icon.

The reference project [CodexBar](https://github.com/steipete/CodexBar) was reviewed for provider and
switching behavior; no CodexBar artwork or implementation is included in this directory.

The complete directory, including these source notes and the upstream license, is included by
`packaging/pyinstaller/TokenMeter.spec`. Adding an SVG asset alone does not enable a provider.
