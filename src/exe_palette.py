"""Version-locked, offline copies of verified native UI color assignments and menu visibility branches.

This module does not install or execute binaries. Each color is a four-byte little-endian 0x00RRGGBB
immediate in an existing MOV instruction; each hidden group swaps one existing "skip this control"
conditional jump for an unconditional jump to the same target. Offsets come from
docs/NATIVE-UI-COLOR-INDEX.md and docs/NATIVE-UI-RESEARCH-HANDOFF.md.
"""
import hashlib
import json
from pathlib import Path
import re
import struct

EXE_SHA256 = '4716e856138ef6e8292be452f041ecd15ee8f1204bfec0a525d7d47e2e8fd859'
# Tested 2026-10-14 by launching the game: Mitosis verifies its own executable at startup and quits cleanly (exit code 0,
# before connecting to its server) when any byte differs, even one in padding that never runs. Editing the exe is
# therefore switched off; the research stays for reference. Defeating that check is out of scope for this tool.
BLOCKED = ('Mitosis checks its own Mitosis.exe when it starts and closes itself if the file was changed (tested: even one unused '
           'byte). Game colors and hiding menu buttons edit that file, so they are switched off. Art edits are not affected.')
# field -> [(operand file offset, original 0xRRGGBB, palette phase)]. Phases: startup defaults, then the
# reset palettes the game switches between (0/4 teal, 1 purple, 2 gray). One override sets every phase.
SITES = {
    'BUTTON_CLEAR_COLOR': [(0x39F7D7, 0x40BDC4, 'startup'), (0x3978AF, 0x40BDC4, 'theme_0_or_4'), (0x39791C, 0xC5C5E1, 'theme_1'), (0x3979CA, 0xDDDDDD, 'theme_2')],
    'ACTIVE_ITEM_COLOR': [(0x39F7CD, 0xE52C58, 'startup'), (0x3978A5, 0xE52C58, 'theme_0_or_4')],
    'BG_COLOR': [(0x39F7C3, 0x0D2829, 'startup'), (0x39789B, 0x0D2829, 'theme_0_or_4'), (0x397926, 0x1A1121, 'theme_1'), (0x3979D4, 0x242424, 'theme_2')],
    'BG_COLOR_LIGHT': [(0x39F773, 0xFCFCFC, 'startup'), (0x3978CD, 0xFCFCFC, 'theme_0_or_4'), (0x397A6A, 0xFCFCFC, 'theme_1_or_2')],
    'NORMAL_TEXT_COLOR': [(0x39F77D, 0x247276, 'startup'), (0x397855, 0x247276, 'theme_0_or_4'), (0x39794E, 0xABABD3, 'theme_1'), (0x3979FC, 0xAAAAAA, 'theme_2')],
    'DARKER_TEXT_COLOR': [(0x39F79B, 0x194C4D, 'startup'), (0x397873, 0x194C4D, 'theme_0_or_4'), (0x397962, 0x545058, 'theme_1'), (0x397A10, 0x555555, 'theme_2')],
    'ROUNDED_BUTTON_BORDER_COLOR': [(0x39F791, 0x40BDC4, 'startup'), (0x397869, 0x40BDC4, 'theme_0_or_4'), (0x39796C, 0xABABD3, 'theme_1'), (0x397A1A, 0xA0A0A0, 'theme_2')],
    'MESSENGER_BG_COLOR': [(0x39F787, 0x030B0B, 'startup'), (0x39785F, 0x030B0B, 'theme_0_or_4'), (0x397958, 0x0A060D, 'theme_1'), (0x397A06, 0x0A0A0A, 'theme_2')],
    'HEADER_COLOR': [(0x39F7E1, 0x113638, 'startup'), (0x3978B9, 0x113638, 'theme_0_or_4'), (0x397930, 0x140D1A, 'theme_1'), (0x3979DE, 0x141414, 'theme_2')],
    'BOTTOM_BARS_COLOR': [(0x39F7AF, 0x061515, 'startup'), (0x397887, 0x061515, 'theme_0_or_4'), (0x397A74, 0x0A060D, 'theme_1_or_2')],
    'LINE_DARK_COLOR': [(0x39F7A5, 0x08191A, 'startup'), (0x39787D, 0x0A2022, 'theme_0_or_4'), (0x397944, 0x140D19, 'theme_1'), (0x3979F2, 0x1E1E1E, 'theme_2')],
    'LOADER_COLOR': [(0x39F7B9, 0x2E8F94, 'startup'), (0x397891, 0x2E8F94, 'theme_0_or_4'), (0x39793A, 0x8E8FC4, 'theme_1'), (0x3979E8, 0xD9D9D9, 'theme_2')],
    'CHOOSEN_ITEM_BG': [(0x39F7EB, 0x40BDC4, 'startup'), (0x3978C3, 0x40BDC4, 'theme_0_or_4'), (0x397976, 0x5D5E89, 'theme_1'), (0x397A24, 0x6CF0A3, 'theme_2')],
    'INGAME_TEXT_COLOR': [(0x39F7F5, 0x859798, 'startup'), (0x3978D7, 0x859798, 'theme_0_or_4'), (0x397980, 0x8C8C96, 'theme_1'), (0x397A2E, 0x8C8C8C, 'theme_2')],
    'INGAME_DARK_TEXT_COLOR': [(0x39F7FF, 0x113537, 'startup'), (0x3978E1, 0x113537, 'theme_0_or_4'), (0x39798A, 0x241C2B, 'theme_1'), (0x397A38, 0x1C1C1C, 'theme_2')],
    'INGAME_BORDER_COLOR': [(0x39F809, 0x566F70, 'startup'), (0x3978EB, 0x566F70, 'theme_0_or_4'), (0x397994, 0x545058, 'theme_1'), (0x397A42, 0x555555, 'theme_2')],
    'LEADERBOARD_SELF_COLOR': [(0x39F813, 0xDDFB83, 'startup'), (0x3978F5, 0xDDFB83, 'theme_0_or_4'), (0x39799E, 0xF1E3FE, 'theme_1'), (0x397A4C, 0x6CF0A3, 'theme_2')],
    'GUILD_BACKGROUND_COLOR': [(0x39F81D, 0x18A3E8, 'startup'), (0x3978FF, 0x18A3E8, 'theme_0_or_4'), (0x3979A8, 0x1A1121, 'theme_1'), (0x397A56, 0x1A1A1A, 'theme_2')],
    'DARK_GUILD_BACKGROUND_COLOR': [(0x39F827, 0x091E1F, 'startup'), (0x397909, 0x091E1F, 'theme_0_or_4'), (0x3979B2, 0x120A16, 'theme_1'), (0x397A60, 0x121212, 'theme_2')],
    # Not a palette global: the MultiButton constructor's default color for an expanded circular group.
    'MULTIBUTTON_EXPANDED_COLOR': [(0x5F4D41, 0xE5365F, 'MultiButton constructor')],
}
# Palette fields are RIP-relative stores (C7 05 disp32 imm32) checked by their target global ...
GLOBALS = {'BUTTON_CLEAR_COLOR': 0x1421F632C, 'ACTIVE_ITEM_COLOR': 0x1421F6328, 'BG_COLOR': 0x1421F6354, 'BG_COLOR_LIGHT': 0x1421F6374,
           'NORMAL_TEXT_COLOR': 0x1421F6348, 'DARKER_TEXT_COLOR': 0x1421F6344, 'ROUNDED_BUTTON_BORDER_COLOR': 0x1421F6340,
           'MESSENGER_BG_COLOR': 0x1421F634C, 'HEADER_COLOR': 0x1421F6320, 'BOTTOM_BARS_COLOR': 0x1421F635C, 'LINE_DARK_COLOR': 0x1421F6358,
           'LOADER_COLOR': 0x1421F6350, 'CHOOSEN_ITEM_BG': 0x1421F6324, 'INGAME_TEXT_COLOR': 0x1421F6338, 'INGAME_DARK_TEXT_COLOR': 0x1421F633C,
           'INGAME_BORDER_COLOR': 0x1421F6330, 'LEADERBOARD_SELF_COLOR': 0x1421F6334, 'GUILD_BACKGROUND_COLOR': 0x1421F6308,
           'DARK_GUILD_BACKGROUND_COLOR': 0x1421F630C}
# ... other stores by their exact original instruction bytes before the operand.
PREFIX = {'MULTIBUTTON_EXPANDED_COLOR': bytes.fromhex('c7 87 b4 02 00 00')}  # MOV dword ptr [RDI+0x2B4], imm32
# Main-menu controls that can be left out: id -> (file offset, original six bytes, replacement, label).
# Each replacement is NOP + JMP rel32 with the original displacement, so it lands on the original skip target.
HIDE = {
    'mobile_store_badges': (0x5CF746, bytes.fromhex('0f 84 f2 04 00 00'), bytes.fromhex('90 e9 f2 04 00 00'), 'App Store and Google Play badges'),
    'discord': (0x5CFC45, bytes.fromhex('0f 84 79 02 00 00'), bytes.fromhex('90 e9 79 02 00 00'), 'Discord button'),
    'top_banner': (0x5D0192, bytes.fromhex('0f 85 3f 04 00 00'), bytes.fromhex('90 e9 3f 04 00 00'), 'Top banner with the Mitosis OG tag'),
    'left_banner_1': (0x5D30B2, bytes.fromhex('0f 85 63 01 00 00'), bytes.fromhex('90 e9 63 01 00 00'), 'First left promo (Worm.is / Strike.is)'),
    'left_banner_2': (0x5D3242, bytes.fromhex('0f 85 63 01 00 00'), bytes.fromhex('90 e9 63 01 00 00'), 'Second left promo (Worm.is / Strike.is)'),
}

# The art each control is drawn from (checked against captures of the menu). Hiding that art (fully transparent)
# works without touching Mitosis.exe; Worm.is and Strike.is are not drawn from art the lab can edit.
HIDE_SPRITES = {'mobile_store_badges': ['icon-iphone', 'icon-android'], 'discord': ['icon-discord'], 'top_banner': ['icon-og']}
COLORS_BLOCKED = ('Game colors are set inside Mitosis.exe, and Mitosis closes itself when that file was changed, '
                  'so they are switched off. Art edits are not affected.')


def parse_color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('Use a color in #RRGGBB format.')
    return int(value[1:], 16)


def patch(data, overrides=None, hide=()):
    overrides, hide = dict(overrides or {}), list(dict.fromkeys(hide or ()))
    if hashlib.sha256(data).hexdigest() != EXE_SHA256:
        raise ValueError('Unsupported or already edited executable. Use the exact original build documented in the color notes.')
    if not overrides and not hide: raise ValueError('Choose at least one game color or menu control to hide.')
    if set(overrides) - SITES.keys(): raise ValueError('Unknown game color: ' + ', '.join(sorted(set(overrides) - SITES.keys())))
    if set(hide) - HIDE.keys(): raise ValueError('Unknown menu control: ' + ', '.join(sorted(set(hide) - HIDE.keys())))
    parsed = {name: parse_color(value) for name, value in overrides.items()}
    output = bytearray(data); changes = []
    for name, value in parsed.items():
        for offset, expected, phase in SITES[name]:
            start = offset-6
            # This build's .text RVA differs from its file position by 0xC00.
            instruction_va = 0x140000000 + start + 0xC00
            if name in PREFIX:
                target = None
                verified = data[start:offset] == PREFIX[name]
            else:
                target = instruction_va+10+struct.unpack_from('<i',data,start+2)[0]
                verified = data[start:start+2] == b'\xC7\x05' and target == GLOBALS[name]
            if not verified or struct.unpack_from('<I',data,offset)[0] != expected:
                raise ValueError('Color instruction does not match the verified location.')
            before=data[offset:offset+4];after=struct.pack('<I',value)
            output[offset:offset+4]=after
            changes.append(dict(field=name, phase=phase, file_offset=f'0x{offset:X}', instruction_va=f'0x{instruction_va:X}',
                                global_va=f'0x{target:X}' if target else None, before=before.hex(' '), after=after.hex(' '), color=f'#{value:06X}'))
    for group in hide:
        offset, original, replacement, label = HIDE[group]
        if data[offset:offset+6] != original: raise ValueError('Menu branch does not match the verified location: ' + group)
        output[offset:offset+6] = replacement
        changes.append(dict(field='HIDE', phase=label, hide=group, file_offset=f'0x{offset:X}', before=original.hex(' '), after=replacement.hex(' '), color=''))
    return bytes(output),changes


def build_copy(source, destination, overrides, hide=()):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.is_relative_to(source.parent):
        raise ValueError('Write the test copy outside the source/game folder.')
    original=source.read_bytes();output,changes=patch(original,overrides,hide)
    manifest=destination.with_suffix('.palette.json')
    if destination.exists() or manifest.exists(): raise ValueError('Choose a new output path; existing files are never replaced.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as stream:stream.write(output)
    report=dict(source=str(source),source_sha256=EXE_SHA256,output=str(destination),
                output_sha256=hashlib.sha256(output).hexdigest(),same_size=len(output)==len(original),
                installed=False,changes=changes,
                scope='Shared UI palette: each field affects every control that reads it, and one value replaces all of its theme palettes.',
                acceptance='Byte-verified offline copy. Not installed or tested inside the running game.')
    with manifest.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    return report
