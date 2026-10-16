"""Builds: separate versions of your mod (a spooky one, a Post Malone one...) you can switch between, and the Theme AI
chats that made each one.

A build is everything you made: saved edits and their history, what is in the mod, game colors, hidden menu buttons,
fonts, sounds and Theme AI's undo list. The active build lives in the workspace as always (state.json, themes.json);
switching stores it under saved-builds/<id>/ and loads the other one. Saved pictures, fonts and sounds are files named
by their version, so builds share them and nothing is copied. Switching never touches the game: what is installed
stays until you press Update Mitosis or Restore.

    saved-builds/index.json            {active, installed, builds: [{id, name, created, updated, chat}]}
    saved-builds/<id>/state.json       that build's part of state.json (when it is not the active one)
    saved-builds/<id>/themes.json      its Theme AI undo list
    saved-builds/<id>/chats/<cid>.json its Theme AI chats: {id, title, updated, entries, history}
"""
import json
import base64
import copy
import re
import shutil
import time
import uuid
from lab_core import atomic_bytes, atomic_json, read_json

KEYS = {'edits': dict, 'staged': list, 'palette': dict, 'hide': list, 'fonts': dict, 'audio': dict}
FILES = ('themes.json',)          # workspace files that belong to the active build
MAX_BUILDS, MAX_CHATS = 60, 80
MAX_CHAT = 6 * 1024 * 1024        # one saved conversation (its plans and samples included)


def clean_name(name, fallback='Build'):
    name = ' '.join(str(name or '').split())[:60]
    return name or fallback


def ident(value):
    value = str(value or '')
    if not re.fullmatch(r'[a-f0-9]{8,32}', value): raise ValueError('Unknown build or chat.')
    return value


class Builds:
    def __init__(self, lab):
        self.lab = lab
        self.folder = lab.root / 'saved-builds'
        self.index_file = self.folder / 'index.json'
        self.switch_file = self.folder / 'pending-switch.json'
        with lab.lock:
            self.recover_switch()
            index = read_json(self.index_file, None)
            if not isinstance(index, dict) or not index.get('builds'):
                # The work you already have becomes your first build.
                first = self.new_entry('My build')
                index = dict(active=first['id'], installed=first['id'] if lab.active() else '', builds=[first])
                self.write(index)
            self.index = index

    def recover_switch(self):
        """Roll back an uncommitted switch, including after process termination.

        The journal is removed only after all workspace files and the index agree.
        Restoring it is idempotent: another interruption can safely retry it.
        """
        if not self.switch_file.exists(): return
        saved = read_json(self.switch_file)
        self.lab._build_recovery_required = True
        for name in FILES:
            data = saved['files'][name]
            path = self.lab.root / name
            if data is None: path.unlink(missing_ok=True)
            else: atomic_bytes(path, base64.b64decode(data, validate=True))
        atomic_json(self.lab.state_path, saved['state'])
        atomic_json(self.index_file, saved['index'])
        self.lab.state = saved['state']
        self.index = saved['index']
        self.lab._thumbs.clear()
        self.switch_file.unlink()
        self.lab._build_recovery_required = False

    # ----- the list -----------------------------------------------------------------------------------------
    @staticmethod
    def new_entry(name):
        now = time.time()
        return dict(id=uuid.uuid4().hex[:16], name=clean_name(name), created=now, updated=now, chat='')

    def write(self, index=None):
        if index is not None: self.index = index
        self.folder.mkdir(parents=True, exist_ok=True)
        atomic_json(self.index_file, self.index)

    def entry(self, bid):
        bid = ident(bid)
        found = next((b for b in self.index['builds'] if b['id'] == bid), None)
        if not found: raise ValueError('That build no longer exists.')
        return found

    def active(self): return self.entry(self.index['active'])

    def state_of(self, bid):
        """A build's part of state.json: the live one for the active build, its saved copy otherwise."""
        if bid == self.index['active']: return self.lab.state
        return read_json(self.folder / bid / 'state.json', {}) or {}

    def summary(self, state):
        edits = sum(1 for h in (state.get('edits') or {}).values() if h.get('cursor', -1) >= 0)
        return dict(edits=edits, in_mod=len(state.get('staged') or []), sounds=len(state.get('audio') or {}), fonts=len(state.get('fonts') or {}),
                    colors=len(state.get('palette') or {}), hidden=len(state.get('hide') or []))

    def listing(self):
        with self.lab.lock:
            installed = self.index.get('installed') if self.lab.active() else ''
            builds = [dict(b, active=b['id'] == self.index['active'], installed=b['id'] == installed, **self.summary(self.state_of(b['id'])),
                           chats=len(list((self.folder / b['id'] / 'chats').glob('*.json'))))
                      for b in self.index['builds']]
            return dict(active=self.index['active'], installed=installed or '', builds=builds)

    # ----- switching ----------------------------------------------------------------------------------------
    def store_active(self):
        """Keep the active build's state and files under saved-builds/<id>/."""
        bid = self.index['active']
        part = {k: self.lab.state.get(k, kind()) for k, kind in KEYS.items()}
        atomic_json(self.folder / bid / 'state.json', part)
        for name in FILES:
            src = self.lab.root / name
            if src.is_file(): atomic_bytes(self.folder / bid / name, src.read_bytes())
            else: (self.folder / bid / name).unlink(missing_ok=True)
        self.active()['updated'] = time.time()

    def load(self, bid, part):
        for k, kind in KEYS.items():
            value = part.get(k)
            self.lab.state[k] = value if isinstance(value, kind) else kind()
        for name in FILES:
            saved = self.folder / bid / name
            if saved.is_file(): atomic_bytes(self.lab.root / name, saved.read_bytes())
            else: (self.lab.root / name).unlink(missing_ok=True)
        self.lab.persist()
        self.lab._thumbs.clear()

    def switch(self, bid):
        with self.lab.lock:
            self.recover_switch()
            target = self.entry(bid)
            if bid == self.index['active']: return self.listing()
            self.store_active()
            part = read_json(self.folder / bid / 'state.json')
            if not isinstance(part, dict): raise ValueError('That build has no saved state. The current build was kept.')
            journal = dict(state=copy.deepcopy(self.lab.state), index=copy.deepcopy(self.index), files={
                name: base64.b64encode((self.lab.root / name).read_bytes()).decode('ascii')
                if (self.lab.root / name).is_file() else None for name in FILES})
            atomic_json(self.switch_file, journal)
            try:
                self.load(bid, part)
                self.index['active'] = bid
                target['updated'] = time.time()
                self.write()
                self.switch_file.unlink()  # commit point
            except Exception:
                self.recover_switch()
                raise
            return self.listing()

    def create(self, name='', copy_of=''):
        """A new build, switched to: empty (Start over) or a copy of another build."""
        with self.lab.lock:
            if len(self.index['builds']) >= MAX_BUILDS: raise ValueError(f'You have {MAX_BUILDS} builds. Delete one you no longer need first.')
            source = self.entry(copy_of) if copy_of else None
            entry = self.new_entry(name or (f'{source["name"]} (copy)' if source else f'Build {len(self.index["builds"]) + 1}'))
            if source:
                if source['id'] == self.index['active']: self.store_active()
                shutil.copytree(self.folder / source['id'], self.folder / entry['id'], ignore=shutil.ignore_patterns('chats'), dirs_exist_ok=True)
            else:
                atomic_json(self.folder / entry['id'] / 'state.json', {k: kind() for k, kind in KEYS.items()})
            self.index['builds'].append(entry)
            self.write()
            return self.switch(entry['id'])

    def rename(self, bid, name):
        with self.lab.lock:
            self.entry(bid)['name'] = clean_name(name, self.entry(bid)['name'])
            self.write()
            return self.listing()

    def delete(self, bid):
        """Forget a build that is not the one open (its pictures stay on disk; the game is not touched)."""
        with self.lab.lock:
            entry = self.entry(bid)
            if bid == self.index['active']: raise ValueError('Switch to another build before deleting this one.')
            self.index['builds'].remove(entry)
            if self.index.get('installed') == bid: self.index['installed'] = ''
            self.write()
            shutil.rmtree(self.folder / bid, ignore_errors=True)
            return self.listing()

    def installed(self, done):
        """After an install (done=True) or a restore (False): which build is in the game now."""
        with self.lab.lock:
            self.index['installed'] = self.index['active'] if done else ''
            self.write()

    # ----- Theme AI chats of the active build ------------------------------------------------------------------
    def chat_folder(self): return self.folder / self.index['active'] / 'chats'

    def chats(self):
        with self.lab.lock:
            out = []
            for f in self.chat_folder().glob('*.json'):
                doc = read_json(f, {}) or {}
                if doc.get('id') == f.stem: out.append(dict(id=f.stem, title=doc.get('title') or 'New chat', updated=doc.get('updated', 0), count=len(doc.get('entries') or [])))
            out.sort(key=lambda c: c['updated'], reverse=True)
            return dict(active=self.active().get('chat') or '', chats=out)

    def chat(self, cid):
        cid = ident(cid)
        with self.lab.lock:
            doc = read_json(self.chat_folder() / (cid + '.json'), None)
            if not doc: raise ValueError('That chat no longer exists.')
            if self.active().get('chat') != cid: self.active()['chat'] = cid; self.write()
            return doc

    def save_chat(self, cid, entries, history, title=''):
        cid = ident(cid)
        if not isinstance(entries, list) or not isinstance(history or [], list): raise ValueError('Send the chat as a list of entries.')
        doc = dict(id=cid, title=clean_name(title, 'New chat')[:60], updated=time.time(), entries=entries[-60:], history=(history or [])[-40:])
        if len(json.dumps(doc)) > MAX_CHAT: raise ValueError('This chat is too long to save. Start a new chat.')
        with self.lab.lock:
            folder = self.chat_folder()
            if not (folder / (cid + '.json')).is_file() and len(list(folder.glob('*.json'))) >= MAX_CHATS:
                oldest = min(folder.glob('*.json'), key=lambda f: f.stat().st_mtime)   # the oldest chat makes room
                oldest.unlink(missing_ok=True)
            atomic_json(folder / (cid + '.json'), doc)
            self.active()['chat'] = cid
            self.write()
            return dict(id=cid, title=doc['title'], updated=doc['updated'])

    def delete_chat(self, cid):
        cid = ident(cid)
        with self.lab.lock:
            (self.chat_folder() / (cid + '.json')).unlink(missing_ok=True)
            if self.active().get('chat') == cid: self.active()['chat'] = ''; self.write()
            return self.chats()
