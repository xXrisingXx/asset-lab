"""Small atlases with independent panel, button and marker pixels."""
from PIL import Image, ImageDraw


def add_hide_controls(game):
    for i in range(3):
        sheet = Image.new('RGBA', (96, 32))
        draw = ImageDraw.Draw(sheet)
        draw.ellipse((2, 2, 29, 29), fill=(220, 170, 80, 255))
        draw.rounded_rectangle((34, 2, 61, 29), radius=6, fill=(180, 180, 180, 170))
        draw.ellipse((76, 12, 83, 19), fill='white')
        name = f'cleanup-{i}'
        sheet.save(game / 'assets' / f'{name}.png')
        (game / 'assets' / f'{name}.xml').write_text(
            f'<TextureAtlas imagePath="{name}.png">' + ''.join(
                f'<SubTexture name="{n}" x="{x}" y="0" width="32" height="32" />'
                for n, x in [('button-circle', 0), ('rounded-bg', 32), ('map-marker', 64)]
            ) + '</TextureAtlas>', encoding='utf-8')
    return game


TOOLBAR_NAMES = ('icon-ingame-potion', 'icon-ingame-potion-desktop', 'icon-report-abuse',
                 'icon-ingame-chat', 'icon-ingame-chat-desktop', 'icon-pause-button')


def add_toolbar_controls(game, sheets=('toolbar-a', 'toolbar-b', 'toolbar-c')):
    for sheet_name in sheets:
        names = (*TOOLBAR_NAMES, 'untouched-marker')
        sheet = Image.new('RGBA', (64 * len(names), 64))
        draw = ImageDraw.Draw(sheet)
        for i, _ in enumerate(names):
            x = i * 64
            draw.ellipse((x + 4, 4, x + 59, 59), outline='white', width=3)
            draw.rectangle((x + 21, 18, x + 42, 45), fill=(80 + i * 20, 120, 170, 255))
        sheet.save(game / 'assets' / f'{sheet_name}.png')
        (game / 'assets' / f'{sheet_name}.xml').write_text(
            f'<TextureAtlas imagePath="{sheet_name}.png">' + ''.join(
                f'<SubTexture name="{name}" x="{i * 64}" y="0" width="64" height="64" />'
                for i, name in enumerate(names)) + '</TextureAtlas>', encoding='utf-8')
    return game
