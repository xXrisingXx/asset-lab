'use strict';
/* =====================================================================================================================
   ASSET LAB EDITOR ENGINE  ·  window.Ed
   A layered, Photoshop-style image editor for one game asset at a time. core.js is the engine: document, layers,
   compositor, previews, selection, history and every registry. editor/shell.js draws the UI (menubar, options bar, tool
   strip, panel dock, dialogs, view overlay), handles the pointer and keyboard, and registers the basic tools and menus.
   Feature modules (editor/select.js, paint.js, fill.js, transform.js, adjust.js, filters.js, styles.js, layers.js,
   color.js) are built on this API alone and never edit core.js or shell.js.

   WRITING A MODULE  A classic script loaded after core.js, app.js and shell.js (index.html). Wrap it in an IIFE and take
   the engine from window:  (()=>{ const Ed=window.Ed; Ed.registerTool('pencil',{...}); Ed.registerMenu('Filter/...',{...}); })();
   Never declare a top-level `const Ed` or `let Ed`: it would shadow window.Ed for every other script and the tests.
   Registering at load time is fine; Ed.ready (Promise) resolves once every script has run. Never assume another module is
   loaded; a missing module must not break anything. Report problems to the player with Ed.message(text, isError).
   Everything is plain DOM and canvas 2D (no build step; the page CSP allows no inline script/style, eval or blob workers).

   COORDINATES  Image pixels, origin top-left: pixel (x, y) covers x..x+1, its centre is (x+.5, y+.5). Rectangles are
   {x, y, w, h}. Layer ids are strings ('L1', 'L2'...); where a layer is expected (`ref`) you may pass the layer object, its
   id, or a number = index (0 = bottom); null/undefined = the active layer.

   DOCUMENT  Ed.doc (null until an image is open) = {width, height, name, layers, activeIndex, target, version, missing}
     width/height are fixed to the asset (game sprites cannot change size). layers is bottom-to-top. target is 'pixels'
     or 'mask': what painting changes on the active layer. version grows with every committed change. missing is a Set of
     what the document uses that no module provides ('blend x', 'effect y', 'adjustment z'): drawn as normal / skipped.
   LAYER = {id, name, kind:'pixel'|'adjustment', canvas (document-sized), visible, opacity 0..1, fill 0..1 (scales the
     layer's own pixels, not its effects), blend (mode id), locks:{alpha (transparent pixels), pixels, position, all},
     mask (document-sized canvas or null; the mask value is its ALPHA, RGB white: 255 shows, 0 hides), maskEnabled,
     clip (clipped to the layer below), effects {type: params} (params.enabled===false hides one), adjustment
     {type, params} (adjustment layers), data {} (JSON for modules, e.g. a text layer's text; saved with the document),
     version (counts pixel changes; use it to cache thumbnails)}.
     Change layers only through Ed.layers.* or inside a history transaction (below), never by assigning fields, so undo,
     the panels, saving and dirty tracking follow. Drawing into layer.canvas / layer.mask is allowed inside a transaction
     after tx.touch() (or with begin(label, [layer])), then call Ed.update(rect).
     Ed.open({width, height, name, image|canvas, layerName}) starts a one-layer document (app.js does this per asset;
     Ed.test.load for tests) · Ed.close()

   LAYERS  Ed.layers
     list() · get(ref) · index(ref) · active() · count()
     add({name, canvas (drawn at x, y), at (index; default above the active one), kind, adjustment, opacity, fill, blend,
       clip, locks, mask, effects, data, history, op}) -> layer; an adjustment layer gets a white mask and the mask target
     remove(ref) (not the last layer; a removed clip base releases its clipped layers) · duplicate(ref) -> layer
     move(fromIndex, toIndex) · setActive(ref, 'pixels'|'mask') (no history, like Photoshop) · set(ref, props, {history})
       props: name, visible, opacity, fill, blend, clip, locks (merged), maskEnabled, mask (canvas|null), effects,
       adjustment, data
     addMask(ref, 'reveal'|'hide'|'selection'|'hide-selection') · removeMask(ref, apply=false)
     Each call is one history state named like Photoshop's ('New Layer', 'Opacity Change'...). Options: history:'Label'
     renames it, op:{...} sets the recipe entry, history:false applies directly without a state and without marking the
     draft dirty; use it inside your own Ed.history.record / begin..commit, or for live slider updates between begin and
     commit (the Layers panel does this).  Ed.setTarget('pixels'|'mask')

   COMPOSITOR  The flattened document is written into S.draft (the host's draft canvas) whenever it changes, so save,
     export, review and Theme AI use it unchanged; snapshot() flushes first. Bottom-to-top with opacity, fill, blend mode,
     layer mask, clipping groups (a base layer and the clip:true layers right above it, blended as a group as Photoshop's
     clipping masks are: inside, members blend with the base as if it were opaque (its colour at Fill), then the base's
     masked transparency applies once to the whole; the base's opacity and blend mode apply to the whole group; hiding
     the base hides the group; an adjustment layer is never a base), adjustment layers (applied to everything below,
     limited by their mask, opacity x fill and blend mode) and effects in Photoshop order: 'behind' effects (drop shadow,
     outer glow) onto what is below, then the content, then 'inner' (overlays, inner glow/shadow, satin, bevel) and 'over'
     (stroke). Opacity fades a layer and its effects as one (an effect hidden under opaque pixels stays hidden at 50%);
     effects get info.opacity 1 then. Previews composite from caches of the layers below and above the active group and
     re-composite only the changed rectangle; frames are scheduled with requestAnimationFrame.
     Ed.update(rect?) schedule a re-composite (rect, or everything) · Ed.flush() composite now · Ed.composite() -> flushed
     draft canvas. Ed.render.composite({layers:[refs], region, into}) -> canvas: a subset, in document order (for merges,
     Stamp Visible, "sample all layers") · Ed.render.layer(ref, {effects:true, opacity:false, region, into}) -> canvas: one
     pixel layer with mask, fill and effects on transparency (blend mode not applied) · Ed.render.below(index) -> canvas ·
     Ed.render.content(ref) -> canvas: the masked pixels. All return new document-sized canvases unless `into` is given.
     Speed: never call getImageData / putImageData on the document's own canvases (layers, masks, the draft, temp()):
     Chrome then moves that canvas to software rendering for the rest of the session and every frame gets slower. Use
     Ed.util.read(canvas, rect) -> ImageData and Ed.util.write(canvas, imageData, rect), which go through a small canvas
     kept for reading. Each read still waits for the GPU, so keep reads out of per-frame paths. Measured stroke frames on
     2048 x 2048 with 3 layers: 4.2 ms on a real GPU, 21-28 ms on SwiftShader.

   BLEND MODES  Ed.registerBlend(id, {label, group, op | fn | composite}) (re-registering replaces)
     group: 'normal'|'darken'|'lighten'|'contrast'|'inversion'|'component' (Photoshop's six groups). Lists follow
     Photoshop's order; ids Photoshop does not have go last in their group. op = a canvas globalCompositeOperation (fast,
     GPU). fn(cb, cs, out) = the per-pixel blend B(cb, cs): unpremultiplied RGB 0..1 arrays, write the 3 results into out;
     the W3C general formula does the alpha (CPU, region only). composite(dst, src, info) = full control for modes that
     are not a color blend (Dissolve): dst and src are ImageData of info.region (src alpha already scaled by opacity x
     fill x mask), info = {region, alpha, doc}; write the result into dst.data. Native ids: normal darken multiply
     color-burn lighten screen color-dodge linear-dodge (per pixel, W3C formula with B = min(1, Cb + Cs): canvas
     'lighter' would add the alphas of half-transparent pixels too) overlay soft-light hard-light difference exclusion
     hue saturation color luminosity.
     Ed.blends.list() -> [{id, label, group}] in menu order · Ed.blends.get(id) · Ed.blends.groups ·
     Ed.blends.mix(id, cb, cs) -> [r, g, b] (adjustment layers use fn for their blend mode)

   ADJUSTMENTS  Ed.registerAdjustment(type, {label, defaults, apply(imageData, params, info)}): change unpremultiplied
     RGBA in place, keep alpha; info = {region, layer, doc}. An adjustment layer {kind:'adjustment', adjustment:{type,
     params}} applies it to the composite below (or to its clipping group). Ed.adjustments.get(type) / list().
   EFFECTS  Ed.registerEffect(type, {label, stage:'behind'|'inner'|'over', order, defaults, pad(params) -> px,
     render(ctx, info)}). render draws onto ctx in document coordinates; ctx is the real destination (what is below for
     'behind'), so use your own blend mode and globalAlpha; ctx state is saved and restored around you. info = {layer,
     params, source (the layer's masked pixels, document-sized; Fill not applied), region {x,y,w,h} to update (draw at
     least that), opacity (multiply your alpha by it), doc, temp() -> a cleared, reset document-sized scratch canvas,
     free(canvas) (give every temp back), blit(ctx, src, rect, op, alpha)}. Effects ignore Fill. order sorts within a stage
     (suggested: drop shadow 10, outer glow 20, pattern overlay 30, gradient overlay 40, color overlay 50, satin 60, inner
     glow 70, inner shadow 80, bevel 90, stroke 100). pad(params) = how far outside the pixels the effect reaches, so the
     changed area of a stroke grows by it. Ed.effects.get(type) / list().

   PREVIEWS  Never history; one at a time (starting one replaces the last).
     Ed.preview.stroke({layer, canvas, opacity, mode, color, selection:true}) -> preview: a stroke buffer composited into
       a layer while painting. canvas = document-sized buffer, alpha = coverage (paint it in the color you want). mode =
       canvas operation ('source-over' paints, 'destination-out' erases, a blend mode). On a mask target the buffer paints
       the gray (luminance) of `color`. The selection clips it (selection:false to ignore). Lock transparency turns
       source-over into source-atop, keeps the old alpha for blend modes and skips modes that change alpha.
     Ed.preview.layer({layer, canvas, target:'pixels'|'mask'}) shows a whole replacement (filters, dialogs, transform).
     Ed.preview.update(rect?) after changing the buffer/canvas · Ed.preview.clear() · Ed.preview.active
     Ed.commitStroke(label, {rect, op}) applies the active stroke preview to its layer as one state ('Brush Tool').
     Ed.strokeBuffer() -> the shared, cleared, reset document-sized stroke buffer.
     Ed.strokeInto(dstCanvas, {canvas, opacity, mode, color, target, lockAlpha, selection}, rect) applies a buffer the same
     way to any canvas (for tools that build their own previews).

   SELECTION  Ed.selection = null (nothing selected: operations apply everywhere) or a document-sized canvas, mask in
     ALPHA, RGB white. Never draw into it; make a new mask and call set().
     Ed.select.set(maskCanvas, mode='replace'|'add'|'subtract'|'intersect', {label}) (add = screen of alphas, subtract
     and intersect exact; an empty result deselects) · rect(x, y, w, h, mode, {label}) · all() · none() · invert()
     reselect() · bounds() -> {x,y,w,h}|null · hasSelection() · getMask() · outline() -> {h: Int32Array [y,x0,x1,...],
     v: Int32Array [x,y0,y1,...]} (the 50% boundary as merged runs; the shell draws the marching ants from it) · version.
     Every change is a history state ({history:false} on set() inside an open transaction makes the change part of that
     transaction's state instead, e.g. moving selected pixels together with their selection); the shell binds Ctrl+A,
     Ctrl+D, Shift+Ctrl+D, Shift+Ctrl+I, Delete/Backspace.
   PIXEL OPERATIONS THAT RESPECT THE SELECTION
     Ed.applyToTarget(label, draw(ctx, info), {rect, op}) -> Promise<bool>. draw (may be async) changes a copy of the
       target (layer pixels or mask) through ctx; info = {layer, target, canvas, selection, rect, doc}. The result goes
       back through the soft selection (only rect, default the selection's bounds), lock transparency keeps the old alpha,
       hidden/locked/adjustment targets refuse with a message, and it becomes one history state.
     Ed.editable({view:false}) -> '' or a plain sentence why the active target cannot change (view:false skips the
       "Texture view with Original off" rule, which only applies to tools that paint at a pointer).
     Ed.sample(x, y, {size:1|3|5, from:'composite'|'layer'}) -> '#rrggbb' or null (fully transparent).

   HISTORY  Photoshop-style states; the first is the opened image. Ed.history:
     begin(label, layerRefs, {rect, mask:true|'only', op}) -> tx: remembers structure, selection and the before-pixels of
       the given layers (whole canvases, or only rect). tx.touch(layer, 'pixels'|'mask', rect) remembers a rectangle just
       before you change it (cheap; strokes use it). commit({rect, op}) / tx.commit() stores only what changed (with no
       rect the changed area is found by comparing). cancel() / tx.cancel() puts everything back. Only one transaction is
       open: begin() commits an open one. record(label, fn(tx), opts) = begin + fn + commit (cancel when fn throws; fn may
       return a promise).
     undo() · redo() · jump(index) · list() -> [{index, label, current, undone}] · index · label(i) · canUndo() ·
     canRedo() · truncate() (drops the states after the current one, as a new change after going back does; returns how
     many) · bytes · open (the open tx). limits = {states: 50, bytes: 512 MB}: the oldest states drop first. A new
     state after going back drops the later ones. Each state marks the draft dirty (S.dirty, badge, Save button) and adds
     its op (default {tool: label}) to S.ops, the saved recipe; keep ops tiny (the recipe is capped at 64 KB).

   TOOLS  Ed.registerTool(id, def) (re-registering an id replaces it; core ids: 'hand' H, 'zoom' Z, 'eyedropper' I,
     'brush' B, 'eraser' E). def = {label, icon (text, an Element, or a function returning one; Ed.icon(pathData|[...],
     {size}) builds a 24 px stroke SVG), group (strip slot, Photoshop order: 'move','marquee','lasso','wand','eyedropper',
     'healing','brush','clone','history','eraser','fill','blur','dodge','type','shape','hand','zoom'; others go before
     Hand), order, key ('b'; Shift+key cycles the group's tools with that key), cursor ('crosshair', 'brush' = outline of
     options.size, 'eyedropper', 'zoom-in', 'grab' or any CSS cursor), options [descriptor], edits (true: needs an editable
     target, Texture view and Original off; refused with a message otherwise), altEyedropper (Alt-click samples the
     foreground), and handlers called as methods of the registered tool (keep state on `this`; errors are caught, shown and
     collected in Ed.test.errors): onDown(p, e) (return false to cancel the drag), onMove(p, e) (while pressed, once per
     coalesced sample), onUp(p, e), onHover(p, e), onKey(e) -> true when handled (it gets keys before menus and shortcuts),
     onActivate(), onDeactivate(), onCancel() (Escape), onCommit() (Enter), onOptions(values),
     drawOverlay(ctx, view) (viewport CSS px; view = {x, y, scale, width, height, toScreen(x, y), toImage(x, y)})}.
     p = {x, y (float image coordinates), pressure (pen/touch pressure; 1 for a pressed mouse), tiltX, tiltY, twist,
     buttons, button, shiftKey, altKey, ctrlKey (Ctrl or Cmd), pointerType, clientX, clientY, time}.
     Middle/right drag and Space pan with any tool; the wheel zooms at the cursor (the shell handles these).
     Ed.setTool(id) · Ed.tool (active id; S.tool mirrors it) · Ed.tools.get(id) / list() / groups · Ed.toolSlots() ->
     [{group, label, tools, current}] · Ed.callTool(name, ...args) calls the active tool safely.
     Option descriptor = {key, label, type:'range'|'number'|'select'|'checkbox'|'color'|'segmented'|'button'|'text', min,
     max, step, unit, options:[{value, label, title}] ('-' = separator in a select), default, id (element id), title,
     action() (buttons), translate (false: a select of names, like fonts, that the page translation must leave alone)}.
     Values persist per tool in localStorage. Ed.toolOptions(id) -> the live values (read-only) ·
     Ed.setToolOptions(id, values) (clamped, saved, 'options' event). A 'size' option gets [ and ], 'hardness' Shift+[ ],
     'opacity' the digit keys (5 = 50%, 0 = 100%, two quick digits = exact; without one, digits set the layer opacity).
     Shortcuts the shell owns: tool keys, [ ], digits, X (swap colors), D (default colors), Space, Escape (onCancel, clear
     the preview), Enter (onCommit) and every menu shortcut. Keys never act while an input, select, textarea or dialog
     has focus.

   MENUS  Ed.registerMenu(path, {action(params), shortcut ('Ctrl+Shift+N', 'Delete', or an array), enabled(), checked(),
     order (number within its menu), separatorBefore, label (string or function, e.g. 'Undo Brush Tool')}). path =
     'Filter/Blur/Gaussian Blur...'; the menubar is File Edit Image Layer Select Filter View, then any other top menu.
     Ed.menu.run(path, params) runs it (false when disabled); with params a command must skip its dialog. Ed.menu.list()
     -> [{path, shortcut, enabled, checked}] · Ed.menu.tree() · Ed.menu.get(path) · Ed.menu.forEvent(keyEvent) -> path.
     Ed.keys.matches(keyEvent, 'Ctrl+Z') · Ed.keys.parse(spec). Shortcut display follows Photoshop ('Shift+Ctrl+N').
     Core menus: File (Import Image as Layer..., Save Edit Ctrl+S, Export PNG, Export Recipe, Revert to Original), Edit
     (Undo Ctrl+Z, Redo Shift+Ctrl+Z / Ctrl+Y, Clear Delete), Image (Color & Surface...), Layer (New Layer Shift+Ctrl+N,
     Duplicate Layer Ctrl+J, Delete Layer, Layer Mask/..., Create Clipping Mask Alt+Ctrl+G), Select (All, Deselect,
     Reselect, Inverse), View (Zoom In/Out, Fit on Screen Ctrl+0, 100% Ctrl+1, Pixel Grid, Crisp Pixels, Show Original,
     Preview As/..., Focus Editor F).

   DIALOGS (shell)  Ed.dialog({title, text, fields:[descriptor], values, preview(params), ok(params), cancel(), okLabel,
     delay}) -> Promise<params|null> (the promise also has .element). Modal and docked top right so the picture stays in
     view; preview(params) runs debounced (delay, 70 ms) and should show its result with Ed.preview.layer/stroke; the
     Preview checkbox clears it; OK clears the preview and then awaits ok(params) (do the real work there, e.g. with
     Ed.applyToTarget); Cancel/Escape clears it and calls cancel(); Reset restores the defaults. The <dialog> has
     data-ed-dialog=title and every input has name=field key. Field types are the tool option types plus custom ones:
     Ed.registerField(type, render(field, value, set(v)) -> Element) (for 'curve', 'histogram'...; without a renderer the
     field explains which module is missing). Ed.ui.field(descriptor, value, set(v, done)) -> {node, sync(v)} renders
     any option control for your own panels.
   COLORS  Ed.fg / Ed.bg ('#rrggbb', remembered) · Ed.setColor('fg'|'bg', hex) · Ed.swapColors() · Ed.defaultColors()
     (black/white; white/black on a mask) · event 'color' · Ed.registerColorPicker(fn({which, color, anchor}) ->
     hex|null|Promise; null restores the native picker) · Ed.pickColor(which, anchorElement). The chips at the bottom of
     the tool strip use it; #paintColor / #paintBackground are the native inputs behind them.
   PANELS (shell)  Ed.registerPanel(id, {title, order, render(container)}): a tab in the dock above the inspector (Layers
     10, History 20). render runs when the tab is shown and again (once per frame) after document, layer, history and
     selection changes; keep your DOM in `container` and update it. Ed.panels.show(id) / refresh(id?) / list() / get(id).
     Ed.registerLayerBadge(fn(layer, row) -> Element|string|null) adds badges to Layers rows ('fx', link icons...).
     Ed.registerOverlay(id, {order, draw(ctx, view) -> true when it drew}) draws on the view overlay whatever the tool
     (guides, grid, mask view...), above the pixel grid, under the marching ants and the tool's own overlay; view is the
     same object tools' drawOverlay gets. Call Ed.view.redrawOverlay() when its picture changes. Re-registering replaces.
   VIEW Ed.view.toClient(x, y) -> {x, y} page CSS px (integer image coordinates are pixel corners) · toImage(clientX,
     clientY) -> {x, y} · scale · transform {x, y, scale, left, top, width, height} · setZoom(z, clientX?, clientY?) (keeps
     the point under the cursor) · zoomIn / zoomOut (Photoshop's steps: Ed.view.zooms) · fit() · actual() · panBy(dx, dy)
     · getState() / setState({zoom, px, py}) · redraw() · redrawOverlay() · pixelGrid (get/set; drawn from 800%) ·
     blocked ('' or why painting is off: another preview mode or Original). The view is crisp above 100%.
   EVENTS  Ed.on(name, fn) -> off() · Ed.off(name, fn) · Ed.emit(name, detail). 'open' (doc|null), 'document' ({reason:
     'open'|'restore'|'commit'|'undo'|'redo'|'history'}), 'layers', 'active', 'selection', 'history', 'preview', 'tool',
     'tools', 'options' ({id, values}), 'color', 'view', 'render' (after each view frame), 'overlay', 'menus', 'panels',
     'blends', 'registry'. DOM: document receives CustomEvent 'lab:asset' (detail = the asset, sounds included) after an
     asset loads and 'lab:document' ({reason}) after the editor document changes.
   PERSISTENCE  Saving posts the flattened PNG plus Ed.document.serialize() -> Promise<{version:1, width, height, name,
     activeIndex, target, layers:[{id, name, kind, visible, opacity, fill, blend, locks, maskEnabled, clip, effects,
     adjustment, data, png, mask_png}]}> (base64 PNGs) to /api/save; the lab keeps it as revisions/<key>/<version>.layers.json.
     A single plain layer is not stored (Ed.document.trivial()). Opening a revision with layers runs
     Ed.document.restore(json, flatImage) -> Promise<{ok, reason, worst, changed}>: it rebuilds the layers and checks the
     composite against the saved PNG (tolerance 4 premultiplied); a mismatch or a missing module keeps the flat image as one
     layer and the player is told. If the document changed while the layers loaded it keeps that work (changed: true). Modules: keep everything a layer needs in layer.data / effects / adjustment (JSON, max 256 KB each).
   HOST (app.js)  Ed.attach({draft, draw, fit, message, changed(op), transform(), getView(), setView(v), canPaint(),
     opened(doc)}) plugs the engine into the page; Ed.loading(promise) marks an asset load for Ed.test.ready.
   UTILITIES  Ed.util = {clamp, canvas(w, h), clone, toHex([r,g,b]), rgbOf(hex), luma(hex), union(a, b), grow(rect, n),
     area(rect) (clipped to the document), b64(bytes), unb64(text), store (localStorage get/set in try/catch), read(canvas,
  rect) -> ImageData, write(canvas, imageData, rect) (pixel access that keeps the canvas on the GPU, see Speed)} ·
     Ed.icon(paths, {size, width, fill, viewBox}) · Ed.message(text, isError)
   TEST HOOKS  Ed.ready (Promise) · Ed.doc · Ed.view.toClient / toImage · Ed.test.pixels('composite' | layer index |
     'selection' | 'mask') -> Promise<{width, height, data: base64 RGBA}> ('selection' and 'mask' carry the mask in alpha;
     no selection reads all 0, no mask all 255) · Ed.test.load(width, height, base64Rgba) (a fresh one-layer document
     without the server) · Ed.test.ready() (after an asset and its layers are shown) · Ed.test.errors (exceptions caught in
     tools, events, effects, panels, dialogs) · Ed.test.state (layers, active, target, history, selection, tool) ·
     Ed.setTool / Ed.tool / Ed.toolOptions / Ed.setToolOptions · Ed.menu.run / list · Ed.history.undo / redo / list / jump
   ===================================================================================================================== */
(()=>{
const Ed=window.Ed={};
let doc=null;

// ----- helpers ---------------------------------------------------------------------------------------------------------
const clamp=(v,lo,hi)=>Math.min(hi,Math.max(lo,v));
const canvas=(w,h)=>{const c=document.createElement('canvas');c.width=Math.max(1,w|0);c.height=Math.max(1,h|0);return c;};
const g2=c=>c.getContext('2d');
const clone=v=>v==null?v:JSON.parse(JSON.stringify(v));
const toHex=c=>'#'+[0,1,2].map(i=>clamp(Math.round(c[i]),0,255).toString(16).padStart(2,'0')).join('');
const rgbOf=h=>{h=String(h||'#000').replace('#','');if(h.length===3)h=[...h].map(c=>c+c).join('');const n=parseInt(h.slice(0,6),16)||0;return[n>>16&255,n>>8&255,n&255];};
const luma=h=>{const[r,g,b]=rgbOf(h);return .299*r+.587*g+.114*b;};
const union=(a,b)=>!a?b:!b?a:{x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),w:Math.max(a.x+a.w,b.x+b.w)-Math.min(a.x,b.x),h:Math.max(a.y+a.h,b.y+b.h)-Math.min(a.y,b.y)};
const grow=(r,n)=>r&&n?{x:r.x-n,y:r.y-n,w:r.w+2*n,h:r.h+2*n}:r;
function area(r){ // an integer rectangle inside the document, or null when empty
  if(!doc)return null;if(!r)return{x:0,y:0,w:doc.width,h:doc.height};
  const x=Math.max(0,Math.floor(r.x)),y=Math.max(0,Math.floor(r.y)),x2=Math.min(doc.width,Math.ceil(r.x+r.w)),y2=Math.min(doc.height,Math.ceil(r.y+r.h));
  return x2>x&&y2>y?{x,y,w:x2-x,h:y2-y}:null;}
const b64=u8=>{let s='';for(let i=0;i<u8.length;i+=0x8000)s+=String.fromCharCode.apply(null,u8.subarray(i,i+0x8000));return btoa(s);};
const unb64=s=>{const t=atob(s),u=new Uint8Array(t.length);for(let i=0;i<t.length;i++)u[i]=t.charCodeAt(i);return u;};
function report(e,where){Ed.test.errors.push({where,message:String(e&&e.message||e),stack:String(e&&e.stack||'')});console.error('[editor] '+where+':',e);}
const store={get(k,d){try{const v=localStorage.getItem(k);return v==null?d:JSON.parse(v);}catch(e){return d;}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch(e){}}};
Ed.util={clamp,canvas,clone,toHex,rgbOf,luma,union,grow,area,b64,unb64,store};

// ----- events and the host (app.js plugs in the draft canvas, the view and dirty tracking) -----------------------------
const listeners={};
Ed.on=(name,fn)=>{(listeners[name]||(listeners[name]=[])).push(fn);return()=>Ed.off(name,fn);};
Ed.off=(name,fn)=>{if(listeners[name])listeners[name]=listeners[name].filter(f=>f!==fn);};
Ed.emit=(name,detail)=>{for(const fn of (listeners[name]||[]).slice()){try{fn(detail);}catch(e){report(e,'event '+name);}}};
const host={draft:null,draw(){},changed(){},message(){},opened(){},canPaint:()=>'',fit(){},
  transform:()=>({x:0,y:0,scale:1,left:0,top:0,width:0,height:0}),getView:()=>({zoom:1,px:0,py:0}),setView(){}};
Ed.attach=o=>{Object.assign(host,o);};
Ed.message=(text,error=false)=>host.message(text,error);
function changedDoc(reason,op){if(!doc)return;doc.version++;host.changed(op);Ed.emit('document',{reason});document.dispatchEvent(new CustomEvent('lab:document',{detail:{reason}}));}

// ----- blend modes ------------------------------------------------------------------------------------------------------
const blends=new Map();
const BLEND_ORDER=['normal','dissolve','darken','multiply','color-burn','linear-burn','darker-color','lighten','screen','color-dodge','linear-dodge','lighter-color','overlay','soft-light','hard-light','vivid-light','linear-light','pin-light','hard-mix','difference','exclusion','subtract','divide','hue','saturation','color','luminosity'];
const BLEND_GROUPS=['normal','darken','lighten','contrast','inversion','component'];
let blendSeq=0;
Ed.registerBlend=(id,def)=>{blends.set(id,{id,label:def.label||id,group:BLEND_GROUPS.includes(def.group)?def.group:'normal',op:def.op||null,fn:def.fn||null,composite:def.composite||null,seq:blends.get(id)?.seq??++blendSeq});Ed.emit('blends');};
const rank=b=>{const i=BLEND_ORDER.indexOf(b.id);return BLEND_GROUPS.indexOf(b.group)*1000+(i<0?900+b.seq:i);};
const lum=c=>.3*c[0]+.59*c[1]+.11*c[2];
function clipColor(c){const l=lum(c),n=Math.min(c[0],c[1],c[2]),x=Math.max(c[0],c[1],c[2]);
  if(n<0)for(let i=0;i<3;i++)c[i]=l+(c[i]-l)*l/(l-n);if(x>1)for(let i=0;i<3;i++)c[i]=l+(c[i]-l)*(1-l)/(x-l);return c;}
function setLum(c,l,o){const d=l-lum(c);o[0]=c[0]+d;o[1]=c[1]+d;o[2]=c[2]+d;return clipColor(o);}
const satOf=c=>Math.max(c[0],c[1],c[2])-Math.min(c[0],c[1],c[2]);
function setSat(c,s,o){const i=[0,1,2].sort((a,b)=>c[a]-c[b]),lo=c[i[0]],mid=c[i[1]],hi=c[i[2]];const r=[0,0,0];
  if(hi>lo){r[i[1]]=(mid-lo)*s/(hi-lo);r[i[2]]=s;}o[0]=r[0];o[1]=r[1];o[2]=r[2];return o;}
const sep=f=>(cb,cs,o)=>{o[0]=f(cb[0],cs[0]);o[1]=f(cb[1],cs[1]);o[2]=f(cb[2],cs[2]);};
const hardLight=(b,s)=>s<=.5?b*2*s:b+(2*s-1)-b*(2*s-1);
const T3=[0,0,0];
const NATIVE=[['normal','Normal','normal','source-over',sep((b,s)=>s)],['darken','Darken','darken','darken',sep(Math.min)],
  ['multiply','Multiply','darken','multiply',sep((b,s)=>b*s)],['color-burn','Color Burn','darken','color-burn',sep((b,s)=>b>=1?1:s<=0?0:1-Math.min(1,(1-b)/s))],
  ['lighten','Lighten','lighten','lighten',sep(Math.max)],['screen','Screen','lighten','screen',sep((b,s)=>b+s-b*s)],
  ['color-dodge','Color Dodge','lighten','color-dodge',sep((b,s)=>b<=0?0:s>=1?1:Math.min(1,b/(1-s)))],
  ['linear-dodge','Linear Dodge (Add)','lighten',null,sep((b,s)=>Math.min(1,b+s))],   // per pixel: canvas 'lighter' adds alpha too
  ['overlay','Overlay','contrast','overlay',sep((b,s)=>hardLight(s,b))],
  ['soft-light','Soft Light','contrast','soft-light',sep((b,s)=>s<=.5?b-(1-2*s)*b*(1-b):b+(2*s-1)*((b<=.25?((16*b-12)*b+4)*b:Math.sqrt(b))-b))],
  ['hard-light','Hard Light','contrast','hard-light',sep(hardLight)],['difference','Difference','inversion','difference',sep((b,s)=>Math.abs(b-s))],
  ['exclusion','Exclusion','inversion','exclusion',sep((b,s)=>b+s-2*b*s)],
  ['hue','Hue','component','hue',(b,s,o)=>setLum(setSat(s,satOf(b),T3),lum(b),o)],
  ['saturation','Saturation','component','saturation',(b,s,o)=>setLum(setSat(b,satOf(s),T3),lum(b),o)],
  ['color','Color','component','color',(b,s,o)=>setLum(s,lum(b),o)],['luminosity','Luminosity','component','luminosity',(b,s,o)=>setLum(b,lum(s),o)]];
for(const [id,label,group,op,fn] of NATIVE)Ed.registerBlend(id,{label,group,op,fn});
Ed.blends={get:id=>blends.get(id),list:()=>[...blends.values()].sort((a,b)=>rank(a)-rank(b)).map(b=>({id:b.id,label:b.label,group:b.group})),
  groups:BLEND_GROUPS,mix(id,cb,cs){const b=blends.get(id)||blends.get('normal'),o=[0,0,0];(b.fn||NATIVE[0][4])(cb,cs,o);return o;}};
// The W3C general formula: Cs' = (1 - ab) Cs + ab B(Cb, Cs), then source-over. dst/src are unpremultiplied RGBA bytes.
function blendPixels(dst,src,alpha,fn){const cb=[0,0,0],cs=[0,0,0],o=[0,0,0];
  for(let i=0;i<dst.length;i+=4){const as=src[i+3]/255*alpha;if(as<=0)continue;const ab=dst[i+3]/255;
    cs[0]=src[i]/255;cs[1]=src[i+1]/255;cs[2]=src[i+2]/255;cb[0]=dst[i]/255;cb[1]=dst[i+1]/255;cb[2]=dst[i+2]/255;
    if(ab>0)fn(cb,cs,o);else{o[0]=cs[0];o[1]=cs[1];o[2]=cs[2];}
    const ao=as+ab*(1-as);
    for(let k=0;k<3;k++)dst[i+k]=Math.round(255*(as*(1-ab)*cs[k]+as*ab*clamp(o[k],0,1)+(1-as)*ab*cb[k])/ao);
    dst[i+3]=Math.round(255*ao);}}

// ----- adjustment, effect, field and badge registries -------------------------------------------------------------------
const adjustments=new Map(),effects=new Map();
Ed.registerAdjustment=(type,def)=>{adjustments.set(type,{type,label:def.label||type,defaults:def.defaults||{},apply:def.apply});Ed.emit('registry',{kind:'adjustment',type});};
Ed.adjustments={get:t=>adjustments.get(t),list:()=>[...adjustments.values()]};
Ed.registerEffect=(type,def)=>{effects.set(type,{type,label:def.label||type,stage:['behind','inner','over'].includes(def.stage)?def.stage:'over',order:def.order??50,pad:def.pad||(()=>0),render:def.render,defaults:def.defaults||{}});Ed.emit('registry',{kind:'effect',type});};
Ed.effects={get:t=>effects.get(t),list:()=>[...effects.values()].sort((a,b)=>a.order-b.order)};
const fields=new Map();Ed.registerField=(type,render)=>{fields.set(type,render);};Ed.fields={get:t=>fields.get(t)};
const badges=[];Ed.registerLayerBadge=fn=>{badges.push(fn);Ed.emit('layers');};Ed.layerBadges=()=>badges.slice();
const overlays=new Map();Ed.registerOverlay=(id,def)=>{overlays.set(id,{id,order:100,...(typeof def==='function'?{draw:def}:def)});Ed.emit('overlay');};
Ed.overlays=()=>[...overlays.values()].sort((a,b)=>a.order-b.order);

// ----- document and layers ----------------------------------------------------------------------------------------------
Object.defineProperty(Ed,'doc',{get:()=>doc,enumerable:true});
const PROPS=['name','visible','opacity','fill','blend','clip','maskEnabled'];
function makeLayer(o={}){
  const L={id:o.id||'L'+doc.nextId++,name:o.name||'Layer '+doc.counter++,kind:o.kind==='adjustment'?'adjustment':'pixel',canvas:o.canvasReady||canvas(doc.width,doc.height),
    visible:o.visible!==false,opacity:clamp(o.opacity??1,0,1),fill:clamp(o.fill??1,0,1),blend:o.blend||'normal',
    locks:{alpha:false,pixels:false,position:false,all:false,...(o.locks||{})},mask:o.mask||null,maskEnabled:o.maskEnabled!==false,clip:!!o.clip,
    effects:clone(o.effects)||{},adjustment:clone(o.adjustment)||null,data:clone(o.data)||{},version:0};
  if(o.canvas&&!o.canvasReady)g2(L.canvas).drawImage(o.canvas,o.x||0,o.y||0);
  return L;}
function resolve(ref){if(!doc)return null;if(ref==null)return doc.layers[doc.activeIndex]||null;if(typeof ref==='object')return doc.layers.includes(ref)?ref:null;
  if(typeof ref==='number')return doc.layers[ref]||null;return doc.layers.find(L=>L.id===ref)||null;}
const active=()=>doc?doc.layers[doc.activeIndex]||null:null;
const targetOf=L=>L&&L.kind==='adjustment'&&L.mask?'mask':'pixels';   // what a newly selected layer paints on
Ed.open=(o)=>{
  closeTx();pv=null;cache=null;dirty=null;
  doc={width:o.width|0,height:o.height|0,name:o.name||'Untitled',layers:[],activeIndex:0,target:'pixels',nextId:1,counter:1,version:0,missing:new Set()};
  pool.length=0;
  if(o.layers)doc.layers=o.layers;else{const L=makeLayer({name:o.layerName||o.name||'Layer 0',canvas:o.image||o.canvas});doc.layers.push(L);}
  selection=null;lastSelection=null;selInfo=null;
  if(host.draft){host.draft.width=doc.width;host.draft.height=doc.height;}
  history.reset();Ed.update();host.opened(doc);
  Ed.emit('open',doc);Ed.emit('selection');Ed.emit('layers');Ed.emit('active');Ed.emit('history');Ed.emit('document',{reason:'open'});
  document.dispatchEvent(new CustomEvent('lab:document',{detail:{reason:'open'}}));
  return doc;};
Ed.close=()=>{closeTx();pv=null;cache=null;doc=null;selection=null;selInfo=null;history.reset();Ed.emit('open',null);Ed.emit('layers');Ed.emit('history');Ed.emit('selection');};

function structureSnap(){return{layers:doc.layers.map(L=>({L,mask:L.mask,locks:{...L.locks},effects:clone(L.effects),adjustment:clone(L.adjustment),data:clone(L.data),...Object.fromEntries(PROPS.map(k=>[k,L[k]]))})),active:doc.activeIndex,target:doc.target};}
function snapKey(s){return JSON.stringify([s.active,s.target,s.layers.map(x=>[x.L.id,x.mask?1:0,x.locks,x.effects,x.adjustment,x.data,PROPS.map(k=>x[k])])]);}
function sameSnap(a,b){return a.layers.length===b.layers.length&&a.layers.every((x,i)=>x.L===b.layers[i].L&&x.mask===b.layers[i].mask)&&snapKey(a)===snapKey(b);}
function restoreSnap(s){doc.layers=s.layers.map(x=>{const L=x.L;for(const k of PROPS)L[k]=x[k];L.mask=x.mask;L.locks={...x.locks};L.effects=clone(x.effects);L.adjustment=clone(x.adjustment);L.data=clone(x.data);return L;});
  doc.activeIndex=clamp(s.active,0,Math.max(0,doc.layers.length-1));doc.target=s.target==='mask'&&active()?.mask?'mask':'pixels';}

Ed.layers={
  list:()=>doc?doc.layers.slice():[],count:()=>doc?doc.layers.length:0,get:resolve,index:ref=>{const L=resolve(ref);return L?doc.layers.indexOf(L):-1;},active,
  add(o={}){if(!doc)return null;let L;run(o.history,'New Layer',o.op||{tool:'layer',action:'new'},()=>{
      L=makeLayer(o);if(L.kind==='adjustment'&&!o.mask&&o.withMask!==false){L.mask=canvas(doc.width,doc.height);const m=g2(L.mask);m.fillStyle='#fff';m.fillRect(0,0,doc.width,doc.height);}
      const at=clamp(o.at??doc.activeIndex+1,0,doc.layers.length);doc.layers.splice(at,0,L);doc.activeIndex=at;doc.target=L.kind==='adjustment'&&L.mask?'mask':'pixels';});
    return L;},
  remove(ref,o={}){const L=resolve(ref);if(!L)return false;if(doc.layers.length<2){Ed.message('A document needs at least one layer.');return false;}
    run(o.history,'Delete Layer',o.op||{tool:'layer',action:'delete'},()=>{const i=doc.layers.indexOf(L);doc.layers.splice(i,1);
      if(!L.clip||i===0)for(let k=i;k<doc.layers.length&&doc.layers[k].clip;k++)doc.layers[k].clip=false;   // a deleted base releases its clipped layers
      doc.activeIndex=clamp(i-1<0?0:i-1,0,doc.layers.length-1);doc.target=targetOf(active());});return true;},
  duplicate(ref,o={}){const S=resolve(ref);if(!S)return null;let L;run(o.history,'Duplicate Layer',o.op||{tool:'layer',action:'duplicate'},()=>{
      L=makeLayer({...S,id:null,name:o.name||S.name+' copy',canvas:S.canvas,mask:null,locks:S.locks,effects:S.effects,adjustment:S.adjustment,data:S.data,kind:S.kind});
      if(S.mask){L.mask=canvas(doc.width,doc.height);g2(L.mask).drawImage(S.mask,0,0);}
      const i=doc.layers.indexOf(S)+1;doc.layers.splice(i,0,L);doc.activeIndex=i;doc.target=targetOf(L);});return L;},
  move(from,to,o={}){if(!doc)return false;const i=typeof from==='number'?from:Ed.layers.index(from);const j=clamp(to,0,doc.layers.length-1);if(i<0||i>=doc.layers.length||i===j)return false;
    run(o.history,'Layer Order',{tool:'layer',action:'move'},()=>{const L=doc.layers.splice(i,1)[0];doc.layers.splice(j,0,L);if(doc.layers[0]?.clip)doc.layers[0].clip=false;doc.activeIndex=j;});return true;},
  setActive(ref,target){const L=resolve(ref);if(!L)return false;const i=doc.layers.indexOf(L);const t=target==='mask'&&L.mask?'mask':target==='pixels'?'pixels':L.kind==='adjustment'&&L.mask?'mask':'pixels';
    if(i===doc.activeIndex&&t===doc.target)return true;doc.activeIndex=i;doc.target=t;cache=null;Ed.emit('active');Ed.emit('layers');return true;},
  set(ref,props,o={}){const L=resolve(ref);if(!L)return false;const label=o.history||setLabel(props);
    const apply=()=>{for(const [k,v] of Object.entries(props)){
      if(k==='locks')L.locks={...L.locks,...v};else if(k==='opacity'||k==='fill')L[k]=clamp(+v,0,1);else if(k==='effects'||k==='adjustment'||k==='data')L[k]=clone(v);
      else if(k==='mask'){L.mask=v||null;if(!v&&doc.target==='mask'&&L===active())doc.target='pixels';}else if(PROPS.includes(k))L[k]=v;}
      if(L.clip&&doc.layers.indexOf(L)===0)L.clip=false;};
    if(o.history===false){apply();cache=null;Ed.update(fxArea(L));Ed.emit('layers');return true;}   // a live preview's cache may hold this layer
    run(label,label,{tool:'layer',action:Object.keys(props).join(',')},apply);return true;},
  addMask(ref,how='reveal',o={}){const L=resolve(ref);if(!L)return false;if(L.mask){Ed.message('This layer already has a mask.');return false;}
    run(o.history,'Add Layer Mask',{tool:'mask',action:how},()=>{const m=canvas(doc.width,doc.height),g=g2(m);
      if(how==='reveal'||how==='hide-selection'){g.fillStyle='#fff';g.fillRect(0,0,doc.width,doc.height);}
      if(how==='selection'&&selection)g.drawImage(selection,0,0);if(how==='selection'&&!selection){g.fillStyle='#fff';g.fillRect(0,0,doc.width,doc.height);}
      if(how==='hide-selection'&&selection){g.globalCompositeOperation='destination-out';g.drawImage(selection,0,0);g.globalCompositeOperation='source-over';}
      L.mask=m;L.maskEnabled=true;doc.activeIndex=doc.layers.indexOf(L);doc.target='mask';});return true;},
  removeMask(ref,apply=false,o={}){const L=resolve(ref);if(!L||!L.mask)return false;
    run(o.history,apply?'Apply Layer Mask':'Delete Layer Mask',{tool:'mask',action:apply?'apply':'delete'},t=>{
      if(apply&&L.kind==='pixel'){t.touch(L,'pixels',area());blit(g2(L.canvas),L.mask,area(),'destination-in');}L.mask=null;if(doc.target==='mask')doc.target='pixels';});return true;},
};
function setLabel(p){const k=Object.keys(p);if(k.length!==1)return 'Layer Properties';
  return {opacity:'Opacity Change',fill:'Fill Opacity Change',blend:'Blending Change',name:'Rename Layer',visible:p.visible?'Show Layer':'Hide Layer',locks:'Lock Change',clip:p.clip?'Create Clipping Mask':'Release Clipping Mask',
    effects:'Layer Style',adjustment:'Modify Adjustment Layer',mask:'Layer Mask',maskEnabled:p.maskEnabled?'Enable Layer Mask':'Disable Layer Mask',data:'Layer Change'}[k[0]]||'Layer Properties';}
// One history state for a structure change (or a plain change with history:false).
function run(history,label,op,fn){if(history===false){fn({touch(){}});cache=null;Ed.update();Ed.emit('layers');Ed.emit('active');return;}
  Ed.history.record(typeof history==='string'?history:label,fn,{op});}
Ed.setTarget=t=>{const L=active();if(!L)return false;return Ed.layers.setActive(L,t==='mask'&&L.mask?'mask':'pixels');};

// ----- the compositor ---------------------------------------------------------------------------------------------------
const pool=[];
// Scratch canvases are reused; a reused one comes back with a fresh context state (a module may have left a mode set).
function tmp(){const c=pool.pop();if(c&&c.width===doc.width&&c.height===doc.height){c._tmp=true;const g=g2(c);if(g.reset)g.reset();else{g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.globalCompositeOperation='source-over';g.filter='none';g.clearRect(0,0,c.width,c.height);}return c;}
  const n=canvas(doc.width,doc.height);n._tmp=true;return n;}
function free(c){if(c&&c._tmp&&doc&&c.width===doc.width&&c.height===doc.height&&pool.length<8&&!pool.includes(c))pool.push(c);}
const UNBOUNDED=new Set(['copy','source-in','source-out','destination-in','destination-atop']);
function clip(g,R){g.save();g.beginPath();g.rect(R.x,R.y,R.w,R.h);g.clip();}
// Engine copies never trust a context's leftover state: a module (or addMask) may leave a mode, alpha, transform, filter
// or shadow on a layer or mask context, and history must still put back exactly what it saved.
function plain(g){g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.globalCompositeOperation='source-over';g.filter='none';g.shadowColor='transparent';g.shadowBlur=0;}
function blit(g,src,R,op='source-over',alpha=1){if(alpha<=0)return;g.save();plain(g);if(UNBOUNDED.has(op)){g.beginPath();g.rect(R.x,R.y,R.w,R.h);g.clip();}
  g.globalAlpha=alpha;g.globalCompositeOperation=op;g.drawImage(src,R.x,R.y,R.w,R.h,R.x,R.y,R.w,R.h);g.restore();}
// Replace rectangle R of dst with src (taken from R, or from (sx, sy) of a cropped patch).
function put(dst,src,R,sx=R.x,sy=R.y){const g=g2(dst);g.save();plain(g);g.clearRect(R.x,R.y,R.w,R.h);g.drawImage(src,sx,sy,R.w,R.h,R.x,R.y,R.w,R.h);g.restore();}
const copyInto=(dst,src,R)=>put(dst,src,R);
// getImageData on a canvas the GPU draws makes Chrome move that canvas to software rendering for good (then every frame
// uploads the whole draft). Pixels are read and written through one small canvas kept for reading instead.
let reader=null;
function readerFor(w,h){if(!reader){reader=document.createElement('canvas');reader.getContext('2d',{willReadFrequently:true});}
  if(reader.width<w||reader.height<h){reader.width=Math.max(w,reader.width);reader.height=Math.max(h,reader.height);}return reader.getContext('2d');}
function readPixels(c,R){const g=readerFor(R.w,R.h);g.clearRect(0,0,R.w,R.h);g.drawImage(c,R.x,R.y,R.w,R.h,0,0,R.w,R.h);return g.getImageData(0,0,R.w,R.h);}
function writePixels(c,D,R){const g=readerFor(R.w,R.h);g.putImageData(D,0,0);put(c,g.canvas,R,0,0);}
Ed.util.read=(c,r)=>readPixels(c,r||{x:0,y:0,w:c.width,h:c.height});Ed.util.write=(c,D,r)=>writePixels(c,D,r||{x:0,y:0,w:D.width,h:D.height});
let pv=null,cache=null,dirty=null,selection=null,lastSelection=null,selInfo=null;
function fxList(L,stage){const out=[];for(const [type,params] of Object.entries(L.effects||{})){if(!params||params.enabled===false)continue;const d=effects.get(type);
  if(!d){doc.missing.add('effect '+type);continue;}if(d.stage===stage)out.push([d,params]);}return out.sort((a,b)=>a[0].order-b[0].order);}
function fxPad(L){let n=0;for(const [type,params] of Object.entries(L.effects||{})){const d=effects.get(type);if(d&&params&&params.enabled!==false){try{n=Math.max(n,Math.ceil(+d.pad(params)||0));}catch(e){report(e,'effect pad '+type);}}}return n;}
function fxArea(L,R){return grow(R||null,L?fxPad(L):0)||null;}
function drawFx(g,L,src,R,stage,alpha){for(const [d,params] of fxList(L,stage)){g.save();try{d.render(g,{layer:L,params,source:src,region:R,opacity:alpha,doc,temp:tmp,free,blit});}catch(e){report(e,'effect '+d.type);}finally{g.restore();}}}
function pixelsOf(L){if(pv&&pv.layer===L&&pv.target==='pixels')return pv.kind==='stroke'?pv.scratch:pv.canvas;return L.canvas;}
function maskOf(L){if(pv&&pv.layer===L&&pv.target==='mask')return pv.kind==='stroke'?pv.scratch:pv.canvas;return L.mask&&L.maskEnabled?L.mask:null;}
function content(L,R){const px=pixelsOf(L),m=maskOf(L);if(!m)return px;const t=tmp(),g=g2(t);copyInto(t,px,R);blit(g,m,R,'destination-in');return t;}
function draw(g,src,R,id,alpha){if(alpha<=0)return;let b=blends.get(id);if(!b){doc.missing.add('blend '+id);b=blends.get('normal');}
  if(b.op){blit(g,src,R,b.op,alpha);return;}
  const D=readPixels(g.canvas,R),Sd=readPixels(src,R);
  if(b.composite){for(let i=3;i<Sd.data.length;i+=4)Sd.data[i]=Math.round(Sd.data[i]*alpha);b.composite(D,Sd,{region:R,alpha,doc});}else blendPixels(D.data,Sd.data,alpha,b.fn);
  writePixels(g.canvas,D,R);}
// Opacity fades a layer and its effects as one (Photoshop): with effects the whole stack is built at full strength on a
// copy of what is below, then the destination moves toward it by alpha (premultiplied: fade it, then add the copy).
// Without effects that equals drawing with alpha, which the W3C formula is linear in.
function fade(g,R,alpha,render){if(alpha<=0)return;const T=tmp();put(T,g.canvas,R);render(g2(T));
  g.save();plain(g);g.globalAlpha=alpha;g.globalCompositeOperation='destination-out';g.fillStyle='#000';g.fillRect(R.x,R.y,R.w,R.h);g.restore();blit(g,T,R,'lighter',alpha);free(T);}
const hasFx=L=>['behind','inner','over'].some(s=>fxList(L,s).length);
function drawLayer(g,L,R,alpha=L.opacity,blend=L.blend,fx=true){const pad=fx?fxPad(L):0,RR=pad?area(grow(R,pad)):R,src=content(L,RR);
  const all=a=>t=>{if(fx)drawFx(t,L,src,R,'behind',a);draw(t,src,R,blend,a*L.fill);if(fx){drawFx(t,L,src,R,'inner',a);drawFx(t,L,src,R,'over',a);}};
  if(fx&&alpha<1&&hasFx(L))fade(g,R,alpha,all(1));else all(alpha)(g);free(src);}
function adjustLayer(g,L,R){const d=adjustments.get(L.adjustment&&L.adjustment.type);if(!d){doc.missing.add('adjustment '+(L.adjustment&&L.adjustment.type));return;}
  const k=L.opacity*L.fill;if(k<=0)return;const D=readPixels(g.canvas,R),A=new ImageData(new Uint8ClampedArray(D.data),R.w,R.h);
  try{d.apply(A,{...d.defaults,...(L.adjustment.params||{})},{region:R,layer:L,doc});}catch(e){report(e,'adjustment '+d.type);return;}
  const m=maskOf(L),M=m?readPixels(m,R).data:null;
  const b=blends.get(L.blend)||blends.get('normal'),fn=L.blend!=='normal'&&b.fn?b.fn:null,dd=D.data,ad=A.data,cb=[0,0,0],cs=[0,0,0],o=[0,0,0];
  for(let i=0;i<dd.length;i+=4){let w=k;if(M)w*=M[i+3]/255;if(w<=0||!dd[i+3])continue;
    if(fn){for(let c=0;c<3;c++){cb[c]=dd[i+c]/255;cs[c]=ad[i+c]/255;}fn(cb,cs,o);for(let c=0;c<3;c++)dd[i+c]=Math.round(255*(cb[c]+(clamp(o[c],0,1)-cb[c])*w));}
    else for(let c=0;c<3;c++)dd[i+c]=Math.round(dd[i+c]+(ad[i+c]-dd[i+c])*w);}
  writePixels(g.canvas,D,R);}
// A clipping group as Photoshop builds it: inside, the base counts as opaque (its colour at Fill wherever it has pixels)
// and the clipped layers blend with that; the base's own transparency (its masked shape) then applies once to the whole,
// so soft edges keep the base's alpha instead of growing more opaque. Inner effects sit under the clipped layers; 'over'
// effects (strokes) are drawn after the cut so they may reach outside the shape.
function group(g,base,members,R){const pad=fxPad(base),shape=content(base,pad?area(grow(R,pad)):R);
  const G=tmp(),gg=g2(G),D=readPixels(shape,R),d=D.data,a=Math.round(255*base.fill);for(let i=3;i<d.length;i+=4)if(d[i])d[i]=a;writePixels(G,D,R);
  drawFx(gg,base,shape,R,'inner',1);
  for(const m of members){if(!m.visible)continue;if(m.kind==='adjustment'){adjustLayer(gg,m,R);continue;}
    const T=tmp(),tg=g2(T);tg.clearRect(R.x,R.y,R.w,R.h);drawLayer(tg,m,R,1,'normal');draw(gg,T,R,m.blend,m.opacity);free(T);}
  blit(gg,shape,R,'destination-in');drawFx(gg,base,shape,R,'over',1);
  const all=a=>t=>{drawFx(t,base,shape,R,'behind',a);draw(t,G,R,base.blend,a);};   // the base's opacity fades its behind effects with the group
  if(base.opacity<1&&fxList(base,'behind').length)fade(g,R,base.opacity,all(1));else all(base.opacity)(g);free(G);free(shape);}
// Composite list[from, to) onto g inside R. A layer followed by clip:true layers forms a clipping group.
function stack(g,list,from,to,R){for(let i=from;i<to;){const L=list[i];let j=i+1;if(L.kind!=='adjustment')while(j<to&&list[j].clip)j++;
    if(L.visible){if(L.kind==='adjustment')adjustLayer(g,L,R);else if(j===i+1)drawLayer(g,L,R);else group(g,L,list.slice(i+1,j),R);}i=j;}}
function groupRange(k){let a=k;while(a>0&&doc.layers[a].clip&&doc.layers[a-1].kind!=='adjustment')a--;let b=a+1;if(doc.layers[a].kind!=='adjustment')while(b<doc.layers.length&&doc.layers[b].clip)b++;return[a,Math.max(b,k+1)];}
const simple=(list,from,to)=>{for(let i=from;i<to;i++){const L=list[i];if(L.kind==='adjustment'||(!L.clip&&L.visible&&(L.blend!=='normal'||fxList(L,'behind').length)))return false;}return true;};
function buildCache(){if(!doc||!pv)return null;const k=doc.layers.indexOf(pv.layer);if(k<0)return null;const[a,b]=groupRange(k),n=doc.layers.length,R=area();
  const below=a>0?canvas(doc.width,doc.height):null;if(below)stack(g2(below),doc.layers,0,a,R);
  let above=null;if(b<n&&simple(doc.layers,b,n)){above=canvas(doc.width,doc.height);stack(g2(above),doc.layers,b,n,R);}
  return{below,above,from:a,to:b,whole:b>=n};}
function recomposite(R){const g=g2(host.draft);
  if(pv&&cache){g.clearRect(R.x,R.y,R.w,R.h);if(cache.below)g.drawImage(cache.below,R.x,R.y,R.w,R.h,R.x,R.y,R.w,R.h);
    stack(g,doc.layers,cache.from,cache.to,R);if(cache.above)blit(g,cache.above,R);else if(!cache.whole)stack(g,doc.layers,cache.to,doc.layers.length,R);return;}
  g.clearRect(R.x,R.y,R.w,R.h);stack(g,doc.layers,0,doc.layers.length,R);}
Ed.update=rect=>{if(!doc)return;const R=area(rect);if(!R)return;dirty=union(dirty,R);host.draw();};
Ed.flush=()=>{if(!doc||!dirty||!host.draft)return;const R=area(dirty);dirty=null;if(!R)return;
  if(host.draft.width!==doc.width||host.draft.height!==doc.height){host.draft.width=doc.width;host.draft.height=doc.height;R.x=R.y=0;R.w=doc.width;R.h=doc.height;}
  if(pv&&pv.kind==='stroke'){const base=pv.target==='mask'?pv.layer.mask:pv.layer.canvas;copyInto(pv.scratch,base,R);strokeInto(pv.scratch,pv,R);}
  if(pv&&!cache)cache=buildCache();
  try{recomposite(R);}catch(e){report(e,'composite');}};
Ed.composite=()=>{Ed.flush();return host.draft;};
Ed.render={
  composite(o={}){const list=o.layers?o.layers.map(resolve).filter(Boolean).sort((a,b)=>doc.layers.indexOf(a)-doc.layers.indexOf(b)):doc.layers;const R=area(o.region),out=o.into||canvas(doc.width,doc.height);
    if(R){const g=g2(out);g.clearRect(R.x,R.y,R.w,R.h);stack(g,list,0,list.length,R);}return out;},
  layer(ref,o={}){const L=resolve(ref),R=area(o.region),out=o.into||canvas(doc.width,doc.height);if(!L||!R)return out;const g=g2(out);g.clearRect(R.x,R.y,R.w,R.h);
    if(L.kind==='pixel')drawLayer(g,L,R,o.opacity?L.opacity:1,'normal',o.effects!==false);return out;},
  below(index){const R=area(),out=canvas(doc.width,doc.height);stack(g2(out),doc.layers,0,clamp(index,0,doc.layers.length),R);return out;},
  content(ref){const L=resolve(ref),R=area(),out=canvas(doc.width,doc.height);if(L){const c=content(L,R);g2(out).drawImage(c,0,0);free(c);}return out;}};

// ----- previews ---------------------------------------------------------------------------------------------------------
// A stroke buffer is applied to a layer (or its mask) through the selection, with lock transparency honored.
function strokeInto(dst,p,R){const g=g2(dst);let src=p.buffer,t=null;const sel=p.useSelection?selection:null;
  if(sel||p.target==='mask'){t=tmp();const tg=g2(t);copyInto(t,src,R);if(sel)blit(tg,sel,R,'destination-in');
    if(p.target==='mask'){clip(tg,R);tg.globalCompositeOperation='source-in';tg.fillStyle='#fff';tg.fillRect(R.x,R.y,R.w,R.h);tg.restore();}src=t;}
  if(p.target==='mask'){const v=luma(p.color)/255;blit(g,src,R,'destination-out',p.opacity);if(v>0)blit(g,src,R,'lighter',p.opacity*v);}
  else if(!p.lockAlpha||p.mode==='source-over'||p.mode==='source-atop')blit(g,src,R,p.lockAlpha?'source-atop':p.mode,p.opacity);
  else if(!['destination-out','destination-in','copy','xor','source-in','source-out','destination-atop','lighter'].includes(p.mode)){
    // Lock transparency with a blend mode: blend, then put the old alpha back.
    const old=readPixels(dst,R);blit(g,src,R,p.mode,p.opacity);const now=readPixels(dst,R);
    for(let i=3;i<now.data.length;i+=4)now.data[i]=old.data[i];writePixels(dst,now,R);}
  free(t);}
function startPreview(p){Ed.preview.clear(false);if(!doc)return null;p.layer=resolve(p.layer);if(!p.layer)return null;
  p.target=p.target==='mask'&&p.layer.mask?'mask':'pixels';p.lockAlpha=p.target==='pixels'&&!!(p.layer.locks.alpha);
  if(p.kind==='stroke'){p.scratch=tmp();p.scratch._tmp=false;copyInto(p.scratch,p.target==='mask'?p.layer.mask:p.layer.canvas,area());}
  pv=p;cache=null;p.area=null;Ed.emit('preview',p);return p;}
const dropScratch=p=>{if(p.scratch){p.scratch._tmp=true;free(p.scratch);p.scratch=null;}};
Ed.preview={
  stroke(o){return startPreview({kind:'stroke',layer:o.layer,target:o.target??(doc&&doc.target),buffer:o.canvas,opacity:clamp(o.opacity??1,0,1),mode:o.mode||'source-over',color:o.color||Ed.fg,useSelection:o.selection!==false});},
  layer(o){const p=startPreview({kind:'layer',layer:o.layer,target:o.target||'pixels',canvas:o.canvas});if(p)Ed.preview.update();return p;},
  update(rect){if(!pv)return;const R=area(rect?grow(rect,fxPad(pv.layer)):null);if(!R)return;pv.area=union(pv.area,R);Ed.update(R);},
  clear(redraw=true){if(!pv)return;const p=pv;pv=null;cache=null;dropScratch(p);if(redraw)Ed.update(p.kind==='layer'?null:p.area);Ed.emit('preview',null);},
  get active(){return pv;}};
let strokeCanvas=null;
Ed.strokeBuffer=()=>{if(!doc)return null;if(!strokeCanvas||strokeCanvas.width!==doc.width||strokeCanvas.height!==doc.height||(pv&&pv.buffer===strokeCanvas))strokeCanvas=canvas(doc.width,doc.height);
  else{const g=g2(strokeCanvas);if(g.reset)g.reset();else{g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.globalCompositeOperation='source-over';g.clearRect(0,0,doc.width,doc.height);}}return strokeCanvas;};
Ed.commitStroke=(label,o={})=>{const p=pv;if(!p||p.kind!=='stroke')return false;const R=area(o.rect||p.area);
  if(!R){Ed.preview.clear();return false;}const L=p.layer,t=p.target;
  pv=null;cache=null;dropScratch(p);Ed.emit('preview',null);
  Ed.history.record(label,tx=>{tx.touch(L,t,R);strokeInto(t==='mask'?L.mask:L.canvas,p,R);},{op:o.op||{tool:label.toLowerCase()},rect:R});return true;};
Ed.strokeInto=(dst,o,rect)=>{const R=area(rect);if(R)strokeInto(dst,{buffer:o.canvas,opacity:o.opacity??1,mode:o.mode||'source-over',color:o.color||Ed.fg,target:o.target||'pixels',lockAlpha:!!o.lockAlpha,useSelection:o.selection!==false},R);};

// ----- selection --------------------------------------------------------------------------------------------------------
function analyze(mask){ // bounds of alpha > 0 and the 50% outline as merged runs
  const W=doc.width,H=doc.height,d=readPixels(mask,{x:0,y:0,w:W,h:H}).data;let x0=W,y0=H,x1=-1,y1=-1;const inside=new Uint8Array((W+2)*(H+2));
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){const a=d[(y*W+x)*4+3];if(a){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}if(a>=128)inside[(y+1)*(W+2)+x+1]=1;}
  const h=[],v=[],at=(x,y)=>inside[(y+1)*(W+2)+x+1];
  for(let y=0;y<=H;y++){let run=-1;for(let x=0;x<=W;x++){const e=x<W&&at(x,y-1)!==at(x,y);if(e&&run<0)run=x;else if(!e&&run>=0){h.push(y,run,x);run=-1;}}}
  for(let x=0;x<=W;x++){let run=-1;for(let y=0;y<=H;y++){const e=y<H&&at(x-1,y)!==at(x,y);if(e&&run<0)run=y;else if(!e&&run>=0){v.push(x,run,y);run=-1;}}}
  return{bounds:x1<0?null:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1},h:Int32Array.from(h),v:Int32Array.from(v)};}
let selVersion=0;
function useSelection(mask){selection=mask;selInfo=mask?analyze(mask):null;if(selInfo&&!selInfo.bounds){selection=null;selInfo=null;}selVersion++;Ed.emit('selection');}
function whiteCanvas(){const c=canvas(doc.width,doc.height),g=g2(c);g.fillStyle='#fff';g.fillRect(0,0,doc.width,doc.height);return c;}
function changeSelection(label,next,op,hist){if(!doc)return false;if(hist===false&&tx){selection=next;return true;}Ed.history.record(label,()=>{selection=next;},{op:op||{tool:'select',action:label.toLowerCase()}});return true;}
Ed.select={
  set(mask,mode='replace',o={}){if(!doc)return false;const m=canvas(doc.width,doc.height),g=g2(m);
    if(mode!=='replace'&&selection){g.drawImage(selection,0,0);g.globalCompositeOperation=mode==='add'?'source-over':mode==='subtract'?'destination-out':'destination-in';}
    else if(mode==='subtract'||mode==='intersect')return changeSelection(o.label||'Deselect',null);
    g.drawImage(mask,0,0);g.globalCompositeOperation='source-in';g.fillStyle='#fff';g.fillRect(0,0,doc.width,doc.height);
    return changeSelection(o.label||{replace:'Selection',add:'Add to Selection',subtract:'Subtract from Selection',intersect:'Intersect Selection'}[mode]||'Selection',m,undefined,o.history);},
  rect(x,y,w,h,mode='replace',o={}){if(!doc)return false;const m=canvas(doc.width,doc.height),g=g2(m);g.fillStyle='#fff';g.fillRect(x,y,w,h);return Ed.select.set(m,mode,{label:o.label||'Rectangular Marquee'});},
  all(){return changeSelection('Select All',whiteCanvas());},
  none(){if(!selection)return false;return changeSelection('Deselect',null);},
  invert(){if(!selection)return false;const m=whiteCanvas(),g=g2(m);g.globalCompositeOperation='destination-out';g.drawImage(selection,0,0);return changeSelection('Inverse',m);},
  reselect(){if(!lastSelection||selection)return false;return changeSelection('Reselect',lastSelection);},
  bounds:()=>selInfo?{...selInfo.bounds}:null,hasSelection:()=>!!selection,getMask:()=>selection,
  outline:()=>selInfo?{h:selInfo.h,v:selInfo.v}:null,get version(){return selVersion;}};
Object.defineProperty(Ed,'selection',{get:()=>selection,enumerable:true});

// ----- history ----------------------------------------------------------------------------------------------------------
let states=[],hi=0,tx=null,bytes=0;
const crop=(src,R)=>{const c=canvas(R.w,R.h);g2(c).drawImage(src,R.x,R.y,R.w,R.h,0,0,R.w,R.h);return c;};
const refOf=(L,t)=>t==='mask'?L.mask:L.canvas;
function diffRect(a,b){const W=a.width,H=a.height,F={x:0,y:0,w:W,h:H},A=new Uint32Array(readPixels(a,F).data.buffer),B=new Uint32Array(readPixels(b,F).data.buffer);
  let x0=W,y0=H,x1=-1,y1=-1;for(let y=0;y<H;y++){const r=y*W;for(let x=0;x<W;x++)if(A[r+x]!==B[r+x]){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}}
  return x1<0?null:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};}
const stateBytes=s=>s.steps.reduce((n,st)=>n+(st.kind==='pixels'?st.before.reduce((m,b)=>m+b.rect.w*b.rect.h*4,0)+st.after.rect.w*st.after.rect.h*4:st.kind==='selection'?((st.before?1:0)+(st.after?1:0))*doc.width*doc.height*4:st.kind==='structure'?st.extra:0),0);
function makeTx(label,o={}){
  const t={label,op:o.op,snap:structureSnap(),sel:selection,touched:[],full:[],
    touch(L,target='pixels',rect){L=resolve(L);const ref=L&&refOf(L,target);const R=area(rect);if(!ref||!R)return t;t.touched.push({L,target,ref,rect:R,before:crop(ref,R)});return t;},
    commit(c){if(tx===t)commitTx(c);},cancel(){if(tx===t)cancelTx();}};
  return t;}
function closeTx(){if(tx)commitTx();}
function commitTx(c={}){const t=tx;tx=null;if(!t||!doc)return null;
  for(const f of t.full){if(refOf(f.L,f.target)!==f.ref)continue;const R=c.rect?area(c.rect):diffRect(f.copy,f.ref);if(R)t.touched.push({L:f.L,target:f.target,ref:f.ref,rect:R,before:crop(f.copy,R)});}
  const steps=[],after=structureSnap();let changedRect=null;
  if(!sameSnap(t.snap,after)){const was=new Set(t.snap.layers.map(x=>x.L)),now=new Set(after.layers.map(x=>x.L));let extra=0;
    for(const x of t.snap.layers)if(!now.has(x.L))extra+=doc.width*doc.height*4*(x.mask?2:1);for(const x of after.layers)if(!was.has(x.L))extra+=doc.width*doc.height*4*(x.mask?2:1);
    steps.push({kind:'structure',before:t.snap,after,extra});changedRect=area();}
  const groups=new Map();for(const p of t.touched){if(refOf(p.L,p.target)!==p.ref)continue;const k=p.L.id+'|'+p.target;if(!groups.has(k))groups.set(k,{L:p.L,target:p.target,ref:p.ref,before:[],rect:null});const gr=groups.get(k);gr.before.push({rect:p.rect,canvas:p.before});gr.rect=union(gr.rect,p.rect);}
  for(const gr of groups.values()){steps.push({kind:'pixels',L:gr.L,target:gr.target,before:gr.before,after:{rect:gr.rect,canvas:crop(gr.ref,gr.rect)}});gr.L.version++;changedRect=union(changedRect,fxArea(gr.L,gr.rect));}
  if(t.sel!==selection){steps.push({kind:'selection',before:t.sel,after:selection});if(t.sel)lastSelection=t.sel;}
  if(!steps.length){Ed.update(changedRect);return null;}
  const s={label:t.label,op:t.op||{tool:String(t.label).toLowerCase()},steps};s.bytes=stateBytes(s);
  for(const x of states.splice(hi+1))bytes-=x.bytes;states.push(s);hi=states.length-1;bytes+=s.bytes;trim();
  finish(s,changedRect,'commit',s.op);return s;}
function cancelTx(){const t=tx;tx=null;if(!t||!doc)return;
  for(const f of t.full)if(refOf(f.L,f.target)===f.ref)copyInto(f.ref,f.copy,area());
  for(const p of t.touched.slice().reverse())if(refOf(p.L,p.target)===p.ref)put(p.ref,p.before,p.rect,0,0);
  restoreSnap(t.snap);if(t.sel!==selection)useSelection(t.sel);cache=null;Ed.update();Ed.emit('layers');Ed.emit('active');}
function trim(){const L=history.limits;while(states.length>2&&(states.length-1>L.states||bytes>L.bytes)){const s=states.shift();bytes-=s.bytes;hi--;states[0].steps=[];bytes-=states[0].bytes||0;states[0].bytes=0;}}
function apply(s,undo){const steps=undo?s.steps.slice().reverse():s.steps;let R=null,structure=false,sel=false;
  for(const st of steps){
    if(st.kind==='structure'){restoreSnap(undo?st.before:st.after);structure=true;R=area();}
    else if(st.kind==='pixels'){const ref=refOf(st.L,st.target);if(!ref)continue;
      if(undo)for(const b of st.before.slice().reverse())put(ref,b.canvas,b.rect,0,0);
      else put(ref,st.after.canvas,st.after.rect,0,0);
      st.L.version++;R=union(R,fxArea(st.L,st.after.rect));}
    else if(st.kind==='selection'){const next=undo?st.before:st.after;if(selection&&!next)lastSelection=selection;selection=next;sel=true;}}
  if(sel||structure)useSelectionIfChanged();return{R,structure};}
function useSelectionIfChanged(){if(selInfo&&selInfo.mask===selection)return;useSelection(selection);if(selInfo)selInfo.mask=selection;}
function finish(s,R,reason,op){if(reason!=='commit')Ed.preview.clear(false);cache=null;if(s.steps.some(st=>st.kind==='selection'))useSelectionIfChanged();
  Ed.update(R||area());Ed.emit('history');if(s.steps.some(st=>st.kind==='structure')){Ed.emit('layers');Ed.emit('active');}else Ed.emit('layers');changedDoc(reason,op);}
const history=Ed.history={
  limits:{states:50,bytes:512*1024*1024},
  get index(){return hi;},
  reset(){states=[{label:'Open',op:null,steps:[],bytes:0}];hi=0;bytes=0;tx=null;},
  list:()=>states.map((s,i)=>({index:i,label:s.label,current:i===hi,undone:i>hi})),
  canUndo:()=>hi>0,canRedo:()=>hi<states.length-1,
  label:i=>states[i]?.label,
  begin(label,refs=[],o={}){closeTx();if(!doc)return makeTx(label,o);tx=makeTx(label,o);
    for(const ref of [].concat(refs||[])){const L=resolve(ref);if(!L)continue;for(const target of o.mask==='only'?['mask']:o.mask?['pixels','mask']:['pixels']){const r=refOf(L,target);if(!r)continue;
      if(o.rect)tx.touch(L,target,o.rect);else tx.full.push({L,target,ref:r,copy:crop(r,area())});}}
    return tx;},
  commit(c){return commitTx(c);},cancel(){cancelTx();},
  get open(){return tx;},
  record(label,fn,o={}){const own=history.begin(label,o.layers||[],o);let r;
    try{r=fn(own);}catch(e){if(tx===own)cancelTx();throw e;}
    if(r&&typeof r.then==='function')return r.then(v=>{if(tx===own)commitTx(o);return v;},e=>{if(tx===own)cancelTx();throw e;});
    if(tx===own)commitTx(o);return r;},
  undo(){closeTx();if(hi<=0)return false;Ed.preview.clear(false);const s=states[hi--];const r=apply(s,true);finish(s,r.R,'undo',{tool:'undo'});return true;},
  redo(){closeTx();if(hi>=states.length-1)return false;Ed.preview.clear(false);const s=states[++hi];const r=apply(s,false);finish(s,r.R,'redo',{tool:'redo'});return true;},
  jump(i){closeTx();i=clamp(i|0,0,states.length-1);if(i===hi)return false;Ed.preview.clear(false);let R=null;const touched={steps:[]};
    while(hi>i){const s=states[hi--];R=union(R,apply(s,true).R);touched.steps.push(...s.steps);}while(hi<i){const s=states[++hi];R=union(R,apply(s,false).R);touched.steps.push(...s.steps);}
    finish(touched,R,'history',{tool:'history',state:states[hi].label});return true;},
  truncate(){closeTx();const gone=states.splice(hi+1);for(const x of gone)bytes-=x.bytes;if(gone.length)Ed.emit('history');return gone.length;},
  get bytes(){return bytes;}};
history.reset();

// ----- pixel operations through the selection --------------------------------------------------------------------------
Ed.editable=(o={})=>{if(!doc)return 'Open an image first.';if(o.view!==false){const v=host.canPaint();if(v)return v;}const L=active();if(!L)return 'There is no layer to edit.';
  if(!L.visible)return `“${L.name}” is hidden. Show it to edit it.`;
  if(L.locks.all||(doc.target==='pixels'&&L.locks.pixels))return `“${L.name}” is locked. Unlock it in the Layers panel to edit it.`;
  if(doc.target==='mask'&&!L.mask)return 'This layer has no mask to edit.';
  if(doc.target==='pixels'&&L.kind==='adjustment')return `“${L.name}” is an adjustment layer and has no pixels. Paint on its mask to choose where it applies.`;return '';};
function mergeInto(dst,src,R,sel,lockAlpha){const g=g2(dst);const old=lockAlpha?readPixels(dst,R):null;
  if(!sel)copyInto(dst,src,R);else{const t=tmp(),tg=g2(t);copyInto(t,src,R);blit(tg,sel,R,'destination-in');blit(g,sel,R,'destination-out');blit(g,t,R,'lighter');free(t);}
  if(old){const now=readPixels(dst,R);for(let i=3;i<now.data.length;i+=4)now.data[i]=old.data[i];writePixels(dst,now,R);}}
Ed.applyToTarget=async(label,drawFn,o={})=>{const why=Ed.editable({view:false});if(why){Ed.message(why,true);return false;}
  const L=active(),target=doc.target,ref=refOf(L,target),sel=selection,R=area(o.rect)||(sel&&selInfo?area(selInfo.bounds):area());if(!R)return false;
  const work=canvas(doc.width,doc.height);g2(work).drawImage(ref,0,0);
  try{await drawFn(g2(work),{layer:L,target,canvas:work,selection:sel,rect:R,doc});}catch(e){report(e,'apply '+label);Ed.message(label+' failed: '+e.message,true);return false;}
  if(doc.layers[doc.activeIndex]!==L||refOf(L,target)!==ref)return false;
  history.record(label,t=>{t.touch(L,target,R);mergeInto(ref,work,R,sel,target==='pixels'&&L.locks.alpha);},{op:o.op||{tool:String(label).toLowerCase()},rect:R});return true;};
Ed.sample=(x,y,o={})=>{if(!doc)return null;Ed.flush();const n=o.size||1,h=(n-1)>>1,R=area({x:Math.floor(x)-h,y:Math.floor(y)-h,w:n,h:n});if(!R)return null;
  const src=o.from==='layer'?(doc.target==='mask'?active().mask:active().canvas):host.draft;if(!src)return null;const d=readPixels(src,R).data;
  let r=0,gg=0,b=0,a=0;for(let i=0;i<d.length;i+=4){const al=d[i+3];r+=d[i]*al;gg+=d[i+1]*al;b+=d[i+2]*al;a+=al;}
  if(o.from==='layer'&&doc.target==='mask'){const v=a/(d.length/4);return toHex([v,v,v]);}return a?toHex([r/a,gg/a,b/a]):null;};

// ----- colors -----------------------------------------------------------------------------------------------------------
let colors=store.get('ed-colors',{fg:'#66ddd2',bg:'#ffffff'});if(!/^#[0-9a-f]{6}$/i.test(colors.fg||'')||!/^#[0-9a-f]{6}$/i.test(colors.bg||''))colors={fg:'#66ddd2',bg:'#ffffff'};
Object.defineProperty(Ed,'fg',{get:()=>colors.fg,enumerable:true});Object.defineProperty(Ed,'bg',{get:()=>colors.bg,enumerable:true});
Ed.setColor=(which,h)=>{if(!/^#?[0-9a-f]{6}$/i.test(String(h||'')))return false;h=('#'+String(h).replace('#','')).toLowerCase();const k=which==='bg'?'bg':'fg';if(colors[k]===h)return true;colors={...colors,[k]:h};store.set('ed-colors',colors);Ed.emit('color',{which:k,color:h});return true;};
Ed.swapColors=()=>{colors={fg:colors.bg,bg:colors.fg};store.set('ed-colors',colors);Ed.emit('color',{which:'both'});};
Ed.defaultColors=()=>{colors=doc&&doc.target==='mask'?{fg:'#ffffff',bg:'#000000'}:{fg:'#000000',bg:'#ffffff'};store.set('ed-colors',colors);Ed.emit('color',{which:'both'});};
let picker=null;Ed.registerColorPicker=fn=>{picker=fn;};Ed.pickColor=async(which,anchor)=>{const k=which==='bg'?'bg':'fg';
  if(picker){try{const c=await picker({which:k,color:colors[k],anchor});if(c)Ed.setColor(k,c);}catch(e){report(e,'color picker');}return;}Ed.emit('pick-native',{which:k,anchor});};

// ----- tools ------------------------------------------------------------------------------------------------------------
const tools=new Map(),optionValues={},TOOL_GROUPS=[['move','Move'],['marquee','Marquee'],['lasso','Lasso'],['wand','Magic Wand'],['eyedropper','Eyedropper'],['healing','Healing'],['brush','Brush'],
  ['clone','Clone Stamp'],['history','History Brush'],['eraser','Eraser'],['fill','Gradient & Paint Bucket'],['blur','Blur, Sharpen & Smudge'],['dodge','Dodge, Burn & Sponge'],['type','Type'],['shape','Shapes'],['hand','Hand'],['zoom','Zoom']];
let toolId=null,toolSeq=0;const slotPick=store.get('ed-slots',{});
function optionsOf(id){const t=tools.get(id);if(!t)return{};if(!optionValues[id]){const saved=store.get('ed-tool:'+id,{});optionValues[id]={};
    for(const o of t.options||[])if(o.key&&o.type!=='button')optionValues[id][o.key]=saved[o.key]!==undefined&&valid(o,saved[o.key])?saved[o.key]:o.default;}return optionValues[id];}
function valid(o,v){if(o.type==='range'||o.type==='number')return typeof v==='number'&&isFinite(v);if(o.type==='checkbox')return typeof v==='boolean';if(o.type==='select'||o.type==='segmented')return (o.options||[]).some(x=>x!=='-'&&(x.value??x)===v);
  return o.type==='text'||o.type==='color'||!o.type?typeof v==='string':v!==undefined;}   // custom field types keep their own values
Ed.registerTool=(id,def)=>{const was=tools.get(id),activeNow=toolId===id;if(activeNow&&was)callTool(was,'onDeactivate');
  tools.set(id,{...def,id,label:def.label||id,group:def.group||id,key:(def.key||'').toLowerCase(),cursor:def.cursor||'default',options:def.options||[],seq:was?.seq??++toolSeq});delete optionValues[id];
  if(activeNow){callTool(tools.get(id),'onActivate');Ed.emit('tool',id);}Ed.emit('tools');return tools.get(id);};
Ed.tools={get:id=>tools.get(id),list:()=>[...tools.values()],groups:TOOL_GROUPS};
Object.defineProperty(Ed,'tool',{get:()=>toolId,enumerable:true});
Ed.setTool=id=>{const t=tools.get(id);if(!t)return false;if(toolId===id)return true;const old=tools.get(toolId);if(old)callTool(old,'onDeactivate');Ed.preview.clear();
  toolId=id;slotPick[t.group]=id;store.set('ed-slots',slotPick);store.set('ed-tool',id);callTool(t,'onActivate');Ed.emit('tool',id);return true;};
Ed.toolOptions=id=>optionsOf(id??toolId);
Ed.setToolOptions=(id,values)=>{const t=tools.get(id);if(!t)return false;const cur=optionsOf(id);for(const o of t.options||[]){if(!(o.key in values))continue;let v=values[o.key];
    if(o.type==='range'||o.type==='number'){v=Number(v);if(!isFinite(v))continue;v=clamp(v,o.min??-Infinity,o.max??Infinity);}if(o.type==='checkbox')v=!!v;
    if(o.type==='select'||o.type==='segmented'){const x=(o.options||[]).find(x=>x!=='-'&&String(x.value??x)===String(v));if(!x)continue;v=x.value??x;}   // only listed values
    if(o.type==='button'||!valid(o,v))continue;cur[o.key]=v;}
  store.set('ed-tool:'+id,cur);callTool(t,'onOptions',cur);Ed.emit('options',{id,values:cur});return true;};
Ed.toolSlots=()=>{const by=new Map();for(const t of [...tools.values()].sort((a,b)=>(a.order??a.seq)-(b.order??b.seq))){if(!by.has(t.group))by.set(t.group,[]);by.get(t.group).push(t);}
  const order=TOOL_GROUPS.map(g=>g[0]),names=Object.fromEntries(TOOL_GROUPS);const keys=[...by.keys()].sort((a,b)=>{const i=order.indexOf(a),j=order.indexOf(b);return (i<0?order.length-2.5:i)-(j<0?order.length-2.5:j);});
  return keys.map(g=>{const list=by.get(g),cur=list.find(t=>t.id===toolId)||list.find(t=>t.id===slotPick[g])||list[0];return{group:g,label:names[g]||cur.label,tools:list,current:cur.id};});};
function callTool(t,name,...args){if(!t||typeof t[name]!=='function')return undefined;try{return t[name](...args);}catch(e){report(e,`tool ${t.id}.${name}`);Ed.message(`${t.label}: ${e.message}`,true);return undefined;}}
Ed.callTool=(name,...args)=>callTool(tools.get(toolId),name,...args);
Ed.icon=(paths,o={})=>{const ns='http://www.w3.org/2000/svg',s=document.createElementNS(ns,'svg');s.setAttribute('viewBox',o.viewBox||'0 0 24 24');s.setAttribute('width',o.size||18);s.setAttribute('height',o.size||18);
  s.setAttribute('fill','none');s.setAttribute('stroke','currentColor');s.setAttribute('stroke-width',o.width||1.7);s.setAttribute('stroke-linecap','round');s.setAttribute('stroke-linejoin','round');s.setAttribute('aria-hidden','true');
  for(const d of [].concat(paths)){const p=document.createElementNS(ns,'path');p.setAttribute('d',d);if(o.fill)p.setAttribute('fill',o.fill);s.append(p);}return s;};

// ----- menus and shortcuts ----------------------------------------------------------------------------------------------
const menus=new Map();let menuSeq=0;const TOP=['File','Edit','Image','Layer','Select','Filter','View'];
Ed.registerMenu=(path,def={})=>{menus.set(path,{path,def,seq:menus.get(path)?.seq??++menuSeq});Ed.emit('menus');};
const parseKey=s=>{const parts=String(s).split('+');let key=parts.pop();if(key===''&&parts.length&&parts[parts.length-1]===''){parts.pop();key='+';}
  const m={ctrl:false,shift:false,alt:false};for(const p of parts){const q=p.trim().toLowerCase();if(q==='ctrl'||q==='cmd'||q==='meta'||q==='control')m.ctrl=true;else if(q==='shift')m.shift=true;else if(q==='alt'||q==='option')m.alt=true;}
  return{...m,key:key.length===1?key.toLowerCase():key};};
Ed.keys={parse:parseKey,matches(e,spec){for(const s of [].concat(spec||[])){const p=parseKey(s);if(!!(e.ctrlKey||e.metaKey)!==p.ctrl||!!e.altKey!==p.alt)continue;
    const k=e.key&&e.key.length===1?e.key.toLowerCase():e.key,sym=p.key.length===1&&!/[a-z0-9]/.test(p.key);if(!sym&&!!e.shiftKey!==p.shift)continue;
    if(k===p.key||(/^[0-9]$/.test(p.key)&&e.code==='Digit'+p.key)||(/^[a-z]$/.test(p.key)&&e.code==='Key'+p.key.toUpperCase()))return true;}return false;},
  label:spec=>[].concat(spec||[])[0]||''};
function menuItem(m){const d=m.def;let enabled=true,checked=false;try{enabled=d.enabled?!!d.enabled():true;checked=d.checked?!!d.checked():false;}catch(e){report(e,'menu '+m.path);enabled=false;}
  let label=m.path.split('/').pop();try{if(d.label)label=typeof d.label==='function'?d.label():d.label;}catch(e){}
  return{path:m.path,label,shortcut:Ed.keys.label(d.shortcut),enabled,checked,checkable:!!d.checked,separatorBefore:!!d.separatorBefore,order:d.order??100};}
Ed.menu={
  run(path,params){const m=menus.get(path);if(!m)throw Error('There is no menu command '+path);const it=menuItem(m);if(!it.enabled)return false;
    try{const r=m.def.action&&m.def.action(params);if(r&&typeof r.catch==='function')r.catch(e=>{report(e,'menu '+path);Ed.message(e.message,true);});return r===undefined?true:r;}
    catch(e){report(e,'menu '+path);Ed.message(e.message,true);return false;}},
  list:()=>[...menus.values()].map(m=>{const it=menuItem(m);return{path:it.path,shortcut:it.shortcut,enabled:it.enabled,checked:it.checked};}),
  get:path=>menus.get(path),
  tree(){const root={children:new Map()};for(const m of [...menus.values()].sort((a,b)=>a.seq-b.seq)){const parts=m.path.split('/');let node=root;
      parts.forEach((p,i)=>{if(i===parts.length-1){node.children.set(p+'\u0000'+m.path,{name:p,item:m,order:m.def.order??100,seq:m.seq});return;}
        if(!node.children.has(p))node.children.set(p,{name:p,children:new Map(),order:Infinity,seq:m.seq});node=node.children.get(p);node.order=Math.min(node.order,m.def.order??100);});}
    const sort=n=>[...n.children.values()].sort((a,b)=>a.order-b.order||a.seq-b.seq).map(c=>c.children?{name:c.name,children:sort(c),separatorBefore:false}:{name:c.name,...menuItem(c.item)});
    const top=sort(root);return[...TOP.map(n=>top.find(t=>t.name===n)).filter(Boolean),...top.filter(t=>!TOP.includes(t.name))];},   // a menu with nothing in it is not shown
  forEvent(e){for(const m of menus.values())if(m.def.shortcut&&Ed.keys.matches(e,m.def.shortcut))return m.path;return null;}};

// ----- panels (rendered by the shell) -----------------------------------------------------------------------------------
const panels=new Map();let panelSeq=0;
Ed.registerPanel=(id,def)=>{panels.set(id,{id,title:def.title||id,order:def.order??(100+panelSeq),seq:panels.get(id)?.seq??++panelSeq,...def});Ed.emit('panels');};
Ed.panels={list:()=>[...panels.values()].sort((a,b)=>a.order-b.order||a.seq-b.seq),get:id=>panels.get(id),show(id){Ed.emit('panel-show',id);},refresh(id){Ed.emit('panel-refresh',id||null);}};

// ----- view (geometry comes from app.js) --------------------------------------------------------------------------------
const ZOOMS=[.01,.015,.02,.03,.04,.05,.0625,.0833,.125,.1667,.25,.3333,.5,.6667,1,2,3,4,5,6,7,8,12,16,24,32,48,64];
Ed.view={
  toClient(x,y){const t=host.transform();return{x:t.left+t.x+x*t.scale,y:t.top+t.y+y*t.scale};},
  toImage(cx,cy){const t=host.transform();return{x:(cx-t.left-t.x)/t.scale,y:(cy-t.top-t.y)/t.scale};},
  get scale(){return host.transform().scale;},get transform(){return host.transform();},
  setZoom(z,cx,cy){const t=host.transform(),v=host.getView();z=clamp(z,.01,64);if(cx==null){cx=t.left+t.width/2;cy=t.top+t.height/2;}
    // Keep the image point under (cx, cy) where it is: zoom first, then shift by what moved.
    const ax=cx-t.left,ay=cy-t.top,ix=(ax-t.x)/t.scale,iy=(ay-t.y)/t.scale;host.setView({zoom:z,px:v.px,py:v.py});
    const t2=host.transform();host.setView({zoom:z,px:v.px+(ax-ix*z)-t2.x,py:v.py+(ay-iy*z)-t2.y});Ed.emit('view');},
  zoomIn(cx,cy){const z=Ed.view.scale;Ed.view.setZoom(ZOOMS.find(v=>v>z*1.001)||64,cx,cy);},
  zoomOut(cx,cy){const z=Ed.view.scale;Ed.view.setZoom([...ZOOMS].reverse().find(v=>v<z/1.001)||.01,cx,cy);},
  fit(){host.fit();Ed.emit('view');},actual(cx,cy){Ed.view.setZoom(1,cx,cy);},
  panBy(dx,dy){const v=host.getView();host.setView({zoom:v.zoom,px:v.px+dx,py:v.py+dy});Ed.emit('view');},
  redraw(){host.draw();},redrawOverlay(){Ed.emit('overlay');},
  get pixelGrid(){return !!store.get('ed-grid',true);},set pixelGrid(v){store.set('ed-grid',!!v);Ed.emit('overlay');},
  getState:()=>host.getView(),setState(v){host.setView({...host.getView(),...v});Ed.emit('view');},
  get blocked(){return doc?host.canPaint():'Open an image first.';},
  zooms:ZOOMS};

// ----- persistence -----------------------------------------------------------------------------------------------------
const KEEP=['id','name','kind','visible','opacity','fill','blend','locks','maskEnabled','clip','effects','adjustment','data'];
async function pngOf(c){const blob=await new Promise((ok,fail)=>c.toBlob(b=>b?ok(b):fail(Error('The image could not be encoded.')),'image/png'));return b64(new Uint8Array(await blob.arrayBuffer()));}
async function bitmap(data){const blob=new Blob([unb64(data)],{type:'image/png'});return await createImageBitmap(blob);}
Ed.document={
  trivial(){if(!doc)return true;if(doc.layers.length!==1)return false;const L=doc.layers[0];
    return L.kind==='pixel'&&L.visible&&L.opacity===1&&L.fill===1&&L.blend==='normal'&&!L.mask&&!L.clip&&!Object.keys(L.effects||{}).length&&!Object.keys(L.data||{}).length&&!Object.values(L.locks).some(Boolean);},
  async serialize(){if(!doc)return null;const layers=[];
    for(const L of doc.layers){const o={};for(const k of KEEP)o[k]=clone(L[k]);o.png=await pngOf(L.canvas);if(L.mask)o.mask_png=await pngOf(L.mask);layers.push(o);}
    return{version:1,width:doc.width,height:doc.height,activeIndex:doc.activeIndex,target:doc.target,name:doc.name,layers};},
  async restore(json,flat){
    if(!json||json.version!==1||json.width!==doc.width||json.height!==doc.height||!Array.isArray(json.layers)||!json.layers.length)return{ok:false,reason:'The saved layers do not fit this image.'};
    const W=doc.width,H=doc.height,saved=doc,v=doc.version;let built;
    try{built=[];for(const o of json.layers){const c=canvas(W,H);g2(c).drawImage(await bitmap(o.png),0,0);let m=null;if(o.mask_png){m=canvas(W,H);g2(m).drawImage(await bitmap(o.mask_png),0,0);}
        built.push({o,c,m});}}catch(e){return{ok:false,reason:'The saved layers could not be read.'};}
    if(doc!==saved)return{ok:false,reason:'Another image was opened.'};
    if(doc.version!==v)return{ok:false,changed:true,reason:'It was changed before its layers finished opening.'};   // never throw away the player's work
    let n=0;const ids=new Set();const layers=built.map(({o,c,m})=>{let id=typeof o.id==='string'&&/^L\d+$/.test(o.id)&&!ids.has(o.id)?o.id:null;
      const L={id:'',name:String(o.name||'Layer').slice(0,255),kind:o.kind==='adjustment'?'adjustment':'pixel',canvas:c,visible:o.visible!==false,opacity:clamp(+o.opacity||0,0,1),fill:clamp(o.fill==null?1:+o.fill,0,1),
        blend:typeof o.blend==='string'?o.blend:'normal',locks:{alpha:false,pixels:false,position:false,all:false,...(o.locks||{})},mask:m,maskEnabled:o.maskEnabled!==false,clip:!!o.clip,
        effects:o.effects&&typeof o.effects==='object'?o.effects:{},adjustment:o.adjustment||null,data:o.data&&typeof o.data==='object'?o.data:{},version:0};
      if(id){ids.add(id);L.id=id;n=Math.max(n,+id.slice(1));}return L;});
    for(const L of layers)if(!L.id){L.id='L'+(++n);ids.add(L.id);}
    const prev={layers:doc.layers,activeIndex:doc.activeIndex,target:doc.target,nextId:doc.nextId,counter:doc.counter};
    doc.layers=layers;doc.activeIndex=clamp(json.activeIndex|0,0,layers.length-1);doc.target=json.target==='mask'&&layers[doc.activeIndex].mask?'mask':'pixels';doc.nextId=n+1;doc.counter=layers.length;doc.missing=new Set();
    if(layers[0].clip)layers[0].clip=false;
    // The restored layers must give the saved picture; otherwise (a module is missing, a file is damaged) keep the flat image.
    const out=canvas(W,H);stack(g2(out),layers,0,layers.length,area());let ok=!doc.missing.size,worst=0;
    if(ok&&flat){const ref=canvas(W,H);g2(ref).drawImage(flat,0,0);const A=g2(out).getImageData(0,0,W,H).data,B=g2(ref).getImageData(0,0,W,H).data;
      for(let i=0;i<A.length;i+=4){const aa=A[i+3],bb=B[i+3];let d=Math.abs(aa-bb);for(let k=0;k<3;k++)d=Math.max(d,Math.abs(A[i+k]*aa-B[i+k]*bb)/255);if(d>worst)worst=d;}ok=worst<=4;}
    if(!ok){const missing=[...doc.missing];Object.assign(doc,prev);doc.missing=new Set();return{ok:false,reason:missing.length?`It uses ${missing.join(', ')}, which this version of the editor does not have.`:'The layers no longer add up to the saved picture.',worst};}
    cache=null;pv=null;history.reset();Ed.update();Ed.emit('layers');Ed.emit('active');Ed.emit('history');Ed.emit('document',{reason:'restore'});
    document.dispatchEvent(new CustomEvent('lab:document',{detail:{reason:'restore'}}));return{ok:true,worst};}};

// ----- test hooks -------------------------------------------------------------------------------------------------------
let readyDone;Ed.ready=new Promise(r=>{readyDone=r;});Ed._ready=()=>readyDone(Ed);
let loading=Promise.resolve();Ed.loading=p=>{loading=Promise.resolve(p).catch(()=>{});return p;};
const rgbaOf=(c,fill)=>{if(!c){const d=new Uint8ClampedArray(doc.width*doc.height*4);if(fill)d.fill(fill);return d;}return readPixels(c,{x:0,y:0,w:c.width,h:c.height}).data;};
Ed.test={errors:[],
  async pixels(target='composite'){if(!doc)throw Error('No document is open');let data;
    if(target==='composite'||target==null){Ed.flush();data=rgbaOf(host.draft);}
    else if(target==='selection')data=rgbaOf(selection,0);
    else if(target==='mask'){const L=active();data=L&&L.mask?rgbaOf(L.mask):rgbaOf(null,255);}
    else{const L=resolve(typeof target==='string'&&/^\d+$/.test(target)?+target:target);if(!L)throw Error('No layer '+target);data=rgbaOf(L.canvas);}
    return{width:doc.width,height:doc.height,data:b64(new Uint8Array(data.buffer,data.byteOffset,data.byteLength))};},
  load(w,h,data){const bytes=unb64(data),c=canvas(w,h);g2(c).putImageData(new ImageData(new Uint8ClampedArray(bytes.buffer,bytes.byteOffset,bytes.byteLength),w,h),0,0);
    Ed.open({width:w,height:h,name:'Test image',canvas:c,layerName:'Layer 0'});return new Promise(r=>requestAnimationFrame(()=>r(true)));},
  async ready(){await loading;await new Promise(r=>requestAnimationFrame(()=>r()));return true;},
  get state(){return{tool:toolId,layers:doc?doc.layers.map(L=>({id:L.id,name:L.name,kind:L.kind,visible:L.visible,opacity:L.opacity,fill:L.fill,blend:L.blend,clip:L.clip,mask:!!L.mask,locks:{...L.locks}})):[],activeIndex:doc?doc.activeIndex:-1,target:doc?doc.target:null,history:history.list().length,index:hi,selection:!!selection};}};
})();
