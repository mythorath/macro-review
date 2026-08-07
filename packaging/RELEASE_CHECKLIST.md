# Portable release checklist (clean Windows 10/11 VM)

Use this before promoting a preview commit to a `vX.Y.Z` stable tag.

## Prep
- [ ] Fresh Windows 10/11 VM (no repo checkout, no existing MacroReview AppData)
- [ ] Download the preview ZIP from the GitHub `preview` release
- [ ] Verify SHA-256 against the published `.sha256` file
- [ ] Note SmartScreen prompt (unsigned builds): More info → Run anyway

## Missing Python path
- [ ] Ensure `python` / `py` are NOT on PATH
- [ ] Launch `MacroReview.exe`
- [ ] Open Setup → Run setup
- [ ] Confirm a clear error asking for 64-bit Python 3.11+

## Happy path
- [ ] Install 64-bit Python 3.11+ from python.org (add to PATH)
- [ ] Launch `MacroReview.exe`
- [ ] Setup → Check system (GPU/Ollama/disk cards populate)
- [ ] Setup → Run setup (managed venv + deps; Ollama optional with skip flags)
- [ ] Restart the app; status shows Ready / Library opens
- [ ] Library: pick a tiny folder, Start with Limit 1–3
- [ ] Results: thumbnails, filters, detail pane, crop overlay when present
- [ ] Settings → About shows version/channel/commit
- [ ] Settings → Check for updates opens the GitHub release page (or reports up to date)

## Update / replace portable folder
- [ ] Quit the app
- [ ] Extract a newer preview ZIP beside or over the old folder
- [ ] Launch new `MacroReview.exe`
- [ ] Confirm `%LOCALAPPDATA%\MacroReview` settings/data/venv are reused
- [ ] Confirm Results still load without re-running the whole pipeline

## Promote to stable
- [ ] Automated CI is green for the commit
- [ ] This checklist passed on a clean VM
- [ ] `version_info.APP_VERSION` matches the intended tag
- [ ] Create annotated tag `vX.Y.Z` on that commit and push
- [ ] Confirm the immutable GitHub Release assets appear
