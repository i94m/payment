"""图片验证码生成（Pillow 手绘，无字体文件依赖）。"""
import base64
import io
import math
import random
import string


def generate_image_captcha(length: int = 4) -> tuple[str, str]:
    """生成图片验证码。

    返回 (content: "data:image/png;base64,...", code: 4 位数字小写)。
    """
    from PIL import Image, ImageDraw, ImageFont

    code = "".join(random.choices(string.digits, k=length))

    W, H = 130, 44
    img = Image.new("RGB", (W, H), (245, 245, 245))
    draw = ImageDraw.Draw(img)

    # 背景噪点
    for _ in range(50):
        x = random.randint(0, W - 1)
        y = random.randint(0, H - 1)
        c = random.randint(200, 240)
        draw.point((x, y), fill=(c, c, c))

    font = ImageFont.load_default(size=28)

    colors = [
        (40, 40, 40), (50, 35, 35), (35, 50, 35),
        (35, 35, 50), (50, 50, 35), (35, 50, 50),
    ]
    char_w = W // length
    for i, ch in enumerate(code):
        x = char_w * i + random.randint(2, 8)
        y = random.randint(4, 10)
        color = random.choice(colors)
        angle = random.randint(-12, 12)
        char_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_layer)
        char_draw.text((x, y), ch, fill=(*color, 255), font=font)
        rotated = char_layer.rotate(angle, resample=Image.BICUBIC, expand=False)
        img = Image.alpha_composite(img.convert("RGBA"), rotated).convert("RGB")
        draw = ImageDraw.Draw(img)

    # 干扰线
    for _ in range(4):
        x1 = random.randint(0, W)
        y1 = random.randint(0, H)
        x2 = random.randint(0, W)
        y2 = random.randint(0, H)
        c = random.randint(150, 200)
        draw.line([(x1, y1), (x2, y2)], fill=(c, c, c), width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    content = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    return content, code.lower()


async def verify_captcha_code(cache, captcha_key: str, captcha_code: str) -> str | None:
    """校验图片验证码。通过返回 None，失败返回错误信息；验证后一次性失效。

    本地环境（APP_ENV=local）允许万能验证码 0000，供自动化测试使用。
    """
    if not captcha_code:
        return "请输入验证码"
    from app.core.config import config
    if config.APP_ENV == "local" and captcha_code.strip() == "0000":
        return None
    if not captcha_key:
        return "请输入验证码"
    cached = await cache.get(f"captcha:{captcha_key}")
    await cache.delete(f"captcha:{captcha_key}")
    if not cached:
        return "验证码已过期，请刷新"
    if str(cached).lower() != captcha_code.strip().lower():
        return "验证码错误"
    return None
