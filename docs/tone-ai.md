# Tone AI

The **TONE AI** tab asks an AI for the amp settings that match a song, and lets you take the
suggestions you like into the patch in the editor.  It asks either an OpenAI-style server or
Claude, through the Agent Client Protocol.  It needs an internet connection (or a server of your
own); everything else in KataTone works without one.

## Choosing who to ask

Press the gear and choose under **Ask**:

- **An OpenAI-style server.**  Enter the **server** (default `https://api.openai.com/v1`), the
  **model** and your **API key**.  Any server with an OpenAI-style chat API works: OpenAI, or a
  local one such as Ollama, LM Studio or vLLM (give its base URL; a local server needs no key).
  The `OPENAI_API_KEY` environment variable is used when no key is saved.
- **Claude, through ACP.**  KataTone starts an agent that speaks the
  [Agent Client Protocol](https://agentclientprotocol.com) for each question and asks it for
  the **model** (`sonnet` by default; `opus`, `haiku` or a full model name also work, and an
  empty field uses the agent's own).  It uses your Claude account, so there is no API key to
  enter.  The agent is only asked to answer: it starts in an empty temporary directory,
  KataTone gives it no file or terminal access, asks it to use no tools, and refuses every
  permission it asks for.

  Any other ACP agent can be used the same way, if it lets the model be chosen or with the
  model field empty.

### Getting Claude ready

Under the Claude settings a status line says what is missing, with a button for it:

- **Check** looks at the agent and at your sign-in (`claude auth status`); it is run when the
  dialog opens and asks no question.
- **Install the agent** (when it is not installed) runs `npm install` for
  `@agentclientprotocol/claude-agent-acp` into `claude-agent/` in the data directory, so no
  administrator rights are needed, and then uses it.  It needs Node.js 22 or newer and npm
  (from your distribution, or nodejs.org), and downloads about 400 MB: the agent brings the `claude` program it runs on.  Packages are installed with
  `--ignore-scripts`, so none of them runs code while installing.
- **Sign in** (when you are not signed in) runs `claude auth login --claudeai`, which opens
  the sign-in page in the browser of this machine; the link is also shown in case it does
  not open.  The sign-in is kept in `~/.claude`, as if you had run `claude` yourself.

**Install the agent** and **Sign in** run programs on the machine running KataTone, so they
are offered in the desktop window only.  In a browser session (`katatone-web`) the dialog can
Check and tells what to run on that machine instead:

```bash
npm install -g @agentclientprotocol/claude-agent-acp    # or: npm install --prefix DIR ...
claude auth login
```

In the [Flatpak](flatpak.md) Node.js and npm are part of the package, and the sign-in is kept
in the data directory (`claude-config/`) instead of `~/.claude`, which the sandbox does not
share with your desktop: a sign-in made outside the Flatpak is not seen inside it, so sign in
once there.

On a headless machine such as a Raspberry Pi the sign-in page cannot reach the program from
another machine's browser, so sign in once over SSH (`claude auth login`).  The agent command
(default `claude-agent-acp`; the installed one is under the data directory; `npx -y
@agentclientprotocol/claude-agent-acp` works without installing) can be changed in the dialog
in the desktop window, but in a browser session only in `ai.json`.

The settings are kept in `ai.json` in the data directory, readable by you only.

## Asking

Type the song in the **Song** field, as you would in a search box: a title, or a title and
   artist; typos are fine, the model works out which song you mean.  Press **Ask** (or Enter).

What is sent: the text you typed, the amp model KataTone is connected to (for example
`KATANA-50 Gen 3`), and instructions with the names and ranges of the amp's parameters.  Nothing
from your patches or your computer is sent.  The line under the search field shows where the
question goes.  An answer takes a few seconds (about 8 s for Sonnet through ACP).

## The suggestion

The model answers with the song it matched (and how sure it is), the original rig, how it
translated it to the amp, and settings for: the **amp**, **booster**, **mod**, **FX**,
**delay**, **reverb**, **EQ 1** (parametric or 10-band) and the **noise suppressor**.  Effects
are written to the colour variation (green, red or yellow) the patch has selected; the card
title says which.

Each setting is shown as `current → suggested`.  Nothing changes until you press **Apply**:

- tick or untick single settings, or **Take this section** for a whole card; **Select all** and
  **Select none** do the same for everything.  Settings that are already what the model
  suggests start unticked;
- a mod, FX or EQ **type** and its settings go together: if you do not take a new type, the
  settings written for it are greyed out ("needs the new type");
- a section that switches an effect **off** is a setting like any other: untick it to keep the
  effect on.

**Apply** writes the ticked settings to the patch in the editor as one step, sends them to the
amp when it is connected live, and **Ctrl+Z** takes them all back.  Save the result from the
**PATCH** tab as usual.  Applying to another patch than the one asked about is refused (the
suggestion is shown again against the new patch).

## When the answer is incomplete

The model is told it may leave out what it is not sure of, and models do.  The tab copes:

- a section or setting that is missing is left alone, and the tab lists the sections the model
  left out;
- a name that is a little different ("t scream", "Spring", "500 Hz") is matched to the amp's own;
- a number out of range is clamped, and the row says "clamped from …";
- anything that cannot be used (an unknown name, a setting of another effect) is listed under
  "could not be used" with the reason, and the rest is shown;
- a reply that is not JSON is asked for once more; after that the error is shown.

The model's raw reply is under **The model's reply**.  Press **Ask** again for another try.

## Limits

- The suggestion is the model's recollection of a recording, not a measurement: it can be
  wrong, and it can misjudge the song.  Treat it as a starting point, and use your ears (the
  [Analyzer](analyzer.md) can fit EQ to a reference recording).
- The tab suggests one patch: the song's main guitar sound.  A solo or clean part is mentioned
  in the notes only.
- Mod and FX get the type and the settings of that effect; the EQ is written to EQ 1, and EQ 2
  is left alone.
- Each question uses a few thousand tokens of your server's quota (or your Claude plan's).
- The tab does not keep past suggestions; the settings you apply are in the patch.
