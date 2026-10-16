![Created by Novu](media/readme/novu-credit.webp)

# Asset Lab

**Make Mitos.is look your way.**

Asset Lab is a Windows desktop editor for the artwork and sounds in **Mitos.is: The Game**. Find an asset, edit it by hand or describe a new look to Theme AI, then review your changes before installing them.

[**Download Asset Lab**](https://github.com/xXrisingXx/asset-lab/releases/latest)

## Features

| Feature | What you can do |
| --- | --- |
| **Asset library** | Search sprites, textures, backgrounds, fonts and sounds. Use categories, favorites, custom names and image similarity search to find what you need. |
| **Layered editor** | Paint, erase, select, fill, add text and shapes, transform artwork, adjust colors, apply filters and layer effects, and export PNGs. |
| **Theme AI** | Describe colors, gradients, outlines, glow and other effects. Preview the plan before applying it to one asset, a checked selection or a broader theme. |
| **AI image generation** | Ask for a new logo or redesigned artwork, compare generated variants, adjust the result and apply the one you like. Requires the optional local AI download. |
| **Fonts and sounds** | Style game text and replace or edit sounds, with trimming, fades and volume controls. |
| **Flexible workspace** | Collapse either sidebar or widen the canvas. Preview artwork as a texture, tile, cell or button. |
| **Builds and saved work** | Keep different mod builds, return to saved edits and continue Theme AI conversations. |
| **Review and restore** | Compare original and edited artwork, exclude individual changes, update an installed mod and restore the original files. |

## See it in action

### The original game asset

The **MITOSIS main-menu title** in the editor. The library is on the left, the canvas is in the center, and layers and adjustments are on the right.

![Asset Lab layout with the original MITOSIS title](media/readme/editor.png)

### A new logo generated with Theme AI

> Redraw the MITOSIS title as a completely new futuristic game logo, with bold beveled chrome lettering, cyan illuminated edges and a glowing cell forming the O. Keep the text MITOSIS clearly readable.

Theme AI's image-generation tool creates new artwork. Choose a variant, adjust its fit, and apply it to your workspace.

![Theme AI image-generation variants and the newly generated MITOSIS logo](media/readme/theme-ai.png)

### The finished design

Close the chat panel to inspect the new logo on the larger canvas. Saving a design does not install it into the game.

![The AI-generated MITOSIS logo in Asset Lab's full editor layout](media/readme/theme-result.png)

<details>
<summary>View the request and before/after review</summary>

![The redesign request in Theme AI](media/readme/theme-plan.png)

![Review comparing the original and redesigned game artwork](media/readme/review.png)

</details>

## Get started

1. Download **AssetLab.exe** from the [latest release](https://github.com/xXrisingXx/asset-lab/releases/latest).
2. Run it and follow setup. Asset Lab finds your Steam installation of Mitos.is and opens its own desktop window.
3. Choose whether to install the optional local AI. You can skip it and use the manual editor and keyword-based theme effects.
4. If setup asks for Microsoft's WebView2 runtime, follow the installation prompt so the desktop window can open.

You need a **64-bit Windows PC** and an installed copy of **Mitos.is: The Game**. The optional AI download is about **6.3 GB**. For local AI, **16 GB of RAM** is recommended; **32 GB is recommended for image generation**, which uses both system memory and graphics memory. A compatible graphics card with **6 GB or more of video memory** helps. Setup checks your hardware and available space. Drawing can take several minutes, especially while the models load; speed varies by PC.

## Make your first mod

### Edit by hand

1. Search the library and select an asset.
2. Use the editor's tools and layers, or choose **Import as layer** to bring in your own image.
3. Preview the result and use undo or history to adjust your work.
4. Click **Save edit + recipe** to save the change to your mod.

### Use Theme AI

1. Open **Theme AI**. To limit a request, check the assets you want in the library first.
2. Describe the result. For example:
   - “Rainbow outlines on the buttons with a subtle glow.”
   - “Deep ocean blues, keep the logo.”
   - “Redraw the MITOSIS title as a futuristic chrome logo with cyan lighting.”
3. For color and effect changes, inspect the plan and preview, then click **Apply**.
4. For new artwork, click **Draw it**, compare the variants, adjust the fit and click **Apply to the game art**.
5. Continue with another request or use the available undo controls if you want to try a different direction.

Be specific about the asset, colors and effect you want. Generated lettering and small details can vary, so inspect the preview before applying a design.

### Review and install

1. Open **Review** or **Review files** to compare the saved changes with the originals.
2. Use **Remove** beside anything you want to leave out. Your saved work stays available.
3. Close Mitos.is, or let Asset Lab close it when prompted.
4. Choose **Install in Mitosis** or **Install saved mod**, then launch the game to see the result.

**Apply and Save change your workspace. Install changes what the game uses.** You can keep editing an existing mod and install the updated version without restoring everything first.

## Restore the original game

Close Mitos.is and choose **Restore original**. Asset Lab restores the original files it backed up when installing your mod.

Steam updates can replace modified files. Restore originals before a game update when possible. If Steam has already replaced them, use **Clear stale install** when Asset Lab offers it, then review your mod before installing again.

## Keep your library organized

- **Favorite** assets you edit often so they are easy to find again.
- **Name** assets to make searches and Theme AI requests more specific.
- Use **Search by image** or **Find similar** to locate related artwork.
- **Remove from pool** hides unwanted assets from browsing and theme selection. Enable **Removed** to find and restore them later.
- Use separate **builds** to keep different looks organized.

## Updates and saved work

Asset Lab checks for updates when it starts and asks before installing one. Your builds, saved edits, favorites and settings are kept separately from application updates. If an update is interrupted, reopen Asset Lab so it can recover.

Open **Settings → About** to find your workspace and other saved-file locations. Before closing, finish any active drawing or sound task and respond to any unsaved-edit prompt.

## Useful to know

- Local AI runs on your PC. Internet access is needed for setup downloads, updates and any web links you ask the app to use.
- Some icons are colored or stretched by the game. Use the previews and **Keep it white** or **Keep the original shape** where offered.
- Asset Lab edits visual and audio assets. It does not change game logic, online systems or menu hitboxes; hiding an image does not necessarily disable its button.
- Use sound and image files you have permission to use, and keep a separate copy of work you do not want to lose.

---

Created by **Novu**. Asset Lab is a fan-made tool, not affiliated with the developers of Mitos.is. Mitos.is and its game artwork belong to their respective owners.

[License](LICENSE) · [Third-party notices](THIRD-PARTY-NOTICES.md)
