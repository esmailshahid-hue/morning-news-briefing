# Morning Briefing — Setup Checklist

A private news podcast that makes itself every morning at 5:30 AM Pakistan time and appears in Apple Podcasts on your iPhone. Expected cost: under $1 a month.

**Time needed:** about 60 minutes, once.
**Use a laptop or desktop** for setup. The iPhone is only needed at the end.
**You'll need:** a Google account, a GitHub account (free, created below), and a bank card that works for international online payments. Many Pakistani bank cards have international payments switched off by default; enable it in your banking app first.

> Menu names on Google and GitHub change occasionally. If a button is worded slightly differently from below, look for the closest match.

---

## Part A — Put the code on GitHub (15 min)

GitHub stores the code, runs it every morning for free, and hosts the podcast files.

- [ ] **A1. Create a GitHub account.** Go to https://github.com/signup and follow the steps. Pick your username carefully; it becomes part of your podcast link (`https://USERNAME.github.io/morning-briefing/`).
- [ ] **A2. Create the repository** (a project folder on GitHub).
  1. Click **+** (top right) → **New repository**.
  2. **Repository name:** `morning-briefing`
  3. Select **Public**. The free hosting only works with public repositories. Your API keys stay hidden (Part C), and the episodes are summaries of public news.
  4. Leave "Add a README" **unticked**.
  5. Click **Create repository**.
- [ ] **A3. Upload the files.**
  1. Unzip `morning-briefing.zip` on your computer.
  2. On the new repository page, click the link **uploading an existing file**.
  3. Open the unzipped `morning-briefing` folder, select **everything inside it**, and drag it onto the upload area. Drag the contents, not the folder itself.
  4. Click **Commit changes**.
- [ ] **A4. Check the scheduler file arrived.** In the repository you should see a `.github` folder. Mac and some Windows setups hide folders starting with a dot, so it often doesn't upload. If it's missing:
  1. Click **Add file → Create new file**.
  2. In the name box, type exactly `.github/workflows/daily-briefing.yml`. The slashes create the folders automatically.
  3. Open `setup/daily-briefing.yml` from the unzipped folder in any text editor, copy everything, and paste it into the big box.
  4. Click **Commit changes**.
- [ ] **A5. Allow the scheduler to save files.** Go to **Settings → Actions → General**. Under **Workflow permissions**, choose **Read and write permissions**, then click **Save**.

## Part B — Get your two Google keys (20 min)

- [ ] **B1. Create the Gemini key** (this writes the script).
  1. Go to https://aistudio.google.com/apikey and sign in.
  2. Click **Create API key**. If asked, let it create a new Google Cloud project, or pick one.
  3. Copy the key and paste it into a temporary note. Note which **project** it belongs to; you'll use that project in the next steps.
- [ ] **B2. Turn on billing for that project.** This is needed for the high-quality voice; the voice itself stays within Google's free monthly allowance.
  1. On the AI Studio API keys page, click **Set up billing** next to your key, or go to https://console.cloud.google.com/billing.
  2. Create a billing account, add your card, and link it to the project from B1.
- [ ] **B3. Set a spending alert.**
  1. In https://console.cloud.google.com/billing, open **Budgets & alerts → Create budget**.
  2. Set the amount to **$5** and keep the default email alerts.
  3. Note: this warns you but doesn't stop charges. Normal usage is well under $1.
- [ ] **B4. Switch on the voice service.**
  1. Go to https://console.cloud.google.com/apis/library and make sure the **same project** is selected at the top of the page.
  2. Search **Cloud Text-to-Speech API**, open it, and click **Enable**.
- [ ] **B5. Create the voice key.**
  1. Go to **APIs & Services → Credentials** and click **Create credentials → API key**. Copy the key into your note.
  2. Click the new key (or **Edit API key**). Under **API restrictions**, choose **Restrict key**, tick **Cloud Text-to-Speech API**, and click **Save**. This limits what the key can be used for if it ever leaks.

## Part C — Give the keys to GitHub (5 min)

Secrets are stored encrypted and never appear in your code or to visitors.

- [ ] **C1.** In your repository, go to **Settings → Secrets and variables → Actions**.
- [ ] **C2.** Click **New repository secret**. Set **Name** to `GEMINI_API_KEY`, paste your Gemini key as the **Secret**, and click **Add secret**.
- [ ] **C3.** Add a second secret with **Name** `GOOGLE_TTS_API_KEY` and your voice key as the **Secret**.
- [ ] **C4.** Delete the keys from your temporary note.

## Part D — First test run (5 min)

- [ ] **D1.** Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
- [ ] **D2.** Click **Daily briefing** on the left, then **Run workflow → Run workflow**.
- [ ] **D3.** Wait 3–5 minutes and refresh the page.
  - **Green tick:** it worked. Click the run and scroll down to the summary. You'll see how many stories were collected, which AI model and voice were used, the episode length, and a table showing each news source.
  - **Red cross:** click the run and read the summary, then see *Troubleshooting* below.

## Part E — Switch on the podcast website (3 min)

- [ ] **E1.** Go to **Settings → Pages**.
- [ ] **E2.** Under **Build and deployment**, set **Source** to **Deploy from a branch**. Then set **Branch** to **gh-pages** with folder **/ (root)**, and click **Save**. The `gh-pages` branch only appears after the first successful run in Part D.
- [ ] **E3.** Wait 1–2 minutes and open `https://USERNAME.github.io/morning-briefing/`, replacing USERNAME with your GitHub username in lowercase. You should see the episode with a play button. Listen to a minute to check the voice.

## Part F — Set up your iPhone (5 min)

- [ ] **F1. Follow the show in Apple Podcasts.**
  1. Open **Apple Podcasts** and go to the **Library** tab.
  2. Tap **⋯** (top right) and choose **Follow a Show by URL**.
  3. Enter `https://USERNAME.github.io/morning-briefing/feed.xml` and tap **Follow**.
  4. If you see "an error has occurred", close the app fully and try again. This is a known Apple glitch.
- [ ] **F2. Make new episodes download automatically.** Open the show, tap **⋯ → Settings**, and turn on **Automatic Downloads**. Turn on notifications too if you want an alert each morning.
- [ ] **F3. Optional: start playing when you get in the car.**
  1. Open the **Shortcuts** app and go to **Automation → + → Bluetooth**.
  2. Choose your car's Bluetooth device, set it to **Is Connected**, and select **Run Immediately**.
  3. Add the action **Play Podcast** and pick **Morning Briefing**. If that action isn't offered, use **Open App → Podcasts** instead.

**Done.** From tomorrow, a new episode appears automatically each morning.

---

## Everyday use and changes

To change settings, edit **`config.yaml`** on GitHub: click the file, click the ✏️ pencil icon, make the change, then click **Commit changes**. The next run uses your new settings.

| I want to… | Change this |
|---|---|
| Make it shorter or longer | `target_minutes` under `briefing` |
| Change the voice or accent | `name` under `voice → google_cloud` (options are listed in the file) |
| Make it speak faster | Remove the `#` before `speaking_rate` |
| Add or remove a news source | The `feeds` lists under `sections` |
| Change a section's focus | The `focus` text for that section |
| Keep more past episodes | `keep_episodes` |

**To change the time it runs**, edit `.github/workflows/daily-briefing.yml` and change the `cron` line. GitHub uses UTC, which is Pakistan time minus 5 hours. For example, 06:15 PKT becomes `"15 1 * * *"`. GitHub sometimes starts scheduled runs 10–30 minutes late, so keep at least an hour between the run time and when you leave.

**To make an extra episode right now**, go to **Actions → Daily briefing → Run workflow**. A second run on the same day replaces that day's episode.

## Troubleshooting

GitHub emails you automatically whenever a run fails. Open the failed run and read its summary.

| Message in the summary | What to do |
|---|---|
| `GEMINI_API_KEY is missing` | Redo Part C. The secret name must match exactly. |
| `Every script model failed` with `HTTP 400` or `403` | The Gemini key is wrong or was deleted. Create a new one (B1) and update the secret (C2). |
| `Every script model failed` with `HTTP 404` | Google has retired those model names. Check the current names at https://ai.google.dev/gemini-api/docs/models and update `script_models` in `config.yaml`. |
| `Voice engine google_cloud failed … 403` | The Text-to-Speech API isn't enabled (B4), billing isn't linked (B2), or the key is restricted to the wrong API (B5). The briefing still works using the backup voice. |
| `Every voice engine failed` | Both voices failed. Fix the voice key as above, and check that billing is active. |
| `Only N stories found` | Several news sources were down. It usually fixes itself the next day; the table shows which feeds failed. |
| One or two feeds show ⚠️ FAILED | Harmless; the briefing continues without them. If a feed fails every day, remove it or replace it in `config.yaml`. |
| Green tick but no new episode in Podcasts | Wait 5 minutes, then pull down to refresh the show. Check that Pages is set to `gh-pages` (E2). |
| Error at "Publish to the podcast website" | Redo step A5. |

## What's in this folder

| File | Purpose |
|---|---|
| `briefing.py` | The program: collects news, writes the script, makes the audio, updates the feed |
| `config.yaml` | All your settings |
| `.github/workflows/daily-briefing.yml` | The daily schedule |
| `setup/daily-briefing.yml` | A visible copy of the schedule file, for step A4 |
| `requirements.txt` | Software libraries the program uses |
| `assets/cover.png` | The podcast cover image |

## Privacy notes

- **Hidden:** your API keys are only in GitHub Secrets.
- **Public:** the code, `config.yaml`, and the episodes can be seen by anyone who finds the link. Episodes are summaries of public news, and the feed is marked so it won't appear in Apple's podcast directory. Don't add personal details (such as your holdings) to `config.yaml`.
- **Google's data use:** the account is on Google's paid tier, so Google doesn't use this content to train its models.
