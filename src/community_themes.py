"""Curated, credited theme bundles. Applying creates a separate, recoverable build."""
import io
import json
import zipfile
import pictures


def listing():
    path = pictures.PRESETS / 'community-themes.json'
    return json.loads(path.read_text(encoding='utf-8'))['themes'] if path.is_file() else []


def find(tid):
    for theme in listing():
        if theme['id'] == tid:
            return theme
    raise ValueError('Unknown community theme.')


def supported(pics, part):
    key = part['target']
    if key == 'set:logo':
        return pics.logo_rows()
    if key in pictures.BACKGROUND_SETS:
        return pics.background_rows(key)
    if key in pictures.button_sets.SETS:
        return pics.button_rows(key)
    return pics.set_rows(key)


def apply(tid, pics, builds, foods=None):
    theme = find(tid)
    food_parts = [p for p in theme['parts'] if p['target'].startswith('set:food:') and p.get('status') == 'approved']
    choices = {p['preset'] for p in food_parts}
    foods = {} if foods is None else foods
    if not isinstance(foods, dict) or any(k not in {p['target'] for p in food_parts} or v not in choices for k, v in foods.items()):
        raise ValueError('Choose food artwork from this theme for each food slot.')
    with pics.lab.lock:
        ready, skipped = [], []
        known = {p['id'] for p in pictures.presets()}
        for part in theme['parts']:
            part = dict(part, preset=foods.get(part['target'], part['preset']))
            if part.get('status') != 'approved':
                continue
            if part['preset'] not in known:
                raise ValueError('A theme asset is missing. Reinstall the app before using this theme.')
            if supported(pics, part):
                # Decode before changing builds, so corrupt assets cannot leave a half-created theme.
                pics.render(part['preset'])
                ready.append(part)
            else:
                skipped.append(part['name'])
        if not ready:
            raise ValueError('This installation has none of the artwork used by this theme.')
        previous = builds.index['active']
        created = None
        try:
            builds.create(f"{theme['name']} - {theme['credit']}")
            created = builds.index['active']
            results = [pics.apply(p['preset'], p['target']) for p in ready]
        except Exception:
            # A failed theme must not replace the user's current work or leave partial edits active.
            if builds.index['active'] != previous:
                builds.switch(previous)
            if created:
                builds.delete(created)
            raise
        return dict(build=created, name=theme['name'], credit=theme['credit'],
                    count=sum(r.get('count', 1) for r in results), skipped=skipped,
                    pending=[p['name'] for p in theme['parts'] if p.get('status') != 'approved'])


def export(tid, pics):
    theme = find(tid)
    approved = [p for p in theme['parts'] if p.get('status') == 'approved']
    bundle = pics.export([p['preset'] for p in approved])
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(bundle)) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr('CREDIT.txt', f"{theme['name']}\nCredit: {theme['credit']}\n\n{theme['description']}\n\n{theme['note']}\n")
        target.writestr('theme.json', json.dumps(dict(theme, parts=approved), indent=2))
    return out.getvalue()
