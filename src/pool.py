"""Marks the user puts on assets, remembered in small JSON files: removed from the pool, and favorites.

A mark is stored by kind, sheet name and asset name rather than by file, so it also covers the
identical copies in other archives and survives new downloads and game updates. Nothing in the
game or the workspace is deleted; removed art is only hidden and skipped.
"""
import json
from pathlib import Path
import time


class Marks:
    def __init__(self, catalog, file, flag, note, cascade):
        """flag: the catalog row field to set. cascade: marking a sheet also marks every sprite on it."""
        self.catalog, self.file, self.flag, self.note, self.cascade = catalog, Path(file), flag, note, cascade
        try: saved = json.loads(self.file.read_text(encoding='utf-8')).get(flag, [])
        except (OSError, ValueError, AttributeError): saved = []
        self.entries = {e['id']: e for e in saved if isinstance(e, dict) and isinstance(e.get('id'), str)}
        self.apply()

    def sheet_name(self, row):
        return self.catalog.rows[row['parent']]['name'] if row.get('parent') else ''

    def ident(self, row):
        return f"{row['kind']}|{self.sheet_name(row)}|{row['name']}"

    def apply(self):
        for row in self.catalog.rows.values():
            whole = self.cascade and row.get('parent') and f"image||{self.sheet_name(row)}" in self.entries
            row[self.flag] = bool(whole or self.ident(row) in self.entries)

    def store(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_name(self.file.name + '.tmp')
        doc = {'version': 1, 'note': self.note, self.flag: list(self.entries.values())}
        temp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.file)

    def twins(self, row):
        """The asset and its pixel-identical copies in other files, which may sit on differently named sheets."""
        keys = getattr(self.catalog, 'copies', {}).get(row.get('dup', ''), [])
        return [self.catalog.rows[k] for k in keys] or [row]

    def marked(self): return sum(1 for r in self.catalog.rows.values() if r.get(self.flag))

    def add(self, keys, sheet=False):
        before, last = self.marked(), None
        for key in keys:
            row = self.catalog.rows[key]
            if sheet and row.get('parent'): row = self.catalog.rows[row['parent']]
            for twin in self.twins(row):
                ident = self.ident(twin)
                self.entries[ident] = dict(id=ident, name=twin['name'], kind=twin['kind'], sheet=self.sheet_name(twin), group=twin['group'], at=time.strftime('%Y-%m-%d %H:%M:%S'))
            last = row
        self.apply()
        self.store()
        return dict(name=last['name'] if last else '', marked=self.ident(last) if last else '', records=self.marked() - before, total=len(self.entries))

    def discard(self, keys):
        back, last = [], None
        for key in keys:
            row = last = self.catalog.rows[key]
            wanted = []
            for twin in self.twins(row): wanted += [self.ident(twin), f"image||{self.sheet_name(twin)}" if self.cascade and twin.get('parent') else '']
            for ident in dict.fromkeys(wanted):
                if ident in self.entries:
                    del self.entries[ident]
                    back.append(ident)
        self.apply()
        self.store()
        return dict(restored=back, name=last['name'] if last else '', total=len(self.entries))

    def listing(self):
        return {'file': str(self.file), 'total': len(self.entries), 'hidden': self.marked(),
                self.flag: sorted(self.entries.values(), key=lambda e: (e.get('sheet', ''), e['name']))}


class Pool(Marks):
    """Assets removed from the working pool: hidden from the library and never themed."""
    def __init__(self, catalog, file):
        super().__init__(catalog, file, 'removed', 'Assets hidden from Asset Lab. Delete an entry to bring it back.', cascade=True)

    def apply(self):
        # Art the game downloads from its own server (the item sheet: potions, power-ups, equipment) is replaced by a fresh
        # copy whenever it was changed, so it is always out of the pool and can't be put back.
        super().apply()
        for row in self.catalog.rows.values():
            if row.get('server_only'): row['removed'] = True

    def remove(self, key, sheet=False):
        result = self.add([key], sheet)
        return dict(result, removed=result['marked'])

    def restore(self, key): return self.discard([key])


class Labels(Marks):
    """Your own names for assets ("Speed potion"), shown on cards and found by search and by Theme AI."""
    def __init__(self, catalog, file):
        super().__init__(catalog, file, 'label', 'Names given to assets in Asset Lab.', cascade=False)

    def apply(self):
        for row in self.catalog.rows.values():
            entry = self.entries.get(self.ident(row))
            row['label'] = entry.get('label', '') if entry else ''

    def marked(self): return sum(1 for r in self.catalog.rows.values() if r.get('label'))

    def name(self, keys, label):
        """Give every key (and its identical copies) the same name; an empty name removes it."""
        label = ' '.join(str(label or '').split())[:80]
        for key in keys:
            for twin in self.twins(self.catalog.rows[key]):
                ident = self.ident(twin)
                if label: self.entries[ident] = dict(id=ident, name=twin['name'], kind=twin['kind'], sheet=self.sheet_name(twin), label=label)
                else: self.entries.pop(ident, None)
        self.apply()
        self.store()
        return dict(label=label, count=len(keys), total=len(self.entries))

    def mapping(self): return {key: row['label'] for key, row in self.catalog.rows.items() if row.get('label')}


class Favorites(Marks):
    """Assets starred for quick access."""
    def __init__(self, catalog, file):
        super().__init__(catalog, file, 'favorite', 'Assets starred in Asset Lab.', cascade=False)

    def keys(self): return [key for key, row in self.catalog.rows.items() if row.get('favorite')]
