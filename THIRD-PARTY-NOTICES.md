# Third-party components

AssetLab.exe embeds Microsoft.Web.WebView2 SDK 1.0.4258.31 (Core, WinForms, x64 loader), distributed under Microsoft's
WebView2 SDK licence, included in `launcher/WebView2-LICENSE.txt` in source and `WebView2-LICENSE.txt` in the app package. The separate Evergreen Runtime is supplied by Microsoft;
setup verifies the Microsoft signature of its bootstrapper before executing it when the runtime is missing.

Asset Lab does not include these components; the launcher downloads them from their official sources on first use
(the local AI only when the user agrees) and verifies each file against the SHA-256 listed in `bootstrap.json`.

| Component | Source | Licence |
|---|---|---|
| Python 3.13 (embeddable) | python.org | Python Software Foundation License |
| numpy | PyPI | BSD 3-Clause |
| Pillow | PyPI | MIT-CMU (HPND) |
| soundfile (with libsndfile and its codecs) | PyPI | BSD 3-Clause; libsndfile LGPL 2.1 |
| cffi, pycparser | PyPI | MIT, BSD 3-Clause |
| yt-dlp (sound editor link helper, on request) | github.com/yt-dlp/yt-dlp | Unlicense |
| Deno (JavaScript runtime for yt-dlp, on request) | github.com/denoland/deno | MIT |
| PyAV (with FFmpeg libraries, on request) | PyPI | BSD 3-Clause; the bundled FFmpeg libraries under their own licences |
| Ollama | github.com/ollama/ollama | MIT |
| stable-diffusion.cpp | github.com/leejet/stable-diffusion.cpp | MIT |
| FLUX.2 [klein] 4B (GGUF by leejet) | huggingface.co/leejet/FLUX.2-klein-4B-GGUF | Apache 2.0 |
| FLUX.2 small decoder VAE | huggingface.co/black-forest-labs/FLUX.2-small-decoder | Apache 2.0 |
| Qwen3-4B (GGUF by unsloth) | huggingface.co/unsloth/Qwen3-4B-GGUF | Apache 2.0 |

Mitos.is: The Game and its art belong to its developers. Asset Lab reads game assets from the user's own installation;
the application package does not include those assets. The README screenshots show portions of the game art inside
the editor to illustrate its operation.

The sound editor's link helper (yt-dlp, Deno, PyAV) is downloaded by the lab itself, only after the player agrees, the
first time a page link or a file type that needs it is used. Sounds and music the player brings in remain theirs to
clear: only use music you have the right to use.

## Novu credit font

The bundled UnifrakturCook Bold font is distributed under the SIL Open Font License 1.1.
Its copyright and full license are included in `src/web/fonts/OFL-UnifrakturCook.txt`.
Source: https://github.com/google/fonts/tree/main/ofl/unifrakturcook
