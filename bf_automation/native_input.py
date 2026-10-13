"""逐项核验 Unicode 按下和释放，兼容上游返回最后一项计数的实现。"""


def send_unicode_char(uia, char):
    if not hasattr(uia, 'KeyboardInput'):
        # 较小的驱动接口必须直接返回完整按下/释放事件数。
        return uia.SendUnicodeChar(char)
    flag = uia.KeyboardEventFlag
    down = uia.KeyboardInput(0, ord(char), flag.KeyUnicode | flag.KeyDown)
    up = uia.KeyboardInput(0, ord(char), flag.KeyUnicode | flag.KeyUp)
    pressed = 0
    try:
        pressed = uia.SendInput(down)
    finally:
        released = uia.SendInput(up)
    return 2 if pressed == 1 and released == 1 else 0
