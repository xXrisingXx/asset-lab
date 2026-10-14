"""Precise storage and native-reference notes for every visual catalog record.

Offsets into archives locate compressed members, not individual sprite pixels.
Native references are accepted only for the exact executable hash analyzed.
"""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile
from visual_tags import potion_family

DATA = Path(__file__).resolve().parent / 'data'


def reference_terms(row):
    result = {}
    def add(text, kind):
        if len(text) >= 3 and text not in result: result[text] = kind
    add(row['name'], 'asset name')
    if row['kind'] == 'image': add(PurePosixPath(row['name']).stem, 'image stem')
    add(row['entry'] or row['source'], 'container / image path')
    add(PurePosixPath(row['entry'] or row['source']).name, 'image filename')
    if row['entry']: add(row['source'], 'archive path')
    match = re.match(r'^(.*?[_-])\d', row['name'])
    if match: add(match[1], 'family prefix; individual member is not proven')
    return result


class AssetNotes:
    def __init__(self, catalog, game, reference_file=None):
        self.catalog, self.game = catalog, Path(game)
        self.zip_members = {}
        self.source_hashes = {}
        self.reference_file = Path(reference_file) if reference_file else DATA / 'exe-references.json'
        self.findings_file = DATA / 'visual-findings.json'
        self._version_signature = None
        self._refresh_references()

    def _refresh_references(self):
        # A manual EXE swap or completed research export can happen while the
        # editor is open. Do not keep showing offsets from a stale fingerprint.
        exe = self.game / 'Mitosis.exe'
        def stamp(path):
            try:
                s = path.stat()
                return s.st_size, s.st_mtime_ns, s.st_ctime_ns
            except FileNotFoundError:
                return None
        signature = tuple(stamp(p) for p in (exe, self.reference_file, self.findings_file))
        if signature == self._version_signature: return
        self.exe_hash = hashlib.sha256(exe.read_bytes()).hexdigest()
        self.index = {}
        self.findings = {}
        findings_file = self.findings_file
        if findings_file.is_file():
            findings = json.loads(findings_file.read_text(encoding='utf-8'))
            if findings.get('exe_sha256') == self.exe_hash:
                self.findings = findings.get('findings', {})
        self.reference_status = 'Native reference index is not available for this executable.'
        path = self.reference_file
        if path.is_file():
            doc = json.loads(path.read_text(encoding='utf-8'))
            if doc.get('exe_sha256') == self.exe_hash:
                self.index = doc.get('references', {})
                self.reference_status = 'References verified against this exact executable hash.'
            else:
                self.reference_status = 'Executable version differs from the analyzed build; old offsets are hidden.'
        self._version_signature = signature

    def _member(self, row):
        source = row['source']
        if source not in self.zip_members:
            path = self.catalog.source_path(source)
            members = {}
            with zipfile.ZipFile(path) as archive, path.open('rb') as stream:
                for info in archive.infolist():
                    stream.seek(info.header_offset)
                    header = stream.read(30)
                    if len(header) != 30 or header[:4] != b'PK\x03\x04':
                        raise ValueError('Invalid local archive header')
                    names, extra = struct.unpack_from('<HH', header, 26)
                    members[info.filename] = dict(local_header_offset=f'0x{info.header_offset:X}',
                        encoded_data_offset=f'0x{info.header_offset+30+names+extra:X}',
                        encoded_bytes=info.compress_size, decoded_bytes=info.file_size,
                        compression=info.compress_type, crc32=f'{info.CRC:08X}')
            self.zip_members[source] = members
        return self.zip_members[source][row['entry']]

    def get(self, key):
        self._refresh_references()
        row = self.catalog.rows[key]
        terms = reference_terms(row)
        matches = []
        for text, relationship in terms.items():
            for ref in self.index.get(text, []):
                matches.append(dict(ref, literal=text, relationship=relationship))
        source = row['source']
        installed = (self.catalog.cache / source[6:]) if source.startswith('cache/') else self.game / source
        snapshot = self.catalog.source_path(source)
        if source not in self.source_hashes:
            self.source_hashes[source] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        metadata = {}
        if row.get('atlas'):
            container, entry = row['atlas'].rsplit(':', 1)
            metadata = dict(container=container, member=entry, region_name=row['name'], values=row.get('metadata', {}))
        location = dict(container=str(installed), source=source, member=row['entry'],
                        inspected_source=str(snapshot), container_sha256=self.source_hashes[source],
                        kind='archive member' if row['entry'] else 'loose image file',
                        region=row.get('rect'), rotated_clockwise_in_atlas=bool(row.get('rotated')),
                        width=row.get('width'), height=row.get('height'), metadata=metadata)
        if row['entry']: location.update(self._member(row))
        else: location['encoded_data_offset'] = '0x0'
        steps = [f'Open {row["name"]} in Asset Lab, edit or import pixels, then Save edit + recipe.',
                 'Close Mitosis, review the changed files, and install the saved mod. Restore original after the test.']
        if row.get('parent'):
            steps.insert(1, 'The editor writes the sprite back into its atlas rectangle and handles packed rotation. Keep the dimensions and metadata intact.')
        notes = ['Pixel data is in the external image listed here. An EXE reference points to code or a name, not an embedded copy of these pixels.',
                 'Archive byte offsets describe this source version. Rebuilding the PNG or archive can move them; use Asset Lab to replace the image.']
        if snapshot.resolve() != installed.resolve():
            notes.append('Locations describe the protected original snapshot, not a currently installed edit. The source SHA-256 identifies that exact container.')
        if not matches: notes.append('No verified direct EXE reference is indexed for this asset. A loader, manifest, downloaded table, or constructed name may select it.')
        if row.get('legacy'): notes.append('The atlas audit marks this as a possible leftover region; verify the crop before editing.')
        family = potion_family(row['name'], PurePosixPath(row['entry']).name)
        if family: notes.append(f'{family}: visually verified art family. An in-game effect or item name has not been inferred from its generic sprite name.')
        return dict(asset_key=key, name=row['name'], pixel_location=location, editing_steps=steps,
                    verified_usage=self.findings.get(row['name'], []),
                    exe=dict(path=str(self.game/'Mitosis.exe'), sha256=self.exe_hash,
                             status=self.reference_status, references=matches), notes=notes)

    def text(self, key):
        n = self.get(key); p = n['pixel_location']
        lines = [f'# {n["name"]}', '', f'Asset key: {key}', f'Image container: {p["container"]}',
                 f'Inspected source: {p["inspected_source"]}', f'Container SHA-256: {p["container_sha256"]}',
                 f'Archive member: {p["member"] or "(loose file)"}', f'Encoded data offset: {p["encoded_data_offset"]}',
                 f'Image dimensions: {p["width"]} x {p["height"]}',
                 f'Sprite rectangle [x, y, width, height]: {p["region"]}',
                 f'Packed clockwise: {p["rotated_clockwise_in_atlas"]}',
                 f'Region metadata: {json.dumps(p["metadata"], ensure_ascii=False)}',
                 f'EXE SHA-256: {n["exe"]["sha256"]}', '', *n['notes'], '', '## Editing', '', *n['editing_steps'],
                 '', '## Verified appearance and color controls', '', *n['verified_usage'],
                 '', '## Executable references', '', n['exe']['status']]
        for ref in n['exe']['references']:
            lines += ['', f'Name: {ref["literal"]} ({ref["relationship"]})']
            if ref.get('kind') == 'decoded_path':
                lines += [ref['description'], f'Runtime decoded buffer VA: {ref["decoded_buffer_va"]}',
                          f'Encoded initialization operands: {json.dumps(ref["encoded_sites"])}']
            else:
                lines += [f'String VA: {ref.get("string_va", "unknown")}; file offset: {ref.get("string_file_offset", "unknown")}']
            for use in ref.get('uses', []):
                lines.append(f'Function {use["function"]} at {use["function_va"]}; instruction {use["instruction_va"]}; EXE file offset {use["file_offset"]}')
        return '\n'.join(lines)+'\n'

    def export_all(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            with archive.open('all-visual-locations.jsonl', 'w') as stream:
                for key, row in self.catalog.rows.items():
                    if row['kind'] not in ('image', 'sprite', 'glyph'): continue
                    stream.write((json.dumps(self.get(key), ensure_ascii=False)+'\n').encode('utf-8'))
            archive.writestr('README.txt',
                'One JSON object per visual image, sprite, or font glyph.\n'
                'pixel_location locates the image data and atlas rectangle.\n'
                'exe.references lists only references matched to the exact executable hash.\n'
                'A reference is evidence of a name used by code, not proof that a particular screen draws it.\n'
                'Empty references mean unmapped, not embedded. Family prefixes do not establish an individual item.\n'
                'Use Asset Lab to edit PNGs and rebuild archives. Encoded data offsets are version-specific.\n')
        return output.getvalue()
