"""
HTML to APK Builder  v4.3
Developed by SANTHOSH A

NEW IN v4.3:
  - Multi-file project support (CSS, JS, images, fonts, and more)
  - Automatically copies style.css, script.js, and all linked local assets
  - HTML analyzer detects local linked files and copies them all into assets/
  - Subdirectory structure is preserved inside assets/
  - Feature detection extended to scan linked .css and .js files too
  - All v4.0 features retained (wizard, bridge, downloads, preview)
"""

import os, sys, re, shutil, subprocess, platform, logging, stat, time
from pathlib import Path
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.resolve()
INPUT_DIR    = BASE_DIR / "input_project"
BUILD_DIR    = BASE_DIR / "build" / "android_project"
OUTPUT_DIR   = BASE_DIR / "output"
LOG_DIR      = BASE_DIR / "logs"

APP_NAME     = "MyWebApp"
PACKAGE_NAME = "com.santhosh.generatedapp"
VERSION_CODE = 1
VERSION_NAME = "1.0"
MIN_SDK      = 24
TARGET_SDK   = 34
COMPILE_SDK  = 34

# File extensions that are valid local web assets
ASSET_EXTENSIONS = {
    # Stylesheets
    ".css",
    # Scripts
    ".js", ".mjs",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Media
    ".mp4", ".webm", ".ogg", ".mp3", ".wav", ".flac",
    # Data / misc
    ".json", ".xml", ".txt", ".pdf",
}

BANNER = """
+--------------------------------------------------------------+
|         HTML  ->  APK  Builder   v4.3                       |
|         Multi-File Support: CSS · JS · Images · Fonts       |
|         Developed by SANTHOSH A                             |
+--------------------------------------------------------------+
"""

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = LOG_DIR / f"build_{_ts}.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("apk_builder")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1  —  HTML ANALYZER  (extended for multi-file)
# ─────────────────────────────────────────────────────────────────────────────

def _is_local_url(url: str) -> bool:
    """Return True if url is a relative local path (not http/https/data/blob)."""
    if not url:
        return False
    url = url.strip()
    if url.startswith(("http://", "https://", "//", "data:", "blob:",
                        "javascript:", "mailto:", "tel:", "#")):
        return False
    # Must have a file extension that we care about, or no scheme at all
    parsed = urlparse(url)
    return parsed.scheme == ""


def _normalize_local_path(url: str) -> str:
    """Strip query strings and fragments from a local URL path."""
    url = url.strip()
    url = url.split("?")[0].split("#")[0]
    return unquote(url)


class HTMLFeatureDetector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.features = {
            "internet":       False,
            "images":         False,
            "iframe":         False,
            "external_links": False,
            "local_storage":  False,
            "drag_drop":      False,
            "file_chooser":   False,
            "file_download":  False,
            "live_preview":   False,
            "media":          False,
            "dark_mode":      False,
            "clipboard":      False,
            "scripts":        [],
            "external_urls":  [],
            # NEW: local asset paths detected in HTML
            "local_assets":   [],
        }
        self._raw = ""

    def feed_html(self, html: str):
        self._raw = html
        self.feed(html)
        self._post_scan()

    def _add_local_asset(self, path: str):
        """Record a local asset path (deduplicated)."""
        path = _normalize_local_path(path)
        if path and path not in self.features["local_assets"]:
            self.features["local_assets"].append(path)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        src  = a.get("src",  "")
        href = a.get("href", "")

        if tag == "img":
            self.features["images"] = True
            if src.startswith("http"):
                self.features["internet"] = True
            elif _is_local_url(src):
                self._add_local_asset(src)

        elif tag == "script":
            if src.startswith("http"):
                self.features["internet"] = True
                self.features["scripts"].append(src)
            elif _is_local_url(src):
                self._add_local_asset(src)

        elif tag == "link":
            rel  = a.get("rel", "").lower()
            typ  = a.get("type", "").lower()
            if href.startswith("http"):
                self.features["internet"] = True
            elif _is_local_url(href):
                # stylesheet, icon, font preload, manifest, etc.
                self._add_local_asset(href)

        elif tag == "iframe":
            self.features["iframe"] = True
            if src.startswith("http"):
                self.features["internet"] = True
            elif _is_local_url(src):
                self._add_local_asset(src)

        elif tag == "a":
            if "download" in a:
                self.features["file_download"] = True
            if href.startswith("http"):
                self.features["external_links"] = True
                self.features["internet"] = True
                self.features["external_urls"].append(href)
            elif _is_local_url(href):
                # Could be a local downloadable file
                self._add_local_asset(href)

        elif tag in ("video", "audio"):
            self.features["media"] = True

        elif tag == "source":
            # <source src="…"> inside <video>/<audio>
            if _is_local_url(src):
                self._add_local_asset(src)

        elif tag == "input":
            if a.get("type", "").lower() == "file":
                self.features["file_chooser"] = True

    def _post_scan(self):
        h = self._raw
        if re.search(r'localStorage|sessionStorage', h):
            self.features["local_storage"] = True
        if re.search(r'draggable|ondrop|ondragover|["\'"]drop["\']', h):
            self.features["drag_drop"] = True
            self.features["file_chooser"] = True
        if re.search(r'prefers-color-scheme|dark-mode|darkMode|data-theme', h):
            self.features["dark_mode"] = True
        if re.search(r'fetch\s*\(|XMLHttpRequest|axios\.', h):
            self.features["internet"] = True
        if re.search(r'WebSocket', h):
            self.features["internet"] = True
        if re.search(
            r'URL\.createObjectURL|createObjectURL|\.download\s*=|saveAs\s*\('
            r'|FileSaver|Blob\s*\(|data:text|data:application'
            r'|downloadFile|triggerDownload|saveFile|exportFile', h
        ):
            self.features["file_download"] = True
        if re.search(r'window\.open\s*\(|\.open\s*\(\s*["\']', h):
            self.features["live_preview"] = True
        if re.search(r'navigator\.clipboard|execCommand\s*\(\s*["\'"]copy', h):
            self.features["clipboard"] = True

        # Also scan for url() references in inline <style> blocks (fonts, bg images)
        for url_match in re.finditer(r'''url\s*\(\s*['"]?([^'"\)\s]+)['"]?\s*\)''', h):
            candidate = url_match.group(1)
            if _is_local_url(candidate):
                self._add_local_asset(candidate)


def _scan_css_for_assets(css_path: Path, base_dir: Path) -> list:
    """
    Parse a CSS file for url() references (fonts, background images, etc.)
    Returns list of local asset paths relative to base_dir.
    """
    found = []
    try:
        text = css_path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'''url\s*\(\s*['"]?([^'"\)\s]+)['"]?\s*\)''', text):
            candidate = m.group(1).strip()
            if _is_local_url(candidate):
                # Resolve relative to the CSS file's location
                css_dir = css_path.parent
                resolved = (css_dir / _normalize_local_path(candidate)).resolve()
                try:
                    rel = resolved.relative_to(base_dir.resolve())
                    found.append(str(rel))
                except ValueError:
                    pass  # Outside base_dir — skip
    except Exception as e:
        log.debug("CSS scan error for %s: %s" % (css_path, e))
    return found


def _scan_js_for_assets(js_path: Path, base_dir: Path) -> list:
    """
    Parse a JS file for fetch()/import() calls referencing local paths.
    Returns list of local asset paths relative to base_dir.
    """
    found = []
    try:
        text = js_path.read_text(encoding="utf-8", errors="replace")
        # Match: fetch('./data.json'), import('./module.js'), etc.
        for m in re.finditer(
            r'''(?:fetch|import)\s*\(\s*['"]([^'"]+)['"]\s*\)''', text
        ):
            candidate = m.group(1).strip()
            if _is_local_url(candidate):
                js_dir = js_path.parent
                resolved = (js_dir / _normalize_local_path(candidate)).resolve()
                try:
                    rel = resolved.relative_to(base_dir.resolve())
                    found.append(str(rel))
                except ValueError:
                    pass
    except Exception as e:
        log.debug("JS scan error for %s: %s" % (js_path, e))
    return found


def collect_all_assets(input_dir: Path, html_features: dict) -> list:
    """
    Collect all local asset files to copy into the APK's assets/ folder.

    Strategy:
    1. Include ALL files in input_project/ automatically (any extension in ASSET_EXTENSIONS)
    2. Also include anything explicitly detected in HTML/CSS/JS scans
    3. Deduplicate by resolved path

    Returns list of Path objects (absolute), relative to input_dir.
    """
    log.info("=== STEP 2: Collecting local assets ===")
    seen   = set()
    assets = []   # list of (abs_path, rel_path_str)

    def _add(abs_path: Path, rel_str: str):
        key = str(abs_path.resolve())
        if key not in seen and abs_path.exists() and abs_path.is_file():
            seen.add(key)
            assets.append((abs_path, rel_str))
            log.info("   + %s" % rel_str)

    # ── Phase 1: Walk entire input_project directory ───────────────────────
    log.info("   [Phase 1] Scanning input_project/ for all web asset files...")
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "index.html":
            continue  # always handled separately
        suffix = path.suffix.lower()
        if suffix in ASSET_EXTENSIONS:
            rel = str(path.relative_to(input_dir))
            _add(path, rel)

    # ── Phase 2: Explicit paths detected in HTML ───────────────────────────
    log.info("   [Phase 2] Processing HTML-detected local asset paths...")
    for rel_str in html_features.get("local_assets", []):
        abs_path = (input_dir / rel_str).resolve()
        try:
            rel = str(abs_path.relative_to(input_dir.resolve()))
            _add(abs_path, rel)
        except ValueError:
            log.warning("   [!] Asset outside input_project, skipped: %s" % rel_str)

    # ── Phase 3: Deep scan of CSS files for url() references ──────────────
    log.info("   [Phase 3] Deep-scanning CSS files for url() references...")
    css_files = [p for p, _ in assets if p.suffix.lower() == ".css"]
    for css_path in css_files:
        sub_assets = _scan_css_for_assets(css_path, input_dir)
        for rel_str in sub_assets:
            abs_path = (input_dir / rel_str).resolve()
            try:
                rel = str(abs_path.relative_to(input_dir.resolve()))
                _add(abs_path, rel)
            except ValueError:
                pass

    # ── Phase 4: Deep scan of JS files for fetch/import paths ─────────────
    log.info("   [Phase 4] Deep-scanning JS files for fetch/import paths...")
    js_files = [p for p, _ in assets if p.suffix.lower() in (".js", ".mjs")]
    for js_path in js_files:
        sub_assets = _scan_js_for_assets(js_path, input_dir)
        for rel_str in sub_assets:
            abs_path = (input_dir / rel_str).resolve()
            try:
                rel = str(abs_path.relative_to(input_dir.resolve()))
                _add(abs_path, rel)
            except ValueError:
                pass

    # ── Also scan CSS/JS files for feature detection ───────────────────────
    _extend_features_from_assets(html_features, assets)

    log.info("   Total local assets: %d file(s)" % len(assets))
    return assets


def _extend_features_from_assets(features: dict, assets: list):
    """
    Scan linked CSS/JS content to augment feature detection
    (e.g. localStorage used only in script.js, not inline HTML).
    """
    for path, _ in assets:
        suffix = path.suffix.lower()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if suffix == ".css":
            if re.search(r'prefers-color-scheme', text):
                features["dark_mode"] = True

        elif suffix in (".js", ".mjs"):
            if re.search(r'localStorage|sessionStorage', text):
                features["local_storage"] = True
            if re.search(r'fetch\s*\(|XMLHttpRequest|axios\.', text):
                features["internet"] = True
            if re.search(r'WebSocket', text):
                features["internet"] = True
            if re.search(
                r'URL\.createObjectURL|createObjectURL|\.download\s*=|saveAs\s*\('
                r'|FileSaver|Blob\s*\(|downloadFile|triggerDownload', text
            ):
                features["file_download"] = True
            if re.search(r'window\.open\s*\(', text):
                features["live_preview"] = True
            if re.search(r'navigator\.clipboard|execCommand\s*\(\s*["\'"]copy', text):
                features["clipboard"] = True
            if re.search(r'draggable|ondrop|ondragover', text):
                features["drag_drop"] = True


def analyze_html(html_path: Path) -> dict:
    log.info("=== STEP 1: Analyzing HTML file ===")
    content = html_path.read_text(encoding="utf-8", errors="replace")
    det = HTMLFeatureDetector()
    det.feed_html(content)
    f = det.features
    log.info("Detected features:")
    for k, v in f.items():
        if isinstance(v, bool):
            log.info("   %-22s %s" % (k, "YES" if v else "no"))
    if f["local_assets"]:
        log.info("   Local assets detected in HTML: %s" % ", ".join(f["local_assets"]))
    return f


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2  —  ASSET FILE: bridge.js
# ─────────────────────────────────────────────────────────────────────────────
BRIDGE_JS = r"""
/*
 * bridge.js  —  Android WebView helper
 * Developed by SANTHOSH A
 *
 * Injected into every page after load.
 * Provides:
 *   1. window.open() -> Android.openPreview(html)  (Live Preview)
 *   2. <a download href="blob:..."> -> Android.downloadBase64()
 *   3. <a download href="data:..."> -> Android.downloadBase64()
 */
(function () {
  'use strict';

  /* ── 1. Patch window.open ──────────────────────────────────── */
  var _origOpen = window.open;

  window.open = function (url, target, features) {
    if (!url || url === '' || url === 'about:blank') {
      var captured = '';
      var fakeWin = {
        document: {
          write:   function (h) { captured += h; },
          writeln: function (h) { captured += h + '\n'; },
          close:   function ()  {
            if (typeof Android !== 'undefined') {
              Android.openPreview(captured);
            }
          }
        },
        close: function () {}
      };
      return fakeWin;
    }

    if (url.indexOf('blob:') === 0) {
      fetch(url)
        .then(function (r) { return r.text(); })
        .then(function (html) {
          if (typeof Android !== 'undefined') {
            Android.openPreview(html);
          }
        })
        .catch(function () {
          _origOpen.call(window, url, target, features);
        });
      return null;
    }

    return _origOpen.call(window, url, target, features);
  };

  /* ── 2. Intercept <a download> clicks ─────────────────────── */
  document.addEventListener('click', function (e) {
    var node = e.target;
    while (node && node.tagName !== 'A') {
      node = node.parentElement;
    }
    if (!node || !node.hasAttribute('download')) return;

    var href  = node.href  || '';
    var fname = node.getAttribute('download') || 'download';
    if (!fname || fname.trim() === '') fname = 'download';

    if (href.indexOf('blob:') === 0) {
      e.preventDefault();
      e.stopPropagation();
      fetch(href)
        .then(function (r) { return r.blob(); })
        .then(function (b) {
          var reader = new FileReader();
          reader.onload = function () {
            if (typeof Android !== 'undefined') {
              Android.downloadBase64(
                reader.result,
                fname,
                b.type || 'application/octet-stream'
              );
            }
          };
          reader.readAsDataURL(b);
        })
        .catch(function (err) {
          if (typeof Android !== 'undefined') {
            Android.showToast('Download error: ' + err);
          }
        });
      return;
    }

    if (href.indexOf('data:') === 0) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof Android !== 'undefined') {
        Android.downloadBase64(href, fname, '');
      }
    }
  }, true);

})();
"""


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3  —  ANDROID FILE GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def gen_manifest(pkg: str, app_name: str, orientation: str = "auto") -> str:
    # Maps the web UI's Auto/Portrait/Landscape choice to the Android
    # manifest attribute. "auto" maps to "unspecified" so the system/device
    # decides, matching how the option behaves in the build console UI.
    orientation_map = {
        "auto": "unspecified",
        "portrait": "portrait",
        "landscape": "landscape",
    }
    android_orientation = orientation_map.get((orientation or "auto").lower(), "unspecified")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    package="' + pkg + '">\n'
        '\n'
        '    <!-- Networking -->\n'
        '    <uses-permission android:name="android.permission.INTERNET" />\n'
        '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />\n'
        '\n'
        '    <!-- Storage (maxSdkVersion avoids popup on Android 10+) -->\n'
        '    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE"\n'
        '        android:maxSdkVersion="32" />\n'
        '    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE"\n'
        '        android:maxSdkVersion="29" />\n'
        '\n'
        '    <queries>\n'
        '        <package android:name="com.whatsapp" />\n'
        '        <package android:name="com.whatsapp.w4b" />\n'
        '        <package android:name="com.google.android.apps.nbu.paisa.user" />\n'
        '        <package android:name="net.one97.paytm" />\n'
        '        <package android:name="com.phonepe.app" />\n'
        '        <package android:name="org.telegram.messenger" />\n'
        '        <package android:name="com.instagram.android" />\n'
        '        <package android:name="com.google.android.youtube" />\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.VIEW" />\n'
        '            <data android:scheme="https" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.VIEW" />\n'
        '            <data android:scheme="http" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.VIEW" />\n'
        '            <data android:scheme="whatsapp" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.VIEW" />\n'
        '            <data android:scheme="upi" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.DIAL" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.CALL" />\n'
        '            <data android:scheme="tel" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.SENDTO" />\n'
        '            <data android:scheme="mailto" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.VIEW" />\n'
        '            <data android:scheme="tg" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.VIEW" />\n'
        '            <data android:scheme="geo" />\n'
        '        </intent>\n'
        '        <intent>\n'
        '            <action android:name="android.intent.action.VIEW" />\n'
        '            <data android:scheme="market" />\n'
        '        </intent>\n'
        '    </queries>\n'
        '\n'
        '    <application\n'
        '        android:allowBackup="true"\n'
        '        android:icon="@mipmap/ic_launcher"\n'
        '        android:label="' + app_name + '"\n'
        '        android:roundIcon="@mipmap/ic_launcher_round"\n'
        '        android:supportsRtl="true"\n'
        '        android:theme="@style/AppTheme"\n'
        '        android:usesCleartextTraffic="true"\n'
        '        android:requestLegacyExternalStorage="true"\n'
        '        android:networkSecurityConfig="@xml/network_security_config">\n'
        '\n'
        '        <activity\n'
        '            android:name=".MainActivity"\n'
        '            android:exported="true"\n'
        '            android:screenOrientation="' + android_orientation + '"\n'
        '            android:windowSoftInputMode="adjustResize"\n'
        '            android:configChanges="orientation|screenSize|keyboardHidden|keyboard">\n'
        '            <intent-filter>\n'
        '                <action android:name="android.intent.action.MAIN" />\n'
        '                <category android:name="android.intent.category.LAUNCHER" />\n'
        '            </intent-filter>\n'
        '        </activity>\n'
        '\n'
        '        <provider\n'
        '            android:name="androidx.core.content.FileProvider"\n'
        '            android:authorities="' + pkg + '.fileprovider"\n'
        '            android:exported="false"\n'
        '            android:grantUriPermissions="true">\n'
        '            <meta-data\n'
        '                android:name="android.support.FILE_PROVIDER_PATHS"\n'
        '                android:resource="@xml/file_provider_paths" />\n'
        '        </provider>\n'
        '\n'
        '    </application>\n'
        '\n'
        '</manifest>\n'
    )


def gen_main_activity(pkg: str) -> str:
    lines = [
        "package " + pkg + ";",
        "",
        "import android.annotation.SuppressLint;",
        "import android.app.AlertDialog;",
        "import android.content.ContentValues;",
        "import android.content.Intent;",
        "import android.net.Uri;",
        "import android.os.Build;",
        "import android.os.Bundle;",
        "import android.os.Environment;",
        "import android.provider.MediaStore;",
        "import android.util.Base64;",
        "import android.util.Log;",
        "import android.webkit.JsPromptResult;",
        "import android.webkit.JsResult;",
        "import android.webkit.ValueCallback;",
        "import android.webkit.WebChromeClient;",
        "import android.webkit.WebResourceRequest;",
        "import android.webkit.WebSettings;",
        "import android.webkit.WebView;",
        "import android.webkit.WebViewClient;",
        "import android.widget.FrameLayout;",
        "import android.widget.Toast;",
        "import androidx.appcompat.app.AppCompatActivity;",
        "import androidx.webkit.WebSettingsCompat;",
        "import androidx.webkit.WebViewFeature;",
        "import java.io.*;",
        "import java.net.HttpURLConnection;",
        "import java.net.URL;",
        "",
        "/**",
        " * MainActivity — HTML to APK Builder v4.3",
        " * Developed by SANTHOSH A",
        " *",
        " * Multi-file project support: all assets (CSS/JS/images/fonts)",
        " * are bundled in assets/ and loaded relative to index.html.",
        " */",
        "public class MainActivity extends AppCompatActivity {",
        "",
        "    private static final String TAG = \"APKBuilder\";",
        "    private WebView webView;",
        "    private ValueCallback<Uri[]> mFilePathCallback;",
        "    private String bridgeJs = null;",
        "",
        "    // =========================================================",
        "    // JavaScript -> Java bridge",
        "    // =========================================================",
        "    public class AndroidBridge {",
        "",
        "        @android.webkit.JavascriptInterface",
        "        public void downloadBase64(String base64Data, String fileName, String mimeType) {",
        "            Log.d(TAG, \"downloadBase64: \" + fileName);",
        "            try {",
        "                byte[] bytes;",
        "                if (base64Data.contains(\",\")) {",
        "                    String pure = base64Data.substring(base64Data.indexOf(\",\") + 1);",
        "                    bytes = Base64.decode(pure, Base64.DEFAULT);",
        "                } else {",
        "                    bytes = Base64.decode(base64Data, Base64.DEFAULT);",
        "                }",
        "                saveBytes(bytes, fileName, mimeType);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"downloadBase64 failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }",
        "",
        "        @android.webkit.JavascriptInterface",
        "        public void downloadText(String text, String fileName, String mimeType) {",
        "            Log.d(TAG, \"downloadText: \" + fileName);",
        "            try {",
        "                byte[] bytes = text.getBytes(\"UTF-8\");",
        "                String safeName = (fileName != null && !fileName.isEmpty()) ? fileName : \"download.txt\";",
        "                String safeMime = (mimeType != null && !mimeType.isEmpty()) ? mimeType : \"text/plain\";",
        "                saveBytes(bytes, safeName, safeMime);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"downloadText failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }",
        "",
        "        @android.webkit.JavascriptInterface",
        "        public void showToast(String message) {",
        "            showToastOnUi(message);",
        "        }",
        "",
        "        @android.webkit.JavascriptInterface",
        "        public void openPreview(String htmlContent) {",
        "            Log.d(TAG, \"openPreview: \" + (htmlContent != null ? htmlContent.length() : 0) + \" chars\");",
        "            final String html = htmlContent;",
        "            runOnUiThread(new Runnable() {",
        "                @Override public void run() { showPreviewDialog(html); }",
        "            });",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // onCreate",
        "    // =========================================================",
        "    @SuppressLint({\"SetJavaScriptEnabled\", \"AddJavascriptInterface\"})",
        "    @Override",
        "    protected void onCreate(Bundle savedInstanceState) {",
        "        super.onCreate(savedInstanceState);",
        "        setContentView(R.layout.activity_main);",
        "",
        "        bridgeJs = loadAssetText(\"bridge.js\");",
        "",
        "        webView = findViewById(R.id.webview);",
        "        WebSettings s = webView.getSettings();",
        "",
        "        s.setJavaScriptEnabled(true);",
        "        s.setDomStorageEnabled(true);",
        "        s.setDatabaseEnabled(true);",
        "        s.setAllowFileAccess(true);",
        "        s.setAllowContentAccess(true);",
        "        s.setAllowFileAccessFromFileURLs(true);",
        "        s.setAllowUniversalAccessFromFileURLs(true);",
        "        s.setMediaPlaybackRequiresUserGesture(false);",
        "        s.setLoadWithOverviewMode(true);",
        "        s.setUseWideViewPort(true);",
        "        s.setSupportZoom(true);",
        "        s.setBuiltInZoomControls(true);",
        "        s.setDisplayZoomControls(false);",
        "        s.setTextZoom(100);",
        "        s.setCacheMode(WebSettings.LOAD_DEFAULT);",
        "        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);",
        "",
        "        if (WebViewFeature.isFeatureSupported(WebViewFeature.FORCE_DARK)) {",
        "            WebSettingsCompat.setForceDark(s, WebSettingsCompat.FORCE_DARK_OFF);",
        "        }",
        "",
        "        webView.addJavascriptInterface(new AndroidBridge(), \"Android\");",
        "",
        "        webView.setWebViewClient(new WebViewClient() {",
        "            @Override",
        "            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {",
        "                String url = request.getUrl().toString();",
        "",
        "                if (url.startsWith(\"blob:\")) {",
        "                    interceptBlobDownload(url);",
        "                    return true;",
        "                }",
        "",
        "                if (!url.startsWith(\"http://\") && !url.startsWith(\"https://\")",
        "                        && !url.startsWith(\"file://\") && !url.startsWith(\"data:\")",
        "                        && !url.startsWith(\"javascript:\")) {",
        "                    return openExternalScheme(url);",
        "                }",
        "",
        "                return false;",
        "            }",
        "",
        "            @Override",
        "            public void onPageFinished(WebView view, String url) {",
        "                if (bridgeJs != null && !bridgeJs.isEmpty()) {",
        "                    view.evaluateJavascript(bridgeJs, null);",
        "                }",
        "            }",
        "",
        "            @Override",
        "            public void onReceivedError(WebView view, WebResourceRequest request,",
        "                                        android.webkit.WebResourceError error) {",
        "                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {",
        "                    int code = error.getErrorCode();",
        "                    if (code == ERROR_UNKNOWN || code == -10) return;",
        "                }",
        "                super.onReceivedError(view, request, error);",
        "            }",
        "        });",
        "",
        "        webView.setWebChromeClient(new WebChromeClient() {",
        "",
        "            @Override",
        "            public boolean onShowFileChooser(WebView wv,",
        "                    ValueCallback<Uri[]> filePathCallback,",
        "                    FileChooserParams fileChooserParams) {",
        "                if (mFilePathCallback != null) {",
        "                    mFilePathCallback.onReceiveValue(null);",
        "                }",
        "                mFilePathCallback = filePathCallback;",
        "                Intent intent = fileChooserParams.createIntent();",
        "                try {",
        "                    startActivityForResult(intent, 1001);",
        "                } catch (Exception e) {",
        "                    mFilePathCallback = null;",
        "                    return false;",
        "                }",
        "                return true;",
        "            }",
        "",
        "            @Override",
        "            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {",
        "                new AlertDialog.Builder(MainActivity.this)",
        "                    .setMessage(message)",
        "                    .setPositiveButton(\"OK\", (d, w) -> result.confirm())",
        "                    .setOnCancelListener(d -> result.cancel())",
        "                    .show();",
        "                return true;",
        "            }",
        "",
        "            @Override",
        "            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {",
        "                new AlertDialog.Builder(MainActivity.this)",
        "                    .setMessage(message)",
        "                    .setPositiveButton(\"OK\",     (d, w) -> result.confirm())",
        "                    .setNegativeButton(\"Cancel\", (d, w) -> result.cancel())",
        "                    .setOnCancelListener(d -> result.cancel())",
        "                    .show();",
        "                return true;",
        "            }",
        "",
        "            @Override",
        "            public boolean onJsPrompt(WebView view, String url, String message,",
        "                                      String defaultValue, JsPromptResult result) {",
        "                android.widget.EditText input = new android.widget.EditText(MainActivity.this);",
        "                input.setText(defaultValue);",
        "                new AlertDialog.Builder(MainActivity.this)",
        "                    .setMessage(message)",
        "                    .setView(input)",
        "                    .setPositiveButton(\"OK\",     (d, w) -> result.confirm(input.getText().toString()))",
        "                    .setNegativeButton(\"Cancel\", (d, w) -> result.cancel())",
        "                    .setOnCancelListener(d -> result.cancel())",
        "                    .show();",
        "                return true;",
        "            }",
        "        });",
        "",
        "        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {",
        "            Log.d(TAG, \"DownloadListener: \" + url.substring(0, Math.min(60, url.length())));",
        "            if (url.startsWith(\"data:\")) {",
        "                handleDataUriDownload(url, mimeType, contentDisposition);",
        "            } else if (url.startsWith(\"blob:\")) {",
        "                interceptBlobDownload(url);",
        "            } else {",
        "                downloadUrlInBackground(url, userAgent, mimeType, contentDisposition);",
        "            }",
        "        });",
        "",
        "        // Load index.html — all sibling CSS/JS/images load automatically",
        "        // because WebView resolves file:///android_asset/ relative URLs",
        "        webView.loadUrl(\"file:///android_asset/index.html\");",
        "    }",
        "",
        "    // =========================================================",
        "    // Live Preview dialog",
        "    // =========================================================",
        "    private void showPreviewDialog(String htmlContent) {",
        "        WebView preview = new WebView(this);",
        "        WebSettings ps = preview.getSettings();",
        "        ps.setJavaScriptEnabled(true);",
        "        ps.setDomStorageEnabled(true);",
        "        ps.setAllowFileAccess(true);",
        "        ps.setAllowUniversalAccessFromFileURLs(true);",
        "        ps.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);",
        "        preview.setWebViewClient(new WebViewClient());",
        "",
        "        String html = (htmlContent != null && !htmlContent.trim().isEmpty())",
        "            ? htmlContent : \"<html><body><p>Empty preview</p></body></html>\";",
        "",
        "        preview.loadDataWithBaseURL(",
        "            \"file:///android_asset/\",",
        "            html,",
        "            \"text/html\",",
        "            \"UTF-8\",",
        "            null",
        "        );",
        "",
        "        FrameLayout container = new FrameLayout(this);",
        "        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(",
        "            FrameLayout.LayoutParams.MATCH_PARENT,",
        "            FrameLayout.LayoutParams.MATCH_PARENT",
        "        );",
        "        preview.setLayoutParams(lp);",
        "        container.addView(preview);",
        "",
        "        AlertDialog dlg = new AlertDialog.Builder(this)",
        "            .setTitle(\"Live Preview\")",
        "            .setView(container)",
        "            .setPositiveButton(\"Close\", null)",
        "            .create();",
        "        dlg.show();",
        "        if (dlg.getWindow() != null) {",
        "            dlg.getWindow().setLayout(",
        "                android.view.WindowManager.LayoutParams.MATCH_PARENT,",
        "                (int)(getResources().getDisplayMetrics().heightPixels * 0.92)",
        "            );",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // External URI scheme handler",
        "    // =========================================================",
        "    private boolean openExternalScheme(String url) {",
        "        Log.d(TAG, \"openExternalScheme: \" + url);",
        "        if (url.startsWith(\"whatsapp://\")) {",
        "            try {",
        "                Intent wa = new Intent(Intent.ACTION_VIEW);",
        "                wa.setData(android.net.Uri.parse(url));",
        "                wa.setPackage(\"com.whatsapp\");",
        "                wa.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "                startActivity(wa);",
        "                return true;",
        "            } catch (Exception e1) {",
        "                try {",
        "                    Intent wab = new Intent(Intent.ACTION_VIEW);",
        "                    wab.setData(android.net.Uri.parse(url));",
        "                    wab.setPackage(\"com.whatsapp.w4b\");",
        "                    wab.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "                    startActivity(wab);",
        "                    return true;",
        "                } catch (Exception e2) {",
        "                    try {",
        "                        Intent waAny = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url));",
        "                        waAny.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "                        startActivity(waAny);",
        "                        return true;",
        "                    } catch (Exception e3) {",
        "                        Toast.makeText(this, \"WhatsApp not installed\", Toast.LENGTH_SHORT).show();",
        "                        return true;",
        "                    }",
        "                }",
        "            }",
        "        }",
        "        if (url.startsWith(\"upi://\")) {",
        "            try {",
        "                Intent upi = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url));",
        "                upi.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "                startActivity(upi);",
        "            } catch (Exception e) {",
        "                Toast.makeText(this, \"No UPI app found\", Toast.LENGTH_SHORT).show();",
        "            }",
        "            return true;",
        "        }",
        "        if (url.startsWith(\"tel:\")) {",
        "            try {",
        "                Intent tel = new Intent(Intent.ACTION_DIAL, android.net.Uri.parse(url));",
        "                tel.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "                startActivity(tel);",
        "            } catch (Exception e) {",
        "                Toast.makeText(this, \"No phone app found\", Toast.LENGTH_SHORT).show();",
        "            }",
        "            return true;",
        "        }",
        "        if (url.startsWith(\"mailto:\")) {",
        "            try {",
        "                Intent mail = new Intent(Intent.ACTION_SENDTO, android.net.Uri.parse(url));",
        "                mail.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "                startActivity(mail);",
        "            } catch (Exception e) {",
        "                Toast.makeText(this, \"No email app found\", Toast.LENGTH_SHORT).show();",
        "            }",
        "            return true;",
        "        }",
        "        if (url.startsWith(\"intent:\")) {",
        "            try {",
        "                Intent parsed = Intent.parseUri(url, Intent.URI_INTENT_SCHEME);",
        "                parsed.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "                startActivity(parsed);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"intent: scheme failed\", e);",
        "            }",
        "            return true;",
        "        }",
        "        try {",
        "            Intent generic = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url));",
        "            generic.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);",
        "            startActivity(generic);",
        "        } catch (android.content.ActivityNotFoundException e) {",
        "            String scheme = url.contains(\"://\") ? url.split(\"://\")[0] : url;",
        "            Toast.makeText(this, \"No app found for: \" + scheme + \"://\", Toast.LENGTH_SHORT).show();",
        "        } catch (Exception e) {",
        "            Log.e(TAG, \"openExternalScheme generic failed\", e);",
        "        }",
        "        return true;",
        "    }",
        "",
        "    // =========================================================",
        "    // Download helpers",
        "    // =========================================================",
        "    private void interceptBlobDownload(String blobUrl) {",
        "        String escaped = blobUrl.replace(\"\\\\\", \"\\\\\\\\\").replace(\"'\", \"\\\\'\");",
        "        String js =",
        "            \"(function(){\" +",
        "            \"  fetch('\" + escaped + \"')\" +",
        "            \"    .then(function(r){ return r.blob(); })\" +",
        "            \"    .then(function(b){\" +",
        "            \"      var rd = new FileReader();\" +",
        "            \"      rd.onload = function(){\" +",
        "            \"        if(typeof Android!=='undefined')\" +",
        "            \"          Android.downloadBase64(rd.result,'download',b.type||'application/octet-stream');\" +",
        "            \"      };\" +",
        "            \"      rd.readAsDataURL(b);\" +",
        "            \"    })\" +",
        "            \"    .catch(function(e){ if(typeof Android!=='undefined') Android.showToast('Blob error: '+e); });\" +",
        "            \"})();\";",
        "        webView.evaluateJavascript(js, null);",
        "    }",
        "",
        "    private void handleDataUriDownload(String dataUri, String mimeType, String contentDisposition) {",
        "        new Thread(() -> {",
        "            try {",
        "                String[] parts = dataUri.split(\",\", 2);",
        "                String header  = parts[0];",
        "                String body    = parts.length > 1 ? parts[1] : \"\";",
        "                String mime    = (mimeType != null && !mimeType.isEmpty()) ? mimeType",
        "                    : (header.contains(\":\") ? header.split(\":\")[1].split(\";\")[0] : \"application/octet-stream\");",
        "                String ext     = extensionForMime(mime);",
        "                String fname   = extractFilename(contentDisposition, \"download\" + ext);",
        "                byte[] bytes;",
        "                if (header.contains(\"base64\")) {",
        "                    bytes = Base64.decode(body, Base64.DEFAULT);",
        "                } else {",
        "                    bytes = java.net.URLDecoder.decode(body, \"UTF-8\").getBytes(\"UTF-8\");",
        "                }",
        "                saveBytes(bytes, fname, mime);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"data URI download failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }).start();",
        "    }",
        "",
        "    private void downloadUrlInBackground(String urlStr, String userAgent, String mimeType, String contentDisposition) {",
        "        new Thread(() -> {",
        "            try {",
        "                URL url = new URL(urlStr);",
        "                HttpURLConnection conn = (HttpURLConnection) url.openConnection();",
        "                conn.setRequestProperty(\"User-Agent\", userAgent != null ? userAgent : \"Android\");",
        "                conn.connect();",
        "                String cd   = conn.getHeaderField(\"Content-Disposition\");",
        "                String ct   = conn.getHeaderField(\"Content-Type\");",
        "                String mime = (ct != null && !ct.isEmpty()) ? ct.split(\";\")[0].trim()",
        "                    : (mimeType != null ? mimeType : \"application/octet-stream\");",
        "                String fname = extractFilename(cd != null ? cd : contentDisposition,",
        "                    \"download\" + extensionForMime(mime));",
        "                ByteArrayOutputStream baos = new ByteArrayOutputStream();",
        "                InputStream is = conn.getInputStream();",
        "                byte[] buf = new byte[8192];",
        "                int n;",
        "                while ((n = is.read(buf)) != -1) baos.write(buf, 0, n);",
        "                is.close();",
        "                conn.disconnect();",
        "                saveBytes(baos.toByteArray(), fname, mime);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"URL download failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }).start();",
        "    }",
        "",
        "    private void saveBytes(byte[] bytes, String fileName, String mimeType) {",
        "        try {",
        "            String safeMime = (mimeType != null && !mimeType.isEmpty()) ? mimeType : \"application/octet-stream\";",
        "            String safeFile = (fileName != null && !fileName.trim().isEmpty()) ? fileName.trim() : \"download.bin\";",
        "            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {",
        "                ContentValues cv = new ContentValues();",
        "                cv.put(MediaStore.Downloads.DISPLAY_NAME, safeFile);",
        "                cv.put(MediaStore.Downloads.MIME_TYPE, safeMime);",
        "                cv.put(MediaStore.Downloads.IS_PENDING, 1);",
        "                Uri col = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);",
        "                Uri uri = getContentResolver().insert(col, cv);",
        "                if (uri != null) {",
        "                    try (OutputStream os = getContentResolver().openOutputStream(uri)) {",
        "                        if (os != null) os.write(bytes);",
        "                    }",
        "                    cv.clear();",
        "                    cv.put(MediaStore.Downloads.IS_PENDING, 0);",
        "                    getContentResolver().update(uri, cv, null, null);",
        "                    showToastOnUi(\"Saved to Downloads: \" + safeFile);",
        "                }",
        "            } else {",
        "                File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);",
        "                dir.mkdirs();",
        "                File out = new File(dir, safeFile);",
        "                try (FileOutputStream fos = new FileOutputStream(out)) {",
        "                    fos.write(bytes);",
        "                }",
        "                showToastOnUi(\"Saved: \" + out.getAbsolutePath());",
        "            }",
        "        } catch (Exception e) {",
        "            Log.e(TAG, \"saveBytes failed\", e);",
        "            showToastOnUi(\"Save failed: \" + e.getMessage());",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // Utility helpers",
        "    // =========================================================",
        "    private void showToastOnUi(String message) {",
        "        runOnUiThread(() -> Toast.makeText(this, message, Toast.LENGTH_LONG).show());",
        "    }",
        "",
        "    private String loadAssetText(String assetName) {",
        "        try (InputStream is = getAssets().open(assetName);",
        "             BufferedReader br = new BufferedReader(new InputStreamReader(is, \"UTF-8\"))) {",
        "            StringBuilder sb = new StringBuilder();",
        "            String line;",
        "            while ((line = br.readLine()) != null) {",
        "                sb.append(line).append('\\n');",
        "            }",
        "            return sb.toString();",
        "        } catch (Exception e) {",
        "            Log.e(TAG, \"Failed to load asset: \" + assetName, e);",
        "            return null;",
        "        }",
        "    }",
        "",
        "    private String extensionForMime(String mime) {",
        "        if (mime == null) return \".bin\";",
        "        switch (mime.trim().toLowerCase().split(\";\")[0].trim()) {",
        "            case \"text/html\":              return \".html\";",
        "            case \"text/plain\":              return \".txt\";",
        "            case \"text/css\":               return \".css\";",
        "            case \"text/javascript\":",
        "            case \"application/javascript\": return \".js\";",
        "            case \"application/json\":       return \".json\";",
        "            case \"application/pdf\":        return \".pdf\";",
        "            case \"application/zip\":        return \".zip\";",
        "            case \"image/png\":              return \".png\";",
        "            case \"image/jpeg\":             return \".jpg\";",
        "            case \"image/gif\":              return \".gif\";",
        "            case \"image/svg+xml\":          return \".svg\";",
        "            default:                       return \".bin\";",
        "        }",
        "    }",
        "",
        "    private String extractFilename(String contentDisposition, String fallback) {",
        "        if (contentDisposition != null) {",
        "            java.util.regex.Matcher m = java.util.regex.Pattern",
        "                .compile(\"filename\\\\*?=[\\\"']?([^\\\"';\\\\n]+)[\\\"']?\",",
        "                         java.util.regex.Pattern.CASE_INSENSITIVE)",
        "                .matcher(contentDisposition);",
        "            if (m.find()) return m.group(1).trim();",
        "        }",
        "        return (fallback != null) ? fallback : \"download.bin\";",
        "    }",
        "",
        "    // =========================================================",
        "    // File chooser result",
        "    // =========================================================",
        "    @Override",
        "    protected void onActivityResult(int requestCode, int resultCode, Intent data) {",
        "        super.onActivityResult(requestCode, resultCode, data);",
        "        if (requestCode == 1001) {",
        "            if (mFilePathCallback == null) return;",
        "            Uri[] results = null;",
        "            if (resultCode == RESULT_OK && data != null) {",
        "                if (data.getClipData() != null) {",
        "                    int count = data.getClipData().getItemCount();",
        "                    results = new Uri[count];",
        "                    for (int i = 0; i < count; i++) {",
        "                        results[i] = data.getClipData().getItemAt(i).getUri();",
        "                    }",
        "                } else if (data.getDataString() != null) {",
        "                    results = new Uri[]{Uri.parse(data.getDataString())};",
        "                }",
        "            }",
        "            mFilePathCallback.onReceiveValue(results);",
        "            mFilePathCallback = null;",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // Back button",
        "    // =========================================================",
        "    @Override",
        "    public void onBackPressed() {",
        "        if (webView != null && webView.canGoBack()) {",
        "            webView.goBack();",
        "        } else {",
        "            super.onBackPressed();",
        "        }",
        "    }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def gen_build_gradle(pkg: str, version_name: str = VERSION_NAME, version_code: int = VERSION_CODE) -> str:
    lines = [
        "plugins {",
        "    id 'com.android.application'",
        "}",
        "",
        "android {",
        "    compileSdk " + str(COMPILE_SDK),
        "    namespace '" + pkg + "'",
        "",
        "    defaultConfig {",
        "        applicationId \"" + pkg + "\"",
        "        minSdk " + str(MIN_SDK),
        "        targetSdk " + str(TARGET_SDK),
        "        versionCode " + str(version_code),
        "        versionName \"" + version_name + "\"",
        "        multiDexEnabled true",
        "    }",
        "",
        "    buildTypes {",
        "        release {",
        "            minifyEnabled false",
        "            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'",
        "            signingConfig signingConfigs.debug",
        "        }",
        "        debug {",
        "            debuggable true",
        "        }",
        "    }",
        "",
        "    compileOptions {",
        "        sourceCompatibility JavaVersion.VERSION_1_8",
        "        targetCompatibility JavaVersion.VERSION_1_8",
        "    }",
        "",
        "    packagingOptions {",
        "        resources {",
        "            excludes += ['/META-INF/**']",
        "        }",
        "    }",
        "}",
        "",
        "dependencies {",
        "    implementation 'androidx.appcompat:appcompat:1.6.1'",
        "    implementation 'com.google.android.material:material:1.11.0'",
        "    implementation 'androidx.webkit:webkit:1.9.0'",
        "    implementation 'androidx.core:core:1.12.0'",
        "    implementation 'androidx.multidex:multidex:2.0.1'",
        "}",
    ]
    return "\n".join(lines) + "\n"


def gen_settings_gradle(app_name: str) -> str:
    lines = [
        "pluginManagement {",
        "    repositories {",
        "        google()",
        "        mavenCentral()",
        "        gradlePluginPortal()",
        "    }",
        "}",
        "dependencyResolutionManagement {",
        "    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)",
        "    repositories {",
        "        google()",
        "        mavenCentral()",
        "    }",
        "}",
        "rootProject.name = \"" + app_name + "\"",
        "include ':app'",
    ]
    return "\n".join(lines) + "\n"


def gen_root_build_gradle() -> str:
    return (
        "plugins {\n"
        "    id 'com.android.application' version '8.2.2' apply false\n"
        "}\n"
    )


def gen_layout() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<RelativeLayout xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:layout_width="match_parent"\n'
        '    android:layout_height="match_parent"\n'
        '    android:background="#FFFFFF">\n'
        '\n'
        '    <WebView\n'
        '        android:id="@+id/webview"\n'
        '        android:layout_width="match_parent"\n'
        '        android:layout_height="match_parent" />\n'
        '\n'
        '</RelativeLayout>\n'
    )


def gen_styles() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources>\n'
        '    <style name="AppTheme" parent="Theme.AppCompat.Light.NoActionBar">\n'
        '        <item name="colorPrimary">#2196F3</item>\n'
        '        <item name="colorPrimaryDark">#1976D2</item>\n'
        '        <item name="colorAccent">#03DAC5</item>\n'
        '        <item name="android:windowBackground">@android:color/white</item>\n'
        '    </style>\n'
        '</resources>\n'
    )


def gen_colors() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources>\n'
        '    <color name="ic_launcher_background">#2196F3</color>\n'
        '</resources>\n'
    )


def gen_network_security() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<network-security-config>\n'
        '    <base-config cleartextTrafficPermitted="true">\n'
        '        <trust-anchors>\n'
        '            <certificates src="system" />\n'
        '            <certificates src="user" />\n'
        '        </trust-anchors>\n'
        '    </base-config>\n'
        '    <domain-config cleartextTrafficPermitted="true">\n'
        '        <domain includeSubdomains="true">localhost</domain>\n'
        '        <domain includeSubdomains="true">127.0.0.1</domain>\n'
        '    </domain-config>\n'
        '</network-security-config>\n'
    )


def gen_file_provider_paths() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<paths>\n'
        '    <external-path        name="external"       path="." />\n'
        '    <external-files-path  name="external_files" path="." />\n'
        '    <files-path           name="internal"        path="." />\n'
        '    <cache-path           name="cache"           path="." />\n'
        '    <external-cache-path  name="external_cache"  path="." />\n'
        '</paths>\n'
    )


def gen_proguard() -> str:
    return (
        '-keep class * extends android.webkit.WebViewClient { *; }\n'
        '-keep class * extends android.webkit.WebChromeClient { *; }\n'
        '-keepclassmembers class * {\n'
        '    @android.webkit.JavascriptInterface <methods>;\n'
        '}\n'
    )


def gen_gradle_properties() -> str:
    return (
        'org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8\n'
        'android.useAndroidX=true\n'
        'android.enableJetifier=true\n'
    )


def gen_gradle_wrapper_props() -> str:
    return (
        'distributionBase=GRADLE_USER_HOME\n'
        'distributionPath=wrapper/dists\n'
        'distributionUrl=https\\://services.gradle.org/distributions/gradle-8.2-bin.zip\n'
        'zipStoreBase=GRADLE_USER_HOME\n'
        'zipStorePath=wrapper/dists\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# GRADLE WRAPPER — real bootstrap scripts
#
# NOTE (v4.3 fix): earlier versions wrote fake gradlew/gradlew.bat stubs that
# tried to run a non-existent "gradlew.jar" directly with `java -jar`. That
# file was never created anywhere in the pipeline, so every build failed at
# the compile step. These are the genuine Gradle Wrapper launcher scripts —
# they invoke gradle-wrapper.jar, which knows how to download the configured
# Gradle distribution (see gen_gradle_wrapper_props) on first run.
# ─────────────────────────────────────────────────────────────────────────────
GRADLEW_SH = r"""#!/usr/bin/env sh
APP_BASE_NAME=$(basename "$0")
APP_HOME=$(cd "$(dirname "$0")" >/dev/null && pwd)
CLASSPATH="$APP_HOME/gradle/wrapper/gradle-wrapper.jar"
if [ -n "$JAVA_HOME" ] ; then
    JAVACMD="$JAVA_HOME/bin/java"
else
    JAVACMD="java"
fi
exec "$JAVACMD" -Xmx64m -Xms64m $JAVA_OPTS $GRADLE_OPTS \
  "-Dorg.gradle.appname=$APP_BASE_NAME" \
  -classpath "$CLASSPATH" \
  org.gradle.wrapper.GradleWrapperMain "$@"
"""

GRADLEW_BAT = r"""@rem Gradle startup script for Windows
@if "%DEBUG%"=="" @echo off
setlocal
set DIRNAME=%~dp0
set APP_BASE_NAME=%~n0
set APP_HOME=%DIRNAME%
set CLASSPATH=%APP_HOME%gradle\wrapper\gradle-wrapper.jar
if defined JAVA_HOME (
  set JAVA_EXE=%JAVA_HOME%\bin\java.exe
) else (
  set JAVA_EXE=java.exe
)
"%JAVA_EXE%" -Xmx64m -Xms64m %JAVA_OPTS% %GRADLE_OPTS% "-Dorg.gradle.appname=%APP_BASE_NAME%" -classpath "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %*
endlocal
"""

# Mirrors of the official gradle-wrapper.jar, hosted on a domain we're
# allowed to reach. Tried in order; first success wins.
_GRADLE_WRAPPER_JAR_MIRRORS = [
    "https://raw.githubusercontent.com/gradle/gradle/v8.2.0/gradle/wrapper/gradle-wrapper.jar",
    "https://github.com/gradle/gradle/raw/v8.2.0/gradle/wrapper/gradle-wrapper.jar",
]


def _ensure_gradle_wrapper_jar(dest: Path) -> bool:
    """Download the real gradle-wrapper.jar bootstrap launcher.

    This file is tiny (~60KB) and is what gradlew/gradlew.bat actually run.
    It is NOT the full Gradle distribution — that gets downloaded separately
    and automatically by this jar the first time a build runs, per the
    distributionUrl in gradle-wrapper.properties.
    """
    try:
        import urllib.request
        for url in _GRADLE_WRAPPER_JAR_MIRRORS:
            try:
                log.info("   Fetching gradle-wrapper.jar from " + url)
                req = urllib.request.Request(url, headers={"User-Agent": "html-to-apk-converter"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                if data and len(data) > 1024:
                    dest.write_bytes(data)
                    log.info("   gradle-wrapper.jar OK (%.1f KB)" % (len(data) / 1024))
                    return True
            except Exception as e:
                log.warning("   Mirror failed (%s): %s" % (url, e))
        log.error("   Could not fetch gradle-wrapper.jar from any mirror.")
        log.error("   Build will fail until this file is present at: " + str(dest))
        return False
    except Exception as e:
        log.error("   Unexpected error fetching gradle-wrapper.jar: %s" % e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# WINDOWS-SAFE DIRECTORY REMOVAL
# ─────────────────────────────────────────────────────────────────────────────
def _force_remove(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception as e:
        log.debug("Force-remove fallback failed for %s: %s" % (path, e))


def robust_rmtree(path: Path, retries: int = 5, delay: float = 0.5):
    if not path.exists():
        return
    if platform.system() == "Windows":
        try:
            raw = str(path.resolve())
            if not raw.startswith("\\\\?\\"):
                raw = "\\\\?\\" + raw
            path = Path(raw)
        except Exception:
            pass
    for attempt in range(1, retries + 1):
        try:
            shutil.rmtree(path, onerror=_force_remove)
            if not path.exists():
                return
        except Exception as e:
            log.debug("rmtree attempt %d/%d failed: %s" % (attempt, retries, e))
            if attempt < retries:
                time.sleep(delay)
    if path.exists():
        tombstone = path.parent / (path.name + "_old_" + str(int(time.time())))
        try:
            path.rename(tombstone)
            log.warning("Could not delete old build dir; renamed to: " + tombstone.name)
        except Exception as e:
            log.warning("Could not rename old build dir: %s — will overwrite files." % e)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4  —  BUILD PROJECT TREE  (now copies ALL assets)
# ─────────────────────────────────────────────────────────────────────────────
def build_android_project(features: dict,
                           html_path: Path,
                           asset_files: list,
                           pkg: str = PACKAGE_NAME,
                           app_name: str = APP_NAME,
                           version_name: str = VERSION_NAME,
                           version_code: int = VERSION_CODE,
                           orientation: str = "auto") -> Path:
    log.info("=== STEP 3: Building Android project structure ===")
    proj = BUILD_DIR
    if proj.exists():
        log.info("   Cleaning previous build directory...")
        robust_rmtree(proj)

    pkg_path = pkg.replace(".", "/")

    dirs = [
        proj / "app/src/main/java" / pkg_path,
        proj / "app/src/main/res/layout",
        proj / "app/src/main/res/values",
        proj / "app/src/main/res/xml",
        proj / "app/src/main/res/mipmap-hdpi",
        proj / "app/src/main/res/mipmap-mdpi",
        proj / "app/src/main/res/mipmap-xhdpi",
        proj / "app/src/main/res/mipmap-xxhdpi",
        proj / "app/src/main/res/mipmap-xxxhdpi",
        proj / "app/src/main/assets",
        proj / "gradle/wrapper",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    def w(path: Path, content: str):
        path.write_text(content, encoding="utf-8")
        log.debug("   Wrote: " + str(path.relative_to(proj)))

    # Java source
    w(proj / "app/src/main/java" / pkg_path / "MainActivity.java",
      gen_main_activity(pkg))

    # Manifest
    w(proj / "app/src/main/AndroidManifest.xml", gen_manifest(pkg, app_name, orientation))

    # Resources
    w(proj / "app/src/main/res/layout/activity_main.xml",       gen_layout())
    w(proj / "app/src/main/res/values/styles.xml",               gen_styles())
    w(proj / "app/src/main/res/values/colors.xml",               gen_colors())
    w(proj / "app/src/main/res/xml/network_security_config.xml", gen_network_security())
    w(proj / "app/src/main/res/xml/file_provider_paths.xml",     gen_file_provider_paths())

    # Gradle
    w(proj / "app/build.gradle",          gen_build_gradle(pkg, version_name, version_code))
    w(proj / "settings.gradle",           gen_settings_gradle(app_name))
    w(proj / "build.gradle",              gen_root_build_gradle())
    w(proj / "gradle.properties",         gen_gradle_properties())
    w(proj / "app/proguard-rules.pro",    gen_proguard())
    w(proj / "gradle/wrapper/gradle-wrapper.properties", gen_gradle_wrapper_props())

    # Gradlew scripts — real Gradle Wrapper bootstrap (the old stub here
    # referenced a non-existent gradlew.jar and could never actually run).
    # We use Gradle's official wrapper bootstrap source so `./gradlew` /
    # `gradlew.bat` download the configured Gradle version on first run.
    w(proj / "gradlew", GRADLEW_SH)
    (proj / "gradlew").chmod(0o755)
    w(proj / "gradlew.bat", GRADLEW_BAT)

    # gradle-wrapper.jar — the small bootstrap launcher the gradlew scripts
    # invoke. Without this file present, gradlew has nothing to execute.
    wrapper_jar_dir = proj / "gradle/wrapper"
    wrapper_jar_dir.mkdir(parents=True, exist_ok=True)
    _ensure_gradle_wrapper_jar(wrapper_jar_dir / "gradle-wrapper.jar")

    # ── Assets: index.html + bridge.js ────────────────────────────────────
    assets_dir = proj / "app/src/main/assets"

    shutil.copy2(html_path, assets_dir / "index.html")
    log.info("   Copied HTML  -> assets/index.html")

    w(assets_dir / "bridge.js", BRIDGE_JS)
    log.info("   Wrote bridge -> assets/bridge.js")

    # ── NEW: Copy all detected local assets (preserving folder structure) ──
    log.info("   Copying local assets into assets/ ...")
    copied_count  = 0
    skipped_count = 0

    for abs_path, rel_str in asset_files:
        # Normalise path separators for this OS
        rel_posix  = rel_str.replace("\\", "/")
        dest_path  = assets_dir / rel_posix
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(abs_path, dest_path)
            log.info("   + assets/%s" % rel_posix)
            copied_count += 1
        except Exception as e:
            log.warning("   [!] Could not copy %s: %s" % (rel_posix, e))
            skipped_count += 1

    log.info("   Assets copied: %d  |  skipped: %d" % (copied_count, skipped_count))
    log.info("   Android project structure created OK")
    return proj


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3b — APP ICON
#
# NEW: the manifest has always pointed at @mipmap/ic_launcher /
# @mipmap/ic_launcher_round, but no prior version of this script ever wrote
# those PNGs — Gradle would fail resource resolution at build time with no
# icon supplied. This generates real icons at every required density from
# a single uploaded image, and falls back to a plain generated icon if the
# user doesn't supply one so a build never fails for a missing icon.
# ─────────────────────────────────────────────────────────────────────────────
MIPMAP_DENSITIES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def _make_fallback_icon():
    """A simple generated square icon used when the user supplies none."""
    from PIL import Image, ImageDraw
    size = 192
    img = Image.new("RGB", (size, size), (33, 150, 243))  # matches ic_launcher_background
    draw = ImageDraw.Draw(img)
    draw.ellipse((size * 0.2, size * 0.2, size * 0.8, size * 0.8), fill=(255, 255, 255))
    return img


def apply_app_icon(project_dir: Path, icon_path: Path = None) -> None:
    """Resize the given icon (or a generated fallback) into every mipmap
    density folder as both ic_launcher.png and ic_launcher_round.png."""
    from PIL import Image

    log.info("=== STEP 3b: Applying app icon ===")
    try:
        if icon_path and Path(icon_path).exists():
            src = Image.open(icon_path).convert("RGBA")
            log.info("   Using uploaded icon: " + str(icon_path))
        else:
            src = _make_fallback_icon().convert("RGBA")
            log.info("   No icon supplied — using generated default icon.")
    except Exception as e:
        log.warning("   Could not read uploaded icon (%s) — using default." % e)
        src = _make_fallback_icon().convert("RGBA")

    # Make square (center-crop) so it isn't distorted by resizing.
    w, h = src.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        src = src.crop((left, top, left + side, top + side))

    for folder, px in MIPMAP_DENSITIES.items():
        out_dir = project_dir / "app/src/main/res" / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        resized = src.resize((px, px), Image.LANCZOS)
        resized.save(out_dir / "ic_launcher.png", format="PNG")
        resized.save(out_dir / "ic_launcher_round.png", format="PNG")

    log.info("   Icon written to all %d mipmap densities." % len(MIPMAP_DENSITIES))


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5  —  COMPILE APK
# ─────────────────────────────────────────────────────────────────────────────
def find_sdk() -> Path | None:
    candidates = [
        os.environ.get("ANDROID_HOME", ""),
        os.environ.get("ANDROID_SDK_ROOT", ""),
        str(Path.home() / "Android/Sdk"),
        str(Path.home() / "AppData/Local/Android/Sdk"),
        "/opt/android-sdk",
        "/usr/local/android-sdk",
    ]
    for c in candidates:
        p = Path(c)
        if p.exists() and (p / "platforms").exists():
            return p
    return None


def compile_apk(project_dir: Path, pkg: str = PACKAGE_NAME, output_dir: Path = None) -> bool:
    log.info("=== STEP 4: Compiling APK ===")
    if output_dir is None:
        output_dir = OUTPUT_DIR
    sdk = find_sdk()
    if sdk:
        log.info("   Android SDK: " + str(sdk))
        (project_dir / "local.properties").write_text(
            "sdk.dir=" + sdk.as_posix() + "\n", encoding="utf-8")
    else:
        log.warning("   Android SDK not found — skipping Gradle build.")
        log.warning("   Set ANDROID_HOME and re-run, or open the project in Android Studio.")
        return False

    is_win = platform.system() == "Windows"
    gradlew_name = "gradlew.bat" if is_win else "./gradlew"
    gradlew_path = project_dir / ("gradlew.bat" if is_win else "gradlew")

    # IMPORTANT: subprocess.run does NOT search `cwd` for the executable on
    # Windows the way a shell prompt would — CreateProcess only checks PATH
    # and a few fixed system dirs. Passing just "gradlew.bat" here used to
    # fail with FileNotFoundError even though the file existed right inside
    # project_dir, because that folder was never on PATH. Using the resolved
    # absolute path fixes this on both Windows and POSIX.
    if not gradlew_path.exists():
        log.error("   %s not found at: %s" % (gradlew_name, gradlew_path))
        log.error("   The Android project may not have been generated correctly.")
        return False

    # On Windows, launching a .bat file is not a direct CreateProcess call —
    # Windows transparently routes it through cmd.exe. The earlier fix here
    # tried to invoke cmd.exe explicitly with a manually-quoted command
    # string, but subprocess.run() with a list argument and shell=False
    # already does its own quoting per list item when it assembles the
    # actual Windows command line — adding our own quotes on top of that
    # produced a literal escaped quote character in the path
    # (...\gradlew.bat\") that Windows then failed to find. The fix is to
    # NOT hand-build any quoting at all: pass each argument as its own list
    # item and let subprocess handle quoting. This is robust to spaces and
    # parentheses in the path without double-quoting anything.
    if is_win:
        cmd = ["cmd.exe", "/c", str(gradlew_path), "assembleDebug", "--stacktrace"]
    else:
        cmd = [str(gradlew_path), "assembleDebug", "--stacktrace"]
    log.info("   Running: " + " ".join(cmd))
    try:
        result = subprocess.run(cmd, cwd=str(project_dir), timeout=600)
        if result.returncode != 0:
            log.error("   Gradle build FAILED.")
            return False
    except FileNotFoundError:
        log.error("   Could not execute %s. Ensure JDK 17+ is installed and on PATH." % gradlew_name)
        return False
    except subprocess.TimeoutExpired:
        log.error("   Build timed out.")
        return False

    apks = list(project_dir.glob("app/build/outputs/apk/**/*.apk"))
    if not apks:
        log.error("   No APK found after build.")
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    apk_slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', pkg.split(".")[-1])
    dest = output_dir / (apk_slug + ".apk")
    shutil.copy2(apks[0], dest)
    log.info("   APK -> " + str(dest) + "  (%.1f KB)" % (dest.stat().st_size / 1024))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6  —  SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
def print_summary(features: dict, project_dir: Path, apk_built: bool,
                  asset_count: int,
                  app_name: str = APP_NAME, package_name: str = PACKAGE_NAME):
    log.info("")
    log.info("=" * 62)
    log.info("  BUILD SUMMARY  —  HTML to APK Builder v4.3")
    log.info("=" * 62)
    log.info("  Package  : " + package_name)
    log.info("  App Name : " + app_name)
    log.info("  Min SDK  : " + str(MIN_SDK) + "  (Android 7.0+)")
    log.info("  Target   : API " + str(TARGET_SDK))
    log.info("")
    log.info("  Detected capabilities:")
    for k, v in features.items():
        if isinstance(v, bool) and v:
            log.info("    YES  " + k)
    log.info("")
    log.info("  Multi-file assets bundled: %d file(s)" % asset_count)
    log.info("  (CSS, JS, images, fonts, media — all copied into assets/)")
    log.info("")
    log.info("  v4.3 new features:")
    log.info("    - Auto-detects and copies style.css, script.js, images, fonts")
    log.info("    - Preserves subfolder structure inside assets/")
    log.info("    - Deep-scans CSS url() and JS fetch/import for linked files")
    log.info("    - Feature detection extended into linked CSS and JS files")
    log.info("")
    if apk_built:
        log.info("  APK: " + str(OUTPUT_DIR / "app.apk"))
        log.info("  Install: adb install output/app.apk")
    else:
        log.info("  Open in Android Studio: " + str(project_dir))
        log.info("  Then: Build > Build APK(s)")
    log.info("")
    log.info("  Log: " + str(log_file))
    log.info("=" * 62)
    log.info("  Developed by SANTHOSH A · SanStudio")
    log.info("=" * 62)


# ─────────────────────────────────────────────────────────────────────────────
# APP IDENTITY WIZARD
# ─────────────────────────────────────────────────────────────────────────────

def extract_title_from_html(html_path: Path) -> str:
    try:
        content = html_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', '', text)
    text = text[:30]
    return text or "myapp"


def make_package_id(domain: str, app_slug: str) -> str:
    domain_clean = re.sub(r'[^a-z0-9]', '', domain.lower())
    if not domain_clean:
        domain_clean = "santhosh"
    return "com." + domain_clean + "." + app_slug


def prompt(label: str, default: str) -> str:
    try:
        answer = input("  %s [%s]: " % (label, default)).strip()
        return answer if answer else default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def validate_package(pkg: str) -> bool:
    parts = pkg.split(".")
    if len(parts) < 2:
        return False
    return all(re.match(r'^[a-z][a-z0-9_]*$', p) for p in parts)


def run_app_wizard(html_path: Path, asset_files: list):
    print()
    print("  +----------------------------------------------------------+")
    print("  |   APP IDENTITY SETUP                                     |")
    print("  |   Press Enter to accept a suggestion, or type your own   |")
    print("  +----------------------------------------------------------+")
    print()

    html_title     = extract_title_from_html(html_path)
    suggested_name = html_title if html_title else "MyWebApp"
    print("  Detected HTML title: \"%s\"" % (html_title if html_title else "(none found)"))

    if asset_files:
        print("  Detected local assets: %d file(s)" % len(asset_files))
        for _, rel in asset_files[:8]:
            print("    · " + rel.replace("\\", "/"))
        if len(asset_files) > 8:
            print("    · ... and %d more" % (len(asset_files) - 8))
    print()

    app_name = prompt("App Name", suggested_name)
    if not app_name:
        app_name = suggested_name

    app_slug      = slugify(app_name)
    suggested_pkg = make_package_id("santhosh", app_slug)

    print()
    print("  Auto-generated Package ID: %s" % suggested_pkg)

    while True:
        pkg = prompt("Package ID", suggested_pkg)
        if not pkg:
            pkg = suggested_pkg
        pkg = pkg.lower().replace(" ", "").replace("-", "_")
        if validate_package(pkg):
            break
        print("  [!] Invalid package ID. Use format like: com.yourname.appname")
        suggested_pkg = pkg

    print()
    version_name = prompt("Version Name", "1.0")
    if not version_name:
        version_name = "1.0"

    try:
        digits       = re.sub(r'[^0-9]', '', version_name)
        version_code = max(1, int(digits[:4])) if digits else 1
    except Exception:
        version_code = 1

    print()
    print("  +----------------------------------------------------------+")
    print("  |   BUILD CONFIGURATION                                    |")
    print("  +----------------------------------------------------------+")
    print("  App Name    : " + app_name)
    print("  Package ID  : " + pkg)
    print("  Version     : " + version_name + "  (code: " + str(version_code) + ")")
    print("  Local assets: %d file(s) will be bundled" % len(asset_files))
    print("  Output APK  : output/app.apk")
    print()

    try:
        confirm = input("  Proceed with build? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        confirm = "y"

    if confirm in ("n", "no"):
        print()
        print("  Build cancelled.")
        sys.exit(0)

    print()
    return app_name, pkg, version_name, version_code


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(BANNER)

    html_path = INPUT_DIR / "index.html"
    if not html_path.exists():
        log.error("Missing: " + str(html_path))
        log.error("Place your index.html inside input_project/ and re-run.")
        sys.exit(1)

    # Step 1: Analyze HTML features
    features = analyze_html(html_path)

    # Step 2: Collect all local assets (CSS, JS, images, fonts, etc.)
    asset_files = collect_all_assets(INPUT_DIR, features)

    # Run the interactive wizard
    app_name, package_name, version_name, version_code = run_app_wizard(html_path, asset_files)

    log.info("Input   : " + str(html_path))
    log.info("App     : " + app_name)
    log.info("Package : " + package_name)
    log.info("Version : " + version_name)
    log.info("Assets  : %d file(s)" % len(asset_files))
    log.info("")

    # Step 3: Build Android project (copies index.html + all assets)
    project_dir = build_android_project(
        features, html_path, asset_files,
        pkg=package_name,
        app_name=app_name,
        version_name=version_name,
        version_code=version_code,
    )

    # Step 3b: Apply app icon (generated fallback, since CLI has no upload)
    apply_app_icon(project_dir, icon_path=None)

    # Step 4: Compile APK
    apk_built = compile_apk(project_dir, pkg=package_name)

    # Step 5: Print summary
    print_summary(features, project_dir, apk_built, len(asset_files), app_name, package_name)


if __name__ == "__main__":
    main()
