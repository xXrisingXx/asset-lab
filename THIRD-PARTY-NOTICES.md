# Third-party components

Asset Lab does not include these components; the launcher downloads them from their official sources on first use
(the local AI only when the user agrees) and verifies each file against the SHA-256 listed in `bootstrap.json`.

| Component | Source | Licence |
|---|---|---|
| Python 3.13 (embeddable) | python.org | Python Software Foundation License |
| numpy | PyPI | BSD 3-Clause |
| Pillow | PyPI | MIT-CMU (HPND) |
| Ollama | github.com/ollama/ollama | MIT |
| stable-diffusion.cpp | github.com/leejet/stable-diffusion.cpp | MIT |
| FLUX.2 [klein] 4B (GGUF by leejet) | huggingface.co/leejet/FLUX.2-klein-4B-GGUF | Apache 2.0 |
| FLUX.2 small decoder VAE | huggingface.co/black-forest-labs/FLUX.2-small-decoder | Apache 2.0 |
| Qwen3-4B (GGUF by unsloth) | huggingface.co/unsloth/Qwen3-4B-GGUF | Apache 2.0 |

Mitos.is: The Game and its art belong to its developers. Asset Lab reads them from the user's own installation and
never redistributes them.
