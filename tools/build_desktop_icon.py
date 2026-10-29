"""将用户已选项目Logo转换为Windows多尺寸图标，不重新设计标志。"""
from pathlib import Path
import shutil
from PIL import Image

root = Path(__file__).resolve().parents[1]
target = root / 'desktop/assets'
target.mkdir(parents=True, exist_ok=True)
source = root / 'web/public/elfin-logo-arm-v1.png'
shutil.copyfile(source, target / 'logo.png')
with Image.open(source) as image:
    image.convert('RGBA').save(target / 'app.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
print('DESKTOP_ICON_OK')
