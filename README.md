# HTML to APK Converter v4.3
### Developed by SANTHOSH A · SanStudio
![HTMLtoAPK Screenshot](https://github.com/A-Santhosh-Hub/HTML_to_APK_Converter-Using-Python/blob/main/HTMLtoAPK.png)
## What's New — Local Web Build Console + Fixes

- **New:** Browser-based Build Console (drag-and-drop ZIP/icon upload,
  live build progress, one-click APK download) — runs locally via
  `start_build_console.sh` / `.bat`. The terminal-based wizard still works
  exactly as before.
- **Fixed:** `gradlew` / `gradlew.bat` were placeholder stubs pointing at a
  `gradlew.jar` that was never created, so the Gradle compile step could
  never succeed on any machine. These now use the real Gradle Wrapper
  bootstrap, and the matching `gradle-wrapper.jar` is fetched automatically.
- **Fixed:** the app icon was referenced in the manifest
  (`@mipmap/ic_launcher`) but never actually generated, which would fail
  Android resource resolution at build time. Icons are now generated at all
  required densities, either from an uploaded image or a safe default.
- **Fixed (Windows):** even after resolving the absolute path to
  `gradlew.bat`, builds run from a folder containing spaces or parentheses
  (e.g. a Windows auto-renamed download folder like `MyProject (1)`) failed
  with `The system cannot find the path specified.` This happens because
  Windows transparently routes `.bat` execution through an implicit
  `cmd.exe`, which can mis-handle those characters. The fix invokes
  `cmd.exe /c` explicitly, passing the batch file path as its own list item
  (not as a manually quoted string) so Python's own Windows command-line
  quoting handles spaces/parentheses correctly without any double-quoting.
- **Fixed:** the generated `gradlew` (sh) and `gradlew.bat` scripts stored
  their default JVM memory options as a quoted string and then expanded it
  unquoted, which caused `java` to receive literal quote characters as part
  of the argument and fail with `Could not find or load main class "-Xmx64m"`.
  Both scripts now pass `-Xmx64m -Xms64m` directly.
- **New:** orientation (Auto / Portrait / Landscape) is now actually written
  into `AndroidManifest.xml` — previously collected nowhere and not applied.

## What's New in v4.3 — Multi-File Project Support

Previously the converter only bundled `index.html` into the APK.
**v4.3 now supports full multi-file web projects** — including external CSS,
JavaScript files, images, fonts, JSON data files, and more.

---

## Supported Input Structure

Place all your project files inside `input_project/`:

```
input_project/
├── index.html          ← entry point (required)
├── style.css           ← external stylesheet
├── script.js           ← external JavaScript
├── css/
│   └── theme.css
├── js/
│   ├── app.js
│   └── utils.js
├── assets/
│   ├── logo.png
│   ├── hero.webp
│   └── fonts/
│       └── MyFont.woff2
└── data/
    └── config.json
```

All files are automatically detected and copied into the APK's `assets/` folder,
**preserving your subfolder structure exactly**. Your HTML's relative paths like
`<link href="css/style.css">` or `<img src="assets/logo.png">` will work perfectly
inside the WebView.

---

## Supported File Types

| Category   | Extensions                                    |
|------------|-----------------------------------------------|
| Stylesheets | `.css`                                       |
| Scripts     | `.js`, `.mjs`                                |
| Images      | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.ico`, `.bmp` |
| Fonts       | `.ttf`, `.otf`, `.woff`, `.woff2`, `.eot`   |
| Media       | `.mp4`, `.webm`, `.ogg`, `.mp3`, `.wav`, `.flac` |
| Data        | `.json`, `.xml`, `.txt`, `.pdf`              |

---

## How Asset Detection Works (4 Phases)

1. **Directory scan** — every file in `input_project/` with a recognized extension
2. **HTML parse** — `<link href>`, `<script src>`, `<img src>`, `<source src>`, etc.
3. **CSS deep-scan** — `url()` references in stylesheets (fonts, background images)
4. **JS deep-scan** — `fetch()` and `import()` calls referencing local files

Feature detection (localStorage, internet, media, etc.) is also **extended into
linked CSS/JS files**, not just the HTML source.

---

## Usage — Web Build Console (new)

Instead of running the converter from the terminal, you can use a local
browser-based Build Console:

1. **Windows:** double-click `start_build_console.bat`
2. **Mac/Linux:** double-click `start_build_console.sh` (or run `./start_build_console.sh`)

This installs `flask`/`pillow` if missing, starts a local server, and opens
`http://localhost:5000` in your browser automatically.

In the browser:
1. Drop your project ZIP (must contain `index.html` at its root, or inside
   a single top-level folder)
2. Drop an icon image (PNG/JPG/WEBP) — it's resized into every required
   Android icon density automatically
3. Fill in App Name, Package ID, Version Name/Code, and pick an orientation
4. Click **Start Debug Build**

The page shows live build progress and gives you a **Download APK** button
when the build finishes. It calls the exact same `converter.py` pipeline as
the CLI below — same HTML/CSS/JS analysis, same Android project generation —
just through a browser instead of typed prompts.

Requires the same JDK 17+ and Android SDK as the CLI version (see
Requirements below). If no Android SDK is found, the page reports that
clearly instead of failing silently.

---

## Usage — Command Line (original)


1. Drop your project files into `input_project/`
2. Run: `python converter.py`
3. Enter app name, package ID, version when prompted
4. APK is generated at `output/app.apk`

---

## Requirements

- Python 3.10+
- JDK 17+
- Android SDK (set `ANDROID_HOME` or open project in Android Studio)

---

## Project Structure

```
HTML_to_APK_Converter/
├── converter.py            ← main script (shared by CLI and web UI)
├── app.py                  ← local web server for the Build Console
├── templates/
│   └── index.html          ← Build Console web UI
├── start_build_console.sh  ← double-click launcher (Mac/Linux)
├── start_build_console.bat ← double-click launcher (Windows)
├── input_project/          ← YOUR PROJECT FILES GO HERE (CLI mode)
│   └── index.html
├── build/android_project/  ← generated Android project (CLI mode)
├── web_jobs/                ← per-build folders (web UI mode, auto-created)
├── output/                 ← APK output
└── logs/                   ← build logs
```

---

*Developed by SANTHOSH A · SanStudio*
-----
