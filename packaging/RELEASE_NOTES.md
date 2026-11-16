OpenKeyTranslate for Windows (CPU build)
========================================

A Human-in-the-Loop manga / webtoon translator (English -> Polish). Source code, documentation and the build recipe:
https://github.com/Weed050/OpenKeyTranslate

Start
-----
1. Unzip the whole folder anywhere (for example Documents) and keep its contents together.
2. Run OpenKeyTranslate.exe. The app opens in its own window ("Starting..." until it is ready). Close the window to
   stop the app. Links to other websites open in your browser.
3. The first start downloads the OCR models and the correction-memory model (internet needed once; a few minutes).
   The log is in %APPDATA%\OpenKeyTranslate\logs. To watch it live, start the exe from a terminal or with --console.
   To open the app in your browser instead of its window: OpenKeyTranslate.exe --browser (then a console window
   stays open, and closing it stops the app).
4. Settings > Translation & API keys: paste your own free key (Groq or Google AI Studio) and press Test.

"Windows protected your PC"
---------------------------
The exe is not code-signed (a certificate costs money every year), so SmartScreen calls the publisher unknown. Check
the download first (below), then: More info > Run anyway.

Check that this is the official build
-------------------------------------
Every release is built by GitHub Actions from the public source (.github/workflows/build-windows.yml +
packaging/okt.spec), not on anyone's own computer, and GitHub signs a record of that build:

    gh attestation verify OpenKeyTranslate-<version>-windows-cpu.zip --repo Weed050/OpenKeyTranslate

(GitHub CLI: https://cli.github.com). It prints the workflow and the commit the zip was built from. Without the CLI,
compare the checksum with SHA256SUMS.txt of the release:

    Get-FileHash OpenKeyTranslate-<version>-windows-cpu.zip -Algorithm SHA256

The whole build log is public on the Actions tab of the repository. You can also upload the zip to VirusTotal
yourself; apps packaged with PyInstaller are sometimes flagged by a few engines without reason (false positives).

Requirements
------------
Windows 10 / 11, 64-bit, a CPU with AVX (almost any from the last ten years), 8 GB RAM recommended, ~1.3 GB of disk
for the app plus ~0.5 GB for the models. The app window needs the Microsoft Edge WebView2 Runtime. It is part of
Windows 11 and comes with Edge updates on Windows 10. Without it the app opens in the browser; the free runtime is at
https://developer.microsoft.com/microsoft-edge/webview2/. This build runs the OCR on the CPU (~20 s per page); an NVIDIA GPU needs the version
from the source code (setup/pip_install_instruction.txt in the repository).

Privacy and your data
---------------------
Page images never leave your computer: OCR, cleaning and lettering run locally. Only the extracted text goes to the
translation service whose key you entered. Your projects, settings (with your keys), database and logs are in
%APPDATA%\OpenKeyTranslate - not in this folder, so a new version can replace this folder without losing anything.

Uninstall: delete this folder. Settings > Storage & uninstall lists every other folder the app uses (data, model
caches, the app window's data in %LOCALAPPDATA%\OpenKeyTranslate) with its size.

License: Apache License 2.0 (LICENSE). The bundled libraries keep their own licenses (in _internal, next to each
package's metadata).
